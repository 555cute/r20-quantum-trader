#!/usr/bin/env python3
"""
R20 High-Alpha Quantitative Multi-Factor Trading Matrix & Execution Engine (R20 Quantum Trader v6.8.1)
Architecture:
1. Multi-Dimensional Quant Factor Sub-Engine:
   - Trend Momentum: EMA Slope (9/21/55), Multi-Timeframe Alignment (15M, 1H, 4H)
   - Volume & Price Dynamics: MACD Histogram Acceleration, OBV Flow Divergence, Volume Expansion Ratio
   - Mean Reversion & Volatility: Multi-Scale VWAP Bias, RSI 14/7 Dynamic Zones, Bollinger Bandwidth & Squeeze
   - Market Microstructure: Dynamic High/Low Dow Theory, Wick Absorption Geometry, Volatility Quantile (ATR%)
2. Continuous Non-Linear Alpha Scoring (-5.0 to +5.0 Score Distribution):
   - Dynamic weight synthesis across Momentum, Volume, Volatility, and Macro Sentiment
3. 6 Institutional Quant Setups:
   - 🌊 Institutional Pullback (顺势机构回踩)
   - 🚀 Momentum Squeeze Breakout (动量挤压突破)
   - 💎 Extreme Mean Reversion (极值均值回归)
   - ⚡ Resistance Exhaustion Short (阻力抛压做空)
   - 🌪️ Breakdown Acceleration Short (破位放量追空)
   - 🛡️ Liquidity Sweep Reversal (流动性猎杀反转)
4. Dynamic Adaptive Position Sizing, Volatility-Trailing Exits & Cooldown Protection.
"""

import os
import sys
from pathlib import Path

_THIS_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _THIS_DIR.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
if str(_THIS_DIR) not in sys.path:
    sys.path.insert(0, str(_THIS_DIR))

try:
    from r20_backend.version import __version__
except Exception:
    __version__ = "7.6.0"

from r20_backend.time_utils import beijing_day

# 必须用 scripts.okx_runtime 包形式：okx_rest 读的是同一模块实例的冻结环境，
# 裸 okx_runtime 是另一份 _FROZEN_ENVIRONMENT 全局，freeze 周期对其无效（US-002 命门）。
from scripts.okx_runtime import (
    current_environment,
    freeze_environment as freeze_okx_environment,
    unfreeze_environment as unfreeze_okx_environment,
    selected_environment,
)
import json
import math
import time
import datetime
import subprocess
import urllib.request
import fcntl
from typing import Tuple, Dict, Any, List, Optional
from concurrent.futures import ThreadPoolExecutor
from market_data_service import fetch_candles, fetch_ticker
import scripts.okx_rest as okx_rest

# 执行层风控参数单一事实源（后台「风控管理页」写入 .env，本进程 import 时读取生效）
from risk_constants import (
    MAX_CONCURRENT_POSITIONS_CAP,
    MAX_MARGIN_EQUITY_RATIO,
    MAX_SCALE_IN_COUNT,
    MAX_SINGLE_ASSET_MARGIN,
    MAX_LEVERAGE,
    MIN_ENTRY_CONFIDENCE,
    MIN_SCALE_IN_CONFIDENCE,
    MIN_SCALE_IN_PROFIT_RATIO,
    DAILY_LOSS_EQUITY_RATIO,
    MAX_DAILY_LOSS_USDT,
    RISK_PER_TRADE_EQUITY_RATIO,
    SINGLE_ASSET_EQUITY_RATIO,
    STOP_COOLDOWN_MINUTES,
    TIME_STOP_ATR_BAND,
    TIME_STOP_HOURS,
    effective_max_positions,
)

WORKSPACE_DIR = str(_PROJECT_ROOT)
DATA_DIR = os.path.join(WORKSPACE_DIR, "data")
LOGS_DIR = os.path.join(WORKSPACE_DIR, "logs")

LEDGER_JSON_FILE = os.path.join(DATA_DIR, "trading_ledger.json")
LOG_FILE = os.path.join(LOGS_DIR, "ai_factor_trader.log")
POSITION_TRACKER_FILE = os.path.join(DATA_DIR, "position_trackers.json")
SIGNAL_JOURNAL_FILE = os.path.join(DATA_DIR, "signal_journal.json")
STOP_COOLDOWN_FILE = os.path.join(DATA_DIR, "stop_cooldown.json")
CIRCUIT_BREAKER_FILE = os.path.join(DATA_DIR, "circuit_breaker.json")
NEWS_SENTIMENT_FILE = os.path.join(DATA_DIR, "news_sentiment.json")
AI_POSITION_MANAGEMENT_FILE = os.path.join(DATA_DIR, "ai_position_management.json")
TRADER_LOCK_FILE = os.path.join(DATA_DIR, ".ai_factor_trader.lock")
TRADER_SLOT_FILE = os.path.join(DATA_DIR, ".ai_factor_trader_slot.json")

try:
    import sys
    sys.path.append(os.path.join(WORKSPACE_DIR, "scripts"))
    from db_manager import record_trade_sqlite
    from qq_notifier import notify_trade_open, notify_trade_close
    from ai_brain_trader import execute_batch_ai_brain_cycle, get_latest_ai_decision
except Exception:
    record_trade_sqlite = None
    notify_trade_open = None
    notify_trade_close = None
    execute_batch_ai_brain_cycle = None
    get_latest_ai_decision = None

from instrument_pool import load_instruments

TARGET_INSTRUMENTS = load_instruments()

ASSET_CLASS_PROFILES = {
    "commodity": {
        "entry_threshold": 2.2,
        "min_profit_ratio": 0.0075,
        "tp_atr_mult": 2.2,
        "sl_atr_mult": 1.3,
        "trailing_kick_in": 1.1,
        "trailing_pullback": 0.45
    },
    "index": {
        "entry_threshold": 2.2,
        "min_profit_ratio": 0.0065,
        "tp_atr_mult": 2.0,
        "sl_atr_mult": 1.2,
        "trailing_kick_in": 1.0,
        "trailing_pullback": 0.40
    },
    "stock": {
        "entry_threshold": 2.2,
        "min_profit_ratio": 0.0090,
        "tp_atr_mult": 2.3,
        "sl_atr_mult": 1.3,
        "trailing_kick_in": 1.2,
        "trailing_pullback": 0.50
    },
    "crypto": {
        "entry_threshold": 2.2,
        "min_profit_ratio": 0.0250,
        "tp_atr_mult": 2.8,
        "sl_atr_mult": 1.4,
        "trailing_kick_in": 2.2,
        "trailing_pullback": 0.80
    }
}

# 并发/同向持仓上限：后台风控管理页可配 (R20_MAX_CONCURRENT_POSITIONS=0 表示自动跟随标的池容量)
MAX_CONCURRENT_POSITIONS, MAX_SAME_DIRECTION_POSITIONS = effective_max_positions(len(TARGET_INSTRUMENTS))
TAKER_FEE_RATE = 0.0005
MAKER_FEE_RATE = 0.0002 # Limit Order Maker Fee (60% Lower Than Market Taker)
# 日亏熔断/单标的保证金/金字塔加仓等阈值均由 risk_constants 单一事实源注入（.env 可配）。


def effective_daily_loss_limit(usdt_available: float = None) -> float:
    """单日亏损熔断线 = min(绝对封顶, 可用余额 5%)，小资金账户自动收紧。"""
    cap = MAX_DAILY_LOSS_USDT
    if usdt_available and usdt_available > 0:
        cap = min(cap, max(round(float(usdt_available) * DAILY_LOSS_EQUITY_RATIO, 2), 1.0))
    return cap


def effective_single_asset_margin(usdt_available: float = None) -> float:
    """单标的累计保证金上限 = min(绝对封顶, 可用余额 30%)，与提示词风险预算同口径。"""
    cap = MAX_SINGLE_ASSET_MARGIN
    if usdt_available and usdt_available > 0:
        cap = min(cap, max(round(float(usdt_available) * SINGLE_ASSET_EQUITY_RATIO, 2), 1.0))
    return cap


# 单笔 1R 风险额与单笔保证金占比 (RISK_PER_TRADE_EQUITY_RATIO / MAX_MARGIN_EQUITY_RATIO)
# 由 risk_constants 单一事实源注入，与提示词 {{risk_budget}} 保持同口径。


def effective_risk_per_trade(pool_risk_usd: float, usdt_available: float = None) -> float:
    """单笔基准风险额 = min(池内配置绝对值, 可用余额 × 2%)，避免 20U 账户被要求押 15U。"""
    cap = float(pool_risk_usd or 0.0)
    if usdt_available and usdt_available > 0:
        cap = min(cap, max(round(float(usdt_available) * RISK_PER_TRADE_EQUITY_RATIO, 4), 0.05))
    return cap


def quantize_size(raw_sz: float, min_sz: float) -> float:
    """按交易所最小下单步长(minSz)向下量化张数。

    关键修正：历史实现把数量强制取整并抬到「至少 1 张」，而 OKX 多数永续的 minSz 实为 0.01 张，
    导致小资金账户仓位被向上放大最多 100 倍(如 BTC 0.01 张=7.92U 名义被抬成 1 张=792U)。
    现在低于最小步长时返回 0.0 由上层跳过该标的，而不是放大成 1 张。
    """
    step = float(min_sz or 0) or 1.0
    try:
        raw = float(raw_sz or 0.0)
    except (TypeError, ValueError):
        return 0.0
    if raw <= 0:
        return 0.0
    return round(math.floor(raw / step + 1e-9) * step, 10)


def max_size_within_margin(usdt_available: float, leverage: float, price: float, ct_val: float, min_sz: float) -> float:
    """可用余额硬顶：单笔保证金不得超过可用余额的 MAX_MARGIN_EQUITY_RATIO，超出部分直接砍掉。"""
    if not usdt_available or usdt_available <= 0 or price <= 0 or ct_val <= 0:
        return float("inf")
    max_margin = float(usdt_available) * MAX_MARGIN_EQUITY_RATIO
    raw = (max_margin * max(1.0, float(leverage or 1.0))) / (float(price) * float(ct_val))
    return quantize_size(raw, min_sz)
# MIN_SCALE_IN_CONFIDENCE (顺势加仓最低 AI 置信度) 由 risk_constants 单一事实源注入

def is_tradfi_market_liquid(asset_type: str) -> bool:
    """Strict US Regular Trading Window (BJ 21:30 ~ 次日 04:00)"""
    if asset_type in ["crypto", "commodity"]:
        return True
    
    tz_bj = datetime.timezone(datetime.timedelta(hours=8))
    now_bj = datetime.datetime.now(tz_bj)
    weekday = now_bj.weekday()
    hour = now_bj.hour
    minute = now_bj.minute

    # Weekend check
    if weekday == 5 and hour >= 5: return False
    if weekday == 6: return False
    if weekday == 0 and (hour < 21 or (hour == 21 and minute < 30)): return False

    # Mon-Fri Core Hours
    if (hour == 21 and minute >= 30) or (hour >= 22) or (hour < 4):
        return True
    return False

# 交易 shell 子进程三件套（结果/字符串/JSON 包装）已随 US-007 全量 REST 迁移删除
# （2026-09-10）：V5 直签唯一通道见 scripts/okx_rest.py；subprocess 仅保留本地
# python 脚本调度用途。禁止复活任何 shell=True 的交易所调用。

def fetch_candles_direct(inst_id: str, bar: str = "15m", limit: int = 45):
    """Direct fetch from OKX Official Market REST API with Keep-Alive connection pooling."""
    return fetch_candles(inst_id, bar=bar, limit=limit)

