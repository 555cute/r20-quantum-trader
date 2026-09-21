#!/usr/bin/env python3
"""
R20 AI Brain Six-Crypto Quantitative Trading Decision Engine (ai_brain_trader.py)
Batch ingests six crypto perpetuals into one macro-context LLM call.
Maintains a validated live decision cache and durable Web audit history.
"""

import os
import sys
from pathlib import Path

from r20_backend.math_utils import safe_float as _shared_safe_float

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = Path(PROJECT_ROOT)
SCRIPTS_DIR = os.path.join(PROJECT_ROOT, "scripts")
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

import scripts.okx_rest as okx_rest
# 风控提示词与执行层共用单一事实源，防止「提示词口径 vs 代码口径」漂移
from risk_constants import (
    DAILY_LOSS_EQUITY_RATIO,
    MAX_CONCURRENT_POSITIONS_CAP,
    MAX_DAILY_LOSS_USDT,
    MAX_LEVERAGE,
    MIN_LEVERAGE,
    MAX_MARGIN_EQUITY_RATIO,
    MAX_SAME_DIRECTION_POSITIONS,
    MAX_SCALE_IN_COUNT,
    MAX_SINGLE_ASSET_MARGIN,
    MIN_ENTRY_CONFIDENCE,
    MIN_RISK_REWARD_RATIO,
    MIN_SCALE_IN_CONFIDENCE,
    MIN_SCALE_IN_PROFIT_RATIO,
    PORTFOLIO_RISK_BUDGET_USDT,
    RISK_PER_TRADE_EQUITY_RATIO,
    SINGLE_ASSET_EQUITY_RATIO,
    STOP_COOLDOWN_MINUTES,
    TIME_STOP_ATR_BAND,
    TIME_STOP_HOURS,
    effective_daily_loss_limit,
    effective_single_asset_margin,    MAX_TOTAL_EXPOSURE_USDT,
)
import json
import time
import datetime
import urllib.request
import subprocess
import tempfile
import fcntl
from typing import Dict, Any, List, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    from r20_backend.config import settings as standalone_settings
except ImportError:
    standalone_settings = None

try:
    from r20_backend.version import __version__
except Exception:
    __version__ = "7.6.0"


def _get_system_version_tag() -> str:
    return f"v{__version__}"

WORKSPACE_DIR = PROJECT_ROOT
DATA_DIR = os.path.join(WORKSPACE_DIR, "data")
from market_data_service import fetch_single_indicator, fetch_ticker, fetch_candles
# 结构优化阶段4·B3：单标的数据包装配已搬入 scripts/brain/packages.py（门面保留薄壳）
from scripts.brain.packages import fetch_single_instrument_package as _fetch_single_instrument_package
# 结构优化阶段4·B3 第二块：跨所采集/健康度/提示词组装已搬入 scripts/brain/xvenue.py。
# 依赖面较宽（适配器缝、safe_float、VENUE_HEALTH_FILE、atomic_write_json、_XV_HEALTH），
# 全部走**调用期注入**，理由见该模块 docstring 与 r20_backend/README.md §5。
from scripts.brain.prompt import (
    construct_full_market_prompt as _construct_full_market_prompt_impl,
)
# 第三十刀：在途持仓/挂单文本装配搬入 account_text，按调用期注入（同名参数解析陷阱见
# construct_full_market_prompt 的 docstring）。
from scripts.brain.account_text import (
    build_position_lines as _build_position_lines,
    build_pending_order_lines as _build_pending_order_lines,
)
from scripts.brain.runtime import (
    capture_policy_snapshot,
    resolve_llm_runtime,
)
from scripts.brain.snapshots import (
    update_factor_library_snapshot,
    write_calculus_snapshot,
    write_prompt_snapshot,
)
from scripts.brain.dispatch import (
    dispatch_llm_and_persist_decisions,
)
from scripts.brain.cycle_parts import (
    normalize_position_management as _normalize_position_management,
    build_effective_prompt_text as _build_effective_prompt_text,
    build_history_record as _build_history_record,
)
from scripts.brain.decisions import (
    validate_and_filter_decision as _validate_and_filter_decision_impl,
    assemble_decision_cache as _assemble_decision_cache_impl,
)
from scripts.brain.xvenue import (
    _xvenue_enabled as _xvenue_enabled_impl,
    _xv_record as _xv_record_impl,
    _xv_flush_health as _xv_flush_health_impl,
    _get_xvenue_adapter as _get_xvenue_adapter_impl,
    _xv_binance_snapshot as _xv_binance_snapshot_impl,
    _xv_gate_snapshot as _xv_gate_snapshot_impl,
    fetch_cross_venue_matrix as _fetch_cross_venue_matrix_impl,
    _xv_divergence_notes as _xv_divergence_notes_impl,
    _xvenue_prompt_line as _xvenue_prompt_line_impl,
)
AI_DECISION_CACHE_FILE = os.path.join(DATA_DIR, "ai_brain_decisions.json")
AI_DECISION_HISTORY_FILE = os.path.join(DATA_DIR, "ai_brain_history.json")


def _ai_health_path() -> str:
    # 调用时解析 DATA_DIR——测试 patch 模块属性即封闭（律①）
    return os.path.join(DATA_DIR, "ai_health.json")


def read_cycle_health() -> dict:
    """供 trader/面板读取最近批次健康；缺文件=无记录（不误伤）。"""
    try:
        p = _ai_health_path()
        if not os.path.exists(p):
            return {}
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _record_cycle_health(status: str, reason: str = "") -> None:
    """审计监控面：trader 为 15 分钟短驻进程，内存计数跨轮即失忆——连续失败
    计数持久化到 data/ai_health.json。04:45 起 14 轮 LLM 停摆但巡检 rc=0 全绿
    的根因就是失败终态没有任何跨进程可查痕迹。>=2 连续失败由 data_health 降
    PARTIAL、由 trader 在 executed_actions 显式告警。"""
    prev = read_cycle_health()
    now_iso = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).isoformat()
    if status == "ok":
        payload = {
            "last_status": "ok", "last_at": now_iso, "consecutive_failures": 0,
            "last_ok_at": now_iso, "last_error": None,
            "total_failures": int(prev.get("total_failures", 0) or 0),
        }
    else:
        payload = {
            "last_status": "failed", "last_at": now_iso,
            "consecutive_failures": int(prev.get("consecutive_failures", 0) or 0) + 1,
            "last_error": str(reason)[:300], "last_ok_at": prev.get("last_ok_at"),
            "total_failures": int(prev.get("total_failures", 0) or 0) + 1,
        }
    try:
        atomic_write_json(_ai_health_path(), payload)
    except Exception as exc:
        print(f"[AI Brain Batch] warn ai_health 旁车写入失败: {exc}")
AI_POSITION_MANAGEMENT_FILE = os.path.join(DATA_DIR, "ai_position_management.json")
AI_LAST_PROMPT_FILE = os.path.join(DATA_DIR, "ai_brain_last_prompt.txt")
VENUE_HEALTH_FILE = os.path.join(DATA_DIR, "venue_health.json")
FACTOR_LIBRARY_FILE = os.path.join(DATA_DIR, "factor_library_snapshot.json")
NEWS_SENTIMENT_FILE = os.path.join(DATA_DIR, "news_sentiment.json")
AI_MEMORY_MD_FILE = os.path.join(DATA_DIR, "AI_TRADING_MEMORY.md")
CALCULUS_SNAPSHOT_FILE = os.path.join(DATA_DIR, "calculus_snapshot.json")
AI_MEMORY_FILE = os.path.join(DATA_DIR, "ai_trading_memory.json")
PROMPT_OVERRIDE_FILE = os.path.join(DATA_DIR, "system_prompt_override.txt")
AI_BRAIN_LOCK_FILE = os.path.join(DATA_DIR, ".ai_brain_cycle.lock")
DECISION_MAX_AGE_SECONDS = 300

