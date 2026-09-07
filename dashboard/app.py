"""
Web Dashboard Application Module
"""
from __future__ import annotations
from typing import Any
from pathlib import Path
from r20_exchange.runtime import get_exchange, selected_environment, state_path
from scripts.instrument_pool import load_instruments
from r20_backend.account_baseline import load_account_baseline
from r20_backend.account_paths import classify_bill
import os
import json
import time
import datetime
import subprocess
import shutil
import asyncio
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DASHBOARD_DIR = BASE_DIR
WORKSPACE_DIR = os.path.dirname(BASE_DIR)
DATA_DIR = os.path.join(WORKSPACE_DIR, "data")
LOGS_DIR = os.path.join(WORKSPACE_DIR, "logs")

LEDGER_JSON_FILE = os.path.join(DATA_DIR, "trading_ledger.json")
LOG_FILE = os.path.join(LOGS_DIR, "ai_factor_trader.log")
NEWS_SENTIMENT_FILE = os.path.join(DATA_DIR, "news_sentiment.json")
REVIEW_JOURNAL_FILE = os.path.join(DATA_DIR, "trade_review_journal.json")
REPORT_JSON_FILE = os.path.join(DATA_DIR, "self_improvement_report.json")
POSITION_TRACKER_FILE = os.path.join(DATA_DIR, "position_trackers.json")
SNAPSHOTS_JSON_FILE = os.path.join(DATA_DIR, "snapshots.json")
STATE_JSON_FILE = os.path.join(DATA_DIR, "trading_state.json")
AI_DECISIONS_FILE = os.path.join(DATA_DIR, "ai_brain_decisions.json")
AI_HISTORY_FILE = os.path.join(DATA_DIR, "ai_brain_history.json")
AI_LAST_PROMPT_FILE = os.path.join(DATA_DIR, "ai_brain_last_prompt.txt")
FACTOR_LIBRARY_FILE = os.path.join(DATA_DIR, "factor_library_snapshot.json")
AI_MEMORY_MD_FILE = os.path.join(DATA_DIR, "AI_TRADING_MEMORY.md")


def load_trading_memory_md() -> str:
    """Render the live heuristic memory library.

    The structured store (data/structured_trading_memory.json) is the single
    authority written by the self-evolution engine; the legacy markdown file is
    only a fallback. Reading the file alone freezes the homepage panel on the
    last hand-edited snapshot while the engine keeps revising lessons.
    """
    rendered = ""
    try:
        from scripts.evolution_shield import render_trading_memory
        rendered = render_trading_memory(AI_MEMORY_MD_FILE, os.path.join(DATA_DIR, "ai_trading_memory.json")) or ""
    except Exception:
        rendered = ""
    if rendered.strip():
        return rendered + _memory_freshness_note()
    if os.path.exists(AI_MEMORY_MD_FILE):
        try:
            with open(AI_MEMORY_MD_FILE, "r", encoding="utf-8") as f:
                return f.read()
        except Exception:
            return ""
    return ""


def _memory_freshness_note() -> str:
    """One-line provenance footer so the panel visibly tracks engine revisions."""
    structured = os.path.join(DATA_DIR, "structured_trading_memory.json")
    try:
        with open(structured, "r", encoding="utf-8") as f:
            store = json.load(f)
        lessons = store.get("lessons") or []
        stamps = [i.get("created_at") for i in lessons if i.get("created_at")]
        newest = max(stamps) if stamps else ""
        if newest:
            try:
                local = datetime.datetime.fromisoformat(newest).astimezone(
                    datetime.timezone(datetime.timedelta(hours=8))
                ).strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                local = str(newest)[:19]
            return (
                f"\n\n> 权威来源: structured_trading_memory.json"
                f" | 修订 {str(store.get('revision'))[:8]} | 共 {len(lessons)} 条心法"
                f" | 最近更新 {local} (UTC+8)"
            )
        return f"\n\n> 权威来源: structured_trading_memory.json | 共 {len(lessons)} 条心法"
    except Exception:
        return ""

DASHBOARD_CACHE_FILE = os.path.join(DATA_DIR, "dashboard_last_good.json")
_BOUND_IDENTITY = None


def _scoped_data(current: str, name: str) -> str:
    if current == os.path.join(DATA_DIR, name):
        return str(state_path(name))
    return current


def ledger_file() -> str:
    return _scoped_data(LEDGER_JSON_FILE, "trading_ledger.json")


def tracker_file() -> str:
    return _scoped_data(POSITION_TRACKER_FILE, "position_trackers.json")


def snapshots_file() -> str:
    return _scoped_data(SNAPSHOTS_JSON_FILE, "snapshots.json")


def dashboard_cache_file() -> str:
    return _scoped_data(DASHBOARD_CACHE_FILE, "dashboard_last_good.json")


def report_file() -> str:
    return _scoped_data(REPORT_JSON_FILE, "self_improvement_report.json")


def state_file() -> str:
    return _scoped_data(STATE_JSON_FILE, "trading_state.json")


def decisions_file() -> str:
    return _scoped_data(AI_DECISIONS_FILE, "ai_brain_decisions.json")


def history_file() -> str:
    return _scoped_data(AI_HISTORY_FILE, "ai_brain_history.json")


def prompt_file() -> str:
    return _scoped_data(AI_LAST_PROMPT_FILE, "ai_brain_last_prompt.txt")


def dashboard_worker_enabled() -> bool:
    return os.getenv("R20_DASHBOARD_WORKER_ENABLED", "1").lower() in {"1", "true", "yes"}


def bind_account_scope(*, load_cache: bool = True):
    """Rebuild account paths for the live identity and drop the previous account's memory."""
    global _BOUND_IDENTITY, CACHE_DATA, LAST_CACHE_TIME
    env = selected_environment()
    identity = env.identity
    if identity == _BOUND_IDENTITY:
        return env
    _BOUND_IDENTITY = identity
    CACHE_DATA = load_persisted_dashboard_cache() if load_cache else {}
    cached_identity = (CACHE_DATA.get("runtime") or {}).get("identity")
    cached_exchange = CACHE_DATA.get("exchange")
    if cached_identity not in {None, identity} or cached_exchange not in {None, env.exchange}:
        CACHE_DATA = {}
    LAST_CACHE_TIME = 0
    return env



def _exchange_try(method: str, *args, **kwargs):
    try:
        client = get_exchange()
        return True, getattr(client, method)(*args, **kwargs), ""
    except Exception as exc:
        return False, None, str(exc)


def _stamp_runtime(payload: dict, env=None) -> dict:
    selected = env if env is not None else selected_environment()
    view = {
        "exchange": selected.exchange,
        "environment": selected.mode,
        "identity": selected.identity,
        "quantity_unit": "base",
        "ctVal": "1",
    }
    payload.update(view)
    payload["runtime"] = view
    return payload




def get_target_instruments() -> list[dict[str, Any]]:
    return load_instruments()


TARGET_INSTRUMENTS = load_instruments()

@asynccontextmanager
async def dashboard_lifespan(_: FastAPI):
    enabled = os.getenv("R20_DASHBOARD_WORKER_ENABLED", "1").lower() in {"1", "true", "yes"}
    if enabled:
        start_dashboard_background_worker()
    try:
        yield
    finally:
        if enabled:
            stop_dashboard_background_worker()


app = FastAPI(title="R20 AI Quantitative Matrix", docs_url=None, redoc_url=None, lifespan=dashboard_lifespan)
templates = Jinja2Templates(directory=os.path.join(DASHBOARD_DIR, "templates"))
app.mount("/static", StaticFiles(directory=os.path.join(DASHBOARD_DIR, "static")), name="static")

