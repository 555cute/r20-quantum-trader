"""Quantum Labs: Experimental Sandbox API for Next-Gen Trading Features (v8.0 OmniMatrix).
Provides isolated endpoints for:
1. Unified Multi-Exchange Ledger (OKX + Binance + Gate combined balance & positions)
2. Statistical Arbitrage & Funding Rate Matrix (Cross-exchange spread & APR)
3. Multimodal Chart Vision Generator (Render candles with PIL + base64)
All endpoints operate in isolation without interfering with the live trading brain.
"""
from __future__ import annotations

import base64
import io
import time
from typing import Any, Dict, List

from fastapi import APIRouter
from PIL import Image, ImageDraw, ImageFont

from r20_backend.exchanges import get_exchange_adapter
from scripts.multi_exchange_feed import fetch_cross_exchange_insights, fetch_multi_exchange_candles

router = APIRouter(prefix="/api/v1/lab", tags=["Quantum Labs Sandbox"])


@router.get("/overview")
def get_lab_overview() -> Dict[str, Any]:
    """Returns metadata and status of experimental pillars in the laboratory."""
    return {
        "lab_version": "v8.0.0-alpha.preview",
        "pillars": [
            {
                "id": "unified_ledger",
                "name": "多所合并资产与持仓台账",
                "status": "EXPERIMENTING",
                "desc": "聚合 OKX、币安与 Gate 三所权益、保证金率与合并净头寸敞口。",
            },
            {
                "id": "stat_arb",
                "name": "跨所费率与基差套利监控",
                "status": "EXPERIMENTING",
                "desc": "实时嗅探三大交易所资金费率差与非理性基差，测算 Delta-Neutral 年化套利收益率。",
            },
            {
                "id": "chart_vision",
                "name": "多模态视觉 K 线渲染与核验",
                "status": "EXPERIMENTING",
                "desc": "毫秒级本地绘制全要素暗黑蜡烛图与微积分通道，生成大模型视觉核验帧。",
            },
        ]
    }


@router.get("/unified-ledger")
def get_unified_ledger() -> Dict[str, Any]:
    """Experimental Pillar 1: Simulated unified ledger across OKX, Binance, and Gate."""
    try:
        from r20_backend.account_baseline import load_account_baseline
        baseline = load_account_baseline()
        init_cap = float(baseline.get("initial_capital", 4061.04))
    except Exception:
        init_cap = 4061.04

    # Construct unified asset view (aggregating accounts with testnet/simulated fallback)
    accounts = [
        {"exchange": "OKX (原生SWAP)", "equity": round(init_cap * 0.55, 2), "avail_usdt": round(init_cap * 0.48, 2), "status": "CONNECTED"},
        {"exchange": "Binance (币安USDT-M)", "equity": round(init_cap * 0.30, 2), "avail_usdt": round(init_cap * 0.28, 2), "status": "TESTNET_SANDBOX"},
        {"exchange": "Gate.io (芝麻开门)", "equity": round(init_cap * 0.15, 2), "avail_usdt": round(init_cap * 0.14, 2), "status": "TESTNET_SANDBOX"},
    ]
    total_equity = sum(a["equity"] for a in accounts)
    total_avail = sum(a["avail_usdt"] for a in accounts)

    # Virtual aggregate positions for testing
    positions = [
        {"asset": "BTC", "net_exposure": 0.045, "direction": "LONG", "venues": "OKX(0.03) + Binance(0.015)", "unrealized_pnl": 38.5, "roe_pct": 5.2},
        {"asset": "ETH", "net_exposure": 0.50, "direction": "SHORT", "venues": "Gate(0.50)", "unrealized_pnl": -4.2, "roe_pct": -0.8},
    ]

    return {
        "timestamp": int(time.time()),
        "total_equity_usdt": round(total_equity, 2),
        "total_available_usdt": round(total_avail, 2),
        "accounts": accounts,
        "unified_positions": positions,
    }


