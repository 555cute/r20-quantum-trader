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

# 结构优化阶段4·B3：纯信号逻辑已搬入 scripts/trader/signals.py，re-export 保持门面表面不变
from scripts.trader.signals import clamp, evaluate_asset_signal as _evaluate_asset_signal  # noqa: F401
from scripts.trader.reservation_reconcile import (
    utc_age_seconds as _rr_utc_age_seconds,
    reconcile_reservation_ledger as _rr_reconcile,
)
from scripts.trader.position_mgmt import (
    execute_ai_position_management as _execute_ai_position_management_impl,
)
from scripts.trader.leverage import clamp_ai_leverage
from scripts.trader.sizing import size_for_decision
from scripts.trader.venue_evidence import (
    build_venue_candidates as _venue_evidence_candidates,
    persist_venue_decision as _venue_evidence_persist,
)
from scripts.trader.cycle_stages import (
    cycle_disclosure_payload,
    cycle_disclosure_summary,
    write_cycle_disclosure_snapshot,
    data_shape_preflight_stage,
    fetch_positions_and_reconcile,
    scan_risk_gates_and_ai_brain,
    fetch_universe_and_manage_positions,
    persist_state_and_sync_ledger,
    preflight_reconcile_and_housekeeping,
    venue_protection_watchdog_stage,
)
from scripts.trader.venue_protection import (
    read_ledger_rows,
    audit_cross_venue_protection,
    watchdog_debounce_step,
)
from scripts.trader.entry_execution import (
    execute_entry_scan,
)
from scripts.trader.position_exit import (
    manage_position_tp_and_trailing as _position_exit_manage,
)
from scripts.trader.order_submit import (
    submit_protected_limit_order as _order_submit_protected,
)
from scripts.trader.routing_policy import (
    estimate_margin_usdt as _routing_policy_estimate_margin,
    load_preferred_venue as _routing_policy_load_pref,
    load_routing_mode as _routing_policy_load_mode,
    portfolio_budget_guard as _routing_policy_budget_guard,
    portfolio_risk_budget_usdt as _routing_policy_risk_budget,
    route_and_reserve_signal as _routing_policy_route,
    _decision_payload as _routing_policy_decision_payload,
    _rejection_focus_reason as _routing_policy_rejection_reason,
)
from scripts.trader.venue_query import (
    close_position_confirmed as _venue_query_close_confirmed,
    fetch_other_venue_positions as _venue_query_other_positions,
    query_positions as _venue_query_positions,
    venue_execution_ready as _venue_query_exec_ready,
    _venue_health_stamp as _venue_query_health_stamp,
)
from scripts.trader.cloud_protection import (
    amend_venue_stop_loss as _cloud_protection_amend,
    ensure_cloud_position_protection as _cloud_protection_ensure,
    sync_cloud_algo_stop as _cloud_protection_sync_stop,
    _live_oco_coverage as _cloud_protection_coverage,
)
from scripts.trader.order_lifecycle import (
    clean_stale_open_orders as _order_lifecycle_clean,
    reconcile_pending_orders as _order_lifecycle_reconcile,
)
from scripts.trader.ledger_writer import (
    record_open_intent as _ledger_writer_intent,
    record_trade as _ledger_writer_trade,
)
from scripts.trader.signal_snapshot import (
    build_signal_snapshot as _signal_snapshot_build,
)
from scripts.trader.circuit_guard import (
    check_black_swan_sentinel as _circuit_guard_sentinel,
    is_circuit_breaker_active as _circuit_guard_breaker,
)
from scripts.trader.data_shape import (validate_intents_file,
                                       validate_trackers_file)
from scripts.trader.cycle_snapshot import (
    broken_execution_venues,
    build_state_payload,
    collect_pending_inst_ids,
)
from scripts.trader.notifications import (
    entry_action_message,
    entry_failure_message,
    trade_open_kwargs,
)
from scripts.trader.order_intent import (
    build_order_intent,
    resolve_entry_prices,
)
from scripts.trader.pyramiding import (
    pyramiding_gate,
)
from scripts.trader.brackets import (
    normalize_bracket_prices,
)
from scripts.trader.gates import (
    order_margin_gate as _order_margin_gate_impl,
    equity_margin_cap as _equity_margin_cap_impl,
    is_tradfi_market_liquid as _is_tradfi_market_liquid_impl,
)
from scripts.trader.factors import fetch_single_instrument_data as _fetch_single_instrument_data
from scripts.trader.position_universe import (
    collect_okx_position_payloads as _collect_okx_position_payloads,
    merge_cross_venue_positions as _merge_cross_venue_positions,
)
from scripts.trader.protection import (
    protection_signals,
    ratcheted_trailing_stop,
    ai_tightens_stop,
    close_fee as _close_fee,
    close_trade_payload as _close_trade_payload,
)

# US-003 决策面接线：选所路由（US-002）与预算原子预留（US-001）以模块绑定名引用，
# 接线级测试 patch 模块属性即可完全离线（零出网/零凭证/零真实预留库）。
from r20_backend import risk_reservation
from r20_backend import venue_router
from r20_backend.exchanges import canonical_base
from r20_backend.exchanges import registry as venue_registry
from r20_backend.exchanges import routing_policy

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
import tempfile
import time
import warnings
import datetime
import subprocess
import urllib.request
import fcntl
from typing import Tuple, Dict, Any, List, Optional
from concurrent.futures import ThreadPoolExecutor
from market_data_service import fetch_candles, fetch_ticker
# 行情取数健康快照的写盘入口（读侧在后端 /metrics，见 MARKET_DATA_HEALTH_FILE 注释）。
from market_data_health import write_snapshot as write_market_data_health_snapshot
import scripts.okx_rest as okx_rest

# 执行层风控参数单一事实源（后台「风控管理页」写入 .env，本进程 import 时读取生效）
from risk_constants import (
    MAX_CONCURRENT_POSITIONS_CAP,
    MAX_MARGIN_EQUITY_RATIO,
    MAX_SCALE_IN_COUNT,
    MAX_SINGLE_ASSET_MARGIN,
    MAX_LEVERAGE,
    MIN_LEVERAGE,
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
    effective_daily_loss_limit,
    effective_single_asset_margin,
)

WORKSPACE_DIR = str(_PROJECT_ROOT)
DATA_DIR = os.path.join(WORKSPACE_DIR, "data")
LOGS_DIR = os.path.join(WORKSPACE_DIR, "logs")

LEDGER_JSON_FILE = os.path.join(DATA_DIR, "trading_ledger.json")
# 批E(2026-09-13)：周期收尾的台账/DB spawn 总闸（模块导入时快照——测试用
# patch.dict(clear=True) 清空环境也抹不掉）。生产不设 R20_LEDGER_SYNC_DISABLED。
LEDGER_AUTOSYNC_ENABLED = str(os.environ.get("R20_LEDGER_SYNC_DISABLED", "")).strip().lower() not in ("1", "true", "yes")
# roadmap G8：跨所（Gate/Binance）云端保护单巡检总闸。**默认关闭** —— 置于模块导入时
# 快照（与 LEDGER_AUTOSYNC_ENABLED 同法）。开闸 = 每周期对外所仓位核验保护腿并在临期
# 前续期（先挂新后撤旧；绝不猜价位、绝不撤人工腿）。开闸是运营决定，需人工拍板。
R20_VENUE_PROTECTION_WATCHDOG = str(os.environ.get("R20_VENUE_PROTECTION_WATCHDOG", "0")).strip().lower() in ("1", "true", "yes")
# 第一百二十九刀：**预演模式**（G8 开闸前的第一步）。置 1 时巡检每周期照常判定，
# 但**绝不下单/撤单**——只报"如果开闸这一轮会做什么"（审计层的 `dry_run`/`would`）。
# 与总闸同法：默认关，且总闸未开时本标志无意义（整个巡检不跑）。
R20_VENUE_PROTECTION_WATCHDOG_DRY_RUN = str(os.environ.get("R20_VENUE_PROTECTION_WATCHDOG_DRY_RUN", "0")).strip().lower() in ("1", "true", "yes")
# 第一百三十刀：**防抖窗口**（分钟，默认 30）：缺口必须持续这么久才允许真实写单。
# 续期窗口是 24h，30 分钟远小于它 —— 防的是"瞬时口径波动被当成缺口"。
# 置 0 = 显式关闭防抖（立即动手）。
try:
    R20_VENUE_PROTECTION_WATCHDOG_DEBOUNCE_S = float(
        os.environ.get("R20_VENUE_PROTECTION_WATCHDOG_DEBOUNCE_MIN", "30")) * 60.0
