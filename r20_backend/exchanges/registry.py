"""多交易所适配器注册表：场所选择与能力门禁的单一入口。

上层（因子聚合 / ExecutionRouter / 后台配置）只认 venue 字符串；任何执行类请求
先过 ``require_execution``，未开闸场所 fail-closed 显式拒绝。

凭证与网络档位（2026-09-09 Phase 2 起）：
- ``venue_credentials(venue)``：从加密密钥库读 API Key/Secret（只读行情用不到，
  Phase 3 执行与更高限频档消费；后台「多所凭证」面板负责录入）；
- ``R20_BINANCE_TESTNET`` / ``R20_GATE_TESTNET``=1 且该所适配器声明 test_url 时，
  注册表实例化即指向官方沙盒端点。
"""
from __future__ import annotations

import os
from typing import Dict, Optional, Tuple

from .base import BaseExchangeAdapter, ExchangeCapabilityError, canonical_base
from .binance import BinanceAdapter
from .gate import GateAdapter
from .okx import OKXPublicAdapter

_ADAPTERS: Dict[str, type] = {
    "okx": OKXPublicAdapter,       # 只读行情；生产执行仍居 ai_factor_trader 遗留路径
    "binance": BinanceAdapter,
    "gate": GateAdapter,
}

# 经「适配器」下单的场所开闸表——Phase 3 实现真实 place_order 后逐所置 True。
# 注意 okx 的实盘能力并没有消失：下单/撤单/云端 OCO 在 scripts/ai_factor_trader
# 的 CLI+REST 遗留链路中稳定运行；本表只管「是否通过 exchanges 适配器执行」。
ADAPTER_EXECUTION_ENABLED: Dict[str, bool] = {
    "okx": False, "binance": False, "gate": False,
}

_INSTANCES: Dict[str, BaseExchangeAdapter] = {}


def venue_testnet_enabled(venue: str) -> bool:
    key = str(venue or "").strip().lower()
    return str(os.environ.get(f"R20_{key.upper()}_TESTNET", "0")).strip().lower() in ("1", "true", "yes", "on")


def get_adapter(venue: str) -> BaseExchangeAdapter:
    key = str(venue or "").strip().lower()
    cls = _ADAPTERS.get(key)
    if cls is None:
        raise ExchangeCapabilityError(f"未知交易所 venue={venue!r}，可用: {sorted(_ADAPTERS)}")
    if key not in _INSTANCES:
        _INSTANCES[key] = cls()
    return _INSTANCES[key]


def clear_instances() -> None:
    """档位/凭证热切换后丢弃缓存实例，下次 get_adapter 按新环境重建。"""
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


def require_execution(venue: str) -> None:
    """执行门禁：任何场所经适配器下单前先问这里。未开闸一律 fail-closed。"""
    adapter = get_adapter(venue)
    cap = adapter.capabilities
    if not ADAPTER_EXECUTION_ENABLED.get(cap.venue, False):
        raise ExchangeCapabilityError(
            f"{cap.display_name}: 适配器执行未开闸（Phase 3 门槛：单所 ≥100 笔可信样本）。"
            f"当前该场所仅提供只读行情"
            + ("。注：OKX 实盘执行走 ai_factor_trader 遗留链路，不经本路由。"
               if cap.venue == "okx" else "。")
        )
    if not cap.supports_orders:
        raise ExchangeCapabilityError(f"{cap.display_name}: supports_orders=False，适配器未实装下单")


def resolve_symbol(symbol: str, venue: str) -> str:
    """canonical/任意写法 → 指定场所原生 instId。"""
    return get_adapter(venue).native_symbol(symbol)
