"""Delete postings that never matched and that nobody ever looked at.

WHY DELETE, when every other removal in this codebase hides instead. Retiring
and delisting protect rows a person has a relationship with: something they
applied to, judged, or wrote a note about. These are the opposite - postings
that failed the person's own criteria, that they never opened and never
decided anything about. Nothing points at them and nothing remembers them.

WHY IT IS WORTH DOING AT ALL. Measured on a profile one month old: 14,492 rows
carrying 978 that matched. The closing passes of a collect - repost annotation,
duplicate detection, seat backfill - walk every row, and while they do it
nothing else can write; that is what made a status change during a collect fail
with "database is locked". The cost of these rows is not disk, it is the time
every pass spends on them.

WHY IT HOLDS THIS TIME. Deleting rows a collect will re-add buys one quiet pass
and nothing more. It works because the collector stops STORING postings that do
not qualify (see cli.collect): the board is still read and still screened, and
what fails the criteria is simply not written down. Delete without that change
and the table is back within a day; make that change without deleting and the
existing rows sit there for ever. Neither half is the feature on its own.

WHAT COUNTS AS UNTOUCHED, and the trap in it: 2,458 of these rows carried a
status, which looks like a person having judged them. Every one of them said
`closed`, which is the status the engine writes ITSELF when a posting comes off
its board and nobody had judged it (db.close_untouched_delisted). It is
deliberately outside the flow vocabulary, so a person cannot choose it. Reading
"has a status" as "was touched" would have spared 2,458 rows nobody ever saw.
A cleared status (a null in the log) is the opposite - somebody set one and
then took it back - and counts as touched.

SEAT HISTORY IS SPARED. Repost detection reads every row that shares a seat, so
deleting the earlier rounds of a seat whose latest round survives would quietly
shorten its history: a note reading "advertised 3 times since June" when it was
30. Candidates that share a seat with a surviving row are therefore kept. On
that same profile it cost 310 rows out of 11,057 to keep all 157 affected
seats whole.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from . import db, reposts
from . import status as status_vocab

if TYPE_CHECKING:  # pragma: no cover - typing only
    import os
    from pathlib import Path

# Rows to consider: never matched, not thrown away by hand, and carrying none
# of the four marks a person leaves - a note, an attachment, a status they
# chose, or a status they cleared.
_CANDIDATES = """
CREATE TEMP TABLE prune_candidate AS
SELECT key, seat, delisted_at FROM jobs
 WHERE qualified = 0
   AND retired_at IS NULL
   AND key NOT IN (SELECT key FROM job_note)
   AND key NOT IN (SELECT key FROM attachment)
   AND key NOT IN (SELECT key FROM job_status_log
                    WHERE status IS NULL OR status <> ?)
   AND key NOT IN (SELECT key FROM job_status
                    WHERE COALESCE(status, '') <> ''
                      AND COALESCE(status, '') <> ?)
"""

# Put back anything whose seat still has a keeper, so no surviving row's
# advertising history loses its earlier rounds.
_SPARE_SEATS = """
DELETE FROM prune_candidate
 WHERE seat IS NOT NULL
   AND seat IN (SELECT seat FROM jobs
                 WHERE seat IS NOT NULL
                   AND key NOT IN (SELECT key FROM prune_candidate))
"""


@dataclass(frozen=True)
class Plan:
    """What a prune would remove, counted before anything is written."""

    rows: int
    """Rows in the table now."""
    doomed: int
    """Rows this prune would delete."""
    still_listed: int
    """Of those, ones still on their board. They do not come back: the
    collector no longer stores a posting that fails the criteria."""
    delisted: int
    """Of those, ones already taken down."""
    seat_spared: int
    """Candidates kept because a surviving row shares their seat."""
    statuses: int
    """Engine-written `closed` rows that go with them."""

    @property
    def survivors(self) -> int:
        return self.rows - self.doomed


def _build(con: sqlite3.Connection) -> tuple[int, int]:
    """Fill the temp table. Returns (candidates before sparing, spared)."""
    con.execute("DROP TABLE IF EXISTS prune_candidate")
    con.execute(_CANDIDATES, (status_vocab.CLOSED, status_vocab.CLOSED))
    before = int(con.execute(
        "SELECT COUNT(*) FROM prune_candidate").fetchone()[0])
    con.execute(_SPARE_SEATS)
    after = int(con.execute(
        "SELECT COUNT(*) FROM prune_candidate").fetchone()[0])
    return before, before - after


def plan(con: sqlite3.Connection) -> Plan:
    """Count what a prune would take, changing nothing."""
    _, spared = _build(con)

    def count(sql: str) -> int:
        return int(con.execute(sql).fetchone()[0])

    return Plan(
        rows=count("SELECT COUNT(*) FROM jobs"),
        doomed=count("SELECT COUNT(*) FROM prune_candidate"),
        still_listed=count("SELECT COUNT(*) FROM prune_candidate "
                           "WHERE delisted_at IS NULL"),
        delisted=count("SELECT COUNT(*) FROM prune_candidate "
                       "WHERE delisted_at IS NOT NULL"),
        seat_spared=spared,
        statuses=count("SELECT COUNT(*) FROM job_status_log WHERE key IN "
                       "(SELECT key FROM prune_candidate)"),
    )


def apply(con: sqlite3.Connection,
          home: os.PathLike[str] | str | None = None) -> dict[str, Any]:
    """Take the backup, delete the rows, and leave the rest consistent.

    RE-PLANS RATHER THAN TAKING A PLAN. A caller that showed a plan, waited for
    a person to read it and then applied it would be deleting against counts
    that a collect may have moved underneath it. The plan is a report; the
    delete decides for itself.
    """
    intent = plan(con)
    backup: Path | None = None
    if home is not None and intent.doomed:
        backup = db.backup(home, con, tag="prune")

    con.execute("DELETE FROM job_status WHERE key IN "
                "(SELECT key FROM prune_candidate)")
    con.execute("DELETE FROM job_status_log WHERE key IN "
                "(SELECT key FROM prune_candidate)")
    con.execute("DELETE FROM jobs WHERE key IN "
                "(SELECT key FROM prune_candidate)")
    # A row folded behind one of these would now be hidden behind nothing and
    # invisible for ever. duplicate_of is not rebuilt from scratch anywhere,
    # so it is corrected here; repost_of is, by annotate below.
    con.execute("UPDATE jobs SET duplicate_of = NULL, duplicate_reason = NULL "
                "WHERE duplicate_of IS NOT NULL "
                "AND duplicate_of NOT IN (SELECT key FROM jobs)")
    con.commit()
    reposts.annotate(con)
    con.execute("DROP TABLE IF EXISTS prune_candidate")
    con.commit()
    # Deleted pages stay in the file as free space, so the profile would not
    # get smaller and neither would a backup of it. VACUUM cannot run inside a
    # transaction, which is why it is after the commit.
    #
    # AND IT IS ALLOWED TO FAIL. It rewrites the whole file and needs every
    # other connection to be out of the way; the app itself holds one open
    # while it is running, and this verb is meant to be runnable from there.
    # The rows are already gone and committed by this point - reclaiming the
    # space is the one part that can wait for the next quiet moment, and
    # raising here would report a completed delete as an error.
    reclaimed = True
    try:
        con.execute("VACUUM")
    except sqlite3.OperationalError:
        reclaimed = False
    return {"deleted": intent.doomed, "kept": intent.survivors,
            "still_listed": intent.still_listed, "delisted": intent.delisted,
            "seat_spared": intent.seat_spared, "statuses": intent.statuses,
            "reclaimed": reclaimed,
            "backup": str(backup) if backup else None}