from r20_backend.version import __version__
from instrument_pool import load_instruments
from prompt_library import active_profile, append_layer, apply_module_layout
from r20_gateway.telemetry import ModelCallTelemetry
from llm_credentials import get_cpa_client_config as _get_cpa_client_config  # noqa: E402

TARGET_INSTRUMENTS = load_instruments()

try:  # 跨所符号归一（审计 P2-12）：把 BINANCE:BTCUSDT / BTC_USDT / BTC 统一成 OKX 形态
    from r20_backend.exchanges.base import canonical_base as _canonical_base_name
except Exception:  # pragma: no cover - scripts/ 直接运行时走兜底
    try:
        from exchanges.base import canonical_base as _canonical_base_name  # type: ignore
    except Exception:
        def _canonical_base_name(symbol: str) -> str:
            s = str(symbol or "").strip().upper()
            for marker in ("-USDT-SWAP", "USDT", "_USDT", "-USDT"):
                if s.endswith(marker):
                    s = s[: -len(marker)]
                    break
            return s.replace("-", "").replace("_", "")


def atomic_write_json(path: str, payload: Any) -> None:
    """Replace JSON atomically so readers never observe a partial cache."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=".ai-brain-", suffix=".tmp", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


def single_brain_cycle(func):
    """Prevent overlapping cron runs from overwriting the shared decision cache."""
    def wrapped(*args, **kwargs):
        os.makedirs(DATA_DIR, exist_ok=True)
        lock_handle = open(AI_BRAIN_LOCK_FILE, "a+", encoding="utf-8")
        try:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            lock_handle.close()
            print("[AI Brain Batch] Skip: another inference cycle is still running")
            return None
        try:
            lock_handle.seek(0)
            lock_handle.truncate()
            lock_handle.write(str(os.getpid()))
            lock_handle.flush()
            return func(*args, **kwargs)
        finally:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
            lock_handle.close()
    return wrapped


def safe_float(value: Any, default: float = 0.0) -> float:
    """薄壳：转调单一事实源（`r20_backend.math_utils.safe_float`，第一百五十刀）。

    语义与既有实现逐条一致（`nan`/`±inf`/不可转 ⇒ `default`；`bool` 按 `float()`）——
    只是不再各写一份（三份等价实现的漂移代价是"因子与风控静默算出不同的数"）。
    """
    return _shared_safe_float(value, default)


def is_same_direction_scale_request(position_side: str, action: str) -> bool:
    """Allow only same-direction scale-in requests to reach execution hard gateways."""
    side = str(position_side or "").lower()
    decision = str(action or "").upper()
    return (side == "long" and decision == "BUY_LONG") or (side == "short" and decision == "SELL_SHORT")


def get_cpa_client_config() -> Tuple[str, str]:
    """薄壳：调用时解析门面全局，使测试的 patch / 直接赋值生效。

    实现已迁往 r20_backend.llm.credentials（结构优化阶段 4·B3 第四十六刀）。
    ⚠️ `standalone_settings` 必须**在这里**读取后传入 —— 门面全局会被测试
    patch / 原地 reload，子模块 import 期绑定会读到陈旧副本。
    """
    return _get_cpa_client_config(standalone_settings)

def read_prompt_override() -> str:
    """读取管理员提示词覆盖层（不存在/不可读 → 空串，绝不抛）。"""
    try:
        if os.path.exists(PROMPT_OVERRIDE_FILE):
            with open(PROMPT_OVERRIDE_FILE, "r", encoding="utf-8") as handle:
                return handle.read().strip()
    except OSError:
        pass
    return ""


# 止损基准（审计 P2-5）：池条目 per-instrument 值优先——与 ai_factor_trader.instrument_profile
# 同一优先级；TRADFI 等不在池内的标的回落到资产类别档（数值与 ai_factor_trader.
# ASSET_CLASS_PROFILES 逐项对齐，tests/audit/test_audit_config_p4_cleanup.py 有源码钉守着）。
_SL_ATR_BY_ASSET_CLASS = {"commodity": 1.3, "index": 1.2, "stock": 1.3, "crypto": 1.4}


def _prefer_pool_inst(candidate: str, current: str) -> bool:
    """同币多合约时的**确定性**优选（顺序无关）：USDT 永续优先，其次字典序更小。

    为什么需要它：`setdefault` 的"首值优先"会把选择权交给 `TARGET_INSTRUMENTS` 的排列顺序，
    那是**静默的任意选择**（改一行配置就换了合约）。本函数让它可解释、可复现。
    """
    cand_swap = str(candidate).endswith("-USDT-SWAP")
    curr_swap = str(current).endswith("-USDT-SWAP")
    if cand_swap != curr_swap:
        return cand_swap
    return str(candidate) < str(current)


def canonical_position_inst_id(raw: Any) -> str:
    """跨所持仓符号 → OKX 形态（审计 P2-12，模块级便于直接测试）。

    规则：池内币种映射回池内 instId；标准 USDT 永续写法补齐成 OKX 形态；
    其余（日期合约/币本位/不认识的写法）原样保留——绝不假装认识。
    """
    text = str(raw or "").strip().upper()
    if not text:
        return ""
    bare = text.split(":")[-1]
    # 第一百八十三刀：这里原本是 `pool_by_base.setdefault(base, iid)` —— **首值优先**，
    # 于是"同一个币有多个池内合约"时选哪个**取决于 TARGET_INSTRUMENTS 的顺序**（静默的
    # 任意选择；增删一个条目就会换合约，进而换下单标的）。真机核对：当前 9 个目标合约
    # **同币重复为 0**，所以这是潜在风险而非现行错误。改成**与顺序无关的确定性优选**：
    #   1) 优先标准 USDT 永续（`BASE-USDT-SWAP`）；
    #   2) 其余按字典序取最小。
    pool_by_base: Dict[str, str] = {}
    for item in (TARGET_INSTRUMENTS if isinstance(TARGET_INSTRUMENTS, list) else []):
        iid = str((item or {}).get("instId") or "").strip().upper()
        if not iid:
            continue
        base = _canonical_base_name(iid)
        current = pool_by_base.get(base)
        if current is None or _prefer_pool_inst(iid, current):
            pool_by_base[base] = iid
    base = _canonical_base_name(bare)
    # 第一百八十五刀：`canonical_base` 修好"非 USDT 计价"的提取后（`BTC-USDC` → `BTC`、
    # `BTC-USD-SWAP` → `BTC`），**池查找必须加一道"标准形态"闸**，否则币本位/日期合约
    # 会因为币种相同而被映射到池内的 **USDT 永续**（`BTC-USD-SWAP` → `BTC-USDT-SWAP`）——
    # 那是**换了下单标的**，直接违背本函数"其余原样保留，绝不假装认识"的契约
    # （既有用例 `test_unknown_forms_are_preserved_verbatim` 当场判红，救回一刀）。
    standard = bool(base) and bare in (base, f"{base}USDT", f"{base}_USDT",
                                       f"{base}-USDT", f"{base}-USDT-SWAP")
    if standard and base in pool_by_base:
        return pool_by_base[base]
    if standard:
        return f"{base}-USDT-SWAP"
    return text


def _sl_atr_mult_for(package: Dict[str, Any]) -> float:
    """提示词里展示的止损基准＝执行层真正会用的那个数（不再硬编码 1.5~2.0x）。"""
    raw = (package or {}).get("sl_atr_mult")
    try:
        value = float(raw)
        if value > 0:
            return value
    except (TypeError, ValueError):
        pass
    name = str((package or {}).get("name") or "").strip().upper()
    for item in (TARGET_INSTRUMENTS if isinstance(TARGET_INSTRUMENTS, list) else []):
        if str((item or {}).get("name") or "").strip().upper() == name:
            try:
                pooled = float((item or {}).get("sl_atr_mult"))
                if pooled > 0:
                    return pooled
            except (TypeError, ValueError):
                pass
            break
    return float(_SL_ATR_BY_ASSET_CLASS.get(str((package or {}).get("type") or "crypto"), 1.4))


def get_effective_system_prompt(profile: Dict[str, Any] = None, context: Dict[str, Any] = None) -> str:
    """模型真正收到的 System Prompt = 模块布局(SYSTEM_PROMPT) → **之后**再追加管理员覆盖层。

    审计 P1-3(2026-09-13)：旧实现把覆盖层拼在 SYSTEM_PROMPT 尾部再交给
    `apply_module_layout(..., "trading_system", ...)`——而该布局只输出被列名的模块，
    未列名的 base 段（正是这段覆盖层）被直接丢弃，且 fail-closed 只对 trading_user 生效。
    于是 UI 承诺"下一次 AI 推演循环将自动叠加此提示词覆盖层"，模型却从未收到过。
    现在覆盖层在布局**之后**拼接，顺序与 UI 呈现一致；接口侧同样调用本函数，
    保证「看到的」= 「模型收到的」。
    """
    prof = profile if isinstance(profile, dict) and profile else active_profile()
    effective = apply_module_layout(
        SYSTEM_PROMPT, prof, "trading_system", f"{prof.get('name', '稳健')}交易系统提示词模板", context=context
    )
    override = read_prompt_override()
    if override:
        effective = f"{effective}\n\n【管理员提示词覆盖层（同样必须遵守上述风控和 JSON 约束）】\n{override}"
    return effective


def fetch_single_instrument_package(item: Dict[str, Any]) -> Dict[str, Any]:
    """装配单标的数据包。实现见 scripts/brain/packages.py。

    门面保留同名壳：调用点（`execute_batch_ai_brain_cycle` 里的线程池提交）
    与其他模块的引用都按全局名查找，故调用点无需改动。
    两个行情函数在**调用时**注入，而不是被子模块 import 期烘焙 ——
    `pin_baseline_risk_env()` 的重载名单不含子模块，import 期绑定会让
    `patch.object(门面, "fetch_candles")` 失效。详见该模块 docstring。
    """
    return _fetch_single_instrument_package(
        item,
        fetch_candles=fetch_candles,
        fetch_single_indicator=fetch_single_indicator,
    )

# ── SYSTEM_PROMPT · v7.6 优质预设基线 ──────────────────────────────────────
# 设计契约：
# 1) 分节标题与 data/prompt_library.json 的 trading_system 布局 8 个 base 模块一一对应——
#    标题即接口，线上布局按标题实时取用本代码最新文本，杜绝快照漂移；
# 2) 全部风控数值由 scripts/risk_constants.py 插值（后台风控管理页写入 .env，下一巡检周期生效），
#    保证「提示词口径 == 执行层口径」，模型永远不会被告知过期规则；
# 3) JSON 契约段含花括号，作为独立普通字符串，不参与 format 插值。
_SYSTEM_CORE = """==== 【系统角色定位与核心使命】 ====
你是 R20 Quantum Trader 的首席 AI 交易官，负责 1H~4H 加密合约多空双向波段的高胜率交易裁决。你的使命按优先级排列：
1. 捍卫本金：单笔风险有界、日亏有熔断、敞口有上限，任何单笔损失都不得伤及账户根基；
2. 捕捉正期望：只在数学期望为正（概率优势 × 盈亏比 > 摩擦成本）的机会上下注，用高确定性波段积累复利；
3. 拒绝懈怠与恐惧：当空仓且存在至少一个合法顺势候选时（符合顺势高胜率形态）并通过全部硬门禁，必须果断在候选标的池中选优输出限价进场指令，不得无故放弃合规机会——空仓不是风控，无优势硬开才是风险。
一切金额类参数（保证金、风险额、熔断线）一律以每轮用户消息中【本周期风险预算】小节的实时推导值为准，严禁引用或臆想任何固定绝对金额。

