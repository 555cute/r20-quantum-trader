"""多交易所适配器注册表：场所选择与能力门禁的单一入口。

上层（因子聚合 / ExecutionRouter / 后台配置）只认 venue 字符串；任何执行类请求
先过 ``require_execution``，未开闸场所 fail-closed 显式拒绝。

凭证与网络档位（US-001 起 = env_profiles 单一入口；US-002 起 = AccountKey 三元键）：
- ``venue_credentials(venue)``：从加密密钥库读 API Key/Secret（只读行情用不到，
  Phase 3 执行与更高限频档消费；后台「多所凭证」面板负责录入）；
- 端点档由 ``env_profiles`` 按 (venue, environment) 解析；旧
  ``R20_BINANCE_TESTNET``/``R20_GATE_TESTNET``=1 兼容映射（binance→demo 同旧 URL、
  gate→sandbox 双候选探测择优并钉死，禁签名跨域回退）；
- ``_INSTANCES`` 缓存键 = AccountKey (venue, environment, credential_fingerprint)：
  同所不同环境、同环境不同凭证代际**永不互串**；密钥轮换后自动重建实例。
- 执行开闸双轴（US-002）：gate live 档 ``R20_GATE_EXECUTION``、沙盒档
  ``R20_GATE_DEMO_EXECUTION``（默认 0）各自独立，任一缺失 fail-closed。
"""
from __future__ import annotations

import os
from typing import Dict, Optional, Tuple

from .base import BaseExchangeAdapter, ExchangeCapabilityError, canonical_base
from .binance import BinanceAdapter
from .gate import GateAdapter
from .identity import (AccountKey, credential_fingerprint,
                       is_sandbox_environment)
from .okx import OKXPublicAdapter

_ADAPTERS: Dict[str, type] = {
    "okx": OKXPublicAdapter,       # 只读行情；生产执行仍居 ai_factor_trader 遗留路径
    "binance": BinanceAdapter,
    "gate": GateAdapter,
}

# 经「适配器」下单的场所开闸——G9 统一后不再手工维护双源：单一事实 =
# 各所能力表 ``adapter_execution_flag`` 声明（gate 已声明；okx 实盘执行走
# ai_factor_trader 直签链路、binance orders 未实装 → 均未声明，恒关）。
# 本映射仅为向后兼容再导出保留，由能力表推导，勿再手改。
ADAPTER_EXECUTION_ENABLED: Dict[str, bool] = {}


_TRUE_VALUES = ("1", "true", "yes", "on")


def _env_on(name: str) -> bool:
    return str(os.environ.get(name, "0")).strip().lower() in _TRUE_VALUES


def execution_open(venue: str, environment: str = "live") -> bool:
    """场所执行开闸的统一判定（运行时读 env，支持热切换无需改码）。

    G9 单源判定 = 能力表 ``adapter_execution_flag`` 声明 AND 环境双轴旗标：
    - 能力表未声明旗标（okx 实盘走 ai_factor_trader 直签链路）→ 结构性恒关，
      与 env 无关；binance/gate 均已声明（US-005 起三家执行面平权）；
    - 已声明（gate/binance）：live 档读声明旗标原样，沙盒档（sandbox/demo/testnet）
      读 ``<前缀>DEMO_EXECUTION`` 变体（R20_GATE_EXECUTION→R20_GATE_DEMO_
      EXECUTION）——打开只放行**模拟盘真实发送**，绝不标示/充当 LIVE 实盘。
    单参调用 ``execution_open(venue)`` = live 档语义，逐字节兼容旧判定。
    """
    key = str(venue or "").strip().lower()
    cls = _ADAPTERS.get(key)
    base_flag = getattr(getattr(cls, "capabilities", None), "adapter_execution_flag", "")
    if not base_flag:
        return False
    if is_sandbox_environment(environment):
        return _env_on(base_flag.replace("EXECUTION", "DEMO_EXECUTION"))
    return _env_on(base_flag)


