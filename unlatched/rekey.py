"""rekey.py - make a job's key agree with the collector that wrote it.

THE DISAGREEMENT. A key is `<prefix>:<id>`, and the prefix is meant to say
which collector the row came from. On a live board it did not: 410 rows carried
`manual:` while their source said `imported`, because that collector's rows
arrived pre-keyed from when it pushed them as hand-adds.

Nothing READ the prefix - recheck and the population queries scope on
jobs.source - so this was cosmetic right up until the moment it was not. Under
several collectors the prefix is the namespace that keeps two collectors'
reports of the same posting apart, and a namespace that lies is worse than no
namespace at all.

Decided 2026-08-13: the stored data has to reflect what is actually true, and
moving the pipeline to achieve that is the acceptable cost.

SO THE PIPELINE MOVES WITH THE KEY. A job's key is referenced by its status,
its whole status history, its notes and its attachments; renaming the job alone
would leave every one of those pointing at a row that no longer exists. This
module moves all of them in one transaction, or none of them.

WHAT IT WILL NOT DO. It never merges two jobs. If the corrected key is already
taken by a different row, that row is REPORTED and left exactly as it is - a
merge would fold two postings' histories together, and no automatic rule should
be allowed to do that.
"""
from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import os
    from pathlib import Path

# Every column anywhere in the schema that holds a job key. A table missing
# from this list is a table whose rows would be orphaned by a rename, so it is
# written out in full rather than discovered - and the test asserts this list
# matches what the database actually has.
KEY_COLUMNS: tuple[tuple[str, str], ...] = (
    ("jobs", "key"),
    # The posting this one was folded behind. A key that moved without this
    # would leave a group pointing at nothing, and the row would come back out
    # of hiding on the next dedupe pass.
    ("jobs", "duplicate_of"),
    ("job_status", "key"),
    ("job_status_log", "key"),
    ("job_note", "key"),
    ("attachment", "key"),
)

MARKER = "rekey_prefix_matches_source"


def _table_exists(con: sqlite3.Connection, table: str) -> bool:
    row = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,)).fetchone()
    return row is not None


def plan(con: sqlite3.Connection) -> tuple[list[tuple[str, str]], list[dict[str, Any]]]:
    """What would change, and what cannot. Reads only.

    Returns (moves, conflicts) where moves is [(old_key, new_key)].
    """
    rows = con.execute(
        "SELECT key, source FROM jobs "
        "WHERE source IS NOT NULL AND source != '' AND instr(key, ':') > 0"
    ).fetchall()
    existing = {r[0] for r in con.execute("SELECT key FROM jobs")}

    moves: list[tuple[str, str]] = []
    conflicts: list[dict[str, Any]] = []
    for row in rows:
        key, source = row[0], row[1]
        prefix, _, suffix = key.partition(":")
        if prefix == source or not suffix:
            continue
        new_key = f"{source}:{suffix}"
        if new_key in existing:
            # TWO ROWS, ONE CORRECTED KEY. Left alone and named: merging them
            # would join two postings' application histories, which is not a
            # decision a migration gets to make.
            conflicts.append({"key": key, "would_be": new_key, "source": source})
            continue
        existing.add(new_key)
        moves.append((key, new_key))
    return moves, conflicts


def backup(home: Path | os.PathLike[str] | str, con: sqlite3.Connection) -> Path:
    """A copy of the database beside it, taken before anything is written.

    THROUGH SQLITE'S OWN BACKUP API, not a file copy. This database runs in WAL
    mode, so the .db file on its own is not the whole story - a plain copy taken
    while a write-ahead log holds recent pages produces a backup that is missing
    exactly the most recent work. sqlite3's backup() checkpoints properly.
    """
    # Imported here rather than at the top for the same reason as run_once
    # below: db imports this module at load time.
    from . import db as db_mod
    return db_mod.backup(home, con, tag="rekey")


def apply(con: sqlite3.Connection, home: Path | os.PathLike[str] | str,
          ) -> dict[str, Any]:
    """Correct every disagreeing key, moving its pipeline with it.

    ALL OR NOTHING. One transaction: a rename that got halfway would leave
    statuses pointing at jobs that no longer exist, which is the exact damage
    this is meant to prevent.
    """
    moves, conflicts = plan(con)
    result: dict[str, Any] = {"moved": 0, "conflicts": conflicts, "backup": None,
                              "rows_touched": {}}
    if not moves:
        return result

    result["backup"] = str(backup(home, con))
    touched: dict[str, int] = {}
    con.execute("BEGIN IMMEDIATE")
    try:
        for old, new in moves:
            for table, column in KEY_COLUMNS:
                if not _table_exists(con, table):
                    continue
                cur = con.execute(
                    f"UPDATE {table} SET {column} = ? WHERE {column} = ?",  # noqa: S608
                    (new, old))
                if cur.rowcount:
                    touched[f"{table}.{column}"] = (
                        touched.get(f"{table}.{column}", 0) + cur.rowcount)
    except sqlite3.Error:
        con.rollback()
        raise
    con.commit()
    result["moved"] = len(moves)
    result["rows_touched"] = touched
    return result


def run_once(con: sqlite3.Connection, home: Path | os.PathLike[str] | str,
             ) -> dict[str, Any] | None:
    """Correct the keys if they need it, and remember that it was done.

    IDEMPOTENT TWO WAYS OVER: the marker stops it being considered again, and
    even without the marker a second run finds nothing to move because the
    prefixes now agree. The marker exists so a database that has been through
    this can say so, not to make the operation safe.
    """
    from . import db as db_mod

    moves, _ = plan(con)
    if not moves:
        if db_mod.get_meta(con, MARKER) is None:
            db_mod.set_meta(con, MARKER, datetime.now(tz=UTC).isoformat())
        return None
    result = apply(con, home)
    db_mod.set_meta(con, MARKER, datetime.now(tz=UTC).isoformat())
    return result


def orphans(con: sqlite3.Connection) -> dict[str, int]:
    """Rows in the pipeline tables whose job key does not exist.

    THE CHECK THAT PROVES THE MOVE WAS COMPLETE, and the one worth keeping
    afterwards: it answers "did anything get left behind" for every table at
    once, rather than for the tables somebody remembered.
    """
    found: dict[str, int] = {}
    for table, column in KEY_COLUMNS:
        if table == "jobs" and column == "key":
            continue
        if not _table_exists(con, table):
            continue
        count = con.execute(
            f"SELECT COUNT(*) FROM {table} t "  # noqa: S608
            f"WHERE t.{column} IS NOT NULL AND t.{column} != '' "
            f"AND NOT EXISTS (SELECT 1 FROM jobs j WHERE j.key = t.{column})"
        ).fetchone()[0]
        if count:
            found[f"{table}.{column}"] = count
    return found
