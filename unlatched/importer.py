"""importer.py - take rows another collector already gathered, and fetch nothing.

For the case where a separate app has ALREADY read the postings: it has the
title, the employer, the description and the apply link, and going back out to
re-read them would be pure waste - and, for a site read only with a person
present, a second automated reader of pages that were already read once.

THIS MODULE MAKES NO REQUESTS. Not "usually none", not "none unless a field is
missing" - none. A field the caller did not supply stays empty, because the
alternative is a bulk import quietly turning into a crawl.

Everything else about an imported row is ordinary: it is screened by the same
code as a collected one, takes a status, is exported, retires and restores, and
takes part in dedupe. The only difference is who did the reading.
"""
from __future__ import annotations

import csv
import io
import json
import re
from datetime import datetime
from typing import TYPE_CHECKING, Any

from . import db, screen
from . import links as links_mod
from . import status as status_mod

if TYPE_CHECKING:
    import sqlite3
    from pathlib import Path

# The collector id used when nobody names one: the single unnamed handoff this
# module was built for. A configured collector supplies its own id, which
# becomes both jobs.source and the row's key namespace.
SOURCE_NAME = "imported"

# Which revision of the published handoff contract this app reads. See
# COLLECTORS.md. Declared by JSON senders as `version`; CSV has nowhere to put
# it and is version 1 by definition.
CONTRACT_VERSION = 1

# What a collector may call itself. It becomes a key prefix and a jobs.source
# value, so it has to be a plain token - not a path, not a name with a colon in
# it that would fake a second namespace.
_COLLECTOR_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")


class BadCollectorIdError(ValueError):
    """A collector id that cannot safely be a namespace."""


def check_collector_id(collector: str) -> str:
    """The id, or an error naming what is wrong with it.

    CHECKED RATHER THAN TRUSTED, because this string arrives from config a
    third party may have written and then becomes the prefix that separates
    their rows from everybody else's. A collector that could put a colon in its
    own id could claim another collector's namespace.

    CASE IS NORMALISED, NOT REFUSED. The config is hand-written and "MyBoard"
    is what somebody types; refusing it would be pedantry. Everything
    downstream uses the value this returns rather than the one it was handed,
    so the id that reaches jobs.source is the normalised one either way.
    """
    cleaned = str(collector or "").strip().lower()
    if not _COLLECTOR_ID.match(cleaned):
        msg = (f"collector id {collector!r} is not usable: it has to be 1-32 "
               "characters of a-z, 0-9, underscore or hyphen, starting with a "
               "letter or digit")
        raise BadCollectorIdError(msg)
    return cleaned

# What a row may carry. Anything else in the file is IGNORED rather than
# rejected: the collector on the other side has its own richer schema (rank,
# fit_score, drop_reason, clearance, travel...) and none of it is load-bearing
# here, so an import must not fail because the sender knows more than we store.
FIELDS = ("url", "title", "company", "location", "description",
          "posted", "apply_url", "key", "status", "applied_at", "apply_kind")

# What apply_kind may say. Anything else is stored as unknown rather than
# rejected - a sender inventing a fourth state should not fail an import, but it
# must not be silently believed either.
APPLY_KINDS = ("easy-apply", "external")

# Markers that must never arrive AS a destination. The handoff contract says
# 'easy-apply' and 'closed' are classifications, not URLs, and one appearing
# in the apply_url field is a defect worth reporting. That audit needs a check,
# not an intention - so this is it.
NOT_DESTINATIONS = ("easy-apply", "easy_apply", "easyapply", "closed", "none",
                    "n/a", "-")


class ImportRowError(ValueError):
    """A row that cannot become a job, named so the caller can say which."""


def marker_in_destination(row: dict[str, Any]) -> str:
    """The marker a row wrongly put in apply_url, or "" if it is clean.

    Reported specifically rather than trusted, because the failure is silent in
    the worst way: a literal 'easy-apply' sitting in the destination column is a
    NON-EMPTY value, so it would pass every "does this row have a destination"
    test - and then every easy-apply row in the batch would carry the SAME
    string and dedupe would fold them all into one job.
    """
    value = str(row.get("apply_url") or "").strip().lower()
    return value if value in NOT_DESTINATIONS else ""


