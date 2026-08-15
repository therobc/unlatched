"""One collect at a time, across processes.

The desktop already refuses to start a second command while one is running, but
that guard is an in-memory field on the app - it cannot see a collect started by
a tray icon, a scheduled task, a second copy of the app, or somebody running the
engine from a terminal. Any of those can start a second engine against the same
database, which is the worst version of the write contention WAL and the busy
timeout only partly relieve.

AN OS FILE LOCK, NOT A PID FILE. A pid file has to answer "is that process still
alive", which is awkward on Windows and wrong whenever a pid is recycled. An
exclusive lock held on an open handle is released BY THE OPERATING SYSTEM when
the holder exits for any reason - crash, kill, power loss - so a stale lock
cannot outlive the run that took it.
"""
from __future__ import annotations

import os
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import IO, TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

LOCK_NAME = ".collect.lock"


class AlreadyRunningError(Exception):
    """Another process holds the collect lock."""


def _lock_path(home: str | os.PathLike[str] | None) -> Path:
    from . import paths
    return Path(paths.db_path(home)).parent / LOCK_NAME


def _take(handle: IO[str]) -> bool:
    """Try to take an exclusive, non-blocking lock. False if somebody has it.

    The two branches are the same lock with different spellings. `type: ignore`
    on the posix side because mypy resolves stdlib modules for the platform it
    is RUNNING on, and this is checked on Windows where fcntl has no stubs -
    the names are correct, they are simply unreachable to the checker here.
    """
    try:
        import msvcrt
    except ImportError:
        import fcntl
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)  # type: ignore[attr-defined]
        except OSError:
            return False
        return True
    try:
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        return False
    return True


@contextmanager
def collect_lock(home: str | os.PathLike[str] | None = None) -> Iterator[None]:
    """Hold the collect lock for this block, or raise AlreadyRunning.

    The pid is written into the file purely so a person looking at it can tell
    WHICH process is collecting. Nothing reads it back to make a decision - the
    lock itself is the decision, and a pid that is only ever displayed cannot
    be wrong in a way that matters.
    """
    path = _lock_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+")

    if not _take(handle):
        # Closed HERE, on its own, before raising. Closing in a finally that
        # also covers the failure path raised PermissionError on Windows when
        # the lock had not been taken, which turned "somebody else is
        # collecting" into an unrelated crash.
        _close_quietly(handle)
        msg = f"another collect is already running (lock: {path})"
        raise AlreadyRunningError(msg)

    # THE PID GOES IN A SIBLING FILE, NOT IN THE LOCKED ONE. The lock is held on
    # byte 0, and truncating a file whose locked byte is being removed fails on
    # Windows - the first version did exactly that and could not record anything.
    # Nothing reads this back to make a decision; it exists so a person looking
    # at a stuck profile can tell which process is collecting.
    with suppress(OSError):
        path.with_suffix(".pid").write_text(str(os.getpid()), encoding="utf-8")

    try:
        yield
    finally:
        # The OS drops the lock when the handle closes, so nothing is unlocked
        # by hand - which is what makes a crash, a kill or a power loss safe.
        # The file is left in place: deleting it races another process about to
        # open it, and an empty lock file costs nothing.
        _close_quietly(handle)


def _close_quietly(handle: IO[str]) -> None:
    with suppress(OSError):
        handle.close()
