#!/usr/bin/env python3
"""
R20 AI LLM-Native Self-Improvement & Strategy Evolution Engine v6.8.1 (self_improvement_engine.py)
Focuses purely on Crypto Alpha generation & dynamic quantitative risk adaptation.
Eliminates rigid cooldown bans in favor of dynamic volatility-adjusted thresholds,
asymmetric Kelly bet-sizing, and LLM cognitive post-mortem lessons.
"""

import os
import sys
import json
import time
import datetime
import urllib.request
import tempfile
import fcntl
import hashlib
from typing import Dict, Any, List, Optional, Tuple

from pathlib import Path

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = Path(PROJECT_ROOT)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

try:
    from r20_backend.config import settings as standalone_settings
except ImportError:
    standalone_settings = None

WORKSPACE_DIR = PROJECT_ROOT
DATA_DIR = os.path.join(WORKSPACE_DIR, "data")
LOGS_DIR = os.path.join(WORKSPACE_DIR, "logs")

LEDGER_JSON_FILE = os.path.join(DATA_DIR, "trading_ledger.json")
REPORT_JSON_FILE = os.path.join(DATA_DIR, "self_improvement_report.json")
AI_DECISIONS_FILE = os.path.join(DATA_DIR, "ai_brain_decisions.json")
AI_MEMORY_FILE = os.path.join(DATA_DIR, "ai_trading_memory.json")
AI_MEMORY_MD_FILE = os.path.join(DATA_DIR, "AI_TRADING_MEMORY.md")
EVOLUTION_LAST_PROMPT_FILE = os.path.join(DATA_DIR, "self_improvement_last_prompt.txt")
LOG_FILE = os.path.join(LOGS_DIR, "self_improvement.log")
EVOLUTION_LOCK_FILE = os.path.join(DATA_DIR, ".self_improvement.lock")

from r20_backend.time_utils import parse_beijing
from r20_backend.version import __version__
from instrument_pool import load_instruments
from prompt_library import active_profile, apply_module_layout
from r20_gateway.telemetry import ModelCallTelemetry
TARGET_INSTRUMENTS = [item["name"] for item in load_instruments()]

def atomic_write_json(path: str, payload: Any) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=".evolution-", suffix=".tmp", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


def clamp(value, lower, upper, default):
    try:
        return max(lower, min(upper, float(value)))
    except (TypeError, ValueError):
        return default


def single_evolution_cycle(func):
    def wrapped(*args, **kwargs):
        lock_handle = open(EVOLUTION_LOCK_FILE, "a+", encoding="utf-8")
        try:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            lock_handle.close()
            log_msg("Self-evolution skipped: another cycle is still running")
            return None
        try:
            return func(*args, **kwargs)
        finally:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
            lock_handle.close()
    return wrapped


