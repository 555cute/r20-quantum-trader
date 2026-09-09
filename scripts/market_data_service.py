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

# OKX bar 合法字面量（大小写敏感：分钟小写 m，小时/天/周/月大写）
_OKX_VALID_BARS = {
    "1m", "3m", "5m", "15m", "30m",
    "1H", "2H", "4H", "6H", "12H",
    "1D", "3D", "1W", "1M", "3M",
    "1Hutc", "4Hutc", "1Dutc", "1Wutc", "1Mutc", "1Dutc8", "1Wutc8", "1Mutc8",
}


def normalize_bar(bar: str) -> str:
    """Normalize K线周期到 OKX 合法字面量（大小写容错：1h→1H、4h→4H、15M→15m）。

    OKX /market/candles 与 indicators 接口对 bar/timeframe 参数严格区分大小写：
    小写 1h/4h 一律报 Parameter bar error，导致「1H/4H 数据拿不到」——
    所有入口统一先过这里。已是合法值（含 1M 月份大写特例）原样返回。
    """
    raw = str(bar or "").strip()
    if not raw:
        return "15m"
    if raw in _OKX_VALID_BARS:
        return raw
    low = raw.lower()
    for unit, canon in (("h", "H"), ("d", "D"), ("w", "W")):
        if low.endswith(unit) and low[: -1].isdigit():
            return low[: -1] + canon
    if low.endswith("m") and low[: -1].isdigit():
        n = int(low[: -1])
        if n in (1, 3, 5, 15, 30):
            return f"{n}m"
    return raw  # 未知/非法字面量交给 OKX 报错，不在本地臆造


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
                    backoff_factor=0.35,
                    # 429：OKX 公共行情按 IP 限频 40req/2s，引擎+面板共出口 IP 的部署
                    # 易触发——重试（尊重 Retry-After）吸收突发，避免 1H/4H K线偶发拿空
                    status_forcelist=[429, 500, 502, 503, 504],
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
# 0b. 多场所只读备源（Phase 2 · 2026-09-09）
#     仅当 OKX 三级容灾（www→aws→CLI）全断时兜底，保「价格连续性」优先。
#     量/张数单位随场所原生语义（币安=币量、Gate=张数），与 OKX 口径不同，
#     消费方仅得相对量级用于放量检测；大陆受限 IP 上自然失败落空，无副作用。
# ---------------------------------------------------------------------------

ALT_VENUES = ("binance", "gate")

# 健康文件与 ai_brain_trader 的 VENUE_HEALTH_FILE 同源目录（scripts/../data/）
import pathlib as _pathlib  # noqa: E402  （0b 段局部引入，避免动头部 import 块）
VENUE_HEALTH_FILE = str(_pathlib.Path(__file__).resolve().parents[1] / "data" / "venue_health.json")

_ALT_ORDER_CACHE = {"ts": 0.0, "order": None}
_ALT_ORDER_LOCK = threading.Lock()


def _alt_venue_order() -> tuple:
    """健康感知备源顺序（US-004）：本轮 failed 数升序 → 延迟后置（差 >5x 才翻转）→ 静态原序。

    与 AC「近3条记录内 failed 多的场所后置」的语义对应：
    ai_brain_trader._xv_flush_health 每周期整文件覆盖写，文件内容即「最近记录」
    级别的本轮快照（failed 为 name->reason dict，无逐次历史）——故以本轮 failed
    数为主排序键，不另造历史文件。avg_ms 仅当与全场最快所差距 >5 倍时才参与
    翻转（防毫秒级抖动让备源序反复横跳）；avg_ms 缺失/为 0（该所本周期无延迟
    样本）视为中性，不降权。

    缓存理由：备源路径在 OKX 全断时会爆发几十次请求（因子轮询/brain/回测），
    而健康文件每 15 分钟周期至多更新一次——60s TTL 读内存吸收 IO，防放大。
    任何缺失/损坏/结构异常一律回退静态 ALT_VENUES，绝不抛（热文件纪律：本模块
    被生产 trader 子进程直接加载；OKX 正常时本函数根本不被调用，主路径零感知）。
    """
    try:
        now = time.time()
        with _ALT_ORDER_LOCK:
            cached = _ALT_ORDER_CACHE["order"]
            if cached is not None and now - _ALT_ORDER_CACHE["ts"] < 60.0:
                return cached
        order = None
        try:
            p = _pathlib.Path(VENUE_HEALTH_FILE)
            if p.exists():
                raw = json.loads(p.read_text(encoding="utf-8"))
                venues = raw.get("venues") if isinstance(raw, dict) else None
                if isinstance(venues, dict):
                    base_idx = {v: i for i, v in enumerate(ALT_VENUES)}
                    stats: Dict[str, Any] = {}
                    for v in ALT_VENUES:
                        rec = venues.get(v)
                        rec = rec if isinstance(rec, dict) else {}
                        failed = rec.get("failed")
                        nf = len(failed) if isinstance(failed, (dict, list)) else 0
                        try:
                            avg = float(rec.get("avg_ms"))
                        except (TypeError, ValueError):
                            avg = 0.0
                        stats[v] = (nf, avg if avg > 0 else 0.0)
                    samples = [a for _, a in stats.values() if a > 0]
                    min_lat = min(samples) if samples else 0.0

                    def _key(v):
                        nf, avg = stats[v]
                        slow = 1 if (avg > 0 and min_lat > 0 and avg > 5.0 * min_lat) else 0
                        return (nf, slow, base_idx.get(v, 99))

                    order = tuple(sorted(ALT_VENUES, key=_key))
        except Exception:
            order = None
        if order is None:
            order = ALT_VENUES
        with _ALT_ORDER_LOCK:
            _ALT_ORDER_CACHE["ts"] = time.time()
            _ALT_ORDER_CACHE["order"] = order
        return order
    except Exception:
        return ALT_VENUES


