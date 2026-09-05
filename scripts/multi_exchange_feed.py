"""Helper functions to fetch candles and orderbooks with multi-exchange fallback (OKX -> Binance -> Gate)."""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def fetch_multi_exchange_candles(base_asset: str, interval: str = "15m", limit: int = 24) -> List[List[float]]:
    """Fetches candlestick data with automatic failover: OKX -> Binance -> Gate.
    Returns standard format: [[ts, o, h, l, c, vol], ...]
    """
    from r20_backend.exchanges import get_exchange_adapter

    # 1. Try OKX
    try:
        okx = get_exchange_adapter("okx")
        candles = okx.fetch_klines(base_asset, interval=interval, limit=limit, timeout=3.5)
        if len(candles) >= 15:
            return candles
    except Exception as e:
        logger.debug("OKX klines fallback trigger: %s", e)

    # 2. Try Binance
    try:
        binance = get_exchange_adapter("binance")
        candles = binance.fetch_klines(base_asset, interval=interval, limit=limit, timeout=3.5)
        if len(candles) >= 15:
            return candles
    except Exception as e:
        logger.debug("Binance klines fallback trigger: %s", e)

    # 3. Try Gate
    try:
        gate = get_exchange_adapter("gate")
        candles = gate.fetch_klines(base_asset, interval=interval, limit=limit, timeout=3.5)
        if len(candles) >= 15:
            return candles
    except Exception as e:
        logger.debug("Gate klines fallback trigger: %s", e)

    return []


def fetch_cross_exchange_insights(base_asset: str) -> Dict[str, Any]:
    """Fetches real-time price & funding comparison across OKX, Binance, and Gate for cross-arbitrage insight."""
    from r20_backend.exchanges import get_exchange_adapter

    prices = {}
    funding = {}
    binance_ls_ratio = None

    # OKX
    try:
        okx = get_exchange_adapter("okx")
        t_okx = okx.fetch_ticker(base_asset, timeout=3.0)
        if t_okx.get("last"):
            prices["okx"] = float(t_okx["last"])
    except Exception:
        pass

    # Binance
    try:
        binance = get_exchange_adapter("binance")
        t_bn = binance.fetch_ticker(base_asset, timeout=3.0)
        if t_bn.get("last"):
            prices["binance"] = float(t_bn["last"])
        binance_ls_ratio = binance.fetch_long_short_ratio(base_asset, timeout=3.0)
    except Exception:
        pass

    # Gate
    try:
        gate = get_exchange_adapter("gate")
        t_gt = gate.fetch_ticker(base_asset, timeout=3.0)
        if t_gt.get("last"):
            prices["gate"] = float(t_gt["last"])
        if t_gt.get("funding_rate"):
            funding["gate"] = float(t_gt["funding_rate"])
    except Exception:
        pass

    # Calculate spread disparity between Binance and OKX
    spread_pct = 0.0
    if "okx" in prices and "binance" in prices and prices["okx"] > 0:
        spread_pct = round((prices["binance"] - prices["okx"]) / prices["okx"] * 100, 3)

    return {
        "prices": prices,
        "spread_disparity_pct": spread_pct,
        "binance_top_trader_long_short_ratio": binance_ls_ratio,
        "gate_funding_rate": funding.get("gate"),
    }
