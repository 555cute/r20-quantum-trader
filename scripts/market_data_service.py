"""Public market data via the selected exchange adapter.

Keeps the historical fetch_* call contract. All live requests go through
get_exchange() so a frozen environment is used for every public call.
Indicators are computed from real candles; missing inputs fail closed
(no fabricated zero ADX / empty success).
"""
from __future__ import annotations

import logging
import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger("market_data_service")


def _get_exchange():
    from r20_exchange.runtime import get_exchange

    return get_exchange()


def _finite(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or abs(number) == float("inf"):
        return None
    return number


def _call(method: str, *args: Any, **kwargs: Any) -> Any:
    try:
        exchange = _get_exchange()
        func = getattr(exchange, method)
    except Exception as exc:
        logger.debug("exchange.%s unavailable: %s", method, exc)
        return None
    try:
        return func(*args, **kwargs)
    except Exception as exc:
        logger.debug("exchange.%s failed: %s", method, exc)
        return None


def _ohlcv(raw: Sequence[Any]) -> Optional[Tuple[List[float], List[float], List[float], List[float]]]:
    """Newest-first adapter candles -> chronological high/low/close/volume."""
    if not raw:
        return None
    rows: List[Sequence[Any]] = []
    for row in raw:
        if not isinstance(row, (list, tuple)) or len(row) < 6:
            return None
        if len(row) >= 9 and str(row[8]) not in {"", "1"}:
            continue
        rows.append(row)
    if not rows:
        rows = [row for row in raw if isinstance(row, (list, tuple)) and len(row) >= 6]
    if not rows:
        return None
    chronological = list(reversed(rows))
    highs: List[float] = []
    lows: List[float] = []
    closes: List[float] = []
    vols: List[float] = []
    for row in chronological:
        high = _finite(row[2])
        low = _finite(row[3])
        close = _finite(row[4])
        volume = _finite(row[5])
        if None in (high, low, close, volume):
            return None
        if high < low or close <= 0:
            return None
        highs.append(high)
        lows.append(low)
        closes.append(close)
        vols.append(volume)
    return highs, lows, closes, vols


def _wilder_smooth(values: Sequence[float], period: int) -> List[float]:
    if period <= 0 or len(values) < period:
        return []
    seed = sum(values[:period]) / float(period)
    out = [seed]
    for value in values[period:]:
        out.append((out[-1] * (period - 1) + value) / float(period))
    return out


def _compute_adx(
    highs: Sequence[float], lows: Sequence[float], closes: Sequence[float], period: int = 14
) -> Optional[float]:
    if len(closes) < (period * 2 + 1) or len(highs) != len(closes) or len(lows) != len(closes):
        return None
    plus_dm: List[float] = []
    minus_dm: List[float] = []
    tr_list: List[float] = []
    for i in range(1, len(closes)):
        up = highs[i] - highs[i - 1]
        down = lows[i - 1] - lows[i]
        plus_dm.append(up if up > down and up > 0 else 0.0)
        minus_dm.append(down if down > up and down > 0 else 0.0)
        tr_list.append(
            max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
        )
    atr = _wilder_smooth(tr_list, period)
    sm_plus = _wilder_smooth(plus_dm, period)
    sm_minus = _wilder_smooth(minus_dm, period)
    if not atr or len(atr) != len(sm_plus) or len(atr) != len(sm_minus):
        return None
    dx: List[float] = []
    for atr_v, plus_v, minus_v in zip(atr, sm_plus, sm_minus):
        if atr_v <= 0:
            dx.append(0.0)
            continue
        plus_di = 100.0 * plus_v / atr_v
        minus_di = 100.0 * minus_v / atr_v
        di_sum = plus_di + minus_di
        dx.append(0.0 if di_sum <= 0 else 100.0 * abs(plus_di - minus_di) / di_sum)
    adx_series = _wilder_smooth(dx, period)
    if not adx_series:
        return None
    return adx_series[-1]


def _compute_rsi(closes: Sequence[float], period: int = 14) -> Optional[float]:
    if len(closes) < period + 1:
        return None
    diffs = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [diff if diff > 0 else 0.0 for diff in diffs]
    losses = [-diff if diff < 0 else 0.0 for diff in diffs]
    if len(gains) < period:
        return None
    avg_gain = sum(gains[-period:]) / float(period)
    avg_loss = sum(losses[-period:]) / float(period)
    if avg_loss <= 0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def _compute_kdj(
    highs: Sequence[float], lows: Sequence[float], closes: Sequence[float], period: int = 9
) -> Optional[Tuple[float, float, float]]:
    if len(closes) < period or len(highs) != len(closes) or len(lows) != len(closes):
        return None
    k = 50.0
    d = 50.0
    last: Optional[Tuple[float, float, float]] = None
    for i in range(period - 1, len(closes)):
        window_h = highs[i - period + 1 : i + 1]
        window_l = lows[i - period + 1 : i + 1]
        highest = max(window_h)
        lowest = min(window_l)
        rsv = 50.0 if highest == lowest else (closes[i] - lowest) / (highest - lowest) * 100.0
        k = (2.0 / 3.0) * k + (1.0 / 3.0) * rsv
        d = (2.0 / 3.0) * d + (1.0 / 3.0) * k
        j = 3.0 * k - 2.0 * d
        last = (k, d, j)
    return last


def _compute_bbwidth(closes: Sequence[float], period: int = 20, band: float = 2.0) -> Optional[float]:
    if len(closes) < period:
        return None
    window = closes[-period:]
    mean = sum(window) / float(period)
    if mean <= 0:
        return None
    variance = sum((value - mean) ** 2 for value in window) / float(period)
    stdev = math.sqrt(variance)
    return (2.0 * band * stdev) / mean


def _compute_cmf(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    vols: Sequence[float],
    period: int = 20,
) -> Optional[float]:
    if len(closes) < period:
        return None
    mfv_sum = 0.0
    vol_sum = 0.0
    for high, low, close, volume in zip(
        highs[-period:], lows[-period:], closes[-period:], vols[-period:]
    ):
        span = high - low
        mfm = 0.0 if span == 0 else ((close - low) - (high - close)) / span
        mfv_sum += mfm * volume
        vol_sum += volume
    if vol_sum <= 0:
        return None
    return mfv_sum / vol_sum


def _indicator_from_ohlcv(
    name: str,
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    vols: Sequence[float],
) -> Optional[Dict[str, Any]]:
    key = name.upper().replace("-", "")
    if key == "ADX":
        adx = _compute_adx(highs, lows, closes)
        if adx is None:
            return None
        return {"adx": str(round(adx, 4))}
    if key == "RSI":
        rsi = _compute_rsi(closes)
        if rsi is None:
            return None
        return {"rsi": str(round(rsi, 4))}
    if key == "KDJ":
        kdj = _compute_kdj(highs, lows, closes)
        if kdj is None:
            return None
        k, d, j = kdj
        return {"k": str(round(k, 4)), "d": str(round(d, 4)), "j": str(round(j, 4))}
    if key in {"BBWIDTH", "BBW"}:
        width = _compute_bbwidth(closes)
        if width is None:
            return None
        return {"bbWidth": str(round(width, 6))}
    if key == "CMF":
        cmf = _compute_cmf(highs, lows, closes, vols)
        if cmf is None:
            return None
        return {"cmf": str(round(cmf, 6))}
    return None


def fetch_ticker(inst_id: str, timeout: float = 3.5) -> Optional[Dict[str, Any]]:
    """Fetch a single instrument ticker from the selected exchange."""
    _ = timeout
    data = _call("ticker", inst_id)
    if isinstance(data, dict) and data:
        return data
    return None


def fetch_tickers_bulk(inst_type: str = "SWAP", timeout: float = 4.0) -> Dict[str, Dict[str, Any]]:
    """Fetch all instrument tickers in one adapter call."""
    _ = timeout
    data = _call("tickers")
    if not isinstance(data, dict):
        return {}
    wanted = str(inst_type or "").upper()
    result: Dict[str, Dict[str, Any]] = {}
    for inst_id, item in data.items():
        if not isinstance(item, dict):
            continue
        key = str(item.get("instId") or inst_id)
        if wanted and wanted not in {"", "ALL"} and wanted not in key.upper():
            continue
        result[key] = item
    return result


def fetch_orderbook_depth(inst_id: str, sz: int = 5, timeout: float = 3.5) -> Optional[Dict[str, Any]]:
    """Fetch orderbook depth. Returns {'bids': [...], 'asks': [...]}."""
    _ = timeout
    data = _call("orderbook", inst_id, sz)
    if not isinstance(data, dict):
        return None
    if "bids" not in data or "asks" not in data:
        return None
    return data


def fetch_candles(
    inst_id: str,
    bar: str = "15m",
    limit: int = 45,
    timeout: float = 4.0,
) -> List[List[str]]:
    """Fetch newest-first candles from the selected exchange. Never fabricates rows."""
    _ = timeout
    data = _call("candles", inst_id, bar, limit)
    if not isinstance(data, list) or not data:
        return []
    rows: List[List[str]] = []
    for row in data:
        if isinstance(row, (list, tuple)) and len(row) >= 5:
            rows.append([str(part) for part in row])
    return rows


def fetch_funding_rate(inst_id: str, timeout: float = 3.5) -> Optional[float]:
    """Fetch current perpetual funding rate as percentage."""
    _ = timeout
    data = _call("funding_rate", inst_id)
    rate = _finite(data)
    if rate is None:
        return None
    return round(rate, 4)


def fetch_open_interest(inst_id: str, timeout: float = 3.5) -> Optional[Dict[str, Any]]:
    """Fetch open interest. Missing exchange support stays unavailable."""
    _ = timeout
    data = _call("open_interest", inst_id)
    if isinstance(data, dict) and data:
        return data
    return None


def fetch_long_short_ratio(inst_id: str, timeout: float = 3.5) -> Optional[float]:
    """Account long/short ratio. Unsupported venues return None."""
    _ = timeout
    data = _call("long_short_ratio", inst_id)
    return _finite(data)


def fetch_taker_volume(inst_id: str, timeout: float = 3.5) -> Optional[Dict[str, Any]]:
    """Taker buy/sell volume. Unsupported venues return None."""
    _ = timeout
    data = _call("taker_volume", inst_id)
    if not isinstance(data, dict):
        return None
    buy = _finite(data.get("buyVol"))
    sell = _finite(data.get("sellVol"))
    if buy is None or sell is None:
        return None
    return {"buyVol": buy, "sellVol": sell}


def fetch_single_indicator(
    inst_id: str,
    indicator: str,
    bar: str = "1H",
    timeout: float = 3.5,
) -> Dict[str, Any]:
    """Compute one indicator from real candles. Empty dict on insufficient data."""
    _ = timeout
    raw = fetch_candles(inst_id, bar=bar, limit=80)
    parsed = _ohlcv(raw)
    if parsed is None:
        return {}
    highs, lows, closes, vols = parsed
    values = _indicator_from_ohlcv(indicator, highs, lows, closes, vols)
    return dict(values) if values else {}


def fetch_indicators_batch(
    inst_id: str,
    indicators: List[str],
    bar: str = "1H",
    timeout: float = 4.0,
) -> Dict[str, Dict[str, Any]]:
    """Compute consumed indicators from one candle pull. Omit keys that cannot be proven."""
    _ = timeout
    raw = fetch_candles(inst_id, bar=bar, limit=80)
    parsed = _ohlcv(raw)
    if parsed is None:
        return {}
    highs, lows, closes, vols = parsed
    result: Dict[str, Dict[str, Any]] = {}
    for indicator in indicators:
        key = indicator.upper().replace("-", "")
        values = _indicator_from_ohlcv(key, highs, lows, closes, vols)
        if values:
            result[key] = values
    return result


def format_oi_usd(open_interest: Optional[Dict[str, Any]], last_price: float = 0.0) -> Optional[str]:
    """Prefer venue USD notional, otherwise convert normalized BASE OI once."""
    if not isinstance(open_interest, dict):
        return None
    usd = _finite(open_interest.get("oiUsd"))
    if usd is None or usd <= 0:
        oi_base = _finite(open_interest.get("oi"))
        price = _finite(last_price) or 0.0
        if oi_base is not None and oi_base > 0 and price > 0:
            usd = oi_base * price
    if usd is None or usd <= 0:
        return None
    if usd > 1e8:
        return f"{round(usd / 1e8, 2)}亿 U"
    return f"{round(usd / 1e4, 1)}万 U"
