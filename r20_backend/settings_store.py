"""Safe local .env configuration persistence for the R20 admin plane."""
from __future__ import annotations
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Mapping
from .config import ROOT, refresh_settings
from .file_locks import file_lock
from .redact import mask as _redact_mask

ENV_FILE = ROOT / ".env"
MANAGED_KEYS = {
    "OKX_BASE_URL",
    "R20_OKX_ENV",
    "OKX_API_KEY",
    "OKX_SECRET_KEY",
    "OKX_PASSPHRASE",
    "OKX_LIVE_API_KEY", "OKX_LIVE_SECRET_KEY", "OKX_LIVE_PASSPHRASE",
    "OKX_DEMO_API_KEY", "OKX_DEMO_SECRET_KEY", "OKX_DEMO_PASSPHRASE",
    "OKX_IS_SIMULATED",
    # 多交易所网络档位与 6 账户独立凭证（US-003）
    "R20_BINANCE_TESTNET", "R20_GATE_TESTNET",
    "BINANCE_API_KEY", "BINANCE_SECRET_KEY",
    "BINANCE_LIVE_API_KEY", "BINANCE_LIVE_SECRET_KEY",
    "BINANCE_DEMO_API_KEY", "BINANCE_DEMO_SECRET_KEY",
    "BINANCE_TESTNET_API_KEY", "BINANCE_TESTNET_SECRET_KEY",
    "GATE_API_KEY", "GATE_SECRET_KEY",
    "GATE_LIVE_API_KEY", "GATE_LIVE_SECRET_KEY",
    "GATE_DEMO_API_KEY", "GATE_DEMO_SECRET_KEY",
    "GATE_TESTNET_API_KEY", "GATE_TESTNET_SECRET_KEY",
    "GATE_SANDBOX_API_KEY", "GATE_SANDBOX_SECRET_KEY",
    # Gate/Binance 执行路由总闸（真实下单权限，默认关）
    "R20_GATE_EXECUTION", "R20_GATE_DEMO_EXECUTION",
    "R20_BINANCE_EXECUTION", "R20_BINANCE_DEMO_EXECUTION",
    # 跨所同向合并敞口上限（US-005 拒开阈值，单位 USDT）
    "R20_MAX_TOTAL_EXPOSURE_USDT",
    "LLM_BASE_URL",
    "LLM_API_KEY",
    "LLM_MODEL",
    "LLM_REASONING_EFFORT",
    "R20_NOTIFICATION_WEBHOOK",
    "R20_NOTIFY_WEBHOOK_ENABLED",
    "R20_NOTIFY_WECHAT_ENABLED",
    "R20_WECHAT_WEBHOOK",
    "R20_NOTIFY_TELEGRAM_ENABLED",
    "R20_TELEGRAM_BOT_TOKEN",
    "R20_TELEGRAM_CHAT_ID",
    "R20_NOTIFY_QQ_ENABLED",
    "R20_QQ_APP_ID",
    "R20_QQ_CLIENT_SECRET",
    "R20_QQ_OPENID",
    "R20_SETUP_TOKEN",
    "R20_ADMIN_TOKEN",
    "R20_MANUAL_CLOSE_ENABLED",
}

# 执行层风控参数（后台「风控管理页」写入，scripts/risk_constants.py 读取）
try:
    from scripts.risk_constants import RISK_ENV_KEYS as _RISK_ENV_KEYS
    MANAGED_KEYS.update(_RISK_ENV_KEYS)
except Exception:
    pass


def mask(value: str, visible: int = 4) -> str:
    """凭证脱敏 —— **转发到单一事实源** `r20_backend.redact.mask`。

    结构优化阶段 4·B3 第四十九刀：本函数与 `r20_backend.llm.util.mask_secret`
    原先各有一份**逐字节相同**的实现（`settings_store` 面向后台设置页、
    `llm.util` 面向 LLM 配置页）。两份都在决定"密钥能露出几个字符"，
    任何一处改动都会让两侧脱敏强度不一致，故收敛到同一实现。

    ⚠️ 名字**必须**保留在本模块：`tests/audit/test_audit_batch1_credentials_trust_boundary.py`
    直接 `from r20_backend.settings_store import mask`，且
    `r20_backend/routers/gateway/notifications.py` 也从这里导入。
    """
    return _redact_mask(value, visible)


def is_masked(value: str) -> bool:
    """审计修复A2(2026-09-13)：识别 mask()/mask_url() 的产物（含连续8星或纯星串）。
    脱敏读↔明文写回环防线：掩码串出现在写请求里一律视为『用户未改动』，绝不落盘。"""
    if not value:
        return False
    v = value.strip()
    return "*" * 8 in v or set(v) == {"*"}


def mask_url(url: str, visible_tail: int = 6) -> str:
    """Mask token/key inside webhook URLs like https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxxx."""
    if not url:
        return ""
    if "?" in url:
        base, query = url.split("?", 1)
        if len(query) <= visible_tail:
            return f"{base}?{'*' * 8}"
        return f"{base}?key={'*' * 8}{query[-visible_tail:]}"
    if len(url) <= 12:
        return "*" * len(url)
    return f"{url[:12]}{'*' * 8}{url[-visible_tail:]}"


_ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class EnvValueError(ValueError):
    """非法的环境变量写入（值含换行/控制字符、键名非法）——路由层映射为 400。"""


def sanitize_env_value(key: str, value: Any) -> str:
    """审计 P2-7(2026-09-13)：.env 是"每行一个 KEY=VALUE"的格式，值里出现换行就等于
    追加新键（密钥页/通知页/LLM 页任一输入框都能伪造 `R20_BINANCE_EXECUTION=1` 这类
    执行开闸键）。这里统一拒绝换行/NUL，并顺带拒绝控制字符与首尾空白污染。"""
    text = "" if value is None else str(value)
    if any(ch in text for ch in ("\n", "\r", "\0")):
        raise EnvValueError(f"环境变量 {key} 的值不得包含换行或空字符（防止注入新配置键）")
    if any(ord(ch) < 32 for ch in text):
        raise EnvValueError(f"环境变量 {key} 的值包含控制字符，已拒绝写入")
    return text.strip()


import errno


def _safe_commit_file(temp_path: str, target: Path) -> None:
    """原子替换目标文件，对 Docker bind-mount 单文件挂载点的 EBUSY 提供安全回退。"""
    os.chmod(temp_path, 0o600)
    try:
        os.replace(temp_path, target)
    except OSError as exc:
        # 当 target 是 Docker 单文件 bind-mount 挂载点时，rename 会报 EBUSY (Device or resource busy)
        # 此时安全回退为截断覆写已有 inode，并以 fsync 保证落盘
        if getattr(exc, "errno", None) == errno.EBUSY:
            with open(temp_path, "r", encoding="utf-8") as src, open(target, "w", encoding="utf-8") as dst:
                dst.write(src.read())
                dst.flush()
                os.fsync(dst.fileno())
        else:
            raise
    try:
        os.chmod(target, 0o600)
    except OSError:
        pass


def remove_env(keys: set[str] | list[str] | tuple[str, ...]) -> None:
    targets = {str(k) for k in keys}
    invalid = sorted(k for k in targets if not _ENV_KEY_RE.match(k))
    if invalid:
        raise EnvValueError(f"非法的环境变量键名: {', '.join(invalid)}")
    ENV_FILE.parent.mkdir(parents=True, exist_ok=True)
    # 审计 P0-2(2026-09-13)：.env 是全站配置唯一写入口，此处读-改-写必须整体持锁。
    # 旧实现无锁 → 两个并发保存（风控页 / 通知页 / LLM 页 / 密钥页 / 策略回滚）各自
    # 基于同一份旧文本回写，后写者静默覆盖先写者；而两者都会改写本进程 os.environ，
    # 于是接口双双返回「已保存」，磁盘却只剩一份。锁文件与被保护文件同目录（flock，
    # 进程崩溃自动释放）。锁只覆盖 RMW，不覆盖 os.environ 同步（后者无 I/O）。
    with file_lock(ENV_FILE):
        existing = ENV_FILE.read_text(encoding="utf-8").splitlines() if ENV_FILE.exists() else []
        result = []
        for line in existing:
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped and stripped.split("=", 1)[0].strip() in targets:
                continue
            result.append(line)
        fd, temp_path = tempfile.mkstemp(prefix=".r20-env-", dir=ENV_FILE.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write("\n".join(result).rstrip() + "\n"); handle.flush(); os.fsync(handle.fileno())
            _safe_commit_file(temp_path, ENV_FILE)
        finally:
            if os.path.exists(temp_path): os.unlink(temp_path)
    for key in targets: os.environ.pop(key, None)


def update_env(values: Mapping[str, str | bool | None]) -> None:
    ENV_FILE.parent.mkdir(parents=True, exist_ok=True)
    # 审计 P0-2(2026-09-13)：同上，整个 RMW 持 file_lock；丢更新与「UI 显示已生效、
    # 磁盘未生效」的谎报都源于此处无锁。绝不能只锁写那一半（会退化成另一种丢更新）。
    with file_lock(ENV_FILE):
        existing: list[str] = ENV_FILE.read_text(encoding="utf-8").splitlines() if ENV_FILE.exists() else []
        remaining = {
            str(key): sanitize_env_value(str(key), value)
            for key, value in values.items()
            if key in MANAGED_KEYS and value is not None
        }
        result: list[str] = []
        for line in existing:
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                result.append(line)
                continue
            key = stripped.split("=", 1)[0].strip()
            if key not in remaining:
                result.append(line)
                continue
            value = remaining.pop(key)
            result.append(f"{key}={value}")
        if remaining:
            if result and result[-1]:
                result.append("")
            result.extend(f"{key}={value}" for key, value in remaining.items())

        fd, temp_path = tempfile.mkstemp(prefix=".r20-env-", dir=ENV_FILE.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write("\n".join(result) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            _safe_commit_file(temp_path, ENV_FILE)
        finally:
            if os.path.exists(temp_path):
                os.unlink(temp_path)
    for key, value in values.items():
        if key in MANAGED_KEYS and value is not None:
            os.environ[key] = str(value)
    refresh_settings()
