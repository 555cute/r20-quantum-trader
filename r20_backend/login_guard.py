"""按来源 IP 的登录限速，防止暴力破解与撞库洪泛。

与既有的「按账号 5 次失败锁 15 分钟」互补：账号锁只保护单个用户名，攻击者只要
轮换用户名（admin/root/operator…）或放慢速度即可绕过；本模块从 IP 维度设限。

两级阈值（均可用环境变量覆盖）：
- 总尝试数：窗口内超过 R20_LOGIN_IP_MAX_ATTEMPTS -> 判定为洪泛，封锁窗口长度；
- 失败次数：窗口内达到 R20_LOGIN_IP_MAX_FAILURES -> 判定为猜密，封锁更长时间。

设 R20_LOGIN_RATE_LIMIT=0 可整体关闭（本地压测/自动化测试用）。
"""
from __future__ import annotations

import os
import threading
import time
from collections import deque
from typing import Any

_WINDOW = int(os.getenv("R20_LOGIN_IP_WINDOW_SECONDS", "300"))          # 5 分钟滑动窗口
_MAX_ATTEMPTS = int(os.getenv("R20_LOGIN_IP_MAX_ATTEMPTS", "100"))      # 窗口内总尝试
_MAX_FAILURES = int(os.getenv("R20_LOGIN_IP_MAX_FAILURES", "15"))       # 窗口内失败次数
_BLOCK_SECONDS = int(os.getenv("R20_LOGIN_IP_BLOCK_SECONDS", "900"))    # 猜密封锁时长
_MAX_KEYS = int(os.getenv("R20_LOGIN_IP_MAX_KEYS", "10000"))            # 有界内存，防被撑爆


def _enabled() -> bool:
    return os.getenv("R20_LOGIN_RATE_LIMIT", "1").strip().lower() not in ("0", "false", "off", "no")


class _State:
    __slots__ = ("attempts", "failures", "blocked_until")

    def __init__(self) -> None:
        self.attempts: deque[float] = deque()
        self.failures: deque[float] = deque()
        self.blocked_until: float = 0.0


_lock = threading.Lock()
_states: dict[str, _State] = {}


def _prune(state: _State, now: float) -> None:
    floor = now - _WINDOW
    for bucket in (state.attempts, state.failures):
        while bucket and bucket[0] < floor:
            bucket.popleft()


def _reset_for_tests() -> None:
    with _lock:
        _states.clear()


def check(ip: str) -> tuple[bool, int]:
    """返回 (是否放行, 需等待秒数)。"""
    if not _enabled() or not ip or ip == "unknown":
        return True, 0
    now = time.time()
    with _lock:
        state = _states.get(ip)
        if not state:
            return True, 0
        _prune(state, now)
        remain = state.blocked_until - now
        if remain > 0:
            return False, int(remain) + 1
    return True, 0


def note_attempt(ip: str) -> None:
    """记录一次登录尝试（无论成败），用于识别撞库洪泛。"""
    if not _enabled() or not ip or ip == "unknown":
        return
    now = time.time()
    with _lock:
        if len(_states) > _MAX_KEYS and ip not in _states:
            # 内存保护：清掉最久未活动的一半键位
            for stale in sorted(_states, key=lambda k: _states[k].attempts[-1] if _states[k].attempts else 0)[: _MAX_KEYS // 2]:
                _states.pop(stale, None)
        state = _states.setdefault(ip, _State())
        _prune(state, now)
        state.attempts.append(now)
        if _MAX_ATTEMPTS > 0 and len(state.attempts) >= _MAX_ATTEMPTS:
            state.blocked_until = max(state.blocked_until, now + _WINDOW)


def note_failure(ip: str) -> None:
    """记录一次认证失败；达到阈值则按 IP 封锁更长时间。"""
    if not _enabled() or not ip or ip == "unknown":
        return
    now = time.time()
    with _lock:
        state = _states.setdefault(ip, _State())
        _prune(state, now)
        state.failures.append(now)
        if _MAX_FAILURES > 0 and len(state.failures) >= _MAX_FAILURES:
            state.blocked_until = max(state.blocked_until, now + _BLOCK_SECONDS)


def stats() -> dict[str, Any]:
    now = time.time()
    with _lock:
        blocked = [ip for ip, st in _states.items() if st.blocked_until > now]
        return {
            "enabled": _enabled(),
            "tracked_ips": len(_states),
            "blocked_ips": len(blocked),
            "window_seconds": _WINDOW,
            "max_attempts": _MAX_ATTEMPTS,
            "max_failures": _MAX_FAILURES,
            "block_seconds": _BLOCK_SECONDS,
        }
