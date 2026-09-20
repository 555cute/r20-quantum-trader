"""
Web Dashboard Application Module
"""
from __future__ import annotations
from typing import Any
from r20_backend.time_utils import beijing_text
from r20_backend.dashboard_payload.collect import (  # noqa: E402
    collect_core_account_state,
)
from r20_backend.dashboard_payload.cache import (  # noqa: E402
    load_persisted_dashboard_cache as _core_load_persisted_dashboard_cache,
    persist_dashboard_cache as _core_persist_dashboard_cache,
    _inject_local_data_into_stale as _core__inject_local_data_into_stale,
)
from r20_backend.dashboard_payload.position_view import (  # noqa: E402
    collect_position_rows as _core_collect_position_rows,
)
from r20_backend.dashboard_payload.order_view import (  # noqa: E402
    collect_pending_order_rows as _core_collect_pending_order_rows,
)
from r20_backend.dashboard_payload.trade_stats import (  # noqa: E402
    aggregate_trade_stats as _core_aggregate_trade_stats,
    aggregate_bills_and_metrics,
)
from r20_backend.dashboard_payload.trader_leaderboard import (  # noqa: E402
    build_inst_leaderboard as _core_build_inst_leaderboard,
)
from r20_backend.dashboard_payload.integrity_sidecars import (  # noqa: E402
    merge_all_integrity_sidecars as _core_merge_all_integrity_sidecars,
)
from r20_backend.dashboard_payload.bills import (  # noqa: E402
    aggregate_bills as _core_aggregate_bills,
)
from r20_backend.dashboard_payload.algo_protection import (  # noqa: E402
    collect_algo_protection as _core_collect_algo_protection,
)
from r20_backend.dashboard_payload.cache_payload import (  # noqa: E402
    build_live_cache_payload as _core_build_live_cache_payload,
)
from r20_backend.dashboard_payload.reset_state import (  # noqa: E402
    read_reset_initial_state as _core_read_reset_initial_state,
)
from r20_backend.dashboard_payload.factors_view import (  # noqa: E402
    build_factors_list as _core_build_factors_list,
)
from r20_backend.dashboard_payload.ledger_view import (  # noqa: E402
    load_ledger_lifecycle_trades as _core_load_ledger_lifecycle_trades,
)
from r20_backend.dashboard_payload.multi_venue import (  # noqa: E402
    collect_cross_venue_positions as _core_collect_cross_venue_positions,
)
from r20_backend.dashboard_payload.local_reads import (  # noqa: E402
    load_local_reads as _core_load_local_reads,
)
from r20_backend.dashboard_payload.health import (  # noqa: E402
    load_trading_memory_md as _core_load_trading_memory_md,
    _memory_freshness_note as _core__memory_freshness_note,
    build_ai_health as _core_build_ai_health,
    _load_cross_venue_data as _core__load_cross_venue_data,
)
from r20_backend.dashboard_payload import read_json, read_text, read_text_lines
from scripts import okx_rest
from scripts.instrument_pool import load_instruments
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
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

# 第 143 刀：本模块从 r20_backend/dashboard_cache.py 迁到 r20_backend/dashboard_cache.py。
# 位置深了一层，故**显式**上溯到仓库根（原来靠 dirname 恰好也对，但不写清楚就是隐患：
# 路径常量算错 ⇒ 读到不存在的 data/ ⇒ 正是 §136 那次 P0 的同款事故）。
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # 仓库根
DASHBOARD_DIR = os.path.join(BASE_DIR, "r20_backend")                    # 本包（模板/静态资源所在）
WORKSPACE_DIR = BASE_DIR
DATA_DIR = os.path.join(WORKSPACE_DIR, "data")
LOGS_DIR = os.path.join(WORKSPACE_DIR, "logs")

