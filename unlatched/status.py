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

from datetime import datetime
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    import sqlite3


def now_iso() -> str:
    """The local wall clock with its offset: "2026-09-05T14:46:41-04:00".

    LOCAL, NOT UTC, matching desktop/src/date.rs::now_iso. Both halves write
    job_status and job_status_log, so a disagreement here is a disagreement
    inside one column of one database.

    astimezone() with no argument attaches THIS MACHINE'S zone, asked of the
    operating system, so it is right wherever the app is installed and on both
    sides of a daylight-saving change. The offset is kept on the stamp: a bare
    local time is ambiguous across that change and unusable for the
    cross-tool comparison the collector needs.
    """
    return datetime.now().astimezone().isoformat(timespec="seconds")


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
        "offer_withdrawn", "declined_offer", "no_offer", "rejection_email",
        "no_response", "pass")

# Closed the loop, either way.
SETTLED = ("hired", "offer_withdrawn", "declined_offer", "no_offer",
           "rejection_email", "no_response", "pass", "closed")

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
# Rejection Email and No Response are both here: each of them is an
# application that WAS sent, and the difference between them is whether
# anybody answered - not whether it happened.
PROVES_APPLIED = ("applied", "interviewed", "offer", "accepted_offer", "hired",
                  "offer_withdrawn", "declined_offer", "no_offer",
                  "rejection_email", "no_response")

# Statuses renamed since they were first written, applied on open by
# db._migrate_status_tables. Mirrors status::RENAMES in the desktop.
#
# "Denied" was the app's word for it; "No Offer" replaced it - the same event,
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

    APPENDS. The rule for every note in the app: nothing a person wrote is
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


def _log_row_id(con: sqlite3.Connection, key: str, new_status: Any,
                at: str) -> int | None:
    """The id of an already-recorded transition, or None.

    IDENTITY IS (key, status, at). Two genuinely different events cannot share
    all three: a status set twice for one job in the same second IS one event,
    and a real re-application months later carries a different `at`. A job that
    goes applied -> pass -> applied keeps both "applied" rows, because their
    timestamps differ.

    `IS` rather than `=`, because a cleared status is stored as NULL and
    `status = NULL` is never true - the row would be invisible to this check
    and every re-import would append another clear.
    """
    row = con.execute(
        "SELECT id FROM job_status_log WHERE key = ? AND at = ? AND status IS ?",
        (key, at, new_status)).fetchone()
    return int(row[0]) if row else None


def import_status(con: sqlite3.Connection, data: dict[str, Any]) -> dict[str, Any]:
    """Import an export in the shape `export_status` produces.

    Keys are kept verbatim - they are not required to look like anything this
    package would itself generate, so a file written by some other tool the
    person was tracking their search in can be brought straight in.

    Log entries are replayed first (oldest to newest, as given), then the
    "status" dict is applied on top - the dict is the authoritative CURRENT
    state, so this order guarantees the final `job_status` table matches it
    exactly even if the log's last entry for a key does not.

    IMPORTING THE SAME EXPORT TWICE CHANGES NOTHING. It used to append every
    log row again, which is not a tidiness problem: the dashboard's funnel,
    the Applied column and the response rate are all counted FROM THIS LOG, so
    a second import doubled the number of applications a person appears to
    have made. `applied_among` reads it too, so a bulk-retire warning would
    have counted the same application twice.

    A transition already present is skipped rather than replaced, EXCEPT that
    a note, pay or offer date arriving for a row that has none is filled in -
    the same COALESCE rule `set_status` uses, and for the same reason: a later
    statement carrying more detail should not be thrown away, and one carrying
    less should not erase what is there.

    WHAT THIS DOES NOT DO is remove duplicates a previous import already
    created. Rewriting somebody's recorded history is a different act from
    declining to add to it, and it needs to be asked for rather than done on
    open.
    """
    status_map = data.get("status") or {}
    log_list = data.get("log") or []
    now = now_iso()

    log_rows = 0
    log_duplicates = 0
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
        existing = _log_row_id(con, key, new_status, at)
        if existing is not None:
            # Already recorded. COALESCE the three optional fields so a
            # re-export carrying detail this database lacks still lands, while
            # one carrying less cannot blank what is here.
            con.execute(
                "UPDATE job_status_log SET note = COALESCE(note, ?), "
                "pay = COALESCE(pay, ?), offer_date = COALESCE(offer_date, ?) "
                "WHERE id = ?",
                (note, entry.get("pay"), entry.get("offer_date"), existing))
            log_duplicates += 1
            continue
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
    note_duplicates = 0
    for entry in data.get("notes") or []:
        if not isinstance(entry, dict):
            continue
        key, note = entry.get("key"), entry.get("note")
        if not key or not note:
            continue
        at = entry.get("at") or now
        # A note is its own text at its own moment, so all three identify it.
        # The same words written twice about one job on different days are two
        # notes and both are kept; the same words at the same moment are the
        # same note arriving twice.
        already = con.execute(
            "SELECT 1 FROM job_note WHERE key = ? AND note = ? AND at = ?",
            (key, note, at)).fetchone()
        if already:
            note_duplicates += 1
            continue
        con.execute(
            "INSERT INTO job_note (key, note, at) VALUES (?, ?, ?)",
            (key, note, at))
        note_rows += 1

    con.commit()
    # THE SKIPPED COUNTS ARE REPORTED, not swallowed. "imported 0" after a
    # re-import reads as a failure; "0 added, 412 already here" is the same
    # fact said in a way somebody can act on.
    return {"status_rows": status_rows, "log_rows": log_rows,
            "note_rows": note_rows, "log_duplicates": log_duplicates,
            "note_duplicates": note_duplicates}
