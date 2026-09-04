"""db.py - SQLite open/migrate and the upsert helpers every other module uses.

Two axes live in this database and they are kept in different tables on
purpose: `jobs` is what the machine decided (screening output), `job_status`
and `job_status_log` are what the person decided (their own pipeline).
A re-screen may rewrite `jobs` freely; it must never touch the other two.
The rule exists because a rescore pass once did exactly that, and overwrote
completed work.
"""
from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from . import links, paths, rekey, reposts

# Aliased because `status` is a local variable name in several functions here,
# and a module shadowed by a parameter fails at the call rather than at import.
from . import status as status_vocab

if TYPE_CHECKING:
    import os

SCHEMA = """
CREATE TABLE IF NOT EXISTS companies (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  domain TEXT, careers_url TEXT,
  ats TEXT, ats_ref TEXT,
  probe_status TEXT DEFAULT 'new',
  last_probed TEXT
);
CREATE TABLE IF NOT EXISTS jobs (
  key TEXT PRIMARY KEY,
  company_id INTEGER REFERENCES companies(id),
  -- Which collector produced this row. It is recoverable from the key (the
  -- half before the colon), but a column is what lets the app group, filter
  -- and show provenance without every reader re-parsing a composite string.
  source TEXT,
  title TEXT NOT NULL, location TEXT,
  remote TEXT, remote_evidence TEXT,
  salary_min INTEGER, salary_max INTEGER, currency TEXT,
  -- The rate as the employer actually wrote it. salary_min/max are always
  -- ANNUALISED so a mixed corpus can be compared against one floor, but that
  -- multiplies by 2080 hours - our assumption, not the employer's. Showing a
  -- derived 56,160 for a posting that said "$27.00/hr" hides what it said.
  hourly_rate REAL,
  -- url is the posting's own page: the thing a person clicks to apply.
  url TEXT, posted_at TEXT, fetched_at TEXT,
  -- The last collect run that still found this posting on its board. A job
  -- pulled from the board stops being updated, so last_seen falling behind
  -- the company's latest successful collect is how a delisted job is
  -- recognised. Never deleted: the person may have applied to it, and their
  -- own pipeline record has to outlive the listing.
  last_seen TEXT,
  -- When this posting was first found MISSING from a successful collect of
  -- its board. NULL means still listed.
  delisted_at TEXT,
  -- When the PERSON removed this row from their lists. Distinct from
  -- delisted_at, which is when the EMPLOYER took the posting down: one is a
  -- fact about the world, the other is a decision by the reader, and
  -- collapsing them would let a collect resurrect something somebody threw
  -- away. Retiring hides, it does not delete: the row keeps its status, its
  -- history and its place in the repost record, and can be put back.
  retired_at TEXT,
  description TEXT,
  employment_type TEXT,
  -- Identity of the SEAT this posting advertises (company+title+place),
  -- distinct from the posting id. Employers mint a new id when they
  -- re-advertise, so the key cannot tell a repost from a new job; the seat
  -- can. See reposts.py.
  seat TEXT,
  -- The seat's advertising history in one sentence, written by
  -- reposts.annotate after a collect or re-screen. Stored so the desktop
  -- renders a string instead of re-deriving date arithmetic the engine
  -- already owns.
  repost_note TEXT,
  -- The advertisement this one is a NEW ENTRY after: same seat, more than four
  -- weeks later (reposts.NEW_ENTRY_DAYS). Decided 2026-08-12: a seat
  -- advertised again after more than four weeks is a new entry, linked back to
  -- the round it originated from.
  --
  -- A LINK, NOT A GROUPING. duplicate_of hides a row behind another; this
  -- points at one while both stay visible, because a seat advertised again
  -- after a month is a real opening somebody can apply to and the earlier
  -- round is context rather than a replacement.
  repost_of TEXT,
  -- Screening output that answers "what would I have to add to my resume to
  -- be a clean fit for THIS posting". Stored rather than computed on demand
  -- because the alternative is one subprocess per visible row.
  coverage_pct REAL, missing_skills TEXT,
  -- What the posting demands, compressed to a few words ("5+ yrs, BS, CDL").
  -- Lets a reader rule a row out without opening it.
  requirements_summary TEXT,
  score REAL, screen_reasons TEXT,
  -- keep | alt | drop. `qualified` stays as the coarse did-it-match flag;
  -- verdict carries WHY, so an alt row is visible without inflating the count.
  verdict TEXT,
  -- WHICH KIND of alt: 'salary' or 'requirements' (empty when unknown).
  -- The verdict says a row fell short; it never said what of, and four
  -- unrelated conditions produce it - pay under the floor, an employment
  -- type the search did not ask for, a description too thin to judge, and a
  -- requirement the profile rules out. Those are two different questions for
  -- a reader ("they do not pay enough" against "this is not my job"), and
  -- separating them was impossible while the only record of the reason was
  -- the English in screen_reasons.
  --
  -- Empty is a real value, not a gap: a row forced to alt without being
  -- screened (a hand-added job, an import) has no reason to record, and the
  -- requirements card is written to include it so no row is unreachable.
  alt_reason TEXT,
  qualified INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS job_status (
  key TEXT PRIMARY KEY,
  status TEXT NOT NULL,
  note TEXT, updated TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS job_status_log (
  id INTEGER PRIMARY KEY, key TEXT, status TEXT, note TEXT, at TEXT,
  -- What was offered, and when. Structured rather than folded into the note
  -- because these are the two facts a person is asked for months later and
  -- the two least likely to survive in memory. Only an Offer row carries them.
  pay TEXT, offer_date TEXT
);
-- Notes that are not about a status change.
--
-- A SEPARATE TABLE, not a job_status_log row with a null status: a null status
-- in the log already means "the status was cleared", which import_status acts
-- on by deleting the job_status row. A note-only entry there would import as
-- an erased status - the note would arrive having deleted what it was written
-- about.
CREATE TABLE IF NOT EXISTS job_note (
  id INTEGER PRIMARY KEY, key TEXT NOT NULL, note TEXT NOT NULL, at TEXT NOT NULL
);
-- Files and links kept beside a job: a resume sent, an offer letter, the
-- confirmation screen, the recruiter's scheduling link.
--
-- THE BYTES ARE NOT IN HERE. Only metadata. Any local agent can read this
-- database - that is a documented feature - so the one thing that must not
-- live in it is content a stranger wrote. Files sit under
-- <home>/attachments/<trust>/ instead, and `trust` says which side of the
-- conversation wrote them: 'mine' (the person's own, offered to an assistant
-- freely) or 'posting' (the employer's, never offered).
--
-- THEY BELONG TO THE JOB, not to an application event (decided 2026-08-12).
-- Re-applying months later with a different resume therefore adds a second
-- attachment to the same job rather than starting a fresh set, and both are
-- kept with their own dates.
CREATE TABLE IF NOT EXISTS attachment (
  id INTEGER PRIMARY KEY,
  key TEXT NOT NULL,
  trust TEXT NOT NULL,
  -- image | text | pdf | other | link. Decided from the extension at attach
  -- time, so nothing has to open a file to find out how to show it.
  kind TEXT NOT NULL,
  -- The generated name the bytes are written under. NULL for a link.
  stored_name TEXT,
  -- The original name, for display only.
  display_name TEXT NOT NULL,
  url TEXT,
  bytes INTEGER,
  added_at TEXT NOT NULL
);
-- Every move between trust classes. The question asked later is never "what
-- class is this" but "why was this one readable", and only a history answers.
CREATE TABLE IF NOT EXISTS attachment_trust_log (
  id INTEGER PRIMARY KEY,
  attachment_id INTEGER NOT NULL,
  was TEXT, now TEXT, at TEXT NOT NULL
);
-- Small key/value store for facts about the database itself rather than the
-- jobs in it - currently only which seat-keying rule the rows were written
-- with, so a change to that rule can recompute them instead of silently
-- leaving stale keys behind.
CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT
);
CREATE INDEX IF NOT EXISTS ix_jobs_company ON jobs(company_id);
CREATE INDEX IF NOT EXISTS ix_jobs_qualified ON jobs(qualified);
CREATE INDEX IF NOT EXISTS ix_status_log_key ON job_status_log(key);
CREATE INDEX IF NOT EXISTS ix_job_note_key ON job_note(key);
CREATE INDEX IF NOT EXISTS ix_attachment_key ON attachment(key);
"""

