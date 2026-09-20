"""R20 Quantum Hedge Fund Investment Committee (Trading Desk Council).
Fully Re-architected in v7.2.2 with Full Account Awareness:
1. Symmetrical Trader Roles (Equal Peer Traders):
   - Trader A: Senior Trend-Pullback Trader (Conservative & High Win-rate)
   - Trader B: Senior Momentum-Breakout Trader (Aggressive & High R:R)
   - Trader C: Senior Quantitative & Calculus Trader (Data-Driven & Microstructure)
2. Trade Proposal & Portfolio Review Protocol:
   Every trader analyzes:
   - Account available capital (USDT balance), position count & risk limits;
   - Active position lifecycle (HOLD / CLOSE_MARKET / UPDATE_SL for trailing profit);
   - Pending maker limit orders lifecycle (CANCEL stale orders vs. KEEP active setups);
   - Opening/Pyramiding proposals for all 6 active instruments with exact parameters.
3. Chief Investment Officer (CIO / Head of Trading) Verdict:
   The CIO reviews all submitted proposals, weighs cross-examination feedback, determines
   which trader's plan to fund and execute (or rejects all for WAIT), and outputs the
   final deterministic trading JSON contract covering decisions, position_management,
   and pending_orders_management.
"""

from __future__ import annotations

from r20_backend.council.debate import (
    _call_single_trader_critique as _core__call_single_trader_critique,
    _call_single_trader as _core__call_single_trader,
    _render_seat_prompt,
    execute_council_debate as _core_execute_council_debate,
)
from r20_backend.council.presets import (
    apply_preset_suite as _core_apply_preset_suite,
    get_available_presets,
    get_preset_suites,
    reset_role_template as _core_reset_role_template,
)
from r20_backend.council.roster import (
    clamp_council_timeout,
    resolve_seat_model,
    seat_model_health,
    validate_council_roles,
    validate_seat_model_bindings,
)
from r20_backend.council.policy import (
    DEFAULT_CONSENSUS_MODE,
    ALL_AVAILABLE_PRESETS,
    CIO_MIN_ARBITRATION_TIME,
    COUNCIL_PRESET_SUITES,
    DEFAULT_COUNCIL_TIMEOUT,
    DEFAULT_PRESET_TEMPLATES,
    MAX_COUNCIL_TIMEOUT,
    MIN_COUNCIL_TIMEOUT,
    MIN_SAFE_REASONING_TIME,
    VALID_CONSENSUS_MODES,
)

import concurrent.futures
import functools
import json
import os
import re
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from r20_backend.file_locks import file_lock

_BJ = timezone(timedelta(hours=8))

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
COUNCIL_CONFIG_FILE = DATA_DIR / "council_config.json"

# 审计 P2-14：CIO 裁决最低预算（秒）——唯一会被真正执行的输出
# 60s 总预算在真实网关 RT（单席 20~250s、参谋并行+CIO 至少两段串行）下必然整体
# 超时静默降级（50 周期 0 成功实测，2026-09-10）；240s 兼顾决策时效与调度器 600s 硬超时。
# 审计 P2-13：超时预算单一事实源。旧实现三套默认（schemas 60 / 本模块 240 / 前端 240）
# 且只有导入路径夹取 → 手改配置文件写 5000 会被原样当预算用，撞上调度器 600s 击杀。













def _locked_council(fn):
    """装饰器（审计 P2-6）：council_config.json 是 RMW 目标，一次辩论会重写多次；
    旧实现无锁 → 面板保存与辩论写回互相覆盖（席位提示词丢失的复现路径之一）。
    使用可重入 file_lock，嵌套 save_council_config 不会自锁。"""
    @functools.wraps(fn)
    def _wrapper(*args, **kwargs):
        with file_lock(COUNCIL_CONFIG_FILE):
            return fn(*args, **kwargs)
    return _wrapper