def _apply_kind(row: dict[str, Any]) -> str:
    """How this job is applied to, as the sender classified it.

    Empty means UNKNOWN, and unknown is deliberately distinct from
    'easy-apply'. An empty apply_url used to mean both "no external route
    exists" and "we did not manage to capture one", which are opposite facts -
    and the second one is a defect that looked exactly like the first.

    Inferred rather than demanded when the sender says nothing: a row carrying a
    real destination is external by observation, whatever it did or did not
    declare. Nothing is inferred in the other direction - an empty destination
    says nothing about which of the two reasons applies, which is the entire
    point of this field.
    """
    stated = str(row.get("apply_kind") or "").strip().lower()
    if stated in APPLY_KINDS:
        return stated
    if str(row.get("apply_url") or "").strip():
        return "external"
    return ""


# Prefixes a collector's OWN rows may already carry, per collector id.
#
# ONE ENTRY, AND IT IS HISTORY RATHER THAN DESIGN. The unnamed handoff wrote
# `manual:` keys for years, because those rows first arrived as hand-adds.
# rekey.py corrected the stored rows to `imported:` on 2026-08-13, but the
# program on the other side still writes `manual:` - in its rows and in its
# closures - and this app does not control when that changes. Without this, its
# next handoff would have created a second copy of all 410 rows and every
# closure would have matched nothing.
LEGACY_PREFIXES: dict[str, frozenset[str]] = {
    SOURCE_NAME: frozenset({"manual"}),
}


def namespaced_key(collector: str, given: str) -> str:
    """`given` placed inside `collector`'s namespace.

    THE SENDER'S ID IS KEPT WHOLE. Only a prefix that is already THIS
    collector's - its id, or a legacy spelling of it - is replaced; anything
    else is kept as part of the id. A sender whose own ids look like
    "li:998877" and "in:998877" means two different postings by them, and
    stripping at the first colon would fold those two into one row.

    A PREFIX IS NEVER TRUSTED TO NAME A NAMESPACE. A file arriving as the
    myboard collector carrying "othertool:123" does not get to write into
    othertool's rows; it becomes "myboard:othertool:123", which is
    unambiguous and still cannot collide with anything of othertool's.
    """
    prefix, _, rest = given.partition(":")
    if rest and (prefix == collector or prefix in LEGACY_PREFIXES.get(collector, ())):
        return f"{collector}:{rest}"
    return f"{collector}:{given}"


def _key_for(row: dict[str, Any], collector: str = SOURCE_NAME) -> str:
    """A stable id for the row, inside its own collector's namespace.

    THE COLLISION THIS PREVENTS. Two collectors both reading the same posting
    derive the same stable id from the same URL, so without a namespace
    the second silently OVERWRITES the first's row instead of being recognised
    as a separate report of the same job. A prefix the sender supplies is not
    enough either: a collector that can name its own namespace can claim
    somebody else's.

    So for a NAMED collector the prefix is imposed here, and any prefix the
    file supplied is replaced.

    NO EXCEPTION FOR THE UNNAMED DEFAULT, NOT ANY MORE. It had one until
    2026-08-13, because 410 live rows were keyed `manual:` while
    their source said `imported`, and honouring the file's prefix was what kept
    those rows matching. rekey.py has since corrected them. Correctness was
    to win over migration cost, so the exception now does the opposite of
    what it was for: a sender still writing `manual:` keys would create a
    second copy of every row beside the corrected one.

    That is why this normalises rather than trusting: the SENDER'S PREFIX IS
    NOT THE SENDER'S TO CHOOSE, whichever collector it is.
    """
    given = str(row.get("key") or "").strip()
    if not given:
        url = str(row.get("url") or "").strip()
        if not url:
            msg = "each row needs a url or a key"
            raise ImportRowError(msg)
        from .manual import stable_id
        given = stable_id(url)
    return namespaced_key(collector, given)


