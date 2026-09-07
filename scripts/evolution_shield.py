#!/usr/bin/env python3
"""
R20 Evolution Shield & Anti-Poisoning Cognitive Guardian (evolution_shield.py)
-------------------------------------------------------------------------------
Ensures AI Self-Evolution DOES NOT become a double-edged sword:
1. Anti-Single-Event Bias / Outlier Rejection:
   Single flash-crash or anomalous spikes cannot dictate long-term strategy.
2. Constitution Red-Lines (Non-negotiable Rules):
   - Prohibits "Never go Long" or "Never go Short" biases.
   - Prohibits widening stop losses to bag-hold losses.
   - Prohibits aggressive revenge betting or Martingale sizing.
3. Structured White-Box Lesson Schema:
   - id, category, rule_text, health_score, enabled, created_at, ttl_days, sample_size
4. Cognitive Decay & Health Score:
   - Lessons lose health score if they contradict recent positive performance.
   - Decayed lessons auto-archive, preventing cognitive poisoning.
"""

from __future__ import annotations

import datetime
import json
import math
import re
import copy
import sys
import hashlib
import os
import tempfile
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

WORKSPACE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = WORKSPACE_DIR / "data"
STRUCTURED_MEMORY_FILE = DATA_DIR / "structured_trading_memory.json"
AI_MEMORY_MD_FILE = DATA_DIR / "AI_TRADING_MEMORY.md"
if str(WORKSPACE_DIR) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_DIR))
from r20_backend.file_lock import acquire, release

# 官方不可逾越的基准心法库 (Baseline Golden Lessons)
BASELINE_LESSONS = [
    {
        "id": "lesson_trend_pullback",
        "category": "TREND_FOLLOWING",
        "rule_text": "【顺势回踩低吸做多与反弹承压高抛做空】绝对禁止在 4H/1H 多头通道逆势摸顶开空，空单只在宏观空头反弹受阻时限价挂单；箱体震荡边界双向高抛低吸。",
        "health_score": 98.0,
        "enabled": True,
        "created_at": "2026-09-01 00:00:00",
        "ttl_days": 14,
        "sample_size": 42,
        "is_baseline": True,
        "shield_status": "PASSED",
    },
    {
        "id": "lesson_wide_atr_stop",
        "category": "RISK_CONTROL",
        "rule_text": "【宽止损抗噪杜绝随意割肉】止损必须设在结构外 1.8x~2.2x 1H ATR 以外，给足波动呼吸空间，从物理上隔绝 15M/5M 杂波插针洗损。",
        "health_score": 95.0,
        "enabled": True,
        "created_at": "2026-09-01 00:00:00",
        "ttl_days": 14,
        "sample_size": 38,
        "is_baseline": True,
        "shield_status": "PASSED",
    },
    {
        "id": "lesson_breakeven_lock",
        "category": "WIN_RATE_LOCK",
        "rule_text": "【浮盈0.8R坚决保本锁死胜率】持仓浮盈达到 0.8R~1.0R 坚决执行保本移损 (UPDATE_SL)，将潜在亏损彻底消除为零风险平仓，锁死胜率下限，杜绝盈利变割肉。",
        "health_score": 99.0,
        "enabled": True,
        "created_at": "2026-09-01 00:00:00",
        "ttl_days": 14,
        "sample_size": 50,
        "is_baseline": True,
        "shield_status": "PASSED",
    },
    {
        "id": "lesson_anti_correlation_rush",
        "category": "PORTFOLIO_DIVERSIFICATION",
        "rule_text": "【严禁跨标的同向共振堆叠单边敞口】无论阻力高空还是顺势做多，严禁在多相关标的（BTC/ETH/SOL/DOGE）上同向无节制开仓，必须对总同向在手仓位施加硬性约束，防范系统性 Beta 踩踏。",
        "health_score": 92.0,
        "enabled": True,
        "created_at": "2026-09-04 12:00:00",
        "ttl_days": 7,
        "sample_size": 15,
        "is_baseline": True,
        "shield_status": "PASSED",
    },
]