def log_msg(msg: str):
    tz_bj = datetime.timezone(datetime.timedelta(hours=8))
    timestamp = datetime.datetime.now(tz_bj).strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {msg}"
    print(line)
    try:
        os.makedirs(LOGS_DIR, exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass

def get_cpa_client_config() -> Tuple[str, str]:
    """Resolve LLM credentials only from process environment or local .env."""
    try:
        from r20_backend.llm_manager import get_active_llm_runtime
        active_llm = get_active_llm_runtime()
        if active_llm.get("base_url"):
            return active_llm["base_url"], active_llm.get("api_key", "")
    except Exception:
        pass
    if standalone_settings:
        return standalone_settings.llm_base_url, standalone_settings.llm_api_key
    return (
        os.getenv("LLM_BASE_URL") or os.getenv("OPENAI_BASE_URL") or "https://api.openai.com/v1",
        os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY") or "",
    )

# =============================================================================
# 数理快照可观测性（宿主确定性审计，2026-09-10）
# 事故链：build_signal_snapshot 旧版 schema 错配（09-09 已修复写入侧）导致历史
# journal 全部为「对象存在但 22/17 动力学字段 null」的空壳；宿主把空壳原样喂给
# 模型，模型只能自数 null，既易漂移，也给「倒推伪造」留了口子。从此由宿主逐单
# 判定可观测性并把统计结论前置注入 Prompt；join 侧同时禁止用未来或过期快照
# 回填因果证据。
# =============================================================================
DYNAMICS_FIELDS = (
    "velocity", "acceleration", "jerk", "impulse", "curvature", "power",
    "power_regime", "regime", "dynamics_quality",
    "continuation_prob_pct", "breakdown_prob_pct", "var_95_pct", "cvar_95_pct",
    "prob_regime", "is_fat_tail", "energy_integral", "deviation_area_integral",
)
# 动力学链视为「可观测」的最低非空字段数（88% 容差：允许个别外部观测缺失）
DYNAMICS_OBSERVED_MIN = max(1, int(len(DYNAMICS_FIELDS) * 0.85) + 1)
# 开仓时刻快照与开仓时间的 join 窗口：至多 6 小时（adoption 路径应在开仓后 1~2 个 15M 周期内补录）
SNAPSHOT_MAX_STALE_SECONDS = 6 * 3600
SIDE_ALIASES = {"多": "long", "空": "short", "long": "long", "short": "short"}


def _parse_bj(ts) -> Optional[datetime.datetime]:
    dt = parse_beijing(ts)
    # Existing join callers use naive Beijing values; normalize BEFORE removing tz.
    return dt.replace(tzinfo=None) if dt else None


def classify_snapshot_observability(snap) -> str:
    """逐单分类：DYNAMICS_OBSERVED / PARTIAL / PRICE_ONLY / NONE。

    只按 DYNAMICS_FIELDS 的真实非空计数；price/atr/adx/funding 等属于普通观测，
    不算动力学链。全 null 空壳不再是「有快照」，杜绝表面可观测、实际不可归因。
    """
    if not isinstance(snap, dict) or not snap:
        return "NONE"
    n = sum(1 for k in DYNAMICS_FIELDS if snap.get(k) is not None)
    if n == 0:
        return "PRICE_ONLY"
    if n >= DYNAMICS_OBSERVED_MIN:
        return "DYNAMICS_OBSERVED"
    return "PARTIAL"


def prune_snapshot(snap):
    """剔除值为 null 的字段；可观测性判定由 snapshot_observability 标签承载，
    不再让模型在 22 个 null 里自行数证据。"""
    if not isinstance(snap, dict):
        return None
    pruned = {k: v for k, v in snap.items() if v is not None}
    return pruned or None


def audit_snapshot_observability(closed_trades) -> Dict[str, int]:
    total = len(closed_trades)
    counts = {"DYNAMICS_OBSERVED": 0, "PARTIAL": 0, "PRICE_ONLY": 0, "NONE": 0}
    for t in closed_trades:
        tag = str(t.get("snapshot_observability") or "NONE")
        counts[tag] = counts.get(tag, 0) + 1
    counts["total"] = total
    counts["math_observable"] = counts["DYNAMICS_OBSERVED"] + counts["PARTIAL"]
    return counts


def render_observability_brief(audit) -> str:
    return (
        f"已平仓 {audit['total']} 笔 | 开仓时刻数理快照：完全可观测 {audit['DYNAMICS_OBSERVED']} / "
        f"部分可观测 {audit['PARTIAL']} / 仅价格与普通观测 {audit['PRICE_ONLY']} / 无快照 {audit['NONE']}"
    )


def evolution_fallback_model() -> Optional[str]:
    """复盘专属回退模型：主模型网关故障时，按后台模型池顺序选下一个候选。

    同网关优先（2026-09-10）：模型池可能横跨多域名（tokenrhythm/cpa 混布），
    死域上的席位（如 cpa 的 gemini）回退过去也是 400/504，故先选与激活模型
    同 base_url 的健康池成员，其次才考虑异域名候选。

    刻意只作用于自进化复盘调用——交易主脑的选模与回退链是风险行为，
    调整需用户批准（fallback_model_ids 属全局配置，本函数绝不改写）。
    """
    try:
        from r20_backend.llm_manager import init_llm_config
        cfg = init_llm_config() or {}
        active = str(cfg.get("active_model_id") or "").strip()
        models = [m for m in (cfg.get("models") or []) if isinstance(m, dict)]
        active_base = ""
        for m in models:
            if str(m.get("id") or "").strip() == active:
                active_base = str(m.get("base_url") or "").strip()
                break
        same_gw, other_gw = [], []
        for m in models:
            mid = str(m.get("id") or "").strip()
            if not mid or mid == active:
                continue
            (same_gw if str(m.get("base_url") or "").strip() == active_base else other_gw).append(mid)
        for mid in same_gw + other_gw:
            return mid
    except Exception as exc:
        log_msg(f"复盘回退模型解析失败: {exc}")
    return None


def load_signal_journal():
    """读取开仓时刻的数理快照日志，按标的分组，供平仓台账 join 真实因果证据。"""
    journal_file = os.path.join(DATA_DIR, "signal_journal.json")
    by_inst = {}
    if not os.path.exists(journal_file):
        return by_inst
    try:
        with open(journal_file, "r", encoding="utf-8") as f:
            for rec in json.load(f):
                inst = str(rec.get("name") or rec.get("inst") or "")
                if inst:
                    by_inst.setdefault(inst, []).append(rec)
    except Exception as e:
        log_msg(f"读取 signal_journal 异常: {e}")
    return by_inst


def _match_snapshot(journal_by_inst, inst, open_time, side=None):
    """按方向与开仓时间就近匹配开仓时刻快照（不晚于开仓、且在 join 窗口内的最后一条）。

    三条因果铁律（2026-09-10）：
    1. 方向必须一致——台账方向可解析且 journal 记录带方向时，不一致者跳过，
       防止把空头开仓快照当多头成因；
    2. 禁止未来快照——旧版无候选时回填 candidates[0] 会把开仓之后的行情写进
       「开仓证据」，属于典型的倒推伪造通道，现一律返回 None 标记不可观测；
    3. 禁止过期证据——快照距开仓超过 SNAPSHOT_MAX_STALE_SECONDS 即非本次开仓
       的因果现场，弃用。
    """
    candidates = journal_by_inst.get(inst) or []
    open_dt = _parse_bj(open_time)
    if not candidates or open_dt is None:
        return None
    wanted_side = SIDE_ALIASES.get(str(side or "").strip())
    best_dt, best_rec = None, None
    for rec in candidates:
        rec_side = SIDE_ALIASES.get(str(rec.get("side") or "").strip())
        if wanted_side and rec_side and rec_side != wanted_side:
            continue
        rec_dt = _parse_bj(rec.get("entryTime"))
        if rec_dt is None or rec_dt > open_dt:
            continue
        if (open_dt - rec_dt).total_seconds() > SNAPSHOT_MAX_STALE_SECONDS:
            continue
        if best_dt is None or rec_dt > best_dt:
            best_dt, best_rec = rec_dt, rec
    return (best_rec or {}).get("snapshot")


def load_closed_trades():
    account_init_file = os.path.join(DATA_DIR, "account_initial_state.json")
    reset_time_str = "1970-01-01 00:00:00"
    if os.path.exists(account_init_file):
        try:
            with open(account_init_file, "r", encoding="utf-8") as f:
                acc_init = json.load(f)
                reset_time_str = acc_init.get("reset_time", "1970-01-01 00:00:00")
        except Exception:
            pass

    journal_by_inst = load_signal_journal()
    closed_trades = []
    if os.path.exists(LEDGER_JSON_FILE):
        try:
            with open(LEDGER_JSON_FILE, "r", encoding="utf-8") as f:
                t_list = json.load(f)
                for t in t_list:
                    if t.get("status") == "holding":
                        continue
                    
                    c_time = str(t.get("close_time") or t.get("time") or "")
                    if c_time and c_time < reset_time_str:
                        continue

                    inst = str(t.get("inst") or t.get("name") or "OTHER")
                    if inst not in TARGET_INSTRUMENTS:
                        continue
                    pnl = float(t.get("pnl", 0.0) or 0.0)
                    gross = float(t.get("gross_pnl", pnl) or pnl)
                    fee = abs(float(t.get("fee", 0.0) or 0.0))
                    strat = str(t.get("strategy") or "⚡ 趋势")
                    reason = str(t.get("exit_reason") or t.get("remark") or "")

                    # join 铁律：方向一致、非未来、非过期；宿主逐单标注可观测性
                    raw_side = str(t.get("side") or t.get("direction") or "")
                    snap = t.get("signal_snapshot") or _match_snapshot(
                        journal_by_inst, inst, t.get("open_time"), raw_side)
                    observability = classify_snapshot_observability(snap)
                    closed_trades.append({
                        "inst": inst,
                        "side": raw_side,
                        "time": c_time,
                        "open_time": t.get("open_time", ""),
                        "strategy": strat,
                        "margin": t.get("margin", "--"),
                        "gross_pnl": round(gross, 2),
                        "fee": round(fee, 2),
                        "net_pnl": round(pnl, 2),
                        "exit_reason": reason,
                        "snapshot_observability": observability,
                        "entry_snapshot": prune_snapshot(snap),
                    })
        except Exception as e:
            log_msg(f"读取交易台账异常: {e}")

    return closed_trades

EVOLUTION_SYSTEM_PROMPT = """你是 R20 Quantum Trader 的首席投资官，负责基于真实已平仓交易证据进行认知复盘。模型只输出严格 JSON；宿主程序负责北京时间戳与 Markdown 渲染。

【证据纪律】
1. 只允许根据输入台账中真实可见的字段归因；不得把盈亏结果倒推成未提供的微积分、定积分、概率、新闻或聪明钱事实。
2. 宿主已逐单标注 snapshot_observability 并前置注入确定性可观测性审计（非模型推断）：仅 DYNAMICS_OBSERVED 可对该单全链路数理归因，PARTIAL 只允许引用其 entry_snapshot 中实际非空的字段；PRICE_ONLY / NONE 一律按「数理快照不可观测」处理，严禁对 v/a/j/I、energy_integral、deviation_area_integral、延续/击穿概率、VaR/CVaR 作任何因果陈述或假设性归因，不得编造；缺失本身不得被解读成「动力学异常」等证据。
3. 单笔交易或小样本通常不足以证伪长期规律。证据不足时允许 NO_CHANGE，禁止为了每日报告强行制造新心法。
4. 长期记忆只是软启发式，永远不得弱化数据有效性、4H 方向否决、R:R、ATR、杠杆、保证金、OCO、禁止逆势补仓或 JSON 契约等硬风控。
5. 同时审查盈利与亏损、手续费、仓位规模、退出原因和反例；区分已验证事实、待验证假设与随机波动。
6. 基准心法（is_baseline）属宪法级记忆：你的输出只能新增或限定，不能物理删除；宿主会把清单中被省略的基准心法原样补回并留痕。若你依据充分反例认定某条基准已失效，写入 diagnosis_insights 交人工复核，而不是从 ai_long_term_memory 中静默删掉它。

【记忆更新规则】
- ADD：多个独立样本支持新的可复用经验。
- REVISE：新证据明确限定旧经验的适用条件。
- INVALIDATE：充分反例证明旧经验失效。
- NO_CHANGE：证据不足、无新增交易或结论无法区分策略问题与随机性。
- 输出 0~4 条结论即可；没有高质量新证据时宁可空数组，不得凑数。

必须输出严格 JSON 对象，不得输出 Markdown、代码围栏或额外解释。
"""

def resolve_memory_update(change_status: str, proposed_memory: Any, existing_memory: List[str]) -> Tuple[str, List[str], bool]:
    """Normalize LLM memory change and preserve existing lessons when evidence is insufficient."""
    status = str(change_status or "NO_CHANGE").upper()
    if status not in {"NO_CHANGE", "ADD", "REVISE", "INVALIDATE"}:
        status = "NO_CHANGE"
    proposed = proposed_memory if isinstance(proposed_memory, list) else []
    # 心法条目同受模型 schema 漂移影响，入库前统一压平为字符串
    proposed = [s for s in (_coerce_display_str(x) for x in proposed) if s]
    preserve = status == "NO_CHANGE" or not proposed
    return status, list(existing_memory if preserve else proposed), preserve


def merge_memory_with_constitution(change_status: str, proposed_texts: List[str],
                                   existing_lessons: List[Dict[str, Any]]) -> Tuple[List[str], List[str]]:
    """基准心法宪法级保护（2026-09-10，落实「NO_CHANGE 全量保留」纪律的推广形态）。

    - ADD 为纯追加：现有全部条目保留 + 新增条目去重后置；
    - REVISE / INVALIDATE：模型可整理非基准战术层，但任何被省略的基准心法
      （is_baseline）由宿主原样补回——大模型复盘无权物理删除宪法级记忆，
      证伪基准必须走 diagnosis_insights → 人工/管理端复核通道；
    - 返回 (最终清单, 被强制补回的基准心法)。
    """
    def _text(lesson):
        return _coerce_display_str(lesson.get("rule_text") or "")

    enabled = [l for l in (existing_lessons or []) if isinstance(l, dict) and l.get("enabled")]
    existing_texts = [t for t in (_text(l) for l in enabled) if t]
    baseline_texts = [t for t in (_text(l) for l in enabled if l.get("is_baseline")) if t]

    final: List[str] = []
    for p in proposed_texts or []:
        t = _coerce_display_str(p)
        if t and t not in final:
            final.append(t)
    if change_status == "ADD":
        final = existing_texts + [t for t in final if t not in existing_texts]
    readded = [t for t in baseline_texts if t not in final]
    return final + readded, readded


def compose_evolution_prompts(closed_trades: List[Dict[str, Any]], existing_memory_md: str = "", timestamp_str: str = "") -> Tuple[str, str, str, Dict[str, int]]:
    """组装自进化 System/User 提示词，并前置注入宿主确定性数理快照可观测性审计。

    返回 (system, user, now_bj_str, snapshot_audit)。审计由宿主统计而非模型自数
    null，从结构上杜绝「表面有快照、实际全空值」诱发的倒推伪造。
    """
    tz_bj = datetime.timezone(datetime.timedelta(hours=8))
    now_bj_str = timestamp_str or datetime.datetime.now(tz_bj).strftime("%Y-%m-%d %H:%M:%S (北京时间)")

    total = len(closed_trades)
    wins = [t for t in closed_trades if t["net_pnl"] > 0]
    losses = [t for t in closed_trades if t["net_pnl"] <= 0]
    win_rate = round(len(wins) / total * 100, 1) if total > 0 else 0.0
    total_net = round(sum(t["net_pnl"] for t in closed_trades), 2)
    total_fees = round(sum(t["fee"] for t in closed_trades), 2)
    snapshot_audit = audit_snapshot_observability(closed_trades)
    observability_brief = render_observability_brief(snapshot_audit)

    v_counts: Dict[str, int] = {}
    for t in closed_trades:
        v = str(t.get("venue") or "okx").upper()
        v_counts[v] = v_counts.get(v, 0) + 1
    v_summary = ", ".join(f"{v}: {c}笔" for v, c in sorted(v_counts.items())) if v_counts else "无"

    memory_context = f"""======================= 【当前系统已有的历史长期记忆库】 =======================
{existing_memory_md.strip()}
""" if existing_memory_md.strip() else "当前长期记忆库为空 (系统初始冷启动状态)"

    prompt = f"""======================= 【当前认知复盘基准时间】 =======================
【复盘基准时间】: {now_bj_str}

{memory_context}

======================= 【R20 加密量化实盘战绩与历史交易台账】 =======================
【统计汇总】:
- 总平仓笔数: {total} 笔 (胜 {len(wins)} / 负 {len(losses)} | 胜率: {win_rate}%)
- 跨交易所分布: {v_summary}
- 累计净盈亏: {total_net:+.2f} USDT | 累计手续费消耗: {total_fees:.2f} USDT
- 当前聚焦标的池: {TARGET_INSTRUMENTS}

【逐笔历史交易明细 (按时间排序)】:
{json.dumps(closed_trades, indent=2, ensure_ascii=False)}

【复盘与长期记忆进化任务】:
请严格基于可观测台账证据复盘。以宿主注入的「数理快照可观测性审计」为准：对 PRICE_ONLY / NONE 的交易不得输出任何数理因果，只能标注“数理快照不可观测”。证据不足时使用 NO_CHANGE，不得强行生成新规律。输出标准 JSON：
{{
  "change_status": "NO_CHANGE" | "ADD" | "REVISE" | "INVALIDATE",
  "diagnosis_insights": [
    "0~4 条有台账字段支持的诊断；区分已验证事实与待验证假设"
  ],
  "evolution_actions": [
    "0~4 条可执行改进；证据不足时只提出数据采集或观察建议"
  ],
  "ai_long_term_memory": [
    "生效后的完整心法清单：必须原样包含现有全部基准心法（宿主会把省略的基准补回并留痕），新增条目须有多个独立样本支持；不得覆盖任何硬风控"
  ],
  "memory_overwrites_reason": "说明证据支持何种变更；NO_CHANGE 时明确为何不覆盖旧记忆"
}}
"""

    profile = active_profile()
    runtime_context = {
        "decision_timestamp": now_bj_str, "timestamp": now_bj_str,
        "timestamp_beijing": now_bj_str,
        "trading_memory": existing_memory_md.strip(),
        "existing_memory_markdown": existing_memory_md.strip() or "当前长期记忆库为空 (系统初始冷启动状态)",
        "total": total, "wins": len(wins), "losses": len(losses), "win_rate": win_rate,
        "total_net": f"{total_net:+.2f}", "total_fees": f"{total_fees:.2f}",
        "target_instruments": ", ".join(TARGET_INSTRUMENTS),
        "closed_trades_json": json.dumps(closed_trades, indent=2, ensure_ascii=False),
        "active_instruments": ",".join(TARGET_INSTRUMENTS),
        "snapshot_observability_summary": observability_brief,
        "dynamics_observable_trades": snapshot_audit["math_observable"],
        "unobservable_trades": snapshot_audit["PRICE_ONLY"] + snapshot_audit["NONE"],
        "profile_name": profile.get("name", ""), "timezone": "Asia/Shanghai",
        "strategy_version": os.getenv("R20_VERSION", f"v{__version__}"),
    }
    effective_evolution_system = apply_module_layout(EVOLUTION_SYSTEM_PROMPT, profile, "evolution_system", f"{profile.get('name', '稳健')}自进化系统提示词模板", context=runtime_context)
    effective_evolution_user = apply_module_layout(prompt, profile, "evolution_user", f"{profile.get('name', '稳健')}自进化用户提示词模板", context=runtime_context)
    # 宿主宪章：代码层硬约束，在风格档案 layout 之后强制追加——profile 只能调整
    # 措辞风格，永远无法删改证据纪律与基准心法保护（Code is Law，2026-09-10）。
    host_constitution = (
        "\n\n======================= 【宿主宪章·代码层硬约束（任何提示词风格档案不可覆盖）】 =======================\n"
        f"1. 数理快照可观测性审计（宿主确定性统计，非模型推断）：{observability_brief}。\n"
        "2. 逐单标注含义：DYNAMICS_OBSERVED=开仓动力学/积分/概率链完整，可作数理因果归因；"
        "PARTIAL=仅可引用 entry_snapshot 中实际非空字段；PRICE_ONLY / NONE=数理快照不可观测，"
        "严禁编造或倒推 v/a/j/I、energy_integral、deviation_area_integral、延续/击穿概率、VaR/CVaR 因果，"
        "字段缺失本身不得解读为任何证据。\n"
        "3. ai_long_term_memory 给出生效后完整清单时必须原样包含全部现有基准心法（is_baseline）："
        "省略条目会被宿主原样补回并留痕；认定基准失效只能写入 diagnosis_insights 交人工复核，禁止静默删除。\n"
        "4. 证据不足必须 NO_CHANGE；NO_CHANGE 永不覆盖或清空长期记忆。\n"
    )
    effective_evolution_system = effective_evolution_system.rstrip() + host_constitution
    effective_evolution_user = effective_evolution_user.rstrip() + host_constitution
    return effective_evolution_system, effective_evolution_user, now_bj_str, snapshot_audit


def call_llm_evolution_review(closed_trades: List[Dict[str, Any]], existing_memory_md: str = "", timestamp_str: str = "",
                              model_override: Optional[str] = None) -> Dict[str, Any]:
    base_url, api_key = get_cpa_client_config()
    if not api_key:
        log_msg("[AI Evolution] Error: CPA API Key not found, using fallback heuristics.")
        return {}

    effective_evolution_system, effective_evolution_user, now_bj_str, _audit = compose_evolution_prompts(
        closed_trades, existing_memory_md=existing_memory_md, timestamp_str=timestamp_str)
    try:
        snapshot = f"【SYSTEM PROMPT】:\n{effective_evolution_system.strip()}\n\n{'='*70}\n【USER PROMPT ({now_bj_str})】：\n{effective_evolution_user.strip()}"
        fd, temp_path = tempfile.mkstemp(prefix=".evolution-prompt-", suffix=".tmp", dir=DATA_DIR)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(snapshot)
        os.replace(temp_path, EVOLUTION_LAST_PROMPT_FILE)
    except OSError:
        pass

    model_name = os.environ.get("LLM_MODEL") or ""
    effort = os.environ.get("LLM_REASONING_EFFORT") or "high"
    api_format = "openai_chat"
    try:
        from r20_backend.llm_manager import get_active_llm_runtime, execute_llm_request
        active_llm = get_active_llm_runtime()
        model_name = os.environ.get("LLM_MODEL") or active_llm.get("model") or model_name
        effort = os.environ.get("LLM_REASONING_EFFORT") or active_llm.get("reasoning_effort") or effort
        api_format = active_llm.get("api_format", "openai_chat")
        base_url = active_llm.get("base_url") or base_url
        api_key = active_llm.get("api_key") or api_key
        thinking_timeout = max(90.0, float(active_llm.get("thinking_timeout") or os.environ.get("LLM_THINKING_TIMEOUT", 120.0)))
    except Exception:
        execute_llm_request = None
        thinking_timeout = max(90.0, float(os.environ.get("LLM_THINKING_TIMEOUT", os.environ.get("LLM_TIMEOUT_SECONDS", 120.0))))
    if model_override:
        # 复盘专属回退模型：优先于 env 与主脑激活位（见 evolution_fallback_model）
        model_name = str(model_override)

    telemetry = ModelCallTelemetry(
        "self_improvement", model_name, str(effort), effective_evolution_system, effective_evolution_user
    )
    try:
        t0 = time.time()
        log_msg(f"🚀 正在调用 {model_name} ({api_format} / 思考上限 {thinking_timeout:.0f}s) 进行 AI 大脑深度认知复盘与策略参数优化...")
        raw_res = None
        content = ""
        if execute_llm_request:
            content, _, usage_dict, _ = execute_llm_request(
                messages=[
                    {"role": "system", "content": effective_evolution_system},
                    {"role": "user", "content": effective_evolution_user}
                ],
                model=model_name,
                base_url=base_url,
                api_key=api_key,
                api_format=api_format,
                reasoning_effort=effort,
                temperature=0.2,
                response_format={"type": "json_object"},
                timeout=thinking_timeout,
            )
            raw_res = {"usage": usage_dict} if isinstance(usage_dict, dict) else {}
        else:
            payload = {
                "model": model_name,
                "messages": [
                    {"role": "system", "content": effective_evolution_system},
                    {"role": "user", "content": effective_evolution_user}
                ],
                "temperature": 0.2,
                "response_format": {"type": "json_object"}
            }
            if effort not in ("none", "auto"):
                payload["reasoning_effort"] = effort
            req = urllib.request.Request(
                f"{base_url}/chat/completions",
                data=json.dumps(payload).encode("utf-8"),
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json", "User-Agent": "Mozilla/5.0"}
            )
            with urllib.request.urlopen(req, timeout=thinking_timeout) as resp:
                res = json.loads(resp.read().decode("utf-8"))
                content = res["choices"][0]["message"]["content"].strip()
                raw_res = res

        content = (content or "").strip()
        if content.startswith("```json"):
            content = content[7:]
        if content.startswith("```"):
            content = content[3:]
        if content.endswith("```"):
            content = content[:-3]
        
        review_json = json.loads(content.strip())
        if not isinstance(review_json, dict):
            review_json = {}
        telemetry.finish("success", raw_res, output_chars=len(content))
        log_msg(f"✅ AI 大脑认知复盘完成 (耗时 {round(time.time() - t0, 2)}s)")
        return review_json
    except Exception as e:
        telemetry.finish("failed", error=e)
        log_msg(f"Error in LLM evolution review: {e}")
        # Surface the upstream failure in the dashboard report instead of silently
        # degrading to an unexplained NO_CHANGE (which looks like a stale cache).
        return {"__llm_error__": f"{type(e).__name__}: {e}"}


_TEXTISH_KEYS = (
    "observation", "detail", "text", "action", "content", "analysis",
    "finding", "summary", "description", "reason", "evidence",
)


def _coerce_display_str(item) -> str:
    """把复盘数组项归一为展示字符串。

    - 字符串原样；若内容是自序列化的 JSON（模型常见漂移）则解包递归处理；
    - 对象：优先【dimension/title/category】+ 已知正文字段；action_type 类对象
      用其作标题；无已知键时按 key:value 拼接，绝不落回 str(dict)。
    """
    if isinstance(item, str):
        s = item.strip()
        if s.startswith("{") or s.startswith("["):
            try:
                parsed = json.loads(s)
            except Exception:
                return s
            if isinstance(parsed, (dict, list)):
                return _coerce_display_str(parsed)
        return s
    if isinstance(item, dict):
        title = str(item.get("dimension") or item.get("title") or item.get("category")
                    or item.get("action_type") or "").strip()
        body = ""
        for k in _TEXTISH_KEYS:
            v = item.get(k)
            if isinstance(v, (str, int, float)) and str(v).strip():
                body = str(v).strip()
                break
        if not body:
            parts = [f"{k}:{v}" for k, v in item.items()
                     if not isinstance(v, (dict, list)) and str(v).strip() and k != "dimension"]
            body = "；".join(parts)
        if title and body and not body.startswith(f"【{title}】"):
            return f"【{title}】{body}"
        return body or title
    if isinstance(item, list):
        return "；".join(filter(None, (_coerce_display_str(x) for x in item)))
    return str(item).strip() if item is not None else ""


@single_evolution_cycle
def run_self_evolution(force: bool = False):
    tz_bj = datetime.timezone(datetime.timedelta(hours=8))
    now_bj = datetime.datetime.now(tz_bj)
    timestamp_str = now_bj.strftime("%Y-%m-%d %H:%M:%S")
    log_msg(f"🧬 启动 R20 AI 大脑自进化认知复盘与实战心法提炼 (v{__version__} Crypto Focus)...")

    closed_trades = load_closed_trades()
    total_trades = len(closed_trades)
    ledger_revision = hashlib.sha256(
        json.dumps(closed_trades, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    if not force and os.path.exists(REPORT_JSON_FILE):
        try:
            with open(REPORT_JSON_FILE, "r", encoding="utf-8") as f:
                previous_report = json.load(f)
            if previous_report.get("ledger_revision") == ledger_revision:
                log_msg("No new closed-trade evidence; keeping the current adaptive configuration")
                return previous_report
        except Exception:
            pass

    # 1. Base Stats
    win_trades = [t for t in closed_trades if t["net_pnl"] > 0]
    loss_trades = [t for t in closed_trades if t["net_pnl"] <= 0]
    win_count = len(win_trades)
    win_rate = round(win_count / total_trades * 100, 1) if total_trades > 0 else 0.0
    total_win_amt = sum(t["net_pnl"] for t in win_trades)
    total_loss_amt = abs(sum(t["net_pnl"] for t in loss_trades))
    total_fees_amt = sum(t["fee"] for t in closed_trades)
    profit_factor = round(total_win_amt / total_loss_amt, 2) if total_loss_amt > 0 else (99.0 if total_win_amt > 0 else 0.0)

    from scripts import evolution_shield as memory_service
    memory_snapshot, existing_memory_md, existing_core_lessons = memory_service.read_trading_context(
        AI_MEMORY_MD_FILE, AI_MEMORY_FILE)

    # 宿主确定性数理快照可观测性审计（写进报告，结论不依赖模型自数 null）
    snapshot_audit = audit_snapshot_observability(closed_trades)
    constitution_readded: List[str] = []
    log_msg("🔬 数理快照可观测性审计: " + render_observability_brief(snapshot_audit))

    # 2. Call LLM for Cognitive Review & Memory Overwriting
    # 复盘预算守卫：调度器对子进程有 600s 硬超时，504 内部重试可达 ~560s。
    # 主调用超过 EVOLUTION_FALLBACK_BUDGET_SECONDS 后不再回退（回退大概率被腰斩，
    # 徒耗一池模型调用；本周期照常落 NO_CHANGE + 错误透传）。
    EVOLUTION_FALLBACK_BUDGET_SECONDS = 400.0
    cycle_t0 = time.time()
    llm_review = call_llm_evolution_review(closed_trades, existing_memory_md=existing_memory_md, timestamp_str=timestamp_str)
    if not isinstance(llm_review, dict):
        llm_review = {}
    # 复盘专属单次回退（2026-09-10）：qwen3.8-flash 网关 504 曾连续吞掉 09-09 与
    # 验证轮复盘；换池内下一个模型重试一次，交易主脑选模不受影响。
    if llm_review.get("__llm_error__"):
        fallback_model = evolution_fallback_model()
        elapsed = time.time() - cycle_t0
        if fallback_model and elapsed > EVOLUTION_FALLBACK_BUDGET_SECONDS:
            log_msg(f"⏳ 复盘主模型已耗时 {elapsed:.0f}s 超预算 {EVOLUTION_FALLBACK_BUDGET_SECONDS:.0f}s，"
                    f"放弃回退避免调度器 600s 腰斩（NO_CHANGE + 错误透传）")
        elif fallback_model:
            log_msg(f"⚠️ 复盘主模型失败（{str(llm_review['__llm_error__'])[:120]}），回退 {fallback_model} 重试一次")
            fb_review = call_llm_evolution_review(
                closed_trades, existing_memory_md=existing_memory_md,
                timestamp_str=timestamp_str, model_override=fallback_model)
            if isinstance(fb_review, dict) and fb_review and not fb_review.get("__llm_error__"):
                llm_review = fb_review
                log_msg(f"✅ 回退模型 {fallback_model} 复盘完成（仅本周期；不改全局激活位）")

    change_status, _, _ = resolve_memory_update(llm_review.get("change_status", "NO_CHANGE"), [], [])
    insights = llm_review.get("diagnosis_insights", [])
    actions_taken = llm_review.get("evolution_actions", [])
    if not isinstance(insights, list):
        insights = []
    if not isinstance(actions_taken, list):
        actions_taken = []
    # 模型 schema 漂移归一：部分模型把数组项输出为对象（{dimension, analysis} /
    # {action_type, action}）或自序列化 JSON 字符串；不归一则前端渲染成
    # [object Object] / 原始 JSON（2026-09-09 用户截图）。统一压平成展示字符串。
    insights = [s for s in (_coerce_display_str(x) for x in insights) if s]
    actions_taken = [s for s in (_coerce_display_str(x) for x in actions_taken) if s]
    
    raw_asset_mults = llm_review.get("asset_multipliers", {})
    if not isinstance(raw_asset_mults, dict):
        raw_asset_mults = {}
    asset_mults = {
        asset: clamp(raw_asset_mults.get(asset, 1.0), 0.5, 1.5, 1.0)
        for asset in TARGET_INSTRUMENTS
    }
    change_status, long_term_memory, preserve_existing_memory = resolve_memory_update(
        change_status, llm_review.get("ai_long_term_memory", []), existing_core_lessons
    )

    if not preserve_existing_memory:
        # Safe extraction: convert potential dicts {"rule_text": "..."} to string safely
        safe_long_term = []
        for item in long_term_memory:
            if isinstance(item, dict):
                val = str(item.get("rule_text") or item.get("text") or item.get("lesson") or "").strip()
            else:
                val = str(item or "").strip()
            if val:
                safe_long_term.append(val)
        # 宪法级保护：基准心法不允许被进化输出物理删除（2026-09-10）
        safe_long_term, constitution_readded = merge_memory_with_constitution(
            change_status, safe_long_term, memory_snapshot.get("lessons") or [])
        if constitution_readded:
            log_msg(f"🛡️ 进化输出遗漏/试图删除 {len(constitution_readded)} 条基准心法，宿主已按宪法补回保留")

        try:
            published = memory_service.publish_review(
                safe_long_term, expected_version=memory_snapshot["version"],
                sample_size=total_trades, change_status=change_status)
            preserve_existing_memory = not published
        except Exception as exc:
            preserve_existing_memory = True
            log_msg(f"Memory publication rejected; retaining authority: {exc}")

    # Keep the legacy markdown mirror in lock-step with the authority so the
    # public dashboard can never freeze on a hand-edited snapshot.
    try:
        if memory_service.sync_markdown_mirror():
            log_msg("🪞 AI_TRADING_MEMORY.md 已同步至结构化心法权威库")
    except Exception as exc:
        log_msg(f"Markdown mirror sync skipped: {exc}")

    # Persist asset multipliers to data/asset_multipliers.json so brain trader can consume
    try:
        mults_payload = {
            "timestamp": timestamp_str,
            "multipliers": asset_mults,
            "updated_by": "self_improvement_engine",
        }
        atomic_write_json(os.path.join(DATA_DIR, "asset_multipliers.json"), mults_payload)
    except Exception as exc:
        log_msg(f"Failed to persist asset multipliers: {exc}")

    # Reflect concurrent toggle/rollback even when the model returns NO_CHANGE.
    _, _, long_term_memory = memory_service.read_trading_context(AI_MEMORY_MD_FILE, AI_MEMORY_FILE)

    # 4. Save Dashboard Report
    report_payload = {
        "timestamp": timestamp_str,
        "ledger_revision": ledger_revision,
        "total_trades": total_trades,
        "win_rate": win_rate,
        "profit_factor": profit_factor,
        "mode": "R20 Native Heuristic Memory (启发式长期记忆)",
        "change_status": change_status,
        "memory_preserved": preserve_existing_memory,
        "insights": insights,
        "diagnosis_insights": insights,
        "memory_overwrites_reason": llm_review.get("memory_overwrites_reason", ""),
        "actions_taken": actions_taken,
        "core_lessons": long_term_memory,
        "snapshot_audit": snapshot_audit,
        "baseline_memory_protected": len(constitution_readded),
        "llm_error": str(llm_review.get("__llm_error__") or ""),
    }

    atomic_write_json(REPORT_JSON_FILE, report_payload)

    log_msg(f"🧬 自进化认知复盘完成 | 状态={change_status} | 当前保留 {len(long_term_memory)} 条启发式长期记忆")
    try:
        from qq_notifier import notify_evolution_report
        top_lesson = long_term_memory[0] if long_term_memory else "保持风控原则"
        notify_evolution_report(win_rate, total_trades, change_status, top_lesson)
    except Exception as e:
        log_msg(f"自进化通知发送失败: {e}")
    return report_payload

if __name__ == "__main__":
    force_run = "--force" in sys.argv or "-f" in sys.argv
    res = run_self_evolution(force=force_run)
    print(json.dumps(res, indent=2, ensure_ascii=False))