# Indexes over columns that ADDED_JOB_COLUMNS may still be adding. They
# cannot live in SCHEMA: executescript runs before the migration, so on a
# database predating the column, CREATE INDEX ... ON jobs(seat) raises before
# ALTER TABLE has had a chance to create it.
# Splitting the alt pile is a DATA migration, and it is mirrored in the
# desktop's db.rs - see ALT_REASON_BACKFILL there.
#
# WHY A STORED VERSION AND NOT "did we just add the column". That was the
# first attempt and it could not work: both halves create the jobs table, so
# whichever one migrates SECOND finds the column already present and skips the
# backfill - and for somebody who installs an update and opens the app, the
# desktop always goes first. Observed on 2026-09-02 on a real profile: 563 alt
# rows, 83 of them describing a fallback floor, and every one of them left
# unlabelled with the salary card reading 0. A column migration can be guarded
# by the column; the DATA migration beside it cannot.
#
# The pay case is the only one screen.py describes as a "fallback floor", so
# this recovers the split exactly rather than guessing. Everything else keeps
# the empty value, which the requirements card is written to include.
ALT_REASON_VERSION = 1
ALT_REASON_BACKFILL = (
    "UPDATE jobs SET alt_reason = 'salary' "
    "WHERE verdict = 'alt' AND screen_reasons LIKE '%fallback floor%'")

