"""An installed profile keeps its alt rows on the right card after upgrading.

CREATE TABLE IF NOT EXISTS does nothing to a table that already exists, so a
database made before alt_reason existed has every alt row carrying no reason
at all. Two things then have to hold, and only one of them is about the new
column: the split has to be RECOVERED where the evidence is there, and no row
may fall off the dashboard where it is not.

The evidence is real rather than a guess. screen.py describes the pay case,
and only the pay case, as being above a "fallback floor" - so an UPDATE over
screen_reasons reproduces exactly the split screening would make today.
"""
from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

from unlatched import db

if TYPE_CHECKING:
    from pathlib import Path


def _database_from_before_the_column(path: Path) -> sqlite3.Connection:
    """A profile whose jobs table predates alt_reason.

    Built by creating the current schema and dropping the column, rather than
    by pasting an old CREATE TABLE: a copied schema stops being what the app
    used to write the moment anything else changes, and this stays honest.
    """
    con = db.connect_at(path)
    con.execute("ALTER TABLE jobs DROP COLUMN alt_reason")
    # AND THE MARKER WITH IT. connect_at just ran the migration, so the stored
    # version says the split has already been done; leaving it would make this
    # a database that predates the column and claims otherwise, and the test
    # would then prove only that the guard works.
    con.execute("DELETE FROM meta WHERE key = 'alt_reason_version'")
    con.commit()
    con.close()
    return sqlite3.connect(path)


def _add(con: sqlite3.Connection, key: str, verdict: str, reasons: str) -> None:
    con.execute(
        "INSERT INTO jobs (key, company_id, title, verdict, qualified, "
        "fetched_at, screen_reasons) VALUES (?, 1, 'Support', ?, 1, "
        "'2026-08-01T09:00:00', ?)",
        (key, verdict, reasons))


def test_the_column_is_missing_before_the_migration(tmp_path):
    """The positive control. Without this, every assertion below would pass
    just as happily against a database that already had the column, and the
    migration would be untested.
    """
    con = _database_from_before_the_column(tmp_path / "old.db")
    columns = {row[1] for row in con.execute("PRAGMA table_info(jobs)")}
    assert "alt_reason" not in columns


def test_an_upgrade_recovers_the_pay_case_from_what_screening_wrote(tmp_path):
    path = tmp_path / "old.db"
    con = _database_from_before_the_column(path)
    _add(con, "gh:pay", "alt",
         "pay under the $70,000 floor (above the $52,000 fallback floor)")
    _add(con, "gh:degree", "alt", "asks for a bachelor's degree")
    _add(con, "gh:thin", "alt", "description too short to judge")
    _add(con, "gh:clean", "keep", "")
    con.commit()
    con.close()

    upgraded = db.connect_at(path)
    reasons = dict(upgraded.execute("SELECT key, alt_reason FROM jobs"))

    assert reasons["gh:pay"] == "salary"
    # NOT guessed at. Screening records no marker for these - counted: the
    # phrase "fallback floor" appears in exactly one place in the engine, the
    # pay branch of screen.py - so the honest answer is that the reason is
    # unknown, and the requirements card is written to include unknown rather
    # than leave those rows unreachable.
    assert reasons["gh:degree"] is None
    assert reasons["gh:thin"] is None
    assert reasons["gh:clean"] is None


def test_a_keep_is_never_relabelled_by_the_backfill(tmp_path):
    """The UPDATE is scoped to alt rows. A kept job whose reasons happen to
    mention the fallback floor - a row that was alt on an earlier run and
    became a keep when the floor changed - must not be filed under a card
    for jobs that fell short.
    """
    path = tmp_path / "old.db"
    con = _database_from_before_the_column(path)
    _add(con, "gh:1", "keep",
         "pay under the $70,000 floor (above the $52,000 fallback floor)")
    con.commit()
    con.close()

    upgraded = db.connect_at(path)
    stored = upgraded.execute(
        "SELECT alt_reason FROM jobs WHERE key = 'gh:1'").fetchone()[0]
    assert stored is None


def test_upgrading_twice_changes_nothing_the_second_time(tmp_path):
    path = tmp_path / "old.db"
    con = _database_from_before_the_column(path)
    _add(con, "gh:pay", "alt",
         "pay under the $70,000 floor (above the $52,000 fallback floor)")
    con.commit()
    con.close()

    db.connect_at(path).close()
    # A person re-screens and the row is no longer held back on pay. The
    # backfill must not reinstate the old answer on the next launch: it runs
    # when the column is ADDED, not on every open.
    second = db.connect_at(path)
    second.execute("UPDATE jobs SET alt_reason = 'requirements'")
    second.commit()
    second.close()

    third = db.connect_at(path)
    stored = third.execute(
        "SELECT alt_reason FROM jobs WHERE key = 'gh:pay'").fetchone()[0]
    assert stored == "requirements"


def test_the_backfill_runs_even_when_the_column_already_exists(tmp_path):
    """THE DEFECT THIS GUARD REPLACED, on 2026-09-02.

    Both halves of the app create the jobs table. The first version keyed the
    backfill off "did we just add the column", so whichever half migrated
    SECOND found it already there and skipped - and for somebody who installs
    an update and opens the app, the desktop always goes first. Observed on a
    real profile: 563 alt rows, 83 of them describing a fallback floor, all
    left unlabelled, with the salary card reading 0.

    Here the column is added WITHOUT the marker, which is exactly the state
    the other half leaves behind.
    """
    path = tmp_path / "half-migrated.db"
    con = _database_from_before_the_column(path)
    _add(con, "gh:pay", "alt",
         "pay under the $70,000 floor (above the $52,000 fallback floor)")
    _add(con, "gh:other", "alt", "asks for a bachelor's degree")
    # What the other half does: the column, and nothing else.
    con.execute("ALTER TABLE jobs ADD COLUMN alt_reason TEXT")
    con.commit()
    con.close()

    upgraded = db.connect_at(path)
    reasons = dict(upgraded.execute("SELECT key, alt_reason FROM jobs"))
    assert reasons["gh:pay"] == "salary"
    assert reasons["gh:other"] is None
