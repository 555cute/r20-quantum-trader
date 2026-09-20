#!/usr/bin/env python3
"""
R20 High-Alpha Quantitative Factor Library Engine (factor_library.py)
Calculates and normalizes 5 core factor pillars for crypto perpetuals:
1. Momentum & Trend (ADX, RSI, EMA slope, KDJ)
2. Volatility & Channel (ATR%, Bollinger Bandwidth)
3. Volume & Money Flow (15M Volume Ratio, OBV, CMF Chaikin Flow, 5M Taker Net Flow)
4. Orderbook & Microstructure (Bid/Ask Imbalance Ratio, BBO Spread)
5. Smart Money & Derivatives (Top100 Weighted Long Ratio, 24H Net Flow, Funding Rate, OI)
"""

import os
import sys
from pathlib import Path

from r20_backend.math_utils import safe_float as _shared_safe_float

_THIS_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _THIS_DIR.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
if str(_THIS_DIR) not in sys.path:
    sys.path.insert(0, str(_THIS_DIR))

import json
import time
from datetime import datetime, timedelta, timezone
import subprocess
import urllib.request
from typing import Dict, Any, List, Optional

from scripts.factors.defaults import build_default_factors
from scripts.factors.scoring import score_composite_alpha
from scripts.factors.candles_15m import compute_15m_indicators
from concurrent.futures import ThreadPoolExecutor

_BJ = timezone(timedelta(hours=8))

WORKSPACE_DIR = str(_PROJECT_ROOT)
#: ⚠️ `R20_DATA_DIR` 是**测试沙箱专用环境变量**（由 tests/config_sandbox.isolate_config
#: 设置、由 `run_script` 拉起的子进程继承）：跑测试时把 data/ 写入重定向到沙箱，
#: **生产从不设置该变量 → 取值与原先逐位相同**。修复"测试经子进程写生产文件"
#: 的泄漏（§88/§91.6），不改任何业务行为。
DATA_DIR = os.environ.get("R20_DATA_DIR") or os.path.join(WORKSPACE_DIR, "data")
FACTOR_LIB_CACHE_FILE = os.path.join(DATA_DIR, "factor_library_snapshot.json")

from instrument_pool import load_instruments
from market_data_service import fetch_orderbook_depth, fetch_indicators_batch, fetch_ticker, fetch_funding_rate, fetch_candles

TARGET_INSTRUMENTS = load_instruments()

def safe_float(val: Any, default: float = 0.0) -> float:
    """薄壳：转调单一事实源（`r20_backend.math_utils.safe_float`，第一百五十刀）。

    本函数与 `scripts/ai_brain_trader.safe_float`、`scripts/calculus/regime._safe_float`
    原为**三份**逐条等价的实现（按 14 组输入行为对拍一致），现收敛到一处：
    `nan`/`±inf`/不可转 ⇒ `default`；`bool` 按 `float()` 语义（`True→1.0`）。
    """
    return _shared_safe_float(val, default)

def _resolve_calculate_calculus():
    """按需（并缓存）解析微积分引擎。

    原实现在 `compute_instrument_factors` 的**循环体内**每次
    `sys.path.append(...)` + `from calculus_engine import calculate_calculus` ——
    8 个标的就重复 8 次路径追加与导入查找。这里改为**模块内 memo 一次**：
    行为等价（返回同一个函数对象），只是不再每个标的重复做。

    取不到时返回 `None` —— `compute_15m_indicators` 会跳过 Pillar 6
    而保留前面算好的 ATR/RSI/VWAP/OBV（与原来"内层 try: pass"的语义一致）。
    """
    global _CALCULUS_ENGINE_FN
    if _CALCULUS_ENGINE_FN is not _UNRESOLVED:
        return _CALCULUS_ENGINE_FN
    fn = None
    try:
        sys.path.append(os.path.join(WORKSPACE_DIR, "scripts"))
        from calculus_engine import calculate_calculus
        fn = calculate_calculus
    except Exception:
        fn = None
    _CALCULUS_ENGINE_FN = fn
    return fn


_UNRESOLVED = object()
_CALCULUS_ENGINE_FN = _UNRESOLVED