def _derive_adapter_execution_enabled() -> Dict[str, bool]:
    """由能力表推导兼容映射：仅反映「该所是否声明了 env 开闸路径」。"""
    return {v: bool(getattr(getattr(cls, "capabilities", None),
                            "adapter_execution_flag", "")) for v, cls in _ADAPTERS.items()}


ADAPTER_EXECUTION_ENABLED.update(_derive_adapter_execution_enabled())

#: US-002：缓存键 = AccountKey (venue, environment, credential_fingerprint)
_INSTANCES: Dict[AccountKey, BaseExchangeAdapter] = {}


def venue_testnet_enabled(venue: str) -> bool:
    key = str(venue or "").strip().lower()
    return str(os.environ.get(f"R20_{key.upper()}_TESTNET", "0")).strip().lower() in ("1", "true", "yes", "on")


def gate_environment_axis() -> str:
    """Gate 的交易所资金环境轴（live|demo）——旧 R20_GATE_TESTNET 布尔归并。

    与「本地执行模式（off/dry_run/发送）」是两条独立轴（US-002）：本函数只答
    「打到哪个资金环境」，不答「允不允许发送」。
    """
    from . import env_profiles
    env = env_profiles.legacy_environment_for("gate")
    return "demo" if is_sandbox_environment(env) else "live"


def _account_key(venue_key: str, environment: str) -> AccountKey:
    """组装三元身份键；凭证读取 fail-soft——密钥库异常按 anon，不抛穿上层。"""
    try:
        api_key, _secret = venue_credentials(venue_key, environment)
    except Exception:
        api_key = ""
    return AccountKey(venue=venue_key, environment=environment,
                      fingerprint=credential_fingerprint(api_key))


def get_adapter(venue: str, environment: Optional[str] = None) -> BaseExchangeAdapter:
    """按 AccountKey (venue, environment, credential_fingerprint) 取适配器实例。

    environment=None → 旧 R20_{VENUE}_TESTNET 布尔兼容映射（见 env_profiles）。
    凭证代际入键：``venue_credentials`` 返回的 api_key 变化（轮换/清空）后
    fingerprint 失配 → 自动重建实例，旧凭证连接不残留；读取密钥库异常按
    anon 指纹降级（fail-soft，只读行情面不受牵连）。
    """
    from . import env_profiles
    key = str(venue or "").strip().lower()
    cls = _ADAPTERS.get(key)
    if cls is None:
        raise ExchangeCapabilityError(f"未知交易所 venue={venue!r}，可用: {sorted(_ADAPTERS)}")
    env = str(environment or "").strip().lower() or env_profiles.legacy_environment_for(key)
    cache_key = _account_key(key, env)
    if cache_key not in _INSTANCES:
        _INSTANCES[cache_key] = cls(environment=env)
    return _INSTANCES[cache_key]


def adapter_environment(venue: str) -> str:
    """该场所当前布尔开关解析出的档位名（观测/诊断用，纯读）。"""
    from . import env_profiles
    return env_profiles.legacy_environment_for(str(venue or "").strip().lower())


def clear_instances() -> None:
    """档位/凭证热切换后丢弃全部 AccountKey 缓存实例，下次 get_adapter 重建。"""
    _INSTANCES.clear()