POST_MIGRATION_INDEXES = (
    "CREATE INDEX IF NOT EXISTS ix_jobs_seat ON jobs(seat)",
)

JOB_COLUMNS = (
    "key", "company_id", "source", "title", "location", "remote", "remote_evidence",
    "salary_min", "salary_max", "currency", "hourly_rate", "url", "posted_at",
    "fetched_at",
    "description", "employment_type", "last_seen", "delisted_at", "score",
    "screen_reasons",
    "seat", "repost_note", "repost_of", "coverage_pct", "missing_skills",
    "requirements_summary",
    "verdict",
    "alt_reason",
    "qualified",
    "retired_at",
    "apply_url",
    "apply_kind",
    "duplicate_of",
    "duplicate_reason",
)

# Columns added after the first release. CREATE TABLE IF NOT EXISTS does
# nothing to a table that already exists, so an installed database keeps its
# original shape forever unless it is migrated - which is how a new column
# silently reads back as "missing" on every profile that predates it.
ADDED_JOB_COLUMNS = (("source", "TEXT"), ("employment_type", "TEXT"),
                     ("verdict", "TEXT"), ("hourly_rate", "REAL"),
                     ("last_seen", "TEXT"), ("delisted_at", "TEXT"),
                     ("seat", "TEXT"), ("repost_note", "TEXT"),
                     ("repost_of", "TEXT"),
                     ("coverage_pct", "REAL"), ("missing_skills", "TEXT"),
                     ("requirements_summary", "TEXT"),
                     ("retired_at", "TEXT"),
                     # Where the Apply button actually goes. A posting on one
                     # board is often a shopfront for an application hosted on
                     # another, so two rows collected from different places can
                     # be the same job - and this is the only field that says so
                     # exactly rather than by text similarity.
                     ("apply_url", "TEXT"),
                     # The posting this one duplicates, and why. A COLUMN
                     # rather than a deletion: the row keeps its status and its
                     # history and simply stops appearing beside its twin, so
                     # an over-fire is one UPDATE from being undone.
                     ("duplicate_of", "TEXT"),
                     ("duplicate_reason", "TEXT"),
                     # WHY an apply_url is empty, which is not the same question
                     # as whether it is empty.
                     #
                     # Empty meant two opposite things and nothing could tell
                     # them apart: "this job applies on the board itself, so no
                     # external destination EXISTS" and "we failed to capture
                     # one". A sending collector hit exactly this: a silent
                     # empty write looked identical to a job with no external
                     # route, and three runs of rows arrived unjoinable with
                     # nothing to say so.
                     #
                     # Collectors now classify at collection, and a row
                     # arriving with neither a destination NOR a
                     # classification has to be reportable. That was
                     # impossible here: there was nowhere for the
                     # classification to live, so the audit could not be run
                     # at all.
                     #
                     # Values: 'easy-apply' (applies on the board, no external
                     # URL will ever exist), 'external' (a real destination,
                     # which apply_url should carry), or empty (unknown - the
                     # state that now stands out instead of hiding).
                     ("apply_kind", "TEXT"),
                     # WHICH KIND of alt - see the CREATE TABLE comment.
                     ("alt_reason", "TEXT"))


# What an Offer records beyond a note, added to job_status_log after that
# table's first release. Mirrors ADDED_STATUS_LOG_COLUMNS in the desktop's
# db.rs - the schema is a shared contract, and a column only one side migrates
# is how the two halves drift apart.
ADDED_STATUS_LOG_COLUMNS = (("pay", "TEXT"), ("offer_date", "TEXT"))