def compute_instrument_factors(item: Dict[str, Any], smart_money_pool: Dict[str, Any]) -> Dict[str, Any]:
    inst_id = item["instId"]
    name = item["name"]
    ccy = item.get("ccy", "")
    headers = {"User-Agent": "Mozilla/5.0"}
    
    factors = build_default_factors(inst_id, name)

    # 1. Ticker & Depth (Orderbook)
    try:
        req = urllib.request.Request(f"https://www.okx.com/api/v5/market/ticker?instId={inst_id}", headers=headers)
        with urllib.request.urlopen(req, timeout=3) as resp:
            d = json.loads(resp.read().decode("utf-8"))
            if d.get("code") == "0" and d.get("data"):
                t = d["data"][0]
                factors["price"] = safe_float(t.get("last"))
                factors["microstructure"]["bid_px"] = safe_float(t.get("bidPx", factors["price"]))
                factors["microstructure"]["ask_px"] = safe_float(t.get("askPx", factors["price"]))
                op = safe_float(t.get("open24h", 0))
                factors["chg24h"] = round(((factors["price"] - op) / op * 100) if op > 0 else 0.0, 2)
                
                # Spread
                if factors["microstructure"]["ask_px"] > 0 and factors["price"] > 0:
                    spread = factors["microstructure"]["ask_px"] - factors["microstructure"]["bid_px"]
                    factors["microstructure"]["spread_pct"] = round(spread / factors["price"] * 100, 4)
    except Exception:
        pass

    # 2. Orderbook Depth (Top 5 Level Imbalance) via direct REST (zero Node CLI fork)
    try:
        ob_data = fetch_orderbook_depth(inst_id, sz=5)
        if ob_data and isinstance(ob_data, dict):
            bids = ob_data.get("bids", [])
            asks = ob_data.get("asks", [])
            total_bid_sz = sum(safe_float(b[1]) for b in bids)
            total_ask_sz = sum(safe_float(a[1]) for a in asks)
            if total_ask_sz > 0:
                ratio = round(total_bid_sz / total_ask_sz, 2)
                factors["microstructure"]["bid_ask_depth_ratio"] = ratio
                if ratio >= 1.5:
                    factors["microstructure"]["depth_bias"] = "STRONG_BID"
                elif ratio <= 0.67:
                    factors["microstructure"]["depth_bias"] = "STRONG_ASK"
    except Exception:
        pass

    # 3. 15M Candles -> ATR, RSI, VWAP Bias, Vol Ratio, OBV
    try:
        d = {"data": fetch_candles(inst_id, bar="15m", limit=24)}
        if d["data"] and len(d["data"]) >= 15:
            raw_candles = d["data"]
            # 15M 派生序列 + 指标 + Pillar 6（阶段 4·B3 第三十二刀：
            # 迁至 scripts/factors/candles_15m.py）。取数仍在本门面内，
            # 故对 fetch_candles 的 patch.object 缝不受影响。
            closes, highs, lows, vols = compute_15m_indicators(
                raw_candles, factors, safe_float=safe_float,
                calculate_calculus=_resolve_calculate_calculus())
        else:
            print(f"[Factor] ⚠️ {inst_id} 15m K线获取不足15根（www/aws/CLI 三级容灾均未取回），15M 因子降级缺省")
    except Exception as exc:
        print(f"[Factor] ⚠️ {inst_id} 15m K线处理异常: {exc}")

    # 3.5. 1H Candles -> 1H ATR & 1H RSI
    try:
        d = {"data": fetch_candles(inst_id, bar="1H", limit=24)}
        if d["data"] and len(d["data"]) >= 15:
            raw_1h = d["data"]
            closes_1h = [safe_float(c[4]) for c in reversed(raw_1h)]
            highs_1h = [safe_float(c[2]) for c in reversed(raw_1h)]
            lows_1h = [safe_float(c[3]) for c in reversed(raw_1h)]

            tr_list_1h = []
            for i in range(1, len(closes_1h)):
                tr = max(highs_1h[i] - lows_1h[i], abs(highs_1h[i] - closes_1h[i-1]), abs(lows_1h[i] - closes_1h[i-1]))
                tr_list_1h.append(tr)
            if len(tr_list_1h) >= 14:
                atr_1h = sum(tr_list_1h[-14:]) / 14
                factors["volatility_channel"]["atr_1h"] = round(atr_1h, 4)
                if factors["price"] > 0:
                    factors["volatility_channel"]["atr_1h_pct"] = round(atr_1h / factors["price"] * 100, 2)
        else:
            print(f"[Factor] ⚠️ {inst_id} 1H K线获取不足15根（www/aws/CLI 三级容灾均未取回），1H ATR 字段降级缺省")
    except Exception as exc:
        print(f"[Factor] ⚠️ {inst_id} 1H K线处理异常: {exc}")

    # 4. OKX Official Indicators (ADX, KDJ, BBWidth, CMF) via 1 single batch REST call (zero Node CLI fork)
    try:
        inds = fetch_indicators_batch(inst_id, ["adx", "kdj", "bbwidth", "cmf"], bar="1H")
        if "ADX" in inds:
            factors["trend_momentum"]["adx_1h"] = safe_float(inds["ADX"].get("adx"))
        if "KDJ" in inds:
            factors["trend_momentum"]["kdj_j"] = safe_float(inds["KDJ"].get("j"))
        if "BBWIDTH" in inds:
            factors["volatility_channel"]["bb_width_1h"] = safe_float(inds["BBWIDTH"].get("bbWidth"))
        if "CMF" in inds:
            factors["volume_money_flow"]["cmf_1h"] = safe_float(inds["CMF"].get("cmf"))
    except Exception:
        pass

    # 5. Derivatives & SmartMoney
    try:
        req = urllib.request.Request(f"https://www.okx.com/api/v5/public/funding-rate?instId={inst_id}", headers=headers)
        with urllib.request.urlopen(req, timeout=3) as resp:
            d = json.loads(resp.read().decode("utf-8"))
            if d.get("code") == "0" and d.get("data"):
                factors["smart_money_derivatives"]["funding_rate_pct"] = round(safe_float(d["data"][0].get("fundingRate")) * 100, 4)
    except Exception:
        pass

    try:
        req = urllib.request.Request(f"https://www.okx.com/api/v5/public/open-interest?instType=SWAP&instId={inst_id}", headers=headers)
        with urllib.request.urlopen(req, timeout=3) as resp:
            d = json.loads(resp.read().decode("utf-8"))
            if d.get("code") == "0" and d.get("data"):
                usd = safe_float(d["data"][0].get("oiUsd", 0))
                factors["smart_money_derivatives"]["oi_usd"] = f"{round(usd / 1e8, 2)}亿 U" if usd > 1e8 else f"{round(usd / 1e4, 1)}万 U"
    except Exception:
        pass

    if ccy:
        try:
            req = urllib.request.Request(f"https://www.okx.com/api/v5/rubik/stat/contracts/long-short-account-ratio?ccy={ccy}&period=5m", headers=headers)
            with urllib.request.urlopen(req, timeout=3) as resp:
                d = json.loads(resp.read().decode("utf-8"))
                if d.get("code") == "0" and d.get("data") and len(d["data"]) > 0:
                    factors["smart_money_derivatives"]["long_short_ratio"] = str(d["data"][0][1])
        except Exception:
            pass

        try:
            req = urllib.request.Request(f"https://www.okx.com/api/v5/rubik/stat/taker-volume?ccy={ccy}&instType=CONTRACTS&period=5m", headers=headers)
            with urllib.request.urlopen(req, timeout=3) as resp:
                d = json.loads(resp.read().decode("utf-8"))
                if d.get("code") == "0" and d.get("data") and len(d["data"]) > 0:
                    b_vol = safe_float(d["data"][0][1])
                    s_vol = safe_float(d["data"][0][2])
                    net_diff = b_vol - s_vol
                    factors["volume_money_flow"]["taker_net_usd"] = f"{round(net_diff / 1e4, 1)}万 U"
        except Exception:
            pass

    # SmartMoney Overlay（仅当真有数据源时覆盖缺失占位；无源时保留 available=False）
    if ccy in smart_money_pool:
        factors["smart_money_derivatives"]["available"] = True
        factors["smart_money_derivatives"]["reason"] = ""
        sm = smart_money_pool[ccy]
        ls = sm.get("longShortRatio", {})
        notional = sm.get("notional", {})
        win = sm.get("winRate", {})
        w_long = round(safe_float(ls.get("weightedLongRatio", 0.5)) * 100, 1)
        net_usdt = safe_float(notional.get("netNotionalUsdt", 0))
        net_str = f"{round(net_usdt / 1e4, 1)}万 U" if abs(net_usdt) >= 1e4 else f"{round(net_usdt, 0)} U"
        
        factors["smart_money_derivatives"]["weighted_long_pct"] = w_long
        factors["smart_money_derivatives"]["smart_money_flow_usd"] = net_str
        if ls.get("longShortRatio") is not None:
            factors["smart_money_derivatives"]["long_short_ratio"] = str(round(safe_float(ls.get("longShortRatio")), 2))
        long_avg = safe_float(notional.get("smartMoneyLongAvgEntry", 0))
        short_avg = safe_float(notional.get("smartMoneyShortAvgEntry", 0))
        if long_avg > 0:
            factors["smart_money_derivatives"]["avg_long_entry"] = f"{long_avg:.6g}"
        if short_avg > 0:
            factors["smart_money_derivatives"]["avg_short_entry"] = f"{short_avg:.6g}"
        long_win = safe_float(win.get("avgLongWinRate", 0))
        short_win = safe_float(win.get("avgShortWinRate", 0))
        if long_win > 0 or short_win > 0:
            parts = []
            if long_win > 0:
                parts.append(f"多胜率{round(long_win * 100, 1)}%")
            if short_win > 0:
                parts.append(f"空胜率{round(short_win * 100, 1)}%")
            factors["smart_money_derivatives"]["top_win_rate"] = " / ".join(parts)
        if w_long >= 65.0 and net_usdt > 0:
            factors["smart_money_derivatives"]["signal"] = "BULL_ACCUMULATION"
        elif w_long <= 35.0 and net_usdt < 0:
            factors["smart_money_derivatives"]["signal"] = "BEAR_DISTRIBUTION"

    # 复合 alpha 打分与信号建议（-100~+100）
    # 阶段 4·B3 第二十九刀：整段迁至 scripts/factors/scoring.py::score_composite_alpha，
    # 本处只保留调用。八项门槛/加减分与两处阻尼的说明见该模块文档串。
    score_composite_alpha(factors)


    return factors