except (TypeError, ValueError):
    R20_VENUE_PROTECTION_WATCHDOG_DEBOUNCE_S = 30 * 60.0
# 防抖状态（跨周期记忆"这缺口从什么时候开始"）：只记时刻，不记凭证/不记仓位细节
VENUE_PROTECTION_WATCHDOG_STATE_FILE = os.path.join(
    DATA_DIR, "venue_protection_watchdog_state.json")
LOG_FILE = os.path.join(LOGS_DIR, "ai_factor_trader.log")
POSITION_TRACKER_FILE = os.path.join(DATA_DIR, "position_trackers.json")
# 2026-09-16：`SIGNAL_JOURNAL_FILE` 常量已删——它把路径**钉死在导入期**，
# `patch.object(aft, "DATA_DIR", tmp)` 对它无效，是"测试真写生产"的通道。
# 信号日记路径现由 `_signal_journal_file()` 调用期解析（见其 docstring）。
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
    from ai_brain_trader import execute_batch_ai_brain_cycle, get_latest_ai_decision, read_cycle_health
except Exception:
    record_trade_sqlite = None
    notify_trade_open = None
    notify_trade_close = None
    execute_batch_ai_brain_cycle = None
    get_latest_ai_decision = None
    read_cycle_health = None

from instrument_pool import load_instruments, pool_is_trustworthy, pool_state

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
# 单笔 1R 风险额 / 数量量化 / 可用余额硬顶：**不再本地孪生**（审计批6）。
# 曾与 r20_backend/execution/sizing.py 逐字重复两份，是「改一处漏一处」的漂移源。
from r20_backend.execution import (
    effective_risk_per_trade,
    max_size_within_margin,
    quantize_size,
)
from r20_backend.execution.cooldowns import (
    add_stop_cooldown as _cooldowns_add,
    is_in_stop_cooldown as _cooldowns_is_in,
    load_stop_cooldowns as _cooldowns_load,
    read_stop_cooldowns_state as _cooldowns_read_state,
)

def order_margin_gate(planned_margin: float, *, size: float, price: float, ct_val: float,
                      leverage: float, usdt_available: float) -> float:
    """多所下单保证金闸门。实现与理由见 scripts/trader/gates.py。

    风控常量在**调用期**读取（`risk_constants` 的 .env 改参由门面重载刷新）。
    注意：本函数名在**本文件里**出现 3 次（1 定义 + 开多 + 开空），这是计数锚点
    `tests/audit/test_audit_config_p0_hardening.py::test_both_call_sites_pass_gate_and_equity_cap`
    所依赖的。不要把本壳改成别名赋值；也不要在注释里写出带左括号的函数名
    —— 那会把自己也数进去，锚点会以"多了一次"的形式翻红（本轮就踩过这个坑）。
    """
    return _order_margin_gate_impl(
        planned_margin, size=size, price=price, ct_val=ct_val, leverage=leverage,
        usdt_available=usdt_available,
        max_single_asset_margin=MAX_SINGLE_ASSET_MARGIN,
        max_margin_equity_ratio=MAX_MARGIN_EQUITY_RATIO,
    )


def equity_margin_cap(usdt_available: float) -> float:
    """权益占比硬顶。实现见 scripts/trader/gates.py。"""
    return _equity_margin_cap_impl(usdt_available,
                                   max_margin_equity_ratio=MAX_MARGIN_EQUITY_RATIO)
# MIN_SCALE_IN_CONFIDENCE (顺势加仓最低 AI 置信度) 由 risk_constants 单一事实源注入

def is_tradfi_market_liquid(asset_type: str) -> bool:
    """美股常规交易时段判定。实现见 scripts/trader/gates.py。"""
    return _is_tradfi_market_liquid_impl(asset_type)

# 交易 shell 子进程三件套（结果/字符串/JSON 包装）已随 US-007 全量 REST 迁移删除
# （2026-09-10）：V5 直签唯一通道见 scripts/okx_rest.py；subprocess 仅保留本地
# python 脚本调度用途。禁止复活任何 shell=True 的交易所调用。

def fetch_candles_direct(inst_id: str, bar: str = "15m", limit: int = 45):
    """Direct fetch from OKX Official Market REST API with Keep-Alive connection pooling."""
    return fetch_candles(inst_id, bar=bar, limit=limit)

class UnreadableTrackers(dict):
    """**读不出来**的持仓追踪状态（与"文件不存在/真的是空"区分，第一百三十七刀）。

    它就是个 `dict`（调用方语义不变），只多一个身份标记，供 `save_trackers` 判定：
    **本轮状态不可信 ⇒ 拒绝落盘**。为什么必须有这个标记——

    读失败时返回 `{}` 会被下游当成"没有任何在管持仓"，于是：
    - `pyramiding_gate` 的「每仓最多加仓 N 次」判据 `scale_count < max` 拿到
      `scale_count=0` ⇒ **上限被静默绕过**（可反复加仓，过度集中）；
    - 一旦某笔加仓成功，调用方会 `tracker["scale_count"] = 1` 再 `save_trackers(trackers)`
      ⇒ 用这个**近乎空的字典覆盖整个文件** ⇒ 其它持仓的移动止损水位与挂单归属依据
      **被永久抹掉**（后者会让在场挂单失去 tracker 归属，只能靠意图文件兜底）。

    本刀先堵**破坏性**那一半（拒绝覆盖）；"上限无法核验"那一半如实告警，
    是否改成 fail-closed（禁本轮加仓）见 `load_trackers` 的 docstring。
    """