# WHERE A COMPANY CAME FROM. Mirrors ADDED_COMPANY_COLUMNS in the desktop.
#
# It could not be answered before, and the question is not academic: a
# "refresh seeded companies" action cannot be expressed without it.
# starter.seed() and discover BOTH write probe_status="yielding", so a shipped
# employer and one the app found by crawling are indistinguishable -
# and probe_status is about whether a board ANSWERS, which is a different
# question that will keep changing underneath any attempt to infer origin
# from it.
#
# Values: 'seeded' (shipped with the app), 'discovered' (found by crawling or
# by a search source), 'manual' (typed in by the person), 'imported' (came
# from somebody else's collector). Empty means a row that predates the column -
# honestly unknown, rather than guessed.
#
# IMPORTED IS ITS OWN VALUE rather than a kind of discovered: we hold no board
# for those employers, so a "refresh the seeded companies" action must not
# sweep them up and find nothing to read.
ADDED_COMPANY_COLUMNS = (("origin", "TEXT"),)

SEEDED, DISCOVERED, MANUAL, IMPORTED = (
    "seeded", "discovered", "manual", "imported")

def _migrate_status_tables(con: sqlite3.Connection) -> None:
    """Bring job_status_log's columns and the status vocabulary up to date.

    BOTH TABLES GET THE RENAME. The log is what the funnel and the export read,
    so renaming only job_status would leave a person's history still saying
    "denied" while the row in front of them said "No Offer" - the same event
    under two names, in the one place the app promises to preserve exactly.
    """
    existing = {row[1] for row in con.execute("PRAGMA table_info(job_status_log)")}
    for name, decl in ADDED_STATUS_LOG_COLUMNS:
        if name not in existing:
            con.execute(f"ALTER TABLE job_status_log ADD COLUMN {name} {decl}")
    company_cols = {row[1] for row in con.execute("PRAGMA table_info(companies)")}
    for name, decl in ADDED_COMPANY_COLUMNS:
        if name not in company_cols:
            con.execute(f"ALTER TABLE companies ADD COLUMN {name} {decl}")
    for old, new in status_vocab.RENAMES:
        con.execute("UPDATE job_status SET status = ? WHERE status = ?", (new, old))
        con.execute("UPDATE job_status_log SET status = ? WHERE status = ?",
                    (new, old))


def _migrate_jobs(con: sqlite3.Connection) -> None:
    existing = {row[1] for row in con.execute("PRAGMA table_info(jobs)")}
    added = False
    for name, decl in ADDED_JOB_COLUMNS:
        if name not in existing:
            con.execute(f"ALTER TABLE jobs ADD COLUMN {name} {decl}")
            added = True
    stored_alt = int(get_meta(con, "alt_reason_version") or 0)
    if stored_alt < ALT_REASON_VERSION:
        con.execute(ALT_REASON_BACKFILL)
        set_meta(con, "alt_reason_version", str(ALT_REASON_VERSION))
    if added and "source" not in existing:
        # Backfill from the key rather than leaving history blank: every key
        # is "<source>:<id>", so the provenance of already-collected rows is
        # recoverable exactly, not guessed.
        con.execute(
            "UPDATE jobs SET source = substr(key, 1, instr(key, ':') - 1) "
            "WHERE source IS NULL AND instr(key, ':') > 0")
    for statement in POST_MIGRATION_INDEXES:
        con.execute(statement)
    # Seats are backfilled over history, not only over rows collected from
    # here on: a repost is a comparison against what came BEFORE, so a
    # database whose earlier rows have no seat can never report one.
    stored = int(get_meta(con, "seat_version") or 0)
    reposts.backfill_seats(con, stored_version=stored)
    if stored != reposts.SEAT_VERSION:
        set_meta(con, "seat_version", str(reposts.SEAT_VERSION))


def get_meta(con: sqlite3.Connection, key: str) -> str | None:
    row = con.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return str(row[0]) if row and row[0] is not None else None