def _atomic_write_json(file_path: Path, data: Any) -> None:
    file_path.parent.mkdir(parents=True, exist_ok=True)
    temp_dir = file_path.parent
    with tempfile.NamedTemporaryFile("w", dir=temp_dir, delete=False, encoding="utf-8") as tf:
        json.dump(data, tf, ensure_ascii=False, indent=2)
        # 第一百五十四刀：与另外 5 个原子写辅助统一 —— rename 前 fsync，
        # 否则断电可能留下空/截断文件（rename 的原子性管不了数据是否已落盘）
        tf.flush()
        os.fsync(tf.fileno())
        temp_name = tf.name
    os.replace(temp_name, file_path)




@_locked_council
def load_council_config() -> Dict[str, Any]:
    """读取委员会配置；**绝不**用工厂默认覆盖可解析的用户文件。

    审计 P1-4a：旧实现两道静默覆盖（读闸白名单不匹配 / JSON 损坏）都是
    `except: pass` → 落到 `_atomic_write_json(default)`，用户自写提示词无声蒸发。
    现在：可解析 → 原样返回（结构问题只打警告标记，由写闸/UI 提示）；
    损坏 → 先备份成 `council_config_corrupt_*.json` 再重建默认（留痕可恢复）。
    """
    if COUNCIL_CONFIG_FILE.is_file():
        try:
            with open(COUNCIL_CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and "roles" in data:
                mode = str(data.get("consensus_mode", DEFAULT_CONSENSUS_MODE)).strip().lower()
                if mode not in VALID_CONSENSUS_MODES:
                    data["consensus_mode"] = DEFAULT_CONSENSUS_MODE
                problem = validate_council_roles(data.get("roles"))
                if problem:
                    # 保留用户数据，只标记（旧实现在这里直接覆盖成工厂默认）
                    data["config_warning"] = f"委员会配置结构异常：{problem}；已保留原文件，请在面板补齐仲裁官席位"
                if _migrate_untouched_preset_prompts(data):
                    data["updated_at"] = datetime.now(_BJ).isoformat(sep=" ", timespec="seconds")
                    _atomic_write_json(COUNCIL_CONFIG_FILE, data)
                return data
            corrupt_reason = "缺少 roles 字段"
        except (OSError, json.JSONDecodeError, ValueError, TypeError) as exc:
            corrupt_reason = str(exc)[:160]
        # 走到这里 = 文件存在但不可解析/结构不可用 → 先留备份再重建，绝不静默吞掉
        try:
            stamp = datetime.now(_BJ).strftime("%Y%m%d_%H%M%S")
            rescue = DATA_DIR / f"council_config_corrupt_{stamp}.json"
            rescue.write_bytes(COUNCIL_CONFIG_FILE.read_bytes())
            print(f"[council] 委员会配置不可用（{corrupt_reason}），已备份为 {rescue.name} 并重建工厂默认", flush=True)
        except OSError as exc:
            print(f"[council] 委员会配置不可用且备份失败：{exc}", flush=True)

    default_config: Dict[str, Any] = {
        "enabled": False,
        "consensus_mode": DEFAULT_CONSENSUS_MODE,
        "timeout_seconds": DEFAULT_COUNCIL_TIMEOUT,
        "roles": {k: dict(v) for k, v in DEFAULT_PRESET_TEMPLATES.items()},
        "updated_at": datetime.now(_BJ).isoformat(sep=" ", timespec="seconds"),
    }
    _atomic_write_json(COUNCIL_CONFIG_FILE, default_config)
    return default_config


@_locked_council
def save_council_config(config: Dict[str, Any], *, enforce_models: bool = True) -> Dict[str, Any]:
    if not isinstance(config, dict):
        raise ValueError("Council config must be a dict")
    roles = config.get("roles")
    if not isinstance(roles, dict) or not roles:
        raise ValueError("委员会至少需要包含角色配置")

    # 审计 P1-4a：读写两侧共用同一校验，杜绝"写得进、读不回"的白名单漂移
    problem = validate_council_roles(roles)
    if problem:
        if "仲裁官" in problem:
            raise ValueError("委员会必须保留至少一位首席终审仲裁官/交易总监(CIO)！")
        raise ValueError(f"委员会配置不合法：{problem}")
    # 审计 P1-4b：不允许再写出"查不到就静默回落主脑"的席位绑定
    previous: Dict[str, Any] = {}
    try:
        # 只用真路径读旧配置：隔离执行类测试会把 COUNCIL_CONFIG_FILE 换成 MagicMock，
        # 而 MagicMock 既是 os.PathLike 又能被 open() 当**文件描述符**解释——直接 open() 会
        # 打开并随后关闭 fd 1（把 stdout 关掉，套件退出码变 120）。这里显式限定 str/Path。
        candidate = COUNCIL_CONFIG_FILE
        config_path = Path(candidate) if isinstance(candidate, (str, Path)) else None
        if config_path is not None and config_path.is_file():
            with open(config_path, "r", encoding="utf-8") as handle:
                previous = (json.load(handle) or {}).get("roles") or {}
    except (OSError, json.JSONDecodeError, AttributeError, TypeError, ValueError):
        previous = {}
    model_problems = validate_seat_model_bindings(roles, previous)
    if model_problems and enforce_models:
        raise ValueError("；".join(model_problems))
    if model_problems and not enforce_models:
        # 导入/套用整包时不做整体拒绝：清空未登记绑定（=跟随主脑）并如实回报，
        # 避免"跨机导入因本机没有某模型而整包失败"，同时绝不静默保留一个查不到的 id。
        cleared = []
        for role_id, role in roles.items():
            if not isinstance(role, dict):
                continue
            requested = str(role.get("model_id") or "").strip()
            if requested and any(f"席位 {role_id} " in p for p in model_problems):
                role["model_id"] = ""
                cleared.append({"role_id": role_id, "model_id": requested})
        if cleared:
            config["cleared_model_bindings"] = cleared

    for role_id, role in roles.items():
        if not isinstance(role, dict):
            raise ValueError(f"角色 {role_id} 配置必须为字典")
        role["id"] = role_id
        role.setdefault("enabled", True)
        role.setdefault("weight", 0.3)
        role.setdefault("reasoning_effort", "medium")
        role.setdefault("temperature", 0.2)

    mode = str(config.get("consensus_mode", DEFAULT_CONSENSUS_MODE)).strip().lower()
    if mode not in VALID_CONSENSUS_MODES:
        mode = DEFAULT_CONSENSUS_MODE
    config["consensus_mode"] = mode
    # 审计 P2-13：超时预算在任何写入口都夹到 [MIN, MAX]（含直接改文件后保存的路径）
    config["timeout_seconds"] = clamp_council_timeout(config.get("timeout_seconds", DEFAULT_COUNCIL_TIMEOUT))

    config["updated_at"] = datetime.now(_BJ).isoformat(sep=" ", timespec="seconds")
    _atomic_write_json(COUNCIL_CONFIG_FILE, config)
    return config


# ---- 预设对齐迁移：仅替换"仍为旧出厂文案"的角色提示词(sha256 前16位识别)，用户定制一律保留 ----
_LEGACY_PRESET_PROMPT_HASHES: Dict[str, Any] = {
    "trader_trend": {"28fc1b0874f20dfc", "5c13b5e47c5cc054", "8bc787c9f01be9ac"},
    "trader_momentum": {"37fb3f948d309f3b", "ee75c86b42cf6a2c", "8e23b4f5ad1699d6"},
    "trader_quant": {"5a18438f6afe6c87", "2704fd8c6000df18", "51ecd08f51160da9"},
    "cio": {"165538e81c0bec8f", "88f1ab886c89a086"},
}

def _migrate_untouched_preset_prompts(config: Dict[str, Any]) -> bool:
    import hashlib
    changed = False
    for role_id, role in (config.get("roles") or {}).items():
        legacy_hash = _LEGACY_PRESET_PROMPT_HASHES.get(str(role_id))
        if not legacy_hash or not isinstance(role, dict):
            continue
        prompt = str(role.get("prompt", ""))
        digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16]
        is_legacy = (digest == legacy_hash) if isinstance(legacy_hash, str) else (digest in legacy_hash)
        if is_legacy:
            new_tpl = DEFAULT_PRESET_TEMPLATES.get(str(role_id))
            if new_tpl and prompt != new_tpl["prompt"]:
                role["prompt"] = new_tpl["prompt"]
                role["description"] = new_tpl["description"]
                changed = True
    return changed


