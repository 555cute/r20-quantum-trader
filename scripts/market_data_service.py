"""High-Performance Zero-Process Direct Public Market Data Service (market_data_service.py).

Eliminates repetitive Node CLI / OKX CLI process fork overhead during public market
data harvesting (tickers, orderbooks, indicators, candles).
Uses persistent connection-pooled HTTP Keep-Alive sessions with pure-Python fallbacks.
"""
from __future__ import annotations

import json
import logging
import math
import subprocess
import threading
import time
from typing import Any, Dict, List, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger("market_data_service")

OKX_PUBLIC_HOSTS = [
    "https://www.okx.com",
    "https://aws.okx.com",
]

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json",
}

_SESSION: Optional[requests.Session] = None
_SESSION_LOCK = threading.Lock()


def get_market_session() -> requests.Session:
    """Thread-safe persistent connection-pooled requests session."""
    global _SESSION
    if _SESSION is None:
        with _SESSION_LOCK:
            if _SESSION is None:
                s = requests.Session()
                s.headers.update(DEFAULT_HEADERS)
                retries = Retry(
                    total=2,
                    backoff_factor=0.2,
                    status_forcelist=[500, 502, 503, 504],
                    raise_on_status=False,
                )
                adapter = HTTPAdapter(
                    pool_connections=12,
                    pool_maxsize=24,
                    max_retries=retries,
                    pool_block=False,
                )
                s.mount("https://", adapter)
                s.mount("http://", adapter)
                _SESSION = s
    return _SESSION


def _public_get(path: str, params: Optional[Dict[str, Any]] = None, timeout: float = 3.5) -> Optional[Dict[str, Any]]:
    """Try primary then fallback OKX public endpoints."""
    session = get_market_session()
    for base in OKX_PUBLIC_HOSTS:
        url = f"{base}{path}"
        try:
            resp = session.get(url, params=params, timeout=timeout)
            if resp.status_code == 200:
                data = resp.json()
                if str(data.get("code", "0")) == "0":
                    return data
        except Exception as exc:
            logger.debug("Public GET %s failed on %s: %s", path, base, exc)
    return None


def _public_post(path: str, payload: Dict[str, Any], timeout: float = 4.0) -> Optional[Dict[str, Any]]:
    """Try primary then fallback OKX public POST endpoints."""
    session = get_market_session()
    headers = {"Content-Type": "application/json"}
    for base in OKX_PUBLIC_HOSTS:
        url = f"{base}{path}"
        try:
            resp = session.post(url, json=payload, headers=headers, timeout=timeout)
            if resp.status_code == 200:
                data = resp.json()
                if str(data.get("code", "0")) == "0":
                    return data
        except Exception as exc:
            logger.debug("Public POST %s failed on %s: %s", path, base, exc)
    return None


# ---------------------------------------------------------------------------
# 1. Ticker & Bulk Tickers
# ---------------------------------------------------------------------------

