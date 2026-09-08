"""Offline interprocess lock contention and release tests.

Uses real child processes. Does not import the FastAPI app, start the gateway,
or touch production data/credentials.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from r20_backend.file_lock import acquire, release

ROOT = Path(__file__).resolve().parents[1]

_HOLDER = """
import sys
from r20_backend.file_lock import acquire, release
path, ready = sys.argv[1], sys.argv[2]
shared = len(sys.argv) > 3 and sys.argv[3] == "shared"
handle = open(path, "a+", encoding="utf-8")
acquire(handle, blocking=False, shared=shared)
with open(ready, "w", encoding="utf-8") as fh:
    fh.write("1")
sys.stdin.read()
release(handle)
handle.close()
"""

_WAITER = """
import sys
from r20_backend.file_lock import acquire, release
path, done = sys.argv[1], sys.argv[2]
handle = open(path, "a+", encoding="utf-8")
acquire(handle, blocking=True)
with open(done, "w", encoding="utf-8") as fh:
    fh.write("1")
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


class FileLockProcessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.lock_path = Path(self.temp.name) / "resource.lock"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _hold(self, shared: bool = False) -> subprocess.Popen[str]:
        ready = Path(self.temp.name) / ("ready-shared" if shared else "ready")
        args = [sys.executable, "-c", _HOLDER, str(self.lock_path), str(ready)]
        if shared:
            args.append("shared")
        proc = subprocess.Popen(
            args,
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
            self.fail(f"lock holder did not start: {stderr}")
        return proc

    def test_nonblocking_contention_then_release_allows_acquire(self) -> None:
        holder = self._hold()
        try:
            with self.lock_path.open("a+", encoding="utf-8") as handle:
                with self.assertRaises(BlockingIOError):
                    acquire(handle, blocking=False)
            assert holder.stdin is not None
            holder.stdin.close()
            self.assertEqual(holder.wait(timeout=8), 0)
            with self.lock_path.open("a+", encoding="utf-8") as handle:
                acquire(handle, blocking=False)
                release(handle)
        finally:
            if holder.poll() is None:
                holder.kill()
                holder.wait(timeout=5)

    def test_blocking_acquire_waits_until_holder_releases(self) -> None:
        holder = self._hold()
        done = Path(self.temp.name) / "done"
        waiter = subprocess.Popen(
            [sys.executable, "-c", _WAITER, str(self.lock_path), str(done)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(ROOT),
            env=_child_env(),
            text=True,
        )
        for stream in (waiter.stdout, waiter.stderr):
            if stream is not None:
                self.addCleanup(stream.close)
        try:
            time.sleep(0.3)
            self.assertFalse(done.exists(), "blocking waiter acquired while holder still owns the lock")
            assert holder.stdin is not None
            holder.stdin.close()
            self.assertEqual(holder.wait(timeout=8), 0)
            _wait_path(done)
            self.assertEqual(waiter.wait(timeout=8), 0)
        finally:
            for proc in (holder, waiter):
                if proc.poll() is None:
                    proc.kill()
                    proc.wait(timeout=5)

    def test_shared_lock_contention_matches_platform_contract(self) -> None:
        holder = self._hold(shared=True)
        try:
            with self.lock_path.open("a+", encoding="utf-8") as handle:
                if os.name == "nt":
                    with self.assertRaises(BlockingIOError):
                        acquire(handle, blocking=False, shared=True)
                else:
                    acquire(handle, blocking=False, shared=True)
                    release(handle)
        finally:
            if holder.poll() is None:
                assert holder.stdin is not None
                holder.stdin.close()
                try:
                    holder.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    holder.kill()
                    holder.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()