def set_meta(con: sqlite3.Connection, key: str, value: str) -> None:
    con.execute(
        "INSERT INTO meta (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))
    con.commit()


# How long a blocked write waits before giving up. SQLite's default is 0: a
# write that finds the database locked fails AT ONCE with "database is locked"
# rather than waiting for the other writer to finish.
#
# Five seconds is chosen against what actually collides here - a single row
# write from the desktop against a collect's batch insert, both of which are
# milliseconds - not against a long transaction. A person mid-edit waits an
# imperceptible moment instead of being told the app is broken.
BUSY_TIMEOUT_MS = 5000


def _tune(con: sqlite3.Connection) -> None:
    """Connection settings every caller needs, in one place rather than three.

    THE TWO SETTINGS FIX DIFFERENT HALVES, and this was established by a test
    that refused to fail rather than by reasoning about it:

    BUSY_TIMEOUT FIXES THE REPORTED FAILURE - writer against writer. SQLite
    allows one writer at a time in BOTH journal modes, WAL included, so the
    desktop writing job_status while a collect writes jobs is a collision either
    way. What the default timeout of 0 does is make the second writer fail AT
    ONCE instead of queueing, which is why a status change during a collect came
    back as "database is locked" rather than taking a moment.

    WAL FIXES READER AGAINST WRITER. Under the rollback journal a COMMITTING
    writer takes an exclusive lock and readers are locked out for its duration,
    so the UI could not read the board mid-collect. Under WAL a reader is never
    blocked by a writer.

    Note for anyone tempted to test this with an open BEGIN IMMEDIATE: that
    takes a RESERVED lock and readers are still permitted under RESERVED. A test
    built on that assumption passes in both journal modes and measures nothing -
    see tests/test_concurrency.py, which times the wait instead.

    journal_mode belongs to the FILE and persists once set, so this is
    idempotent. It is still executed on every connect because a database created
    before this existed would otherwise stay in rollback mode forever, and
    nothing else would ever change it.

    busy_timeout is PER CONNECTION and does not persist, so it genuinely has to
    be set every time.

    ONE KNOWN LIMIT: WAL requires shared memory and does not work over a network
    share. Every profile this ships with is a local directory, and a home on a
    network path was already unsupported for other reasons.
    """
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA journal_mode = WAL")
    con.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")


def backup(home: os.PathLike[str] | str, con: sqlite3.Connection, *,
           tag: str) -> Path:
    """A copy of the database beside it, taken before anything is written.

    THROUGH SQLITE'S OWN BACKUP API, not a file copy. This database runs in WAL
    mode, so the .db file on its own is not the whole story - a plain copy taken
    while a write-ahead log holds recent pages produces a backup that is missing
    exactly the most recent work. sqlite3's backup() checkpoints properly.

    `tag` names the operation, so a directory of these says what each was taken
    before rather than only when.
    """
    stamp = datetime.now(tz=UTC).strftime("%Y%m%d-%H%M%S")
    target = Path(home) / f"unlatched.db.before-{tag}-{stamp}"
    with sqlite3.connect(target) as dest:
        con.backup(dest)
    dest.close()
    # A backup that was not written is worse than none: the caller goes ahead
    # believing there is a way back.
    if not target.is_file() or target.stat().st_size == 0:
        msg = f"backup at {target} was not written - refusing to continue"
        raise RuntimeError(msg)
    return target


def connect(home: str | os.PathLike[str] | None = None) -> sqlite3.Connection:
    path = paths.db_path(home)
    con = sqlite3.connect(str(path))
    con.row_factory = sqlite3.Row
    _tune(con)
    con.executescript(SCHEMA)
    _migrate_jobs(con)
    _migrate_status_tables(con)
    con.commit()
    # LAST, AND ONLY AFTER THE SCHEMA IS WHOLE. Correcting a key touches every
    # table that references one, so those tables have to exist first. It takes
    # a backup before it moves anything and does nothing at all on a database
    # whose prefixes already agree, which is every database this ever opens
    # after the first time.
    rekey.run_once(con, path.parent)
    return con


def connect_at(db_file: str | os.PathLike[str]) -> sqlite3.Connection:
    """Open (and migrate) a specific db file, bypassing paths.py entirely.

    Used by tests that want a throwaway database without touching the data
    dir resolver at all.
    """
    Path(db_file).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(db_file))
    con.row_factory = sqlite3.Row
    _tune(con)
    con.executescript(SCHEMA)
    _migrate_jobs(con)
    _migrate_status_tables(con)
    con.commit()
    return con