def import_row(con: sqlite3.Connection, cfg: dict[str, Any], row: dict[str, Any],
               *, resume_text: str = "", collector: str = SOURCE_NAME,
               ) -> dict[str, Any]:
    """Store one already-read posting. Fetches nothing.

    `collector` is which configured collector this row came from. It becomes
    the row's jobs.source AND its key namespace, so two collectors reporting
    the same posting stay two rows with their own provenance instead of one
    overwriting the other.
    """
    collector = check_collector_id(collector)
    title = str(row.get("title") or "").strip()
    if not title:
        msg = "each row needs a title"
        raise ImportRowError(msg)

    key = _key_for(row, collector)
    url = links_mod.safe_or_empty(str(row.get("url") or "").strip())
    company = str(row.get("company") or "").strip()
    description = str(row.get("description") or "")
    location = str(row.get("location") or "").strip()

    # IMPORTED, not discovered: somebody else's collector found this employer,
    # and "refresh the seeded companies" must not sweep it up - we have no
    # board of our own for it to refresh.
    company_id = db.upsert_company(
        con, company or "Imported", probe_status="imported",
        origin=db.IMPORTED)

    posting = type("Posting", (), {
        "title": title, "location": location, "description": description,
        "employment_type": "",
    })()
    fields = screen.screen_job(posting, cfg, resume_text)

    now = status_mod.now_iso()
    fields.update({
        "company_id": company_id,
        # THE COLLECTOR, not the constant "imported". Every imported row from
        # every collector used to collapse to one label, so a second
        # collector's rows would have been indistinguishable from the first's -
        # and the per-source rules below them (what may be re-fetched, what
        # reports its own closures) had nothing to key on.
        "source": collector,
        "title": title,
        "location": location,
        "url": url,
        "posted_at": str(row.get("posted") or "").strip(),
        "apply_url": links_mod.normalise_apply_url(row.get("apply_url")),
        "apply_kind": _apply_kind(row),
        "fetched_at": now,
        "last_seen": now,
        "description": description,
        # Somebody else's collector already decided this row was worth sending,
        # and a person asked for the import. Same reasoning as a hand-added
        # job: it is never dropped for failing the title filter or the salary
        # floor. The reasons are still recorded and still shown.
        "qualified": 1,
        "verdict": "keep" if fields.get("verdict") == "keep" else "alt",
    })
    db.upsert_job(con, key, fields)
    db.relist(con, key)

    # THE STATUS, which is the whole reason application history is worth
    # importing at all. Without it the app holds the posting and does not know
    # the person approached that employer - so it cannot stop them applying
    # twice, which is the one thing the record exists to prevent.
    #
    # Only ever SET, never cleared: an import that arrives with no status must
    # not wipe a decision the person made in the app. The sending collector
    # knows what it gathered; it does not know what happened here since.
    recorded = str(row.get("status") or "").strip().lower()
    if recorded:
        existing = con.execute(
            "SELECT status FROM job_status WHERE key = ?", (key,)).fetchone()
        if existing is None:
            status_mod.set_status(con, key, recorded,
                                   at=str(row.get("applied_at") or "").strip() or None)

    return {"key": key, "title": title, "company": company,
            "apply_url": fields["apply_url"], "status": recorded or None}


# The most a handoff may be, in bytes.
#
# A CEILING RATHER THAN TRUST. This is a published interface, so the file is
# whatever somebody else's program wrote - and json.loads and csv both read the
# whole thing into memory before anything here can object. The largest real
# handoff so far is 190 KB (2026-08-12, 37 jobs and 62 closures with full
# descriptions); 64 MB is roughly three hundred times that, which is room for a
# collector far larger than any we have and still short of exhausting anything.
MAX_HANDOFF_BYTES = 64 * 1024 * 1024


class HandoffTooLargeError(ImportRowError):
    """A handoff bigger than this app will read into memory."""


# The columns a template hands somebody, in the order they make sense to fill.
# NOT the same as FIELDS: `key` is deliberately last because most collectors
# should not set it, and `closed` and `generated_at` are CSV-only.
TEMPLATE_COLUMNS = ("url", "title", "company", "location", "posted",
                    "apply_url", "apply_kind", "description", "status",
                    "applied_at", "closed", "key", "generated_at")


def template_csv(now: str) -> str:
    """A spreadsheet somebody can open, fill in and hand back.

    THE ANSWER TO "WHAT DO I WRITE". A published format that exists only as
    prose is one every author implements slightly differently; a file they can
    open in Excel is one they get right by typing into it. The example row is
    real enough to import and obvious enough not to be mistaken for data.
    """
    example = {
        "url": "https://boards.greenhouse.io/example/jobs/1",
        "title": "Support Analyst",
        "company": "Example Employer",
        "location": "Remote, US",
        "posted": "2026-08-13",
        "apply_url": "https://boards.greenhouse.io/example/jobs/1/apply",
        "apply_kind": "external",
        "description": "Paste the whole posting here.\nParagraphs are fine.",
        "status": "",
        "applied_at": "",
        "closed": "FALSE",
        "key": "",
        "generated_at": now,
    }
    out = io.StringIO()
    # \n rather than the default \r\n: this is written through Python's text
    # layer, which turns \n into the platform ending on its own, and leaving
    # csv's own \r\n in place would produce \r\r\n on Windows.
    writer = csv.DictWriter(out, fieldnames=list(TEMPLATE_COLUMNS),
                            lineterminator="\n")
    writer.writeheader()
    writer.writerow(example)
    return out.getvalue()


