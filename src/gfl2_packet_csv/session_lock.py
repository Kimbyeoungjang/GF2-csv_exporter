"""Process-lifetime advisory locks in the existing per-user IPC directory.

Never unlink reusable lock files: another process could otherwise lock a
separate file under the same name. The OS releases the lock on process exit.
"""
from __future__ import annotations

import errno
import os
from pathlib import Path

from .ipc import ipc_root


class SessionBusy(RuntimeError):
    pass


class SessionLock:
    def __init__(self, path: Path, *, create: bool = True):
        self.path = Path(path)
        self.create = create
        self._file = None

    def __enter__(self):
        if self._file is not None:
            raise RuntimeError('lock already acquired')
        fd = os.open(self.path, os.O_RDWR | (os.O_CREAT if self.create else 0), 0o600)
        fp = os.fdopen(fd, 'r+b', buffering=0)
        try:
            if os.name == 'nt':
                import msvcrt
                # Windows permits locking a byte beyond EOF. No write needed.
                msvcrt.locking(fp.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fp.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BaseException as exc:
            fp.close()
            if isinstance(exc, OSError) and exc.errno in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                raise SessionBusy('session already active') from exc
            raise
        self._file = fp
        return self

    def __exit__(self, *args):
        fp, self._file = self._file, None
        if fp is not None:
            # Closing the OS handle releases the lock, including on crash.
            fp.close()


def gui_lock() -> SessionLock:
    return SessionLock(ipc_root() / 'gui.lock')


def capture_lock() -> SessionLock:
    return SessionLock(ipc_root() / 'capture.lock')


def owner_alive(path: Path | None) -> bool:
    """A unique GUI lease is alive only while another process holds its lock.

    No lease is supplied by older/manual helper invocations. A supplied but
    missing or unreadable lease fails closed; never signal the game in that case.
    """
    if path is None:
        return True
    try:
        with SessionLock(path, create=False):
            return False
    except SessionBusy:
        return True
    except OSError:
        return False