def update_factor_library() -> Dict[str, Any]:
    """Fetch and calculate multi-pillar factor library snapshot for 6 instruments."""
    # 1. Smart Money Pool：通过 Binance 公开大户指标 + OKX Rubik 备选双源容灾采集
    #    双源均不可用时保持空池 → 优雅缺失化 available=False
    try:
        try:
            from scripts.factors.smart_money import fetch_smart_money_pool
        except ImportError:
            from factors.smart_money import fetch_smart_money_pool
        smart_money_pool = fetch_smart_money_pool(TARGET_INSTRUMENTS)
    except Exception as e:
        print(f"[Factor Library] SmartMoney pool fetch fallback: {e}")
        smart_money_pool = {}

    # 2. Parallel Factor Computations
    with ThreadPoolExecutor(max_workers=6) as executor:
        results = list(executor.map(lambda item: compute_instrument_factors(item, smart_money_pool), TARGET_INSTRUMENTS))

    snapshot = {
        "timestamp": int(time.time()),
        "time_str": datetime.now(_BJ).isoformat(sep=" ", timespec="seconds"),
        "instruments": results
    }

    # Atomic Write
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        tmp_file = FACTOR_LIB_CACHE_FILE + ".tmp"
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(snapshot, f, ensure_ascii=False, indent=2)
        os.replace(tmp_file, FACTOR_LIB_CACHE_FILE)
    except Exception as e:
        print(f"[Factor Library] Cache write error: {e}")

    return snapshot

if __name__ == "__main__":
    snap = update_factor_library()
    print(f"✅ Factor Library Engine Snapshot Complete at {snap['time_str']}:")
    for inst in snap["instruments"]:
        print(f"[{inst['name']}] Alpha Score: {inst['composite_alpha_score']:+5.1f} | Signal: {inst['signal_recommendation']:10} | ADX: {inst['trend_momentum']['adx_1h']} | SM Long: {inst['smart_money_derivatives']['weighted_long_pct']}% | CMF: {inst['volume_money_flow']['cmf_1h']} | Depth: {inst['microstructure']['bid_ask_depth_ratio']}")