def load_trackers():
    if os.path.exists(POSITION_TRACKER_FILE):
        try:
            with open(POSITION_TRACKER_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def save_trackers(trackers):
    try:
        with open(POSITION_TRACKER_FILE, "w", encoding="utf-8") as f:
            json.dump(trackers, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

def load_stop_cooldowns():
    if os.path.exists(STOP_COOLDOWN_FILE):
        try:
            with open(STOP_COOLDOWN_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def add_stop_cooldown(inst_id: str, side: str, reason: str = "止损冷却"):
    cooldowns = load_stop_cooldowns()
    key = f"{inst_id}_{side}"
    cooldowns[key] = {
        "instId": inst_id,
        "side": side,
        "ts": int(time.time()),
        "reason": reason
    }
    try:
        with open(STOP_COOLDOWN_FILE, "w", encoding="utf-8") as f:
            json.dump(cooldowns, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

def is_in_stop_cooldown(inst_id: str, side: str) -> bool:
    cooldowns = load_stop_cooldowns()
    key = f"{inst_id}_{side}"
    if key in cooldowns:
        rem_sec = STOP_COOLDOWN_MINUTES * 60 - (int(time.time()) - cooldowns[key].get("ts", 0))
        if rem_sec > 0:
            return True
    return False

def clamp(value, lower, upper, default):
    try:
        return max(lower, min(upper, float(value)))
    except (TypeError, ValueError):
        return default


def load_adaptive_config():
    """Fallback config reader maintaining compatibility."""
    return {}

def clean_stale_open_orders() -> Tuple[bool, str]:
    """Cancel stale entry orders; any inability to verify/cancel blocks the trading cycle."""
    try:
        open_orders = okx_rest.pending_orders()
    except Exception as exc:
        return False, f"invalid open-orders response: {exc}"
    now_ts = int(time.time() * 1000)
    for order in open_orders:
        inst_id = str(order.get("instId") or "")
        order_id = str(order.get("ordId") or "")
        state = str(order.get("state", "live")).lower()
        created_at = int(order.get("cTime", now_ts) or now_ts)
        if state not in {"live", "partially_filled"} or not order_id or now_ts - created_at <= 240000:
            continue
        try:
            okx_rest.cancel_order(inst_id, order_id)
        except Exception as exc:
            return False, f"failed to cancel stale order {inst_id}/{order_id}: {exc}"
        print(f"[挂单生命周期管理] 自动撤销超时挂单: {inst_id} (ordId={order_id}, state={state})")
    return True, "open orders verified"

def check_black_swan_sentinel() -> Tuple[bool, str]:
    """Minute-level Black Swan Sentinel, driven by the unified V5 REST public
    market feed (market_data_service www→aws dual-domain + alt-venue fallback, 零凭证可读).

    US-014 前置收尾（归因：3137c40/09fba6f 将 smartmoney/news CLI 信号面缺失化后，
    新闻模式熔断随之休眠）：黑天鹅熔断改由此公共行情路径**复活**，并按
    「不可判定=不放松」的 fail-closed 语义兜底——行情取不到/样本不足时触发熔断，
    绝不带着盲区继续开新仓。新闻情绪层只消费**可判定**的极端值：旧实现以缺省
    overall_score=50 冒充中性「一切正常」，已删除该假中性值——文件缺失/无该字段
    一律视为不可判定，仅当数值 ≤20 才触发熔断。"""
    # 1. BTC 15M candles extreme plunge (> 3.0% in 15 mins) via unified public REST.
    try:
        candles = fetch_candles_direct("BTC-USDT-SWAP", "15m", 3)
    except Exception as exc:
        return True, f"🚨 黑天鹅熔断：统一行情通道异常 ({type(exc).__name__})，不可判定=不放松，保守暂停新开仓"
    if not candles or len(candles) < 2:
        return True, "🚨 黑天鹅熔断：统一行情通道无有效数据（双域+备源皆断），不可判定=不放松，保守暂停新开仓"
    try:
        latest_c = candles[0]
        c_open = float(latest_c[1])
        c_close = float(latest_c[4])
        c_low = float(latest_c[3])
        drop_pct = (c_close - c_open) / c_open * 100.0
        if drop_pct <= -3.0 or ((c_low - c_open) / c_open * 100.0 <= -4.0):
            return True, f"🚨 监测到 BTC 15M 级别发生断崖式暴跌插针 ({drop_pct:.2f}%)，触发全网黑天鹅紧急熔断！"
    except (ValueError, TypeError, IndexError):
        # 行情形态不可判定同样不得放宽风控
        return True, "🚨 黑天鹅熔断：行情数据格式异常不可判定，不可判定=不放松，保守暂停新开仓"

    # 2. News sentiment file：仅认显式极端值（≤20），缺失≠中性50≠放行。
    if os.path.exists(NEWS_SENTIMENT_FILE):
        try:
            with open(NEWS_SENTIMENT_FILE, "r", encoding="utf-8") as f:
                n_data = json.load(f)
            raw_score = n_data.get("overall_score")
            if raw_score is not None:
                score = float(raw_score)
                if score <= 20.0:
                    return True, f"🚨 监测到突发黑天鹅极度恶性利空舆情 (情绪指数: {score:.1f})，触发全网黑天鹅紧急熔断！"
        except Exception:
            # 情绪文件损坏同样不可判定：不因读不到而放行（与下方熔断状态文件
            # 损坏→安全暂停 的既有语义一致）
            return True, "🚨 黑天鹅熔断：新闻情绪缓存损坏不可判定，不可判定=不放松，保守暂停新开仓"

    return False, ""

def is_circuit_breaker_active(usdt_available: float = None):
    # 1. Black Swan Sentinel Check
    bs_active, bs_reason = check_black_swan_sentinel()
    if bs_active:
        return True, bs_reason

    # 2. File-based Circuit Breaker Check (shared schema with news harvester)
    if os.path.exists(CIRCUIT_BREAKER_FILE):
        try:
            with open(CIRCUIT_BREAKER_FILE, "r", encoding="utf-8") as f:
                cb = json.load(f)
            expires_at = float(cb.get("expires_at_ts", 0) or 0)
            active = bool(cb.get("active")) or cb.get("status") == "triggered"
            if active and (expires_at <= 0 or time.time() < expires_at):
                return True, cb.get("reason") or cb.get("headline") or "黑天鹅极端行情熔断中"
        except Exception as e:
            return True, f"熔断状态文件损坏，安全暂停开仓: {e}"

    # 3. Daily Max Loss Limit Check from lifecycle ledger using Beijing close_time.
    if os.path.exists(LEDGER_JSON_FILE):
        try:
            with open(LEDGER_JSON_FILE, "r", encoding="utf-8") as f:
                ledger = json.load(f)
            tz_bj = datetime.timezone(datetime.timedelta(hours=8))
            today_str = datetime.datetime.now(tz_bj).strftime("%Y-%m-%d")
            today_pnl = sum(
                float(t.get("pnl", 0) or 0)
                for t in ledger
                if t.get("status") == "closed" and beijing_day(t.get("close_time")) == today_str
            )
            _loss_cap = effective_daily_loss_limit(usdt_available)
            if today_pnl < -_loss_cap:
                return True, f"今日累计回撤 ({today_pnl:.2f}U) 触及单日最大风控熔断限额 ({_loss_cap}U｜按可用余额自适应)"
        except Exception as e:
            return True, f"日亏损风控数据读取失败，安全暂停开仓: {e}"

    return False, ""

def query_positions() -> Tuple[bool, List[Dict[str, Any]], str]:
    """Distinguish an exchange-confirmed empty account from a failed query."""
    try:
        rows = okx_rest.positions()
    except Exception as exc:
        return False, [], f"invalid positions response: {exc}"
    return True, rows, ""


def close_position_confirmed(inst_id: str, pos_side: str, before_size: float) -> Tuple[bool, str]:
    """Close a position and verify at the exchange before changing local state."""
    # Pre-cancel any conflicting pending/reduce-only orders for this instrument to release available size
    try:
        for o in okx_rest.pending_orders(inst_id):
            o_side = str(o.get("posSide", "net")).lower()
            if o_side in (pos_side.lower(), "net"):
                o_id = str(o.get("ordId") or "")
                if o_id:
                    okx_rest.cancel_order(inst_id, o_id)
    except Exception as e:
        print(f"[Close Pre-Clean] Warning cancelling pending orders for {inst_id}: {e}")

    try:
        okx_rest.close_position(inst_id, pos_side, td_mode="cross", auto_cxl=True)
    except Exception as exc:
        return False, f"close command failed: {exc}"

    saw_successful_query = False
    for _ in range(6):
        time.sleep(0.6)
        query_ok, positions, query_error = query_positions()
        if not query_ok:
            continue
        saw_successful_query = True
        remaining = 0.0
        for position in positions:
            if position.get("instId") == inst_id and str(position.get("posSide", "net")).lower() == pos_side:
                remaining = abs(float(position.get("pos", 0) or 0))
                break
        if remaining < max(1e-12, abs(before_size) * 0.001):
            return True, "exchange position closed"
    if not saw_successful_query:
        return False, "position verification failed: no successful exchange response"
    return False, f"exchange still reports an open position after close request (before={before_size})"


def prune_trackers(trackers: Dict[str, Any], real_pos_dict: Dict[str, Any]) -> int:
    """Remove stale/non-universe trackers while preserving every live exchange position."""
    valid_keys = {
        f"{inst_id}_{str(position.get('posSide', 'net')).lower()}"
        for inst_id, position in real_pos_dict.items()
        if float(position.get("pos", 0) or 0) > 0
    }
    removed = 0
    for key in list(trackers):
        if key not in valid_keys:
            trackers.pop(key, None)
            removed += 1
    return removed


def submit_protected_limit_order(inst_id: str, side: str, pos_side: str, size: float, price: float, tp_px: float, sl_px: float) -> Tuple[bool, str]:
    """Submit a protected limit order; acceptance is not treated as a fill."""
    # Check if we are running in simulated/demo mode and price diverged significantly from demo orderbook
    env = selected_environment()
    effective_px = price
    effective_tp = tp_px
    effective_sl = sl_px

    if env.simulated:
        try:
            demo_ticker = fetch_ticker(inst_id)
            if demo_ticker and demo_ticker.get("last"):
                demo_last = float(demo_ticker["last"])
                if demo_last > 0 and price > 0:
                    divergence = abs(price - demo_last) / demo_last
                    # If live market price diverged from demo sandbox by more than 5% (e.g. ASTER / illiquid demo pair)
                    if divergence > 0.05:
                        scale = demo_last / price
                        prec = len(str(demo_ticker["last"]).split(".")[1]) if "." in str(demo_ticker["last"]) else 4
                        effective_px = round(price * scale, prec)
                        effective_tp = round(tp_px * scale, prec)
                        effective_sl = round(sl_px * scale, prec)
                        # Re-verify boundary constraints for demo sandbox
                        if pos_side == "long":
                            if effective_sl >= effective_px:
                                effective_sl = round(effective_px * 0.98, prec)
                            if effective_tp <= effective_px:
                                effective_tp = round(effective_px * 1.04, prec)
                        else:
                            if effective_sl <= effective_px:
                                effective_sl = round(effective_px * 1.02, prec)
                            if effective_tp >= effective_px:
                                effective_tp = round(effective_px * 0.96, prec)
        except Exception:
            pass

    # Final Non-Bypassable Verification: verify actual effective price, tp and sl
    from scripts.order_risk import validate_quote_geometry_and_rr
    action_type = "BUY_LONG" if pos_side == "long" else "SELL_SHORT"
    is_valid, reason, _ = validate_quote_geometry_and_rr(action_type, effective_px, effective_tp, effective_sl)
    if not is_valid:
        print(f"[Order Rejected] 最终有效开仓报价未通过核心安全复验: {reason} (px={effective_px}, tp={effective_tp}, sl={effective_sl})")
        return False, f"最终订单核心安全复验拒绝: {reason}"

    try:
        rows = okx_rest.place_order(
            inst_id, side, f"{size:g}",
            pos_side=pos_side, td_mode="cross", ord_type="limit",
            px=effective_px, attach_tp=effective_tp, attach_sl=effective_sl,
        )
    except Exception as exc:
        return False, str(exc)
    order_id = None
    for row in rows:
        order_id = row.get("ordId") or row.get("orderId")
        if order_id:
            break
    if not order_id:
        return False, "exchange accepted response without a verifiable order id"
    return True, str(order_id)


def _float_or_zero(value: Any) -> float:
    try:
        return abs(float(value or 0.0))
    except (TypeError, ValueError):
        return 0.0


def _live_oco_coverage(orders: List[Dict[str, Any]], pos_side: str) -> float:
    """Return contract size covered by live, reduce-only OCO TP/SL orders."""
    coverage = 0.0
    close_side = "sell" if pos_side == "long" else "buy"
    for order in orders:
        if str(order.get("state", "live")).lower() not in {"live", "effective"}:
            continue
        if str(order.get("posSide", "net")).lower() not in {pos_side, "net"}:
            continue
        if str(order.get("side", close_side)).lower() != close_side:
            continue
        if not order.get("tpTriggerPx") or not order.get("slTriggerPx"):
            continue
        reduce_only = str(order.get("reduceOnly", "true")).lower() in {"true", "1", "yes"}
        if not reduce_only:
            continue
        coverage += _float_or_zero(order.get("sz") or order.get("actualSz"))
    return coverage


def ensure_cloud_position_protection(inst_id: str, pos_side: str, size: float, tp_px: float, sl_px: float) -> Tuple[bool, str]:
    """Verify 100% live cloud OCO coverage, repair any gap, and verify again."""
    try:
        algo_rows = okx_rest.pending_algo_orders(inst_id)
    except Exception as exc:
        return False, f"unable to verify cloud OCO: {exc}"
    coverage = _live_oco_coverage(algo_rows, pos_side)
    missing = max(0.0, float(size) - coverage)
    if missing <= max(1e-12, float(size) * 0.001):
        return True, f"cloud OCO coverage verified ({coverage:g}/{size:g})"

    close_side = "sell" if pos_side == "long" else "buy"
    try:
        okx_rest.place_algo_oco(
            inst_id, close_side, missing, pos_side=pos_side, td_mode="cross",
            tp_trigger_px=tp_px, tp_ord_px="-1", sl_trigger_px=sl_px, sl_ord_px="-1",
            reduce_only=True, cxl_on_close_pos=True,
        )
    except Exception as exc:
        return False, f"cloud OCO repair failed: {exc}"

    for _ in range(4):
        time.sleep(0.5)
        try:
            verify_rows = okx_rest.pending_algo_orders(inst_id)
        except Exception:
            continue
        verified_coverage = _live_oco_coverage(verify_rows, pos_side)
        if verified_coverage + max(1e-12, float(size) * 0.001) >= float(size):
            return True, f"cloud OCO repaired and verified ({verified_coverage:g}/{size:g})"
    return False, "cloud OCO repair was submitted but full coverage could not be verified"


def build_signal_snapshot(f: dict) -> dict:
    """抽取开仓时刻的因果动力学与数理快照，供自进化复盘做真实因果归因（而非事后倒推）。

    兼容两套数据源 schema（2026-09-09 修复）：
    1. factor_library 快照块结构（calculus_dynamics / probability_theory / definite_integrals）
    2. 执行层 f["calculus"] = calculate_multi_timeframe 聚合结构
       （velocity / max_abs_jerk / 嵌套 probability_theory / definite_integrals）
    旧版只认结构 1，而开仓路径传入的是结构 2，导致 journal 里 22/24 字段恒为
    None → 复盘全量「数理快照不可观测」、逐单归因失效。
    """
    calc = f.get("calculus_dynamics") or {}
    prob = f.get("probability_theory") or {}
    integ = f.get("definite_integrals") or {}
    multi = f.get("calculus") or {}
    if not calc and multi:
        calc = {
            "velocity": multi.get("velocity"),
            "acceleration": multi.get("acceleration"),
            "jerk": multi.get("max_abs_jerk"),
            "impulse": multi.get("impulse"),
            "curvature": multi.get("curvature"),
            "power": multi.get("power"),
            "power_regime": multi.get("power_regime"),
            "regime": multi.get("regime"),
            "quality": multi.get("quality"),
        }
        prob = multi.get("probability_theory") or {}
        integ = multi.get("definite_integrals") or {}
    micro = f.get("microstructure") or {}
    money = f.get("smart_money_derivatives") or {}
    trend = f.get("trend_momentum") or {}
    snap = {
        "price": f.get("price"),
        "atr": f.get("atr"),
        "velocity": calc.get("velocity"),
        "acceleration": calc.get("acceleration"),
        "jerk": calc.get("jerk"),
        "impulse": calc.get("impulse"),
        "curvature": calc.get("curvature"),
        "power": calc.get("power"),
        "power_regime": calc.get("power_regime"),
        "regime": calc.get("regime"),
        "dynamics_quality": calc.get("quality"),
        "continuation_prob_pct": prob.get("continuation_prob_pct"),
        "breakdown_prob_pct": prob.get("breakdown_prob_pct"),
        "var_95_pct": prob.get("var_95_pct"),
        "cvar_95_pct": prob.get("cvar_95_pct"),
        "prob_regime": prob.get("prob_regime"),
        "is_fat_tail": prob.get("is_fat_tail"),
        "energy_integral": integ.get("energy_integral"),
        "deviation_area_integral": integ.get("deviation_area_integral"),
        "adx": trend.get("adx") or f.get("adx") or f.get("adx_1h"),
        "rsi": trend.get("rsi") or f.get("rsi") or f.get("rsi_14"),
        "funding_rate": micro.get("funding_rate") or f.get("funding_rate"),
        "composite_alpha_score": f.get("composite_alpha_score") or f.get("alpha_score"),
        "smart_money_net": money.get("net_flow") or money.get("taker_net") or money.get("smart_money_flow_usd"),
    }

    # 因子库快照（60s 频，开仓时刻即最新）二次补齐执行层 f 没有的四个外部观测字段
    if any(snap.get(k) is None for k in ("adx", "funding_rate", "composite_alpha_score", "smart_money_net")):
        try:
            lib_file = os.path.join(DATA_DIR, "factor_library_snapshot.json")
            if os.path.exists(lib_file):
                with open(lib_file, "r", encoding="utf-8") as handle:
                    lib = json.load(handle)
                entries = lib.get("instruments") or []
                if isinstance(entries, dict):
                    entries = list(entries.values())
                libf = next(
                    (x for x in entries if isinstance(x, dict) and x.get("instId") == f.get("instId")),
                    None,
                )
                if libf:
                    tm = libf.get("trend_momentum") or {}
                    sm = libf.get("smart_money_derivatives") or {}
                    if snap["adx"] is None:
                        snap["adx"] = tm.get("adx_1h")
                    if snap["funding_rate"] is None:
                        snap["funding_rate"] = sm.get("funding_rate_pct") or tm.get("funding_rate")
                    if snap["composite_alpha_score"] is None:
                        snap["composite_alpha_score"] = libf.get("composite_alpha_score")
                    if snap["smart_money_net"] is None:
                        snap["smart_money_net"] = sm.get("smart_money_flow_usd")
        except Exception:
            pass
    return snap


def record_signal_snapshot(snap: dict) -> None:
    """把开仓时刻的数理快照写入 signal_journal.json，保留最近 500 条供复盘 join。"""
    try:
        journal = []
        if os.path.exists(SIGNAL_JOURNAL_FILE):
            with open(SIGNAL_JOURNAL_FILE, "r", encoding="utf-8") as handle:
                journal = json.load(handle)
        journal.append(snap)
        with open(SIGNAL_JOURNAL_FILE, "w", encoding="utf-8") as handle:
            json.dump(journal[-500:], handle, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Failed to record signal snapshot: {e}")


def record_trade(trade_data):
    if not isinstance(trade_data, dict):
        return
    if "policy_version" not in trade_data:
        try:
            from policy_snapshot import generate_policy_snapshot
            trade_data["policy_version"] = generate_policy_snapshot().get("policy_version", f"v{__version__}@unknown")
        except Exception:
            trade_data["policy_version"] = f"v{__version__}@unknown"
    try:
        ledger = []
        if os.path.exists(LEDGER_JSON_FILE):
            with open(LEDGER_JSON_FILE, "r", encoding="utf-8") as f:
                ledger = json.load(f)
        ledger.append(trade_data)
        with open(LEDGER_JSON_FILE, "w", encoding="utf-8") as f:
            json.dump(ledger, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Failed to record trade to JSON: {e}")

    try:
        if record_trade_sqlite:
            record_trade_sqlite(trade_data)
    except Exception as e:
        print(f"Failed to record trade to SQLite: {e}")

# =============================================================================
# 🧮 Enhanced Quantitative Technical Indicators Math Engine
# =============================================================================
def calc_ema(prices, period):
    if not prices or len(prices) < period:
        return prices[-1] if prices else 0.0
    k = 2.0 / (period + 1)
    ema = prices[0]
    for p in prices[1:]:
        ema = p * k + ema * (1 - k)
    return ema

def calc_rsi(prices, period=14):
    if not prices or len(prices) <= period:
        return 50.0
    gains, losses = [], []
    for i in range(1, len(prices)):
        chg = prices[i] - prices[i-1]
        if chg >= 0:
            gains.append(chg)
            losses.append(0.0)
        else:
            gains.append(0.0)
            losses.append(abs(chg))
    
    if len(gains) < period:
        return 50.0
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))

def calc_atr(candles, period=14):
    if not candles or len(candles) < 2:
        return 0.0
    trs = []
    for i in range(1, len(candles)):
        h = float(candles[i][2])
        l = float(candles[i][3])
        prev_c = float(candles[i-1][4])
        tr = max(h - l, abs(h - prev_c), abs(l - prev_c))
        trs.append(tr)
    if not trs:
        return 0.0
    if len(trs) < period:
        return sum(trs) / len(trs)
    atr = sum(trs[:period]) / period
    for tr in trs[period:]:
        atr = (atr * (period - 1) + tr) / period
    return atr

def calc_macd_histogram_acceleration(prices, fast=12, slow=26, signal=9):
    """Calculates MACD Line, Signal Line, Histogram, and Histogram Delta (Acceleration)"""
    if len(prices) < slow + signal:
        return 0.0, 0.0, 0.0, 0.0
    
    # Calculate fast & slow EMA series
    k_fast = 2.0 / (fast + 1)
    k_slow = 2.0 / (slow + 1)
    k_sig = 2.0 / (signal + 1)

    fast_ema = prices[0]
    slow_ema = prices[0]
    macd_series = []

    for p in prices:
        fast_ema = p * k_fast + fast_ema * (1 - k_fast)
        slow_ema = p * k_slow + slow_ema * (1 - k_slow)
        macd_series.append(fast_ema - slow_ema)

    sig_ema = macd_series[0]
    hist_series = []
    for m in macd_series:
        sig_ema = m * k_sig + sig_ema * (1 - k_sig)
        hist_series.append(m - sig_ema)

    latest_macd = macd_series[-1]
    latest_sig = sig_ema
    latest_hist = hist_series[-1]
    hist_accel = hist_series[-1] - hist_series[-2] if len(hist_series) >= 2 else 0.0

    return latest_macd, latest_sig, latest_hist, hist_accel

def calc_obv_trend(closes, vols, period=14):
    """On-Balance Volume (OBV) and OBV Divergence Slope"""
    if len(closes) < period or len(vols) < period:
        return 0.0, "NEUTRAL"
    
    obv_val = 0.0
    obv_series = [0.0]
    for i in range(1, len(closes)):
        if closes[i] > closes[i-1]:
            obv_val += vols[i]
        elif closes[i] < closes[i-1]:
            obv_val -= vols[i]
        obv_series.append(obv_val)

    # Slope of last 5 bars
    recent_obv = obv_series[-5:]
    recent_px = closes[-5:]
    obv_up = recent_obv[-1] > recent_obv[0]
    px_up = recent_px[-1] > recent_px[0]

    if obv_up and not px_up:
        div_state = "BULL_ACCUMULATION" # 主力隐蔽吸筹
    elif not obv_up and px_up:
        div_state = "BEAR_DISTRIBUTION" # 主力拉高出货背离
    elif obv_up and px_up:
        div_state = "BULL_FLOW"
    else:
        div_state = "BEAR_FLOW"

    return obv_val, div_state

def calc_bollinger_squeeze(closes, period=20, mult=2.0):
    """Bollinger Bandwidth & Squeeze Ratio"""
    if len(closes) < period:
        return 0.0, 0.0, False
    
    sub = closes[-period:]
    sma = sum(sub) / period
    variance = sum((x - sma) ** 2 for x in sub) / period
    std_dev = variance ** 0.5
    upper = sma + mult * std_dev
    lower = sma - mult * std_dev
    bandwidth = ((upper - lower) / sma) * 100.0 if sma > 0 else 0.0
    
    # Squeeze detected if bandwidth is in lowest 20% quantile (< 1.8% for crypto/stock)
    is_squeeze = bandwidth < 1.80
    return bandwidth, std_dev, is_squeeze

# =============================================================================
# 🚀 High-Alpha Multi-Factor Extraction & Quantitative Feature Assembly
# =============================================================================
def fetch_single_instrument_data(item, all_positions, usdt_available):
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
                    "lever": p.get("lever", "3")
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
        except Exception:
            pass

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
        e55_1h = calc_ema(closes_1h, 55)
        
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
    if os.path.exists(NEWS_SENTIMENT_FILE):
        try:
            with open(NEWS_SENTIMENT_FILE, "r", encoding="utf-8") as f_news:
                n_data = json.load(f_news)
                coins_s = n_data.get("coins_sentiment", {})
                if name in coins_s:
                    f["sentiment_score"] = float(coins_s[name].get("sentiment_factor_score", 0.0) or 0.0)
        except Exception:
            pass

    # 5. Causal Multi-Timeframe Calculus Dynamics
    f["calculus"] = {"valid": False, "regime": "RANGE_LOW_VELOCITY", "velocity": 0.0, "acceleration": 0.0, "impulse": 0.0, "max_abs_jerk": 0.0, "quality": 0.0}
    try:
        from calculus_engine import calculate_multi_timeframe
        f["calculus"] = calculate_multi_timeframe({
            "15M": raw_15m,
            "1H": raw_1h,
            "4H": raw_4h
        })
    except Exception:
        pass

    # 6. Dynamic Equal-Risk Position Sizing with AI Self-Evolution Kelly Multipliers
    adaptive_cfg = load_adaptive_config()
    pos_multipliers = adaptive_cfg.get("position_size_multipliers", {})
    pos_mult = float(pos_multipliers.get(f["name"], 1.0))

    sl_mult = ASSET_CLASS_PROFILES.get(asset_type, {}).get("sl_atr_mult", 1.3)
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

# =============================================================================
# Trailing Stop & Risk Management
# =============================================================================
def sync_cloud_algo_stop(inst_id: str, pos_side: str, new_sl: float, reason: str = "") -> bool:
    """Sync ratchet dynamic stop to OKX cloud conditional OCO order.
    
    Ensures that once a position reaches Breakeven (Tier 1) or Profit-Lock (Tier 2),
    the cloud trigger order is immediately amended without waiting for the 15-minute LLM cycle.
    """
    # 修复(2026-09-08)：v7.6 环境重构删除了旧全局 SIMULATED_TRADING，此处残留引用导致
    # NameError，连续 4 个交易周期崩溃(09-08 18:30~19:15 BJ)。DEMO/LIVE 统一尝试云端
    # amend：价格一致时幂等跳过；失败仅返回 False，调用方忽略返回值、由本地棘轮兜底，
    # 与 execute_ai_position_management 内联云端止损上移行为保持一致(演示盘与实盘同构)。
    try:
        algo_orders = okx_rest.pending_algo_orders(inst_id)
        live_algo = next((o for o in algo_orders if o.get("state") == "live" and o.get("posSide") == pos_side and o.get("slTriggerPx")), None)
        if not live_algo:
            return False
        current_cloud_sl = float(live_algo.get("slTriggerPx") or 0.0)
        # Avoid redundant amend if price already matches
        if abs(current_cloud_sl - new_sl) < 1e-6:
            return True
        okx_rest.amend_algo_sl(live_algo["algoId"], new_sl, inst_id=inst_id, new_sl_ord_px="-1")
        return True
    except Exception as e:
        print(f"[Cloud OCO Sync Error] {inst_id} {pos_side}: {e}")
        return False


def manage_position_tp_and_trailing(f, curr_pos, trackers, timestamp_full, executed_actions):
    if not f.get("market_data_valid"):
        executed_actions.append(f"[{f['name']}] 行情数据不完整，保留云端保护并跳过本地移动止盈")
        return False, "行情无效"
    inst_id = f["instId"]
    name = f["name"]
    cur_px = f["price"]
    asset_type = f.get("type", "crypto")
    profile = ASSET_CLASS_PROFILES.get(asset_type, ASSET_CLASS_PROFILES["crypto"])
    atr = max(f["atr"], cur_px * 0.005)
    prec = f["precision"]
    ct_val = f["ctVal"]
    
    pos_sz = float(curr_pos["pos"])
    is_long = "long" in curr_pos["side"].lower()
    entry_px = float(curr_pos["avgPx"])
    pos_key = f"{inst_id}_{curr_pos['side']}"

    now_ts = int(time.time())
    if pos_key not in trackers:
        score, action, reasons, strat_tag, strat_desc = evaluate_asset_signal(f)
        trackers[pos_key] = {
            "instId": inst_id,
            "name": name,
            "side": curr_pos["side"],
            "policy_version": f.get("policy_version", ""),
            "policy_hash": f.get("policy_hash", ""),
            "strategy_tag": strat_tag if strat_tag != "⚪ 观望" else ("🌊 顺势回踩" if is_long else "⚡ 阻力抛压"),
            "entryPx": entry_px,
            "entryTs": now_ts,
            "entryTime": timestamp_full,
            "initialSz": pos_sz,
            "currentSz": pos_sz,
            "highWaterMark": cur_px,
            "lowWaterMark": cur_px,
            "trailingStopPx": round((entry_px - atr * profile["sl_atr_mult"]) if is_long else (entry_px + atr * profile["sl_atr_mult"]), prec),
            "takeProfitPx": round((entry_px + max(atr * profile["tp_atr_mult"], entry_px * profile["min_profit_ratio"])) if is_long else (entry_px - max(atr * profile["tp_atr_mult"], entry_px * profile["min_profit_ratio"])), prec),
            "signal_snapshot": build_signal_snapshot(f),
            "stage_desc": "持有监控中"
        }
        record_signal_snapshot({
            "instId": inst_id,
            "name": name,
            "side": curr_pos["side"],
            "entryTs": now_ts,
            "entryTime": timestamp_full,
            "entryPx": entry_px,
            "sz": pos_sz,
            "policy_version": f.get("policy_version", ""),
            "snapshot": trackers[pos_key]["signal_snapshot"],
        })

    t = trackers[pos_key]
    if not t.get("policy_version") and f.get("policy_version"):
        t["policy_version"] = f.get("policy_version")
        t["policy_hash"] = f.get("policy_hash", "")
    t["currentSz"] = pos_sz
    if "entryTs" not in t:
        t["entryTs"] = now_ts

    # Peak Profit Tracking
    if is_long:
        t["highWaterMark"] = max(t.get("highWaterMark", cur_px), cur_px)
        cur_profit_px = cur_px - entry_px
        peak_profit_px = t["highWaterMark"] - entry_px
    else:
        t["lowWaterMark"] = min(t.get("lowWaterMark", cur_px), cur_px)
        cur_profit_px = entry_px - cur_px
        peak_profit_px = entry_px - t["lowWaterMark"]

    # 1. Hard Stop Loss (loss protection is independent of profit-lock activation).
    # The tracker stop is the exchange-protection source of truth; if a legacy or
    # partially migrated position has no live cloud OCO, the local 15-minute
    # fail-safe still closes it once the stop is breached.
    hard_stop_px = float(t.get("trailingStopPx", 0.0) or 0.0)
    hard_stop_hit = hard_stop_px > 0 and ((is_long and cur_px <= hard_stop_px) or (not is_long and cur_px >= hard_stop_px))
    if hard_stop_hit:
        closed, close_detail = close_position_confirmed(inst_id, "long" if is_long else "short", pos_sz)
        if not closed:
            executed_actions.append(f"[{name}] 硬止损平仓失败，仓位仍保留: {close_detail}")
            return False, "硬止损平仓失败"
        close_fee = (pos_sz * ct_val * cur_px) * TAKER_FEE_RATE
        pnl_val = curr_pos["upl"]
        executed_actions.append(f"[{name}] 🛑 触发硬止损 {hard_stop_px} 并确认平仓 (净盈亏: {pnl_val:+.2f}U)")
        record_trade({
            "is_trade": True,
            "time": timestamp_full,
            "inst": name,
            "name": name,
            "action": "平仓",
            "action_type": "硬止损",
            "direction": f"平{'多' if is_long else '空'}",
            "side": f"{'多' if is_long else '空'}单硬止损",
            "size": pos_sz,
            "sz": pos_sz,
            "price": cur_px,
            "fee": close_fee,
            "pnl": pnl_val,
            "remark": f"价格 {cur_px} 触及保护止损 {hard_stop_px}，交易所确认平仓"
        })
        add_stop_cooldown(inst_id, "long" if is_long else "short", "硬止损")
        if notify_trade_close:
            notify_trade_close(inst=name, pnl=pnl_val, stage="硬止损平仓", exit_px=cur_px)
        trackers.pop(pos_key, None)
        return True, "已硬止损"

    default_tp_dist = max(atr * profile["tp_atr_mult"], entry_px * profile["min_profit_ratio"])
    if not _float_or_zero(t.get("takeProfitPx")):
        t["takeProfitPx"] = round(entry_px + default_tp_dist if is_long else entry_px - default_tp_dist, prec)
    protected, protection_detail = ensure_cloud_position_protection(
        inst_id, "long" if is_long else "short", pos_sz, float(t["takeProfitPx"]), hard_stop_px
    )
    if not protected:
        closed, close_detail = close_position_confirmed(inst_id, "long" if is_long else "short", pos_sz)
        if not closed:
            executed_actions.append(f"[{name}] 🚨 云端 OCO 缺失且安全退出失败: {protection_detail}; {close_detail}")
            return False, "保护与退出均失败"
        pnl_val = curr_pos["upl"]
        executed_actions.append(f"[{name}] 🧯 云端 OCO 无法确认，已安全平仓: {protection_detail}")
        record_trade({
            "is_trade": True, "time": timestamp_full, "inst": name, "name": name,
            "action": "平仓", "action_type": "保护失效退出",
            "direction": f"平{'多' if is_long else '空'}", "side": f"{'多' if is_long else '空'}单保护失效退出",
            "size": pos_sz, "sz": pos_sz, "price": cur_px,
            "fee": (pos_sz * ct_val * cur_px) * TAKER_FEE_RATE, "pnl": pnl_val,
            "remark": f"云端 OCO 无法达到全仓覆盖，交易所确认安全平仓：{protection_detail}"
        })
        add_stop_cooldown(inst_id, "long" if is_long else "short", "云端保护失效")
        if notify_trade_close:
            notify_trade_close(inst=name, pnl=pnl_val, stage="云端保护失效退出", exit_px=cur_px)
        trackers.pop(pos_key, None)
        return True, "保护失效安全退出"
    t["cloudProtection"] = {"verifiedAt": timestamp_full, "detail": protection_detail}

    # 2. Volatility Time-Stop Exit (持仓超最长持仓时间且缩量横盘 → 时间止损，参数见后台风控管理页)
    hold_duration_sec = now_ts - t["entryTs"]
    if hold_duration_sec > TIME_STOP_HOURS * 3600 and abs(cur_profit_px) < TIME_STOP_ATR_BAND * atr:
        closed, close_detail = close_position_confirmed(inst_id, "long" if is_long else "short", pos_sz)
        if not closed:
            executed_actions.append(f"[{name}] 时间止损平仓失败，仓位仍保留: {close_detail}")
            return False, "平仓失败"
        close_fee = (pos_sz * ct_val * cur_px) * TAKER_FEE_RATE
        executed_actions.append(f"[{name}] ⌛ 超过 {TIME_STOP_HOURS:g} 小时无波动横盘，时间止损平仓释放保证金")
        record_trade({
            "is_trade": True,
            "time": timestamp_full,
            "inst": name,
            "name": name,
            "action": "平仓",
            "action_type": "时间止损",
            "direction": f"平{'多' if is_long else '空'}",
            "side": f"{'多' if is_long else '空'}单无波动出场",
            "size": pos_sz,
            "sz": pos_sz,
            "price": cur_px,
            "fee": close_fee,
            "pnl": curr_pos["upl"],
            "remark": f"持仓超 {TIME_STOP_HOURS:g} 小时无突破，主动平仓释放配比"
        })
        if notify_trade_close:
            notify_trade_close(inst=name, pnl=float(curr_pos.get("upl", 0.0) or 0.0), stage="时间止损平仓", exit_px=cur_px)
        if pos_key in trackers: del trackers[pos_key]
        return True, "时间止损"

    # 3. Three-Tier Ratchet Profit-Locking & Momentum Take-Profit Engine
    # Tier 1: Breakeven Lock at +1.5x ATR (~1.0R profit, covers taker fee + 0.20% cushion)
    # Tier 2: Solid Wave Profit Lock at +2.2x ATR (~1.6R profit, lock in at least +1.0x ATR profit)
    # Tier 3: Kinetic Momentum Pullback Exit (Symmetric >= 2.0x ATR peak profit with 0.75x ATR pullback)
    
    tier1_breakeven_trigger = 1.5 * atr
    tier2_lock_trigger = 2.2 * atr
    momentum_tp_trigger = 2.0 * atr
    momentum_pullback_buffer = 0.75 * atr
    
    if is_long:
        # Dynamic Ratchet Stop Calculation for Long
        old_sl = float(t.get("trailingStopPx", 0.0) or 0.0)
        dynamic_floor_sl = old_sl
        if peak_profit_px >= tier2_lock_trigger:
            dynamic_floor_sl = max(dynamic_floor_sl, round(entry_px + 1.0 * atr, prec))
            t["stage_desc"] = f"锁定大波段利润 (保底止损 {dynamic_floor_sl})"
        elif peak_profit_px >= tier1_breakeven_trigger:
            dynamic_floor_sl = max(dynamic_floor_sl, round(entry_px + 0.0020 * entry_px, prec))
            t["stage_desc"] = f"已推保本无风险 (保底止损 {dynamic_floor_sl})"
        
        # If dynamic floor stop ratcheted up, commit and sync to cloud OCO
        if dynamic_floor_sl > old_sl and old_sl > 0:
            t["trailingStopPx"] = dynamic_floor_sl
            sync_cloud_algo_stop(inst_id, "long", dynamic_floor_sl, reason=t["stage_desc"])
        else:
            t["trailingStopPx"] = dynamic_floor_sl

        # A. Hit Ratchet Floor Stop (Locked Profit Trigger)
        if cur_px <= dynamic_floor_sl and peak_profit_px >= tier1_breakeven_trigger:
            closed, close_detail = close_position_confirmed(inst_id, "long", pos_sz)
            if not closed:
                executed_actions.append(f"[{name}] 锁利平多失败，仓位仍保留: {close_detail}")
                return False, "平仓失败"
            close_fee = (pos_sz * ct_val * cur_px) * TAKER_FEE_RATE
            pnl_val = curr_pos["upl"]
            executed_actions.append(f"[{name}] 🛡️ 触发阶梯动态锁利平仓 (净盈亏: {pnl_val:+.2f}U)")
            record_trade({
                "is_trade": True,
                "time": timestamp_full,
                "inst": name,
                "name": name,
                "action": "平仓",
                "action_type": "阶梯锁利",
                "direction": "平多",
                "side": "多单阶梯锁利平仓",
                "size": pos_sz,
                "sz": pos_sz,
                "price": cur_px,
                "fee": close_fee,
                "pnl": pnl_val,
                "remark": f"最高 {t['highWaterMark']} 触发阶梯利润锁定线 {dynamic_floor_sl}"
            })
            if notify_trade_close:
                notify_trade_close(inst=name, pnl=pnl_val, stage="阶梯锁利平仓", exit_px=cur_px)
            if pos_key in trackers: del trackers[pos_key]
            return True, "已阶梯锁利"

        # B. Kinetic Momentum Pullback Exit from Peak (Symmetric 2.0x ATR profit with 0.75x ATR pullback)
        if peak_profit_px >= momentum_tp_trigger and cur_px <= (t["highWaterMark"] - momentum_pullback_buffer):
            closed, close_detail = close_position_confirmed(inst_id, "long", pos_sz)
            if not closed:
                executed_actions.append(f"[{name}] 动能见顶移动止盈失败，仓位仍保留: {close_detail}")
                return False, "平仓失败"
            close_fee = (pos_sz * ct_val * cur_px) * TAKER_FEE_RATE
            pnl_val = curr_pos["upl"]
            executed_actions.append(f"[{name}] 🎯 触发高点回撤动能止盈 (净盈亏: {pnl_val:+.2f}U)")
            record_trade({
                "is_trade": True,
                "time": timestamp_full,
                "inst": name,
                "name": name,
                "action": "平仓",
                "action_type": "移动止盈",
                "direction": "平多",
                "side": "多单高点回撤止盈",
                "size": pos_sz,
                "sz": pos_sz,
                "price": cur_px,
                "fee": close_fee,
                "pnl": pnl_val,
                "remark": f"最高 {t['highWaterMark']} 动能回撤触及移动止盈线"
            })
            if notify_trade_close:
                notify_trade_close(inst=name, pnl=pnl_val, stage="移动止盈", exit_px=cur_px)
            if pos_key in trackers: del trackers[pos_key]
            return True, "已移动止盈"

    else:
        # Dynamic Ratchet Stop Calculation for Short
        old_sl = float(t.get("trailingStopPx", 0.0) or 0.0)
        dynamic_floor_sl = old_sl
        if peak_profit_px >= tier2_lock_trigger:
            dynamic_floor_sl = min(dynamic_floor_sl, round(entry_px - 1.0 * atr, prec))
            t["stage_desc"] = f"锁定大波段利润 (保底止损 {dynamic_floor_sl})"
        elif peak_profit_px >= tier1_breakeven_trigger:
            dynamic_floor_sl = min(dynamic_floor_sl, round(entry_px - 0.0020 * entry_px, prec))
            t["stage_desc"] = f"已推保本无风险 (保底止损 {dynamic_floor_sl})"
        
        # If dynamic floor stop ratcheted down (tightened for short), commit and sync to cloud OCO
        if dynamic_floor_sl < old_sl and old_sl > 0:
            t["trailingStopPx"] = dynamic_floor_sl
            sync_cloud_algo_stop(inst_id, "short", dynamic_floor_sl, reason=t["stage_desc"])
        else:
            t["trailingStopPx"] = dynamic_floor_sl

        # A. Hit Ratchet Floor Stop (Locked Profit Trigger)
        if cur_px >= dynamic_floor_sl and peak_profit_px >= tier1_breakeven_trigger:
            closed, close_detail = close_position_confirmed(inst_id, "short", pos_sz)
            if not closed:
                executed_actions.append(f"[{name}] 锁利平空失败，仓位仍保留: {close_detail}")
                return False, "平仓失败"
            close_fee = (pos_sz * ct_val * cur_px) * TAKER_FEE_RATE
            pnl_val = curr_pos["upl"]
            executed_actions.append(f"[{name}] 🛡️ 触发阶梯动态锁利平仓 (净盈亏: {pnl_val:+.2f}U)")
            record_trade({
                "is_trade": True,
                "time": timestamp_full,
                "inst": name,
                "name": name,
                "action": "平仓",
                "action_type": "阶梯锁利",
                "direction": "平空",
                "side": "空单阶梯锁利平仓",
                "size": pos_sz,
                "sz": pos_sz,
                "price": cur_px,
                "fee": close_fee,
                "pnl": pnl_val,
                "remark": f"最低 {t['lowWaterMark']} 触发阶梯利润锁定线 {dynamic_floor_sl}"
            })
            if notify_trade_close:
                notify_trade_close(inst=name, pnl=pnl_val, stage="阶梯锁利平仓", exit_px=cur_px)
            if pos_key in trackers: del trackers[pos_key]
            return True, "已阶梯锁利"

        # B. Kinetic Momentum Pullback Exit from Peak (Symmetric 2.0x ATR profit with 0.75x ATR pullback)
        if peak_profit_px >= momentum_tp_trigger and cur_px >= (t["lowWaterMark"] + momentum_pullback_buffer):
            closed, close_detail = close_position_confirmed(inst_id, "short", pos_sz)
            if not closed:
                executed_actions.append(f"[{name}] 动能见底移动止盈失败，仓位仍保留: {close_detail}")
                return False, "平仓失败"
            close_fee = (pos_sz * ct_val * cur_px) * TAKER_FEE_RATE
            pnl_val = curr_pos["upl"]
            executed_actions.append(f"[{name}] 🎯 触发低点反弹动能止盈 (净盈亏: {pnl_val:+.2f}U)")
            record_trade({
                "is_trade": True,
                "time": timestamp_full,
                "inst": name,
                "name": name,
                "action": "平仓",
                "action_type": "移动止盈",
                "direction": "平空",
                "side": "空单低点反弹止盈",
                "size": pos_sz,
                "sz": pos_sz,
                "price": cur_px,
                "fee": close_fee,
                "pnl": pnl_val,
                "remark": f"最低 {t['lowWaterMark']} 动能反弹触及移动止盈线"
            })
            if notify_trade_close:
                notify_trade_close(inst=name, pnl=pnl_val, stage="移动止盈", exit_px=cur_px)
            if pos_key in trackers: del trackers[pos_key]
            return True, "已移动止盈"

    return False, "持仓监控中"

def execute_ai_position_management(real_pos_dict, trackers, timestamp_full, executed_actions):
    """Execute only fresh, high-confidence and risk-reducing AI position instructions."""
    if not os.path.exists(AI_POSITION_MANAGEMENT_FILE):
        return
    try:
        with open(AI_POSITION_MANAGEMENT_FILE, "r", encoding="utf-8") as f:
            payload = json.load(f)
        if int(time.time()) - int(payload.get("timestamp", 0) or 0) > 300:
            executed_actions.append("AI持仓指令已过期，未执行")
            return
    except Exception as e:
        executed_actions.append(f"AI持仓指令读取失败: {e}")
        return

    for instruction in payload.get("instructions", []):
        inst_id = str(instruction.get("instId", ""))
        action = str(instruction.get("action", "HOLD")).upper()
        confidence = float(instruction.get("confidence", 0) or 0)
        reason = str(instruction.get("reason", "AI持仓管理"))[:120]
        position = real_pos_dict.get(inst_id)
        if not position or action == "HOLD":
            continue

        pos_side = str(position.get("posSide", "net")).lower()
        current_px = float(position.get("markPx", position.get("last", 0)) or 0)
        avg_px = float(position.get("avgPx", 0) or 0)
        name = inst_id.replace("-USDT-SWAP", "")

        if action == "CLOSE_MARKET":
            if confidence < 85:
                executed_actions.append(f"[{name}] AI平仓置信度{confidence:.0f}<85，拒绝执行")
                continue
            closed, close_detail = close_position_confirmed(inst_id, pos_side, float(position.get("pos", 0) or 0))
            if closed:
                executed_actions.append(f"[{name}] AI高置信度整仓退出: {reason}")
                trackers.pop(f"{inst_id}_{pos_side}", None)
            else:
                executed_actions.append(f"[{name}] AI平仓请求未获交易所确认，仓位保持不变: {close_detail}")

        elif action == "UPDATE_SL":
            new_sl = float(instruction.get("suggested_sl_price", 0) or 0)
            atr_val = max(float(position.get("atr_1h", 0) or 0), float(position.get("atr", 0) or 0), current_px * 0.012)
            
            # Anti-premature trailing fix:
            # 1. Do NOT move SL up until price is at least +1.2x ATR above entry (meaningful profit)
            # 2. Maintain at least 0.8x ATR breathing buffer between current price and new SL to prevent tagging by noise
            if pos_side == "long":
                min_profit_reached = (current_px - avg_px) >= 1.2 * atr_val
                safe_buffer_from_current = (current_px - new_sl) >= 0.7 * atr_val
                tightens_risk = new_sl > 0 and avg_px <= new_sl < current_px and min_profit_reached and safe_buffer_from_current
            elif pos_side == "short":
                min_profit_reached = (avg_px - current_px) >= 1.2 * atr_val
                safe_buffer_from_current = (new_sl - current_px) >= 0.7 * atr_val
                tightens_risk = new_sl > 0 and current_px < new_sl <= avg_px and min_profit_reached and safe_buffer_from_current
            else:
                tightens_risk = False

            if not tightens_risk:
                executed_actions.append(f"[{name}] 浮盈空间不足或与现价缓冲过近({current_px} vs 拟调SL {new_sl})，拒绝过早收紧止损")
                continue
            try:
                algo_orders = okx_rest.pending_algo_orders(inst_id)
            except Exception as exc:
                executed_actions.append(f"[{name}] 云端止损收紧失败，原保护单保持不变（查询异常：{exc}）")
                continue
            live_algo = next((o for o in algo_orders if o.get("state") == "live" and o.get("posSide") == pos_side and o.get("slTriggerPx")), None)
            if not live_algo:
                executed_actions.append(f"[{name}] 未找到真实云端止损单，无法更新")
                continue
            try:
                okx_rest.amend_algo_sl(live_algo["algoId"], new_sl, inst_id=inst_id, new_sl_ord_px="-1")
                amend_ok = True
            except Exception:
                amend_ok = False
            if amend_ok:
                executed_actions.append(f"[{name}] 云端止损收紧至 {new_sl}: {reason}")
                tracker = trackers.get(f"{inst_id}_{pos_side}")
                if tracker:
                    tracker["trailingStopPx"] = new_sl
                try:
                    from qq_notifier import notify_sl_updated
                    notify_sl_updated(name, pos_side, float(live_algo.get("slTriggerPx", 0)), new_sl, reason)
                except Exception:
                    pass
            else:
                executed_actions.append(f"[{name}] 云端止损更新失败，原保护单保持不变")

# =============================================================================
# 🧠 R20 Quantum Trader v6.8.1 Multi-Factor Scoring & Strategy Setup Classifier
# =============================================================================
def evaluate_asset_signal(f):
    """
    Continuous Multi-Factor Quantitative Scoring Engine (-5.0 ~ +5.0).
    Uses trend, volume, mean-reversion and sentiment sub-scores.
    """
    if not f.get("market_data_valid"):
        return 0.0, "HOLD", ["关键行情数据缺失"], "⚪ 观望", "行情数据不完整，禁止生成交易信号"
    inst_id = f["instId"]
    inst_name = f["name"]
    asset_type = f.get("type", "crypto")
    profile = ASSET_CLASS_PROFILES.get(asset_type, ASSET_CLASS_PROFILES["crypto"])
    
    # 1. Hot-reload AI Evolution Config
    adaptive_cfg = load_adaptive_config()
    cooldown_assets = adaptive_cfg.get("cooldown_assets", [])
    strat_weights = adaptive_cfg.get("strategy_weights", {})
    strat_enabled = adaptive_cfg.get("strategy_enabled", {})
    entry_threshold = float(adaptive_cfg.get("entry_threshold", profile.get("entry_threshold", 2.2)))

    # Intervene 1: Cooldown Blacklist
    if inst_name in cooldown_assets or inst_id in cooldown_assets:
        return 0.0, "HOLD", ["⛔ 标的处于AI避险冷却池中，自进化系统禁止开仓"], "⚪ 避险冷却", f"【自进化干预】{inst_name} 胜率不足或连续止损，已被自动关入冷却池避险"

    px = f["price"]
    ema9 = f.get("ema9", px)
    ema21 = f.get("ema21", px)
    ema55 = f.get("ema55", px)
    e21_slope = f.get("ema21_slope_pct", 0.0)
    rsi = f.get("rsi", 50.0)
    rsi_7 = f.get("rsi_7", 50.0)
    vwap_bias = f.get("vwap_bias", 0.0)
    macd_hist = f.get("macd_hist", 0.0)
    macd_accel = f.get("macd_accel", 0.0)
    obv_flow = f.get("obv_flow", "NEUTRAL")
    bb_squeeze = f.get("bb_squeeze", False)
    vol_ratio = f.get("vol_ratio", 1.0)
    regime = f.get("market_regime", "CHOP")
    struct_1h = f.get("structure_1h", "CHOP")

    is_bull_c = f.get("is_bull_candle_15m", False)
    is_bear_c = f.get("is_bear_candle_15m", False)
    lower_wick = f.get("lower_wick_ratio", 0.0)
    upper_wick = f.get("upper_wick_ratio", 0.0)

    cooldown_long = is_in_stop_cooldown(inst_id, "long")
    cooldown_short = is_in_stop_cooldown(inst_id, "short")

    # -------------------------------------------------------------------------
    # 📊 Sub-Factor 1: Trend & Slope Momentum (-1.5 ~ +1.5)
    # -------------------------------------------------------------------------
    score_trend = 0.0
    if regime == "BULL_TREND" and e21_slope > 0.02:
        score_trend = 1.2 + (0.3 if struct_1h == "HH_HL" else 0.0)
    elif regime == "BEAR_TREND" and e21_slope < -0.02:
        score_trend = -1.2 - (0.3 if struct_1h == "LH_LL" else 0.0)
    elif ema9 > ema21 > ema55:
        score_trend = 0.6
    elif ema9 < ema21 < ema55:
        score_trend = -0.6

    # -------------------------------------------------------------------------
    # 📊 Sub-Factor 2: Volume & MACD Acceleration (-1.5 ~ +1.5)
    # -------------------------------------------------------------------------
    score_vol = 0.0
    if macd_accel > 0 and macd_hist > 0:
        score_vol += 0.6
    elif macd_accel < 0 and macd_hist < 0:
        score_vol -= 0.6

    if obv_flow in ["BULL_FLOW", "BULL_ACCUMULATION"]:
        score_vol += 0.5
    elif obv_flow in ["BEAR_FLOW", "BEAR_DISTRIBUTION"]:
        score_vol -= 0.5

    if vol_ratio >= 1.25 and is_bull_c:
        score_vol += 0.4
    elif vol_ratio >= 1.25 and is_bear_c:
        score_vol -= 0.4

    # -------------------------------------------------------------------------
    # 📊 Sub-Factor 3: Mean Reversion & RSI Extremes (-1.2 ~ +1.2)
    # -------------------------------------------------------------------------
    score_mr = 0.0
    if vwap_bias <= -0.75 and rsi <= 35.0:
        score_mr = 1.2 # 超跌反弹多
    elif vwap_bias >= 0.75 and rsi >= 65.0:
        score_mr = -1.2 # 超买冲高空
    elif 40.0 <= rsi <= 55.0 and regime == "BULL_TREND":
        score_mr = 0.5 # 顺势健康区间
    elif 45.0 <= rsi <= 60.0 and regime == "BEAR_TREND":
        score_mr = -0.5 # 顺势空头区间

    # -------------------------------------------------------------------------
    # 📊 Sub-Factor 4: News & Sentiment (-0.8 ~ +0.8)
    # -------------------------------------------------------------------------
    sent_score = f.get("sentiment_score", 0.0)
    score_sent = max(-0.8, min(0.8, sent_score * 1.5))

    # -------------------------------------------------------------------------
    # 📊 Sub-Factor 5: Causal Calculus, Definite Integrals & Probability (-1.5 ~ +1.5)
    # -------------------------------------------------------------------------
    score_calc = 0.0
    c_dyn = f.get("calculus", {})
    c_v = float(c_dyn.get("velocity", 0.0) or 0.0)
    c_a = float(c_dyn.get("acceleration", 0.0) or 0.0)
    c_i = float(c_dyn.get("impulse", 0.0) or 0.0)
    c_j = abs(float(c_dyn.get("max_abs_jerk", 0.0) or 0.0))
    c_regime = c_dyn.get("regime", "")

    # 1. Calculus Dynamics
    if c_regime == "BULL_ACCELERATING" or (c_v > 0.2 and c_a > 0.1 and c_i > 0):
        score_calc += 0.6
    elif c_regime == "BULL_DECELERATING" or (c_v > 0.2 and c_a < -0.3):
        score_calc -= 0.5 # Anti-FOMO deceleration penalty
    elif c_regime == "BEAR_ACCELERATING" or (c_v < -0.2 and c_a < -0.1 and c_i < 0):
        score_calc -= 0.6
    elif c_regime == "BEAR_DECELERATING" or (c_v < -0.2 and c_a > 0.3):
        score_calc += 0.5 # Anti-bottom chasing penalty

    # 2. Definite Integrals (Energy & Area Accumulation)
    d_int = c_dyn.get("definite_integrals", {})
    e_int = float(d_int.get("energy_integral", 0.0) or 0.0)
    dev_area = float(d_int.get("deviation_area_integral", 0.0) or 0.0)
    if e_int > 1.0 and dev_area > 0.6:
        score_calc += 0.4 # Net positive kinetic work done
    elif e_int < -1.0 and dev_area < -0.6:
        score_calc -= 0.4 # Net negative depletion

    # 3. Probability Theory & Stochastic Risk
    p_th = c_dyn.get("probability_theory", {})
    p_cont = float(p_th.get("continuation_prob_pct", 50.0) or 50.0)
    p_break = float(p_th.get("breakdown_prob_pct", 50.0) or 50.0)
    if p_cont >= 70.0:
        score_calc += 0.4
    elif p_break >= 70.0:
        score_calc -= 0.4

    score_calc = max(-1.5, min(1.5, score_calc))

    # -------------------------------------------------------------------------
    # 🎯 Continuous Synthesis Multi-Factor Alpha Score
    # -------------------------------------------------------------------------
    raw_alpha_score = round(score_trend + score_vol + score_mr + score_sent + score_calc, 2)
    
    # -------------------------------------------------------------------------
    # 🏆 6 Institutional Quant Setups Recognition
    # -------------------------------------------------------------------------
    strategy_tag = "⚪ 观望"
    strategy_desc = "因子分布中性，无高置信度共振信号"
    reasons = []

    # High Jerk Shock Filter: Shock market dampens high-risk breakout setups
    is_high_jerk_shock = (c_j >= 1.8 or c_regime == "SHOCK_HIGH_JERK")

    # Setup 1: 🌊 顺势机构回踩 (Institutional Pullback)
    if regime == "BULL_TREND" and (px <= ema21 * 1.008 and px >= ema55 * 0.994) and (38.0 <= rsi <= 56.0) and (is_bull_c or lower_wick >= 0.20) and not cooldown_long and not is_high_jerk_shock:
        strategy_tag = "🌊 顺势回踩"
        raw_alpha_score = max(raw_alpha_score, 2.4)
        strategy_desc = f"【1H机构顺势】回踩EMA21/55价值中枢止跌收阳(RSI={rsi:.1f}, 微积分速度={c_v:+.2f})，顺势低吸做多"
        reasons = ["1H单边主升结构", "EMA价值区放量承接", "微积分动能企稳"]

    # Setup 2: ⚡ 阻力抛压做空 (Resistance Exhaustion)
    elif regime == "BEAR_TREND" and (px >= ema21 * 0.992 and px <= ema55 * 1.006) and (44.0 <= rsi <= 62.0) and (is_bear_c or upper_wick >= 0.20) and not cooldown_short and not is_high_jerk_shock:
        strategy_tag = "⚡ 阻力抛压"
        raw_alpha_score = min(raw_alpha_score, -2.4)
        strategy_desc = f"【1H机构顺势】反弹测试EMA21/55阻力带右侧收阴遇阻(RSI={rsi:.1f}, 微积分速度={c_v:+.2f})，顺势做空"
        reasons = ["1H单边主跌结构", "EMA阻力带量能衰竭遇阻", "微积分动能向下发散"]

    # Setup 3: 🚀 动量挤压突破 (Momentum Squeeze Breakout)
    elif (px > ema9) and (55.0 <= rsi <= 74.0) and vol_ratio >= 1.3 and macd_accel > 0 and is_bull_c and not cooldown_long and (c_a >= -0.2) and not is_high_jerk_shock:
        strategy_tag = "🚀 动量突破"
        raw_alpha_score = max(raw_alpha_score, 2.5)
        strategy_desc = f"【动量爆发】放量突破前高动能发散(量能={vol_ratio}x, 微积分加速度={c_a:+.2f})，顺势追涨"
        reasons = ["动量主升放量突破", f"成交量放大 {vol_ratio} 倍", "微积分正加速度扩张"]

    # Setup 4: 🌪️ 破位放量追空 (Breakdown Acceleration)
    elif (px < ema9) and (26.0 <= rsi <= 45.0) and vol_ratio >= 1.3 and macd_accel < 0 and is_bear_c and not cooldown_short and (c_a <= 0.2) and not is_high_jerk_shock:
        strategy_tag = "🌪️ 破位追空"
        raw_alpha_score = min(raw_alpha_score, -2.5)
        strategy_desc = f"【空头加速】击穿前低关键支撑放量下泄(量能={vol_ratio}x, 微积分加速度={c_a:+.2f})，顺势破位做空"
        reasons = ["空头破位下泄加速", f"放量破位 (量能 {vol_ratio}x)", "微积分负加速度下泄"]

    # Setup 5: 💎 极值均值回归 (Extreme Mean Reversion)
    elif vwap_bias <= -0.85 and rsi <= 30.0 and (is_bull_c or lower_wick >= 0.28) and not cooldown_long:
        strategy_tag = "💎 极值回归"
        raw_alpha_score = max(raw_alpha_score, 2.3)
        strategy_desc = f"【VWAP极值偏离】量价严重负乖离({vwap_bias:+.2f}%)且RSI超卖({rsi:.1f})，微积分减速企稳收阳"
        reasons = [f"VWAP严重负偏离 ({vwap_bias:+.2f}%)", "RSI极值超卖区间", "下引线止跌确认"]

    # Setup 6: 🛡️ 流动性猎杀反转 (Liquidity Sweep Reversal)
    elif vwap_bias >= 0.85 and rsi >= 70.0 and (is_bear_c or upper_wick >= 0.28) and not cooldown_short:
        strategy_tag = "🛡️ 冲高反转"
        raw_alpha_score = min(raw_alpha_score, -2.3)
        strategy_desc = f"【冲高衰竭】刺破正乖离极值区({vwap_bias:+.2f}%)受阻长上影线回落(RSI={rsi:.1f})，微积分动能钝化反转"
        reasons = [f"VWAP严重正偏离 ({vwap_bias:+.2f}%)", "RSI严重超买动能钝化", "上引线受阻承压"]

    # Adaptive strategy enablement and bounded weighting are applied after classification.
    if strategy_tag != "⚪ 观望":
        if strat_enabled.get(strategy_tag, True) is False:
            return 0.0, "HOLD", ["自进化配置已停用该策略"], "⚪ 观望", f"【自进化干预】{strategy_tag} 当前已停用"
        strategy_weight = clamp(strat_weights.get(strategy_tag, 1.0), 0.7, 1.3, 1.0)
        raw_alpha_score *= strategy_weight

    final_score = round(raw_alpha_score, 1)

    # Action Decision based on Adaptive Entry Threshold
    action = "HOLD"
    if final_score >= entry_threshold and not cooldown_long:
        action = "BUY_LONG"
    elif final_score <= -entry_threshold and not cooldown_short:
        action = "SELL_SHORT"

    return final_score, action, reasons, strategy_tag, strategy_desc

def single_trader_cycle(func):
    """Prevent cron/manual overlap across the complete order-management cycle."""
    def wrapped(*args, **kwargs):
        os.makedirs(DATA_DIR, exist_ok=True)
        lock_handle = open(TRADER_LOCK_FILE, "a+", encoding="utf-8")
        try:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            lock_handle.close()
            print("[Trader] Skip: another portfolio cycle is still running")
            return None
        try:
            now_slot = int(time.time()) // 900
            if os.path.exists(TRADER_SLOT_FILE):
                try:
                    with open(TRADER_SLOT_FILE, "r", encoding="utf-8") as f:
                        slot_state = json.load(f)
                    same_slot = int(slot_state.get("slot", -1)) == now_slot
                    recently_started = int(time.time()) - int(slot_state.get("started_at", 0) or 0) < 120
                    if same_slot and recently_started:
                        print("[Trader] Skip: duplicate trigger detected in this 15-minute slot")
                        return None
                except Exception:
                    pass
            with open(TRADER_SLOT_FILE, "w", encoding="utf-8") as f:
                json.dump({"slot": now_slot, "started_at": int(time.time()), "pid": os.getpid()}, f)
            lock_handle.seek(0)
            lock_handle.truncate()
            lock_handle.write(str(os.getpid()))
            lock_handle.flush()
            cycle_environment = freeze_okx_environment()
            print(f"[Trader] OKX environment frozen for cycle: {cycle_environment.mode.upper()} / {cycle_environment.identity}")
            return func(*args, **kwargs)
        finally:
            unfreeze_okx_environment()
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
            lock_handle.close()
    return wrapped


# =============================================================================
# Master Portfolio Execution Loop
# =============================================================================
@single_trader_cycle
def execute_portfolio():
    # US-002 fail-closed entry gate: without a static V5 API Key for the frozen
    # cycle environment the engine must physically refuse to trade.
    # current_environment (not selected_environment): the decorator froze the env
    # for this cycle and okx_rest reads the same frozen instance — the gate must
    # judge the very environment the REST channel will sign with.
    _engine_env = current_environment()
    if not _engine_env.configured:
        print(f"[Engine NOT READY] OKX {_engine_env.mode.upper()} API Key 未配置：V5 直签是唯一交易通道，本周期拒绝交易。")
        return None
    tz_bj = datetime.timezone(datetime.timedelta(hours=8))
    now_dt = datetime.datetime.now(tz_bj)
    timestamp_full = now_dt.strftime("%Y-%m-%d %H:%M:%S")

    # 0. Clean Stale Open Orders & Harvest Real-time News Sentiment
    orders_ok, orders_error = clean_stale_open_orders()
    if not orders_ok:
        print(f"[Trader] Abort: unable to verify/cancel stale open orders: {orders_error}")
        return None
    try:
        harvester_script = os.path.join(WORKSPACE_DIR, "scripts", "news_sentiment_harvester.py")
        if os.path.exists(harvester_script):
            subprocess.run(f"python3 {harvester_script}", shell=True, capture_output=True, text=True, timeout=25)
    except Exception as e:
        print(f"News Harvester sync warning: {e}")

    # 1. Fetch Real Positions. A failed account query aborts the complete cycle.
    positions_ok, all_positions, positions_error = query_positions()
    if not positions_ok:
        print(f"[Trader] Abort: unable to verify exchange positions: {positions_error}")
        return None
    real_pos_dict = {}
    real_long_count = 0
    real_short_count = 0

    if isinstance(all_positions, list):
        for p in all_positions:
            pos_sz = float(p.get("pos", 0) or 0)
            if pos_sz > 0:
                side = p.get("posSide", "net").lower()
                inst_id = p.get("instId")
                if inst_id in real_pos_dict:
                    print(f"[Trader] Abort: simultaneous long/short positions for {inst_id} are not supported")
                    return None
                real_pos_dict[inst_id] = p
                if "long" in side:
                    real_long_count += 1
                elif "short" in side:
                    real_short_count += 1

    active_pos_count = len(real_pos_dict)
    long_count = real_long_count
    short_count = real_short_count

    try:
        pending_orders = okx_rest.pending_orders()
    except Exception as pending_exc:
        print(f"[Trader] Abort: unable to verify pending orders: {pending_exc}")
        return None
    pending_inst_ids = set()
    pending_long_count = 0
    pending_short_count = 0
    if isinstance(pending_orders, list):
        for order in pending_orders:
            if str(order.get("state", "live")).lower() not in {"live", "partially_filled"}:
                continue
            inst_id = str(order.get("instId", ""))
            if inst_id:
                pending_inst_ids.add(inst_id)
            pos_side = str(order.get("posSide", "")).lower()
            if pos_side == "long":
                pending_long_count += 1
            elif pos_side == "short":
                pending_short_count += 1
    reserved_slot_count = active_pos_count + len(pending_inst_ids)
    reserved_long_count = long_count + pending_long_count
    reserved_short_count = short_count + pending_short_count

    try:
        bal_res = okx_rest.balances()
    except Exception as bal_exc:
        print(f"[Trader] Abort: unable to verify account balance: {bal_exc}")
        return None
    usdt_available = 0.0
    if bal_res:
        for d in bal_res[0].get("details", []):
            if d.get("ccy") == "USDT":
                usdt_available = float(d.get("availBal", 0.0))
                break

    # 2. Parallel fetch for the configured crypto universe
    with ThreadPoolExecutor(max_workers=len(TARGET_INSTRUMENTS)) as executor:
        all_factors = list(executor.map(lambda item: fetch_single_instrument_data(item, all_positions, usdt_available), TARGET_INSTRUMENTS))

    # 3. Process Positions & Dynamic Trailing Exits
    executed_actions = []
    trackers = load_trackers()
    stale_tracker_count = prune_trackers(trackers, real_pos_dict)
    if stale_tracker_count:
        executed_actions.append(f"清理 {stale_tracker_count} 条已失效持仓追踪记录")
    for f in all_factors:
        curr_pos = f["position"]
        if curr_pos:
            manage_position_tp_and_trailing(f, curr_pos, trackers, timestamp_full, executed_actions)
    save_trackers(trackers)

    # 4. Check Circuit Breaker & Batch AI Brain Scan (Including Active Positions Detail)
    cb_active, cb_reason = is_circuit_breaker_active(usdt_available)
    # 单标的累计保证金上限按可用余额自适应，与提示词 {{risk_budget}} 同口径
    ASSET_MARGIN_CAP = effective_single_asset_margin(usdt_available)

    brain_cache = {}
    # One LLM call covers the full six-instrument universe and all active positions.
    if not cb_active and execute_batch_ai_brain_cycle:
        try:
            pos_desc = f"当前系统总持仓 {active_pos_count}/{MAX_CONCURRENT_POSITIONS} (多{long_count}/空{short_count})"
            active_pos_list = []
            for f in all_factors:
                position = f.get("position")
                if not position:
                    continue
                position_payload = dict(position)
                tracker = trackers.get(f"{f['instId']}_{position.get('side', '')}", {})
                position_payload["trailingStopPx"] = tracker.get("trailingStopPx")
                position_payload["highWaterMark"] = tracker.get("highWaterMark")
                position_payload["lowWaterMark"] = tracker.get("lowWaterMark")
                position_payload["takeProfitPx"] = tracker.get("takeProfitPx")
                position_payload["stage_desc"] = tracker.get("stage_desc", "")
                position_payload["atr"] = f.get("atr", 0.0)
                active_pos_list.append(position_payload)
            brain_cache = execute_batch_ai_brain_cycle(pos_desc, active_pos_list, usdt_available=usdt_available) or {}
            if brain_cache:
                refreshed_ok, refreshed_positions, refreshed_error = query_positions()
                if not refreshed_ok:
                    executed_actions.append(f"AI持仓管理跳过：无法刷新真实仓位 ({refreshed_error})")
                else:
                    refreshed_pos_dict = {
                        p.get("instId"): p for p in refreshed_positions
                        if float(p.get("pos", 0) or 0) > 0
                    }
                    execute_ai_position_management(refreshed_pos_dict, trackers, timestamp_full, executed_actions)
                    save_trackers(trackers)
            else:
                executed_actions.append("本轮AI推理失败或并发跳过，禁止复用旧持仓指令")
        except Exception as e:
            print(f"[AI Brain Batch Scan Warning] {e}")

    if not cb_active:
        for f in all_factors:
            asset_type = f.get("type", "crypto")
            if not is_tradfi_market_liquid(asset_type):
                continue

            score, action, reasons, strat_tag, strat_desc = evaluate_asset_signal(f)
            inst_id = f["instId"]
            curr_pos = f["position"]
            prec = f["precision"]
            ct_val = f["ctVal"]
            profile = ASSET_CLASS_PROFILES.get(asset_type, ASSET_CLASS_PROFILES["crypto"])
            
            adaptive_cfg = load_adaptive_config()
            tp_mult = adaptive_cfg.get("tp_atr_mult", profile.get("tp_atr_mult", 2.2))
            sl_mult = adaptive_cfg.get("sl_atr_mult", profile.get("sl_atr_mult", 1.3))

            atr = max(f["atr"], f["price"] * 0.005)
            min_prof = adaptive_cfg.get("min_profit_ratio", profile.get("min_profit_ratio", 0.008))
            tp_dist = max(atr * tp_mult, f["price"] * min_prof)
            sl_dist = atr * sl_mult

            # Gate 1: LLM AI Brain Full Execution Authority
            # When AI Brain is active, AI Brain is the SOLE decider for action, leverage, margin, and TP/SL.
            ai_info = brain_cache.get(inst_id) if isinstance(brain_cache, dict) else None
            if not ai_info or "decision" not in ai_info:
                print(f"[AI Brain 全权拦截] {f['name']} 本轮无有效新鲜 AI 决策，禁止开仓")
                continue

            ai_decision = ai_info["decision"]
            ai_act = str(ai_decision.get("action", "WAIT")).upper()
            ai_conf = float(ai_decision.get("confidence", 0) or 0)
            ai_reason = ai_decision.get("summary_reason", "")

            f["ai_thought"] = ai_info.get("thought_process", {})
            f["ai_reason"] = ai_reason
            f["ai_confidence"] = ai_conf
            f["policy_version"] = ai_info.get("policy_version", "")
            f["policy_hash"] = ai_info.get("policy_hash", "")

            # Direct Action Assignment from LLM
            if ai_act in ["BUY_LONG", "SELL_SHORT"]:
                action = ai_act
                strat_tag = f"🧠 AI大脑({ai_act})"
                strat_desc = f"【AI全权决策】{ai_reason}"
                print(f"[AI Brain 全权指令] {f['name']} AI 直接指示 {action} (置信度={ai_conf}%, 理由: {ai_reason})")
            else:
                action = "HOLD"
                strat_tag = "🤖 AI观望"
                strat_desc = f"AI大脑判定当前无高确定性机会({ai_reason})"
                continue

            # Dynamic Equal-Risk Position Size with AI Custom Margin Allocation
            actual_sz = f["sz"]
            ai_margin = float(ai_decision.get("margin_usdt", 0.0) or 0.0)
            ai_lever = float(ai_decision.get("leverage", 3) or 3)
            # 杠杆硬钳制：无论 AI 裁决多激进，执行层不超过后台风控管理页配置的杠杆上限
            ai_lever = max(1.0, min(ai_lever, MAX_LEVERAGE))
            step_sz = float(f.get("minSz", 1) or 1)

            # If AI planned margin & leverage, calculate custom contract size
            if ai_margin > 0 and ai_lever >= 1.0 and f["price"] > 0 and ct_val > 0:
                planned_notional = ai_margin * ai_lever
                calculated_sz = quantize_size(planned_notional / (f["price"] * ct_val), step_sz)
                if calculated_sz > 0:
                    # 风险钳制：围绕自适应基准仓位的 0.5x~2.0x（按交易所最小步长量化，不再强制整数）
                    min_allowed_sz = max(step_sz, quantize_size(f["sz"] * 0.5, step_sz))
                    max_allowed_sz = max(min_allowed_sz, quantize_size(f["sz"] * 2.0, step_sz))
                    actual_sz = max(min_allowed_sz, min(max_allowed_sz, calculated_sz))
                    # 可用余额硬顶：单笔保证金不得超过风控页配置的余额占比，付不起则直接归零跳过而非放大
                    afford_sz = max_size_within_margin(usdt_available, ai_lever, f["price"], ct_val, step_sz)
                    if afford_sz is not None and afford_sz < float("inf"):
                        actual_sz = min(actual_sz, afford_sz)
                    actual_sz = quantize_size(actual_sz, step_sz)

            if actual_sz <= 0:
                if f.get("size_below_exchange_min") or ai_margin > 0:
                    print(f"[仓位跳过] {f['name']} 按风险预算推导的数量低于交易所最小下单量 {step_sz} 张"
                          f"(可用余额 {usdt_available}, 单笔风险额 {f['risk_per_trade_usd']}U)，本周期不交易该标的")
                continue

            # Long Execution (Initial Entry or Strict Pyramiding Scale-In)
            if action == "BUY_LONG":
                is_scale_in = False
                allow_entry = False

                # Case A: Standard Initial Entry (No existing position & slot available)
                if not curr_pos and inst_id not in pending_inst_ids and reserved_slot_count < MAX_CONCURRENT_POSITIONS and reserved_long_count < MAX_SAME_DIRECTION_POSITIONS:
                    if ai_conf >= MIN_ENTRY_CONFIDENCE:
                        allow_entry = True
                    else:
                        print(f"[首发开多拦截] {f['name']} AI置信度 {ai_conf:.1f}% 未达 80% 门禁，宁缺毋滥，拦截入场")

                # Case B: Strict Pyramiding Scale-In (Existing long position in profit/breakeven)
                elif curr_pos and str(curr_pos.get("side", "")).lower() == "long" and inst_id not in pending_inst_ids:
                    pos_upl = float(curr_pos.get("upl", 0.0) or 0.0)
                    pos_upl_ratio = float(curr_pos.get("uplRatio", 0.0) or 0.0)
                    pos_avg_px = float(curr_pos.get("avgPx", 0.0) or 0.0)
                    curr_margin = float(curr_pos.get("margin", 0.0) or 0.0)
                    tracker = trackers.get(f"{inst_id}_long", {})
                    scale_count = int(tracker.get("scale_count", 0))
                    trailing_sl = float(tracker.get("trailingStopPx", 0.0) or 0.0)

                    # Ironclad Pyramiding Rules:
                    # 1. Base position must be in profit (ROI >= +0.8%) OR stop-loss already moved to/above avg entry px (No-risk trade).
                    # 2. Maximum 1 scale-in per position to prevent overconcentration.
                    # 3. Combined margin must not exceed MAX_SINGLE_ASSET_MARGIN.
                    # 4. AI Confidence must be >= 75%.
                    # 5. Calculus Momentum & Probability Gateway: Acceleration a >= -0.25 and Continuation Prob >= 40%
                    c_dyn = f.get("calculus", {})
                    c_accel = float(c_dyn.get("acceleration", 0.0) or 0.0)
                    p_th = c_dyn.get("probability_theory", {})
                    p_cont = float(p_th.get("continuation_prob_pct", 50.0) or 50.0)
                    calculus_accel_ok = (c_accel >= -0.25 and p_cont >= 40.0)

                    is_profit_or_breakeven = (pos_upl > 0 and pos_upl_ratio >= MIN_SCALE_IN_PROFIT_RATIO) or (trailing_sl > 0 and trailing_sl >= pos_avg_px)
                    planned_margin = ai_margin if ai_margin > 0 else (actual_sz * ct_val * f["price"] / max(1.0, ai_lever))
                    within_margin_cap = (curr_margin + planned_margin) <= ASSET_MARGIN_CAP

                    if is_profit_or_breakeven and scale_count < MAX_SCALE_IN_COUNT and within_margin_cap and ai_conf >= MIN_SCALE_IN_CONFIDENCE and calculus_accel_ok:
                        allow_entry = True
                        is_scale_in = True
                        print(f"[Pyramiding] {f['name']} 满足顺势浮盈加多条件: 底仓浮盈={pos_upl:+.2f}U ({pos_upl_ratio*100:+.1f}%), 已加仓{scale_count}次, 微积分加速度={c_accel:+.2f}, 延续概率={p_cont:.1f}%, 计划加仓{actual_sz}张")
                    else:
                        if not is_profit_or_breakeven:
                            print(f"[Pyramiding 拦截] {f['name']} 底仓未达浮盈保本门禁 (浮盈={pos_upl:+.2f}U ROI={pos_upl_ratio*100:+.1f}%), 严禁逆势加仓")
                        elif scale_count >= MAX_SCALE_IN_COUNT:
                            print(f"[Pyramiding 拦截] {f['name']} 已达最大加仓次数 ({scale_count}/{MAX_SCALE_IN_COUNT})")
                        elif not within_margin_cap:
                            print(f"[Pyramiding 拦截] {f['name']} 加仓后总保证金将超限 ({curr_margin + planned_margin:.1f} > {ASSET_MARGIN_CAP}U)")
                        elif ai_conf < MIN_SCALE_IN_CONFIDENCE:
                            print(f"[Pyramiding 拦截] {f['name']} AI加仓置信度不足 ({ai_conf:.0f}% < {MIN_SCALE_IN_CONFIDENCE}%)")
                        elif not calculus_accel_ok:
                            print(f"[Pyramiding 拦截] {f['name']} 数理动能衰竭或延续概率偏低 (加速度={c_accel:+.2f}, 概率={p_cont:.1f}%)，禁止追多加仓")

                if allow_entry:
                    limit_px = round(ai_decision.get("entry_price") if (ai_decision and ai_decision.get("entry_price", 0) > 0) else (f.get("bidPx") or f["price"]), prec)
                    tp_px = round(ai_decision.get("take_profit_price") if (ai_decision and ai_decision.get("take_profit_price", 0) > 0) else (limit_px + tp_dist), prec)
                    sl_px = round(ai_decision.get("stop_loss_price") if (ai_decision and ai_decision.get("stop_loss_price", 0) > 0) else (limit_px - sl_dist), prec)

                    # Hard check: For BUY LONG, OKX strictly requires sl_px < limit_px < tp_px
                    if sl_px >= limit_px:
                        sl_px = round(limit_px - max(sl_dist, f["price"] * 0.012), prec)
                    if tp_px <= limit_px:
                        tp_px = round(limit_px + max(tp_dist, f["price"] * 0.024), prec)

                    accepted, order_ref = submit_protected_limit_order(inst_id, "buy", "long", actual_sz, limit_px, tp_px, sl_px)
                    if accepted:
                        if is_scale_in:
                            tracker = trackers.get(f"{inst_id}_long", {})
                            tracker["scale_count"] = tracker.get("scale_count", 0) + 1
                            save_trackers(trackers)
                            executed_actions.append(f"[{f['name']}] 🚀 AI顺势浮盈金字塔加多挂单已提交 {actual_sz}张@{limit_px} (order={order_ref}, TP={tp_px}, SL={sl_px})")
                            if notify_trade_open:
                                notify_trade_open(
                                    inst=f["name"],
                                    side="多 (顺势加多)",
                                    sz=actual_sz,
                                    px=limit_px,
                                    strategy="🚀 顺势金字塔加多",
                                    reason=str(ai_reason),
                                    tp_px=tp_px,
                                    sl_px=sl_px,
                                    leverage=3,
                                )
                        else:
                            executed_actions.append(f"[{f['name']}] AI限价多单已提交待成交 {actual_sz}张@{limit_px} (order={order_ref}, TP={tp_px}, SL={sl_px})")
                            pending_inst_ids.add(inst_id)
                            reserved_slot_count += 1
                            reserved_long_count += 1
                            if notify_trade_open:
                                notify_trade_open(
                                    inst=f["name"],
                                    side="多",
                                    sz=actual_sz,
                                    px=limit_px,
                                    strategy=strat_tag,
                                    reason=str(ai_reason),
                                    tp_px=tp_px,
                                    sl_px=sl_px,
                                    leverage=3,
                                )
                    else:
                        executed_actions.append(f"[{f['name']}] AI限价多单提交失败: {order_ref}")

            # Short Execution (Initial Entry or Strict Pyramiding Scale-In)
            elif action == "SELL_SHORT":
                is_scale_in = False
                allow_entry = False

                # Case A: Standard Initial Entry
                if not curr_pos and inst_id not in pending_inst_ids and reserved_slot_count < MAX_CONCURRENT_POSITIONS and reserved_short_count < MAX_SAME_DIRECTION_POSITIONS:
                    if ai_conf >= MIN_ENTRY_CONFIDENCE:
                        allow_entry = True
                    else:
                        print(f"[首发开空拦截] {f['name']} AI置信度 {ai_conf:.1f}% 未达 80% 门禁，宁缺毋滥，拦截入场")

                # Case B: Strict Pyramiding Scale-In (Existing short position in profit/breakeven)
                elif curr_pos and str(curr_pos.get("side", "")).lower() == "short" and inst_id not in pending_inst_ids:
                    pos_upl = float(curr_pos.get("upl", 0.0) or 0.0)
                    pos_upl_ratio = float(curr_pos.get("uplRatio", 0.0) or 0.0)
                    pos_avg_px = float(curr_pos.get("avgPx", 0.0) or 0.0)
                    curr_margin = float(curr_pos.get("margin", 0.0) or 0.0)
                    tracker = trackers.get(f"{inst_id}_short", {})
                    scale_count = int(tracker.get("scale_count", 0))
                    trailing_sl = float(tracker.get("trailingStopPx", 0.0) or 0.0)

                    is_profit_or_breakeven = (pos_upl > 0 and pos_upl_ratio >= MIN_SCALE_IN_PROFIT_RATIO) or (trailing_sl > 0 and trailing_sl <= pos_avg_px)
                    planned_margin = ai_margin if ai_margin > 0 else (actual_sz * ct_val * f["price"] / max(1.0, ai_lever))
                    within_margin_cap = (curr_margin + planned_margin) <= ASSET_MARGIN_CAP

                    c_dyn = f.get("calculus", {})
                    c_accel = float(c_dyn.get("acceleration", 0.0) or 0.0)
                    p_th = c_dyn.get("probability_theory", {})
                    p_break = float(p_th.get("breakdown_prob_pct", 50.0) or 50.0)
                    calculus_accel_ok = (c_accel <= 0.25 and p_break >= 40.0)

                    if is_profit_or_breakeven and scale_count < MAX_SCALE_IN_COUNT and within_margin_cap and ai_conf >= MIN_SCALE_IN_CONFIDENCE and calculus_accel_ok:
                        allow_entry = True
                        is_scale_in = True
                        print(f"[Pyramiding] {f['name']} 满足顺势浮盈加空条件: 底仓浮盈={pos_upl:+.2f}U ({pos_upl_ratio*100:+.1f}%), 已加仓{scale_count}次, 微积分加速度={c_accel:+.2f}, 击穿概率={p_break:.1f}%, 计划加仓{actual_sz}张")
                    else:
                        if not is_profit_or_breakeven:
                            print(f"[Pyramiding 拦截] {f['name']} 底仓未达浮盈保本门禁 (浮盈={pos_upl:+.2f}U ROI={pos_upl_ratio*100:+.1f}%), 严禁逆势加仓")
                        elif scale_count >= MAX_SCALE_IN_COUNT:
                            print(f"[Pyramiding 拦截] {f['name']} 已达最大加仓次数 ({scale_count}/{MAX_SCALE_IN_COUNT})")
                        elif not within_margin_cap:
                            print(f"[Pyramiding 拦截] {f['name']} 加仓后总保证金将超限 ({curr_margin + planned_margin:.1f} > {ASSET_MARGIN_CAP}U)")
                        elif ai_conf < MIN_SCALE_IN_CONFIDENCE:
                            print(f"[Pyramiding 拦截] {f['name']} AI加仓置信度不足 ({ai_conf:.0f}% < {MIN_SCALE_IN_CONFIDENCE}%)")
                        elif not calculus_accel_ok:
                            print(f"[Pyramiding 拦截] {f['name']} 数理动能失速企稳或击穿概率偏低 (加速度={c_accel:+.2f}, 概率={p_break:.1f}%)，禁止追空加仓")

                if allow_entry:
                    limit_px = round(ai_decision.get("entry_price") if (ai_decision and ai_decision.get("entry_price", 0) > 0) else (f.get("askPx") or f["price"]), prec)
                    tp_px = round(ai_decision.get("take_profit_price") if (ai_decision and ai_decision.get("take_profit_price", 0) > 0) else (limit_px - tp_dist), prec)
                    sl_px = round(ai_decision.get("stop_loss_price") if (ai_decision and ai_decision.get("stop_loss_price", 0) > 0) else (limit_px + sl_dist), prec)

                    # Hard check: For SELL SHORT, OKX strictly requires tp_px < limit_px < sl_px
                    if sl_px <= limit_px:
                        sl_px = round(limit_px + max(sl_dist, f["price"] * 0.012), prec)
                    if tp_px >= limit_px:
                        tp_px = round(limit_px - max(tp_dist, f["price"] * 0.024), prec)

                    accepted, order_ref = submit_protected_limit_order(inst_id, "sell", "short", actual_sz, limit_px, tp_px, sl_px)
                    if accepted:
                        if is_scale_in:
                            tracker = trackers.get(f"{inst_id}_short", {})
                            tracker["scale_count"] = tracker.get("scale_count", 0) + 1
                            save_trackers(trackers)
                            executed_actions.append(f"[{f['name']}] 🌪️ AI顺势浮盈金字塔加空挂单已提交 {actual_sz}张@{limit_px} (order={order_ref}, TP={tp_px}, SL={sl_px})")
                            if notify_trade_open:
                                notify_trade_open(
                                    inst=f["name"],
                                    side="空 (顺势加空)",
                                    sz=actual_sz,
                                    px=limit_px,
                                    strategy="🌪️ 顺势金字塔加空",
                                    reason=str(ai_reason),
                                    tp_px=tp_px,
                                    sl_px=sl_px,
                                    leverage=3,
                                )
                        else:
                            executed_actions.append(f"[{f['name']}] AI限价空单已提交待成交 {actual_sz}张@{limit_px} (order={order_ref}, TP={tp_px}, SL={sl_px})")
                            pending_inst_ids.add(inst_id)
                            reserved_slot_count += 1
                            reserved_short_count += 1
                            if notify_trade_open:
                                notify_trade_open(
                                    inst=f["name"],
                                    side="空",
                                    sz=actual_sz,
                                    px=limit_px,
                                    strategy=strat_tag,
                                    reason=str(ai_reason),
                                    tp_px=tp_px,
                                    sl_px=sl_px,
                                    leverage=3,
                                )
                    else:
                        executed_actions.append(f"[{f['name']}] AI限价空单提交失败: {order_ref}")

    # 5. Persist Latest State for Web Monitoring Dashboard
    state_payload = {
        "timestamp": timestamp_full,
        "active_positions_count": active_pos_count,
        "max_positions": MAX_CONCURRENT_POSITIONS,
        "long_count": long_count,
        "short_count": short_count,
        "circuit_breaker": {"active": cb_active, "reason": cb_reason},
        "executed_actions": executed_actions,
        "instruments": []
    }

    for f in all_factors:
        score, action, reasons, strat_tag, strat_desc = evaluate_asset_signal(f)
        state_payload["instruments"].append({
            "name": f["name"],
            "instId": f["instId"],
            "type": f["type"],
            "price": f["price"],
            "rsi": round(f["rsi"], 1),
            "rsi_7": round(f.get("rsi_7", 50.0), 1),
            "vwap_bias": round(f.get("vwap_bias", 0.0), 2),
            "macd_hist": f.get("macd_hist", 0.0),
            "macd_accel": f.get("macd_accel", 0.0),
            "obv_flow": f.get("obv_flow", "NEUTRAL"),
            "bb_bandwidth": f.get("bb_bandwidth", 0.0),
            "vol_ratio": f.get("vol_ratio", 1.0),
            "market_regime": f.get("market_regime", "CHOP"),
            "structure_1h": f.get("structure_1h", "CHOP"),
            "trend_1h": "多头" if f.get("trend_1h_bullish") else "空头",
            "trend_4h": "多头" if f.get("trend_4h_bullish") else "空头",
            "score": score,
            "action": action,
            "strategy": strat_tag,
            "desc": strat_desc,
            "position": f["position"]
        })

    with open(os.path.join(DATA_DIR, "trading_state.json"), "w", encoding="utf-8") as f:
        json.dump(state_payload, f, ensure_ascii=False, indent=2)

    # 6. Always Sync Full Lifecycle Ledger and SQLite DB in Realtime
    try:
        sync_script = os.path.join(WORKSPACE_DIR, "scripts", "sync_full_ledger.py")
        if os.path.exists(sync_script):
            subprocess.run(f"python3 {sync_script}", shell=True, capture_output=True, text=True, timeout=15)
        db_script = os.path.join(WORKSPACE_DIR, "scripts", "db_manager.py")
        if os.path.exists(db_script):
            subprocess.run(f"python3 {db_script}", shell=True, capture_output=True, text=True, timeout=15)
    except Exception as e:
        print(f"[Ledger Sync Warning] {e}")

    log_entry = f"[{timestamp_full}] ⚡ R20 Quantum Trader v{__version__} 巡检完成 | 持仓 {active_pos_count}/{MAX_CONCURRENT_POSITIONS} (多{long_count}/空{short_count}) | 动作: {', '.join(executed_actions) if executed_actions else '无开平仓操作'}\n"
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(log_entry)
    print(log_entry.strip())

if __name__ == "__main__":
    if not selected_environment().configured:
        print("[Engine NOT READY] OKX API Key 未配置（LIVE/DEMO）——退出，不执行任何交易。")
        sys.exit(3)
    execute_portfolio()