def _alt_venue_allowed() -> bool:
    """离线/测试熔断开关：R20_ALT_VENUE_FALLBACK=0 时备源路径完全不发网络请求。"""
    import os
    return str(os.environ.get("R20_ALT_VENUE_FALLBACK", "1")).strip().lower() not in ("0", "off", "false")


def _get_venue_adapter(venue: str):
    """懒导入 r20_backend.exchanges（scripts 入口的 sys.path 引导）。"""
    import sys
    from pathlib import Path
    root = str(Path(__file__).resolve().parents[1])
    if root not in sys.path:
        sys.path.insert(0, root)
    from r20_backend.exchanges import get_adapter
    return get_adapter(venue)


def _alt_venue_ticker(inst_id: str) -> Optional[Dict[str, Any]]:
    if not _alt_venue_allowed():
        return None
    for venue in _alt_venue_order():
        try:
            ad = _get_venue_adapter(venue)
            t = ad.fetch_ticker(ad.canonical(inst_id))
        except Exception:
            t = None
        if t and t.get("last"):
            logger.warning("Multi-venue fallback: ticker %s served by %s", inst_id, venue)
            return {
                "instId": inst_id, "venue": venue,
                "last": str(t["last"]),
                "bidPx": str(t.get("bid") or ""),
                "askPx": str(t.get("ask") or ""),
                "open24h": str(t.get("open_24h") or ""),
                "high24h": str(t.get("high_24h") or ""),
                "low24h": str(t.get("low_24h") or ""),
                "vol24h": str(t.get("vol_24h_base") or ""),
                "volCcy24h": str(t.get("vol_24h_base") or ""),
                "ts": str(t.get("ts_ms") or ""),
            }
    return None


def _alt_venue_candles(inst_id: str, bar: str, limit: int) -> List[List[str]]:
    if not _alt_venue_allowed():
        return []
    for venue in _alt_venue_order():
        try:
            ad = _get_venue_adapter(venue)
            kl = ad.fetch_candles(ad.canonical(inst_id), bar, limit)
        except Exception:
            kl = None
        if kl:
            kl = kl[-limit:]
            kl.reverse()  # 适配器升序 → OKX 契约「最新在前」
            logger.warning("Multi-venue fallback: candles %s %s served by %s (%d rows)",
                           inst_id, bar, venue, len(kl))
            return kl
    return []


def _alt_funding_rate(inst_id: str) -> Optional[float]:
    if not _alt_venue_allowed():
        return None
    for venue in _alt_venue_order():
        try:
            ad = _get_venue_adapter(venue)
            r = ad.fetch_funding_rate(ad.canonical(inst_id))
        except Exception:
            r = None
        if r is not None:
            return round(float(r) * 100, 4)  # 对齐 OKX 路径的百分数口径
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
    return _alt_venue_ticker(inst_id)


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