# ---- 委员会配置导入/导出（对齐提示词工坊策略包体验） ----
COUNCIL_EXPORT_FORMAT = "r20-council-config"
COUNCIL_EXPORT_VERSION = 1
_VALID_REASONING_EFFORTS = {"none", "minimal", "low", "medium", "high"}

def export_council_config() -> Dict[str, Any]:
    """自描述导出包：一个 JSON 文件即可完整还原投委会席位、提示词与议事规则。"""
    config = load_council_config()
    return {
        "format": COUNCIL_EXPORT_FORMAT,
        "version": COUNCIL_EXPORT_VERSION,
        "exported_at": datetime.now(_BJ).isoformat(sep=" ", timespec="seconds"),
        "config": {
            "enabled": bool(config.get("enabled", False)),
            "consensus_mode": config.get("consensus_mode", DEFAULT_CONSENSUS_MODE),
            "timeout_seconds": config.get("timeout_seconds", DEFAULT_COUNCIL_TIMEOUT),
            "roles": config.get("roles", {}),
        },
    }

def _backup_council_config() -> str:
    if not COUNCIL_CONFIG_FILE.is_file():
        return ""
    stamp = datetime.now(_BJ).strftime("%Y%m%d_%H%M%S")
    dst = DATA_DIR / f"council_config_backup_{stamp}.json"
    dst.write_bytes(COUNCIL_CONFIG_FILE.read_bytes())
    for stale in sorted(DATA_DIR.glob("council_config_backup_*.json"))[:-10]:
        try:
            stale.unlink()
        except OSError:
            pass
    return dst.name

