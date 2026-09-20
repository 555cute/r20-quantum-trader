"""策略快照的原子写与索引互斥原语。

结构优化阶段 2（B6 第二刀）。`_index_lock` 被 isolated() 作为可注入项提供，
故门面保留同名重导出即可（archive_current_policy 里的裸引用仍可被 patch）。
"""
from __future__ import annotations

import json
import os
import tempfile
import threading

# 模块级可选依赖：_index_lock 用 fcntl 做进程级文件锁，Windows/受限环境降级为
# 仅线程锁。原在 policy_snapshot.py 顶部（模块级 try/except，故外提时未被带到，
# 导致 _index_lock 报 NameError —— 属"常量/依赖搬家必须一并带走"的同一类问题）。
try:
    import fcntl
except ImportError:
    fcntl = None  # type: ignore
from contextlib import contextmanager
from pathlib import Path
from typing import Any

_lock_tls = threading.local()

_process_thread_lock = threading.RLock()


def _atomic_write_json(file_path: Path, data: Any) -> None:
    file_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", dir=file_path.parent, delete=False, encoding="utf-8") as tf:
        json.dump(data, tf, ensure_ascii=False, indent=2)
        # 第一百五十四刀：与其余原子写辅助统一（rename 前 fsync）
        tf.flush()
        os.fsync(tf.fileno())
        temp_name = tf.name
    os.replace(temp_name, file_path)


@contextmanager
def _index_lock(archive_dir: Path, shared: bool = False):
    """Reentrant thread and process file locking context for policy archive index operations."""
    with _process_thread_lock:
        if fcntl is None:
            yield
            return

        archive_dir.mkdir(parents=True, exist_ok=True)
        lock_file = archive_dir / ".index.lock"

        depth = getattr(_lock_tls, "depth", 0)
        if depth > 0:
            _lock_tls.depth = depth + 1
            try:
                yield
            finally:
                _lock_tls.depth -= 1
            return

        fd = os.open(str(lock_file), os.O_RDWR | os.O_CREAT, 0o600)
        flag = fcntl.LOCK_SH if shared else fcntl.LOCK_EX
        fcntl.flock(fd, flag)
        _lock_tls.depth = 1
        _lock_tls.fd = fd
        try:
            yield
        finally:
            _lock_tls.depth = 0
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass
            try:
                os.close(fd)
            except OSError:
                pass
            _lock_tls.fd = None
