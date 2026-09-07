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
_LEDGER_DEFAULT = LEDGER_JSON_FILE
_REPORT_DEFAULT = REPORT_JSON_FILE


def _ledger_file() -> str:
    if LEDGER_JSON_FILE != _LEDGER_DEFAULT:
        return LEDGER_JSON_FILE
    from r20_exchange.runtime import state_path
    return str(state_path("trading_ledger.json"))


def _report_file() -> str:
    if REPORT_JSON_FILE != _REPORT_DEFAULT:
        return REPORT_JSON_FILE
    from r20_exchange.runtime import state_path
    return str(state_path("self_improvement_report.json"))



def _ledger_incomplete() -> bool:
    if LEDGER_JSON_FILE != _LEDGER_DEFAULT:
        return False
    from r20_backend.account_paths import ledger_is_incomplete
    return ledger_is_incomplete()


from r20_backend.version import __version__
from r20_backend.file_lock import acquire, release
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
            acquire(lock_handle, blocking=False)
        except BlockingIOError:
            lock_handle.close()
            log_msg("Self-evolution skipped: another cycle is still running")
            return None
        try:
            return func(*args, **kwargs)
        finally:
            try:
                release(lock_handle)
            except OSError:
                pass
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


def _match_snapshot(journal_by_inst, inst, open_time):
    """按开仓时间就近匹配（不晚于开仓时间的最后一条）开仓快照。"""
    candidates = journal_by_inst.get(inst) or []
    if not candidates or not open_time:
        return None
    best = None
    for rec in candidates:
        if str(rec.get("entryTime") or "") <= str(open_time):
            if best is None or str(rec.get("entryTime") or "") > str(best.get("entryTime") or ""):
                best = rec
    if best is None and candidates:
        best = candidates[0]
    return (best or {}).get("snapshot")


def load_closed_trades():
    if _ledger_incomplete():
        log_msg("台账不完整，跳过自进化归因")
        return []

    reset_time_str = "1970-01-01 00:00:00"
    if LEDGER_JSON_FILE != _LEDGER_DEFAULT:
        account_init_file = os.path.join(DATA_DIR, "account_initial_state.json")
        if os.path.exists(account_init_file):
            try:
                with open(account_init_file, "r", encoding="utf-8") as f:
                    acc_init = json.load(f)
                    reset_time_str = acc_init.get("reset_time", "1970-01-01 00:00:00")
            except Exception:
                pass
    else:
        try:
            from r20_backend.account_baseline import load_account_baseline
            reset_time_str = load_account_baseline().get("reset_time", "1970-01-01 00:00:00")
        except Exception:
            pass

    journal_by_inst = load_signal_journal()
    closed_trades = []
    path = _ledger_file()
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
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

                    closed_trades.append({
                        "inst": inst,
                        "time": c_time,
                        "open_time": t.get("open_time", ""),
                        "strategy": strat,
                        "margin": t.get("margin", "--"),
                        "gross_pnl": round(gross, 2),
                        "fee": round(fee, 2),
                        "net_pnl": round(pnl, 2),
                        "exit_reason": reason,
                        "entry_snapshot": t.get("signal_snapshot") or _match_snapshot(journal_by_inst, inst, t.get("open_time")),
                    })
        except Exception as e:
            log_msg(f"读取交易台账异常: {e}")

    return closed_trades

EVOLUTION_SYSTEM_PROMPT = """你是 R20 Quantum Trader 的首席投资官，负责基于真实已平仓交易证据进行认知复盘。模型只输出严格 JSON；宿主程序负责北京时间戳与 Markdown 渲染。

【证据纪律】
1. 只允许根据输入台账中真实可见的字段归因；不得把盈亏结果倒推成未提供的微积分、定积分、概率、新闻或聪明钱事实。
2. 只有输入中存在对应开平仓快照时，才可对 v/a/j/I、energy_integral、deviation_area_integral、延续/击穿概率、VaR/CVaR 作因果分析；否则必须明确标记“数理快照不可观测”，不得编造。
3. 单笔交易或小样本通常不足以证伪长期规律。证据不足时允许 NO_CHANGE，禁止为了每日报告强行制造新心法。
4. 长期记忆只是软启发式，永远不得弱化数据有效性、4H 方向否决、R:R、ATR、杠杆、保证金、OCO、禁止逆势补仓或 JSON 契约等硬风控。
5. 同时审查盈利与亏损、手续费、仓位规模、退出原因和反例；区分已验证事实、待验证假设与随机波动。

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
    preserve = status == "NO_CHANGE" or not proposed
    return status, list(existing_memory if preserve else proposed), preserve


def call_llm_evolution_review(closed_trades: List[Dict[str, Any]], existing_memory_md: str = "", timestamp_str: str = "") -> Dict[str, Any]:
    base_url, api_key = get_cpa_client_config()
    if not api_key:
        log_msg("[AI Evolution] Error: CPA API Key not found, using fallback heuristics.")
        return {}

    tz_bj = datetime.timezone(datetime.timedelta(hours=8))
    now_bj_str = timestamp_str or datetime.datetime.now(tz_bj).strftime("%Y-%m-%d %H:%M:%S") + " (北京时间)"


    total = len(closed_trades)
    wins = [t for t in closed_trades if t["net_pnl"] > 0]
    losses = [t for t in closed_trades if t["net_pnl"] <= 0]
    win_rate = round(len(wins) / total * 100, 1) if total > 0 else 0.0
    total_net = round(sum(t["net_pnl"] for t in closed_trades), 2)
    total_fees = round(sum(t["fee"] for t in closed_trades), 2)

    memory_context = f"""======================= 【当前系统已有的历史长期记忆库】 =======================
{existing_memory_md.strip()}
""" if existing_memory_md.strip() else "当前长期记忆库为空 (系统初始冷启动状态)"

    prompt = f"""======================= 【当前认知复盘基准时间】 =======================