def load_trackers():
    """读持仓追踪。**文件不存在 ⇒ `{}`**（合法空态）；**存在却读不出来 ⇒ `UnreadableTrackers()`**。

    ⚠️ 第一百三十七刀：此前两种"空"都被压成 `{}`（只加了一条 RuntimeWarning）。
    警告≠安全——返回空字典的**后果**是实打实的：加仓次数上限被静默绕过、
    且下一笔加仓会把文件覆盖成近乎空 ⇒ 其它持仓水位/归属依据永久丢失
    （见 `UnreadableTrackers`）。现在把"读不出来"标记出来，由写入侧拒绝覆盖。

    ⚠️ 残留（待拍板）：加仓上限的"无法核验"这一半本刀**只告警不改行为** ——
    `entry_execution` 的入场循环被 `test_trader_entry_execution_extraction` 以
    **零归一 AST 逐字**冻结，改它需要先给那道门加"文档化差异"机制（独立一刀）。
    """
    if os.path.exists(POSITION_TRACKER_FILE):
        try:
            with open(POSITION_TRACKER_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as _load_err:
            warnings.warn(
                f"[trader] 持仓追踪文件读取失败，本轮按「无在管持仓」继续"
                f"（高风险：移动止损/水位丢失；**加仓次数上限无法核验**；"
                f"且本轮拒绝覆盖该文件）: {_load_err!r}",
                RuntimeWarning)
            return UnreadableTrackers()
    return {}

def save_trackers(trackers):
    """落盘持仓追踪（走本文件的原子写：mkstemp+fsync+os.replace）。

    2026-09-16：① 原先 `open("w")` 直覆写 —— 并发读者（面板/对账/下一轮巡检）
    可能读到半截 JSON，与本文件 `_atomic_write_json` 的既有审计结论相悖；
    ② 写失败原先静默 pass —— 追踪状态悄悄丢失。现改为原子写 + 失败告警。
    """
    # ⚠️ 第一百三十七刀：状态**读不出来**时拒绝覆盖 —— 否则会把其它持仓的
    # 移动止损水位与挂单归属依据永久抹掉（只为一笔记一笔加仓计数）。
    if isinstance(trackers, UnreadableTrackers):
        warnings.warn(
            "[trader] 本轮持仓追踪状态不可读 ⇒ **拒绝落盘**（防止把其它持仓的水位/"
            "加仓计数覆盖成空）；请检查 " + os.path.basename(POSITION_TRACKER_FILE),
            RuntimeWarning)
        return
    try:
        _atomic_write_json(POSITION_TRACKER_FILE, trackers)
    except Exception as _save_err:
        warnings.warn(
            f"[trader] 持仓追踪文件写入失败，本轮追踪状态未落盘"
            f"（下轮将按旧状态继续）: {_save_err!r}",
            RuntimeWarning)

def _atomic_write_json(path, payload):
    """审计③(2026-09-13)：常驻写者统一原子路数（mkstemp+fsync+os.replace，对齐
    sync_full_ledger / r20_gateway.secrets）。此前台账/状态/冷却直 open("w") 覆写，
    并发读者（熔断/日报/备份/面板）可读到半截 JSON：误停开仓、推「0胜0负」假研报、
    止损冷却静默解除。失败时旧文件原样保全（绝不撕裂）。"""
    _dir = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(prefix="." + os.path.basename(path) + "-", dir=_dir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _read_stop_cooldowns_state():
    """薄壳：转调单一事实源，并在**调用时**解析本模块的 `STOP_COOLDOWN_FILE`。

    结构优化阶段 4·B3 第五十刀：本函数与
    `r20_backend/execution/circuit_breaker.py` 的同名函数原为等价重复
    （差在 `os.path.exists` vs `Path.exists`）。已收敛到
    `r20_backend.execution.cooldowns.read_stop_cooldowns_state`。

    ⚠️ 文件路径**必须**在调用时从本模块全局解析：测试会
    `patch.object(aft, "STOP_COOLDOWN_FILE", f)`（见
    `tests/audit/test_audit_batch3_persistence_atomic.py`），import 期烘焙会让补丁静默失效。

    审计③(2026-09-13)：返回 (data, corrupt)。损坏与缺失从此不同权——
    corrupt=True 时 is_in_stop_cooldown 按「在冷却」fail-closed（旧实现损坏→{}
    等价于「无冷却」，硬止损后可立即同向重进）；add 拒做 RMW 防覆盖现场。
    """
    return _cooldowns_read_state(STOP_COOLDOWN_FILE)


def load_stop_cooldowns():
    # 兼容旧契约（只读展示面）；风控判断路径一律走 _read_stop_cooldowns_state
    return _cooldowns_load(STOP_COOLDOWN_FILE)

def add_stop_cooldown(inst_id: str, side: str, reason: str = "止损冷却"):
    """薄壳：转调单一事实源（`cooldowns.add_stop_cooldown`，第一百四十八刀）。

    写入规则（损坏拒绝写回以保全现场 / 落盘失败只告警）只有一处实现；
    本壳只负责在**调用时**提供本模块的 `STOP_COOLDOWN_FILE` 与 `_atomic_write_json`
    （测试会 patch 本模块全局；`position_exit` 等抽取模块也把本名字当注入面）。
    """
    return _cooldowns_add(inst_id, side, STOP_COOLDOWN_FILE, reason=reason,
                          atomic_write_json=_atomic_write_json)

def is_in_stop_cooldown(inst_id: str, side: str) -> bool:
    """薄壳：转调单一事实源（结构优化阶段 4·B3 第五十刀）。

    ⚠️ 冷却文件与冷却时长都在**调用时**从本模块全局解析 ——
    测试会 patch `STOP_COOLDOWN_FILE`，且 `risk_test_env.pin_baseline_risk_env()`
    会重载本模块（它在重载名单里），故按全局名查找是必须的。
    """
    return _cooldowns_is_in(inst_id, side, STOP_COOLDOWN_FILE,
                            STOP_COOLDOWN_MINUTES * 60)



def instrument_profile(inst: dict[str, Any], asset_type: str = "crypto") -> dict[str, Any]:
    """单标的参数解析（审计 P2-5）。

    旧实现：池文件里每条都写了 `max_leverage`/`sl_atr_mult`（TIER_PROFILES 派生），
    但代码只读硬编码 ASSET_CLASS_PROFILES —— 于是管理页/池文件里的参数是装饰品，
    而提示词又给出第三套口径（"止损基准 1.5~2.0x 1H ATR"）。现在单一优先级：
    池条目 per-instrument > 资产类别档 > 代码兜底，三处（提示词/下单/复算）同源。"""
    base = dict(ASSET_CLASS_PROFILES.get(asset_type, ASSET_CLASS_PROFILES["crypto"]))
    for key in ("sl_atr_mult", "tp_atr_mult", "trailing_kick_in", "trailing_pullback", "entry_threshold", "min_profit_ratio"):
        raw = (inst or {}).get(key)
        try:
            if raw is not None and str(raw).strip() != "":
                base[key] = float(raw)
        except (TypeError, ValueError):
            continue
    return base


def load_adaptive_config():
    """Fallback config reader maintaining compatibility."""
    return {}

def _run_captured(script, label=None, timeout=15):
    from r20_backend.spawn import run_script
    t = 60 if timeout == 15 and "sync_full_ledger" in str(script) else timeout
    return run_script(script, timeout=t, label=label)


# 本进程内被回收枚举实证「凭证已死」的外所集合（审计 2026-09-13：坏键所自动摘除
# 执行资格，防最低费率赢下评分后死在下单阶段白烧信号）。trader 每轮新进程=每轮
# 重探，密钥修好后下一轮自动恢复，无需人工。
_BROKEN_VENUES: set = set()


def clean_stale_open_orders(keep_ord_ids: Optional[set] = None) -> Tuple[bool, str]:
    """壳（第八十四刀搬至 `scripts/trader/order_lifecycle.py`，调用期同名注入）。"""
    return _order_lifecycle_clean(
        keep_ord_ids,
        load_open_intents=load_open_intents,
        OPEN_INTENT_TTL_MS=OPEN_INTENT_TTL_MS,
        _BROKEN_VENUES=_BROKEN_VENUES,
        current_environment=current_environment,
        load_instruments=load_instruments,
        okx_rest=okx_rest,
        venue_registry=venue_registry)

# =============================================================================
# US-006 重启接管存量挂单——周期级挂单对账
# =============================================================================
OPEN_INTENT_FILE = os.path.join(DATA_DIR, "open_order_intents.json")
OPEN_INTENT_TTL_MS = 6 * 3600 * 1000  # 本地开仓意图有效期；超期 → 周期意图已失效

RECONCILE_REASON_ORPHAN = "无对应意图"
RECONCILE_REASON_SIDE_MISMATCH = "方向不一致"
RECONCILE_REASON_INTENT_STALE = "周期意图已失效"


def record_open_intent(inst_id: str, side: str, ts_ms: int = None) -> None:
    """壳（第八十三刀搬至 `scripts/trader/ledger_writer.py`，调用期同名注入）。"""
    return _ledger_writer_intent(inst_id, side, ts_ms,
                                 OPEN_INTENT_FILE=OPEN_INTENT_FILE,
                                 OPEN_INTENT_TTL_MS=OPEN_INTENT_TTL_MS,
                                 # 第一百三十五刀：落盘改**原子替换**（消灭"写崩留
                                 # 0 字节/半截 JSON ⇒ 读取侧按孤儿撤单"这一状态）
                                 _atomic_write_json=_atomic_write_json)

class OpenIntentsUnreadable(RuntimeError):
    """本地开仓意图**存在却读不出来**（与"文件不存在/合法为空"区分，第一百三十四刀）。"""


def load_open_intents() -> List[Dict[str, Any]]:
    """读取原始本地开仓意图（不做 TTL 过滤，过期判定交给对账语义分层）。

    ⚠️ 第一百三十四刀：**"读不到"与"没有"必须分开**。此前任何异常都 `return []`，
    而两个调用方（挂单对账 `reconcile_pending_orders` / 存量挂单回收
    `clean_stale_open_orders`）拿 `[]` 会让**每一笔**挂单失去归属 ⇒ 按孤儿**撤销**
    （"撤旧挂新"循环的另一种成因），且 `reconcile_ok` 仍为 True（不 fail-closed）。
    撤单不可逆 ⇒ 现约定：文件**不存在** ⇒ `[]`（合法空态）；文件存在却
    **读不出来/结构不对** ⇒ 抛 `OpenIntentsUnreadable`，调用方据此 fail-closed
    （**不撤任何单** + 禁止本周期新增下单）。

    ⚠️ 残留边界（如实记录）：文件**被删**而此时仍有在场挂单，仍按"没有意图"处理；
    该场景留给"挂单在场 + 意图文件缺失"的独立判定，本刀不动。
    """
    if not os.path.exists(OPEN_INTENT_FILE):
        return []
    try:
        with open(OPEN_INTENT_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except Exception as e:
        raise OpenIntentsUnreadable(f"读取本地意图失败: {e!r}") from e
    if not isinstance(raw, list):
        raise OpenIntentsUnreadable(f"意图文件结构应为 list，实为 {type(raw).__name__}")
    return [i for i in raw if isinstance(i, dict) and i.get("instId")]


def _order_pos_side(side: str) -> str:
    return "long" if str(side).lower() == "buy" else "short"


def reconcile_pending_orders(trackers: Dict[str, Any] = None, now_ms: int = None, pending: List[Dict[str, Any]] = None) -> Tuple[bool, set]:
    """壳（第八十四刀搬至 `scripts/trader/order_lifecycle.py`，调用期同名注入）。"""
    return _order_lifecycle_reconcile(
        trackers, now_ms, pending,
        _order_pos_side=_order_pos_side,
        load_open_intents=load_open_intents,
        load_trackers=load_trackers,
        OPEN_INTENT_TTL_MS=OPEN_INTENT_TTL_MS,
        RECONCILE_REASON_SIDE_MISMATCH=RECONCILE_REASON_SIDE_MISMATCH,
        RECONCILE_REASON_INTENT_STALE=RECONCILE_REASON_INTENT_STALE,
        RECONCILE_REASON_ORPHAN=RECONCILE_REASON_ORPHAN,
        okx_rest=okx_rest)
def check_black_swan_sentinel() -> Tuple[bool, str]:
    """壳（第八十一刀搬至 `scripts/trader/circuit_guard.py`）。

    调用期解析模块全局再注入 —— `patch.object(aft, "NEWS_SENTIMENT_FILE"/
    "fetch_candles_direct")` 的既有测试面保真。
    """
    return _circuit_guard_sentinel(
        fetch_candles_direct=fetch_candles_direct,
        news_sentiment_file=NEWS_SENTIMENT_FILE)


def is_circuit_breaker_active(usdt_available: float = None):
    """壳（第八十一刀搬至 `scripts/trader/circuit_guard.py`，同上注入形状）。"""
    # 注意：行情/情绪文件的注入**不在这里** —— 基线 breaker 经由门面全局
    # sentinel 间接使用它们；本壳把 sentinel 本身注入（同一 patch 面、更短的路径）。
    return _circuit_guard_breaker(
        usdt_available,
        circuit_breaker_file=CIRCUIT_BREAKER_FILE,
        ledger_json_file=LEDGER_JSON_FILE,
        current_environment=current_environment,
        effective_daily_loss_limit=effective_daily_loss_limit,
        # 活体接线测试的 patch 面（aft.check_black_swan_sentinel）经此保留
        sentinel_check=check_black_swan_sentinel)



def query_positions() -> Tuple[bool, List[Dict[str, Any]], str]:
    """壳（第八十六刀搬至 `scripts/trader/venue_query.py`）。"""
    return _venue_query_positions(okx_rest=okx_rest)

def close_position_confirmed(inst_id: str, pos_side: str, before_size: float, venue: str = "okx") -> Tuple[bool, str]:
    """壳（第八十六刀搬至 `scripts/trader/venue_query.py`）。"""
    return _venue_query_close_confirmed(
        inst_id, pos_side, before_size, venue,
        okx_rest=okx_rest, current_environment=current_environment,
        query_positions=query_positions,
        fetch_other_venue_positions=fetch_other_venue_positions)

def amend_venue_stop_loss(ad, symbol: str, pos_side: str, new_sl: float,
                          contracts: float) -> Tuple[bool, str]:
    """壳（第八十五刀搬至 `scripts/trader/cloud_protection.py`，纯函数无注入）。"""
    return _cloud_protection_amend(ad, symbol, pos_side, new_sl, contracts)

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


# =============================================================================
# US-003 交易决策面接入：手动选所优先 + 可解释评分路由 + 预算原子预留
# =============================================================================
# 主脑信号进下单流程**之前**必须先过选所路由与预算预留；任一失败 → 本轮不下单
# （fail-closed），并把选所证据（含 rejected）随决策 JSON 落盘。
# 封闭性约定（测试依赖）：venue_router / risk_reservation / registry /
# routing_policy / AI_DECISION_CACHE_FILE 全部按**模块绑定名**在本文件引用，
# 逐项 patch 即可完全离线；本文件绝不直连除 OKX 直签链路以外的下单端点。

AI_DECISION_CACHE_FILE = os.path.join(DATA_DIR, "ai_brain_decisions.json")
VENUE_HEALTH_FILE = os.path.join(DATA_DIR, "venue_health.json")
#: 行情取数健康快照（worker 每周期写、后端 `/metrics` 读）。
#: 跨进程原因：取数在 worker（15 分钟 respawn），`/metrics` 在后端进程 ——
#: 进程内计数器看不到对方（与 venue_health.json 同一套手法）。
MARKET_DATA_HEALTH_FILE = os.path.join(DATA_DIR, "market_data_health.json")
CYCLE_DISCLOSURE_FILE = os.path.join(DATA_DIR, "cycle_disclosure.json")
#: 组合风险预算总上限（US-001 预留层封顶口径；0/未配置 = 只累计台账不封顶）
PORTFOLIO_RISK_BUDGET_ENV = "R20_PORTFOLIO_RISK_BUDGET_USDT"
#: 场所取数健康度可容忍年龄（brain 15min 周期写盘，给 2 个周期 + 余量）
VENUE_HEALTH_MAX_AGE_S = 1900.0

#: 已接入真实下单实现的场所 → 提交函数。三所对等支持原生受保护开仓。
VENUE_SUBMITTERS: Dict[str, str] = {
    "okx": "okx_rest.place_order",
    "gate": "execution_router.open_protected_position",
    "binance": "execution_router.open_protected_position",
}


def portfolio_risk_budget_usdt() -> float:
    """壳（第八十七刀搬至 `scripts/trader/routing_policy.py`，调用期同名注入）。"""
    return _routing_policy_risk_budget(
        PORTFOLIO_RISK_BUDGET_ENV=PORTFOLIO_RISK_BUDGET_ENV)

def load_preferred_venue() -> str:
    """壳（第八十七刀搬至 `scripts/trader/routing_policy.py`，调用期同名注入）。"""
    return _routing_policy_load_pref(
        routing_policy=routing_policy)

def load_routing_mode() -> str:
    """壳（第八十七刀搬至 `scripts/trader/routing_policy.py`，调用期同名注入）。"""
    return _routing_policy_load_mode(
        routing_policy=routing_policy)

def venue_execution_ready(venue: str, environment: str) -> bool:
    """壳（第八十六刀搬至 `scripts/trader/venue_query.py`）。"""
    return _venue_query_exec_ready(
        venue, environment,
        venue_registry=venue_registry,
        current_environment=current_environment,
        _BROKEN_VENUES=_BROKEN_VENUES)

def fetch_other_venue_positions(environment: str):
    """壳（第八十六刀搬至 `scripts/trader/venue_query.py`）。"""
    return _venue_query_other_positions(
        environment, venue_registry=venue_registry,
        venue_execution_ready=venue_execution_ready)

def _venue_health_stamp():
    """壳（第八十六刀搬至 `scripts/trader/venue_query.py`）。"""
    return _venue_query_health_stamp(VENUE_HEALTH_FILE=VENUE_HEALTH_FILE)

def build_venue_candidates(inst_id: str, environment: str) -> List[Dict[str, Any]]:
    """壳（第八十二刀搬至 `scripts/trader/venue_evidence.py`，调用期注入门面全局）。"""
    return _venue_evidence_candidates(
        inst_id, environment,
        venue_health_stamp=_venue_health_stamp,
        venue_registry=venue_registry,
        load_preferred_venue=load_preferred_venue,
        venue_execution_ready=venue_execution_ready,
        MAKER_FEE_RATE=MAKER_FEE_RATE,
        VENUE_HEALTH_MAX_AGE_S=VENUE_HEALTH_MAX_AGE_S)

def reservation_manager():
    """US-001 预留层单一台账（默认 data/risk_reservation.db）。

    每次取用都新建实例：RiskReservationManager 无进程内态（逐操作短连接 + 表内
    幂等），实例化只多一次建表；换来的是**总上限热生效**——get_manager 的默认
    单例会把首次读到的 limit 钉死，风控页改预算要重启进程才生效。
    """
    budget = portfolio_risk_budget_usdt()
    return risk_reservation.get_manager(
        db_path=risk_reservation.DEFAULT_DB_PATH,
        total_limit_usdt=budget if budget > 0 else None)


def estimate_margin_usdt(notional_usdt: float, margin_usdt: float = 0.0) -> float:
    """壳（第八十七刀搬至 `scripts/trader/routing_policy.py`，纯函数）。"""
    return _routing_policy_estimate_margin(notional_usdt, margin_usdt)

def _decision_payload(decision, preferred: str) -> Dict[str, Any]:
    """壳（第八十七刀搬至 `scripts/trader/routing_policy.py`，纯函数）。"""
    return _routing_policy_decision_payload(decision, preferred)

def persist_venue_decision(inst_id: str, venue_decision: Dict[str, Any]) -> bool:
    """壳（第八十二刀搬至 `scripts/trader/venue_evidence.py`）。

    同名注入 `AI_DECISION_CACHE_FILE`：patch 门面常量的既有面保真；
    flock 包裹的写路径真现在子包（batch3 tripwire 断言指向那里）。
    """
    return _venue_evidence_persist(inst_id, venue_decision,
                                   AI_DECISION_CACHE_FILE=AI_DECISION_CACHE_FILE)

def _rejection_focus_reason(decision, candidates: List[Dict[str, Any]],
                            preferred: str) -> str:
    """壳（第八十七刀搬至 `scripts/trader/routing_policy.py`，纯函数）。"""
    return _routing_policy_rejection_reason(decision, candidates, preferred)

def route_and_reserve_signal(inst_id: str, side: str, size: float, price: float,
                             notional_usdt: float = 0.0, margin_usdt: float = 0.0,
                             intent_id: str = "") -> Dict[str, Any]:
    """壳（第八十七刀搬至 `scripts/trader/routing_policy.py`，调用期同名注入）。"""
    return _routing_policy_route(
        inst_id, side, size, price, notional_usdt, margin_usdt, intent_id,
        _decision_payload=_decision_payload,
        _rejection_focus_reason=_rejection_focus_reason,
        build_venue_candidates=build_venue_candidates,
        estimate_margin_usdt=estimate_margin_usdt,
        load_preferred_venue=load_preferred_venue,
        load_routing_mode=load_routing_mode,
        persist_venue_decision=persist_venue_decision,
        portfolio_budget_guard=portfolio_budget_guard,
        portfolio_risk_budget_usdt=portfolio_risk_budget_usdt,
        reservation_manager=reservation_manager,
        VENUE_SUBMITTERS=VENUE_SUBMITTERS,
        current_environment=current_environment,
        risk_reservation=risk_reservation,
        venue_router=venue_router)

def confirm_signal_reservation(reservation: Dict[str, Any]) -> None:
    """审计④6(2026-09-13)：成交后把预算预留 pending→confirmed（仍占预算直至终态）。
    旧调用点用 except:pass 吞掉了「confirm 方法不存在」的 AttributeError——每次成交必
    抛必吞、状态字段永远说谎。现方法已在 RiskReservationManager 补齐；失败仍不阻断
    交易主流程，但必须可见（预算由 TTL/recovery 兜底对账）。"""
    if not isinstance(reservation, dict):
        return
    try:
        reservation["manager"].confirm(reservation["account_key"], reservation["intent_id"])
    except Exception as exc:
        print(f"[预算预留] warn confirmed 状态推进失败（预算仍占用，对账兜底）: {exc}")


def portfolio_budget_guard(budget_total: float, budget_used: float, margin_est: float,
                           environment: str = "") -> Optional[str]:
    """壳（第八十七刀搬至 `scripts/trader/routing_policy.py`，纯函数）。"""
    return _routing_policy_budget_guard(budget_total, budget_used, margin_est, environment)

def release_signal_reservation(reservation: Dict[str, Any], reason: str = "") -> None:
    """下单未获受理 → 释放本轮预留（终态 rejected，预算即刻回笼）。"""
    if not isinstance(reservation, dict):
        return
    try:
        reservation["manager"].release(reservation["account_key"],
                                       reservation["intent_id"],
                                       state=risk_reservation.STATE_REJECTED)
        print(f"[预算预留] 已释放 {reservation['intent_id']}（{reason or '下单未受理'}）")
    except Exception as exc:
        print(f"[预算预留] warn 释放失败（交由重启 recovery 处理）: {exc}")


#: 预留对账释放 TTL（秒）：现货交易所已不存在且超时 → 终态 closed 回笼预算。
#: 7200s ≈ 8 个 15min 周期——远大于限价单挂单窗口与成交确认窗口，宁慢勿错杀。
RESERVATION_RECONCILE_TTL_S = 7200.0


def _utc_age_seconds(ts_str: str, now_utc: float) -> float:
    """薄壳：转调 `scripts/trader/reservation_reconcile.py`（第五十八刀）。

    ⚠️ 依赖一律调用期注入（本模块会被 `pin_baseline_risk_env()` 原地 reload，
    而重载名单**不含子模块**，import 期绑定会读到旧值）。
    """
    return _rr_utc_age_seconds(ts_str, now_utc)


def reconcile_reservation_ledger(real_pos_dict: Dict[str, Any],
                                 pending_inst_ids: set,
                                 environment: str,
                                 ttl_s: float = None,
                                 venue_snapshot: Optional[Dict[str, list]] = None,
                                 venue_snapshot_verified: bool = True) -> int:
    """薄壳：转调 `scripts/trader/reservation_reconcile.py`（第五十八刀）。

    ⚠️ 位置参数与既有调用**一字未变**（`tests/core/test_reservation_reconcile.py` 用位置参数调用）；
    第一百二十六刀新增 `venue_snapshot_verified`（默认 True ⇒ 老调用方行为不变）。
    四个依赖全部在**调用时**注入 —— 尤其 `reservation_manager` 与
    `fetch_other_venue_positions` 是本模块的模块级名字，测试用
    `patch.object(trader, …)` 替换它们；若子模块 import 期绑一份，
    那 9 条用例会当场翻红（且会真的出网）。
    """
    return _rr_reconcile(
        real_pos_dict,
        pending_inst_ids,
        environment,
        reservation_manager=reservation_manager,
        fetch_other_venue_positions=fetch_other_venue_positions,
        state_closed=risk_reservation.STATE_CLOSED,
        default_ttl_s=RESERVATION_RECONCILE_TTL_S,
        ttl_s=ttl_s,
        venue_snapshot=venue_snapshot,
        # 第一百二十六刀：跨所实况**是否核验成功**必须一路传到对账器 ——
        # 读取失败时 `venue_snapshot` 是空字典，对账器据此会误判"外所无仓无挂"。
        venue_snapshot_verified=venue_snapshot_verified,
    )


def submit_protected_limit_order(inst_id: str, side: str, pos_side: str, size: float, price: float, tp_px: float, sl_px: float, venue_ctx: Optional[Dict[str, Any]] = None) -> Tuple[bool, str]:
    """壳（第八十八刀搬至 `scripts/trader/order_submit.py`，调用期同名注入）。"""
    return _order_submit_protected(
        inst_id, side, pos_side, size, price, tp_px, sl_px, venue_ctx,
        confirm_signal_reservation=confirm_signal_reservation,
        record_open_intent=record_open_intent,
        release_signal_reservation=release_signal_reservation,
        route_and_reserve_signal=route_and_reserve_signal,
        MAX_LEVERAGE=MAX_LEVERAGE,
        MIN_LEVERAGE=MIN_LEVERAGE,
        canonical_base=canonical_base,
        current_environment=current_environment,
        fetch_ticker=fetch_ticker,
        okx_rest=okx_rest,
        venue_registry=venue_registry)


def _float_or_zero(value: Any) -> float:
    try:
        return abs(float(value or 0.0))
    except (TypeError, ValueError):
        return 0.0


def _live_oco_coverage(orders: List[Dict[str, Any]], pos_side: str) -> float:
    """壳（第八十五刀搬至 `scripts/trader/cloud_protection.py`）。"""
    return _cloud_protection_coverage(orders, pos_side, _float_or_zero=_float_or_zero)

def ensure_cloud_position_protection(inst_id: str, pos_side: str, size: float, tp_px: float, sl_px: float) -> Tuple[bool, str]:
    """壳（第八十五刀搬至 `scripts/trader/cloud_protection.py`）。"""
    return _cloud_protection_ensure(
        inst_id, pos_side, size, tp_px, sl_px,
        okx_rest=okx_rest, _live_oco_coverage=_live_oco_coverage)

def build_signal_snapshot(f: dict) -> dict:
    """壳（第八十二刀搬至 `scripts/trader/signal_snapshot.py`）。

    调用期解析 `DATA_DIR` 注入 —— `patch.object(aft, "DATA_DIR", tmp)`
    的既有专测面保真。
    """
    return _signal_snapshot_build(f, data_dir=DATA_DIR)

def _signal_journal_file() -> str:
    """**调用期**解析信号日记路径（与 `build_signal_snapshot(data_dir=DATA_DIR)` 同款）。

    2026-09-16 守卫：模块级 `SIGNAL_JOURNAL_FILE` 是**导入期**绑定，
    `patch.object(aft, "DATA_DIR", tmp)` 这类本仓既有的隔离手法对它无效。
    历史事故：`test_trader_position_exit_extraction` 未替身 `record_signal_snapshot`
    时**真写生产**，累计把 249 条夹具（ETH/2500.0/2026-09-07 10:00:00）写进
    `data/signal_journal.json`（2026-09-16 已清理，备份在 `data/backups/`）。
    该测试后来补了替身（第八十九刀），但**"能真写生产"这个口子必须一起堵**。
    """
    return os.path.join(DATA_DIR, "signal_journal.json")


def record_signal_snapshot(snap: dict) -> None:
    """把开仓时刻的数理快照写入 signal_journal.json，保留最近 500 条供复盘 join。

    2026-09-16：① 路径改为**调用期解析**（见 `_signal_journal_file`），
    令 `patch.object(aft, "DATA_DIR", tmp)` 真正生效；② 落盘改走本文件的
    `_atomic_write_json`（并发读者——面板/复盘/日报——不再可能读到半截 JSON）。
    """
    try:
        journal_file = _signal_journal_file()
        journal = []
        if os.path.exists(journal_file):
            with open(journal_file, "r", encoding="utf-8") as handle:
                journal = json.load(handle)
        journal.append(snap)
        _atomic_write_json(journal_file, journal[-500:])
    except Exception as e:
        print(f"Failed to record signal snapshot: {e}")


def record_trade(trade_data):
    """壳（第八十三刀搬至 `scripts/trader/ledger_writer.py`，调用期同名注入）。"""
    return _ledger_writer_trade(
        trade_data,
        LEDGER_JSON_FILE=LEDGER_JSON_FILE,
        _atomic_write_json=_atomic_write_json,
        record_trade_sqlite=record_trade_sqlite,
        current_environment=current_environment,
        __version__=__version__)
# =============================================================================
# 🧮 Enhanced Quantitative Technical Indicators Math Engine
# =============================================================================
from r20_backend.execution import (
    calc_ema,
    calc_rsi,
    calc_atr,
    calc_macd_histogram_acceleration,
    calc_obv_trend,
    calc_bollinger_squeeze,
)

# =============================================================================
# 🚀 High-Alpha Multi-Factor Extraction & Quantitative Feature Assembly
# =============================================================================
def fetch_single_instrument_data(item, all_positions, usdt_available):
    """装配单个标的的多因子特征。实现见 scripts/trader/factors.py。

    门面保留同名壳：唯一调用点（execute_portfolio 内 executor.map）以及可能的外部
    引用都按全局名查找，调用点无需改动。依赖在**调用时**注入 —— 理由见该模块 docstring。
    """
    return _fetch_single_instrument_data(
        item, all_positions, usdt_available,
        news_sentiment_file=NEWS_SENTIMENT_FILE,
        fetch_candles_direct=fetch_candles_direct,
        instrument_profile=instrument_profile,
        load_adaptive_config=load_adaptive_config,
    )

# =============================================================================
# Trailing Stop & Risk Management
# =============================================================================
def sync_cloud_algo_stop(inst_id: str, pos_side: str, new_sl: float, reason: str = "") -> bool:
    """壳（第八十五刀搬至 `scripts/trader/cloud_protection.py`）。"""
    return _cloud_protection_sync_stop(inst_id, pos_side, new_sl, reason, okx_rest=okx_rest)

def manage_position_tp_and_trailing(f, curr_pos, trackers, timestamp_full, executed_actions):
    """壳（第八十九刀搬至 `scripts/trader/position_exit.py`，调用期同名注入）。"""
    try:
        from scripts.trader.scale_out import execute_scale_out_if_eligible
        execute_scale_out_if_eligible(
            f, curr_pos, trackers, timestamp_full, executed_actions,
            okx_rest=okx_rest,
            venue_registry=venue_registry,
            record_trade=record_trade,
            notify_trade_close=notify_trade_close,
            close_fee=_close_fee,
            close_trade_payload=_close_trade_payload,
            TAKER_FEE_RATE=TAKER_FEE_RATE,
            ensure_cloud_position_protection=ensure_cloud_position_protection,
        )
    except Exception as _so_err:
        print(f"[Scale-Out] 分批止盈判定跳过: {_so_err}")

    return _position_exit_manage(
        f, curr_pos, trackers, timestamp_full, executed_actions,
        _float_or_zero=_float_or_zero,
        add_stop_cooldown=add_stop_cooldown,
        build_signal_snapshot=build_signal_snapshot,
        close_position_confirmed=close_position_confirmed,
        ensure_cloud_position_protection=ensure_cloud_position_protection,
        evaluate_asset_signal=evaluate_asset_signal,
        record_signal_snapshot=record_signal_snapshot,
        record_trade=record_trade,
        sync_cloud_algo_stop=sync_cloud_algo_stop,
        ASSET_CLASS_PROFILES=ASSET_CLASS_PROFILES,
        TAKER_FEE_RATE=TAKER_FEE_RATE,
        TIME_STOP_ATR_BAND=TIME_STOP_ATR_BAND,
        TIME_STOP_HOURS=TIME_STOP_HOURS,
        _close_fee=_close_fee,
        _close_trade_payload=_close_trade_payload,
        notify_trade_close=notify_trade_close,
        protection_signals=protection_signals,
        ratcheted_trailing_stop=ratcheted_trailing_stop)

def execute_ai_position_management(real_pos_dict, trackers, timestamp_full, executed_actions):
    """执行主脑写下的持仓指令。实现与两条安全语义见 scripts/trader/position_mgmt.py。

    注入项**调用期**从门面取值：`AI_POSITION_MANAGEMENT_FILE` 是既有测试缝
    （测试会 patch 门面属性），其余是执行层函数与配置常量。
    """
    return _execute_ai_position_management_impl(
        real_pos_dict, trackers, timestamp_full, executed_actions,
        ai_position_management_file=AI_POSITION_MANAGEMENT_FILE,
        ai_tightens_stop=ai_tightens_stop,
        close_position_confirmed=close_position_confirmed,
        okx_rest=okx_rest,
        venue_registry=venue_registry,
        current_environment=current_environment,
        amend_venue_stop_loss=amend_venue_stop_loss,
    )

# =============================================================================
# 🧠 R20 Quantum Trader v6.8.1 Multi-Factor Scoring & Strategy Setup Classifier
# =============================================================================
def evaluate_asset_signal(f):
    """连续多因子量化评分（-5.0 ~ +5.0）。实现见 scripts/trader/signals.py。

    门面保留同名壳：本函数在文件内的 3 处调用点（以及测试里的
    `ai_factor_trader.evaluate_asset_signal(f)`）都按全局名查找，故调用点无需改动。
    依赖在**调用时**注入，而不是被子模块 import 期烘焙 —— 详见该模块 docstring
    （`pin_baseline_risk_env()` 的重载名单不含子模块，import 期绑定会让基线风控测试翻红）。
    """
    return _evaluate_asset_signal(
        f,
        asset_class_profiles=ASSET_CLASS_PROFILES,
        is_in_stop_cooldown=is_in_stop_cooldown,
        load_adaptive_config=load_adaptive_config,
    )

def _slot_guard_should_skip(now_slot: int) -> bool:
    """同槽重复触发守卫：**判定 + 记录**（第一百三十六刀从装饰器内联抽出）。

    为什么抽出来：它原先内联在 `single_trader_cycle` 的装饰器里，本会话发现它
    有两处该钉的语义却**无法单独测试**：

    1. **写必须是原子的**：旧写法 `open(TRADER_SLOT_FILE, "w")` 先截断再写 —— 写崩/
       断电会留下 0 字节或半截 JSON，而读取分支把它吞进 `except: pass`
       ⇒ **同槽去重静默失效**（同一 15 分钟槽可能跑两轮，重复处理同一批信号）。
       现改走本模块既有的 `_atomic_write_json`（失败时旧文件原样保全）。
    2. **读不到 ≠ 没有状态**：仍**放行**（不因一个状态文件把实盘交易停掉），
       但必须**吼出来**——旧写法静默 `pass`，损坏时外面看不出任何异常。

    返回 True = 判为同槽重复触发（调用方应 Skip 本周期）。
    """
    if os.path.exists(TRADER_SLOT_FILE):
        try:
            with open(TRADER_SLOT_FILE, "r", encoding="utf-8") as f:
                slot_state = json.load(f)
            same_slot = int(slot_state.get("slot", -1)) == now_slot
            recently_started = int(time.time()) - int(slot_state.get("started_at", 0) or 0) < 120
            if same_slot and recently_started:
                print("[Trader] Skip: duplicate trigger detected in this 15-minute slot")
                return True
        except Exception as _slot_exc:
            print(f"[Trader] warn 同槽去重状态不可读（{_slot_exc!r}）——本轮**无法判定**是否"
                  f"同槽重复触发，仍按正常流程执行（请检查 "
                  f"{os.path.basename(TRADER_SLOT_FILE)}）")
    _atomic_write_json(TRADER_SLOT_FILE,
                       {"slot": now_slot, "started_at": int(time.time()), "pid": os.getpid()})
    return False


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
            # 判定 + 记录走助手（原子写 + 读失败告警；见其 docstring）
            if _slot_guard_should_skip(now_slot):
                return None
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
    _preflight = preflight_reconcile_and_housekeeping(
        WORKSPACE_DIR=WORKSPACE_DIR,
        _run_captured=_run_captured,
        clean_stale_open_orders=clean_stale_open_orders,
        current_environment=current_environment,
        datetime=datetime,
        load_trackers=load_trackers,
        os=os,
        reconcile_pending_orders=reconcile_pending_orders    )
    if _preflight is None:
        return None
    entries_blocked, timestamp_full = _preflight

    # 0.5 只读形状预检：把"读得到但会被静默忽略"的形状问题**尽早指名道姓**
    #     （只警告、不阻断 —— 行为判定在加载侧：意图 fail-closed、追踪器拒绝覆盖）
    _shape_violations = data_shape_preflight_stage(
        intents_path=OPEN_INTENT_FILE,
        trackers_path=POSITION_TRACKER_FILE,
        validate_intents_file=validate_intents_file,
        validate_trackers_file=validate_trackers_file)

    # 1. Fetch Real Positions. A failed account query aborts the complete cycle.
    _phase1 = fetch_positions_and_reconcile(
        entries_blocked=entries_blocked,
        _BROKEN_VENUES=_BROKEN_VENUES,
        collect_pending_inst_ids=collect_pending_inst_ids,
        current_environment=current_environment,
        fetch_other_venue_positions=fetch_other_venue_positions,
        load_instruments=load_instruments,
        okx_rest=okx_rest,
        query_positions=query_positions,
        reconcile_reservation_ledger=reconcile_reservation_ledger,
        venue_execution_ready=venue_execution_ready,
        broken_execution_venues=broken_execution_venues,
        venue_registry=venue_registry    )
    if _phase1 is None:
        return None
    (_xv_total, active_pos_count, all_positions, entries_blocked, long_count, pending_inst_ids, real_pos_dict, reserved_long_count, reserved_short_count, reserved_slot_count, short_count, usdt_available, xv_positions_by_venue) = _phase1

    # 2. Parallel fetch for the configured crypto universe
    all_factors, executed_actions, trackers = fetch_universe_and_manage_positions(
        all_positions=all_positions,
        real_pos_dict=real_pos_dict,
        timestamp_full=timestamp_full,
        usdt_available=usdt_available,
        TARGET_INSTRUMENTS=TARGET_INSTRUMENTS,
        ThreadPoolExecutor=ThreadPoolExecutor,
        fetch_single_instrument_data=fetch_single_instrument_data,
        load_trackers=load_trackers,
        manage_position_tp_and_trailing=manage_position_tp_and_trailing,
        prune_trackers=prune_trackers,
        save_trackers=save_trackers    )

    # 4. Check Circuit Breaker & Batch AI Brain Scan (Including Active Positions Detail)
    ASSET_MARGIN_CAP, brain_cache, cb_active, cb_reason = scan_risk_gates_and_ai_brain(
        _xv_total=_xv_total,
        active_pos_count=active_pos_count,
        all_factors=all_factors,
        executed_actions=executed_actions,
        long_count=long_count,
        short_count=short_count,
        timestamp_full=timestamp_full,
        trackers=trackers,
        usdt_available=usdt_available,
        xv_positions_by_venue=xv_positions_by_venue,
        MAX_CONCURRENT_POSITIONS=MAX_CONCURRENT_POSITIONS,
        _collect_okx_position_payloads=_collect_okx_position_payloads,
        _merge_cross_venue_positions=_merge_cross_venue_positions,
        effective_single_asset_margin=effective_single_asset_margin,
        execute_ai_position_management=execute_ai_position_management,
        execute_batch_ai_brain_cycle=execute_batch_ai_brain_cycle,
        is_circuit_breaker_active=is_circuit_breaker_active,
        pool_is_trustworthy=pool_is_trustworthy,
        pool_state=pool_state,
        query_positions=query_positions,
        read_cycle_health=read_cycle_health,
        save_trackers=save_trackers    )

    if not cb_active and pool_is_trustworthy():
        execute_entry_scan(
            all_factors=all_factors,
            brain_cache=brain_cache,
            cb_active=cb_active,
            entries_blocked=entries_blocked,
            executed_actions=executed_actions,
            pending_inst_ids=pending_inst_ids,
            trackers=trackers,
            usdt_available=usdt_available,
            ASSET_MARGIN_CAP=ASSET_MARGIN_CAP,
            reserved_long_count=reserved_long_count,
            reserved_short_count=reserved_short_count,
            reserved_slot_count=reserved_slot_count,
            ASSET_CLASS_PROFILES=ASSET_CLASS_PROFILES,
            MAX_CONCURRENT_POSITIONS=MAX_CONCURRENT_POSITIONS,
            MAX_LEVERAGE=MAX_LEVERAGE,
            MAX_SAME_DIRECTION_POSITIONS=MAX_SAME_DIRECTION_POSITIONS,
            MAX_SCALE_IN_COUNT=MAX_SCALE_IN_COUNT,
            MIN_ENTRY_CONFIDENCE=MIN_ENTRY_CONFIDENCE,
            MIN_LEVERAGE=MIN_LEVERAGE,
            MIN_SCALE_IN_CONFIDENCE=MIN_SCALE_IN_CONFIDENCE,
            MIN_SCALE_IN_PROFIT_RATIO=MIN_SCALE_IN_PROFIT_RATIO,
            build_order_intent=build_order_intent,
            clamp_ai_leverage=clamp_ai_leverage,
            entry_action_message=entry_action_message,
            entry_failure_message=entry_failure_message,
            equity_margin_cap=equity_margin_cap,
            evaluate_asset_signal=evaluate_asset_signal,
            instrument_profile=instrument_profile,
            is_tradfi_market_liquid=is_tradfi_market_liquid,
            load_adaptive_config=load_adaptive_config,
            max_size_within_margin=max_size_within_margin,
            normalize_bracket_prices=normalize_bracket_prices,
            notify_trade_open=notify_trade_open,
            order_margin_gate=order_margin_gate,
            pyramiding_gate=pyramiding_gate,
            quantize_size=quantize_size,
            resolve_entry_prices=resolve_entry_prices,
            save_trackers=save_trackers,
            size_for_decision=size_for_decision,
            submit_protected_limit_order=submit_protected_limit_order,
            trade_open_kwargs=trade_open_kwargs,
        )

    # 4b. 跨所云端保护单巡检（roadmap G8）：Gate/Binance 的触发单带 expiration，
    # 到期后仓位裸奔，而主链的 OKX 保护核验够不到跨所仓位（合成 id 匹配不上）。
    # **默认关闭**（R20_VENUE_PROTECTION_WATCHDOG=1 才跑）：本刀只接线，线上行为零变化。
    # 开闸前先用 `R20_VENUE_PROTECTION_WATCHDOG_DRY_RUN=1` 预演一轮：照常判定但不写单，
    # 日志逐条给出"本来会做"的动作（见该函数 docstring）。
    _wd_report = venue_protection_watchdog_stage(
        xv_positions_by_venue=xv_positions_by_venue,
        executed_actions=executed_actions,
        venue_registry=venue_registry,
        current_environment=current_environment,
        R20_VENUE_PROTECTION_WATCHDOG=R20_VENUE_PROTECTION_WATCHDOG,
        # 第一百二十九刀：预演模式（总闸未开时无意义）——开闸前先看"会做什么"。
        dry_run=R20_VENUE_PROTECTION_WATCHDOG_DRY_RUN,
        # 第一百三十刀：防抖 —— 缺口必须持续够久才允许真实写单（状态不可读写则不写单）。
        state_path=VENUE_PROTECTION_WATCHDOG_STATE_FILE,
        debounce_s=R20_VENUE_PROTECTION_WATCHDOG_DEBOUNCE_S,
        debounce_step=watchdog_debounce_step,
        audit_cross_venue_protection=audit_cross_venue_protection,
        # 第一百七十四刀：台账行（只读；读不到 ⇒ None ⇒ 不产生 ledger 证据）
        ledger_rows=read_ledger_rows(LEDGER_JSON_FILE),
    )

    # 4c. 行情取数健康快照（第 137 刀事故的可观测性闭环）：把本轮的取数
    # 失败计数/耗时/最近成功时刻落盘，供后端 `/metrics` 跨进程读取。
    # 只写一个 JSON、失败只返回 False（绝不抛异常、绝不改变交易行为）。
    write_market_data_health_snapshot(path=MARKET_DATA_HEALTH_FILE)

    # 5. Persist Latest State for Web Monitoring Dashboard
    persist_state_and_sync_ledger(
        _xv_total=_xv_total,
        active_pos_count=active_pos_count,
        all_factors=all_factors,
        cb_active=cb_active,
        cb_reason=cb_reason,
        executed_actions=executed_actions,
        long_count=long_count,
        short_count=short_count,
        timestamp_full=timestamp_full,
        DATA_DIR=DATA_DIR,
        LEDGER_AUTOSYNC_ENABLED=LEDGER_AUTOSYNC_ENABLED,
        LOG_FILE=LOG_FILE,
        MAX_CONCURRENT_POSITIONS=MAX_CONCURRENT_POSITIONS,
        WORKSPACE_DIR=WORKSPACE_DIR,
        __version__=__version__,
        _atomic_write_json=_atomic_write_json,
        _run_captured=_run_captured,
        build_state_payload=build_state_payload,
        evaluate_asset_signal=evaluate_asset_signal,
        os=os    )

    # 6. 周期披露汇总（第 50/51 刀）：每轮必须留下**一条可检索**的"跳过/未核验"行，
    #    并把同一份载荷原子落盘给后端 /metrics（跨进程可观测：读不到 ≠ 没有）
    _disc = cycle_disclosure_payload(
        broken_venues=_BROKEN_VENUES,
        entries_blocked=entries_blocked,
        shape_violations=_shape_violations,
        watchdog_report=_wd_report,
        watchdog_enabled=R20_VENUE_PROTECTION_WATCHDOG)
    print(cycle_disclosure_summary(_disc))
    write_cycle_disclosure_snapshot(
        path=CYCLE_DISCLOSURE_FILE, payload=_disc,
        _atomic_write_json=_atomic_write_json)

if __name__ == "__main__":
    if not selected_environment().configured:
        print("[Engine NOT READY] OKX API Key 未配置（LIVE/DEMO）——退出，不执行任何交易。")
        sys.exit(3)
    execute_portfolio()