def check_rows(path: Path) -> dict[str, Any]:
    """Read a handoff and report what is wrong with it, PER ROW.

    A DRY RUN, WRITING NOTHING. Somebody writing a collector needs to know
    their file is right before it is pointed at a real board, and the answer
    "0 imported" is not a diagnosis. Every problem here names the row number
    the way a spreadsheet does - the header is row 1, so the first job is row 2
    and the number in the message is the one on screen.
    """
    text = _text_of(path)
    fmt = "json" if looks_like_json(text) else "csv"
    closed = read_closures(path)
    stamp = read_generated_at(path)
    problems: list[dict[str, Any]] = []

    # NUMBERED AGAINST THE FILE, not against the jobs. In a CSV the closures
    # are rows too, and numbering the jobs alone would point somebody at the
    # wrong line of their own spreadsheet the moment one appeared above.
    if fmt == "csv":
        numbered = [(i, r) for i, r in enumerate(_csv_rows(text))
                    if not _is_true(r.get("closed"))]
    else:
        numbered = list(enumerate(read_rows(path)))
    rows = [r for _, r in numbered]

    def note(index: int, message: str) -> None:
        # +2 for CSV (the header is row 1); +1 for JSON, where a reader counts
        # elements from one.
        problems.append({"row": index + (2 if fmt == "csv" else 1),
                         "problem": message})

    for index, row in numbered:
        if not str(row.get("title") or "").strip():
            note(index, "no title, so this row cannot become a job")
        if not str(row.get("url") or "").strip() and not str(row.get("key") or "").strip():
            note(index, "no url and no key, so there is nothing to identify it by")
        marker = marker_in_destination(row)
        if marker:
            note(index, f"apply_url says {marker!r}, which is a classification "
                        "rather than a destination - use apply_kind for that")
        stated = str(row.get("apply_kind") or "").strip().lower()
        if stated and stated not in APPLY_KINDS:
            note(index, f"apply_kind {stated!r} is not one of "
                        f"{', '.join(APPLY_KINDS)} - it will read as unknown")
        unknown = sorted(set(row) - set(FIELDS) - {"closed", "generated_at"})
        if unknown:
            # NOT A PROBLEM, and said so in the wording: extra columns are
            # ignored by design. It is reported because a MISSPELLED one looks
            # exactly like an extra one, and that is the mistake this catches.
            note(index, "ignored column(s): " + ", ".join(unknown))

    if fmt == "json" and read_version(path) != CONTRACT_VERSION:
        # REPORTED, NOT REFUSED. A version this app does not know is a reason
        # to say something, not a reason to drop somebody's whole handoff - and
        # a file that omits it parses identically to one that declares it, so
        # without this the field would be advisory in name only.
        problems.append({"row": 0, "problem":
                         f"the file does not declare version {CONTRACT_VERSION}"
                         " - it will still be read, but a contract without a "
                         "version cannot change"})

    return {"format": fmt, "path": str(path), "jobs": len(rows),
            "closed": len(closed), "generated_at": stamp,
            "version": read_version(path), "problems": problems}


def read_version(path: Path) -> int | None:
    """Which revision of the contract the sender claims to meet.

    None for a CSV, which has nowhere to put it, and for a JSON file that does
    not say. Only `check` acts on this: reading is deliberately permissive, so
    the version is what lets the format CHANGE later rather than what gates it
    today.
    """
    try:
        text = _text_of(path)
    except (OSError, ValueError):
        return None
    if not looks_like_json(text):
        return None
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    try:
        return int(data["version"])
    except (KeyError, TypeError, ValueError):
        return None


def _text_of(path: Path) -> str:
    """The file, refused rather than read if it is absurd."""
    size = path.stat().st_size
    if size > MAX_HANDOFF_BYTES:
        msg = (f"{path.name} is {size / 1048576:.0f} MB, over the "
               f"{MAX_HANDOFF_BYTES // 1048576} MB limit for a handoff file")
        raise HandoffTooLargeError(msg)
    # utf-8-sig, not utf-8. Excel writes a byte-order mark on a CSV and it
    # lands on the FIRST HEADER NAME, so `url` arrives as `﻿url` and the
    # column silently is not there - which for a spreadsheet-first format is
    # the single likeliest way a correct file reads as empty.
    return path.read_text(encoding="utf-8-sig")