def upsert_company(con: sqlite3.Connection, name: str, *, domain: str = "",
                    careers_url: str = "", ats: str = "", ats_ref: str = "",
                    probe_status: str | None = None,
                    origin: str | None = None) -> int:
    """Add or update one employer. Returns its id.

    `origin` is WHERE THE ROW CAME FROM - seeded, discovered or manual - and it
    is written only on INSERT. Never overwritten on a later update: a shipped
    employer that the crawler happens to rediscover is still a shipped
    employer, and "refresh the seeded companies" has to mean the same set
    tomorrow as it did today.
    """
    # Same store boundary as upsert_job: careers_url is discovered by following
    # links on remote pages, and the Companies screen renders it as something
    # the OS will open.
    careers_url = links.safe_or_empty(careers_url)
    # id is companies.id, an INTEGER PRIMARY KEY - sqlite3.Row.__getitem__ is
    # typed Any in the stdlib stubs since a row's column types are not known
    # statically; int() states the schema fact the stubs cannot.
    row = con.execute("SELECT id FROM companies WHERE name = ?", (name,)).fetchone()
    if row:
        sets, vals = [], []
        for col, val in (("domain", domain), ("careers_url", careers_url),
                          ("ats", ats), ("ats_ref", ats_ref)):
            if val:
                sets.append(f"{col} = ?")
                vals.append(val)
        if probe_status:
            sets.append("probe_status = ?")
            vals.append(probe_status)
        if sets:
            vals.append(row["id"])
            # S608: columns come from a fixed tuple of literal names above,
            # never from caller input, so there is no injection surface.
            con.execute(f"UPDATE companies SET {', '.join(sets)} WHERE id = ?",  # noqa: S608
                        vals)
            con.commit()
        return int(row["id"])
    cur = con.execute(
        "INSERT INTO companies (name, domain, careers_url, ats, ats_ref, "
        "probe_status, origin) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (name, domain, careers_url, ats, ats_ref, probe_status or "new",
         origin or ""))
    con.commit()
    # lastrowid is None only when the prior statement was not an INSERT, or
    # the table has no rowid - neither is true for this fixed INSERT into a
    # normal rowid table, so a None here means sqlite itself is broken.
    if cur.lastrowid is None:
        raise RuntimeError("INSERT into companies did not get a rowid")
    return cur.lastrowid


def get_company(con: sqlite3.Connection, name: str) -> sqlite3.Row | None:
    row = con.execute("SELECT * FROM companies WHERE name = ?", (name,)).fetchone()
    return cast("sqlite3.Row | None", row)


def list_companies(con: sqlite3.Connection) -> list[sqlite3.Row]:
    rows = con.execute("SELECT * FROM companies ORDER BY name").fetchall()
    return cast("list[sqlite3.Row]", rows)


def upsert_job(con: sqlite3.Connection, key: str, fields: dict[str, Any]) -> None:
    """Write to jobs.* ONLY. Never touches job_status or job_status_log -
    that split is the whole point of the machine/human table separation.

    A PARTIAL `fields` dict is fine on an existing row - a re-screen only
    has machine-derived columns (qualified, score, ...) to say, not the
    title or description it never re-fetched, and a plain upsert-by-INSERT
    would fail NOT NULL on `title` for every row it did not also pass.
    A brand new row still needs `title` (the one NOT NULL column with no
    default) since there is nothing on disk yet to fall back to.
    """
    data = {k: v for k, v in fields.items() if k in JOB_COLUMNS}
    # The store boundary for links (found by a red-team review). A posting's `url`
    # arrives from remote JSON-LD or a board API and ends up as something the
    # desktop hands to the operating system to open, so anything that is not
    # plain http/https loses its link here - at the one point every collector
    # and the hand-add path both pass through. The row itself is untouched: it
    # is still a real job, it just has nowhere to click.
    if "url" in data:
        data["url"] = links.safe_or_empty(data["url"])
    exists = con.execute("SELECT 1 FROM jobs WHERE key = ?", (key,)).fetchone()
    if exists:
        cols = [c for c in JOB_COLUMNS if c in data and c != "key"]
        if cols:
            sets = ", ".join(f"{c} = ?" for c in cols)
            # S608: cols is filtered from the JOB_COLUMNS whitelist above,
            # never from caller-supplied strings, so nothing here is
            # attacker-reachable.
            con.execute(f"UPDATE jobs SET {sets} WHERE key = ?",  # noqa: S608
                        [data[c] for c in cols] + [key])
    else:
        data["key"] = key
        if "title" not in data:
            raise ValueError(f"cannot insert job {key!r} without a title")
        cols = [c for c in JOB_COLUMNS if c in data]
        placeholders = ", ".join("?" for _ in cols)
        # S608: same JOB_COLUMNS whitelist as above.
        con.execute(f"INSERT INTO jobs ({', '.join(cols)}) VALUES ({placeholders})",  # noqa: S608
                    [data[c] for c in cols])
    con.commit()


