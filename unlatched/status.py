"""status.py - The human half of a job's record: status and its history.

`jobs.*` (screen.py's output) and `job_status`/`job_status_log` (this
module) are different axes on purpose. A re-screen may rewrite the first
freely; nothing in this package ever writes the second two except the
functions here, and `screen.py` never imports this module at all. That
separation is deliberate: a rescore pass once overwrote a person's own
"applied" and "pass" decisions, because the original schema kept machine
output and human judgement in the same row.

Rows are addressed by `jobs.key` (`source:id`), never by list position - a
newly collected posting can shift every row's position in a listing, and a
status keyed to a position would silently attach to the wrong job the next
time the list is rebuilt.

Import/export round-trips the shape a status export actually takes:
`{"status": {key: {"status": s, "at": iso}}, "log": [{"key", "from", "to",
"at"}], "exported": iso}`. A transition's "to" is the status recorded in
`job_status_log.status`; "from" is derived, never stored - our log table
has no "from" column, because a status's history is fully recoverable by
replaying "to" values in order. A null "to" means the status was cleared:
the transition is still appended to the log (history is never edited after
the fact), and the corresponding `job_status` row is removed. Older or
hand-built exports may carry a bare string, or a `{"status", "note",
"updated"}` object, instead of the primary shape - both are accepted, and
every key is kept exactly as given, including a prefix like "li:" this
package itself never generates.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    import sqlite3


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


# THE VOCABULARY, mirroring desktop/src/status.rs.
#
# The desktop half owns the labels, colours and dependency rules because it is
# the half that draws them. What the engine needs is narrower: which statuses
# mean a person has closed the loop on a job, and which prove an application
# was made. Both are hand-written here and both are checked against status.rs
# by tests/test_status_vocabulary.py - two SQL lists that disagreed across the
# two halves is exactly the failure that test exists to catch, and this file
# and that one are edited by different hands weeks apart.
FLOW = ("applied", "interviewed", "offer", "accepted_offer", "hired",
        "offer_withdrawn", "declined_offer", "no_offer", "pass")

# Closed the loop, either way.
SETTLED = ("hired", "offer_withdrawn", "declined_offer", "no_offer", "pass",
           "closed")

# What the app writes onto a taken-down posting nobody ever judged.
#
# DELIBERATELY NOT IN `FLOW`, which is what keeps it out of every dropdown: it
# is a word the app writes, never one a person picks, so it cannot compete with
# the statuses they set. `delisted_at` remains the fact that the employer
# pulled the advert - this is only the status filled in where the person had
# recorded nothing, so those rows stop reading "not set" for ever.
#
# Written by db.close_untouched_delisted here and by db.rs::mark_taken_down in
# the desktop. Both halves are checked against each other by
# tests/test_status_vocabulary.py.
CLOSED = "closed"

# Every status that proves an application was actually sent. Read from the LOG
# rather than the current status, so a job since marked No Offer still counts:
# somebody clearing out rejections is removing exactly the rows worth warning
# about.
PROVES_APPLIED = ("applied", "interviewed", "offer", "accepted_offer", "hired",
                  "offer_withdrawn", "declined_offer", "no_offer")

# Statuses renamed since they were first written, applied on open by
# db._migrate_status_tables. Mirrors status::RENAMES in the desktop.
#
# "Denied" was the app's word for it; the first user asked for "No Offer" - the same event,
# said without the verdict on the person. `closed` is deliberately NOT in here:
# it meant "the opening went away", which the app now derives from
# jobs.delisted_at, and rewriting those rows into some other status would be
# inventing a decision nobody made.
RENAMES = (("denied", "no_offer"),)


def placeholders(values: tuple[str, ...]) -> str:
    """`?, ?` - the bind markers for a SQL `IN (...)` over `values`.

    MARKERS RATHER THAN THE VALUES THEMSELVES. These constants are not user
    input, so quoting them inline would be safe today - but a statement built by
    interpolation is one somebody later extends with something that is not a
    constant, and the linter is right to refuse to tell the two cases apart.
    The caller passes the same tuple as parameters.
    """
    return ", ".join("?" for _ in values)


def set_status(con: sqlite3.Connection, key: str, status: str,
               note: str | None = None, at: str | None = None,
               pay: str | None = None, offer_date: str | None = None) -> None:
    """Record a status change, with whatever was written about it.

    `note` is the note for THIS transition, not the job's standing note.
    COALESCE, so a change carrying nothing new LEAVES the existing note alone
    instead of erasing it - this half used to overwrite while the desktop half
    preserved, so the same action through two doors gave two different results.

    `pay` and `offer_date` belong to the LOG row rather than to job_status: they
    describe one event, and a second offer after a re-application is a different
    number that must not overwrite the first.
    """
    when = at or now_iso()
    con.execute(
        "INSERT INTO job_status (key, status, note, updated) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(key) DO UPDATE SET status=excluded.status, "
        "note=COALESCE(excluded.note, job_status.note), updated=excluded.updated",
        (key, status, note, when))
    con.execute(
        "INSERT INTO job_status_log (key, status, note, at, pay, offer_date) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (key, status, note, when, pay, offer_date))
    con.commit()


def add_note(con: sqlite3.Connection, key: str, note: str,
             at: str | None = None) -> None:
    """Record a note that is not about a status change.

    APPENDS. The first user's rule for every note in the app: nothing a person wrote is
    replaced by the next thing they write. The standing note follows the most
    recent one because that is what the list column shows; job_note keeps the
    earlier ones.
    """
    when = at or now_iso()
    con.execute("INSERT INTO job_note (key, note, at) VALUES (?, ?, ?)",
                (key, note, when))
    con.execute("UPDATE job_status SET note = ? WHERE key = ?", (note, key))
    con.commit()


def clear_status(con: sqlite3.Connection, key: str, at: str | None = None) -> None:
    when = at or now_iso()
    con.execute("DELETE FROM job_status WHERE key = ?", (key,))
    con.execute(
        "INSERT INTO job_status_log (key, status, note, at) VALUES (?, NULL, NULL, ?)",
        (key, when))
    con.commit()


def get_status(con: sqlite3.Connection, key: str) -> sqlite3.Row | None:
    # sqlite3.Cursor.fetchone() is typed Any in the stdlib stubs; the row
    # factory is set to sqlite3.Row for every connection this package opens
    # (db.py), so the cast states a fact the stubs cannot express.
    row = con.execute("SELECT * FROM job_status WHERE key = ?", (key,)).fetchone()
    return cast("sqlite3.Row | None", row)


def list_status(con: sqlite3.Connection,
                 status_filter: str | None = None) -> list[sqlite3.Row]:
    if status_filter:
        rows = con.execute(
            "SELECT * FROM job_status WHERE status = ? ORDER BY updated DESC",
            (status_filter,)).fetchall()
    else:
        rows = con.execute("SELECT * FROM job_status ORDER BY updated DESC").fetchall()
    return cast("list[sqlite3.Row]", rows)


def log_for(con: sqlite3.Connection, key: str) -> list[sqlite3.Row]:
    rows = con.execute(
        "SELECT * FROM job_status_log WHERE key = ? ORDER BY id", (key,)).fetchall()
    return cast("list[sqlite3.Row]", rows)


def export_status(con: sqlite3.Connection) -> dict[str, Any]:
    status = {
        row["key"]: {"status": row["status"], "at": row["updated"]}
        for row in con.execute("SELECT key, status, updated FROM job_status")
    }
    log: list[dict[str, Any]] = []
    prev: dict[str, str | None] = {}
    for row in con.execute(
            "SELECT key, status, note, at, pay, offer_date "
            "FROM job_status_log ORDER BY id"):
        key = row["key"]
        entry: dict[str, Any] = {
            "key": key, "from": prev.get(key), "to": row["status"], "at": row["at"]}
        # Written only when present, so an ordinary transition stays the three
        # fields it always was and an older reader is unaffected by fields it
        # has never seen.
        for name in ("note", "pay", "offer_date"):
            if row[name]:
                entry[name] = row[name]
        log.append(entry)
        prev[key] = row["status"]
    # Notes not tied to a status change travel as their own list, mirroring the
    # table split. Folding them into `log` would give them a null status, which
    # import_status reads as "this status was cleared" - the note would arrive
    # having deleted the thing it was written about.
    notes = [
        {"key": row["key"], "note": row["note"], "at": row["at"]}
        for row in con.execute("SELECT key, note, at FROM job_note ORDER BY id")
    ]
    return {"status": status, "log": log, "notes": notes, "exported": now_iso()}


def _status_value_fields(value: Any) -> tuple[Any, Any, Any] | None:
    """Accept a bare status string, the primary {"status", "at"} shape, or
    the older {"status", "note", "updated"} shape. Returns (status, note,
    updated) or None if `value` carries no usable status.
    """
    if isinstance(value, str):
        return (value, None, None) if value else None
    if isinstance(value, dict):
        s = value.get("status")
        if not s:
            return None
        note = value.get("note")
        updated = value.get("at") or value.get("updated")
        return (s, note, updated)
    return None


def import_status(con: sqlite3.Connection, data: dict[str, Any]) -> dict[str, Any]:
    """Import an export produced by this module (or the browser dashboard it
    replaces). Keys are kept verbatim - they are not required to look like
    anything this package would itself generate.

    Log entries are replayed first (oldest to newest, as given), then the
    "status" dict is applied on top - the dict is the authoritative CURRENT
    state, so this order guarantees the final `job_status` table matches it
    exactly even if the log's last entry for a key does not.
    """
    status_map = data.get("status") or {}
    log_list = data.get("log") or []
    now = now_iso()

    log_rows = 0
    for entry in log_list:
        if not isinstance(entry, dict):
            continue
        key = entry.get("key")
        if not key:
            continue
        if "to" in entry:
            new_status = entry.get("to")
        elif "status" in entry:
            new_status = entry.get("status")
        else:
            continue
        note = entry.get("note")
        at = entry.get("at") or entry.get("updated") or now
        con.execute(
            "INSERT INTO job_status_log (key, status, note, at, pay, offer_date) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (key, new_status, note, at, entry.get("pay"), entry.get("offer_date")))
        log_rows += 1
        if new_status is None:
            con.execute("DELETE FROM job_status WHERE key = ?", (key,))

    status_rows = 0
    for key, value in status_map.items():
        parsed = _status_value_fields(value)
        if parsed is None:
            continue
        s, note, updated = parsed
        con.execute(
            "INSERT INTO job_status (key, status, note, updated) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET status=excluded.status, "
            "note=excluded.note, updated=excluded.updated",
            (key, s, note, updated or now))
        status_rows += 1

    # An export written before notes existed has no "notes" key at all, which
    # is not the same as having none - both end up importing zero, and neither
    # is an error.
    note_rows = 0
    for entry in data.get("notes") or []:
        if not isinstance(entry, dict):
            continue
        key, note = entry.get("key"), entry.get("note")
        if not key or not note:
            continue
        con.execute(
            "INSERT INTO job_note (key, note, at) VALUES (?, ?, ?)",
            (key, note, entry.get("at") or now))
        note_rows += 1

    con.commit()
    return {"status_rows": status_rows, "log_rows": log_rows,
            "note_rows": note_rows}