LEDGER_JSON_FILE = os.path.join(DATA_DIR, "trading_ledger.json")
# 批E(2026-09-13)：台账自动同步总闸（模块导入时快照，见 get_all_data 内的触发点）。
# tests/__init__.py 会在任何测试模块导入本模块之前置 R20_LEDGER_SYNC_DISABLED=1，
# 快照即成 False——此后个别测试 clear=True 清空环境也抹不掉该闸门。
LEDGER_AUTOSYNC_ENABLED = str(os.environ.get("R20_LEDGER_SYNC_DISABLED", "")).strip().lower() not in ("1", "true", "yes")
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

    """薄壳：调用时解析门面全局（结构优化阶段 2 / B2）。"""
    return _core_load_trading_memory_md(AI_MEMORY_MD_FILE, DATA_DIR)


def _memory_freshness_note() -> str:

    """薄壳：调用时解析门面全局（结构优化阶段 2 / B2）。"""
    return _core__memory_freshness_note(DATA_DIR)

DASHBOARD_CACHE_FILE = os.path.join(DATA_DIR, "dashboard_last_good.json")


from r20_backend.dashboard_payload.market import (  # noqa: E402
    get_target_instruments,
    _TRADER_CYCLE_MINUTES_MEMO,
    _trader_cycle_minutes,
    _safe_float,
    _is_meaningful_dashboard_snapshot,
    _global_env_axis,
    _load_portfolio_risk_data,
    _load_multi_venue_portfolio,
)
from r20_backend.dashboard_payload.factors import (  # noqa: E402
    _build_factors_from_local_files as _core__build_factors_from_local_files,
    _load_local_factor_library as _core__load_local_factor_library,
    enrich_position_risk_fields as _core_enrich_position_risk_fields,
    load_position_trackers as _core_load_position_trackers,
)


TARGET_INSTRUMENTS = load_instruments()


_NOT_READY_TEXT = "OKX API Key 未配置（NOT READY）：交易与账户查询已禁用"


def _fetch_json(fn, *args, **kwargs):
    """V5 直签查询包装：返回 (ok, data, error)，语义对齐旧子进程查询。

    错误文本透传给页面 data_health.errors；未配置凭证时给出 NOT READY
    人话文案而不是 traceback。异常在并发线程池里被捕获，绝不冒泡。
    """
    try:
        return True, fn(*args, **kwargs), ""
    except okx_rest.OKXNotConfigured:
        return False, None, _NOT_READY_TEXT
    except Exception as exc:  # RuntimeError(OKX 码+msg)、网络错误等人话暴露
        return False, None, f"{type(exc).__name__}: {exc}"







def load_position_trackers():

    """薄壳：调用时解析门面全局路径常量（结构优化阶段 2 / B2 第三刀）。"""
    return _core_load_position_trackers(POSITION_TRACKER_FILE)


def enrich_position_risk_fields(positions, trackers=None):

    """薄壳：调用时解析门面全局路径常量（结构优化阶段 2 / B2 第三刀）。"""
    return _core_enrich_position_risk_fields(POSITION_TRACKER_FILE, positions, trackers)


def _load_local_factor_library():

    """薄壳：调用时解析门面全局路径常量（结构优化阶段 2 / B2 第三刀）。"""
    return _core__load_local_factor_library(FACTOR_LIBRARY_FILE)


def _build_factors_from_local_files(positions, timestamp_full):

    """薄壳：调用时解析门面全局路径常量（结构优化阶段 2 / B2 第三刀）。"""
    return _core__build_factors_from_local_files(FACTOR_LIBRARY_FILE, AI_DECISIONS_FILE, STATE_JSON_FILE, positions, timestamp_full)


def build_ai_health(ai_history_list):

    """薄壳：调用时解析门面全局（结构优化阶段 2 / B2）。"""
    return _core_build_ai_health(DATA_DIR, ai_history_list)


