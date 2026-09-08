"""Process-wide candle memoization and inflight coalescing.

Closed bars are immutable. Only the forming bar can move, so a short TTL
relative to the interval reuses the last successful venue payload without
changing indicator windows or fabricating rows. Empty fetches are not stored.
Gateway jobs are separate processes; this cache only collapses duplicate
calls inside one process (trader internals, chart polls, indicator+OHLC).
"""
from __future__ import annotations

import threading
import time
from typing import Any, Callable

_lock = threading.Lock()
_store: dict[tuple[str, str, str], tuple[float, int, list[list[Any]]]] = {}
_inflight: dict[tuple[str, str, str], threading.Event] = {}

_BAR_TTL = {
    "1m": 5.0,
    "3m": 6.0,
    "5m": 8.0,
    "15m": 10.0,
    "30m": 12.0,
    "1H": 15.0,
    "1h": 15.0,
    "2H": 18.0,
    "2h": 18.0,
    "4H": 20.0,
    "4h": 20.0,
    "1D": 30.0,
    "1d": 30.0,
}


def ttl_for(bar: str) -> float:
    return _BAR_TTL.get(str(bar), 10.0)


def clear() -> None:
    with _lock:
        _store.clear()
        _inflight.clear()


def get_or_fetch(
    venue: str,
    inst_id: str,
    bar: str,
    limit: int,
    fetch: Callable[[int], Any],
    *,
    min_pull: int = 80,
    max_pull: int = 1500,
) -> list[list[Any]]:
    want = max(1, int(limit))
    pull = min(max(want, int(min_pull)), int(max_pull))
    key = (str(venue), str(inst_id), str(bar))

    now = time.monotonic()
    with _lock:
        hit = _store.get(key)
        if hit and (now - hit[0]) < ttl_for(bar) and hit[1] >= want:
            return [list(row) for row in hit[2][:want]]
        waiter = _inflight.get(key)
        owner = waiter is None
        if owner:
            waiter = threading.Event()
            _inflight[key] = waiter
    if not owner:
        waiter.wait(timeout=20)
        with _lock:
            hit = _store.get(key)
        if hit and hit[1] >= want:
            return [list(row) for row in hit[2][:want]]
        rows = fetch(pull) or []
        return [list(row) for row in list(rows)[:want]]
    try:
        rows = list(fetch(pull) or [])
        if not rows:
            return []
        copied = [list(row) for row in rows]
        with _lock:
            _store[key] = (time.monotonic(), pull, copied)
        return [list(row) for row in copied[:want]]
    finally:
        with _lock:
            _inflight.pop(key, None)
        waiter.set()