def venue_credentials(venue: str, environment: Optional[str] = None) -> Tuple[str, str]:
    """从加密密钥库读该场所 (api_key, secret_key)；未配置返回 ("", "")。

    支持三所 6 账户独立凭证（US-003）：
    - environment 归属于沙盒/模拟（demo/sandbox/testnet）时：
      优先尝试 {VENUE}_DEMO_* / {VENUE}_TESTNET_* / {VENUE}_SANDBOX_*，未配时回退通用 {VENUE}_*。
    - environment 归属于实盘（live）时：
      优先尝试 {VENUE}_LIVE_*，未配时回退通用 {VENUE}_*。
    - environment 为 None 时：回退当前默认/通用凭证。
    """
    key = str(venue or "").strip().upper()
    try:
        from r20_gateway.secrets import load_secrets
        vals = load_secrets()
    except Exception:
        vals = {}

    env = str(environment or "").strip().lower() if environment is not None else ""
    if env:
        if is_sandbox_environment(env):
            cand_keys = [f"{key}_DEMO_API_KEY", f"{key}_TESTNET_API_KEY", f"{key}_SANDBOX_API_KEY", f"{key}_API_KEY"]
            cand_secs = [f"{key}_DEMO_SECRET_KEY", f"{key}_TESTNET_SECRET_KEY", f"{key}_SANDBOX_SECRET_KEY", f"{key}_SECRET_KEY"]
        else:
            cand_keys = [f"{key}_LIVE_API_KEY", f"{key}_API_KEY"]
            cand_secs = [f"{key}_LIVE_SECRET_KEY", f"{key}_SECRET_KEY"]

        api_key = ""
        for ck in cand_keys:
            v = str(vals.get(ck) or "").strip()
            if v:
                api_key = v
                break

        secret_key = ""
        for cs in cand_secs:
            v = str(vals.get(cs) or "").strip()
            if v:
                secret_key = v
                break
        return (api_key, secret_key)

    return (str(vals.get(f"{key}_API_KEY") or "").strip(), str(vals.get(f"{key}_SECRET_KEY") or "").strip())


def venue_passphrase(venue: str, environment: Optional[str] = None) -> str:
    """从加密密钥库读该场所 Passphrase（OKX 专属；其他所返回空串）。"""
    key = str(venue or "").strip().upper()
    if key != "OKX":
        return ""
    try:
        from r20_gateway.secrets import load_secrets
        vals = load_secrets()
    except Exception:
        vals = {}

    env = str(environment or "").strip().lower() if environment is not None else ""
    if env:
        if is_sandbox_environment(env):
            cand_pass = ["OKX_DEMO_PASSPHRASE", "OKX_PASSPHRASE"]
        else:
            cand_pass = ["OKX_LIVE_PASSPHRASE", "OKX_PASSPHRASE"]
        for cp in cand_pass:
            v = str(vals.get(cp) or "").strip()
            if v:
                return v
        return ""
    return str(vals.get("OKX_PASSPHRASE") or "").strip()


def registered_venues() -> list:
    return sorted(_ADAPTERS)


def is_registered(venue: str) -> bool:
    return str(venue or "").strip().lower() in _ADAPTERS


def require_execution(venue: str, environment: Optional[str] = None) -> None:
    """执行门禁：任何场所经适配器下单前先问这里。未开闸一律 fail-closed。

    environment=None → 按解析出的适配器实例档位取轴（旧 TESTNET 布尔 → 沙盒档
    检查 ``R20_GATE_DEMO_EXECUTION``；live 档维持 ``R20_GATE_EXECUTION``）。
    拒绝文案指向**当前档位实际缺的那把开关**，不误导去开另一档。
    """
    adapter = get_adapter(venue, environment)
    cap = adapter.capabilities
    env = str(getattr(adapter, "environment", "live") or "live")
    if not execution_open(cap.venue, env):
        if cap.venue == "gate":
            needed = ("R20_GATE_DEMO_EXECUTION" if is_sandbox_environment(env)
                      else "R20_GATE_EXECUTION")
            prompt = f"Gate {env} 档需显式设 {needed}=1（后台凭证就绪后再开）。"
        elif cap.venue == "binance":
            needed = ("R20_BINANCE_DEMO_EXECUTION" if is_sandbox_environment(env)
                      else "R20_BINANCE_EXECUTION")
            prompt = f"Binance {env} 档需显式设 {needed}=1（后台凭证就绪后再开）。"
        else:
            prompt = "当前该场所仅提供只读行情。"

        raise ExchangeCapabilityError(
            f"{cap.display_name}: 适配器执行未开闸。{prompt}"
            + ("注：OKX 实盘执行走 ai_factor_trader 遗留链路，不经本路由。"
               if cap.venue == "okx" else "")
        )
    if not cap.supports_orders:
        raise ExchangeCapabilityError(f"{cap.display_name}: supports_orders=False，适配器未实装下单")


def resolve_symbol(symbol: str, venue: str) -> str:
    """canonical/任意写法 → 指定场所原生 instId。"""
    return get_adapter(venue).native_symbol(symbol)