def looks_like_json(text: str) -> bool:
    """Whether to read this as JSON rather than CSV.

    THE CONTENT DECIDES, NOT THE EXTENSION. CSV is the documented default
    (Decided 2026-08-12: ordinary people do not know what JSON is), and somebody
    exporting from a spreadsheet may well end up with .txt or no suffix at all.
    A file that starts with a brace or a bracket is JSON in every case that
    matters, and a CSV can never start with either - a header name beginning
    with `{` would not be a column any collector could name.
    """
    return text.lstrip()[:1] in ("{", "[")


def _csv_rows(text: str) -> list[dict[str, Any]]:
    """Rows from a spreadsheet export.

    HEADER NAMES ARE NORMALISED - lowercased, trimmed, spaces and hyphens to
    underscores - so "Apply URL" and "apply_url" are the same column. The
    person producing this is typing into Excel, and refusing their file over
    a capital letter would be the format's first act.

    UNKNOWN COLUMNS ARE IGNORED, the same as unknown JSON fields: the sender's
    own schema is richer than ours and an import must not fail because they
    know more than we store.
    """
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None:
        return []
    names = [_column(n) for n in reader.fieldnames]
    rows: list[dict[str, Any]] = []
    for raw in reader:
        row: dict[str, Any] = {}
        for name, value in zip(names, raw.values(), strict=False):
            # Blank cells are dropped rather than stored as "": a spreadsheet
            # produces a value for every column of every row, and an empty
            # string in `key` would otherwise read as a key the sender chose.
            if name and value is not None and str(value).strip():
                row[name] = str(value)
        if row:
            rows.append(row)
    return rows


def _column(name: str) -> str:
    return str(name or "").strip().lower().replace(" ", "_").replace("-", "_")


def _is_true(value: Any) -> bool:
    """Whether a spreadsheet cell means yes.

    Excel writes TRUE; a person writes yes, y, or 1; a script writes true.
    Anything else - including the empty cell every open row has - is no.
    """
    return str(value or "").strip().lower() in ("true", "yes", "y", "1")


def read_rows(path: Path) -> list[dict[str, Any]]:
    """The jobs in a handoff, from CSV or from JSON.

    CSV IS THE DOCUMENTED DEFAULT (decided 2026-08-12). The earlier reasoning here
    was that "a job description is multi-paragraph text with quotes and newlines
    in it, and CSV is where that goes wrong quietly" - which is true of
    HAND-ROLLED writers and not of Excel or Python's csv module, both of which
    quote embedded newlines correctly. The format somebody cannot read is the
    format they cannot produce, and this app already EXPORTS csv, so accepting
    it inbound also gives round-trip symmetry.

    JSON stays for collector authors: a list, or {"jobs": [...]}.

    A ROW MARKED CLOSED IS NOT A JOB and is left to read_closures. Importing it
    would put a posting known to be dead on the board as a live row.
    """
    text = _text_of(path)
    if not looks_like_json(text):
        return [r for r in _csv_rows(text) if not _is_true(r.get("closed"))]
    data = json.loads(text)
    if isinstance(data, dict):
        data = data.get("jobs") or data.get("rows") or []
    if not isinstance(data, list):
        msg = "expected a list of jobs, or an object with a 'jobs' list"
        raise ImportRowError(msg)
    return [r for r in data if isinstance(r, dict)]


def read_generated_at(path: Path) -> str:
    """When the sender says it wrote this file, or "" if it does not say.

    THE FAILURE THIS EXISTS FOR: if the sending collector dies, its file stops
    changing and still parses perfectly. Nothing in the content would reveal
    that, and every mechanical check keeps passing while the data silently ages.
    A run that succeeds while measuring something stale is the failure mode that
    has cost us the most, so the sender stamps the file and this reads it.

    Returns a string rather than a datetime because parsing belongs with the
    caller that decides what "too old" means. A sender that omits the field is
    not an error - it just cannot be checked for staleness.
    """
    try:
        text = _text_of(path)
    except (OSError, ValueError):
        return ""
    if not looks_like_json(text):
        # A COLUMN, because CSV has nowhere else to put it. Read off the first
        # row that carries one, so a spreadsheet repeating the value down the
        # column and a script filling only the first row both land here.
        try:
            rows = _csv_rows(text)
        except (csv.Error, ValueError):
            return ""
        return next((str(r["generated_at"]) for r in rows
                     if str(r.get("generated_at") or "").strip()), "")
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return ""
    if not isinstance(data, dict):
        return ""
    return str(data.get("generated_at") or "")