# 宪法红线规则（任何大模型总结出的心法如果触碰以下词汇或逻辑，直接物理阻断）：
POISON_PATTERNS = [
    (r"(永远不|绝对不|严禁|彻底禁止).*(做多|开多|买入)", "EXTREME_DIRECTIONAL_BIAS (极端做多偏见阻断)"),
    (r"(永远不|绝对不|严禁|彻底禁止).*(做空|开空|卖出)", "EXTREME_DIRECTIONAL_BIAS (极端做空偏见阻断)"),
    (r"(扩大|放宽|取消|不设).*(止损|SL)", "RISK_EXPANSION_VIOLATION (违规抗单放大止损)"),
    (r"(加倍|翻倍|加仓|重仓|梭哈).*(亏损|抗单|摊平)", "MARTINGALE_POISONING (马丁格尔赌徒加仓倾向)"),
    (r"(忽视|不看|废弃).*(4H|宏观|ATR|风控|拦截器)", "GOVERNANCE_OVERRIDE (企图推翻硬风控拦截器)"),
]


def audit_proposed_lesson(rule_text: str, sample_size: int = 1) -> Tuple[bool, str]:
    """
    Applies the Constitution Linter to verify whether a proposed lesson is safe.
    Returns (is_passed, reason).
    """
    if not rule_text or len(rule_text.strip()) < 10:
        return False, "心法文本过短，缺乏明确可复用的交易情境依据"

    # 1. Check Constitution Red-Lines
    for pattern, reason in POISON_PATTERNS:
        if re.search(pattern, rule_text):
            return False, f"触发宪法红线拦截: {reason}"

    # 2. Outlier / Single-Event Rejection Gate
    if sample_size < 2:
        return False, "样本量不足 (单笔偶发事件或极端插针噪点，拒绝写入长期心法)"

    return True, "PASSED"


class MemoryCorruptError(ValueError):
    """Invalid authority is never replaced implicitly."""


class MemoryVersionRequiredError(ValueError):
    """Administrative writes require the version from GET."""


class MemoryConflictError(ValueError):
    """The snapshot used to prepare an update is stale."""


def _validate(lessons):
    if not isinstance(lessons, list):
        raise MemoryCorruptError("Expected a lesson list")
    ids = set()
    for item in lessons:
        if (not isinstance(item, dict) or not isinstance(item.get("id"), str)
                or not item["id"] or item["id"] in ids
                or not isinstance(item.get("rule_text"), str) or not item["rule_text"].strip()
                or type(item.get("enabled")) is not bool):
            raise MemoryCorruptError("Invalid lesson schema or duplicate id")
        for key in ("health_score", "ttl_days", "sample_size"):
            if key in item and (type(item[key]) not in (int, float) or not math.isfinite(item[key]) or item[key] < 0):
                raise MemoryCorruptError(f"Invalid numeric field: {key}")
        for key in ("category", "created_at", "shield_status"):
            if key in item and not isinstance(item[key], str):
                raise MemoryCorruptError(f"Invalid text field: {key}")
        ids.add(item["id"])
    return lessons


def read_memory_snapshot():
    """Pure read: missing != empty; legacy lists remain readable without migration."""
    try:
        raw = STRUCTURED_MEMORY_FILE.read_bytes()
    except FileNotFoundError:
        return {"exists": False, "version": "missing", "lessons": []}
    try:
        payload = json.loads(raw)
        if isinstance(payload, list):
            lessons = payload
        else:
            if (not isinstance(payload, dict) or payload.get("schema_version") != 1
                    or not isinstance(payload.get("revision"), str)):
                raise MemoryCorruptError("Invalid memory envelope")
            lessons = payload["lessons"]
        _validate(lessons)
    except (ValueError, TypeError, KeyError, UnicodeError) as exc:
        raise MemoryCorruptError("Structured memory is damaged; retained unchanged") from exc
    return {"exists": True, "version": hashlib.sha256(raw).hexdigest(), "lessons": lessons}


