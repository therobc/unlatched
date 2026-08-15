"""One collect at a time, across processes.

The desktop's own guard is an in-memory field and cannot see a collect started
by a tray icon, a scheduled task, a second copy of the app, or a terminal. Any
of those could start a second engine against one database.
"""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap

import pytest

from unlatched import runlock


def test_the_lock_can_be_taken(home):
    with runlock.collect_lock(home):
        assert (home / runlock.LOCK_NAME).exists()


def test_it_is_released_when_the_block_ends(home):
    with runlock.collect_lock(home):
        pass
    with runlock.collect_lock(home):
        pass  # must not raise


def test_the_pid_is_recorded_for_a_person_reading_the_file(home):
    """In a SIBLING file: the lock is held on byte 0 of the lock file itself,
    and writing over that byte fails on Windows."""
    with runlock.collect_lock(home):
        pid_file = (home / runlock.LOCK_NAME).with_suffix(".pid")
        assert pid_file.read_text().strip() == str(os.getpid())


def test_a_second_holder_in_this_process_is_refused(home):
    """Same-process control. Cheap, and it proves the lock is exclusive."""
    with (
        runlock.collect_lock(home),
        pytest.raises(runlock.AlreadyRunningError),
        runlock.collect_lock(home),
    ):
        pass


def test_a_second_process_is_refused(home):
    """THE CONTROL THAT MATTERS. The whole point is cross-process.

    A same-process test could pass on a lock implemented with a module-level
    flag, which would protect nothing against the tray or a scheduled task.
    This holds the lock here and asks a real second interpreter to take it.
    """
    script = textwrap.dedent(f"""
        import sys
        sys.path.insert(0, {str(_app_dir())!r})
        from unlatched import runlock
        try:
            with runlock.collect_lock({str(home)!r}):
                print("TOOK")
        except runlock.AlreadyRunningError:
            print("REFUSED")
    """)
    with runlock.collect_lock(home):
        out = subprocess.run(  # noqa: S603 - argv is built here, not user input
            [sys.executable, "-c", script],
            capture_output=True, text=True, timeout=60, check=False)
    assert "REFUSED" in out.stdout, f"second process was not refused: {out.stdout}{out.stderr}"


def test_the_second_process_can_take_it_once_released(home):
    """NEGATIVE CONTROL for the test above.

    Without this, a lock that refused everybody always - a bug, not a feature -
    would pass the cross-process test and look correct.
    """
    script = textwrap.dedent(f"""
        import sys
        sys.path.insert(0, {str(_app_dir())!r})
        from unlatched import runlock
        try:
            with runlock.collect_lock({str(home)!r}):
                print("TOOK")
        except runlock.AlreadyRunningError:
            print("REFUSED")
    """)
    with runlock.collect_lock(home):
        pass
    out = subprocess.run(  # noqa: S603 - argv is built here, not user input
        [sys.executable, "-c", script],
        capture_output=True, text=True, timeout=60, check=False)
    assert "TOOK" in out.stdout, f"lock never released: {out.stdout}{out.stderr}"


def _app_dir() -> str:
    from pathlib import Path
    return str(Path(__file__).resolve().parent.parent)