def _inject_local_data_into_stale(stale, positions, timestamp_full):

    """薄壳：调用时解析门面全局（结构优化阶段 2 / B2）。"""
    return _core__inject_local_data_into_stale(_load_local_factor_library, _load_cross_venue_data, _load_portfolio_risk_data, _build_factors_from_local_files, build_ai_health, load_trading_memory_md, NEWS_SENTIMENT_FILE, AI_HISTORY_FILE, REPORT_JSON_FILE, AI_LAST_PROMPT_FILE, LOG_FILE, LEDGER_JSON_FILE, stale, positions, timestamp_full)




def load_persisted_dashboard_cache():

    """薄壳：调用时解析门面全局（结构优化阶段 2 / B2）。"""
    return _core_load_persisted_dashboard_cache(DASHBOARD_CACHE_FILE)


def persist_dashboard_cache(data):

    """薄壳：调用时解析门面全局（结构优化阶段 2 / B2）。"""
    return _core_persist_dashboard_cache(DASHBOARD_CACHE_FILE, DATA_DIR, data)


CACHE_DATA = load_persisted_dashboard_cache()
LAST_CACHE_TIME = 0
CACHE_LOCK = None
SYNC_EXECUTOR = ThreadPoolExecutor(max_workers=6, thread_name_prefix="dashboard_sync")
_BG_WORKER_THREAD = None
_BG_WORKER_RUNNING = False

def _dashboard_background_worker_loop():
    global _BG_WORKER_RUNNING
    # Initial small pause so server boots cleanly
    time.sleep(0.5)
    while _BG_WORKER_RUNNING:
        try:
            update_cache_cycle()
        except Exception:
            pass
        # Refresh every 2 seconds in background
        time.sleep(2.0)

def start_dashboard_background_worker():
    global _BG_WORKER_THREAD, _BG_WORKER_RUNNING
    if _BG_WORKER_THREAD is None or not _BG_WORKER_THREAD.is_alive():
        _BG_WORKER_RUNNING = True
        _BG_WORKER_THREAD = threading.Thread(
            target=_dashboard_background_worker_loop,
            daemon=True,
            name="dashboard_cache_worker"
        )
        _BG_WORKER_THREAD.start()

def stop_dashboard_background_worker():
    global _BG_WORKER_RUNNING
    _BG_WORKER_RUNNING = False

def get_cache_lock():
    global CACHE_LOCK
    if CACHE_LOCK is None:
        CACHE_LOCK = asyncio.Lock()
    return CACHE_LOCK






def _load_cross_venue_data() -> dict:

    """薄壳：调用时解析门面全局（结构优化阶段 2 / B2）。"""
    return _core__load_cross_venue_data(DATA_DIR, AI_DECISIONS_FILE)


