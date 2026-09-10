"""R20 多交易所适配层（Phase 1）。

- base: ExchangeCapabilities 能力表 / InstrumentSpec / BaseExchangeAdapter
- binance: 币安 USDT-M 只读行情适配器
- gate: Gate.io V4 永续只读行情适配器
- okx: OKX V5 公共行情只读适配器
- env_profiles: (venue, environment) → 端点档单一入口（US-001）
- identity: AccountKey 三元身份 (venue, environment, credential fingerprint)（US-002）
- registry: venue 注册表 + 执行门禁 require_execution()（双轴开关，US-002）
"""
from . import env_profiles
from .base import (
    BaseExchangeAdapter,
    ExchangeCapabilities,
    ExchangeCapabilityError,
    InstrumentSpec,
    canonical_base,
)
from .binance import BinanceAdapter
from .diagnostics import diagnose_venue_connection
from .gate import GateAdapter
from .identity import (
    ANON_CREDENTIAL,
    AccountKey,
    credential_fingerprint,
    is_sandbox_environment,
)
from .okx import OKXPublicAdapter
from .registry import (
    ADAPTER_EXECUTION_ENABLED,
    clear_instances,
    execution_open,
    gate_environment_axis,
    get_adapter,
    is_registered,
    registered_venues,
    require_execution,
    resolve_symbol,
    venue_credentials,
    venue_passphrase,
    venue_testnet_enabled,
)

__all__ = [
    "BaseExchangeAdapter", "ExchangeCapabilities", "ExchangeCapabilityError",
    "InstrumentSpec", "canonical_base", "BinanceAdapter", "GateAdapter",
    "OKXPublicAdapter", "ADAPTER_EXECUTION_ENABLED", "execution_open",
    "get_adapter", "is_registered", "registered_venues", "require_execution",
    "resolve_symbol", "clear_instances", "venue_credentials", "venue_passphrase",
    "venue_testnet_enabled", "env_profiles", "diagnose_venue_connection",
    "AccountKey", "ANON_CREDENTIAL", "credential_fingerprint",
    "is_sandbox_environment", "gate_environment_axis",
]
