"""多因子提取（B3 抽取第二块）。

从 `scripts/ai_factor_trader.py` 搬出的 `fetch_single_instrument_data`（258 行）——
每个标的每周期跑一次的因子装配。

## 注入的 4 个依赖及其理由

| 依赖 | 为什么注入而不是 import |
|---|---|
| `fetch_candles_direct` | 测试 patch 了 `ai_factor_trader.fetch_candles_direct` |
| `news_sentiment_file` | 测试 patch 了 `ai_factor_trader.NEWS_SENTIMENT_FILE`（本模块参数去掉 `_FILE` 后缀，语义是"路径值"） |
| `instrument_profile` | 留在门面（它自身还读 `ASSET_CLASS_PROFILES`，且被测试按门面属性调用） |
| `load_adaptive_config` | 同上，属于门面里可被替换的配置读取面 |

**通例**：`tests/risk_test_env.py::pin_baseline_risk_env()` 的原地重载名单只有
`risk_constants` / `ai_factor_trader` / `ai_brain_trader`，**不含任何子模块** ——
子模块 import 期绑定的任何风控/配置值都不会被刷新，会让基线风控用例随机翻红。
凡读配置、或读被 patch 路径的东西，一律走调用期注入。

## 可以直接导入的

`calc_*`（6 个）与 `effective_risk_per_trade` / `quantize_size` 都来自
`r20_backend.execution` —— 与门面**同一个对象**，因此
`test_audit_config_p1_alignment` 的 `assertIs(getattr(trader, name), getattr(sizing, name))`
照旧成立。`calculate_multi_timeframe` 保持原样在函数内延迟导入
（它来自 `calculus_engine`，测试环境里不一定可导入）。
"""
import json
import os
import urllib
import warnings

from r20_backend.execution import (
    calc_atr,
    calc_bollinger_squeeze,
    calc_ema,
    calc_macd_histogram_acceleration,
    calc_obv_trend,
    calc_rsi,
    effective_risk_per_trade,
    quantize_size,
)