def update_cache_cycle():
    global CACHE_DATA, LAST_CACHE_TIME
    tz_beijing = datetime.timezone(datetime.timedelta(hours=8))
    now_bj = datetime.datetime.now(tz_beijing)
    today_bj_str = now_bj.strftime("%Y-%m-%d")
    timestamp_full = now_bj.strftime("%Y-%m-%d %H:%M:%S (北京时间)")

    source_errors = []

    # Parallel Phase 1: Fetch Balance, Positions, and Maker Orders concurrently
    (_private_not_ready, avail_eq, balance_ok, cash_bal, long_count, orders_data, pending_orders_list, positions, positions_ok, short_count, total_eq, total_pos_upl, trackers, upl_acc) = collect_core_account_state(
        source_errors=source_errors,
        tz_beijing=tz_beijing,
        ThreadPoolExecutor=ThreadPoolExecutor,
        _NOT_READY_TEXT=_NOT_READY_TEXT,
        _core_collect_pending_order_rows=_core_collect_pending_order_rows,
        _core_collect_position_rows=_core_collect_position_rows,
        _fetch_json=_fetch_json,
        datetime=datetime,
        load_instruments=load_instruments,
        load_position_trackers=load_position_trackers,
        okx_rest=okx_rest    )

    # A failed core account query must never overwrite last-known-good data with zeros.
    if not balance_ok or not positions_ok:
        if _is_meaningful_dashboard_snapshot(CACHE_DATA):
            stale = dict(CACHE_DATA)
            stale_positions = (stale.get("positions_summary") or {}).get("items", [])
            enrich_position_risk_fields(stale_positions, trackers)
            stale["data_health"] = {
                "status": "NOT_READY" if _private_not_ready else "STALE",
                "partial": True,
                "errors": source_errors,
                "message": _NOT_READY_TEXT if _private_not_ready else None,
                "last_success_at": CACHE_DATA.get("timestamp"),
                "attempted_at": timestamp_full,
                "cache_age_seconds": max(0.0, round(time.time() - LAST_CACHE_TIME, 1)) if LAST_CACHE_TIME > 0 else None,
            }
            # Inject local-only data that does not depend on OKX private API.
            # factor_library, factors, news, review, logs, trades etc. are
            # read from local files and should always be fresh even in STALE mode.
            _inject_local_data_into_stale(stale, stale_positions, timestamp_full)
            CACHE_DATA = stale
            LAST_CACHE_TIME = time.time()
            return
        CACHE_DATA = {
            "timestamp": timestamp_full,
            "data_health": {
                "status": "NOT_READY" if _private_not_ready else "OFFLINE",
                "partial": True,
                "errors": source_errors,
                "message": _NOT_READY_TEXT if _private_not_ready else None,
            },
            "account": {}, "today_stats": {}, "performance": {},
            "positions_summary": {"total": 0, "max_positions": len(load_instruments()), "items": []},
            "factors": [], "trades": [], "logs": [], "snapshots": [],
        }
        LAST_CACHE_TIME = time.time()
        return

    # Parallel Phase 2: Exchange algo orders for live TP/SL protection
    # （阶段 2·B2 第九刀：迁至 dashboard_payload/algo_protection.py）
    _core_collect_algo_protection(positions, source_errors, _fetch_json,
                                  enrich_position_risk_fields, trackers)
    # 2.5 Multi-Venue Parity: Aggregate active positions & open orders from Binance & Gate
    # （阶段 2·B2 第七刀：整段迁至 dashboard_payload/multi_venue.py）
    long_count, short_count, total_pos_upl = _core_collect_cross_venue_positions(
        positions, pending_orders_list, long_count, short_count, total_pos_upl,
        source_errors=source_errors)
    # 3. Read Reset Initial State（阶段 2·B2 第九刀：迁至 dashboard_payload/reset_state.py）
    reset_time_str, initial_capital_val = _core_read_reset_initial_state(DATA_DIR)
    # 4. Load Bills and Real Order-Level Ledger
    bills_ok, bills_data, bills_error = _fetch_json(okx_rest.bills, limit=100)
    if not bills_ok:
        source_errors.append(f"bills: {bills_error}")
        bills_data = []
    
    # Process Real Orders Aggregation (Minute + Inst + Action)
    # 六项初值由 `_core_aggregate_bills` 内部建立并随返回值给出，此处不再重复初始化。
    (all_closed, all_loss_amt, all_loss_trades, all_win_amt, all_win_rate, all_win_trades, avg_loss, avg_win, cum_roi_pct, cum_total_fees, funding_history_list, inst_leaderboard, profit_factor, today_fees, today_funding, today_loss_trades, today_net_realized_pnl, today_realized_gross, today_win_rate, today_win_trades, total_cum_net_pnl, total_cum_realized_pnl) = aggregate_bills_and_metrics(
        bills_data=bills_data,
        initial_capital_val=initial_capital_val,
        reset_time_str=reset_time_str,
        today_bj_str=today_bj_str,
        total_eq=total_eq,
        total_pos_upl=total_pos_upl,
        tz_beijing=tz_beijing,
        _core_aggregate_bills=_core_aggregate_bills,
        _core_aggregate_trade_stats=_core_aggregate_trade_stats,
        _core_build_inst_leaderboard=_core_build_inst_leaderboard,
        datetime=datetime    )

    # 5. Load Log Lines
    log_lines = read_text_lines(LOG_FILE, 60)

    # 6. Read Trading State & AI Brain LLM Decisions
    # （阶段 2·B2 第八刀：迁至 dashboard_payload/factors_view.py）
    factors_list, state_data = _core_build_factors_list(
        AI_DECISIONS_FILE, STATE_JSON_FILE, FACTOR_LIBRARY_FILE, positions, timestamp_full)
    # 7. Read Ledger Lifecycle Trades for Table (阶段 2·B2 第八刀：迁至 dashboard_payload/ledger_view.py)
    valid_ledger_trades, trades_table = _core_load_ledger_lifecycle_trades(
        LEDGER_JSON_FILE, WORKSPACE_DIR, LEDGER_AUTOSYNC_ENABLED, reset_time_str)
    # 8-10. 本地读取（结构优化阶段 2·B2 第六刀：迁至 dashboard_payload/local_reads.py）
    _local = _core_load_local_reads(
        REPORT_JSON_FILE, SNAPSHOTS_JSON_FILE, NEWS_SENTIMENT_FILE, AI_LAST_PROMPT_FILE,
        AI_HISTORY_FILE, FACTOR_LIBRARY_FILE, load_trading_memory_md,
        reset_time_str, initial_capital_val, total_eq, timestamp_full,
    )
    adaptive_cfg = _local["adaptive_cfg"]
    ai_history_list = _local["ai_history_list"]
    ai_last_prompt_text = _local["ai_last_prompt_text"]
    ai_memory_md_content = _local["ai_memory_md_content"]
    disk_free_gb = _local["disk_free_gb"]
    factor_lib_snapshot = _local["factor_lib_snapshot"]
    news_data = _local["news_data"]
    review_data = _local["review_data"]
    snapshots_list = _local["snapshots_list"]

    # 审计 A2 + 审计监控面（阶段 4·B3 第二十七刀：两段旁车并入迁至
    # dashboard_payload/integrity_sidecars.py）。顺序保持原样：先台账同步状态，
    # 再 AI 连续失败 —— source_errors 的条目顺序即前端展示顺序。
    _core_merge_all_integrity_sidecars(source_errors, DATA_DIR, datetime=datetime)

    # 审计批7(2026-09-13)·「今日已实现」单一事实源：上方 bills 聚合是 OKX 单所视野
    # ——binance/gate 当日平仓（实锤：SUI +27.63）前台永远看不见，与三所合并的台账/
    # 熔断对不上。台账可用时以 ledger_today_stats 覆盖（与熔断锚点逐字同式：
    # net=Σ行pnl，fees 已含行内；funding 单列不混净值），bills 口径退化为
    # 台账缺失/损坏时的单所降级兜底。
    _today_stats_source = "okx_bills_degraded"
    if valid_ledger_trades:
        try:
            from r20_backend.execution.circuit_breaker import ledger_today_stats
            try:
                _kpi_env = _global_env_axis()
            except Exception:
                _kpi_env = ""
            _ts_led = ledger_today_stats(valid_ledger_trades, _kpi_env, today_bj_str)
            today_realized_gross = _ts_led["realized_gross"]
            today_fees = _ts_led["fees_paid"]
            today_net_realized_pnl = _ts_led["net_realized"]
            today_win_trades = _ts_led["win_trades"]
            today_loss_trades = _ts_led["loss_trades"]
            today_win_rate = _ts_led["win_rate"]
            _today_stats_source = "ledger_multi_venue"
        except Exception as _ts_exc:
            print(f"[KPI] warn 今日统计台账口径失败，回退 OKX bills: {_ts_exc}")

    CACHE_DATA = _core_build_live_cache_payload(
        _load_cross_venue_data=_load_cross_venue_data, _load_multi_venue_portfolio=_load_multi_venue_portfolio, _load_portfolio_risk_data=_load_portfolio_risk_data,
        _today_stats_source=_today_stats_source, _trader_cycle_minutes=_trader_cycle_minutes, adaptive_cfg=adaptive_cfg,
        ai_history_list=ai_history_list, ai_last_prompt_text=ai_last_prompt_text, ai_memory_md_content=ai_memory_md_content,
        all_closed=all_closed, all_loss_amt=all_loss_amt, all_loss_trades=all_loss_trades,
        all_win_amt=all_win_amt, all_win_rate=all_win_rate, all_win_trades=all_win_trades,
        avail_eq=avail_eq, avg_loss=avg_loss, avg_win=avg_win,
        build_ai_health=build_ai_health, cash_bal=cash_bal, cum_roi_pct=cum_roi_pct,
        cum_total_fees=cum_total_fees, disk_free_gb=disk_free_gb, factor_lib_snapshot=factor_lib_snapshot,
        factors_list=factors_list, funding_history_list=funding_history_list, initial_capital_val=initial_capital_val,
        inst_leaderboard=inst_leaderboard, load_instruments=load_instruments, log_lines=log_lines,
        long_count=long_count, news_data=news_data, orders_data=orders_data,
        pending_orders_list=pending_orders_list, positions=positions, profit_factor=profit_factor,
        review_data=review_data, short_count=short_count, snapshots_list=snapshots_list,
        source_errors=source_errors, state_data=state_data, timestamp_full=timestamp_full,
        today_bj_str=today_bj_str, today_fees=today_fees, today_funding=today_funding,
        today_loss_trades=today_loss_trades, today_net_realized_pnl=today_net_realized_pnl, today_realized_gross=today_realized_gross,
        today_win_rate=today_win_rate, today_win_trades=today_win_trades, total_cum_net_pnl=total_cum_net_pnl,
        total_cum_realized_pnl=total_cum_realized_pnl, total_eq=total_eq, total_pos_upl=total_pos_upl,
        trades_table=trades_table, upl_acc=upl_acc,
    )
    try:
        from r20_backend.llm_manager import get_active_llm_runtime
        active_llm_info = get_active_llm_runtime()
        CACHE_DATA["llm_runtime"] = {
            "model": active_llm_info.get("model", ""),
            "provider_name": active_llm_info.get("provider_name", "默认"),
            "reasoning_effort": active_llm_info.get("reasoning_effort", "high"),
            "api_format": active_llm_info.get("api_format", "openai_chat"),
        }
    except Exception:
        CACHE_DATA["llm_runtime"] = {
            "model": os.getenv("LLM_MODEL", ""),
            "provider_name": "默认",
            "reasoning_effort": os.getenv("LLM_REASONING_EFFORT", "high"),
            "api_format": "openai_chat",
        }
    try:
        from scripts.calculus_engine import detect_macro_market_regime
        CACHE_DATA["market_regime"] = detect_macro_market_regime(factors_list)
    except Exception:
        try:
            from calculus_engine import detect_macro_market_regime
            CACHE_DATA["market_regime"] = detect_macro_market_regime(factors_list)
        except Exception:
            pass
    persist_dashboard_cache(CACHE_DATA)
    LAST_CACHE_TIME = time.time()

