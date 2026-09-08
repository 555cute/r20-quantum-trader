"""Single-owner process supervisor for the R20 Gateway worker.

Tracks the Popen it spawned. Never infers identity from a PID file or /proc,
and never kills a process it did not create. An already-running worker is
detected by probing the same interprocess lock the worker holds.
"""
from __future__ import annotations

import subprocess
import sys
import threading
import time
from pathlib import Path

from r20_backend.file_lock import acquire, release

ROOT = Path(__file__).resolve().parents[1]
LOCK_FILE = ROOT / "data" / ".r20_gateway.lock"
PID_FILE = ROOT / "data" / "r20_gateway.pid"
LOG_FILE = ROOT / "logs" / "r20_gateway_supervisor.log"
_ENSURE_INTERVAL_SECONDS = 10
_STOP_WAIT_SECONDS = 8

_stop = threading.Event()
_state_lock = threading.Lock()
_thread: threading.Thread | None = None
_owned_process: subprocess.Popen[bytes] | None = None


def worker_lock_held() -> bool:
    """True when any process holds the gateway worker lock."""
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    with LOCK_FILE.open("a+", encoding="utf-8") as handle:
        try:
            acquire(handle, blocking=False)
        except BlockingIOError:
            return True
        try:
            release(handle)
        except OSError:
            pass
        return False


def current_pid() -> int:
    """PID of the live worker this supervisor spawned, else 0."""
    with _state_lock:
        process = _owned_process
        if process is None:
            return 0
        if process.poll() is not None:
            return 0
        return int(process.pid)


def _write_pid(pid: int) -> None:
    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(str(pid), encoding="utf-8")
    try:
        PID_FILE.chmod(0o600)
    except OSError:
        pass


def _clear_pid_file(pid: int) -> None:
    try:
        current = int(PID_FILE.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return
    if current != pid:
        return
    try:
        PID_FILE.unlink(missing_ok=True)
    except OSError:
        pass


def _reap_owned_unlocked() -> None:
    global _owned_process
    process = _owned_process
    if process is None:
        return
    if process.poll() is None:
        return
    try:
        process.wait(timeout=0.1)
    except subprocess.TimeoutExpired:
        pass
    except OSError:
        pass
    _clear_pid_file(int(process.pid))
    _owned_process = None


def _terminate_owned_unlocked(process: subprocess.Popen[bytes]) -> None:
    global _owned_process
    if process.poll() is None:
        try:
            process.terminate()
        except OSError:
            pass
        deadline = time.time() + _STOP_WAIT_SECONDS
        while process.poll() is None and time.time() < deadline:
            time.sleep(0.1)
        if process.poll() is None:
            try:
                process.kill()
            except OSError:
                pass
            try:
                process.wait(timeout=2)
            except (subprocess.TimeoutExpired, OSError):
                pass
    try:
        if process.poll() is not None:
            process.wait(timeout=0.1)
    except (subprocess.TimeoutExpired, OSError):
        pass
    _clear_pid_file(int(process.pid))
    if _owned_process is process:
        _owned_process = None


def _spawn_owned_unlocked() -> int:
    global _owned_process
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with LOG_FILE.open("a", encoding="utf-8") as log:
        process = subprocess.Popen(
            [sys.executable, "-m", "r20_gateway.worker"],
            cwd=ROOT,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
    _owned_process = process
    _write_pid(int(process.pid))
    return int(process.pid)


def ensure_worker() -> int:
    """Keep a single worker. Returns owned PID, or 0 if an external worker holds the lock."""
    with _state_lock:
        _reap_owned_unlocked()
        if _owned_process is not None:
            return int(_owned_process.pid)
        if _stop.is_set():
            return 0
        if worker_lock_held():
            return 0
        return _spawn_owned_unlocked()


def _run() -> None:
    while not _stop.is_set():
        ensure_worker()
        _stop.wait(_ENSURE_INTERVAL_SECONDS)


def start_supervisor() -> None:
    global _thread
    with _state_lock:
        if _thread is not None and _thread.is_alive():
            return
        _stop.clear()
        _thread = threading.Thread(target=_run, name="r20-gateway-supervisor", daemon=True)
        _thread.start()
    ensure_worker()


def stop_supervisor() -> None:
    global _thread
    _stop.set()
    with _state_lock:
        process = _owned_process
        if process is not None:
            _terminate_owned_unlocked(process)
    thread = _thread
    if thread is not None and thread.is_alive() and thread is not threading.current_thread():
        thread.join(timeout=2)
    with _state_lock:
        if _thread is not None and not _thread.is_alive():
            _thread = None
