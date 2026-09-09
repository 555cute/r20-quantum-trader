"""R20 多交易所适配层（Phase 1）。

- base: ExchangeCapabilities 能力表 / InstrumentSpec / BaseExchangeAdapter
- binance: 币安 USDT-M 只读行情适配器
- gate: Gate.io V4 永续只读行情适配器
- registry: venue 注册表 + 执行门禁 require_execution()
"""
from .base import (
    BaseExchangeAdapter,
    ExchangeCapabilities,
    ExchangeCapabilityError,
    InstrumentSpec,
    canonical_base,
)
from .binance import BinanceAdapter
from .gate import GateAdapter
from .okx import OKXPublicAdapter
from .registry import (
    ADAPTER_EXECUTION_ENABLED,
    get_adapter,
    is_registered,
    registered_venues,
    require_execution,
    resolve_symbol,
)

__all__ = [
    "BaseExchangeAdapter", "ExchangeCapabilities", "ExchangeCapabilityError",
    "InstrumentSpec", "canonical_base", "BinanceAdapter", "GateAdapter",
    "OKXPublicAdapter", "ADAPTER_EXECUTION_ENABLED",
    "get_adapter", "is_registered", "registered_venues", "require_execution",
    "resolve_symbol",
]