==== 【核心军规：反割肉·反磨损·选优开单五大铁律】 ====
1. 宽止损隔绝杂波：止损必须放在市场结构失效点之外，距离 1.8x~2.2x 1H ATR（或现价外 1.8%~3.0% 安全垫）。严禁把止损设在 15M/5M 噪音区间被插针扫损；宁可压低杠杆与保证金，也绝不压缩止损呼吸空间。
2. 三阶利润棘轮（兼顾波段奔跑与胜率锁定，杜绝赢小输大）：
   阶梯1（浮盈 < 1.0R）：保持原宽止损给波段充分展开时间，禁止微小浮盈过早提至成本位被杂波扫出；
   阶梯2（浮盈 ≥ 1.5R 且 ROI ≥ +2.2%）：输出 UPDATE_SL 将止损移至保本位（开仓成本 +0.20%），彻底切断本金风险；
   阶梯3（浮盈 ≥ 2.2R 且 ROI ≥ +3.5%）：输出 UPDATE_SL 锁定成本上方至少 +1.0R，扎实锁定波段核心利润。
   主动止盈三道防线（兼顾大波段奔跑与落袋防倒亏）：① 峰值回撤——最高浮盈曾达 ROI ≥ +3.5% 或 ≥ 1.8R，当前浮盈较极值回撤超 45%~55% 且 1H 动能明显破位时，果断 CLOSE_MARKET 或紧贴现价 UPDATE_SL 锁定剩余利润，严禁在微幅浮盈（<1.5R）的正常日内回踩中恐慌砸盘提前出局；② 动能耗散——浮盈充沛（ROI ≥ +2.5%）下 1H 做功功率 Φ = v · a < -0.15 且曲率 κ ≥ 1.8（高位急刹车力竭、长上影假突破受挫）时，提前落袋为安，死等极远挂单是禁止行为；③ 阻力锚定——止盈价优先锚定前方关键阻力/支撑位或 2.0~2.8x ATR 可达位，确保实现高盈亏比正期望，充分享受波段主浪溢价。
3. 敞口纪律（执行层硬拦截，不得试探边界）：
   - 全系统同向持仓上限、单笔保证金占比硬顶、杠杆上限与当日亏损熔断线，一律以每轮用户消息【本周期风险预算】的实时声明为准（执行层硬拦截，不得试探边界）；同向已有 2 笔时，新开同向单的置信度必须自律提升至 85% 以上；严禁在 BTC/ETH/SOL/DOGE 等高相关标的上无节制同向堆叠单边敞口；
   - 标的一旦止损出局，【本周期风险预算】声明的冷静期分钟数内不得再申请该标的，严禁情绪化盲目反手；开仓逻辑必须能在声明的最长持仓时间（时间止损）量级内兑现——超时横盘仓位将被执行层强制离场，禁止寄希望于死扛。
4. 选优开单契约：空仓且候选池存在合法顺势形态时，从概率期望与微积分动能最优的标的中果断输出 BUY_LONG 或 SELL_SHORT 限价单；置信度自信标定：形态达标且空间充足时，按【本周期风险预算】给出的置信度标定带给值（低于该带下沿＝低于执行层门禁的报价会被物理拦截，绝不试探）；只有全部候选均触发明确硬否决或优势不足时才全体 WAIT。目标 R:R 与绝对盈亏比底线一律以【本周期风险预算】声明的目标盈亏比/硬底线为准。
5. 反磨损意识：入场优先用 Maker 限价单锚定支撑/阻力位附近，拒绝市价追单；震荡市拒绝为 1% 以内微小差价支付手续费与滑点。

==== 【决策优先级：高层级永远覆盖低层级】 ====
P0 不可覆盖硬约束：数据有效性核验、交易执行层 Fail-Closed、4H 方向否决、真实价格几何合法性、R:R 盈亏比硬底线、杠杆/保证金/持仓数上限、云端 OCO 全覆盖、禁止逆势补仓、严格 JSON 契约。
P1 核心方向证据（最高权重）：4H 宏观结构与 1H 三大数理基石硬证据（延续/击穿概率、微积分速度 v 与加速度 a、能量积分 E）。
P2 质量确认：1H ADX 趋势强度（ADX 18~22 小仓参与，ADX < 18 严禁半山腰开仓）、量能/OI 异动、聪明钱资金流向与衍生品持仓结构。
P3 执行定位：15M K线、盘口与 Maker 限价挂单位置。P3 优化入场成本，不能单独改变 P1 方向。
不得把“稳健”解释为长期空仓，更不得被解释成“只有完美共振才允许交易”。“减速”不是永久禁令：在 4H 顺势大浪中普通回抽优先作为打折买点与限价入场定位。当市场出现【顺势回踩确认】、【弱势反弹承压】或【箱体边界极值超伸回归】时，必须果断给出精准限价挂单决策。P2/P3 的轻微分歧应通过减小保证金处理，绝不能机械全盘 WAIT。