def handoff_age_hours(generated_at: str, now: datetime) -> float | None:
    """Hours since the sender wrote the file, or None if that is unknowable.

    None for a missing or unparseable stamp, and the caller must not treat that
    as fresh - "cannot tell" and "recent" are different answers, and collapsing
    them is how a dead collector reads as a healthy one.
    """
    if not generated_at:
        return None
    try:
        stamped = datetime.fromisoformat(generated_at)
    except (TypeError, ValueError):
        return None
    if stamped.tzinfo is None:
        stamped = stamped.replace(tzinfo=now.tzinfo)
    return (now - stamped).total_seconds() / 3600.0


def read_closures(path: Path) -> list[str]:
    """Keys the sender says are no longer live, from {"closed": [...]}.

    SEPARATE FROM read_rows, AND THE REASON IS THE SILENT DROP. read_rows takes
    `jobs` and ignores every other key in the object, so a sender bundling
    closures into the same file would have had them accepted with a clean exit
    and no effect at all - 62 of them, on the first real handoff (2026-08-12).
    A closure that vanishes is worse than one that errors: the row stays on the
    board looking live, and nothing anywhere says otherwise.

    Keys only, not objects. Closure is a fact about a posting and the identity
    is the entire payload for it.

    IN CSV THEY ARE A COLUMN, in the same file as the jobs: `closed` set to
    TRUE. Arguably tidier than JSON's separate list, and it is what a person
    editing a spreadsheet would reach for. Such a row is a closure and NOT a
    job - read_rows leaves it alone.
    """
    text = _text_of(path)
    if not looks_like_json(text):
        closed = []
        for row in _csv_rows(text):
            if not _is_true(row.get("closed")):
                continue
            # The key the row would have had if it were being imported, minus
            # the namespace - the caller applies that, the same as for JSON.
            given = str(row.get("key") or "").strip()
            if not given:
                from .manual import stable_id
                given = stable_id(str(row.get("url") or "").strip())
            if given:
                closed.append(given)
        return closed
    data = json.loads(text)
    if not isinstance(data, dict):
        return []
    raw = data.get("closed") or data.get("closures") or []
    if not isinstance(raw, list):
        msg = "'closed' must be a list of job keys"
        raise ImportRowError(msg)
    return [k for k in raw if isinstance(k, str) and k.strip()]


def import_all(con: sqlite3.Connection, cfg: dict[str, Any],
               rows: list[dict[str, Any]], *, resume_text: str = "",
               collector: str = SOURCE_NAME) -> dict[str, Any]:
    """Import every row, reporting what failed rather than stopping.

    One malformed row in a run of hundreds should not cost the other hundreds -
    the sender is another program, and a partial import that names its failures
    is recoverable where an all-or-nothing one is a stand-off.
    """
    added, failed, markers, unknown = [], [], [], 0
    for index, row in enumerate(rows):
        marker = marker_in_destination(row)
        if marker:
            # REPORTED, not rejected. The row is still a real job and the
            # sender still wants it; what is wrong is one field, and dropping
            # the posting would punish the person for a bug between two
            # programs. The destination is dropped instead, so the bad value
            # cannot become a dedupe key that folds every easy-apply row in the
            # batch into a single job.
            markers.append({"row": index, "value": marker,
                            "title": str(row.get("title") or "")[:60]})
        # Rebound rather than mutated: a marker row is imported with the bad
        # destination DROPPED, so it can never become a dedupe key, while the
        # caller's own dict is left untouched.
        usable = (dict(row, apply_url="",
                       apply_kind=row.get("apply_kind") or "easy-apply")
                  if marker else row)
        try:
            added.append(import_row(con, cfg, usable, resume_text=resume_text,
                                     collector=collector))
        except (ImportRowError, ValueError) as e:
            failed.append({"row": index, "error": str(e),
                           "title": str(row.get("title") or "")[:60]})
            continue
        if not (usable.get("apply_url") or "").strip() and not _apply_kind(usable):
            unknown += 1
    con.commit()
    result = {"imported": len(added), "failed": failed, "jobs": added}
    # Both of these are the sender's to fix, so they are REPORTED rather than
    # buried: a row with neither a destination nor a classification is exactly
    # the defect this check exists for, and it was previously indistinguishable
    # from an ordinary easy-apply row.
    if markers:
        result["markers_in_apply_url"] = markers
    if unknown:
        result["no_destination_and_unclassified"] = unknown
    return result