def _local_math_indicators(
    inst_id: str,
    indicators: List[str],
    bar: str = "1H",
) -> Dict[str, Dict[str, str]]:
    """三级兜底：当 OKX MCP 指标接口与 CLI 均不可用时（部署环境常见），
    用本地蜡烛（自带 www→aws→CLI 双源容灾）纯 Python 计算 ADX/KDJ/BBWIDTH/CMF。
    输出与 OKX 官方口径对齐的字符串数值；样本不足时返回空 dict 让上层维持缺省。"""
    rows = fetch_candles(inst_id, bar=bar, limit=120)
    if not rows:
        return {}
    try:
        chron = list(reversed(rows))
        highs = [float(r[2]) for r in chron]
        lows = [float(r[3]) for r in chron]
        closes = [float(r[4]) for r in chron]
        vols = [float(r[5]) for r in chron]
    except (ValueError, IndexError):
        return {}

    result: Dict[str, Dict[str, str]] = {}
    for ind in indicators:
        key = ind.upper().replace("-", "").replace("_", "")
        try:
            if key == "ADX" and len(closes) >= 30:
                trs, pdms, ndms = [], [], []
                for i in range(1, len(closes)):
                    tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
                    up, dn = highs[i] - highs[i - 1], lows[i - 1] - lows[i]
                    trs.append(tr)
                    pdms.append(up if (up > dn and up > 0) else 0.0)
                    ndms.append(dn if (dn > up and dn > 0) else 0.0)
                p = 14
                atr = sum(trs[:p])
                pdm = sum(pdms[:p])
                ndm = sum(ndms[:p])
                dxs = []
                for i in range(p, len(trs)):
                    atr = atr - atr / p + trs[i]
                    pdm = pdm - pdm / p + pdms[i]
                    ndm = ndm - ndm / p + ndms[i]
                    pdi = 100.0 * pdm / atr if atr > 0 else 0.0
                    ndi = 100.0 * ndm / atr if atr > 0 else 0.0
                    denom = pdi + ndi
                    dxs.append(100.0 * abs(pdi - ndi) / denom if denom > 0 else 0.0)
                if len(dxs) >= p:
                    adx = sum(dxs[:p]) / p
                    for dx in dxs[p:]:
                        adx = (adx * (p - 1) + dx) / p
                    result["ADX"] = {"adx": f"{adx:.2f}"}
            elif key == "KDJ" and len(closes) >= 9:
                k = d = 50.0
                for i in range(8, len(closes)):
                    hh = max(highs[i - 8: i + 1])
                    ll = min(lows[i - 8: i + 1])
                    rsv = (closes[i] - ll) / (hh - ll) * 100.0 if hh > ll else 50.0
                    k = (2.0 * k + rsv) / 3.0
                    d = (2.0 * d + k) / 3.0
                j = 3.0 * k - 2.0 * d
                result["KDJ"] = {"k": f"{k:.2f}", "d": f"{d:.2f}", "j": f"{j:.2f}"}
            elif key in ("BBWIDTH", "BBANDWIDTH") and len(closes) >= 20:
                window = closes[-20:]
                mid = sum(window) / 20.0
                sd = (sum((x - mid) ** 2 for x in window) / 20.0) ** 0.5
                if mid > 0:
                    result["BBWIDTH"] = {"bbWidth": f"{(4.0 * sd / mid * 100.0):.2f}"}
            elif key == "CMF" and len(closes) >= 21:
                num = den = 0.0
                for i in range(-20, 0):
                    rng = highs[i] - lows[i]
                    mf = ((closes[i] - lows[i]) - (highs[i] - closes[i])) / rng if rng > 0 else 0.0
                    num += mf * vols[i]
                    den += vols[i]
                result["CMF"] = {"cmf": f"{(num / den):.4f}" if den > 0 else "0.0000"}
        except Exception as exc:
            logger.debug("Local indicator %s failed for %s: %s", key, inst_id, exc)
    return result


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
    bar = normalize_bar(bar)
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
    
    # 三级兜底：MCP/CLI 全灭（部署环境未装 okx CLI 时最常见）→ 本地蜡烛纯 Python 计算
    missing = [ind for ind in indicators if ind.upper().replace("-", "") not in result]
    if missing:
        for k, v in _local_math_indicators(inst_id, missing, bar).items():
            result.setdefault(k, v)
    
    return result


def fetch_single_indicator(
    inst_id: str,
    indicator: str,
    bar: str = "1H",
    timeout: float = 3.5,
) -> Dict[str, Any]:
    """Fetch or compute a single indicator without launching Node CLI."""
    key = indicator.upper().replace("-", "").replace("_", "")
    bar = normalize_bar(bar)
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
    
    # 三级兜底：本地蜡烛 + 纯 Python 数学（部署环境无 CLI / MCP 端点不可达时的最后防线）
    return _local_math_indicators(inst_id, [key], bar).get(key, {})


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
    bar = normalize_bar(bar)
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = 45
    limit = max(1, min(limit, 300))  # OKX 单次上限 300，超限直接报错返回空
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
    return _alt_venue_candles(inst_id, bar, limit)


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
    return _alt_funding_rate(inst_id)


def fetch_open_interest(inst_id: str, timeout: float = 3.5) -> Optional[Dict[str, Any]]:
    """Fetch open interest data."""
    data = _public_get("/api/v5/public/open-interest", params={"instId": inst_id}, timeout=timeout)
    if data and data.get("data"):
        return data["data"][0]
    return None