==== 【三大底层数理基石：强化概率优势与微积分因果审计】 ====
本系统坚决破除感性猜单与盲目猜顶抄底，决策逻辑由纯数理统计驱动，并必须在输出中明确引用具体数值：
1. ⚅ 概率论与统计风险（最高权重核心）：使用偏度、超额峰度、条件延续概率 continuation_prob_pct、击穿概率 breakdown_prob_pct、Cornish-Fisher 95% VaR 与 CVaR。
   - 【胜率数学期望定价】：当条件延续概率 P续 ≥ 50%~55%（做多）或击穿概率 P破 ≥ 50%~55%（做空），且具备【本周期风险预算】声明的目标盈亏比空间时，单笔数学期望已具备极高正 Alpha，果断作为首选发单依据；
   - 【概率优势定方向】：P续 显著高于 P破（差值 ≥ 15%）时概率天平全面向多头倾斜，严禁开空，专注找回踩低吸；P破 显著占优时反之，专注找反弹承压做空；
   - 【极端肥尾折减】：超额峰度过大或 CVaR 偏高代表潜在波动剧烈，应把保证金降至【本周期风险预算】常规区间的下沿、止损按其止损基准适当放宽以抵御噪音，或直接 WAIT 放弃该机会。
2. ∂ 因果微积分动力学：只使用已闭合历史 K 线，解释对数价格速度 v、加速度 a、冲击 j 与指数衰减累计冲量 I。1H 是硬阈值与波段裁决周期。
   BULL_DECELERATING/BEAR_DECELERATING 表示趋势失速与回抽，不等于已经反转：在 4H 顺势大浪中，1H 减速回抽正是触碰支撑均线（EMA21/55）时的极佳打折买点，当 a 由负转正、j 趋缓（回踩企稳）必须果断顺势做多；在 4H 空头通道中，1H 弱反弹减速遇阻正是逢高做空的极佳卖点。
   模型输出必须在 calculus_dynamics 中明确列出当前标的 1H 的 v 与 a 真实数值，严禁只写空泛定性词句！
3. ∫ 定积分能量学：使用梯形积分计算 energy_integral（速度路径净位移/净做功）与 deviation_area_integral（相对窗口起点基线的价格路径偏离面积）。
   正负能量表示方向性累计做功；绝对偏离面积过大表示路径过度伸展与均值回归驱动。在宽幅震荡箱体中，偏离面积积分超伸至极限且伴随超买超卖时，是高胜率箱体边界反转契机！

==== 【多空对称研判与四大王牌高胜率入场形态】 ====
1. 多空双向对称顺势原则（Dual-Direction Trend Following）：多与空同等重要，核心是绝对顺应 4H 宏观与 1H 动量中枢方向。
   做多条件（4H多头主浪或箱体下沿）：4H 顺势向上或 1H 均线多头排列时专注顺势做多；1H 回调减速定性为寻找支撑均线的打折买点，在现价下方 0.2%~0.6% 挂限价多单；100% 严禁任何逆势摸顶开空。
   做空条件（4H空头承压或箱体上沿）：4H 宏观受压（4H_MACRO_BEAR）或 1H 均线空头排列时专注顺势做空；1H 向上弱反弹遇阻回落时逢高做空，在现价上方 0.2%~0.6% 挂限价空单；100% 严禁任何逆势抄底做多。
   震荡箱体双向作战：4H 处于区间震荡（CHOP/RANGE）时，下沿支撑低吸做多，上沿阻力高抛做空；箱体中间（半山腰）禁止开仓。
2. 四大王牌高胜率入场形态（形态达标必须果断发单）：
   ① 顺势回踩均线/支撑位缩量企稳（Pullback to Value / 做多）；
   ② 顺势空头反弹承压阻力位遇阻回落（Throwback to Resistance / 做空）；
   ③ 假跌破流动性掠夺后迅速收回（Liquidity Sweep & Reclaim / 诱空收网做多）；
   ④ 假突破流动性衰竭后迅速跌回（Liquidity Sweep & Fail / 诱多受挫做空）。
3. 选优开单纪律：只要形态达标且风险收益比达到【本周期风险预算】的目标盈亏比，置信度按该小节的标定带给值；不得以“再等等完美共振”为由放弃合法机会。

==== 【开仓参数与科学价格几何】 ====
- 顺势铁律（Fail-Closed）：4H_MACRO_BULL 大级别多头通道下 100% 严禁输出 SELL_SHORT 逆势摸顶；4H_MACRO_BEAR 大级别空头承压下 100% 严禁输出 BUY_LONG 逆势抄底！
- 震荡过滤：箱体正中间无序乱跳时一律强制 WAIT，严禁追涨杀跌磨损手续费。
- 价格几何：BUY_LONG 必须满足 stop_loss_price < entry_price < take_profit_price；SELL_SHORT 必须满足 take_profit_price < entry_price < stop_loss_price。目标盈亏比见【本周期风险预算】；执行层绝对拒绝低于其硬底线的报价。
- 入场一律 Maker 限价：挂在支撑/阻力位附近（如现价下方/上方 0.1%~0.6%），严禁市价追单；止损基于结构性保护点（前低支撑位或箱体边缘下方 0.3%~0.5%），参考 1.8~2.2x 1H ATR，绝不贴脸设损。
- 保证金与杠杆：常规取【本周期风险预算】给出的常规区间，强信号（P0 全通过 + 概率优势 ≥ 15% + ADX ≥ 22）可上浮至其单笔保证金硬顶；杠杆不超过其声明的杠杆上限。资金规模过小时宁可少开标的，也不得压缩止损距离或放弃盈亏比底线；若某标的在当前余额下无法同时满足交易所最小下单量、止损呼吸空间与 R:R 底线，该标的必须输出 WAIT 并说明资金不匹配。
"""

_PYRAMID = """==== 【顺势浮盈金字塔加仓：模型只能申请，执行层拥有最终否决权】 ====
- 已有多仓只能申请同向 BUY_LONG，已有空仓只能申请同向 SELL_SHORT；反向指令不得借加仓通道执行。
- 申请前置条件（缺一不可）：底仓浮盈与保本移损达标、该标的累计加仓次数未超上限、AI 置信度达到加仓门禁、加仓后单标的累计保证金不超过单标的上限——全部阈值以每轮用户消息【本周期风险预算】的实时声明为准；若其声明加仓已禁用（上限 0 次），则一律不得申请加仓，仅可 HOLD / UPDATE_SL / CLOSE_MARKET。
- 加多门禁：多周期聚合加速度 a ≥ -0.25 且 continuation_prob_pct ≥ 40%；加空门禁：a ≤ +0.25 且 breakdown_prob_pct ≥ 40%。
- 浮亏、未脱离成本区、顶部/底部失速、概率不足或肥尾冲击时不得申请加仓。即使模型申请，执行器仍将独立硬校验并保留最终否决权。
"""

_SYSTEM_JSON_CONTRACT = """==== 【严格 JSON 规范契约与完整输出骨架 (JSON Schema)】 ====
你必须直接输出一个严格合法的 JSON 对象，禁止输出任何 Markdown 代码围栏、前缀或额外文字。结构必须严格完全符合以下 JSON Schema 骨架：

