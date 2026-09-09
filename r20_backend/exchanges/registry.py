"""多交易所适配器注册表：场所选择与能力门禁的单一入口。

上层（因子聚合 / ExecutionRouter / 后台配置）只认 venue 字符串；任何执行类请求
先过 ``require_execution``，未开闸场所 fail-closed 显式拒绝。
"""
from __future__ import annotations

from typing import Dict, Optional

from .base import BaseExchangeAdapter, ExchangeCapabilityError, canonical_base
from .binance import BinanceAdapter
from .gate import GateAdapter

_ADAPTERS: Dict[str, type] = {
    "binance": BinanceAdapter,
    "gate": GateAdapter,
    # "okx": OKXAdapter,   # Phase 1 后续：包装现有 market_data_service 行为零变化
}

_INSTANCES: Dict[str, BaseExchangeAdapter] = {}


def get_adapter(venue: str) -> BaseExchangeAdapter:
    key = str(venue or "").strip().lower()
    cls = _ADAPTERS.get(key)
    if cls is None:
        raise ExchangeCapabilityError(f"未知交易所 venue={venue!r}，可用: {sorted(_ADAPTERS)}")
    if key not in _INSTANCES:
        _INSTANCES[key] = cls()
    return _INSTANCES[key]


def registered_venues() -> list:
    return sorted(_ADAPTERS)


def is_registered(venue: str) -> bool:
    return str(venue or "").strip().lower() in _ADAPTERS


def require_execution(venue: str) -> None:
    """执行门禁：Phase 3 前所有非 OKX 场所一律显式拒绝（永不静默）。"""
    adapter = get_adapter(venue)
    cap = adapter.capabilities
    if not cap.supports_orders:
        raise ExchangeCapabilityError(
            f"{cap.display_name}: 实盘执行未开闸（Phase 3 门槛：单所 ≥100 笔可信样本）。"
            f"当前该场所仅提供只读行情。"
        )


def resolve_symbol(symbol: str, venue: str) -> str:
    """canonical/任意写法 → 指定场所原生 instId。"""
    return get_adapter(venue).native_symbol(symbol)