# ── 载荷瘦身（审计「未完成清单」#1）────────────────────────────────────
# 实测一次 /api/all 有 30 万字符量级，体积几乎全部来自三处**重复/历史**内容：
#   · ai_brain_history 25 条，每条都带 top_opportunities / position_management /
#     policy_snapshot（约 5.6KB/条），而首页时间线只需要近期明细；
#   · review.ai_last_prompt 与顶层 ai_last_prompt 是同一段 3.6 万字符提示词的拷贝；
#   · trades 31 行 / logs 60 行，明细页有专用接口。
# 默认返回瘦身后的载荷，并对**每一处省略**显式留痕（`_trimmed` / `_meta.omitted`），
# 让前端能说"本条为摘要，完整内容见 X"，而不是把缺失渲染成"无"（缺失≠0 红线）。
# 需要完整载荷的调用方用 `/api/all?full=1`（行为与旧版逐字节一致）。
from r20_backend.dashboard_payload.slim import (  # noqa: E402
    SLIM_HISTORY_DROP_KEYS,
    SLIM_HISTORY_FULL_ENTRIES,
    SLIM_LOGS,
    SLIM_TRADES,
    slim_payload,
)


async def refresh_cache_if_needed(ttl_seconds: float = 3.0):
    global LAST_CACHE_TIME, CACHE_DATA
    if time.time() - LAST_CACHE_TIME <= ttl_seconds and CACHE_DATA:
        return CACHE_DATA
    lock = get_cache_lock()
    async with lock:
        if time.time() - LAST_CACHE_TIME <= ttl_seconds and CACHE_DATA:
            return CACHE_DATA
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(SYNC_EXECUTOR, update_cache_cycle)
        return CACHE_DATA