@_locked_council
def import_council_config(payload: Dict[str, Any]) -> Dict[str, Any]:
    """导入投委会配置：接受标准导出包或裸 {roles:...} 对象；结构校验+字段清洗，
    导入前自动备份当前配置（保留最近 10 份）。"""
    if not isinstance(payload, dict):
        raise ValueError("导入内容必须是 JSON 对象")
    src = payload
    if payload.get("format") == COUNCIL_EXPORT_FORMAT and isinstance(payload.get("config"), dict):
        src = payload["config"]
    roles_in = src.get("roles")
    if not isinstance(roles_in, dict) or not roles_in:
        raise ValueError("导入文件缺少有效的 roles 席位配置")

    clean_roles: Dict[str, Any] = {}
    for raw_id, role in roles_in.items():
        if not isinstance(role, dict):
            raise ValueError(f"角色 {raw_id} 配置必须为字典")
        prompt = str(role.get("prompt", "")).strip()
        if not prompt:
            raise ValueError(f"角色 {raw_id} 缺少提示词 prompt")
        if len(prompt) > 20000:
            raise ValueError(f"角色 {raw_id} 提示词过长（>20000 字符），请精简后重试")
        try:
            weight = min(1.0, max(0.05, float(role.get("weight", 0.3))))
        except (TypeError, ValueError):
            weight = 0.3
        try:
            temperature = min(1.0, max(0.0, float(role.get("temperature", 0.2))))
        except (TypeError, ValueError):
            temperature = 0.2
        effort = str(role.get("reasoning_effort", "medium")).strip().lower()
        if effort not in _VALID_REASONING_EFFORTS:
            effort = "medium"
        rid = str(raw_id).strip()[:48] or "role"
        clean_roles[rid] = {
            "id": rid,
            "name": str(role.get("name", rid))[:60],
            "role_title": str(role.get("role_title", ""))[:80],
            "description": str(role.get("description", ""))[:200],
            "prompt": prompt,
            "weight": weight,
            "temperature": temperature,
            "reasoning_effort": effort,
            "enabled": bool(role.get("enabled", True)),
            "is_arbitrator": bool(role.get("is_arbitrator", False)) or rid.lower() in {"cio", "arbitrator"},
            "model_id": str(role.get("model_id", ""))[:80],
        }
    try:
        timeout_seconds = min(300.0, max(10.0, float(src.get("timeout_seconds", DEFAULT_COUNCIL_TIMEOUT))))
    except (TypeError, ValueError):
        timeout_seconds = DEFAULT_COUNCIL_TIMEOUT

    backup_file = _backup_council_config()
    saved = save_council_config({
        "enabled": bool(src.get("enabled", False)),
        "consensus_mode": src.get("consensus_mode", DEFAULT_CONSENSUS_MODE),
        "timeout_seconds": timeout_seconds,
        "roles": clean_roles,
    }, enforce_models=False)
    return {
        "roles": list(saved.get("roles", {}).keys()),
        "consensus_mode": saved.get("consensus_mode", DEFAULT_CONSENSUS_MODE),
        "timeout_seconds": saved.get("timeout_seconds", DEFAULT_COUNCIL_TIMEOUT),
        "backup_file": backup_file,
        # 审计 P1-4b：跨机导入里本机没有的模型一律清空（=跟随主脑），并在此点名
        "cleared_model_bindings": saved.get("cleared_model_bindings") or [],
    }






