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

# 经「适配器」下单的场所开闸表——Phase 3 实现真实 place_order 后逐所置 True。
# 注意 okx 的实盘能力并没有消失：下单/撤单/云端 OCO 在 scripts/ai_factor_trader
# 的 V5 REST 直签链路中稳定运行（CLI 已于 v7.9.0 全删）；本表只管「是否通过
# exchanges 适配器执行」。
ADAPTER_EXECUTION_ENABLED: Dict[str, bool] = {
    "okx": False, "binance": False, "gate": False,
}


_TRUE_VALUES = ("1", "true", "yes", "on")


def _env_on(name: str) -> bool:
    return str(os.environ.get(name, "0")).strip().lower() in _TRUE_VALUES


def execution_open(venue: str, environment: str = "live") -> bool:
    """场所执行开闸的统一判定（运行时读 env，支持热切换无需改码）。

    US-002 双轴门禁——本地执行许可按「交易所资金环境」再分档：
    - gate live 档：默认关闸，需显式 ``R20_GATE_EXECUTION=1``（语义不变）；
    - gate 沙盒档（sandbox/demo/testnet）：独立开关 ``R20_GATE_DEMO_EXECUTION``
      （默认 0）——打开只放行**模拟盘真实发送**，绝不标示/充当 LIVE 实盘；
    - 其余场所：静态表（binance 等未实装，恒关）。
    单参调用 ``execution_open(venue)`` = live 档语义，逐字节兼容旧判定。
    """
    key = str(venue or "").strip().lower()
    if key == "gate":
        flag = ("R20_GATE_DEMO_EXECUTION" if is_sandbox_environment(environment)
                else "R20_GATE_EXECUTION")
        return _env_on(flag)
    return ADAPTER_EXECUTION_ENABLED.get(key, False)

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
        api_key, _secret = venue_credentials(venue_key)
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


def venue_credentials(venue: str) -> Tuple[str, str]:
    """从加密密钥库读该场所 (api_key, secret_key)；未配置返回 ("", "")。"""
    key = str(venue or "").strip().upper()
    try:
        from r20_gateway.secrets import load_secrets
        vals = load_secrets()
    except Exception:
        vals = {}
    return (str(vals.get(f"{key}_API_KEY") or ""), str(vals.get(f"{key}_SECRET_KEY") or ""))


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
        needed = ("R20_GATE_DEMO_EXECUTION" if is_sandbox_environment(env)
                  else "R20_GATE_EXECUTION")
        raise ExchangeCapabilityError(
            f"{cap.display_name}: 适配器执行未开闸。"
            + (f"Gate {env} 档需显式设 {needed}=1（后台凭证就绪后再开）。"
               if cap.venue == "gate" else
               "当前该场所仅提供只读行情。")
            + ("注：OKX 实盘执行走 ai_factor_trader 遗留链路，不经本路由。"
               if cap.venue == "okx" else "")
        )
    if not cap.supports_orders:
        raise ExchangeCapabilityError(f"{cap.display_name}: supports_orders=False，适配器未实装下单")


def resolve_symbol(symbol: str, venue: str) -> str:
    """canonical/任意写法 → 指定场所原生 instId。"""
    return get_adapter(venue).native_symbol(symbol)
