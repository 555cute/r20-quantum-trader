"""行情取数的**失败计数 + 一次性告警**（只做可观测性，不改变任何取值行为）。

## 为什么需要它（真实事故，2026-09-15）

`scripts/brain/packages.py` 里 6 处取数各自包着**静默** `except Exception: pass`。
当抽取子模块时漏带模块级 `import json` / `import urllib.request`，`NameError` 被这些
`except` 全部吞掉：函数照常返回、现价恒为 0、`data_quality` 恒为 `invalid` ⇒ 主脑
P0 数据有效性拦截、**约 30 小时没有开新仓**，而整个过程**零日志零信号**。
（详见 `plan_local/` 台账 §136。）

本模块只做两件事：**计数**与**每种失败只吭一声**。

## 语义边界（务必保持）

- `note_failure()` **绝不抛异常**（内部整体 try/except）—— 它本身不能成为新的故障源；
- 不改变调用方的返回值/控制流：调用点仍是 `except ...: note_failure(...)`，
  **继续吞掉异常**（这是原有设计：单点取数失败不影响整包装配）；
- "一次性"= **每个 kind 每进程只打印一次**，后续只累加计数。
  主脑每 15 分钟起一个新进程 ⇒ 故障期间每轮至少一条日志，但不会刷屏；
- 计数用锁保护（同一进程多线程调用安全）；`stats()` 返回**纯副本**，调用方可安全序列化。
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from collections import deque
from typing import Any, Deque, Dict, Optional

_LOCK = threading.Lock()
_FAILURES: Dict[str, int] = {}
_LOGGED: set[str] = set()
_LAST_ERROR: Dict[str, str] = {}

#: 快照格式版本（跨进程消费：worker 写、后端 /metrics 读）。加字段就升版本，
#: 老消费者看到不认识的版本应当**当作不可用**，而不是猜字段。
SCHEMA_VERSION = 1
#: 每个 kind 保留的最近耗时样本数（够算 p50/p95，且内存有界——这是每 15 分钟
#: 一个新进程的短命脚本，绝不能攒成无界列表）。
_MAX_SAMPLES = 64

_CALLS: Dict[str, int] = {}
_FAILED_CALLS: Dict[str, int] = {}
_TOTAL_SECONDS: Dict[str, float] = {}
_MAX_SECONDS: Dict[str, float] = {}
_SAMPLES: Dict[str, Deque[float]] = {}
_LAST_OK_MS: Dict[str, int] = {}
_LAST_CALL_MS: Dict[str, int] = {}

__all__ = [
    "SCHEMA_VERSION",
    "note_failure",
    "note_call",
    "stats",
    "call_stats",
    "snapshot",
    "write_snapshot",
    "load_snapshot",
    "reset",
    "failure_count",
]


def note_failure(kind: str, exc: Optional[BaseException] = None) -> None:
    """记一次取数失败：累加计数；该 kind **首次**失败时打印一条告警。

    绝不抛异常：本函数自身任何意外都被吞掉（它不能变成新的故障源）。
    """
    # ① 计数：**独立兜底且最先执行** —— 计数是事故取证的关键，
    #    绝不允许因为"格式化异常信息失败"连计数一起丢掉（门里的 `__str__` 会抛
    #    的异常用例逼出了这一点：原实现把格式化放在计数之前，detail 一炸就整条丢失）。
    try:
        with _LOCK:
            _FAILURES[kind] = _FAILURES.get(kind, 0) + 1
            count = _FAILURES[kind]
            first = kind not in _LOGGED
            if first:
                _LOGGED.add(kind)
    except Exception:
        return
    # ② 详情与一次性告警：同样各自兜底，失败只影响"记录得详不详细"。
    try:
        detail = f"{type(exc).__name__}: {exc}" if exc is not None else "未知错误"
        with _LOCK:
            _LAST_ERROR[kind] = detail
        if first:
            print(f"[行情取数] ⚠️ {kind} 取数失败（第 {count} 次，后续只累加不再重复打印）: {detail}",
                  flush=True)
    except Exception:
        pass


def failure_count(kind: str) -> int:
    """某个 kind 的累计失败次数（0 表示没失败过）。"""
    with _LOCK:
        return _FAILURES.get(kind, 0)


def stats() -> Dict[str, Any]:
    """失败统计快照（纯副本，可安全 JSON 化）：

    ``{"total": 3, "by_kind": {"okx_ticker": 2, ...}, "last_error": {...}}``
    """
    with _LOCK:
        by_kind = dict(_FAILURES)
        return {
            "total": sum(by_kind.values()),
            "by_kind": by_kind,
            "last_error": dict(_LAST_ERROR),
        }


def reset() -> None:
    """清空计数与"已打印"标记（供测试与进程内复用）。"""
    with _LOCK:
        _FAILURES.clear()
        _LOGGED.clear()
        _LAST_ERROR.clear()
        _CALLS.clear()
        _FAILED_CALLS.clear()
        _TOTAL_SECONDS.clear()
        _MAX_SECONDS.clear()
        _SAMPLES.clear()
        _LAST_OK_MS.clear()
        _LAST_CALL_MS.clear()


def note_call(kind: str, seconds: float, ok: bool = True) -> None:
    """记一次取数调用的**耗时与成败**（只做可观测性，绝不改变取值行为）。

    与 `note_failure` 的分工：`note_failure` 回答"失败了没有、失败几次"，
    `note_call` 回答"**每次调用**花多久、成功率多少、最近一次成功是什么时候"。
    后者是这次要补的洞 —— 第 137 刀的事故里失败计数其实存在，但**没人把它接出去**，
    而且"延时在爬"这种前兆连计数都没有。

    语义边界（与 `note_failure` 完全一致）：
    - **绝不抛异常**：内部整体兜底，它不能成为新的故障源；
    - `seconds` 非有限数/负数一律按 0 计（不臆造耗时，也不让 NaN 污染百分位）；
    - 样本只保留最近 `_MAX_SAMPLES` 条（有界内存）；同 kind 多线程调用安全。
    """
    try:
        try:
            dt = float(seconds)
        except (TypeError, ValueError):
            dt = 0.0
        if dt != dt or dt in (float("inf"), float("-inf")) or dt < 0:   # NaN/Inf/负数
            dt = 0.0
        now_ms = int(time.time() * 1000)
        with _LOCK:
            _CALLS[kind] = _CALLS.get(kind, 0) + 1
            _TOTAL_SECONDS[kind] = _TOTAL_SECONDS.get(kind, 0.0) + dt
            if dt > _MAX_SECONDS.get(kind, 0.0):
                _MAX_SECONDS[kind] = dt
            samples = _SAMPLES.get(kind)
            if samples is None:
                samples = _SAMPLES[kind] = deque(maxlen=_MAX_SAMPLES)
            samples.append(dt)
            _LAST_CALL_MS[kind] = now_ms
            if ok:
                _LAST_OK_MS[kind] = now_ms
            else:
                _FAILED_CALLS[kind] = _FAILED_CALLS.get(kind, 0) + 1
    except Exception:
        return


def _percentile(sorted_values: list, ratio: float) -> Optional[float]:
    """最近秩百分位（样本少时也稳定；空样本返回 None，不返回 0 冒充）。"""
    if not sorted_values:
        return None
    index = max(0, min(len(sorted_values) - 1, int(round(ratio * (len(sorted_values) - 1)))))
    return sorted_values[index]


def call_stats() -> Dict[str, Any]:
    """按 kind 的调用/耗时统计快照（纯副本，可安全 JSON 化）。

    ``{"calls": {...}, "failed_calls": {...}, "latency": {kind: {...}},
       "last_success_ms": {...}, "last_call_ms": {...}}``
    """
    with _LOCK:
        calls = dict(_CALLS)
        failed = dict(_FAILED_CALLS)
        total = dict(_TOTAL_SECONDS)
        maximum = dict(_MAX_SECONDS)
        last_ok = dict(_LAST_OK_MS)
        last_call = dict(_LAST_CALL_MS)
        samples = {k: sorted(v) for k, v in _SAMPLES.items()}
    latency: Dict[str, Any] = {}
    for kind, values in samples.items():
        count = calls.get(kind, len(values))
        latency[kind] = {
            "count": len(values),
            "avg_ms": round(1000.0 * total.get(kind, 0.0) / count, 3) if count else None,
            "max_ms": round(1000.0 * maximum.get(kind, 0.0), 3),
            "p50_ms": (lambda p: None if p is None else round(1000.0 * p, 3))(_percentile(values, 0.50)),
            "p95_ms": (lambda p: None if p is None else round(1000.0 * p, 3))(_percentile(values, 0.95)),
        }
    return {"calls": calls, "failed_calls": failed, "latency": latency,
            "last_success_ms": last_ok, "last_call_ms": last_call}


def snapshot() -> Dict[str, Any]:
    """完整可观测性快照（**跨进程契约**：worker 写文件、后端读文件）。

    刻意与 `stats()` 分开：`stats()` 是既有的**失败计数**契约（有门禁钉住其精确形状），
    本函数是"失败 + 成功率 + 延时百分位 + 最近成功时刻"的合并视图，并带 schema 版本。
    """
    payload = {
        "schema_version": SCHEMA_VERSION,
        "written_at_ms": int(time.time() * 1000),
        "failures": stats(),
    }
    payload.update(call_stats())
    return payload


def write_snapshot(path: str, payload: Optional[Dict[str, Any]] = None) -> bool:
    """原子写快照到 `path`（tmp + `os.replace`）。**绝不抛异常**，失败返回 False。

    为什么要落文件而不是留在内存：取数发生在 **worker 进程**（每 15 分钟 respawn），
    而 `/metrics` 由 **后端进程** 提供 —— 进程内计数器永远看不到对方，
    这与既有的 `data/venue_health.json` 是同一套跨进程手法。
    """
    try:
        data = payload if payload is not None else snapshot()
        target = os.fspath(path)
        parent = os.path.dirname(target)
        if parent:
            os.makedirs(parent, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=parent or ".", prefix=".mdh-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False, indent=2)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, target)
        except Exception:
            try:
                os.unlink(tmp)
            except Exception:
                pass
            raise
        return True
    except Exception:
        return False


def load_snapshot(path: str) -> Dict[str, Any]:
    """读快照；缺失/损坏/**版本不认识**一律返回 `{}`（调用方据此标 source_ok=0）。

    返回 `{}` 而不是抛异常或返回半份数据：宁可显式"没有数据"，
    也不要用未知 schema 的字段拼出一个看着正常的指标。
    """
    try:
        with open(os.fspath(path), "r", encoding="utf-8") as fh:
            payload = json.load(fh)
    except Exception:
        return {}
    if not isinstance(payload, dict):
        return {}
    if payload.get("schema_version") != SCHEMA_VERSION:
        return {}
    return payload