def fetch_ticker(inst_id: str, timeout: float = 3.5) -> Optional[Dict[str, Any]]:
    """Fetch single instrument ticker via direct REST. Fallback to CLI on failure."""
    data = _public_get("/api/v5/market/ticker", params={"instId": inst_id}, timeout=timeout)
    if data and data.get("data"):
        return data["data"][0]
    
    # Emergency CLI fallback
    try:
        res = subprocess.run(
            f"okx market ticker {inst_id} --json 2>/dev/null",
            shell=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if res.returncode == 0 and res.stdout.strip():
            out = json.loads(res.stdout.strip())
            return out[0] if isinstance(out, list) and out else (out if isinstance(out, dict) else None)
    except Exception:
        pass
    return None


def fetch_tickers_bulk(inst_type: str = "SWAP", timeout: float = 4.0) -> Dict[str, Dict[str, Any]]:
    """Fetch all instrument tickers in ONE single network request."""
    data = _public_get("/api/v5/market/tickers", params={"instType": inst_type}, timeout=timeout)
    if data and data.get("data"):
        return {item["instId"]: item for item in data["data"] if "instId" in item}
    return {}


# ---------------------------------------------------------------------------
# 2. Orderbook Depth
# ---------------------------------------------------------------------------

def fetch_orderbook_depth(inst_id: str, sz: int = 5, timeout: float = 3.5) -> Optional[Dict[str, Any]]:
    """Fetch orderbook depth directly via REST. Returns {'bids': [...], 'asks': [...]}.
    Replaces repetitive `okx market orderbook ...` CLI process launches.
    """
    data = _public_get("/api/v5/market/books", params={"instId": inst_id, "sz": sz}, timeout=timeout)
    if data and data.get("data"):
        return data["data"][0]
    
    # Emergency CLI fallback
    try:
        res = subprocess.run(
            f"okx market orderbook {inst_id} --sz {sz} --json 2>/dev/null",
            shell=True,
            capture_output=True,
            text=True,
            timeout=4,
        )
        if res.returncode == 0 and res.stdout.strip():
            out = json.loads(res.stdout.strip())
            if isinstance(out, list) and out:
                return out[0]
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# 3. Technical Indicators (ADX, KDJ, BBWIDTH, CMF, RSI, etc.)
# ---------------------------------------------------------------------------

def fetch_indicators_batch(
    inst_id: str,
    indicators: List[str],
    bar: str = "1H",
    timeout: float = 4.0,
) -> Dict[str, Dict[str, Any]]:
    """Fetch multiple technical indicators in ONE SINGLE HTTP POST request.
    
    Replaces 4x-6x Node CLI process invocations per instrument with 1 fast call.
    Returns: {"ADX": {"adx": "20.1", ...}, "KDJ": {"k": "...", "d": "...", "j": "..."}, ...}
    """
    ind_configs = {ind.upper(): {} for ind in indicators}
    payload = {
        "instId": inst_id,
        "timeframes": [bar],
        "indicators": ind_configs,
    }
    
    data = _public_post("/api/v5/aigc/mcp/indicators", payload, timeout=timeout)
    result: Dict[str, Dict[str, Any]] = {}
    
    if data and data.get("data"):
        try:
            tfs = data["data"][0].get("data", [{}])[0].get("timeframes", {}).get(bar, {}).get("indicators", {})
            for ind in indicators:
                key = ind.upper().replace("-", "")
                items = tfs.get(key, [])
                if items and isinstance(items[0], dict):
                    result[key] = items[0].get("values", {})
            if result:
                return result
        except Exception:
            pass
    
    # If MCP endpoint failed, fallback to querying individual indicator via REST or local math
    for ind in indicators:
        key = ind.upper()
        if key not in result:
            val = fetch_single_indicator(inst_id, ind, bar=bar, timeout=timeout)
            if val:
                result[key] = val
    
    return result


def fetch_single_indicator(
    inst_id: str,
    indicator: str,
    bar: str = "1H",
    timeout: float = 3.5,
) -> Dict[str, Any]:
    """Fetch or compute a single indicator without launching Node CLI."""
    key = indicator.upper().replace("-", "")
    payload = {
        "instId": inst_id,
        "timeframes": [bar],
        "indicators": {key: {}},
    }
    data = _public_post("/api/v5/aigc/mcp/indicators", payload, timeout=timeout)
    if data and data.get("data"):
        try:
            tfs = data["data"][0].get("data", [{}])[0].get("timeframes", {}).get(bar, {}).get("indicators", {})
            items = tfs.get(key, [])
            if items and isinstance(items[0], dict):
                return items[0].get("values", {})
        except Exception:
            pass
    
    # Emergency CLI fallback
    try:
        res = subprocess.run(
            f"okx market indicator {indicator.lower()} {inst_id} --bar {bar} --json 2>/dev/null",
            shell=True,
            capture_output=True,
            text=True,
            timeout=4,
        )
        if res.returncode == 0 and res.stdout.strip():
            ind_res = json.loads(res.stdout.strip())
            if isinstance(ind_res, list) and ind_res:
                tfs = ind_res[0].get("data", [{}])[0].get("timeframes", {}).get(bar, {}).get("indicators", {})
                items = tfs.get(key, [])
                if items and isinstance(items[0], dict):
                    return items[0].get("values", {})
    except Exception:
        pass
    
    return {}


# ---------------------------------------------------------------------------
# 4. Candles
# ---------------------------------------------------------------------------

def fetch_candles(
    inst_id: str,
    bar: str = "15m",
    limit: int = 45,
    timeout: float = 4.0,
) -> List[List[str]]:
    """Fetch candles directly from OKX Official Market REST API with Keep-Alive."""
    data = _public_get(
        "/api/v5/market/candles",
        params={"instId": inst_id, "bar": bar, "limit": limit},
        timeout=timeout,
    )
    if data and data.get("data"):
        return data["data"]
    
    # Emergency CLI fallback
    try:
        res = subprocess.run(
            f"okx market candles {inst_id} --bar {bar} --limit {limit} --json 2>/dev/null",
            shell=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if res.returncode == 0 and res.stdout.strip():
            parsed = json.loads(res.stdout.strip())
            if isinstance(parsed, list):
                return parsed
    except Exception:
        pass
    return []


# ---------------------------------------------------------------------------
# 5. Funding Rate & Open Interest
# ---------------------------------------------------------------------------

def fetch_funding_rate(inst_id: str, timeout: float = 3.5) -> Optional[float]:
    """Fetch current perpetual funding rate as percentage."""
    data = _public_get("/api/v5/public/funding-rate", params={"instId": inst_id}, timeout=timeout)
    if data and data.get("data"):
        try:
            return round(float(data["data"][0].get("fundingRate", 0.0)) * 100, 4)
        except (ValueError, TypeError):
            pass
    return None


def fetch_open_interest(inst_id: str, timeout: float = 3.5) -> Optional[Dict[str, Any]]:
    """Fetch open interest data."""
    data = _public_get("/api/v5/public/open-interest", params={"instId": inst_id}, timeout=timeout)
    if data and data.get("data"):
        return data["data"][0]
    return None