def get_job(con: sqlite3.Connection, key: str) -> sqlite3.Row | None:
    row = con.execute("SELECT * FROM jobs WHERE key = ?", (key,)).fetchone()
    return cast("sqlite3.Row | None", row)


def list_jobs(con: sqlite3.Connection, *, qualified_only: bool = False,
              include_closed: bool = False) -> list[sqlite3.Row]:
    sql = ("SELECT j.*, c.name AS company_name FROM jobs j "
           "LEFT JOIN companies c ON c.id = j.company_id")
    where = []
    params: list[str] = []
    if qualified_only:
        where.append("j.qualified = 1")
    if not include_closed:
        # The settled statuses arrive as bind PARAMETERS; the only thing
        # interpolated here is the run of "?" markers to bind them to. S608 is
        # suppressed because it cannot tell that apart from interpolating the
        # values themselves, which is the thing it is right to refuse.
        markers = status_vocab.placeholders(status_vocab.SETTLED)
        clause = f"SELECT key FROM job_status WHERE status IN ({markers})"  # noqa: S608
        where.append(f"j.key NOT IN ({clause})")
        params.extend(status_vocab.SETTLED)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY j.score DESC"
    rows = con.execute(sql, params).fetchall()
    return cast("list[sqlite3.Row]", rows)


def all_job_keys(con: sqlite3.Connection) -> set[str]:
    return {r["key"] for r in con.execute("SELECT key FROM jobs")}


def snapshot_job_status(con: sqlite3.Connection) -> list[sqlite3.Row]:
    """Every job_status row, ordered, for a byte-identical-after-rescreen
    comparison in tests.
    """
    rows = con.execute(
        "SELECT key, status, note, updated FROM job_status ORDER BY key"
    ).fetchall()
    return cast("list[sqlite3.Row]", rows)


def mark_delisted(con: sqlite3.Connection, company_id: int, seen_at: str) -> int:
    """Flag this company's jobs that the latest collect did NOT find.

    Returns how many were flagged. Called ONLY after a company's collect
    succeeded - if the board errored or returned nothing because of a fetch
    problem, absence means we failed to look, not that the jobs are gone.
    Marking them delisted on a failed fetch would empty a person's list over
    a network blip, which is the same silent-failure class as a truncated
    board reading as zero postings.

    Nothing is deleted. A delisted row keeps its description, its link and
    any status the person set - "I applied to this and it has since been
    pulled" is information, not garbage to collect.
    """
    # A hand-added job is never delisted by this. It came from a link the
    # person pasted, not from a board, so a board collect that did not find
    # it has not learned anything about it - and if they added a job at an
    # employer whose board we also read, it shares this company_id and would
    # otherwise be struck through the first time that board was collected.
    #
    # THE KEYS ARE READ BEFORE THE UPDATE so the same rows can be handed to
    # close_untouched_delisted. rowcount says how many changed and not which,
    # and "which" is the whole question for the status rule.
    rows = con.execute(
        "SELECT key FROM jobs "
        "WHERE company_id = ? AND delisted_at IS NULL "
        "AND source != 'manual' "
        "AND (last_seen IS NULL OR last_seen < ?)",
        (company_id, seen_at)).fetchall()
    keys = [str(r["key"]) for r in rows]
    if not keys:
        return 0
    cur = con.execute(
        "UPDATE jobs SET delisted_at = ? "
        "WHERE company_id = ? AND delisted_at IS NULL "
        "AND source != 'manual' "
        "AND (last_seen IS NULL OR last_seen < ?)",
        (seen_at, company_id, seen_at))
    con.commit()
    close_untouched_delisted(con, keys, at=seen_at)
    return int(cur.rowcount or 0)


