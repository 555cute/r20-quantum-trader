"""Run discovery with fail-closed network and real-resource tripwires.

Usage: python3 -m tests.offline_suite
The same guard can be installed by a sitecustomize harness before standard
``python3 -m unittest discover -s tests -t .``. This does not alter tests/__init__.
This is a Python audit guard, not an OS sandbox for arbitrary native children.
"""
import hashlib
import os
from pathlib import Path
import socket
import sys
import unittest


class OfflineGuard:
    def __init__(self, root=None):
        self.root = Path(root or Path(__file__).resolve().parents[1]).resolve()
        self.roots = {self.root}
        # A linked worktree must also protect the original workspace resources.
        git_file = self.root / '.git'
        if git_file.is_file():
            git_dir = Path(git_file.read_text().strip().removeprefix('gitdir: '))
            self.roots.add(git_dir.resolve().parents[2])
        self.attempts = []
        self.writes = []
        self.children = []
        self.before = self.fingerprint()

    def fingerprint(self):
        """Metadata fingerprint: never read .env, key, ciphertext or config bytes.

        Nanosecond ctime catches write-and-restore; ignore atime (read-only access
        is allowed). The write audit additionally detects attempted mutations.
        """
        result = {}
        for root in self.roots:
            paths = {root / '.env', root / 'data/.r20_secret_key',
                     root / 'data/r20_secrets.enc'}
            # Active services in the original workspace may update their caches
            # concurrently. Monitor all JSON configuration in THIS worktree;
            # original-workspace credentials are monitored without reading them.
            if root == self.root:
                paths.update((root / 'data').rglob('*.json'))
            for path in paths:
                if path.name.endswith('.lock') or path.is_dir():
                    continue
                stat = path.stat() if path.exists() else None
                result[str(path)] = ((stat.st_dev, stat.st_ino, stat.st_size,
                                      stat.st_mtime_ns, stat.st_ctime_ns, stat.st_mode)
                                     if stat else None)
        return result

    def protected(self, value, dir_fd=None):
        if not isinstance(value, (str, bytes, os.PathLike)):
            return False
        path = Path(os.fsdecode(value))
        if not path.is_absolute() and dir_fd is not None and dir_fd != -1:
            path = Path(os.readlink(f'/proc/self/fd/{dir_fd}')) / path
        path = path.resolve()
        return not path.name.endswith('.lock') and any(
            path == root / '.env' or path.is_relative_to(root / 'data')
            for root in self.roots)

    def audit(self, event, args):
        if event in ('socket.connect', 'socket.getaddrinfo', 'socket.sendto'):
            self.attempts.append(event)
            raise RuntimeError('Offline suite blocked network: ' + event)
        if event == 'subprocess.Popen':
            command = args[1]
            tokens = list(command) if isinstance(command, (list, tuple)) else [str(command)]
            executable = Path(str(tokens[0])).name
            # Record executable/subcommand only: never log command bodies or env.
            label = executable + (' ' + str(tokens[1]) if len(tokens) > 1 else '')
            self.children.append(label)
            # Only reviewed local tools and the pure risk-constant import probe.
            # An arbitrary Python/shell child must not bypass the socket guard.
            allowed = (executable == 'uname' and tokens[1:] == ['-p']) or (
                executable == 'grep' and tokens[1:] == [
                    '-rn', 'gemini-3.8-flash-high', '--include=*.py',
                    '--include=*.ts', '--include=*.vue', 'r20_backend',
                    'scripts', 'frontend/src', 'dashboard']) or (
                executable in ('python3', 'python', Path(sys.executable).name)
                and len(tokens) == 3 and tokens[1] == '-c'
                and hashlib.sha256(tokens[2].encode()).hexdigest() ==
                'ae478ea673237b4bb4e386e0df78313d11ee02594816f1d3d2bf1e2213789b37')
            if not allowed:
                self.attempts.append('external child process: ' + executable)
                raise RuntimeError('Offline suite blocked external child process')
        if event == 'os.system':
            self.attempts.append('shell command')
            raise RuntimeError('Offline suite blocked shell command')
        targets = []
        if event == 'open' and isinstance(args[2], int) and args[2] & (
                os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC):
            targets = [(args[0], None)]
        elif event in ('os.remove', 'os.rmdir'):
            targets = [(args[0], args[1])]
        elif event == 'os.rename':
            targets = [(args[0], args[2]), (args[1], args[3])]
        for target, dir_fd in targets:
            if self.protected(target, dir_fd):
                self.writes.append(str(target))
                raise RuntimeError('Offline suite blocked real resource mutation')

    def install(self):
        sys.addaudithook(self.audit)
        return self

    def prove_connection_guard(self):
        # Numeric address bypasses DNS: demonstrate TCP egress denial on BOTH
        # HTTP and HTTPS ports, before an OS connect can happen (TEST-NET-1).
        start = len(self.attempts)
        for port in (80, 443):
            with socket.socket() as sock:
                try:
                    sock.connect(('192.0.2.1', port))
                except RuntimeError as exc:
                    if 'Offline suite blocked network' not in str(exc):
                        raise
                else:
                    raise AssertionError('Connection guard did not fire')
        assert self.attempts[start:] == ['socket.connect', 'socket.connect']
        del self.attempts[start:]
        print('EGRESS_GUARD_SELF_TEST: HTTP/80 + HTTPS/443 blocked')

    def report(self):
        after = self.fingerprint()
        changed = sorted(k for k in self.before.keys() | after.keys()
                         if self.before.get(k) != after.get(k))
        print('NETWORK_ATTEMPTS:', self.attempts)
        print('CONFIG_FINGERPRINT_CHANGES:', changed)
        print('CONFIG_WRITE_ATTEMPTS:', sorted(set(self.writes)))
        print('LOCAL_SUBPROCESSES:', sorted(set(self.children)))
        return not (self.attempts or changed or self.writes)


def main():
    guard = OfflineGuard().install()
    os.chdir(guard.root)
    guard.prove_connection_guard()
    suite = unittest.defaultTestLoader.discover('tests', top_level_dir='.')
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    clean = guard.report()
    return 0 if result.wasSuccessful() and clean else 1


if __name__ == '__main__':
    raise SystemExit(main())