# Auto-start background worker to keep in-memory cache pre-warmed
start_dashboard_background_worker()



# --- SEO Endpoints ---
#
# 结构优化阶段 1（2026-09-14）· 拆除被遮蔽的重复注册。
# 背景：本项目有两套路由层 —— r20_backend/routers/*（模块化）与 r20_backend/dashboard_cache.py
# （legacy 整包）。r20_backend/app.py 是「先 include_router(...) 再
# mount("/", dashboard_app)」，Starlette 按注册顺序匹配，故凡两边同路径者，
# 一律 routers 那套生效，本文件的同名 handler 函数体永不执行 —— 改它不会
# 有任何效果，也不报错（这是最难排查的一类坑）。
#
# /robots.txt 与 /sitemap.xml：路由器已实现（routers/dashboard.py 的
# robots_txt / sitemap_xml），本文件两份连同函数体删除。
#
# /favicon.svg：只在本文件定义（路由器没有同名路由）→ 真实生效，保留。



# --- 实时公开轮询 API ---
#
# 阶段 1 已删除本文件里 @app.get("/api/all") 与 @app.get("/api/overview") 两个装饰器
# （同路径由 r20_backend/routers/dashboard.py 先注册，本文件注册永不命中）；
# 阶段 2·B2 收尾又把整个 FastAPI 外壳（app 实例、静态挂载、/ /doc /login
# /favicon.svg 四条路由）搬到 r20_backend/web_shell.py 与 routers/dashboard.py。
# 本文件自此是**纯库**：提供实现，不持有 app。

async def get_all_data(full: bool = False):
    global CACHE_DATA, LAST_CACHE_TIME
    # Return pre-warmed in-memory snapshot immediately (<1ms)
    if not CACHE_DATA or time.time() - LAST_CACHE_TIME > 5.0:
        data = await refresh_cache_if_needed(1.5)
    else:
        data = CACHE_DATA
    # 审计#1：默认瘦身（省略项在 _meta.omitted 里逐项留痕）；full=1 与旧版逐字节一致
    payload = data if full else slim_payload(data)
    # Realtime data: strictly never cache in browser (max-age=0), micro-cache at edge for 2s with fast revalidation
    return JSONResponse(
        payload,
        headers={"Cache-Control": "public, max-age=0, s-maxage=2, stale-while-revalidate=5"},
    )


async def get_overview():
    global CACHE_DATA, LAST_CACHE_TIME
    if not CACHE_DATA or time.time() - LAST_CACHE_TIME > 12.0:
        data = await refresh_cache_if_needed(2.5)
    else:
        data = CACHE_DATA
    return JSONResponse(
        data,
        headers={"Cache-Control": "public, max-age=1, s-maxage=3, stale-while-revalidate=5"},
    )