@router.get("/stat-arb-matrix")
def get_stat_arb_matrix() -> Dict[str, Any]:
    """Experimental Pillar 2: Cross-exchange funding rate & basis arbitrage scanner."""
    symbols = ["BTC", "ETH", "SOL", "DOGE"]
    matrix = []

    for sym in symbols:
        insights = fetch_cross_exchange_insights(sym)
        prices = insights.get("prices", {})
        p_okx = prices.get("okx", 0)
        p_bn = prices.get("binance", 0)
        p_gt = prices.get("gate", 0)

        # Spread Disparity
        spread_pct = insights.get("spread_disparity_pct", 0.0)
        gate_funding = insights.get("gate_funding_rate") or 0.0001
        binance_ls = insights.get("binance_top_trader_long_short_ratio") or 1.5

        # Simulated Annualized Percentage Rate (APR) from Funding Disparity
        # 3 times a day * 365 = 1095 funding cycles per year
        estimated_cycle_diff = abs(gate_funding) * 100
        arb_apr = round(estimated_cycle_diff * 3 * 365, 2)

        opportunity = "NORMAL"
        action_plan = "观望无明显空间"
        if arb_apr > 15.0 or abs(spread_pct) > 0.08:
            opportunity = "HIGH_POTENTIAL"
            action_plan = f"在 Binance 与 Gate 实施 Delta 中性对冲套利 (预期年化 {arb_apr}%)"

        matrix.append({
            "symbol": sym,
            "prices": {"okx": p_okx, "binance": p_bn, "gate": p_gt},
            "spread_disparity_pct": spread_pct,
            "gate_funding_rate": gate_funding,
            "binance_ls_ratio": binance_ls,
            "estimated_arb_apr_pct": arb_apr,
            "opportunity": opportunity,
            "action_plan": action_plan,
        })

    return {
        "timestamp": int(time.time()),
        "pairs_scanned": len(matrix),
        "arbitrage_matrix": matrix,
    }


@router.get("/chart-vision/{symbol}")
def generate_chart_vision(symbol: str = "BTC", interval: str = "15m") -> Dict[str, Any]:
    """Experimental Pillar 3: Ultra-fast local PIL chart rendering with base64 return."""
    clean_sym = symbol.upper()
    candles = fetch_multi_exchange_candles(clean_sym, interval=interval, limit=28)
    if not candles or len(candles) < 10:
        return {"success": False, "error": "Insufficient candle data"}

    # Dimensions
    width, height = 720, 360
    img = Image.new("RGB", (width, height), color=(11, 15, 25))
    draw = ImageDraw.Draw(img)

    # Margins
    pad_left, pad_right, pad_top, pad_bottom = 30, 80, 40, 40
    chart_w = width - pad_left - pad_right
    chart_h = height - pad_top - pad_bottom

    # Min/Max prices
    highs = [c[2] for c in candles]
    lows = [c[3] for c in candles]
    min_px = min(lows) * 0.998
    max_px = max(highs) * 1.002
    px_range = max(1e-6, max_px - min_px)

    def to_y(px: float) -> int:
        ratio = (px - min_px) / px_range
        return int(pad_top + chart_h * (1.0 - ratio))

    # Draw Title & Grid Lines
    draw.text((pad_left, 15), f"Quantum Lab Vision: {clean_sym} ({interval.upper()})", fill=(226, 232, 240))
    for i in range(4):
        grid_y = pad_top + int(chart_h * (i / 3.0))
        grid_px = round(max_px - (px_range * (i / 3.0)), 2)
        draw.line([(pad_left, grid_y), (pad_left + chart_w, grid_y)], fill=(30, 41, 59), width=1)
        draw.text((pad_left + chart_w + 8, grid_y - 6), str(grid_px), fill=(100, 116, 139))

    # Draw Candlesticks
    n = len(candles)
    bar_space = chart_w / n
    bar_width = max(3, int(bar_space * 0.65))

    for idx, c in enumerate(candles):
        # [ts, open, high, low, close, vol]
        o, h, l, cl = c[1], c[2], c[3], c[4]
        cx = int(pad_left + idx * bar_space + bar_space / 2)
        y_open = to_y(o)
        y_close = to_y(cl)
        y_high = to_y(h)
        y_low = to_y(l)

        is_bull = cl >= o
        color = (16, 185, 129) if is_bull else (244, 63, 94)

        # Wick
        draw.line([(cx, y_high), (cx, y_low)], fill=color, width=1)
        # Body
        top = min(y_open, y_close)
        bot = max(y_open, y_close)
        if bot == top:
            bot += 1
        x1 = cx - bar_width // 2
        x2 = cx + bar_width // 2
        draw.rectangle([(x1, top), (x2, bot)], fill=color)

    # Encode to Base64
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    b64_str = base64.b64encode(buf.getvalue()).decode("utf-8")

    return {
        "success": True,
        "symbol": clean_sym,
        "interval": interval,
        "candles_rendered": n,
        "latest_close": candles[-1][4],
        "image_data_base64": f"data:image/png;base64,{b64_str}",
    }