@_locked_council
def apply_preset_suite(suite_id: str) -> Dict[str, Any]:
    """薄壳：调用时解析门面模块全局，使测试的 patch / 直接赋值生效。

    实现已迁往 r20_backend.council.presets（结构优化阶段 2 / B5 第二刀）。
    """
    return _core_apply_preset_suite(load_council_config, save_council_config, suite_id)


@_locked_council
def reset_role_template(role_id: str) -> Dict[str, Any]:
    """薄壳：调用时解析门面模块全局，使测试的 patch / 直接赋值生效。

    实现已迁往 r20_backend.council.presets（结构优化阶段 2 / B5 第二刀）。
    """
    return _core_reset_role_template(load_council_config, save_council_config, role_id)


def _call_single_trader(
    role_id: str,
    role_spec: Dict[str, Any],
    market_prompt: str,
    master_constitutional_rules: str,
    timeout: float = 20.0,
    runtime_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """薄壳：调用时解析门面模块全局，使测试的 patch / 直接赋值生效。

    实现已迁往 r20_backend.council.debate（结构优化阶段 2 / B5）。
    """
    return _core__call_single_trader(resolve_seat_model, role_id, role_spec, market_prompt, master_constitutional_rules, timeout, runtime_context)


def _call_single_trader_critique(
    role_id: str,
    role_spec: Dict[str, Any],
    my_proposal: str,
    peer_proposals: str,
    master_constitutional_rules: str,
    timeout: float = 15.0,
    runtime_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """薄壳：调用时解析门面模块全局，使测试的 patch / 直接赋值生效。

    实现已迁往 r20_backend.council.debate（结构优化阶段 2 / B5）。
    """
    return _core__call_single_trader_critique(resolve_seat_model, role_id, role_spec, my_proposal, peer_proposals, master_constitutional_rules, timeout, runtime_context)


def execute_council_debate(
    market_prompt: str,
    original_system_prompt: str,
    timeout: float = 240.0,
    runtime_context: Optional[Dict[str, Any]] = None,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """薄壳：调用时解析门面模块全局，使测试的 patch / 直接赋值生效。

    实现已迁往 r20_backend.council.debate（结构优化阶段 2 / B5）。
    """
    return _core_execute_council_debate(load_council_config, resolve_seat_model, _call_single_trader, _call_single_trader_critique, market_prompt, original_system_prompt, timeout, runtime_context)
