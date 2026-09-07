"""Supervisor process-ownership tests without starting the gateway worker."""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import r20_gateway.supervisor as supervisor

ROOT = Path(__file__).resolve().parents[1]
_REAL_POPEN = subprocess.Popen

_HOLDER = """
import sys
from r20_backend.file_lock import acquire, release
path, ready = sys.argv[1], sys.argv[2]
handle = open(path, "a+", encoding="utf-8")
acquire(handle, blocking=False)
with open(ready, "w", encoding="utf-8") as fh:
    fh.write("1")
sys.stdin.read()
release(handle)
handle.close()
"""


def _child_env() -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    return env


def _wait_path(path: Path, timeout: float = 8.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists() and path.stat().st_size > 0:
            return
        time.sleep(0.02)
    raise AssertionError(f"timed out waiting for {path}")


def _live_proc(pid: int) -> Mock:
    proc = Mock()
    proc.pid = pid
    proc.poll.return_value = None

    def terminate() -> None:
        proc.poll.return_value = 0

    proc.terminate.side_effect = terminate
    proc.kill.side_effect = terminate
    proc.wait.return_value = 0
    return proc


class GatewaySupervisorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.lock_path = root / ".r20_gateway.lock"
        self.pid_path = root / "r20_gateway.pid"
        self.log_path = root / "supervisor.log"
        self.spawned: list[Mock] = []
        self.patches = [
            patch.object(supervisor, "LOCK_FILE", self.lock_path),
            patch.object(supervisor, "PID_FILE", self.pid_path),
            patch.object(supervisor, "LOG_FILE", self.log_path),
            patch.object(supervisor, "ROOT", root),
            patch.object(supervisor.subprocess, "Popen", side_effect=self._popen),
        ]
        for patcher in self.patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        self._reset_supervisor()
        self.addCleanup(self._reset_supervisor)
        self.addCleanup(self.temp.cleanup)

    def _reset_supervisor(self) -> None:
        supervisor._stop.set()
        thread = supervisor._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=2)
        with supervisor._state_lock:
            process = supervisor._owned_process
            if process is not None:
                try:
                    process.terminate()
                except Exception:
                    pass
            supervisor._owned_process = None
            supervisor._thread = None
        supervisor._stop.clear()

    def _popen(self, *args: object, **kwargs: object) -> Mock:
        proc = _live_proc(7100 + len(self.spawned))
        self.spawned.append(proc)
        return proc

    def _hold_lock(self) -> subprocess.Popen[str]:
        ready = Path(self.temp.name) / "ready"
        proc = _REAL_POPEN(
            [sys.executable, "-c", _HOLDER, str(self.lock_path), str(ready)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(ROOT),
            env=_child_env(),
            text=True,
        )
        for stream in (proc.stdin, proc.stdout, proc.stderr):
            if stream is not None:
                self.addCleanup(stream.close)
        try:
            _wait_path(ready)
        except AssertionError:
            proc.kill()
            _, stderr = proc.communicate(timeout=5)
            self.fail(f"external worker lock holder did not start: {stderr}")
        return proc

    def test_external_lock_does_not_spawn_worker(self) -> None:
        holder = self._hold_lock()
        try:
            self.assertTrue(supervisor.worker_lock_held())
            self.assertEqual(supervisor.ensure_worker(), 0)
            self.assertEqual(supervisor.ensure_worker(), 0)
            self.assertEqual(self.spawned, [])
            self.assertEqual(supervisor.current_pid(), 0)
        finally:
            assert holder.stdin is not None
            holder.stdin.close()
            holder.wait(timeout=8)

    def test_owned_process_is_not_respawned_while_alive(self) -> None:
        pid = supervisor.ensure_worker()
        self.assertEqual(len(self.spawned), 1)
        self.assertEqual(supervisor.ensure_worker(), pid)
        self.assertEqual(len(self.spawned), 1)
        self.assertEqual(supervisor.current_pid(), pid)
        self.assertTrue(self.pid_path.exists())
        self.assertEqual(self.pid_path.read_text(encoding="utf-8").strip(), str(pid))

    def test_stop_terminates_only_owned_popen_not_pid_file_victim(self) -> None:
        victim = _REAL_POPEN(
            [sys.executable, "-c", "import sys,time; sys.stdout.write('r'); sys.stdout.flush(); time.sleep(60)"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        if victim.stdout is not None:
            self.addCleanup(victim.stdout.close)
        try:
            assert victim.stdout is not None
            self.assertEqual(victim.stdout.read(1), "r")
            owned_pid = supervisor.ensure_worker()
            owned = self.spawned[0]
            self.pid_path.write_text(str(victim.pid), encoding="utf-8")
            supervisor.stop_supervisor()
            owned.terminate.assert_called()
            self.assertIsNone(victim.poll())
            self.assertNotEqual(owned_pid, victim.pid)
            self.assertEqual(supervisor.current_pid(), 0)
        finally:
            if victim.poll() is None:
                victim.terminate()
                victim.wait(timeout=5)

    def test_stop_prevents_new_spawn(self) -> None:
        supervisor._stop.set()
        self.assertEqual(supervisor.ensure_worker(), 0)
        self.assertEqual(self.spawned, [])

    def test_dead_owned_process_is_reaped_before_respawn(self) -> None:
        first = supervisor.ensure_worker()
        self.spawned[0].poll.return_value = 1
        second = supervisor.ensure_worker()
        self.assertEqual(len(self.spawned), 2)
        self.assertNotEqual(first, second)
        self.assertEqual(supervisor.current_pid(), second)


if __name__ == "__main__":
    unittest.main()