【复盘基准时间】: {now_bj_str}

{memory_context}

======================= 【R20 加密量化实盘战绩与历史交易台账】 =======================
【统计汇总】:
- 总平仓笔数: {total} 笔 (胜 {len(wins)} / 负 {len(losses)} | 胜率: {win_rate}%)
- 累计净盈亏: {total_net:+.2f} USDT | 累计手续费消耗: {total_fees:.2f} USDT
- 当前聚焦标的池: {TARGET_INSTRUMENTS}

【逐笔历史交易明细 (按时间排序)】:
{json.dumps(closed_trades, indent=2, ensure_ascii=False)}

【复盘与长期记忆进化任务】:
请严格基于可观测台账证据复盘。当前输入若未提供交易发生时的 v/a/j/I、定积分、概率或 VaR 快照，不得将盈亏事后归因于这些指标，只能标注“数理快照不可观测”。证据不足时使用 NO_CHANGE，不得强行生成新规律。输出标准 JSON：
{{
  "change_status": "NO_CHANGE" | "ADD" | "REVISE" | "INVALIDATE",
  "diagnosis_insights": [
    "0~4 条有台账字段支持的诊断；区分已验证事实与待验证假设"
  ],
  "evolution_actions": [
    "0~4 条可执行改进；证据不足时只提出数据采集或观察建议"
  ],
  "ai_long_term_memory": [
    "0~4 条有多个独立样本支持的软启发式；不得覆盖任何硬风控"
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
        "profile_name": profile.get("name", ""), "timezone": "Asia/Shanghai",
        "strategy_version": os.getenv("R20_VERSION", f"v{__version__}"),
    }
    effective_evolution_system = apply_module_layout(EVOLUTION_SYSTEM_PROMPT, profile, "evolution_system", f"{profile.get('name', '稳健')}自进化系统提示词模板", context=runtime_context)
    effective_evolution_user = apply_module_layout(prompt, profile, "evolution_user", f"{profile.get('name', '稳健')}自进化用户提示词模板", context=runtime_context)
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

@single_evolution_cycle
def run_self_evolution(force: bool = False):
    tz_bj = datetime.timezone(datetime.timedelta(hours=8))
    now_bj = datetime.datetime.now(tz_bj)
    timestamp_str = now_bj.strftime("%Y-%m-%d %H:%M:%S")
    log_msg(f"🧬 启动 R20 AI 大脑自进化认知复盘与实战心法提炼 (v{__version__} Crypto Focus)...")

    if _ledger_incomplete():
        log_msg("台账不完整，拒绝用残缺历史覆盖心法归因")
        report_path = _report_file()
        if os.path.exists(report_path):
            try:
                with open(report_path, "r", encoding="utf-8") as f:
                    previous = json.load(f)
                previous["incomplete_ledger"] = True
                return previous
            except Exception:
                pass
        return {"incomplete_ledger": True, "memory_preserved": True, "core_lessons": [], "change_status": "NO_CHANGE"}

    closed_trades = load_closed_trades()
    total_trades = len(closed_trades)
    ledger_revision = hashlib.sha256(
        json.dumps(closed_trades, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    report_path = _report_file()
    if not force and os.path.exists(report_path):
        try:
            with open(report_path, "r", encoding="utf-8") as f:
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

    # 2. Call LLM for Cognitive Review & Memory Overwriting
    llm_review = call_llm_evolution_review(closed_trades, existing_memory_md=existing_memory_md, timestamp_str=timestamp_str)
    if not isinstance(llm_review, dict):
        llm_review = {}

    change_status, _, _ = resolve_memory_update(llm_review.get("change_status", "NO_CHANGE"), [], [])
    insights = llm_review.get("diagnosis_insights", [])
    actions_taken = llm_review.get("evolution_actions", [])
    if not isinstance(insights, list):
        insights = []
    if not isinstance(actions_taken, list):
        actions_taken = []
    
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
        "llm_error": str(llm_review.get("__llm_error__") or ""),
    }

    atomic_write_json(_report_file(), report_payload)

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
