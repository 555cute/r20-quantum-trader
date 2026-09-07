"""Cross-platform interprocess file locks.

Public API:
    acquire(handle, blocking=True, shared=False) -> None
    release(handle) -> None

``handle`` is an open file object or an integer descriptor. The caller owns
its lifetime and must keep it open for the duration of the lock.

POSIX uses ``fcntl.flock``. Windows uses ``msvcrt.locking``. Contention
raises ``BlockingIOError``. Windows shared requests are exclusive.
This module never installs a fake ``fcntl``.
"""
from __future__ import annotations

import errno
import os
from typing import Any

__all__ = ["acquire", "release"]

_WIN_LOCK_BUSY = {13, 33, 36}


def _fileno(handle: Any) -> int:
    if isinstance(handle, int):
        if handle < 0:
            raise ValueError("handle descriptor must be non-negative")
        return handle
    fileno = getattr(handle, "fileno", None)
    if fileno is None:
        raise TypeError("handle must be a file object or integer descriptor")
    fd = fileno()
    if not isinstance(fd, int) or fd < 0:
        raise TypeError("handle.fileno() must return a non-negative int")
    return fd


def _resync_position(handle: Any, fd: int, position: int) -> None:
    seek = getattr(handle, "seek", None)
    if seek is not None and not isinstance(handle, int):
        try:
            seek(position)
            return
        except OSError:
            pass
    os.lseek(fd, position, os.SEEK_SET)


def _position(handle: Any, fd: int) -> int:
    tell = getattr(handle, "tell", None)
    if tell is not None and not isinstance(handle, int):
        try:
            return int(tell())
        except OSError:
            pass
    return os.lseek(fd, 0, os.SEEK_CUR)


def _lock_busy(exc: OSError) -> bool:
    winerror = getattr(exc, "winerror", None)
    if winerror in _WIN_LOCK_BUSY:
        return True
    return exc.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLOCK, errno.EWOULDBLOCK, 13, 36}


def _acquire_windows(handle: Any, fd: int, blocking: bool) -> None:
    import msvcrt
    import time

    position = _position(handle, fd)
    try:
        size = os.lseek(fd, 0, os.SEEK_END)
        if size < 1:
            os.write(fd, b"\0")
        os.lseek(fd, 0, os.SEEK_SET)
        while True:
            try:
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                return
            except OSError as exc:
                if not _lock_busy(exc):
                    raise
                if not blocking:
                    raise BlockingIOError(errno.EWOULDBLOCK, "Resource temporarily unavailable") from exc
                time.sleep(0.05)
                os.lseek(fd, 0, os.SEEK_SET)
    finally:
        _resync_position(handle, fd, position)


def _release_windows(handle: Any, fd: int) -> None:
    import msvcrt

    position = _position(handle, fd)
    try:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    finally:
        _resync_position(handle, fd, position)


def acquire(handle: Any, blocking: bool = True, shared: bool = False) -> None:
    """Take an interprocess lock on ``handle``.

    ``shared=True`` is POSIX ``LOCK_SH``. On Windows it is exclusive.
    Already-contended non-blocking (and Windows exclusive-shared) requests
    raise ``BlockingIOError``.
    """
    fd = _fileno(handle)
    if os.name == "nt":
        _acquire_windows(handle, fd, blocking=blocking)
        return
    import fcntl

    flags = fcntl.LOCK_SH if shared else fcntl.LOCK_EX
    if not blocking:
        flags |= fcntl.LOCK_NB
    fcntl.flock(fd, flags)


def release(handle: Any) -> None:
    """Release a lock previously taken with :func:`acquire`."""
    fd = _fileno(handle)
    if os.name == "nt":
        _release_windows(handle, fd)
        return
    import fcntl

    fcntl.flock(fd, fcntl.LOCK_UN)
