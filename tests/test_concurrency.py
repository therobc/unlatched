"""Two connections, one database file.

The desktop writes job_status directly through rusqlite while the engine writes
jobs during a collect. They are separate processes on one file, and a status
change made during a collect could fail outright with "database is locked" while
nothing on screen said a refresh was even running.

WHICH SETTING FIXES WHICH HALF - established by a negative control that refused
to fail, not by reasoning:

  * BUSY_TIMEOUT fixes WRITER vs WRITER, which is the reported failure. SQLite
    allows one writer at a time in BOTH journal modes, WAL included. The default
    timeout is 0, so the second writer does not queue - it fails at once. A
    non-zero timeout makes it wait for the first to finish.

  * WAL fixes READER vs WRITER. Under the rollback journal a committing writer
    takes an EXCLUSIVE lock and readers are locked out for the duration; under
    WAL a reader is never blocked by a writer.

An earlier version of this file asserted that a reader is blocked while a writer
holds an open IMMEDIATE transaction. IT IS NOT: BEGIN IMMEDIATE takes RESERVED,
and readers are permitted under RESERVED. That test passed under both journal
modes and therefore measured nothing. Timing the wait is what actually separates
them.

Two connections rather than two processes is the same locking boundary - SQLite
locks per connection, not per process.
"""
from __future__ import annotations

import sqlite3
import time

import pytest

from unlatched import db

# Long enough to measure without being flaky on a shared machine, short enough
# that a test which blocks for the whole thing still finishes quickly.
WAIT_MS = 400


def test_the_database_is_in_wal_mode(home):
    con = db.connect(home)
    mode = con.execute("PRAGMA journal_mode").fetchone()[0]
    con.close()
    assert str(mode).lower() == "wal"


def test_a_busy_timeout_is_set(home):
    """Zero is SQLite's default and it means "fail immediately, never wait".

    NON-ZERO IS ASSERTED SEPARATELY from the value. Comparing only against
    db.BUSY_TIMEOUT_MS mirrors the implementation: set that constant to 0 by
    accident and the test still passes while the property it protects is gone.
    """
    con = db.connect(home)
    timeout = int(con.execute("PRAGMA busy_timeout").fetchone()[0])
    con.close()
    assert timeout > 0, "a zero timeout is the failing behaviour, not a setting"
    assert timeout == db.BUSY_TIMEOUT_MS


def _blocked_write(path, timeout_ms: int) -> tuple[float, Exception | None]:
    """Time a write that collides with a held write lock. Returns (seconds, error).

    The blocking connection keeps an IMMEDIATE transaction open for the whole
    call, so the second writer can only ever fail - what is being measured is
    whether it failed AT ONCE or waited first.
    """
    blocker = sqlite3.connect(str(path))
    blocker.execute("BEGIN IMMEDIATE")
    blocker.execute("INSERT INTO jobs (key, title) VALUES ('held', 'x')")

    second = sqlite3.connect(str(path))
    second.execute(f"PRAGMA busy_timeout = {timeout_ms}")
    started = time.monotonic()
    error: Exception | None = None
    try:
        second.execute("INSERT INTO jobs (key, title) VALUES ('other', 'y')")
        second.commit()
    except sqlite3.OperationalError as e:
        error = e
    elapsed = time.monotonic() - started
    second.close()
    blocker.rollback()
    blocker.close()
    return elapsed, error


@pytest.fixture
def two_writer_db(tmp_path):
    path = tmp_path / "contended.db"
    con = sqlite3.connect(str(path))
    con.execute("PRAGMA journal_mode = WAL")
    con.execute("CREATE TABLE jobs (key TEXT PRIMARY KEY, title TEXT)")
    con.commit()
    con.close()
    return path


def test_without_a_timeout_a_blocked_write_fails_instantly(two_writer_db):
    """NEGATIVE CONTROL. This is the behaviour the app shipped with.

    timeout 0 is SQLite's default, and it is why a status change during a
    collect came back as "database is locked" rather than simply taking a
    moment.
    """
    elapsed, error = _blocked_write(two_writer_db, 0)
    assert error is not None, "a blocked write must fail when no timeout is set"
    assert "locked" in str(error).lower()
    assert elapsed < 0.1, f"failed after {elapsed:.3f}s - that is not instant"


def test_with_a_timeout_a_blocked_write_waits(two_writer_db):
    """POSITIVE CONTROL. The setting is load-bearing, not decorative.

    The blocker never releases, so this write still fails in the end - what
    changed is that it SPENT THE TIMEOUT WAITING first. In the real case the
    collect's transaction commits in milliseconds and the wait ends in success.
    """
    elapsed, error = _blocked_write(two_writer_db, WAIT_MS)
    # The ERROR IS CHECKED, not just its presence. `is not None` alone would be
    # satisfied by any OperationalError - bad SQL, a missing table - so the test
    # could pass while measuring a failure that has nothing to do with locking.
    assert error is not None
    assert "locked" in str(error).lower(), f"failed for another reason: {error}"
    assert elapsed >= (WAIT_MS / 1000) * 0.6, (
        f"gave up after {elapsed:.3f}s, so the timeout was not honoured")


def test_a_reader_is_never_blocked_by_a_writer_under_wal(home):
    """WAL's half: the UI can read the board while a collect writes to it."""
    writer = db.connect(home)
    reader = db.connect(home)
    try:
        writer.execute("BEGIN IMMEDIATE")
        writer.execute(
            "INSERT INTO jobs (key, title, qualified) VALUES ('gh:1', 'A', 1)")
        writer.commit()

        # Committed by the other connection, and visible without waiting.
        assert reader.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1
    finally:
        writer.close()
        reader.close()