def _safe_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def load_position_trackers():
    try:
        with open(tracker_file(), "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def enrich_position_risk_fields(positions, trackers=None):
    """Add margin and stop-line fields even when protection lookup is unavailable."""
    trackers = trackers if isinstance(trackers, dict) else load_position_trackers()
    for position in positions or []:
        inst_id = str(position.get("instId") or "")
        side = str(position.get("posSide") or position.get("side") or "net").lower()
        position["posSide"] = side
        position["quantity_unit"] = "base"
        position["ctVal"] = "1"
        tracker = trackers.get(f"{inst_id}_{side}", {})
        size = abs(_safe_float(position.get("pos_sz", position.get("pos"))))
        price = _safe_float(position.get("markPx")) or _safe_float(position.get("avgPx"))
        notional = abs(_safe_float(position.get("notional_usdt"))) or round(size * price, 2)
        leverage = abs(_safe_float(position.get("lever"), 1.0)) or 1.0
        exchange_margin = abs(_safe_float(position.get("imr")))
        existing_margin = abs(_safe_float(position.get("margin_usdt")))
        if exchange_margin > 0:
            margin = exchange_margin
            margin_source = "exchange_imr"
        elif existing_margin > 0:
            margin = existing_margin
            margin_source = str(position.get("marginSource") or "cached")
        else:
            margin = round(notional / leverage, 2) if notional > 0 else 0.0
            margin_source = "notional_div_leverage"
        exchange_stop = _safe_float(position.get("exchangeSl"))
        tracker_stop = _safe_float(position.get("trailingSl")) or _safe_float(tracker.get("trailingStopPx"))
        exchange_tp = _safe_float(position.get("exchangeTp"))
        tracker_tp = _safe_float(tracker.get("takeProfitPx"))
        position.update({
            "pos_sz": size,
            "notional_usdt": round(notional, 2),
            "margin_usdt": round(margin, 2) if margin > 0 else None,
            "marginSource": margin_source,
            "trailingSl": tracker_stop or None,
            "displayStop": exchange_stop or tracker_stop or None,
            "stopSource": "exchange_cloud" if exchange_stop else ("local_tracker" if tracker_stop else "unavailable"),
            "displayTakeProfit": exchange_tp or tracker_tp or None,
            "stageDesc": position.get("stageDesc") or tracker.get("stage_desc") or "持有监控中",
            "strategyTag": position.get("strategyTag") or tracker.get("strategy_tag") or ("顺势做多" if "long" in side else "逢高做空"),
            "cloudProtectionLastVerified": (tracker.get("cloudProtection") or {}).get("verifiedAt"),
            "cloudProtectionLastDetail": (tracker.get("cloudProtection") or {}).get("detail"),
        })
        if position.get("protectionStatus") in {None, "unknown_stale"} and position["cloudProtectionLastVerified"]:
            position["protectionStatus"] = "verification_stale"
    return positions


def _load_local_factor_library():
    """Load factor_library_snapshot.json — a local file independent of OKX private API."""
    if os.path.exists(FACTOR_LIBRARY_FILE):
        try:
            with open(FACTOR_LIBRARY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _build_factors_from_local_files(positions, timestamp_full):
    """Build factors_list from trading_state.json + ai_brain_decisions.json.

    These local files do not depend on OKX private endpoints, so they are
    available even when the dashboard is in STALE/OFFLINE degraded mode.
    """
    factors_list = []
    pos_map = {p.get("instId"): p for p in positions} if isinstance(positions, list) else {}
    state_data = {}
    ai_decisions = {}
    factor_lib_map = {}
    active_pool = load_instruments()

    if os.path.exists(FACTOR_LIBRARY_FILE):
        try:
            with open(FACTOR_LIBRARY_FILE, "r", encoding="utf-8") as f_lib:
                lib_data = json.load(f_lib)
                for item in lib_data.get("instruments", []):
                    if isinstance(item, dict) and item.get("instId"):
                        factor_lib_map[item["instId"]] = item
        except Exception:
            pass

    if os.path.exists(decisions_file()):
        try:
            with open(decisions_file(), "r", encoding="utf-8") as f:
                ai_decisions = json.load(f)
        except Exception:
            pass

    inst_map = {}
    if os.path.exists(state_file()):
        try:
            with open(state_file(), "r", encoding="utf-8") as f:
                state_data = json.load(f)
                for ins in state_data.get("instruments", []):
                    if isinstance(ins, dict) and ins.get("instId"):
                        inst_map[ins["instId"]] = ins
        except Exception:
            pass

    for target in active_pool:
        inst_id = target.get("instId")
        ins = inst_map.get(inst_id) or {}
        lib_item = factor_lib_map.get(inst_id) or {}
        ai_info = ai_decisions.get(inst_id, {})
        ai_dec = ai_info.get("decision", {})
        ai_thought = ai_info.get("thought_process", {})
        action_val = ai_dec.get("action", ins.get("action", "WAIT"))
        confidence = ai_dec.get("confidence")
        reason = ai_dec.get("summary_reason", ins.get("desc", "新组合标的，雷达与量化特征已接入"))
        strategy_val = "🟢 建议做多" if action_val == "BUY_LONG" else ("🔴 建议做空" if action_val == "SELL_SHORT" else "⚪ AI观望")
        score_val = 2.5 if action_val == "BUY_LONG" else (-2.5 if action_val == "SELL_SHORT" else 0.0)
        m_struct = ai_thought.get("market_structure", f"{ins.get('market_regime', 'CHOP')} ({ins.get('trend_1h', '震荡')})")
        v_oi = ai_thought.get("volume_and_oi", f"OBV: {ins.get('obv_flow', 'NEUTRAL')}, 量能: {ins.get('vol_ratio', 1.0)}x")
        rr_ratio = ai_thought.get("risk_reward_evaluation", "盈亏比评估中")
        raw_t = ai_info.get("raw_ticker", {})
        chg_val = raw_t.get("chg24h") if raw_t.get("chg24h") is not None else lib_item.get("chg24h")
        price_val = ins.get("price") if ins.get("price") not in (None, "--") else lib_item.get("price", "--")
        rsi_val = ins.get("rsi") if ins.get("rsi") is not None else lib_item.get("trend_momentum", {}).get("rsi_14", 50.0)
        adx_val = ai_info.get("adx_1h") if ai_info.get("adx_1h") not in (None, "--") else lib_item.get("trend_momentum", {}).get("adx_1h", "--")
        sm_val = ai_info.get("smart_money") or lib_item.get("smart_money_derivatives", {})
        factors_list.append({
            "name": target.get("name") or ins.get("name"),
            "instId": inst_id,
            "position": pos_map.get(inst_id),
            "type": target.get("type", "crypto"),
            "price": price_val,
            "score": score_val,
            "chg24h": chg_val,
            "bidPx": raw_t.get("bidPx", ins.get("price", lib_item.get("microstructure", {}).get("bid_px", "--"))),
            "askPx": raw_t.get("askPx", ins.get("price", lib_item.get("microstructure", {}).get("ask_px", "--"))),
            "fundingRate": ai_info.get("raw_funding_rate") or (f"{lib_item.get('smart_money_derivatives', {}).get('funding_rate_pct', 0.0):.4f}%" if "funding_rate_pct" in lib_item.get("smart_money_derivatives", {}) else "--"),
            "oiUsd": ai_info.get("raw_oi") or lib_item.get("smart_money_derivatives", {}).get("oi_usd", "--"),
            "takerNetUsd": ai_info.get("raw_taker_vol") or lib_item.get("volume_money_flow", {}).get("taker_net_usd", "--"),
            "lsRatio": ai_info.get("raw_ls_ratio") or lib_item.get("smart_money_derivatives", {}).get("long_short_ratio", "--"),
            "rsi": rsi_val,
            "rsi_7": ins.get("rsi_7", 50.0),
            "vwap_bias": ins.get("vwap_bias", 0.0),
            "macd_hist": ins.get("macd_hist", 0.0),
            "macd_accel": ins.get("macd_accel", 0.0),
            "obv_flow": ins.get("obv_flow", lib_item.get("volume_money_flow", {}).get("obv_flow", "NEUTRAL")),
            "bb_bandwidth": ins.get("bb_bandwidth", lib_item.get("volatility_channel", {}).get("bb_width_1h", 0.0)),
            "vol_ratio": ins.get("vol_ratio", lib_item.get("volume_money_flow", {}).get("vol_ratio_15m", 1.0)),
            "trend_1h": ins.get("trend_1h", "震荡"),
            "trend_4h": ins.get("trend_4h", "震荡"),
            "market_regime": ins.get("market_regime", "CHOP"),
            "strategy_tag": strategy_val,
            "action": action_val,
            "confidence": confidence,
            "smart_money": sm_val,
            "adx_1h": adx_val,
            "leverage": ai_dec.get("leverage", 3),
            "margin_usdt": ai_dec.get("margin_usdt", 0.0),
            "entry_price": ai_dec.get("entry_price", 0.0),
            "take_profit_price": ai_dec.get("take_profit_price", 0.0),
            "stop_loss_price": ai_dec.get("stop_loss_price", 0.0),
            "risk_reward_ratio": ai_dec.get("risk_reward_ratio", "--"),
            "reason": reason,
            "market_structure": m_struct,
            "volume_and_oi": v_oi,
            "rr_ratio": rr_ratio,
            "thought_process": ai_thought,
            "desc": reason,
            "time_str": ai_info.get("time_str") or state_data.get("timestamp") or timestamp_full,
            "timestamp": ai_info.get("timestamp"),
        })
    return factors_list, state_data


def _inject_local_data_into_stale(stale, positions, timestamp_full):
    """Inject local-only data (factor library, factors, news, review) into a stale cache.

    When OKX private endpoints are unavailable, the dashboard enters STALE mode
    and returns the last-known-good snapshot. However, local files like
    factor_library_snapshot.json, trading_state.json, ai_brain_decisions.json,
    news_sentiment.json and the review report do NOT depend on OKX private API
    and should always reflect their latest on-disk state.
    """
    # Factor library — the source of calculus_dynamics, definite_integrals,
    # smart_money_derivatives, probability_theory, microstructure, etc.
    stale["factor_library"] = _load_local_factor_library()

    # Factors list — rebuilt from local trading_state + ai_brain_decisions
    factors_list, state_data = _build_factors_from_local_files(positions, timestamp_full)
    if factors_list:
        stale["factors"] = factors_list
        stale["state_snapshot"] = state_data

    # News intelligence — local file, no OKX dependency
    if os.path.exists(NEWS_SENTIMENT_FILE):
        try:
            with open(NEWS_SENTIMENT_FILE, "r", encoding="utf-8") as f:
                stale["news_intelligence"] = json.load(f)
        except Exception:
            pass

    # AI brain history — local file
    if os.path.exists(history_file()):
        try:
            with open(history_file(), "r", encoding="utf-8") as f:
                stale["ai_brain_history"] = json.load(f)
        except Exception:
            pass

    # Review report — local file
    if os.path.exists(report_file()):
        try:
            with open(report_file(), "r", encoding="utf-8") as f:
                stale["review"] = json.load(f)
        except Exception:
            pass

    # AI last prompt — local file
    if os.path.exists(prompt_file()):
        try:
            with open(prompt_file(), "r", encoding="utf-8") as f:
                stale["ai_last_prompt"] = f.read()
        except Exception:
            pass

    # AI trading memory — structured store first, legacy markdown as fallback
    try:
        stale["ai_trading_memory_md"] = load_trading_memory_md()
    except Exception:
        pass

    # Log lines — local file
    if os.path.exists(LOG_FILE):
        try:
            with open(LOG_FILE, "r", encoding="utf-8") as f:
                lines = f.readlines()
                stale["logs"] = [l.strip() for l in lines[-60:] if l.strip()]
        except Exception:
            pass

    # Trades table — local ledger file
    if os.path.exists(ledger_file()):
        try:
            with open(ledger_file(), "r", encoding="utf-8") as f:
                stale["trades"] = json.load(f)[:60]
        except Exception:
            pass

    return stale


def _is_meaningful_dashboard_snapshot(data):
    return isinstance(data, dict) and isinstance(data.get("account"), dict) and bool(data.get("account")) and "total_eq" in data["account"]

def load_persisted_dashboard_cache():
    try:
        with open(dashboard_cache_file(), "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if _is_meaningful_dashboard_snapshot(data) else {}
    except Exception:
        return {}


def persist_dashboard_cache(data):
    if not _is_meaningful_dashboard_snapshot(data):
        return
    cache_path = dashboard_cache_file()
    cache_dir = os.path.dirname(cache_path) or DATA_DIR
    os.makedirs(cache_dir, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix=".dashboard-cache-", suffix=".json", dir=cache_dir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_path, 0o600)
        os.replace(temp_path, cache_path)
        os.chmod(cache_path, 0o600)
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)


CACHE_DATA = {}
LAST_CACHE_TIME = 0
CACHE_LOCK = None
SYNC_EXECUTOR = ThreadPoolExecutor(max_workers=6, thread_name_prefix="dashboard_sync")
_BG_WORKER_THREAD = None
_BG_WORKER_STOP = threading.Event()
_BG_WORKER_LOCK = threading.Lock()


def _dashboard_background_worker_loop():
    if _BG_WORKER_STOP.wait(0.5):
        return
    while not _BG_WORKER_STOP.is_set():
        try:
            bind_account_scope()
            update_cache_cycle()
        except Exception:
            pass
        if _BG_WORKER_STOP.wait(2.0):
            break


def start_dashboard_background_worker():
    global _BG_WORKER_THREAD
    with _BG_WORKER_LOCK:
        if _BG_WORKER_THREAD is None or not _BG_WORKER_THREAD.is_alive():
            _BG_WORKER_STOP.clear()
            _BG_WORKER_THREAD = threading.Thread(
                target=_dashboard_background_worker_loop,
                daemon=True,
                name="dashboard_cache_worker",
            )
            _BG_WORKER_THREAD.start()


def stop_dashboard_background_worker():
    global _BG_WORKER_THREAD
    with _BG_WORKER_LOCK:
        _BG_WORKER_STOP.set()
        if _BG_WORKER_THREAD is not None:
            _BG_WORKER_THREAD.join(timeout=30)
            if _BG_WORKER_THREAD.is_alive():
                raise RuntimeError("Dashboard refresh worker did not stop within 30 seconds")
            _BG_WORKER_THREAD = None

def get_cache_lock():
    global CACHE_LOCK
    if CACHE_LOCK is None:
        CACHE_LOCK = asyncio.Lock()
    return CACHE_LOCK

def update_cache_cycle():
    global CACHE_DATA, LAST_CACHE_TIME
    tz_beijing = datetime.timezone(datetime.timedelta(hours=8))
    now_bj = datetime.datetime.now(tz_beijing)
    today_bj_str = now_bj.strftime("%Y-%m-%d")
    timestamp_full = now_bj.strftime("%Y-%m-%d %H:%M:%S (北京时间)")

    source_errors = []
    env = selected_environment()

    if not env.configured:
        source_errors.append("exchange: not configured")
        balance_ok, bal_data, positions_ok, pos_data, orders_ok, orders_data = False, [], False, [], False, []
    else:
        with ThreadPoolExecutor(max_workers=3) as pool:
            f_bal = pool.submit(_exchange_try, "balance")
            f_pos = pool.submit(_exchange_try, "positions")
            f_ord = pool.submit(_exchange_try, "open_orders")
            balance_ok, bal_data, balance_error = f_bal.result()
            positions_ok, pos_data, positions_error = f_pos.result()
            orders_ok, orders_data, orders_error = f_ord.result()
        if not balance_ok:
            source_errors.append(f"balance: {balance_error}")
            bal_data = []
        if not positions_ok:
            source_errors.append(f"positions: {positions_error}")
            pos_data = []
        if not orders_ok:
            source_errors.append(f"orders: {orders_error}")
            orders_data = []


    total_eq = 0.0
    avail_eq = 0.0
    cash_bal = 0.0
    upl_acc = 0.0

    if isinstance(bal_data, list) and bal_data:
        for d in bal_data[0].get("details", []):
            if d.get("ccy") == "USDT":
                total_eq = float(d.get("eq", 0.0) or 0.0)
                avail_eq = float(d.get("availBal") or d.get("availEq") or 0.0)
                cash_bal = float(d.get("cashBal", 0.0) or 0.0)
                upl_acc = float(d.get("upl", 0.0) or 0.0)
                break

    positions = []
    total_pos_upl = 0.0
    long_count = 0
    short_count = 0

    trackers = load_position_trackers()

    if isinstance(pos_data, list):
        for p in pos_data:
            pos_val = float(p.get("pos", 0.0) or 0.0)
            if pos_val == 0.0:
                continue

            pos_side = p.get("posSide", p.get("side", "")).lower()
            if "long" in pos_side:
                long_count += 1
            elif "short" in pos_side:
                short_count += 1

            upl = float(p.get("upl", 0.0) or 0.0)
            total_pos_upl += upl

            pos_key = f"{p.get('instId')}_{p.get('posSide', 'net')}"
            t_info = trackers.get(pos_key, {})
            trailing_sl = t_info.get("trailingStopPx", "--")
            stage_desc = t_info.get("stage_desc", "持有监控中")
            strategy_tag = t_info.get("strategy_tag") or ("🌊 低吸" if "long" in pos_side else "⚡ 高空")

            avg_px = float(p.get("avgPx", 0) or 0)
            mark_px = float(p.get("markPx", 0) or 0)
            pos_sz = abs(float(p.get("pos", 0) or 0))
            inst_id_val = p.get("instId", "")
            okx_notional = float(p.get("notionalUsd", 0) or 0)
            okx_imr = float(p.get("imr") or p.get("margin") or 0)
            notional_usdt = round(okx_notional if okx_notional > 0 else (pos_sz * (mark_px if mark_px > 0 else avg_px)), 2)
            raw_upl_ratio = float(p.get("uplRatio", 0.0) or 0.0)
            real_roi_pct = round(raw_upl_ratio * 100, 2)
            price_chg = round(((mark_px - avg_px) / avg_px * 100) if avg_px > 0 else 0, 2)

            lever_val = float(p.get("lever", "3") or 3.0)
            margin_usdt_val = round(okx_imr if okx_imr > 0 else (notional_usdt / lever_val), 2)

            positions.append({
                "instId": p.get("instId"),
                "name": p.get("instId", "").replace("-USDT-SWAP", ""),
                "posSide": pos_side,
                "side": pos_side,
                "pos": pos_sz,
                "pos_sz": pos_sz,
                "quantity_unit": "base",
                "ctVal": "1",
                "notional_usdt": notional_usdt,
                "margin_usdt": margin_usdt_val,
                "marginSource": "exchange_imr" if okx_imr > 0 else "notional_div_leverage",
                "imr": okx_imr or None,
                "lever": p.get("lever", "3"),
                "avgPx": avg_px,
                "markPx": mark_px,
                "upl": upl,
                "uplRatio": real_roi_pct,
                "roi_pct": real_roi_pct,
                "price_change_pct": price_chg,
                "liqPx": p.get("liqPx", "--"),
                "bePx": p.get("bePx", "--"),
                "trailingSl": trailing_sl,
                "stageDesc": stage_desc,
                "strategyTag": strategy_tag,
                "tp1Hit": t_info.get("tp1_hit", False),
                "tp2Hit": t_info.get("tp2_hit", False)
            })

    # Parse Pending Maker Orders
    pending_orders_list = []
    if isinstance(orders_data, list):
        for o in orders_data:
            c_ts = int(o.get("cTime", 0) or 0) / 1000.0
            c_time_str = datetime.datetime.fromtimestamp(c_ts, tz=tz_beijing).strftime("%m-%d %H:%M:%S") if c_ts > 0 else "--"
            inst_id = o.get("instId", "")
            inst_clean = inst_id.replace("-USDT-SWAP", "").replace("-SWAP", "")
            side_raw = str(o.get("side", "")).lower()
            pos_side = str(o.get("posSide", "net")).lower()
            reduce_only = str(o.get("reduceOnly", "false")).lower() == "true"
            ord_type = str(o.get("ordType", "limit")).lower()
            raw_px = str(o.get("px") or "").strip()

            if reduce_only:
                if side_raw == "sell":
                    side_label = "市价平多" if ord_type == "market" else "限价平多"
                    is_long = False
                    side_color = "rose"
                else:
                    side_label = "市价平空" if ord_type == "market" else "限价平空"
                    is_long = True
                    side_color = "emerald"
            else:
                if side_raw == "buy":
                    side_label = "市价买多" if ord_type == "market" else "限价买多"
                    is_long = True
                    side_color = "emerald"
                else:
                    side_label = "市价卖空" if ord_type == "market" else "限价卖空"
                    is_long = False
                    side_color = "rose"

            if not raw_px or raw_px == "0":
                px_display = "市价" if ord_type == "market" else "--"
            else:
                try:
                    px_float = float(raw_px)
                    px_display = f"{px_float:g}"
                except ValueError:
                    px_display = raw_px
            
            attach_list = o.get("attachAlgoOrds", [])
            tp_px = "--"
            sl_px = "--"
            if attach_list and len(attach_list) > 0:
                att = attach_list[0]
                tp_px = str(att.get("tpTriggerPx") or "--")
                sl_px = str(att.get("slTriggerPx") or "--")

            pending_orders_list.append({
                "ordId": str(o.get("ordId", "")),
                "name": inst_clean,
                "inst": inst_clean,
                "instId": inst_id,
                "side": "buy" if side_raw == "buy" else "sell",
                "side_label": side_label,
                "side_raw": side_raw,
                "posSide": pos_side,
                "is_long": is_long,
                "side_color": side_color,
                "ord_type": ord_type,
                "lever": f"{o.get('lever', '3')}x",
                "px": px_display,
                "sz": str(o.get("sz", "--")),
                "cTime": str(o.get("cTime", "")),
                "time": c_time_str,
                "state": str(o.get("state", "live")),
                "tp_px": tp_px,
                "sl_px": sl_px
            })

    # A failed core account query must never overwrite last-known-good data with zeros.
    if not balance_ok or not positions_ok:
        if _is_meaningful_dashboard_snapshot(CACHE_DATA):
            stale = dict(CACHE_DATA)
            stale_positions = (stale.get("positions_summary") or {}).get("items", [])
            enrich_position_risk_fields(stale_positions, trackers)
            stale["data_health"] = {
                "status": "STALE",
                "partial": True,
                "errors": source_errors,
                "last_success_at": CACHE_DATA.get("timestamp"),
                "attempted_at": timestamp_full,
                "cache_age_seconds": max(0.0, round(time.time() - LAST_CACHE_TIME, 1)) if LAST_CACHE_TIME > 0 else None,
            }
            # Inject local-only data that does not depend on OKX private API.
            # factor_library, factors, news, review, logs, trades etc. are
            # read from local files and should always be fresh even in STALE mode.
            _inject_local_data_into_stale(stale, stale_positions, timestamp_full)
            CACHE_DATA = _stamp_runtime(stale, env)
            LAST_CACHE_TIME = time.time()
            return
        CACHE_DATA = _stamp_runtime({
            "timestamp": timestamp_full,
            "data_health": {"status": "OFFLINE", "partial": True, "errors": source_errors},
            "account": {}, "today_stats": {}, "performance": {},
            "positions_summary": {"total": 0, "max_positions": len(load_instruments()), "items": []},
            "factors": [], "trades": [], "logs": [], "snapshots": [],
        }, env)
        LAST_CACHE_TIME = time.time()
        return

    if positions:
        with ThreadPoolExecutor(max_workers=min(len(positions), 6)) as pool:
            futures = {
                pos["instId"]: pool.submit(_exchange_try, "protection_orders", pos["instId"])
                for pos in positions
            }
            algo_results = {inst_id: f.result() for inst_id, f in futures.items()}

        for position in positions:
            algo_ok, algo_orders, algo_error = algo_results.get(position["instId"], (False, [], "timeout"))
            if not algo_ok:
                source_errors.append(f"protection {position['instId']}: {algo_error}")
                algo_orders = []
            matching_algos = [
                o for o in (algo_orders or [])
                if str(o.get("state", "live")).lower() in {"live", "effective"}
                and str(o.get("posSide", "net")).lower() in {position["posSide"], "net"}
            ]
            protected_size = sum(float(o.get("sz", 0) or 0) for o in matching_algos if o.get("slTriggerPx") or o.get("closeAll"))
            full_coverage = any(o.get("closeAll") for o in matching_algos) or protected_size >= float(position["pos_sz"]) * 0.999
            live_algo = next((o for o in matching_algos if o.get("slTriggerPx") and o.get("tpTriggerPx")), None)
            ord_type = str((live_algo or (matching_algos[0] if matching_algos else {})).get("ordType") or "")
            if ord_type == "oco":
                mechanism = "oco"
            elif ord_type == "paired_conditional":
                mechanism = "paired_conditional"
            else:
                mechanism = ord_type or "none"
            position["protectionMechanism"] = mechanism
            position["ordType"] = ord_type
            if live_algo and full_coverage:
                position["exchangeSl"] = float(live_algo.get("slTriggerPx", 0) or 0)
                position["exchangeTp"] = float(live_algo.get("tpTriggerPx", 0) or 0)
                position["protectionStatus"] = "fully_protected"
                position["protectionCoveragePct"] = 100.0
                position["protectionAlgoId"] = live_algo.get("algoId", "")
            elif matching_algos:
                sl_algo = next((o for o in matching_algos if o.get("slTriggerPx")), {})
                position["exchangeSl"] = float(sl_algo.get("slTriggerPx", 0) or 0) or None
                position["exchangeTp"] = float(sl_algo.get("tpTriggerPx", 0) or 0) or None
                position["protectionStatus"] = "partially_protected"
                position["protectionCoveragePct"] = round(min(100.0, protected_size / max(position["pos_sz"], 1e-12) * 100), 1)
                position["protectionAlgoId"] = sl_algo.get("algoId", "")
            else:
                position["exchangeSl"] = None
                position["exchangeTp"] = None
                position["protectionStatus"] = "unprotected"
                position["protectionCoveragePct"] = 0.0
                position["protectionAlgoId"] = ""
    else:
        algo_results = {}

    enrich_position_risk_fields(positions, trackers)

    baseline = load_account_baseline()
    reset_time_str = baseline.get("reset_time", "1970-01-01 00:00:00")
    initial_capital_val = float(baseline.get("initial_capital") or os.getenv("INITIAL_CAPITAL", "10000.0") or 10000.0)

    bills_ok, bills_data, bills_error = _exchange_try("bills", 100) if env.configured else (False, [], "not configured")
    if not bills_ok:
        source_errors.append(f"bills: {bills_error}")
        bills_data = []
    
    # Process Real Orders Aggregation (Minute + Inst + Action)
    orders_by_key = {}
    today_realized_gross = 0.0
    today_fees = 0.0
    cum_total_fees = 0.0
    today_funding = 0.0
    funding_history_list = []
    
    if isinstance(bills_data, list):
        for b in reversed(bills_data):
            ts = int(b.get("ts", 0) or 0) / 1000.0
            dt_bj = datetime.datetime.fromtimestamp(ts, tz=tz_beijing).strftime("%Y-%m-%d %H:%M:%S")
            if dt_bj < reset_time_str:
                continue

            inst = str(b.get("instId", "")).replace("-USDT-SWAP", "")
            pnl = float(b.get("pnl", 0) or 0)
            fee = float(b.get("fee", 0) or 0)
            bal_chg = float(b.get("balChg", 0) or 0)
            sz = float(b.get("sz", 0) or 0)
            kind = classify_bill(b)

            cum_total_fees += fee
            if today_bj_str in dt_bj:
                today_fees += fee

            if kind == "funding":
                funding_pnl = (bal_chg if bal_chg != 0 else pnl)
                if today_bj_str in dt_bj:
                    today_funding += funding_pnl
                funding_desc = "收取资金费 (+)" if funding_pnl > 0 else "支付资金费 (-)"
                funding_history_list.append({
                    "time": dt_bj,
                    "inst": inst,
                    "type_desc": funding_desc,
                    "pnl": round(funding_pnl, 6),
                    "pos_sz": f"{sz} base"
                })
                continue

            if kind == "realized":
                time_min = dt_bj[:16]
                agg_key = f"{time_min}_{inst}"
                if agg_key not in orders_by_key:
                    orders_by_key[agg_key] = {
                        "time": dt_bj,
                        "inst": inst,
                        "gross_pnl": 0.0,
                        "fee": 0.0,
                        "pnl": 0.0
                    }
                orders_by_key[agg_key]["gross_pnl"] += pnl
                orders_by_key[agg_key]["fee"] += fee
                orders_by_key[agg_key]["pnl"] += (pnl + fee)

    today_win_trades = 0
    today_loss_trades = 0
    all_win_trades = 0
    all_loss_trades = 0
    all_win_amt = 0.0
    all_loss_amt = 0.0
    by_inst = {}

    for agg_k, o in orders_by_key.items():
        net = o["pnl"]
        inst = o["inst"]
        t_time = o["time"]

        if inst not in by_inst:
            by_inst[inst] = {"trades": 0, "wins": 0, "losses": 0, "pnl": 0.0}
        by_inst[inst]["trades"] += 1
        by_inst[inst]["pnl"] += net

        # Exclude friction dust / zero-margin test orders (< 0.01 USDT absolute PnL) from win/loss trade count
        if abs(net) < 0.01 and abs(o.get("gross_pnl", 0.0)) < 0.01:
            continue

        if net > 0:
            all_win_trades += 1
            all_win_amt += net
            by_inst[inst]["wins"] += 1
            if today_bj_str in t_time:
                today_win_trades += 1
        elif net < 0:
            all_loss_trades += 1
            all_loss_amt += abs(net)
            by_inst[inst]["losses"] += 1
            if today_bj_str in t_time:
                today_loss_trades += 1

        if today_bj_str in t_time:
            today_realized_gross += o["gross_pnl"]

    today_closed = today_win_trades + today_loss_trades
    today_win_rate = round((today_win_trades / today_closed) * 100, 1) if today_closed > 0 else 0.0

    all_closed = all_win_trades + all_loss_trades
    all_win_rate = round((all_win_trades / all_closed) * 100, 1) if all_closed > 0 else 0.0
    profit_factor = round((all_win_amt / all_loss_amt), 2) if all_loss_amt > 0 else (99.0 if all_win_amt > 0 else 0.0)
    avg_win = round(all_win_amt / all_win_trades, 2) if all_win_trades > 0 else 0.0
    avg_loss = round(all_loss_amt / all_loss_trades, 2) if all_loss_trades > 0 else 0.0

    # Strict Realized PnL strictly from settled trades + settled fundings (Fixed, not jumping with mark price)
    today_net_realized_pnl = round(today_realized_gross + today_fees + today_funding, 2)
    
    # Strict Total Cumulative Net PnL strictly from Equity vs Base Capital
    total_cum_net_pnl = round(total_eq - initial_capital_val, 2)
    cum_roi_pct = round((total_cum_net_pnl / initial_capital_val * 100) if initial_capital_val > 0 else 0.0, 2)
    total_cum_realized_pnl = round(total_cum_net_pnl - total_pos_upl, 2)

    inst_leaderboard = []
    for inst, s in by_inst.items():
        w_r = round((s["wins"] / s["trades"]) * 100, 1) if s["trades"] > 0 else 0.0
        inst_leaderboard.append({
            "inst": inst,
            "trades": s["trades"],
            "wins": s["wins"],
            "losses": s["losses"],
            "win_rate": w_r,
            "pnl": round(s["pnl"], 2)
        })
    inst_leaderboard.sort(key=lambda x: x["pnl"], reverse=True)

    # 5. Load Log Lines
    log_lines = []
    if os.path.exists(LOG_FILE):
        try:
            with open(LOG_FILE, "r", encoding="utf-8") as f:
                lines = f.readlines()
                log_lines = [l.strip() for l in lines[-60:] if l.strip()]
        except Exception:
            pass

    # 6. Read Trading State & AI Brain LLM Decisions
    state_data = {}
    ai_decisions = {}
    if os.path.exists(decisions_file()):
        try:
            with open(decisions_file(), "r", encoding="utf-8") as f:
                ai_decisions = json.load(f)
        except Exception:
            pass

    factors_list = []
    pos_map = {p.get("instId"): p for p in positions} if isinstance(positions, list) else {}
    active_pool = load_instruments()
    inst_state_map = {}
    if os.path.exists(state_file()):
        try:
            with open(state_file(), "r", encoding="utf-8") as f:
                state_data = json.load(f)
                for ins in state_data.get("instruments", []):
                    if isinstance(ins, dict) and ins.get("instId"):
                        inst_state_map[ins["instId"]] = ins
        except Exception:
            pass

    factor_lib_map = {}
    if os.path.exists(FACTOR_LIBRARY_FILE):
        try:
            with open(FACTOR_LIBRARY_FILE, "r", encoding="utf-8") as f_lib:
                lib_data = json.load(f_lib)
                for item in lib_data.get("instruments", []):
                    if isinstance(item, dict) and item.get("instId"):
                        factor_lib_map[item["instId"]] = item
        except Exception:
            pass

    for target in active_pool:
        inst_id = target.get("instId")
        ins = inst_state_map.get(inst_id) or {}
        lib_item = factor_lib_map.get(inst_id) or {}
        ai_info = ai_decisions.get(inst_id, {})
        ai_dec = ai_info.get("decision", {})
        ai_thought = ai_info.get("thought_process", {})

        action_val = ai_dec.get("action", ins.get("action", "WAIT"))
        confidence = ai_dec.get("confidence")
        reason = ai_dec.get("summary_reason", ins.get("desc", "新组合标的，雷达与量化特征已接入"))

        strategy_val = "🟢 建议做多" if action_val == "BUY_LONG" else ("🔴 建议做空" if action_val == "SELL_SHORT" else "⚪ AI观望")
        score_val = 2.5 if action_val == "BUY_LONG" else (-2.5 if action_val == "SELL_SHORT" else 0.0)
        vwap_b = float(ins.get("vwap_bias", 0.0) or 0.0)

        m_struct = ai_thought.get("market_structure", f"{ins.get('market_regime', 'CHOP')} ({ins.get('trend_1h', '震荡')})")
        v_oi = ai_thought.get("volume_and_oi", f"OBV: {ins.get('obv_flow', 'NEUTRAL')}, 量能: {ins.get('vol_ratio', 1.0)}x")
        rr_ratio = ai_thought.get("risk_reward_evaluation", "盈亏比评估中")

        raw_t = ai_info.get("raw_ticker", {})
        funding_r = ai_info.get("raw_funding_rate") or (f"{lib_item.get('smart_money_derivatives', {}).get('funding_rate_pct', 0.0):.4f}%" if "funding_rate_pct" in lib_item.get("smart_money_derivatives", {}) else "--")
        oi_str = ai_info.get("raw_oi") or lib_item.get("smart_money_derivatives", {}).get("oi_usd", "--")
        taker_str = ai_info.get("raw_taker_vol") or lib_item.get("volume_money_flow", {}).get("taker_net_usd", "--")
        ls_str = ai_info.get("raw_ls_ratio") or lib_item.get("smart_money_derivatives", {}).get("long_short_ratio", "--")

        chg_val = raw_t.get("chg24h") if raw_t.get("chg24h") is not None else lib_item.get("chg24h")
        price_val = ins.get("price") if ins.get("price") not in (None, "--") else lib_item.get("price", "--")
        rsi_val = ins.get("rsi") if ins.get("rsi") is not None else lib_item.get("trend_momentum", {}).get("rsi_14", 50.0)
        adx_val = ai_info.get("adx_1h") if ai_info.get("adx_1h") not in (None, "--") else lib_item.get("trend_momentum", {}).get("adx_1h", "--")
        sm_val = ai_info.get("smart_money") or lib_item.get("smart_money_derivatives", {})

        factors_list.append({
            "name": target.get("name") or ins.get("name"),
            "instId": inst_id,
            "position": pos_map.get(inst_id),
            "type": target.get("type", "crypto"),
            "price": price_val,
            "score": score_val,
            "change24h": chg_val,
            "chg24h": chg_val,
            "bidPx": raw_t.get("bidPx", ins.get("price", lib_item.get("microstructure", {}).get("bid_px", "--"))),
            "askPx": raw_t.get("askPx", ins.get("price", lib_item.get("microstructure", {}).get("ask_px", "--"))),
            "fundingRate": funding_r,
            "oiUsd": oi_str,
            "takerNetUsd": taker_str,
            "lsRatio": ls_str,
            "rsi": rsi_val,
            "rsi_7": ins.get("rsi_7", 50.0),
            "vwap_bias": vwap_b,
            "macd_hist": ins.get("macd_hist", 0.0),
            "macd_accel": ins.get("macd_accel", 0.0),
            "obv_flow": ins.get("obv_flow", lib_item.get("volume_money_flow", {}).get("obv_flow", "NEUTRAL")),
            "bb_bandwidth": ins.get("bb_bandwidth", lib_item.get("volatility_channel", {}).get("bb_width_1h", 0.0)),
            "vol_ratio": ins.get("vol_ratio", lib_item.get("volume_money_flow", {}).get("vol_ratio_15m", 1.0)),
            "trend_1h": ins.get("trend_1h", "震荡"),
            "trend_4h": ins.get("trend_4h", "震荡"),
            "market_regime": ins.get("market_regime", "CHOP"),
            "strategy_tag": strategy_val,
            "action": action_val,
            "confidence": confidence,
            "smart_money": sm_val,
            "adx_1h": adx_val,
            "leverage": ai_dec.get("leverage", 3),
            "margin_usdt": ai_dec.get("margin_usdt", 0.0),
            "entry_price": ai_dec.get("entry_price", 0.0),
            "take_profit_price": ai_dec.get("take_profit_price", 0.0),
            "stop_loss_price": ai_dec.get("stop_loss_price", 0.0),
            "risk_reward_ratio": ai_dec.get("risk_reward_ratio", "--"),
            "reason": reason,
            "market_structure": m_struct,
            "volume_and_oi": v_oi,
            "rr_ratio": rr_ratio,
            "thought_process": ai_thought,
            "confluence_15m": m_struct,
            "confluence_1h": v_oi,
            "desc": reason,
            "ai_last_prompt": ai_info.get("ai_last_prompt", ""),
            "time_str": ai_info.get("time_str") or state_data.get("timestamp") or timestamp_full,
            "timestamp": ai_info.get("timestamp"),
        })

    ledger_trades = []
    ledger_path = ledger_file()
    need_ledger_sync = True
    if os.path.exists(ledger_path):
        try:
            mtime = os.path.getmtime(ledger_path)
            if time.time() - mtime < 60:
                need_ledger_sync = False
        except Exception:
            pass

    if need_ledger_sync and env.configured:
        try:
            from scripts.sync_full_ledger import build_lifecycle_ledger
            build_lifecycle_ledger()
        except Exception:
            pass

    if os.path.exists(ledger_path):
        try:
            with open(ledger_path, "r", encoding="utf-8") as f:
                ledger_trades = json.load(f)
        except Exception:
            pass
    if not isinstance(ledger_trades, list):
        ledger_trades = []

    valid_ledger_trades = []
    for t in ledger_trades:
        c_time = str(t.get("close_time", ""))
        o_time = str(t.get("open_time", ""))
        t_time = str(t.get("time", ""))
        if (c_time and c_time >= reset_time_str) or (o_time and o_time >= reset_time_str) or (t_time and t_time >= reset_time_str) or t.get("status") == "holding":
            valid_ledger_trades.append(t)

    trades_table = valid_ledger_trades[:60]

    review_data = {}
    if os.path.exists(report_file()):
        try:
            with open(report_file(), "r", encoding="utf-8") as f:
                review_data = json.load(f)
        except Exception:
            pass

    adaptive_cfg = {}

    # 9. Read Snapshots
    snapshots_list = []
    if os.path.exists(snapshots_file()):
        try:
            with open(snapshots_file(), "r", encoding="utf-8") as f:
                snaps = json.load(f)
                if isinstance(snaps, list):
                    # Filter strictly >= reset_time
                    for s in snaps:
                        s_time = str(s.get("time", ""))
                        if s_time >= reset_time_str:
                            t_eq = float(s.get("total_eq", s.get("equity", initial_capital_val)) or initial_capital_val)
                            pnl_v = round(t_eq - initial_capital_val, 2)
                            roi_v = round((pnl_v / initial_capital_val * 100), 2)
                            snapshots_list.append({
                                "time": s_time,
                                "total_eq": round(t_eq, 2),
                                "pnl": pnl_v,
                                "roi": roi_v
                            })
                    snapshots_list = snapshots_list[-60:]
        except Exception:
            pass

    # Append live current point
    snapshots_list.append({
        "time": timestamp_full.replace(" (北京时间)", ""),
        "total_eq": round(total_eq, 2),
        "pnl": round(total_eq - initial_capital_val, 2),
        "roi": round((total_eq - initial_capital_val) / initial_capital_val * 100, 2)
    })

    # 10. Read News & AI Decisions History
    news_data = {}
    if os.path.exists(NEWS_SENTIMENT_FILE):
        try:
            with open(NEWS_SENTIMENT_FILE, "r", encoding="utf-8") as f:
                news_data = json.load(f)
        except Exception:
            pass

    ai_last_prompt_text = ""
    if os.path.exists(prompt_file()):
        try:
            with open(prompt_file(), "r", encoding="utf-8") as f:
                ai_last_prompt_text = f.read()
        except Exception:
            pass

    ai_history_list = []
    if os.path.exists(history_file()):
        try:
            with open(history_file(), "r", encoding="utf-8") as f:
                raw_history = json.load(f)
                # Keep up to 25 records and trim heavy repeated prompts in older history
                for idx, item in enumerate(raw_history[:25]):
                    c = dict(item)
                    if idx > 0 and "ai_last_prompt" in c and len(str(c["ai_last_prompt"])) > 500:
                        c["ai_last_prompt"] = str(c["ai_last_prompt"])[:200] + "...(历史已收敛)"
                    ai_history_list.append(c)
        except Exception:
            pass

    # Inject latest prompt into review payload if running under older worker
    if isinstance(review_data, dict):
        review_data["ai_last_prompt"] = ai_last_prompt_text

    factor_lib_snapshot = {}
    if os.path.exists(FACTOR_LIBRARY_FILE):
        try:
            with open(FACTOR_LIBRARY_FILE, "r", encoding="utf-8") as f:
                factor_lib_snapshot = json.load(f)
        except Exception:
            pass

    ai_memory_md_content = load_trading_memory_md()

    ai_last_prompt_text = ""
    if os.path.exists(prompt_file()):
        try:
            with open(prompt_file(), "r", encoding="utf-8") as f:
                ai_last_prompt_text = f.read()
        except Exception:
            pass

    # System Disk info
    total_b, used_b, free_b = shutil.disk_usage("/")
    disk_free_gb = round(free_b / (1024 ** 3), 1)

    CACHE_DATA = {
        "timestamp": timestamp_full,
        "date": today_bj_str,
        "data_health": {
            "status": "LIVE" if not source_errors else "PARTIAL",
            "partial": bool(source_errors),
            "errors": source_errors,
            "last_success_at": timestamp_full,
            "cache_age_seconds": 0,
            "timezone": "Asia/Shanghai",
            "bills_complete": bills_ok,
            "bills_coverage_note": "latest 100 bills; realized/commission/funding kept separate; NAV is cumulative equity source of truth"
        },
        "system": {
            "disk": {
                "free_gb": disk_free_gb
            }
        },
        "account": {
            "initial_capital": round(initial_capital_val, 2),
            "total_eq": round(total_eq, 2),
            "avail_eq": round(avail_eq, 2),
            "cash_bal": round(cash_bal, 2),
            "upl": round(upl_acc, 2),
            "pos_upl_total": round(total_pos_upl, 2),
            "cum_realized_pnl": round(total_cum_realized_pnl, 2),
            "cum_net_pnl": round(total_cum_net_pnl, 2),
            "cum_roi_pct": cum_roi_pct,
            "cum_total_fees": round(cum_total_fees, 2),
            "margin_usage_pct": round(((total_eq - avail_eq) / total_eq * 100) if total_eq > 0 else 0, 1)
        },
        "today_stats": {
            "realized_gross": round(today_realized_gross, 2),
            "fees_paid": round(today_fees, 2),
            "funding_paid": round(today_funding, 2),
            "net_realized": round(today_net_realized_pnl, 2),
            "total_pnl": round(today_net_realized_pnl + total_pos_upl, 2),
            "win_trades": today_win_trades,
            "loss_trades": today_loss_trades,
            "win_rate": today_win_rate
        },
        "performance": {
            "all_trades": all_closed,
            "win_trades": all_win_trades,
            "loss_trades": all_loss_trades,
            "win_rate": all_win_rate,
            "profit_factor": profit_factor,
            "total_win_amt": round(all_win_amt, 2),
            "total_loss_amt": round(all_loss_amt, 2),
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "leaderboard": inst_leaderboard
        },
        "positions_summary": {
            "total": len(positions),
            "active_count": len(positions),
            "max": len(load_instruments()),
            "max_positions": len(load_instruments()),
            "long_count": long_count,
            "short_count": short_count,
            "total_upl": round(total_pos_upl, 2),
            "items": positions
        },
        "pending_orders": pending_orders_list,
        "factors": factors_list,
        "funding_settlements": {
            "total_funding_pnl": round(today_funding, 4),
            "items": sorted(funding_history_list, key=lambda x: x["time"], reverse=True)[:30]
        },
        "adaptive_config": adaptive_cfg,
        "review": review_data,
        "ai_trading_memory_md": ai_memory_md_content,
        "ai_last_prompt": ai_last_prompt_text,
        "snapshots": snapshots_list,
        "state_snapshot": state_data,
        "logs": log_lines,
        "trades": trades_table,
        "news_intelligence": news_data,
        "ai_brain_history": ai_history_list,
        "factor_library": factor_lib_snapshot
    }
    try:
        from r20_backend.llm_manager import get_active_llm_runtime
        active_llm_info = get_active_llm_runtime()
        CACHE_DATA["llm_runtime"] = {
            "model": active_llm_info.get("model", "gemini-3.8-flash-high"),
            "provider_name": active_llm_info.get("provider_name", "默认"),
            "reasoning_effort": active_llm_info.get("reasoning_effort", "high"),
            "api_format": active_llm_info.get("api_format", "openai_chat"),
        }
    except Exception:
        CACHE_DATA["llm_runtime"] = {
            "model": os.getenv("LLM_MODEL", "gemini-3.8-flash-high"),
            "provider_name": "默认",
            "reasoning_effort": os.getenv("LLM_REASONING_EFFORT", "high"),
            "api_format": "openai_chat",
        }
    _stamp_runtime(CACHE_DATA, env)
    persist_dashboard_cache(CACHE_DATA)
    LAST_CACHE_TIME = time.time()

def serve_cached_dashboard():
    env = bind_account_scope()
    payload = CACHE_DATA or {}
    if payload:
        return _stamp_runtime(dict(payload), env)
    return _stamp_runtime({
        "timestamp": datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S (北京时间)"),
        "data_health": {"status": "IDLE", "partial": True, "errors": ["account refresh required"]},
        "account": {},
        "today_stats": {},
        "performance": {},
        "positions_summary": {"total": 0, "items": []},
        "factors": [],
        "trades": [],
        "logs": [],
        "snapshots": [],
    }, env)


def refresh_account_snapshot():
    """Session-protected admin route should call this; never expose it as a public POST."""
    bind_account_scope()
    update_cache_cycle()
    return CACHE_DATA




VUE_DIST_DIR = os.path.join(WORKSPACE_DIR, "frontend", "dist")
VUE_ASSETS_DIR = os.path.join(VUE_DIST_DIR, "assets")
DOCS_IMAGES_DIR = os.path.join(WORKSPACE_DIR, "docs", "images")


class CachedStaticFiles(StaticFiles):
    """Custom static files handler that injects Cloudflare/browser long-term caching headers."""
    def __init__(self, *args, cache_control: str = "public, max-age=31536000, immutable", **kwargs):
        self.cache_control = cache_control
        super().__init__(*args, **kwargs)

    def file_response(self, *args, **kwargs) -> Response:
        resp = super().file_response(*args, **kwargs)
        resp.headers["Cache-Control"] = self.cache_control
        return resp


if os.path.isdir(VUE_ASSETS_DIR):
    app.mount("/assets", CachedStaticFiles(directory=VUE_ASSETS_DIR, cache_control="public, max-age=31536000, immutable"), name="vue_assets")

if os.path.isdir(DOCS_IMAGES_DIR):
    app.mount("/docs/images", CachedStaticFiles(directory=DOCS_IMAGES_DIR, cache_control="public, max-age=604800, stale-while-revalidate=86400"), name="docs_images")
    app.mount("/images", CachedStaticFiles(directory=DOCS_IMAGES_DIR, cache_control="public, max-age=604800, stale-while-revalidate=86400"), name="images")

VUE_ADMIN_DIST_DIR = VUE_DIST_DIR  # Same SPA build handles both / and /admin/*
VUE_ADMIN_LEGACY_FILE = os.path.join(VUE_DIST_DIR, "admin", "legacy.html")


def _serve_vue_spa(html_path: str, is_public: bool = True) -> HTMLResponse:
    with open(html_path, "r", encoding="utf-8") as f:
        content = f.read()
    # HTML shell strictly never cached in browser or edge to ensure users always load latest Vite bundle immediately
    cache_header = "no-cache, no-store, must-revalidate, max-age=0"
    return HTMLResponse(
        content=content,
        headers={
            "Cache-Control": cache_header,
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


# --- SEO Endpoints: robots.txt, sitemap.xml, favicon.svg ---

@app.get("/robots.txt", include_in_schema=False)
async def robots_txt():
    f = os.path.join(VUE_DIST_DIR, "robots.txt")
    if os.path.isfile(f):
        return FileResponse(f, media_type="text/plain", headers={"Cache-Control": "public, max-age=86400, s-maxage=604800"})
    pf = os.path.join(WORKSPACE_DIR, "frontend", "public", "robots.txt")
    if os.path.isfile(pf):
        return FileResponse(pf, media_type="text/plain", headers={"Cache-Control": "public, max-age=86400, s-maxage=604800"})
    return PlainTextResponse("User-agent: *\nAllow: /\nAllow: /docs\nAllow: /images/\nDisallow: /admin/\nDisallow: /api/\nSitemap: https://www.r20.cn/sitemap.xml\n")


@app.get("/sitemap.xml", include_in_schema=False)
async def sitemap_xml():
    f = os.path.join(VUE_DIST_DIR, "sitemap.xml")
    if os.path.isfile(f):
        return FileResponse(f, media_type="application/xml", headers={"Cache-Control": "public, max-age=86400, s-maxage=604800"})
    pf = os.path.join(WORKSPACE_DIR, "frontend", "public", "sitemap.xml")
    if os.path.isfile(pf):
        return FileResponse(pf, media_type="application/xml", headers={"Cache-Control": "public, max-age=86400, s-maxage=604800"})
    return Response(content="""<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>https://www.r20.cn/</loc><priority>1.0</priority></url><url><loc>https://www.r20.cn/docs</loc><priority>0.8</priority></url></urlset>""", media_type="application/xml")


@app.get("/favicon.svg", include_in_schema=False)
async def favicon_svg():
    f = os.path.join(VUE_DIST_DIR, "favicon.svg")
    if os.path.isfile(f):
        return FileResponse(f, media_type="image/svg+xml", headers={"Cache-Control": "public, max-age=86400, s-maxage=2592000, immutable"})
    pf = os.path.join(WORKSPACE_DIR, "frontend", "public", "favicon.svg")
    if os.path.isfile(pf):
        return FileResponse(pf, media_type="image/svg+xml", headers={"Cache-Control": "public, max-age=86400, s-maxage=2592000, immutable"})
    return Response(status_code=404)


# --- HTML Page Handlers ---

@app.api_route("/", methods=["GET", "HEAD"], response_class=HTMLResponse)
async def index(request: Request):
    vue_index_file = os.path.join(VUE_DIST_DIR, "index.html")
    if os.path.isfile(vue_index_file):
        return _serve_vue_spa(vue_index_file, is_public=True)
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        headers={"Cache-Control": "public, max-age=0, s-maxage=60, stale-while-revalidate=300"},
    )


@app.get("/admin", response_class=HTMLResponse, include_in_schema=False)
async def admin_spa_root(request: Request):
    """Serve the Vue SPA at /admin — the router handles sub-routes client-side."""
    vue_index_file = os.path.join(VUE_DIST_DIR, "index.html")
    if os.path.isfile(vue_index_file):
        return _serve_vue_spa(vue_index_file, is_public=False)
    return HTMLResponse("Vue build not found. Run `npm run build` in frontend/.", status_code=503)


@app.get("/admin/", response_class=HTMLResponse, include_in_schema=False)
async def admin_spa_root_trailing(request: Request):
    return await admin_spa_root(request)


@app.get("/admin/{subpath:path}", response_class=HTMLResponse, include_in_schema=False)
async def admin_spa_deep_link(request: Request, subpath: str):
    """Vue Router history mode: any /admin/* deep link or refresh serves the SPA shell.
    Real files under dist/admin (e.g. legacy.html) keep priority."""
    admin_dir = Path(VUE_DIST_DIR, "admin").resolve()
    try:
        candidate = (admin_dir / subpath).resolve()
        if candidate.is_relative_to(admin_dir) and candidate.is_file():
            return FileResponse(str(candidate), headers={"Cache-Control": "private, no-cache, no-store, must-revalidate"})
    except (ValueError, OSError):
        pass
    return await admin_spa_root(request)


@app.get("/docs", response_class=HTMLResponse, include_in_schema=False)
@app.get("/docs/", response_class=HTMLResponse, include_in_schema=False)
@app.get("/docs/{subpath:path}", response_class=HTMLResponse, include_in_schema=False)
@app.get("/doc", response_class=HTMLResponse, include_in_schema=False)
async def docs_spa_root(request: Request, subpath: str = ""):
    """Serve the public system documentation page in Vue SPA."""
    vue_index_file = os.path.join(VUE_DIST_DIR, "index.html")
    if os.path.isfile(vue_index_file):
        return _serve_vue_spa(vue_index_file, is_public=True)
    return HTMLResponse("Vue build not found. Run `npm run build` in frontend/.", status_code=503)


@app.get("/trading", response_class=HTMLResponse, include_in_schema=False)
@app.get("/factors", response_class=HTMLResponse, include_in_schema=False)
@app.get("/news", response_class=HTMLResponse, include_in_schema=False)
@app.get("/lab", response_class=HTMLResponse, include_in_schema=False)
@app.get("/history", response_class=HTMLResponse, include_in_schema=False)
async def public_tab_spa_routes(request: Request):
    """Serve the public Vue SPA shell for dedicated tab routes with Cloudflare edge caching."""
    vue_index_file = os.path.join(VUE_DIST_DIR, "index.html")
    if os.path.isfile(vue_index_file):
        return _serve_vue_spa(vue_index_file, is_public=True)
    return await index(request)


# --- Realtime Public Polling APIs (with Cloudflare Edge Micro-Caching) ---

@app.get("/api/all")
async def get_all_data():
    data = serve_cached_dashboard()
    return JSONResponse(
        data,
        headers={"Cache-Control": "public, max-age=0, s-maxage=2, stale-while-revalidate=5"},
    )


@app.get("/api/overview")
async def get_overview():
    data = serve_cached_dashboard()
    return JSONResponse(
        data,
        headers={"Cache-Control": "public, max-age=1, s-maxage=3, stale-while-revalidate=5"},
    )



if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