def close_untouched_delisted(con: sqlite3.Connection, keys: list[str],
                             at: str | None = None) -> int:
    """Give a taken-down posting a status, but ONLY where nobody set one.

    THE SAME RULE FOR EVERY WAY A CLOSURE IS NOTICED. A person pressing "Mark
    taken down" got this; a collect finding the posting gone, the added-links
    recheck, the `delist` verb and a collector's own pushed closures all set
    `delisted_at` and no status - so the rows a person never touched sat in the
    list reading "not set" for ever while the identical row closed by hand read
    "No longer open". The outcome must not depend on how the closure was
    noticed, which is why this is one function called from all five.

    ONLY ONTO AN EMPTY STATUS. Everything else is the person's: an application
    in flight keeps its rung, and a recorded rejection keeps saying it was a
    rejection. Widening this to "anything not in flight" is what overwrote No
    Offer rows in the desktop half - see db.rs::mark_taken_down.

    Returns how many were given the status.
    """
    if not keys:
        return 0
    marks = ", ".join("?" for _ in keys)
    # NO ROW AND AN EMPTY ROW MEAN THE SAME THING. A job nobody ever judged
    # usually has no job_status row at all, but clear_status leaves one behind
    # holding "", and both are "nobody decided".
    rows = con.execute(
        f"SELECT j.key FROM jobs j "  # noqa: S608 - `marks` is bind markers only
        f"LEFT JOIN job_status s ON s.key = j.key "
        f"WHERE j.key IN ({marks}) "
        f"AND COALESCE(s.status, '') = ''",
        keys).fetchall()
    untouched = [str(r["key"]) for r in rows]
    for key in untouched:
        status_vocab.set_status(con, key, status_vocab.CLOSED, at=at)
    return len(untouched)


def relist(con: sqlite3.Connection, key: str) -> None:
    """A posting that reappears is live again - boards go briefly empty
    during edits, and a role that comes back should not stay struck through.
    """
    con.execute("UPDATE jobs SET delisted_at = NULL WHERE key = ?", (key,))


def retire(con: sqlite3.Connection, keys: list[str], *, at: str) -> int:
    """Remove rows from the person's lists. Returns how many moved.

    HIDES, does not delete - which is the same result as deleting, with a way
    back. Everything the row carries is kept: the status they set, the
    append-only log of how it got there, its part in the repost history, and
    the fact it was ever collected. A bulk action over a multi-select is one
    misclick away from erasing months of application history, and a job
    tracker's whole value is that history.

    Retiring is also STICKY against the collector: `retired_at` is not in the
    set of columns a collect writes, so a row that is still live on the board
    is re-read, re-scored and stays hidden. Somebody who threw a job away does
    not want it back tomorrow morning.
    """
    return _write_retired(con, keys, at)


def restore(con: sqlite3.Connection, keys: list[str]) -> int:
    """Put retired rows back. Returns how many returned."""
    return _write_retired(con, keys, None)


def _write_retired(con: sqlite3.Connection, keys: list[str],
                    at: str | None) -> int:
    """One statement per key, deliberately.

    An `IN (...)` clause means building SQL from a computed string, and the
    keys here arrive from a selection in the UI. One row at a time is a fixed,
    fully parameterised statement with nothing to get wrong - and a selection
    is tens of rows made by hand, so the cost is nothing. Both statements run
    inside one transaction, so a failure part-way leaves no half-done removal.
    """
    moved = 0
    for key in keys:
        if at is None:
            cur = con.execute(
                "UPDATE jobs SET retired_at = NULL WHERE key = ?", (key,))
        else:
            cur = con.execute(
                "UPDATE jobs SET retired_at = ? "
                "WHERE key = ? AND retired_at IS NULL", (at, key))
        moved += int(cur.rowcount or 0)
    con.commit()
    return moved


def retired_count(con: sqlite3.Connection) -> int:
    """How many rows are hidden. The UI needs this to offer a way back - a
    pile you cannot see the size of is one you forget you have.
    """
    row = con.execute(
        "SELECT COUNT(*) FROM jobs WHERE retired_at IS NOT NULL").fetchone()
    return int(row[0]) if row else 0


def applied_among(con: sqlite3.Connection, keys: list[str]) -> list[str]:
    """Which of these the person recorded an application for.

    Read before a bulk retire so the confirmation can say so. Removing a job
    you applied to loses the row that matters most - the one you go back to
    when someone finally replies - so it is worth one sentence of warning
    rather than a silent count.

    Reads the append-only LOG rather than the current status, so a job since
    marked No Offer still counts - somebody clearing out rejections is removing
    exactly the rows worth warning about.
    """
    # Bind markers only - the statuses themselves are passed as parameters
    # below. See the note on the same suppression in list_jobs.
    sql = ("SELECT 1 FROM job_status_log WHERE key = ? AND status IN "  # noqa: S608
           f"({status_vocab.placeholders(status_vocab.PROVES_APPLIED)}) LIMIT 1")
    found = []
    for key in keys:
        row = con.execute(sql, (key, *status_vocab.PROVES_APPLIED)).fetchone()
        if row:
            found.append(key)
    return found