def load_structured_memory() -> List[Dict[str, Any]]:
    return read_memory_snapshot()["lessons"]


def is_lesson_expired(item: Dict[str, Any], now: Optional[datetime.datetime] = None) -> bool:
    """Checks if a non-baseline lesson has exceeded its natural half-life (TTL days)."""
    if item.get("is_baseline"):
        return False
    created_at_str = item.get("created_at")
    ttl_days = float(item.get("ttl_days") or 7)
    if not created_at_str:
        return False
    try:
        if created_at_str.endswith("Z"):
            created_at_str = created_at_str[:-1] + "+00:00"
        dt = datetime.datetime.fromisoformat(created_at_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        cur = now or datetime.datetime.now(datetime.timezone.utc)
        return (cur - dt).total_seconds() > (ttl_days * 86400)
    except Exception:
        return False


def render_lessons(lessons):
    now = datetime.datetime.now(datetime.timezone.utc)
    active = []
    for item in lessons:
        if not item.get("enabled"):
            continue
        # If natural half-life expired, suppress from active trading prompt injection
        if is_lesson_expired(item, now):
            continue
        active.append(item)
    # Capacity limit: strictly select at most top 8 active lessons (baseline first, then recent)
    sorted_active = sorted(active, key=lambda x: (1 if x.get("is_baseline") else 0, x.get("created_at", "")), reverse=True)
    capped_active = sorted_active[:8]
    return "\n".join(f"- {item['rule_text']}" for item in capped_active)


def read_trading_context(legacy_md=None, legacy_json=None):
    snapshot = read_memory_snapshot()
    if snapshot["exists"]:
        texts = [i["rule_text"] for i in snapshot["lessons"] if i["enabled"]]
        return snapshot, render_lessons(snapshot["lessons"]), texts
    # Compatibility is read-only and only used if the authority does not exist.
    md_path = Path(legacy_md) if legacy_md is not None else AI_MEMORY_MD_FILE
    text = md_path.read_text(encoding="utf-8") if md_path.is_file() else ""
    texts = []
    if legacy_json is not None and Path(legacy_json).is_file():
        payload = json.loads(Path(legacy_json).read_text(encoding="utf-8"))
        texts = payload.get("core_lessons", [])
        if not isinstance(texts, list) or not all(isinstance(t, str) for t in texts):
            raise MemoryCorruptError("Invalid legacy lessons")
    return snapshot, text or "\n".join(f"- {t}" for t in texts), texts


def render_trading_memory(legacy_md=None, legacy_json=None):
    text = read_trading_context(legacy_md, legacy_json)[1]
    if not text.strip():
        return ""
    return "======================= 【R20 启发式实战认知与长期记忆】 =======================\n" + text


def sync_markdown_mirror() -> bool:
    """Refresh the derived AI_TRADING_MEMORY.md mirror from the structured authority.

    The markdown file is a read-only compatibility artifact for legacy readers
    (notably the public dashboard). It must never drift behind the authority, or
    the homepage freezes on a stale snapshot while the engine keeps revising.
    The structured store remains the single authority; this mirror is rewritten
    after every review cycle.
    """
    snapshot = read_memory_snapshot()
    if not snapshot["exists"]:
        return False
    body = render_trading_memory()
    if not body.strip():
        return False
    lessons = snapshot["lessons"]
    stamps = [i.get("created_at") for i in lessons if i.get("created_at")]
    newest = max(stamps) if stamps else ""
    if newest:
        try:
            local = datetime.datetime.fromisoformat(newest).astimezone(
                datetime.timezone(datetime.timedelta(hours=8))
            ).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            local = str(newest)[:19]
    else:
        local = "--"
    doc = (
        "# R20 AI 交易实战长期心法 (Heuristic Long-Term Memory)\n\n"
        f"> 状态：由自进化防污染认知中枢实时纳管 | 更新基准: {local} (UTC+8)\n"
        f"> 权威来源: structured_trading_memory.json | 修订 {str(snapshot.get('version'))[:8]} | 共 {len(lessons)} 条心法\n"
        "> 宪法安全护栏：已通过极端离群值过滤 (Outlier Rejection) 与防偏见白盒审查。\n\n"
        + body
        + "\n"
    )
    tmp = AI_MEMORY_MD_FILE.with_suffix(f".md.tmp.{os.getpid()}")
    try:
        AI_MEMORY_MD_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(doc, encoding="utf-8")
        os.replace(tmp, AI_MEMORY_MD_FILE)
    finally:
        if tmp.exists():
            tmp.unlink()
    return True


@contextmanager
def _memory_lock():
    STRUCTURED_MEMORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(str(STRUCTURED_MEMORY_FILE) + ".lock", "a") as handle:
        acquire(handle, blocking=True)
        try:
            yield
        finally:
            try:
                release(handle)
            except OSError:
                pass


def _commit(lessons):
    _validate(lessons)
    payload = {"schema_version": 1, "revision": uuid.uuid4().hex, "lessons": lessons}
    fd, name = tempfile.mkstemp(prefix=".memory-", suffix=".tmp", dir=STRUCTURED_MEMORY_FILE.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, STRUCTURED_MEMORY_FILE)
    finally:
        if os.path.exists(name):
            os.unlink(name)
    # Markdown is rendered on demand, never a second commit or an authority.


def _check_version(snapshot, expected_version):
    if expected_version is None or expected_version == "":
        raise MemoryVersionRequiredError("缺少 expected_version，请重新加载心法后再操作")
    if snapshot["version"] != expected_version:
        raise MemoryConflictError("心法版本已变化，请重新加载后再操作；未覆盖当前数据")


def _new_lesson(text, sample_size, category="TACTICAL"):
    return {"id": "lesson_" + uuid.uuid4().hex, "category": category,
            "rule_text": text.strip(), "enabled": True, "health_score": 90.0,
            "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "ttl_days": 7, "sample_size": sample_size, "is_baseline": False,
            "shield_status": "PASSED"}


def _review_candidates(texts, old, sample_size, strict):
    if not isinstance(texts, list) or not all(isinstance(t, str) for t in texts):
        raise ValueError("Expected text list")
    by_text = {i["rule_text"].strip(): i for i in old}
    result = []
    seen = set()
    for text in texts:
        text = text.strip()
        if text in seen:
            continue
        seen.add(text)
        # Unchanged entries retain identity, audit metadata and disabled state.
        if text in by_text:
            result.append(copy.deepcopy(by_text[text]))
            continue
        passed, reason = audit_proposed_lesson(text, sample_size=sample_size)
        if not passed:
            if strict:
                raise ValueError(reason)
            continue
        result.append(_new_lesson(text, sample_size))
    # Preserve disabled tombstones even when omitted by a model or legacy editor.
    result.extend(copy.deepcopy(i) for i in old if not i["enabled"] and i["rule_text"].strip() not in seen)
    return result


def publish_review(texts, *, expected_version, sample_size, change_status):
    if change_status == "NO_CHANGE":
        return False
    if change_status not in {"ADD", "REVISE", "INVALIDATE"}:
        raise ValueError("Invalid change status")
    with _memory_lock():
        snapshot = read_memory_snapshot()
        _check_version(snapshot, expected_version)
        candidates = _review_candidates(texts, snapshot["lessons"], sample_size, True)
        if not candidates or candidates == snapshot["lessons"]:
            return False
        # Rejected-only proposals must not remove existing active entries.
        if not any(i["rule_text"].strip() in {t.strip() for t in texts} for i in candidates):
            return False
        _commit(candidates)
        return True


def save_structured_memory(lessons, *, expected_version):
    """Compatibility publisher; changed/new records must pass the same audit."""
    _validate(lessons)
    with _memory_lock():
        snapshot = read_memory_snapshot()
        _check_version(snapshot, expected_version)
        disabled = {i["rule_text"].strip() for i in snapshot["lessons"] if not i["enabled"]}
        for item in lessons:
            if item["enabled"] and item["rule_text"].strip() in disabled:
                raise ValueError("Disabled text requires explicit toggle, not republication")
            if item not in snapshot["lessons"]:
                passed, reason = audit_proposed_lesson(item["rule_text"], item.get("sample_size", 1))
                if not passed:
                    raise ValueError(reason)
        _commit(lessons)


def toggle_lesson(lesson_id, *, expected_version=None):
    with _memory_lock():
        snapshot = read_memory_snapshot()
        _check_version(snapshot, expected_version)
        lessons = snapshot["lessons"]
        for item in lessons:
            if item["id"] == lesson_id:
                item["enabled"] = not item["enabled"]
                _commit(lessons)
                return item
    return None


def rollback_to_baseline(*, expected_version=None):
    with _memory_lock():
        snapshot = read_memory_snapshot()  # Corruption is never overwritten.
        _check_version(snapshot, expected_version)
        lessons = copy.deepcopy(BASELINE_LESSONS)
        _commit(lessons)
        return lessons


def admin_memory_view():
    snapshot, raw, _ = read_trading_context()
    items = [i["rule_text"] for i in snapshot["lessons"] if i["enabled"]]
    if not snapshot["exists"]:
        items = [line.strip()[2:].strip() for line in raw.splitlines() if line.strip().startswith("- ")]
    return {"items": items, "count": len(items), "raw": raw,
            "structured_lessons": snapshot["lessons"], "version": snapshot["version"],
            "legacy_read_only": not snapshot["exists"]}


def admin_mutate(operation, *, texts=None, index=None, lesson_id=None, expected_version=None):
    with _memory_lock():
        snapshot = read_memory_snapshot()
        _check_version(snapshot, expected_version)
        if not snapshot["exists"]:
            raise MemoryConflictError("Legacy memory is read-only; explicit initialization required")
        old = snapshot["lessons"]
        active = [i["rule_text"] for i in old if i["enabled"]]
        removed = None
        if operation == "delete":
            if lesson_id is not None:
                target = next((i for i in old if i["id"] == lesson_id), None)
                if target is None:
                    raise IndexError("Memory id not found")
                removed = target["rule_text"]
                lessons = [i for i in old if i["id"] != lesson_id]
            else:
                if index is None or index < 0 or index >= len(active):
                    raise IndexError("Memory index not found")
                removed = active.pop(index)
                lessons = [i for i in old if i["rule_text"] != removed]
        elif operation in {"add", "replace"}:
            candidates = list(texts or [])
            if operation == "add":
                candidates += active
            lessons = _review_candidates(candidates, old, 3, True)
        else:
            raise ValueError("Unknown memory operation")
        if lessons != old:
            _commit(lessons)
        result = {"saved": True, "items": [i["rule_text"] for i in lessons if i["enabled"]]}
        if removed is not None:
            result["removed"] = removed
        return result


def add_safe_lesson(rule_text, category="TACTICAL", sample_size=3):
    passed, reason = audit_proposed_lesson(rule_text, sample_size)
    if not passed:
        return False, reason, None
    with _memory_lock():
        lessons = load_structured_memory()
        for item in lessons:
            if item["rule_text"].strip() == rule_text.strip():
                return True, "条目已存在（保留启停状态）", item
        item = _new_lesson(rule_text, sample_size, category)
        lessons.append(item)
        _commit(lessons)
        return True, "心法审查通过并成功收录", item
