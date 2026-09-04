"""export.py - get the pipeline out, into something that outlives this app.

Two applications have been LOST because their status existed in exactly one
place with no way to read it out, and both were recovered only because a
screenshot happened to show a board's own "Applied" badge. That is not a
recovery procedure.

Making one store authoritative is the right architecture and it is also a single
point of loss, so this is not a nice-to-have: a person who loses months of
application history has lost the thing the app is for.

WHAT IT WRITES
One CSV. Not JSON, not a database dump - a person recovering their history is
having a bad day already, and the tool they have is a spreadsheet.

EVERYTHING, NOT THE CURRENT VIEW. Removed and delisted rows come too. A filter
is not a backup, and a posting the employer has taken down is precisely the one
that can no longer be reconstructed from the web.

THE HISTORY IS FLATTENED, NOT DROPPED. job_status_log is append-only and is what
the funnel and the response rate are read from; a snapshot of the current status
alone would lose the fact that a job was applied to before it was refused. It
becomes one readable column rather than a second file, so the export stays one
attachment a person can email themselves.
"""
from __future__ import annotations

import csv
from typing import TYPE_CHECKING, Any, NamedTuple

if TYPE_CHECKING:
    import sqlite3
    from pathlib import Path

# Ordered for reading, not for the schema: who and what first, then what the
# person did about it, then the identifiers needed to find it again.
COLUMNS = (
    "company",
    "title",
    "location",
    "status",
    "applied_on",
    "history",
    "notes",
    "pay_offered",
    "offer_date",
    "posted",
    "found_at",
    "url",
    "apply_url",
    "removed_on",
    "taken_down_on",
    "key",
)

_SQL = """
SELECT jobs.key            AS key,
       companies.name      AS company,
       jobs.title          AS title,
       jobs.location       AS location,
       jobs.posted_at      AS posted,
       jobs.fetched_at     AS found_at,
       jobs.url            AS url,
       jobs.apply_url      AS apply_url,
       jobs.retired_at     AS removed_on,
       jobs.delisted_at    AS taken_down_on,
       job_status.status   AS status
FROM jobs
LEFT JOIN companies  ON companies.id = jobs.company_id
LEFT JOIN job_status ON job_status.key = jobs.key
ORDER BY companies.name COLLATE NOCASE, jobs.title COLLATE NOCASE
"""


class _Entry(NamedTuple):
    """One status change, with everything recorded against it."""

    at: str
    status: str
    note: str
    pay: str
    offer_date: str


def _history(con: sqlite3.Connection) -> dict[str, list[_Entry]]:
    """Every status a job has ever been given, oldest first, keyed by job."""
    out: dict[str, list[_Entry]] = {}
    for row in con.execute(
            "SELECT key, status, at, note, pay, offer_date "
            "FROM job_status_log ORDER BY key, id"):
        out.setdefault(row["key"], []).append(_Entry(
            at=row["at"] or "",
            status=row["status"] or "",
            note=row["note"] or "",
            pay=row["pay"] or "",
            offer_date=row["offer_date"] or "",
        ))
    return out


def _standalone_notes(con: sqlite3.Connection) -> dict[str, list[tuple[str, str]]]:
    """Notes written outside a status change, oldest first, keyed by job."""
    out: dict[str, list[tuple[str, str]]] = {}
    for row in con.execute("SELECT key, note, at FROM job_note ORDER BY key, id"):
        out.setdefault(row["key"], []).append((row["at"] or "", row["note"] or ""))
    return out


def _first_applied(entries: list[_Entry]) -> str:
    """When they applied, from the LOG rather than the current status.

    A job since marked No Offer was still applied to, and the date they did it
    is the one they need when an employer asks.
    """
    return next((e.at for e in entries if e.status == "applied"), "")


def _offer_terms(entries: list[_Entry]) -> tuple[str, str]:
    """Pay and date from the MOST RECENT offer, or empty.

    The most recent rather than the first: a re-negotiated or second offer from
    the same employer is the one that stands.
    """
    for entry in reversed(entries):
        if entry.pay or entry.offer_date:
            return entry.pay, entry.offer_date
    return "", ""


def rows(con: sqlite3.Connection) -> list[dict[str, Any]]:
    """Every job, with its history, ready to write."""
    log = _history(con)
    notes = _standalone_notes(con)
    out = []
    # The columns the query supplies. The rest are computed below, so they are
    # listed here rather than probed for on every row.
    from_query = (
        "key", "company", "title", "location", "posted", "found_at",
        "url", "apply_url", "removed_on", "taken_down_on",
    )
    for row in con.execute(_SQL):
        entries = log.get(row["key"], [])
        record: dict[str, Any] = {name: row[name] for name in from_query}
        record["status"] = row["status"] or "not set"
        record["applied_on"] = _first_applied(entries)
        # "2026-08-01 applied; 2026-08-05 interviewed (left a voicemail)" - the
        # sequence survives in one cell, readable without this app or any tool
        # but a spreadsheet.
        #
        # THE NOTE TRAVELS WITH THE TRANSITION IT BELONGS TO. This column used
        # to select key/status/at only, so every word a person had written
        # about their own applications was silently absent from the one file
        # that exists to outlive the app. Quoting is the csv module's job -
        # notes contain commas, quote marks and newlines, and every one of them
        # survives a round trip through DictWriter.
        record["history"] = "; ".join(
            f"{e.at} {e.status}".strip() + (f" ({e.note})" if e.note else "")
            for e in entries)
        record["notes"] = "; ".join(
            f"{at} {note}".strip() for at, note in notes.get(row["key"], []))
        record["pay_offered"], record["offer_date"] = _offer_terms(entries)
        out.append({name: record.get(name) or "" for name in COLUMNS})
    return out


def write_csv(con: sqlite3.Connection, path: Path) -> int:
    """Write the whole pipeline to `path`. Returns how many jobs were written.

    newline="" per the csv module's contract, and utf-8-sig so Excel opens
    accented company names correctly instead of showing mojibake - the failure
    would land on exactly the person least equipped to diagnose it.
    """
    records = rows(con)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(COLUMNS))
        writer.writeheader()
        writer.writerows(records)
    return len(records)
