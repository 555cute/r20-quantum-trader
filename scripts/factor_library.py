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
import json
import time
import subprocess
import urllib.request
from typing import Dict, Any, List, Optional
from concurrent.futures import ThreadPoolExecutor

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(WORKSPACE_DIR, "data")
FACTOR_LIB_CACHE_FILE = os.path.join(DATA_DIR, "factor_library_snapshot.json")

from instrument_pool import load_instruments

TARGET_INSTRUMENTS = load_instruments()

def safe_float(val: Any, default: float = 0.0) -> float:
    try:
        f = float(val)
        return f if f == f and abs(f) != float("inf") else default
    except (TypeError, ValueError):
        return default

def compute_instrument_factors(item: Dict[str, Any], smart_money_pool: Dict[str, Any]) -> Dict[str, Any]:
    inst_id = item["instId"]
    name = item["name"]
    ccy = item.get("ccy", "")
    headers = {"User-Agent": "Mozilla/5.0"}
    
    factors = {
        "instId": inst_id,
        "name": name,
        "timestamp": int(time.time()),
        "price": 0.0,
        "chg24h": 0.0,
        
        # Pillar 1: Trend & Momentum
        "trend_momentum": {
            "adx_1h": 0.0,
            "rsi_14": 50.0,
            "kdj_j": 50.0,
            "vwap_bias_pct": 0.0,
            "trend_regime": "NEUTRAL"
        },
        
        # Pillar 2: Volatility & Channel
        "volatility_channel": {
            "atr_14": 0.0,
            "atr_pct": 0.0,
            "atr_1h": 0.0,
            "atr_1h_pct": 0.0,
            "bb_width_1h": 0.0,
            "volatility_regime": "NORMAL"
        },
        
        # Pillar 3: Volume & Money Flow
        "volume_money_flow": {
            "vol_ratio_15m": 1.0,
            "obv_flow": "NEUTRAL",
            "cmf_1h": 0.0,
            "taker_net_usd": "0 U",
            "flow_sentiment": "BALANCED"
        },
        
        # Pillar 4: Microstructure & Orderbook
        "microstructure": {
            "bid_px": 0.0,
            "ask_px": 0.0,
            "spread_pct": 0.0,
            "bid_ask_depth_ratio": 1.0,
            "depth_bias": "NEUTRAL"
        },
        
        # Pillar 5: Smart Money & Derivatives
        "smart_money_derivatives": {
            "weighted_long_pct": 50.0,
            "smart_money_flow_usd": "0 U",
            "funding_rate_pct": 0.0,
            "oi_usd": "--",
            "long_short_ratio": "--",
            "avg_long_entry": "--",
            "avg_short_entry": "--",
            "top_win_rate": "--",
            "signal": "NEUTRAL"
        },

        # Pillar 6: Calculus, Definite Integrals & Probability Theory
        "calculus_dynamics": {
            "velocity": 0.0,
            "acceleration": 0.0,
            "impulse": 0.0,
            "jerk": 0.0,
            "regime": "RANGE_LOW_VELOCITY",
            "quality": 0.0,
            "direction": 0
        },
        "definite_integrals": {
            "energy_integral": 0.0,
            "deviation_area_integral": 0.0,
            "volume_action_integral": 0.0,
            "integral_regime": "BALANCED_ENERGY"
        },
        "probability_theory": {
            "skewness": 0.0,
            "kurtosis": 0.0,
            "continuation_prob_pct": 50.0,
            "breakdown_prob_pct": 50.0,
            "var_95_pct": 1.5,
            "cvar_95_pct": 2.2,
            "prob_regime": "GAUSSIAN_BALANCED",
            "is_fat_tail": False
        },
        # Pillar 7: Cross Exchange Arbitrage & Multi-Venue Insights (OKX / Binance / Gate)
        "cross_exchange_insights": {
            "prices": {},
            "spread_disparity_pct": 0.0,
            "binance_top_trader_long_short_ratio": None,
            "gate_funding_rate": None,
        },
        
        # Composite Factor Score (-100 to +100)
        "composite_alpha_score": 0.0,
        "signal_recommendation": "WAIT"
    }

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

    # 2. Orderbook Depth (Top 5 Level Imbalance)
    try:
        cmd = f"okx market orderbook {inst_id} --sz 5 --json 2>/dev/null"
        res = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=3)
        if res.stdout:
            ob_data = json.loads(res.stdout)
            if isinstance(ob_data, list) and ob_data:
                bids = ob_data[0].get("bids", [])
                asks = ob_data[0].get("asks", [])
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
        req = urllib.request.Request(f"https://www.okx.com/api/v5/market/candles?instId={inst_id}&bar=15m&limit=24", headers=headers)
        with urllib.request.urlopen(req, timeout=3) as resp:
            d = json.loads(resp.read().decode("utf-8"))
            if d.get("code") == "0" and d.get("data") and len(d["data"]) >= 15:
                raw_candles = d["data"]
                closes = [safe_float(c[4]) for c in reversed(raw_candles)]
                highs = [safe_float(c[2]) for c in reversed(raw_candles)]
                lows = [safe_float(c[3]) for c in reversed(raw_candles)]
                vols = [safe_float(c[5]) for c in reversed(raw_candles)]

                # ATR 14
                tr_list = []
                for i in range(1, len(closes)):
                    tr = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
                    tr_list.append(tr)
                if len(tr_list) >= 14:
                    atr = sum(tr_list[-14:]) / 14
                    factors["volatility_channel"]["atr_14"] = round(atr, 4)
                    if factors["price"] > 0:
                        factors["volatility_channel"]["atr_pct"] = round(atr / factors["price"] * 100, 2)

                # RSI 14
                diffs = [closes[i] - closes[i-1] for i in range(1, len(closes))]
                gains = [d if d > 0 else 0 for d in diffs]
                losses = [-d if d < 0 else 0 for d in diffs]
                if len(gains) >= 14:
                    avg_g = sum(gains[-14:]) / 14
                    avg_l = sum(losses[-14:]) / 14
                    rs = (avg_g / avg_l) if avg_l > 0 else 100.0
                    factors["trend_momentum"]["rsi_14"] = round(100.0 - (100.0 / (1.0 + rs)), 1)

                # VWAP Bias
                pv_sum = sum(closes[i] * vols[i] for i in range(len(closes)))
                v_sum = sum(vols)
                if v_sum > 0:
                    vwap = pv_sum / v_sum
                    factors["trend_momentum"]["vwap_bias_pct"] = round((factors["price"] - vwap) / vwap * 100, 2)

                # Vol Ratio
                if len(vols) >= 6:
                    avg_v5 = sum(vols[-6:-1]) / 5
                    if avg_v5 > 0:
                        factors["volume_money_flow"]["vol_ratio_15m"] = round(vols[-1] / avg_v5, 2)

                # OBV
                obv = 0
                for i in range(1, len(closes)):
                    if closes[i] > closes[i-1]: obv += vols[i]
                    elif closes[i] < closes[i-1]: obv -= vols[i]
                factors["volume_money_flow"]["obv_flow"] = "BULL_FLOW" if obv > 0 else ("BEAR_FLOW" if obv < 0 else "NEUTRAL")

                # Pillar 6: Calculus, Definite Integrals & Probability Theory (15M High-Resolution)
                try:
                    sys.path.append(os.path.join(WORKSPACE_DIR, "scripts"))
                    from calculus_engine import calculate_calculus
                    c_res = calculate_calculus(closes, highs, lows, vols)
                    if c_res.get("valid"):
                        # Calculus Dynamics
                        factors["calculus_dynamics"]["velocity"] = c_res.get("velocity", 0.0)
                        factors["calculus_dynamics"]["acceleration"] = c_res.get("acceleration", 0.0)
                        factors["calculus_dynamics"]["impulse"] = c_res.get("impulse", 0.0)
                        factors["calculus_dynamics"]["jerk"] = c_res.get("jerk", 0.0)
                        factors["calculus_dynamics"]["regime"] = c_res.get("regime", "RANGE_LOW_VELOCITY")
                        factors["calculus_dynamics"]["quality"] = c_res.get("quality", 0.0)
                        factors["calculus_dynamics"]["direction"] = c_res.get("direction", 0)

                        # Definite Integrals
                        d_int = c_res.get("definite_integrals", {})
                        factors["definite_integrals"]["energy_integral"] = d_int.get("energy_integral", 0.0)
                        factors["definite_integrals"]["deviation_area_integral"] = d_int.get("deviation_area_integral", 0.0)
                        factors["definite_integrals"]["volume_action_integral"] = d_int.get("volume_action_integral", 0.0)
                        factors["definite_integrals"]["integral_regime"] = d_int.get("integral_regime", "BALANCED_ENERGY")

                        # Probability Theory & Stochastic Modeling
                        p_th = c_res.get("probability_theory", {})
                        factors["probability_theory"]["skewness"] = p_th.get("skewness", 0.0)
                        factors["probability_theory"]["kurtosis"] = p_th.get("kurtosis", 0.0)
                        factors["probability_theory"]["continuation_prob_pct"] = p_th.get("continuation_prob_pct", 50.0)
                        factors["probability_theory"]["breakdown_prob_pct"] = p_th.get("breakdown_prob_pct", 50.0)
                        factors["probability_theory"]["var_95_pct"] = p_th.get("var_95_pct", 1.5)
                        factors["probability_theory"]["cvar_95_pct"] = p_th.get("cvar_95_pct", 2.2)
                        factors["probability_theory"]["prob_regime"] = p_th.get("prob_regime", "GAUSSIAN_BALANCED")
                        factors["probability_theory"]["is_fat_tail"] = p_th.get("is_fat_tail", False)
                except Exception:
                    pass
    except Exception:
        pass

    # 3.5. 1H Candles -> 1H ATR & 1H RSI
    try:
        req = urllib.request.Request(f"https://www.okx.com/api/v5/market/candles?instId={inst_id}&bar=1H&limit=24", headers=headers)
        with urllib.request.urlopen(req, timeout=3) as resp:
            d = json.loads(resp.read().decode("utf-8"))
            if d.get("code") == "0" and d.get("data") and len(d["data"]) >= 15:
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
    except Exception:
        pass

    # 4. OKX Official Indicators (ADX, KDJ, BBWidth, CMF)
    for ind, key_path in [
        ("adx", ("trend_momentum", "adx_1h")),
        ("kdj", ("trend_momentum", "kdj_j")),
        ("bbwidth", ("volatility_channel", "bb_width_1h")),
        ("cmf", ("volume_money_flow", "cmf_1h"))
    ]:
        try:
            cmd = f"okx market indicator {ind} {inst_id} --bar 1H --json 2>/dev/null"
            res = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=3)
            if res.stdout:
                ind_res = json.loads(res.stdout)
                if isinstance(ind_res, list) and ind_res:
                    tfs = ind_res[0].get("data", [{}])[0].get("timeframes", {}).get("1H", {}).get("indicators", {})
                    ind_key = ind.upper().replace("-", "")
                    items = tfs.get(ind_key, [])
                    if items:
                        vals = items[0].get("values", {})
                        if ind == "adx": factors[key_path[0]][key_path[1]] = safe_float(vals.get("adx"))
                        elif ind == "kdj": factors[key_path[0]][key_path[1]] = safe_float(vals.get("j"))
                        elif ind == "bbwidth": factors[key_path[0]][key_path[1]] = safe_float(vals.get("bbWidth"))
                        elif ind == "cmf": factors[key_path[0]][key_path[1]] = safe_float(vals.get("cmf"))
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

    # 5.5 Cross Exchange Insights (Binance & Gate Public Integration)
    try:
        from multi_exchange_feed import fetch_cross_exchange_insights
        base_sym = inst_id.split("-")[0] if "-" in inst_id else inst_id
        cross_res = fetch_cross_exchange_insights(base_sym)
        if cross_res:
            factors["cross_exchange_insights"] = cross_res
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

    # SmartMoney Overlay
    if ccy in smart_money_pool:
        sm = smart_money_pool[ccy]
        ls = sm.get("longShortRatio", {})
        notional = sm.get("notional", {})
        win = sm.get("winRate", {})
        w_long = round(safe_float(ls.get("weightedLongRatio", 0.5)) * 100, 1)
        net_usdt = safe_float(notional.get("netNotionalUsdt", 0))
        net_str = f"{round(net_usdt / 1e4, 1)}万 U" if abs(net_usdt) >= 1e4 else f"{round(net_usdt, 0)} U"
        
        factors["smart_money_derivatives"]["weighted_long_pct"] = w_long
        factors["smart_money_derivatives"]["smart_money_flow_usd"] = net_str
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

    # =========================================================================
    # COMPOSITE HIGH-ALPHA SCORING (-100 to +100)
    # =========================================================================
    score = 0.0
    
    # 1. ADX Trend Filter (Threshold = 22)
    adx = factors["trend_momentum"]["adx_1h"]
    if adx >= 22.0:
        score += 15.0 if factors["trend_momentum"]["rsi_14"] >= 50 else -15.0
        factors["trend_momentum"]["trend_regime"] = "STRONG_TREND"
    else:
        factors["trend_momentum"]["trend_regime"] = "CHOP_RANGE"

    # 2. Smart Money Direction
    sm_long = factors["smart_money_derivatives"]["weighted_long_pct"]
    if sm_long >= 70.0: score += 30.0
    elif sm_long <= 35.0: score -= 30.0

    # 3. RSI & KDJ Dynamic Momentum
    rsi = factors["trend_momentum"]["rsi_14"]
    kdj_j = factors["trend_momentum"]["kdj_j"]
    if rsi >= 55.0 and kdj_j >= 60.0: score += 20.0
    elif rsi <= 45.0 and kdj_j <= 40.0: score -= 20.0

    # 4. Money Flow (OBV & CMF)
    cmf = factors["volume_money_flow"]["cmf_1h"]
    obv_f = factors["volume_money_flow"]["obv_flow"]
    if cmf > 0.05 and obv_f == "BULL_FLOW": score += 20.0
    elif cmf < -0.05 and obv_f == "BEAR_FLOW": score -= 20.0

    # 5. Orderbook Microstructure Imbalance
    depth_r = factors["microstructure"]["bid_ask_depth_ratio"]
    if depth_r >= 1.4: score += 15.0
    elif depth_r <= 0.7: score -= 15.0

    # 6. Calculus, Definite Integrals & Probability Theory Modulation
    c_dyn = factors["calculus_dynamics"]
    c_v = c_dyn.get("velocity", 0.0)
    c_a = c_dyn.get("acceleration", 0.0)
    c_i = c_dyn.get("impulse", 0.0)
    c_j = abs(c_dyn.get("jerk", 0.0))
    c_regime = c_dyn.get("regime", "")

    # Bullish acceleration vs deceleration
    if c_regime == "BULL_ACCELERATING" or (c_v > 0.2 and c_a > 0.1 and c_i > 0):
        score += 15.0
    elif c_regime == "BULL_DECELERATING" or (c_v > 0.2 and c_a < -0.3):
        score -= 10.0 # Anti-FOMO top chasing penalty
    elif c_regime == "BEAR_ACCELERATING" or (c_v < -0.2 and c_a < -0.1 and c_i < 0):
        score -= 15.0
    elif c_regime == "BEAR_DECELERATING" or (c_v < -0.2 and c_a > 0.3):
        score += 10.0 # Anti-bottom chasing penalty

    # 7. Definite Integrals & Energy Modulation
    d_int = factors.get("definite_integrals", {})
    e_int = d_int.get("energy_integral", 0.0)
    dev_area = d_int.get("deviation_area_integral", 0.0)
    if e_int > 1.2 and dev_area > 0.8:
        score += 10.0 # Multi-period net positive displacement energy
    elif e_int < -1.2 and dev_area < -0.8:
        score -= 10.0 # Multi-period net negative depletion
    elif abs(dev_area) >= 2.8:
        # Overstretched deviation integral penalty: trigger mean-reversion caution
        score *= 0.8

    # 8. Probability Theory & Stochastic Risk
    p_th = factors.get("probability_theory", {})
    p_cont = p_th.get("continuation_prob_pct", 50.0)
    p_break = p_th.get("breakdown_prob_pct", 50.0)
    is_fat = p_th.get("is_fat_tail", False)
    if p_cont >= 72.0:
        score += 10.0 # High conditional continuation probability
    elif p_break >= 72.0:
        score -= 10.0 # High conditional breakdown probability

    # High Jerk or Extreme Fat Tail Shock Dampener
    if c_j >= 1.8 or c_regime == "SHOCK_HIGH_JERK" or (is_fat and p_th.get("kurtosis", 0) >= 3.5):
        score *= 0.6 # dampen conviction under high-jerk shock / extreme fat tails

    factors["composite_alpha_score"] = round(score, 1)

    # Decision Recommendation
    if score >= 45.0 and adx >= 20.0 and c_j < 1.8:
        factors["signal_recommendation"] = "BUY_LONG"
    elif score <= -45.0 and adx >= 20.0 and c_j < 1.8:
        factors["signal_recommendation"] = "SELL_SHORT"
    else:
        factors["signal_recommendation"] = "WAIT"

    return factors

def update_factor_library() -> Dict[str, Any]:
    """Fetch and calculate multi-pillar factor library snapshot for 6 instruments."""
    # 1. Fetch Smart Money Pool
    smart_money_pool = {}
    try:
        cmd = "okx smartmoney signal-overview-by-filter --instCcyList BTC,ETH,SOL,DOGE,SUI,LINK --json 2>/dev/null"
        res = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=8)
        if res.stdout:
            d = json.loads(res.stdout).get("data", [])
            smart_money_pool = {item.get("ccy"): item for item in d if item.get("ccy")}
    except Exception as e:
        print(f"[Factor Library] SmartMoney pool error: {e}")

    # 2. Parallel Factor Computations
    with ThreadPoolExecutor(max_workers=6) as executor:
        results = list(executor.map(lambda item: compute_instrument_factors(item, smart_money_pool), TARGET_INSTRUMENTS))

    snapshot = {
        "timestamp": int(time.time()),
        "time_str": time.strftime("%Y-%m-%d %H:%M:%S"),
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