def fetch_single_instrument_data(item, all_positions, usdt_available, *,
                                 news_sentiment_file,
                                 fetch_candles_direct,
                                 instrument_profile,
                                 load_adaptive_config):
    """装配单个标的的多因子特征字典。依赖由门面注入，理由见模块 docstring。"""
    inst_id = item["instId"]
    name = item["name"]
    asset_type = item["type"]
    base_sz = item["base_sz"]
    # 交易所最小下单量与步长（OKX 多数永续为 0.01 张），此前被代码的 int()+max(1,..) 完全忽略
    min_sz = float(item.get("minSz", 1) or 1)

    f = {
        "instId": inst_id,
        "name": name,
        "type": asset_type,
        "base_sz": base_sz,
        "sz": base_sz,
        "precision": item["precision"],
        "ctVal": item["ctVal"],
        "risk_per_trade_usd": effective_risk_per_trade(item.get("risk_per_trade_usd", 15.0), usdt_available),
        "minSz": min_sz,
        # 标的分级信息随因子包下发，供拦截插件与提示词按「层级」而非写死币种名做通用判断
        "tier": item.get("tier", "tier_2_momentum"),
        "max_leverage": item.get("max_leverage", 3),
        "sl_atr_mult": item.get("sl_atr_mult", 2.2),
        "price": 0.0,
        "change24h": 0.0,
        "vol24h": 0.0,
        "rsi": 50.0,
        "rsi_7": 50.0,
        "ema9": 0.0,
        "ema21": 0.0,
        "ema55": 0.0,
        "ema21_slope_pct": 0.0,
        "vwap": 0.0,
        "vwap_bias": 0.0,
        "macd_hist": 0.0,
        "macd_accel": 0.0,
        "obv_flow": "NEUTRAL",
        "bb_bandwidth": 0.0,
        "bb_squeeze": False,
        "atr": 0.0,
        "atr_pct": 0.0,
        "vol_15m": 0.0,
        "vol_ma20": 0.0,
        "vol_ratio": 1.0,
        "is_bull_candle_15m": False,
        "is_bear_candle_15m": False,
        "lower_wick_ratio": 0.0,
        "upper_wick_ratio": 0.0,
        "market_regime": "CHOP",
        "structure_1h": "CHOP",
        "trend_1h_bullish": True,
        "trend_4h_bullish": True,
        "trend_1h_bearish": False,
        "trend_4h_bearish": False,
        "sentiment_score": 0.0,
        "position": None,
        "market_data_valid": False,
        "usdtAvailable": usdt_available
    }

    # Match existing position
    for p in all_positions:
        if p.get("instId") == inst_id:
            pos_val = float(p.get("pos", 0))
            if pos_val != 0:
                f["position"] = {
                    "instId": inst_id,
                    "name": name,
                    "side": p.get("posSide", p.get("side", "")),
                    "pos": pos_val,
                    "avgPx": float(p.get("avgPx", 0)),
                    "markPx": float(p.get("markPx", p.get("last", 0)) or 0),
                    "upl": float(p.get("upl", 0)),
                    "uplRatio": float(p.get("uplRatio", 0) or 0),
                    "lever": p.get("lever", "3"),
                    "venue": str(p.get("venue") or p.get("exchange") or "okx").lower(),
                    "exchange": str(p.get("venue") or p.get("exchange") or "okx").lower(),
                    "raw": p.get("raw", {}),
                }
                break

    # 1. Fetch 15M Candles
    raw_15m = fetch_candles_direct(inst_id, "15m", 45)
    if raw_15m:
        candles_15m = list(reversed(raw_15m))
        closes = [float(c[4]) for c in candles_15m]
        vols = [float(c[5]) if len(c) > 5 else 1.0 for c in candles_15m]
        
        f["price"] = closes[-1]
        f["rsi"] = calc_rsi(closes, 14)
        f["rsi_7"] = calc_rsi(closes, 7)
        f["ema9"] = calc_ema(closes, 9)
        f["ema21"] = calc_ema(closes, 21)
        f["ema55"] = calc_ema(closes, 55)
        
        # Calculate EMA21 Slope over last 3 bars
        if len(closes) >= 5:
            prev_e21 = calc_ema(closes[:-3], 21)
            f["ema21_slope_pct"] = ((f["ema21"] - prev_e21) / prev_e21 * 100.0) if prev_e21 > 0 else 0.0

        f["atr"] = calc_atr(candles_15m, 14)
        if f["price"] > 0:
            f["atr_pct"] = (f["atr"] / f["price"]) * 100.0

        # MACD Acceleration
        m_line, m_sig, m_hist, m_accel = calc_macd_histogram_acceleration(closes)
        f["macd_hist"] = round(m_hist, 4)
        f["macd_accel"] = round(m_accel, 4)

        # OBV Flow
        _, obv_flow = calc_obv_trend(closes, vols)
        f["obv_flow"] = obv_flow

        # Bollinger Bands & Squeeze
        bw, std_d, is_sq = calc_bollinger_squeeze(closes)
        f["bb_bandwidth"] = round(bw, 2)
        f["bb_squeeze"] = is_sq

        # Multi-scale VWAP & Bias
        cum_pv = sum([closes[i] * vols[i] for i in range(-15, 0)])
        cum_v = sum(vols[-15:])
        f["vwap"] = (cum_pv / cum_v) if cum_v > 0 else f["price"]
        if f["vwap"] > 0:
            f["vwap_bias"] = ((f["price"] - f["vwap"]) / f["vwap"]) * 100.0
        
        # Latest 15M Candle Geometry
        last_c = candles_15m[-1]
        c_open, c_high, c_low, c_close = float(last_c[1]), float(last_c[2]), float(last_c[3]), float(last_c[4])
        f["bidPx"] = f["price"]
        f["askPx"] = f["price"]
        # Fetch Real-time Orderbook Ticker BBO (Best Bid & Ask) for Precision Limit Placement
        try:
            req_t = urllib.request.Request(f"https://www.okx.com/api/v5/market/ticker?instId={inst_id}", headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req_t, timeout=3) as response_t:
                d_t = json.loads(response_t.read().decode("utf-8"))
                if d_t.get("code") == "0" and "data" in d_t and len(d_t["data"]) > 0:
                    t_item = d_t["data"][0]
                    f["bidPx"] = float(t_item.get("bidPx", f["price"]) or f["price"])
                    f["askPx"] = float(t_item.get("askPx", f["price"]) or f["price"])
        except Exception as _bbo_err:
            # 2026-09-16：原先静默 pass —— BBO 取不到时 bid/ask 会悄悄退回最新价，
            # 限价精度随之降级而无人知道。保留降级（不阻断取数），但必须留痕。
            warnings.warn(
                f"[factors] {inst_id} BBO 盘口取价失败，bid/ask 退回最新价（限价精度降级）: {_bbo_err!r}",
                RuntimeWarning)

        f["is_bull_candle_15m"] = (c_close > c_open)
        f["is_bear_candle_15m"] = (c_close < c_open)
        
        total_len = max(c_high - c_low, f["price"] * 0.0001)
        lower_wick = min(c_open, c_close) - c_low
        upper_wick = c_high - max(c_open, c_close)
        f["lower_wick_ratio"] = lower_wick / total_len
        f["upper_wick_ratio"] = upper_wick / total_len
        
        f["vol_15m"] = vols[-1]
        f["vol_ma20"] = sum(vols[-20:]) / min(len(vols), 20) if vols else 1.0
        f["vol_ratio"] = round(f["vol_15m"] / f["vol_ma20"], 2) if f["vol_ma20"] > 0 else 1.0

    # 2. Fetch 1H & 4H Trend Confluence
    raw_1h = fetch_candles_direct(inst_id, "1H", 35)
    if raw_1h:
        c_1h = list(reversed(raw_1h))
        closes_1h = [float(c[4]) for c in c_1h]
        highs_1h = [float(c[2]) for c in c_1h]
        lows_1h = [float(c[3]) for c in c_1h]
        
        # 1H ATR 14 for Macro Swing Protection
        f["atr_1h"] = calc_atr(c_1h, 14)
        f["atr_15m"] = f["atr"]
        f["atr"] = max(f["atr_1h"], f["atr_15m"] * 1.5, f["price"] * 0.012)
        f["atr_pct"] = (f["atr"] / f["price"]) * 100.0
        
        e9_1h = calc_ema(closes_1h, 9)
        e21_1h = calc_ema(closes_1h, 21)
        # 审计D(2026-09-13)：e55_1h 死算清除（趋势判定只用 e9/e21；ema55 基线另在 f["ema55"] 生产）
        
        f["trend_1h_bullish"] = (e9_1h >= e21_1h and closes_1h[-1] >= e21_1h * 0.996)
        f["trend_1h_bearish"] = (e9_1h <= e21_1h and closes_1h[-1] <= e21_1h * 1.004)

        recent_low = min(lows_1h[-5:])
        prev_low = min(lows_1h[-15:-5])
        recent_high = max(highs_1h[-5:])
        prev_high = max(highs_1h[-15:-5])

        if recent_low < prev_low and recent_high < prev_high:
            f["structure_1h"] = "LH_LL"
        elif recent_high > prev_high and recent_low > prev_low:
            f["structure_1h"] = "HH_HL"
        else:
            f["structure_1h"] = "CHOP"
    
    raw_4h = fetch_candles_direct(inst_id, "4H", 25)
    if raw_4h:
        c_4h = list(reversed(raw_4h))
        closes_4h = [float(c[4]) for c in c_4h]
        e9_4h = calc_ema(closes_4h, 9)
        e21_4h = calc_ema(closes_4h, 21)
        f["trend_4h_bullish"] = (e9_4h >= e21_4h)
        f["trend_4h_bearish"] = (e9_4h <= e21_4h)

    # 3. Dynamic Multi-Wave Regime Classification (Strict 15M + 1H + 4H Real-Time Alignment)
    # Anti-Inertia Fix: Never classify as BEAR_TREND if short-term 15M is actively reversing upwards (EMA9 > EMA21) or price > 15M EMA21/55
    is_15m_bullish = (f["ema9"] >= f["ema21"] and f["price"] >= f["ema21"] * 0.998)
    is_15m_bearish = (f["ema9"] <= f["ema21"] and f["price"] <= f["ema21"] * 1.002)

    if f["trend_1h_bullish"] and is_15m_bullish and (f["structure_1h"] == "HH_HL" or f["price"] >= f["ema21"]):
        f["market_regime"] = "BULL_TREND"
    elif f["trend_1h_bearish"] and is_15m_bearish and (f["structure_1h"] == "LH_LL" or f["price"] <= f["ema21"]):
        f["market_regime"] = "BEAR_TREND"
    else:
        # If 1H says bearish but 15M is rebounding upwards (e.g. V-reversal), strictly lock into CHOP / TRANSITION
        f["market_regime"] = "CHOP"

    # 4. Load Real-time News Sentiment
    if os.path.exists(news_sentiment_file):
        try:
            with open(news_sentiment_file, "r", encoding="utf-8") as f_news:
                n_data = json.load(f_news)
                coins_s = n_data.get("coins_sentiment", {})
                if name in coins_s:
                    f["sentiment_score"] = float(coins_s[name].get("sentiment_factor_score", 0.0) or 0.0)
                # 2026-09-16：区分「情绪=0（真中性）」与「情绪面没读到」。
                # 原先读失败静默 pass，`sentiment_score` 停在默认 0.0 —— 正是
                # 本仓红线「缺失≠0」的反例：主脑会把"没数据"当"中性"。
                f["sentiment_available"] = bool(name in coins_s)
        except Exception as _sent_err:
            f["sentiment_available"] = False
            warnings.warn(
                f"[factors] {name} 舆情文件读取失败，sentiment_score 保持默认"
                f"（标记 sentiment_available=False，不得当成中性）: {_sent_err!r}",
                RuntimeWarning)

    # 5. Causal Multi-Timeframe Calculus Dynamics
    f["calculus"] = {"valid": False, "regime": "RANGE_LOW_VELOCITY", "velocity": 0.0, "acceleration": 0.0, "impulse": 0.0, "max_abs_jerk": 0.0, "quality": 0.0}
    try:
        from calculus_engine import calculate_multi_timeframe
        f["calculus"] = calculate_multi_timeframe({
            "15M": raw_15m,
            "1H": raw_1h,
            "4H": raw_4h
        })
    except Exception as _calc_err:
        # 2026-09-16：原先静默 pass —— 此处失败会退化成「全 0 动力学」
        # （v=a=j=I=0、regime=RANGE_LOW_VELOCITY），主脑会照着 0 推理。
        # 保留 valid=False 的诚实形状，但把原因一并写进去并告警。
        f["calculus"]["error"] = f"{type(_calc_err).__name__}: {_calc_err}"[:200]
        warnings.warn(
            f"[factors] {name} 多周期动力学计算失败，已退化为零动力学"
            f"（calculus.valid=False，原因见 calculus.error）: {_calc_err!r}",
            RuntimeWarning)

    # 6. Dynamic Equal-Risk Position Sizing with AI Self-Evolution Kelly Multipliers
    adaptive_cfg = load_adaptive_config()
    pos_multipliers = adaptive_cfg.get("position_size_multipliers", {})
    pos_mult = float(pos_multipliers.get(f["name"], 1.0))

    sl_mult = float(instrument_profile(f, asset_type).get("sl_atr_mult", 1.3))
    atr_val = max(f["atr"], f["price"] * 0.005)
    if f["ctVal"] > 0 and atr_val > 0:
        raw_dyn_sz = (f["risk_per_trade_usd"] * pos_mult) / (f["ctVal"] * atr_val * sl_mult)
        f["sz"] = quantize_size(raw_dyn_sz, min_sz) if pos_mult > 0 else 0.0
    else:
        f["sz"] = quantize_size(base_sz * pos_mult, min_sz) if pos_mult > 0 else 0.0
    # 基准风险额推导出的数量不足交易所最小下单步长 -> 标记为资金规模不匹配，由上层跳过而非放大成 1 张
    f["size_below_exchange_min"] = bool(f["sz"] <= 0 and pos_mult > 0)

    market_data_valid = (
        len(raw_15m) >= 30
        and len(raw_1h) >= 20
        and len(raw_4h) >= 20
        and f["price"] > 0
        and f["atr"] > 0
        and f.get("bidPx", 0) > 0
        and f.get("askPx", 0) >= f.get("bidPx", 0)
    )
    f["market_data_valid"] = market_data_valid
    if not market_data_valid:
        f["sz"] = 0

    return f