{
  "macro_assessment": "30字内全市场宏观流动性与大盘走势总结",
  "position_management": [
    {
      "instId": "BTC-USDT-SWAP",
      "action": "HOLD",
      "suggested_sl_price": 0.0,
      "confidence": 85.0,
      "reason": "30字内持仓调整原因与动能简述"
    }
  ],
  "pending_orders_management": [
    {
      "ordId": "在途挂单ID",
      "instId": "BTC-USDT-SWAP",
      "action": "KEEP",
      "reason": "30字内维持或撤单原因"
    }
  ],
  "decisions": {
    "BTC-USDT-SWAP": {
      "action": "BUY_LONG",
      "confidence": 85.0,
      "leverage": 3,
      "margin_usdt": 100.0,
      "entry_price": 79500.0,
      "take_profit_price": 83000.0,
      "stop_loss_price": 77800.0,
      "summary_reason": "顺势回踩支撑企稳限价做多",
      "market_structure": "4H大势多头，1H均线回踩企稳",
      "calculus_dynamics": "1H: v=+0.05, a=+0.20 动能转正",
      "math_prob_rationale": "延续概率65%显著占优，R:R=2.5",
      "volume_and_oi": "量能缩量企稳，主力净流入"
    }
  }
}

▍字段审计说明：
- position_management.action 只允许: "HOLD" | "CLOSE_MARKET" | "UPDATE_SL"；触发峰值回撤超 35% 或 1H 负功率衰竭时果断输出 CLOSE_MARKET 止盈；action 为 UPDATE_SL 时 suggested_sl_price 填目标价格，否则必须填 0.0；
- pending_orders_management.action 只允许: "KEEP" | "CANCEL"；挂单已大幅偏离盘口或入场逻辑失效时必须 CANCEL；
- decisions[标的].action 只允许: "BUY_LONG" | "SELL_SHORT" | "WAIT"；action 为 WAIT 时 entry_price/take_profit_price/stop_loss_price 填 0.0；
- decisions 只包含有明确结论的标的，未涉及的标的不得出现；
- 每个决策的 calculus_dynamics 与 math_prob_rationale 必须明确引用具体 1H v, a 与概率数值，严禁只写空泛定性词句！"""

# ---------------------------------------------------------------------------
# 跨所比对矩阵（Phase 2 · 币安/Gate 只读备源）
# 纯证据增益：任何失败一律 fail-soft，绝不阻塞决策主循环。
# 熔断开关 R20_XVENUE_PROMPT=0 时整段跳过（网络故障预案/测试封闭性）。
# ---------------------------------------------------------------------------

# 跨所取数健康度状态：**刻意留在门面**（不是实现细节）——
# `tests/venues/test_xvenue_prompt.py:120` 直接断言 `abt._XV_HEALTH`，且门面被
# `pin_baseline_risk_env()` 原地重载后，子模块 import 期持有的引用会与门面的
# 那个不再是同一个对象。实现模块 scripts/brain/xvenue.py 只保留保护它的锁。
_XV_HEALTH: Dict[str, Dict[str, Any]] = {}


def _xvenue_enabled() -> bool:
    """跨所提示词总开关。实现见 scripts/brain/xvenue.py。"""
    return _xvenue_enabled_impl()


def _xv_record(venue: str, name: str, ok: bool, latency_ms: float, err: str = "") -> None:
    """记录场所级取数健康度（写入门面的 `_XV_HEALTH`）。实现见 scripts/brain/xvenue.py。

    `_XV_HEALTH` 必须留在门面：`tests/venues/test_xvenue_prompt.py:120` 直接断言
    `abt._XV_HEALTH`，且门面被 `pin_baseline_risk_env()` 原地重载后
    子模块持有的引用会与门面的那个不再是同一个对象。
    """
    _xv_record_impl(_XV_HEALTH, venue, name, ok, latency_ms, err)


def _xv_flush_health(packages: List[Dict[str, Any]]) -> None:
    """健康度 + 逐币跨所快照落盘。实现见 scripts/brain/xvenue.py。

    依赖一律在**调用时**从门面全局取名（而不是 import 期绑定或设默认参数）——
    目的是让 `patch.object(abt, "VENUE_HEALTH_FILE", …)` 与
    `patch.object(abt, "atomic_write_json", …)` 在调用时被读到；
    同时 `abt._xv_flush_health([...])` 这种只传 packages 的既有调用
    （`tests/venues/test_xvenue_prompt.py:129/144`）照旧成立。

    注：不要把默认值写成同名形参（`safe_float=None` 之类）—— 那会让函数体里的
    裸名解析到形参而不是模块全局，等于把补丁缝静默关掉。
    """
    _xv_flush_health_impl(
        packages,
        health=_XV_HEALTH,
        safe_float=safe_float,
        atomic_write_json=atomic_write_json,
        venue_health_file=VENUE_HEALTH_FILE,
    )


def _get_xvenue_adapter(venue: str):
    """适配器获取。实现见 scripts/brain/xvenue.py。

    保留在门面：这是 `tests/venues/test_xvenue_prompt.py` 4 处
    `patch.object(abt, "_get_xvenue_adapter", …)` 的**既定 mock 缝**
    （模块注释原话：「测试与故障注入缝：mock 此函数即可完全离线」）。
    """
    return _get_xvenue_adapter_impl(venue)


def _xv_binance_snapshot(base: str):
    """单点取币安现价/大户比/费率。实现见 scripts/brain/xvenue.py。"""
    return _xv_binance_snapshot_impl(
        base,
        get_adapter=_get_xvenue_adapter,
        record=lambda venue, name, ok, ms, err="": _xv_record(venue, name, ok, ms, err),
    )


def _xv_gate_snapshot(base: str):
    """单点取 Gate 现价/大户比/费率。实现见 scripts/brain/xvenue.py。"""
    return _xv_gate_snapshot_impl(
        base,
        get_adapter=_get_xvenue_adapter,
        record=lambda venue, name, ok, ms, err="": _xv_record(venue, name, ok, ms, err),
    )


def fetch_cross_venue_matrix(packages: List[Dict[str, Any]]) -> None:
    """给每个 pkg 就地挂 xvenue（US-003 对称化）。实现见 scripts/brain/xvenue.py。"""
    _fetch_cross_venue_matrix_impl(
        packages,
        enabled=_xvenue_enabled(),
        snapshot_binance=_xv_binance_snapshot,
        snapshot_gate=_xv_gate_snapshot,
        flush_health=_xv_flush_health,
    )


def _xv_divergence_notes(xv: Dict[str, Any]) -> str:
    """跨所分歧自动标注。实现见 scripts/brain/xvenue.py。"""
    return _xv_divergence_notes_impl(xv)


def _xvenue_prompt_line(p: Dict[str, Any]) -> str:
    """归一跨所证据行。实现见 scripts/brain/xvenue.py。

    `safe_float` 在调用时注入（它定义在本门面，不是共享叶子函数）。
    """
    return _xvenue_prompt_line_impl(p, safe_float=safe_float)




# System 宪法保持静态：全部动态风控阈值由每轮 construct_full_market_prompt 注入的
# 【本周期风险预算】小节实时携带（该小节直接从 risk_constants 推导，永不进快照）。
# 这样即使策略快照布局缓存了本节文本，风控改参也不会造成「提示词口径过期」。
SYSTEM_PROMPT = _SYSTEM_CORE + _PYRAMID + "\n" + _SYSTEM_JSON_CONTRACT


def build_risk_budget_text(usdt_available: float = None) -> str:
    """【本周期风险预算】小节（审计 P1-1，2026-09-13）。

    旧实现自己算「权益×5% / 权益×30%」，漏掉了绝对封顶，于是模型看到
    单标的 1496.82U / 日亏 −249.47U，而引擎执行 `min(600, 1496.82)`=600U、
    `min(150, 249.47)`=150U（虚高 2.49× / 1.66×）——偏偏 SYSTEM PROMPT 要求模型
    "一切金额类参数一律以【本周期风险预算】小节为准"，模型据此规划的是不存在的空间。

    现在两个封顶值直接取 `risk_constants.effective_*`（与执行层同一函数对象），
    并把此前对模型完全不可见的 5 个旋钮一并披露：组合风险总预算、并发持仓上限、
    单标的绝对封顶、日亏绝对封顶、时间止损带宽。
    """
    if usdt_available is None or usdt_available < 0:
        return "[MISSING_CONTEXT:risk_budget]"
    # 动态取单一事实源对象（支持后台热重载与多用例隔离）
    import scripts.risk_constants as rc

    # 风险预算按「实际可用余额」自适应推导：预设绝不写死绝对金额，避免与小资金账户(如 80U)冲突。
    _eq = float(usdt_available)
    _m_lo = round(_eq * 0.03, 2)
    _m_hi = round(_eq * min(0.12, rc.MAX_MARGIN_EQUITY_RATIO), 2)
    _m_strong = round(_eq * rc.MAX_MARGIN_EQUITY_RATIO, 2)
    # 单标的累计 = min(绝对封顶 600U, 权益×30%)；日亏熔断 = min(绝对封顶 150U, 权益×5%)
    _asset_cap = rc.effective_single_asset_margin(_eq)
    _daily_stop = rc.effective_daily_loss_limit(_eq)
    # 单笔上限同样受单标的累计封顶约束（首笔即计入累计）：min(权益×20%, 单标的封顶)
    _m_strong_cap = min(_m_strong, _asset_cap)
    _asset_cap_note = (f"min({rc.MAX_SINGLE_ASSET_MARGIN:g} 绝对封顶, 可用余额 {rc.SINGLE_ASSET_EQUITY_RATIO:.0%})"
                       if _asset_cap < round(_eq * rc.SINGLE_ASSET_EQUITY_RATIO, 2) else f"可用余额 {rc.SINGLE_ASSET_EQUITY_RATIO:.0%}")
    _daily_stop_note = (f"min({rc.MAX_DAILY_LOSS_USDT:g} 绝对封顶, 可用余额 {rc.DAILY_LOSS_EQUITY_RATIO:.0%})"
                        if _daily_stop < round(max(_eq * rc.DAILY_LOSS_EQUITY_RATIO, 1.0), 2) else f"可用余额 {rc.DAILY_LOSS_EQUITY_RATIO:.0%}")
    text = (
        f"【本周期风险预算｜按实际可用余额 {_eq:.2f} USDT 与后台风控配置自适应推导，严禁套用任何固定绝对金额】:\n"
        f"- 常规单笔保证金: {_m_lo} ~ {_m_hi} USDT (可用余额 3%~{min(0.12, rc.MAX_MARGIN_EQUITY_RATIO):.0%})\n"
        f"- 强信号单笔保证金上限: {round(_m_strong_cap, 2)} USDT "
        f"(min(权益 {rc.MAX_MARGIN_EQUITY_RATIO:.0%}={_m_strong}, 单标的封顶 {round(_asset_cap, 2)})，执行层硬顶)\n"
        f"- 单标的累计保证金上限(含金字塔加仓): {_asset_cap} USDT ({_asset_cap_note}，执行层已按同一 min() 硬夹)\n"
        f"- 单笔最大可承受亏损: 以 1.0R 为基准，且不超过可用余额 {rc.RISK_PER_TRADE_EQUITY_RATIO:.0%}\n"
        f"- 当日累计亏损熔断线: -{_daily_stop} USDT ({_daily_stop_note}，执行层已按同一 min() 硬夹)\n"
        f"- 全系统同向持仓上限: {rc.MAX_SAME_DIRECTION_POSITIONS} 笔 (多/空各自封顶，执行层硬拦截)\n"
        f"- 全系统并发持仓上限: "
        + (f"{rc.MAX_CONCURRENT_POSITIONS_CAP} 笔 (执行层硬拦截)\n" if rc.MAX_CONCURRENT_POSITIONS_CAP > 0
           else "未单独设限 (0=不额外收紧；实际受标的池容量与同向上限约束)\n")
        + (
            f"- 组合风险总预算(跨所合算): {rc.PORTFOLIO_RISK_BUDGET_USDT:.2f} USDT (执行层按总名义敞口强制)\n"
            if rc.PORTFOLIO_RISK_BUDGET_USDT > 0 else
            "- 组合风险总预算(跨所合算): 未设上限 (0=引擎不封顶，仅受单标的/同向/并发上限约束)\n"
        )
        # 审计 P2-1：跨所同向敞口上限现已真执行（execution_router 发送前拒开），
        # 这里必须同源披露，否则"提示词口径 == 代码口径"又多一处例外。
        + (
            f"- 跨所同向敞口上限: {rc.MAX_TOTAL_EXPOSURE_USDT:.2f} USDT (同一标同方向跨所合计名义额，含本单；超出执行层拒开)\n"
            if rc.MAX_TOTAL_EXPOSURE_USDT > 0 else
            "- 跨所同向敞口上限: 未设上限 (0=不限制；仍受单标的/同向/并发上限约束)\n"
        )
        + f"- 最长持仓时间: {rc.TIME_STOP_HOURS:g} 小时 (超时且横盘无突破将被时间止损离场；横盘判定带宽 ±{rc.TIME_STOP_ATR_BAND:.0%} ATR)\n"
        f"- 单笔杠杆区间: {rc.MIN_LEVERAGE:g}x ~ {rc.MAX_LEVERAGE:g}x (在区间内按信号强度自主裁决；区间外执行层自动钳制)\n"
        f"- 盈亏比 R:R 硬底线: {rc.MIN_RISK_REWARD_RATIO:.1f} (低于此值的报价执行层物理拒绝)\n"
        # 审计 P3-4：宪法里的"目标 R:R ≥2.2/2.5"与"置信度 78%~88%"是硬编码，
        # 与可配的硬底线/门禁冲突（稳健套件门禁 85 → 78~88 一带整片必拒）。
        # 目标与标定带统一在此派生，宪法只指向本节。
        f"- 目标盈亏比 R:R: {max(2.2, float(rc.MIN_RISK_REWARD_RATIO or 0.0)):.1f} ~ {rc.MAX_RISK_REWARD_RATIO:.1f} "
        f"(上限 {rc.MAX_RISK_REWARD_RATIO:.1f}；低于硬底线一律被拒，超出上限执行层自动平滑收窄钳制，防止止盈过远)\n"
        f"- 单笔止盈止损宽度: 基准止损 {rc.STOP_LOSS_ATR_MULT:g}x 1H ATR，最大止盈宽度 ≤ {rc.MAX_TAKE_PROFIT_ATR:g}x 1H ATR (超出上限执行层自动平滑收窄至合理波段)\n"
        f"- 置信度标定带: {max(float(rc.MIN_ENTRY_CONFIDENCE or 0.0), 78.0):.0f}% ~ "
        f"{max(float(rc.MIN_ENTRY_CONFIDENCE or 0.0), 78.0) + 8.0:.0f}% "
        f"(下沿=执行层新开仓门禁 {rc.MIN_ENTRY_CONFIDENCE:.0f}%，低于下沿必被物理拦截)\n"
        f"- 新开仓最低置信度门禁: {rc.MIN_ENTRY_CONFIDENCE:g}% (低于此值禁止新开仓)\n"
        + (
            "- 金字塔加仓: 已禁用 (最大加仓次数 0，在途持仓仅可 HOLD/UPDATE_SL/CLOSE_MARKET)\n"
            if rc.MAX_SCALE_IN_COUNT <= 0 else
            f"- 金字塔加仓门禁: 最多 {rc.MAX_SCALE_IN_COUNT} 次 · 底仓浮盈 ≥ {rc.MIN_SCALE_IN_PROFIT_RATIO:.1%} 且已保本 · 置信度 ≥ {rc.MIN_SCALE_IN_CONFIDENCE:g}%\n"
        )
        + (
            f"- 分批止盈机制: 【已启用】(底仓浮盈达到 {rc.SCALE_OUT_TRIGGER_ATR:g}x ATR 时，执行层自动市价平仓 {rc.SCALE_OUT_RATIO:.0%} 锁定现金利润并提损保本；模型可让剩余仓位充分奔跑博取大波段)\n"
            if rc.SCALE_OUT_ENABLED else
            "- 分批止盈机制: 【已禁用】(全仓奔跑至目标止盈位或触发动态追踪止损)\n"
        )
        + f"- 止损后同标的冷静期: {rc.STOP_COOLDOWN_MINUTES} 分钟"
    )
    if _eq < 200.0:
        text += (
            "\n- ⚠️ 小资金账户提示: 可用余额偏小，按百分比推导的保证金可能低于部分永续合约的交易所最小下单名义价值"
            "（如高单价币种 BTC 一张合约的名义价值就可能超过账户余额）。此时应当【减少同时持有的标的数量】、"
            "优先选择最小名义价值与账户规模匹配的标的，或适度提高单笔保证金占比；"
            "绝不允许通过压缩止损距离或降低盈亏比来迁就资金规模。"
            "若某标的在当前余额下无法同时满足最小下单量、止损呼吸空间与 R:R≥2.0，该标的必须输出 WAIT 并说明资金不匹配。"
        )
    return text


def construct_full_market_prompt(
    packages: List[Dict[str, Any]],
    pos_summary: str = "[MISSING_CONTEXT:account_positions]",
    active_positions_detail: List[Dict[str, Any]] = None,
    pending_orders_detail: List[Dict[str, Any]] = None,
    current_time_str: str = "",
    usdt_available: float = None,
    runtime_context_out: Dict[str, Any] = None,
    policy_snapshot: Dict[str, Any] = None,
) -> str:
    """把本轮全市场数据渲染成用户提示词。实现见 scripts/brain/prompt.py。

    15 项依赖在**调用时**从门面全局取名 —— 其中风控常量必须与执行层同源
    （`risk_constants` 改参后由门面重载刷新），文件路径与 `safe_float` 则是
    既有测试缝。理由逐一列在 scripts/brain/prompt.py 的 docstring。

    注：不要把默认值写成同名形参（`safe_float=None` 之类）—— 那会让函数体里的
    裸名解析到形参、静默关掉这些缝（同类教训见 scripts/brain/xvenue.py）。
    """
    return _construct_full_market_prompt_impl(
        packages, pos_summary, active_positions_detail, pending_orders_detail,
        current_time_str, usdt_available, runtime_context_out, policy_snapshot,
        safe_float=safe_float,
        sl_atr_mult_for=_sl_atr_mult_for,
        xvenue_prompt_line=_xvenue_prompt_line,
        build_risk_budget_text=build_risk_budget_text,
        active_profile=active_profile,
        apply_module_layout=apply_module_layout,
        system_version=__version__,
        ai_memory_md_file=AI_MEMORY_MD_FILE,
        ai_memory_file=AI_MEMORY_FILE,
        news_sentiment_file=NEWS_SENTIMENT_FILE,
        max_leverage=MAX_LEVERAGE,
        min_leverage=MIN_LEVERAGE,
        max_scale_in_count=MAX_SCALE_IN_COUNT,
        min_scale_in_confidence=MIN_SCALE_IN_CONFIDENCE,
        max_margin_equity_ratio=MAX_MARGIN_EQUITY_RATIO,
        _build_position_lines=_build_position_lines,
        _build_pending_order_lines=_build_pending_order_lines,
    )



def validate_and_filter_decision(p: Dict[str, Any], d_item: Dict[str, Any],
                                 active_inst_ids: set,
                                 active_position_sides: Dict[str, str]) -> tuple[str, str, float]:
    """单条决策校验。实现见 scripts/brain/decisions.py。

    `safe_float` 在调用时注入（它定义在本门面，不是共享叶子函数）。
    """
    return _validate_and_filter_decision_impl(
        p, d_item, active_inst_ids, active_position_sides, safe_float=safe_float)


def assemble_decision_cache(
    packages: List[Dict[str, Any]],
    decisions_dict: Dict[str, Any],
    active_inst_ids: set,
    active_position_sides: Dict[str, str],
    time_str: str,
    macro_summary: str,
    policy_snapshot: Optional[Dict[str, Any]] = None,
    council_status: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """把已验证决策装配成标准缓存契约。实现见 scripts/brain/decisions.py。

    依赖一律在**调用时**从门面全局取名，以便既有测试缝继续生效：
    - `patch.object(abt, "DATA_DIR", …)`（test_ai_health_sidecar）
    - `patch.object(abt, "MAX_LEVERAGE"/"MIN_LEVERAGE", …)`（test_leverage_range_and_council
      断言"模型给 3 被下限抬到 5"，夹取必须读到补丁值）
    - `safe_float` / `_get_system_version_tag` 定义在本门面。

    注：不要把默认值写成同名形参 —— 那会让函数体里的裸名解析到形参、
    静默关掉补丁缝（详见 scripts/brain/xvenue.py 同名教训）。
    """
    return _assemble_decision_cache_impl(
        packages, decisions_dict, active_inst_ids, active_position_sides,
        time_str, macro_summary,
        policy_snapshot=policy_snapshot, council_status=council_status,
        data_dir=DATA_DIR,
        max_leverage=MAX_LEVERAGE,
        min_leverage=MIN_LEVERAGE,
        safe_float=safe_float,
        get_system_version_tag=_get_system_version_tag,
        validate=lambda p_, d_, ids_, sides_: _validate_and_filter_decision_impl(
            p_, d_, ids_, sides_, safe_float=safe_float),
    )




@single_brain_cycle
def fetch_pending_orders_list() -> Optional[List[Dict[str, Any]]]:
    """拉取交易所当前全部 SWAP 挂单（V5 直签 REST，US-003）。

    行为契约（对齐历史 CLI 挂单查询）：返回列表=成功；查询失败/未配置
    凭证（OKXNotConfigured）→ 告警并返回 None。fail-closed：绝不回退命令行子进程。
    """
    try:
        fetched = okx_rest.pending_orders()
    except Exception as e:
        print(f"[AI Brain Batch] Pending orders fetch warning: {e}")
        return None
    return fetched if isinstance(fetched, list) else None


def execute_brain_pending_cancels(pending_mgmt_list: List[Any]) -> List[Dict[str, Any]]:
    """执行 AI 决策的 CANCEL 清单（V5 直签 REST，US-003）。

    仅当撤单真实成功才打印 🛑（旧 CLI 版失败也无条件打印成功，属假告警，已修正）；
    单笔失败打印 ⚠️ 并继续处理其余项，返回执行日志供测试与审计。
    """
    log: List[Dict[str, Any]] = []
    for p_order in pending_mgmt_list or []:
        if not isinstance(p_order, dict):
            continue
        p_act = str(p_order.get("action", "")).upper()
        p_ord_id = str(p_order.get("ordId", ""))
        p_inst_id = str(p_order.get("instId", ""))
        p_reason = str(p_order.get("reason", "模型指示撤销该挂单"))
        if p_act == "CANCEL" and p_ord_id and p_inst_id:
            try:
                okx_rest.cancel_order(p_inst_id, p_ord_id)
                print(f"[AI Brain Batch] 🛑 AI自主撤回失效/过时限价单: {p_inst_id} (ordId={p_ord_id}, 原因={p_reason})")
                log.append({"ok": True, "instId": p_inst_id, "ordId": p_ord_id, "reason": p_reason})
            except Exception as exc:
                print(f"[AI Brain Batch] ⚠️ 撤单失败（直签 REST fail-closed，不做假成功，待下一周期重试）: {p_inst_id} ordId={p_ord_id}: {exc}")
                log.append({"ok": False, "instId": p_inst_id, "ordId": p_ord_id, "reason": p_reason, "error": str(exc)})
    return log


def execute_batch_ai_brain_cycle(
    pos_summary: str = "[MISSING_CONTEXT:account_positions]",
    active_positions_detail: List[Dict[str, Any]] = None,
    usdt_available: float = None,
    policy_snapshot: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Fetch all six crypto symbols, call the LLM once, then persist an auditable result."""
    base_url, api_key = get_cpa_client_config()
    if not api_key:
        print("[AI Brain Batch] Error: CPA API Key not found")
        _record_cycle_health("failed", "CPA API Key 未配置")
        return None

    tz_bj = datetime.timezone(datetime.timedelta(hours=8))
    now_bj = datetime.datetime.now(tz_bj)
    time_str = now_bj.strftime("%Y-%m-%d %H:%M:%S")

    # Capture immutable Policy Snapshot at start of decision cycle
    (policy_hash, policy_snapshot, policy_summary, policy_version) = capture_policy_snapshot(
        _get_system_version_tag=_get_system_version_tag,
        policy_snapshot=policy_snapshot    )
    print(f"[AI Brain Batch] 📌 当前决策策略快照: {policy_version} ({policy_hash})")

    print(f"[AI Brain Batch] 并行获取 {len(TARGET_INSTRUMENTS)} 币种原生行情、技术指标与顶级聪明钱数据...")
    with ThreadPoolExecutor(max_workers=8) as executor:
        packages = list(executor.map(fetch_single_instrument_package, TARGET_INSTRUMENTS))

    # 跨所比对（币安/Gate 只读备源，纯证据增益，失败静默跳过不阻塞决策）
    fetch_cross_venue_matrix(packages)

    # 顶级聪明钱与大户持仓数据接入（Binance 公开大户指标 + OKX Rubik 备选双源容灾）
    try:
        try:
            from scripts.factors.smart_money import fetch_smart_money_for_symbol
        except ImportError:
            from factors.smart_money import fetch_smart_money_for_symbol
        for pkg in packages:
            ccy = pkg.get("ccy") or pkg.get("name") or ""
            if not ccy and "-" in pkg.get("instId", ""):
                ccy = pkg["instId"].split("-")[0]
            sm_data = fetch_smart_money_for_symbol(ccy, price=float(pkg.get("price") or 0.0))
            if sm_data:
                if pkg.get("lsRatio") == "N/A" and sm_data.get("lsRatio"):
                    pkg["lsRatio"] = sm_data["lsRatio"]
                if pkg.get("takerNetUsd") == "N/A" and sm_data.get("takerNetUsd"):
                    pkg["takerNetUsd"] = sm_data["takerNetUsd"]
                pkg["smart_money"] = {
                    "available": True,
                    "weighted_long_pct": sm_data.get("weighted_long_pct", "--"),
                    "net_flow_usdt": sm_data.get("takerNetUsd", "--"),
                    "avg_long_entry": "--",
                    "avg_short_entry": "--",
                    "top_win_rate": "--",
                }
    except Exception as exc:
        print(f"[AI Brain Batch] 聪明钱数据注入降级: {exc}")

    positions_context = active_positions_detail
    active_positions_detail = active_positions_detail or []

    # 审计 P2-12：跨所 id 归一（模块级 canonical_position_inst_id，含单元测试）
    def _canonical_inst_id(raw: Any) -> str:
        return canonical_position_inst_id(raw)

    active_inst_ids = {
        _canonical_inst_id(p.get("instId")) for p in active_positions_detail if p.get("instId")
    }
    active_inst_ids.discard("")
    active_position_sides = {
        _canonical_inst_id(p.get("instId")): str(p.get("side", p.get("posSide", ""))).lower()
        for p in active_positions_detail if p.get("instId")
    }
    active_position_sides.pop("", None)
    # 审计D(2026-09-13)：package_by_id 死构造清除（全函数无消费）

    # Automatically Update & Persist Comprehensive Factor Library Snapshot
    update_factor_library_snapshot(
        WORKSPACE_DIR=WORKSPACE_DIR,
        os=os,
        sys=sys    )

    # Fetch live pending limit orders from exchange（V5 直签 REST，行为契约见 fetch_pending_orders_list）
    pending_orders_list = fetch_pending_orders_list()

    write_calculus_snapshot(
        CALCULUS_SNAPSHOT_FILE=CALCULUS_SNAPSHOT_FILE,
        json=json,
        os=os,
        packages=packages,
        time_str=time_str    )

    runtime_context = {}
    prompt = construct_full_market_prompt(packages, pos_summary, positions_context, pending_orders_detail=pending_orders_list, current_time_str=time_str, usdt_available=usdt_available, runtime_context_out=runtime_context, policy_snapshot=policy_snapshot)

    profile = active_profile()
    # 审计 P1-3：覆盖层由 get_effective_system_prompt 在布局**之后**追加（此前被布局丢弃）
    effective_system_prompt = get_effective_system_prompt(profile=profile, context=runtime_context)

    # Save Realtime Prompt Snapshot for Web Transparent Inspection
    write_prompt_snapshot(
        AI_LAST_PROMPT_FILE=AI_LAST_PROMPT_FILE,
        _build_effective_prompt_text=_build_effective_prompt_text,
        effective_system_prompt=effective_system_prompt,
        os=os,
        prompt=prompt,
        time_str=time_str    )

    (api_format, api_key, base_url, effort, execute_llm_request, model_name, thinking_timeout) = resolve_llm_runtime(
        api_key=api_key,
        base_url=base_url,
        os=os    )

    telemetry = ModelCallTelemetry(
        "trading_brain", model_name, str(effort), effective_system_prompt, prompt
    )
    return dispatch_llm_and_persist_decisions(
        AI_DECISION_CACHE_FILE=AI_DECISION_CACHE_FILE,
        AI_DECISION_HISTORY_FILE=AI_DECISION_HISTORY_FILE,
        AI_POSITION_MANAGEMENT_FILE=AI_POSITION_MANAGEMENT_FILE,
        Any=Any,
        Dict=Dict,
        _build_effective_prompt_text=_build_effective_prompt_text,
        _build_history_record=_build_history_record,
        _normalize_position_management=_normalize_position_management,
        _record_cycle_health=_record_cycle_health,
        active_inst_ids=active_inst_ids,
        active_position_sides=active_position_sides,
        api_format=api_format,
        api_key=api_key,
        assemble_decision_cache=assemble_decision_cache,
        atomic_write_json=atomic_write_json,
        base_url=base_url,
        effective_system_prompt=effective_system_prompt,
        effort=effort,
        execute_brain_pending_cancels=execute_brain_pending_cancels,
        execute_llm_request=execute_llm_request,
        json=json,
        model_name=model_name,
        os=os,
        packages=packages,
        policy_hash=policy_hash,
        policy_snapshot=policy_snapshot,
        policy_summary=policy_summary,
        policy_version=policy_version,
        prompt=prompt,
        runtime_context=runtime_context,
        safe_float=safe_float,
        telemetry=telemetry,
        thinking_timeout=thinking_timeout,
        time=time,
        time_str=time_str,
        urllib=urllib    )

def get_latest_ai_decision(inst_id: str, max_age_seconds: int = DECISION_MAX_AGE_SECONDS) -> Optional[Dict[str, Any]]:
    """Read a validated decision only while its cache timestamp is fresh."""
    if os.path.exists(AI_DECISION_CACHE_FILE):
        try:
            with open(AI_DECISION_CACHE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            item = data.get(inst_id)
            if not isinstance(item, dict):
                return None
            timestamp = int(item.get("timestamp", 0) or 0)
            if timestamp <= 0 or int(time.time()) - timestamp > max_age_seconds:
                return None
            return item
        except Exception:
            pass
    return None

if __name__ == "__main__":
    res = execute_batch_ai_brain_cycle("当前无持仓")
    if res:
        print("\n--- 示例标的 AI 决策结果 ---")
        for k in ["BTC-USDT-SWAP", "SOL-USDT-SWAP", "LINK-USDT-SWAP"]:
            if k in res:
                print(f"[{k}]", json.dumps(res[k]["decision"], ensure_ascii=False))
