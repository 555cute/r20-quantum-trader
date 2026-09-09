"""QQ singleton ownership with isolated paths and network-free child sessions."""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from r20_backend import qq_bind, qq_gateway_daemon
from r20_backend.file_lock import acquire, release

ROOT = Path(__file__).resolve().parents[1]
_REAL_POPEN = subprocess.Popen
_DAEMON_CHILD = """
import asyncio, sys
from pathlib import Path
from r20_backend import qq_gateway_daemon as daemon

daemon.DAEMON_LOCK_FILE = Path(sys.argv[1])
daemon.LOG_FILE = Path(sys.argv[2])
marker = Path(sys.argv[3])

def forbidden(*args, **kwargs):
    marker.write_text('network', encoding='utf-8')
    raise AssertionError('QQ network is forbidden in this fixture')

async def session():
    marker.write_text('entered', encoding='utf-8')
    if sys.argv[4] == 'hold':
        await asyncio.to_thread(sys.stdin.read)

daemon._get_credentials = forbidden
daemon._get_access_token = forbidden
daemon._get_ws_url = forbidden
daemon.websockets.connect = forbidden
daemon.urllib.request.urlopen = forbidden
daemon._run_session = session
daemon.main()
"""


class QqGatewayDaemonLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.lock_path = root / 'daemon.lock'
        self.log_path = root / 'daemon.log'
        patcher = patch.multiple(
            qq_bind, DAEMON_LOCK_FILE=self.lock_path,
            DAEMON_LOG_FILE=self.log_path, _OWNED_DAEMON=None,
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.started: list[subprocess.Popen[str]] = []

    @staticmethod
    def _finish(process: subprocess.Popen[str]) -> None:
        if process.stdin is not None and not process.stdin.closed:
            process.stdin.close()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        finally:
            for stream in (process.stdout, process.stderr):
                if stream is not None:
                    stream.close()

    def _spawn(self, script: str, *args: str) -> subprocess.Popen[str]:
        env = dict(os.environ, PYTHONUTF8='1', PYTHONIOENCODING='utf-8')
        env['PYTHONPATH'] = str(ROOT) + os.pathsep + env.get('PYTHONPATH', '')
        process = _REAL_POPEN(
            [sys.executable, '-c', script, *args], cwd=ROOT, env=env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding='utf-8',
        )
        self.addCleanup(self._finish, process)
        return process

    def _start_stand_in(self, *_args, **_kwargs) -> subprocess.Popen[str]:
        process = self._spawn('import sys; sys.stdin.read()')
        self.started.append(process)
        return process

    def _daemon(self, marker: Path, mode: str) -> subprocess.Popen[str]:
        return self._spawn(
            _DAEMON_CHILD, str(self.lock_path), str(self.log_path), str(marker), mode,
        )

    def _wait_entered(self, process: subprocess.Popen[str], marker: Path) -> None:
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            if marker.exists() and marker.read_text(encoding='utf-8') == 'entered':
                return
            if process.poll() is not None:
                self.fail(process.stderr.read() if process.stderr else 'daemon exited')
            time.sleep(0.02)
        self.fail('daemon did not enter its isolated session')

    def test_concurrent_ensure_starts_one_child(self) -> None:
        barrier = threading.Barrier(8)
        errors: list[BaseException] = []

        def ensure() -> None:
            try:
                barrier.wait(timeout=5)
                qq_bind.ensure_qq_gateway_daemon_running()
            except BaseException as exc:
                errors.append(exc)

        with patch.object(qq_bind.subprocess, 'Popen', side_effect=self._start_stand_in):
            threads = [threading.Thread(target=ensure) for _ in range(8)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=8)
                self.assertFalse(thread.is_alive())
            qq_bind.ensure_qq_gateway_daemon_running()

        self.assertEqual(errors, [])
        self.assertEqual(len(self.started), 1)
        self.assertIsNone(self.started[0].poll())

    def test_duplicate_cannot_enter_until_owner_exits(self) -> None:
        root = Path(self.temp.name)
        owner_marker = root / 'owner'
        owner = self._daemon(owner_marker, 'hold')
        self._wait_entered(owner, owner_marker)

        with patch.object(qq_bind.subprocess, 'Popen', side_effect=self._start_stand_in):
            qq_bind.ensure_qq_gateway_daemon_running()
        self.assertEqual(self.started, [])

        duplicate_marker = root / 'duplicate'
        duplicate = self._daemon(duplicate_marker, 'return')
        self.assertEqual(duplicate.wait(timeout=8), 0)
        self.assertFalse(duplicate_marker.exists())

        assert owner.stdin is not None
        owner.stdin.close()
        self.assertEqual(owner.wait(timeout=8), 0)
        successor_marker = root / 'successor'
        successor = self._daemon(successor_marker, 'return')
        self.assertEqual(successor.wait(timeout=8), 0)
        self.assertEqual(successor_marker.read_text(encoding='utf-8'), 'entered')

    def test_session_error_releases_ownership_before_process_exit(self) -> None:
        with patch.multiple(
            qq_gateway_daemon, DAEMON_LOCK_FILE=self.lock_path, LOG_FILE=self.log_path,
        ), patch.object(qq_gateway_daemon.signal, 'signal'), patch.object(
            qq_gateway_daemon, '_run_session', AsyncMock(side_effect=RuntimeError('fixture')),
        ), self.assertRaises(RuntimeError):
            qq_gateway_daemon.main()

        with self.lock_path.open('a+', encoding='utf-8') as handle:
            acquire(handle, blocking=False)
            release(handle)


if __name__ == '__main__':
    unittest.main()
