// SQLite access. The schema below is the shared contract between this app
// and its command-line sibling; both open the same database file and must
// create identical tables, so the CREATE TABLE statements are reproduced
// here exactly rather than paraphrased.

use rusqlite::{params, Connection, OptionalExtension, Result as SqlResult};
use std::collections::HashMap;
use std::path::Path;

use crate::date;

pub const SCHEMA_SQL: &str = "
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
  source TEXT,
  title TEXT NOT NULL, location TEXT,
  remote TEXT, remote_evidence TEXT,
  salary_min INTEGER, salary_max INTEGER, currency TEXT,
  hourly_rate REAL,
  url TEXT, posted_at TEXT, fetched_at TEXT,
  last_seen TEXT, delisted_at TEXT,
  -- When the PERSON removed this row from their lists, as against
  -- delisted_at, which is when the EMPLOYER took the posting down.
  retired_at TEXT,
  description TEXT,
  employment_type TEXT,
  seat TEXT, repost_note TEXT, repost_of TEXT,
  coverage_pct REAL, missing_skills TEXT,
  requirements_summary TEXT,
  score REAL, screen_reasons TEXT,
  verdict TEXT,
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
-- in the log already means 'the status was cleared', which the engine's
-- importer acts on. A note-only row there would import as an erased status -
-- the note would arrive having deleted the thing it was written about.
CREATE TABLE IF NOT EXISTS job_note (
  id INTEGER PRIMARY KEY, key TEXT NOT NULL, note TEXT NOT NULL, at TEXT NOT NULL
);
-- Files and links kept beside a job. THE BYTES ARE NOT IN HERE: any local
-- agent can read this database, so content a stranger wrote must not be in
-- it. Files live under <home>/attachments/<trust>/ and `trust` says which
-- side of the conversation wrote them - 'mine' (offered to an assistant) or
-- 'posting' (never offered). Mirrors the engine's own CREATE TABLE.
CREATE TABLE IF NOT EXISTS attachment (
  id INTEGER PRIMARY KEY,
  key TEXT NOT NULL,
  trust TEXT NOT NULL,
  kind TEXT NOT NULL,
  stored_name TEXT,
  display_name TEXT NOT NULL,
  url TEXT,
  bytes INTEGER,
  added_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS attachment_trust_log (
  id INTEGER PRIMARY KEY,
  attachment_id INTEGER NOT NULL,
  was TEXT, now TEXT, at TEXT NOT NULL
);
-- Small key/value store for facts about the database itself rather than the
-- jobs in it: which seat-keying rule the rows were written with, when each
-- collector's handoff was last taken in, how many consecutive collections a
-- board has come back empty. Mirrors the engine's own CREATE TABLE.
--
-- CREATED HERE BECAUSE IT IS READ HERE. collector_taken_in queries this table,
-- and it was absent from this schema while present in the engine's - so a
-- profile created by THIS app and opened before anything had been collected
-- had no `meta` at all, the query failed with 'no such table', and the call
-- site's unwrap_or_default turned that into 'no collector has ever been read
-- in'. Silently, which is the shape of failure this file's own ensure_columns
-- comment is about: a missing column is a hard error rather than a null, and
-- so is a missing table.
CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT
);
CREATE INDEX IF NOT EXISTS ix_attachment_key ON attachment(key);
CREATE INDEX IF NOT EXISTS ix_jobs_company ON jobs(company_id);
CREATE INDEX IF NOT EXISTS ix_jobs_qualified ON jobs(qualified);
CREATE INDEX IF NOT EXISTS ix_status_log_key ON job_status_log(key);
CREATE INDEX IF NOT EXISTS ix_job_note_key ON job_note(key);
";

/// Columns the engine has added to `jobs` since the first release, mirroring
/// ADDED_JOB_COLUMNS in the engine's db.py.
///
/// This app has to know about them because CREATE TABLE IF NOT EXISTS does
/// nothing to a table that already exists: a database created by an older
/// version keeps its original shape forever. Selecting a column that is not
/// there is a hard SQLite error, not a null - so before this existed, opening
/// an older profile failed the whole triage query with "no such column:
/// jobs.hourly_rate" and showed an empty table. Caught by the GUI harness,
/// which seeds its home from an older schema, and it is exactly what a user
/// hits after updating the app and opening it before collecting.
const ADDED_JOB_COLUMNS: [(&str, &str); 18] = [
    ("source", "TEXT"),
    ("employment_type", "TEXT"),
    ("verdict", "TEXT"),
    ("hourly_rate", "REAL"),
    ("last_seen", "TEXT"),
    ("delisted_at", "TEXT"),
    ("seat", "TEXT"),
    ("repost_note", "TEXT"),
    ("repost_of", "TEXT"),
    ("coverage_pct", "REAL"),
    ("missing_skills", "TEXT"),
    ("requirements_summary", "TEXT"),
    ("retired_at", "TEXT"),
    // Where the Apply button goes when it leaves the board the job was found
    // on. Mirrored here so a database created by EITHER half has the column:
    // the schema is a shared contract, and a column only one side migrates is
    // how the two drift apart.
    ("apply_url", "TEXT"),
    // The posting this one duplicates, and why. Grouping hides rather than
    // deletes, so both halves have to migrate or one side would select a
    // column the other never created.
    ("duplicate_of", "TEXT"),
    ("duplicate_reason", "TEXT"),
    // WHY an apply_url is empty, which is a different question from whether it
    // is. Empty meant both "applies on the board itself, so no external
    // destination exists" and "we failed to capture one" - opposite facts, and
    // the second is a defect that looked exactly like the first.
    ("apply_kind", "TEXT"),
    // WHICH KIND of alt - 'salary' or 'requirements' - so the two cards that
    // split that pile have something to query. Mirrored here because either
    // half can create the database, and the card would otherwise fail its
    // whole query with "no such column" on a profile the engine had not
    // opened yet - the failure observed for jobs.hourly_rate, which is why
    // this list exists at all (see its doc comment). The two lists are now
    // compared by a test rather than by remembering.
    ("alt_reason", "TEXT"),
];

/// What an Offer records beyond a note, added to `job_status_log` after that
/// table's first release. Mirrors ADDED_STATUS_LOG_COLUMNS in the engine's
/// db.py - the schema is a shared contract, and a column only one side
/// migrates is how the two halves drift apart.
const ADDED_STATUS_LOG_COLUMNS: [(&str, &str); 2] = [("pay", "TEXT"), ("offer_date", "TEXT")];

/// Where a company came from - seeded, discovered, manual or imported.
/// Mirrors ADDED_COMPANY_COLUMNS in the engine's db.py.
///
/// BOTH HALVES MIGRATE OR NEITHER SHOULD. Whichever opens an older database
/// first has to add the column, because the other will select it - and
/// selecting a column that is not there is a hard SQLite error, not a null.
const ADDED_COMPANY_COLUMNS: [(&str, &str); 1] = [("origin", "TEXT")];

/// The one origin the UI names out loud, in the Collect menu's "seeded
/// employers only". Spelled the same as `unlatched.db.SEEDED`, because it is
/// passed straight through to `collect --origin`, whose argparse `choices`
/// rejects anything else.
pub const SEEDED: &str = "seeded";

/// Adds any of the listed columns this database does not have yet. Columns
/// only, never data: an empty column reads as "not known", which is true.
///
/// CREATE TABLE IF NOT EXISTS does nothing to a table that already exists, so
/// a database created by an older version keeps its original shape forever.
/// Selecting a column that is not there is a hard SQLite error, not a null -
/// which is how an older profile once failed the whole triage query with "no
/// such column: jobs.hourly_rate" and showed an empty table.
fn ensure_columns(conn: &Connection, table: &str, columns: &[(&str, &str)]) -> SqlResult<()> {
    let mut existing = std::collections::HashSet::new();
    {
        let mut stmt = conn.prepare(&format!("PRAGMA table_info({table})"))?;
        let names = stmt.query_map([], |r| r.get::<_, String>(1))?;
        for name in names {
            existing.insert(name?);
        }
    }
    for (name, decl) in columns.iter() {
        if !existing.contains(*name) {
            conn.execute(
                &format!("ALTER TABLE {table} ADD COLUMN {name} {decl}"),
                [],
            )?;
        }
    }
    Ok(())
}

/// Applies the status renames in `crate::status::RENAMES` to both the current
/// state and the history.
///
/// BOTH TABLES. The log is what the funnel and the export read, so renaming
/// only `job_status` would leave a person's history still saying "denied"
/// while the row in front of them said "No Offer" - the same event under two
/// names, in the one place the app promises to preserve exactly.
///
/// Idempotent, and cheap: `job_status` holds one row per decision a person has
/// made, not one per job collected.
fn rename_retired_statuses(conn: &Connection) -> SqlResult<()> {
    for (from, to) in crate::status::RENAMES.iter() {
        conn.execute(
            "UPDATE job_status SET status = ?2 WHERE status = ?1",
            params![from, to],
        )?;
        conn.execute(
            "UPDATE job_status_log SET status = ?2 WHERE status = ?1",
            params![from, to],
        )?;
    }
    Ok(())
}

/// How long a blocked write waits before giving up. SQLite's default is zero:
/// a write that finds the database locked fails AT ONCE with "database is
/// locked" rather than waiting. Kept in step with `BUSY_TIMEOUT_MS` in the
/// engine's `db.py`; both sides of the same file want the same patience.
const BUSY_TIMEOUT_MS: u32 = 5_000;

pub fn open(path: &Path) -> SqlResult<Connection> {
    let conn = Connection::open(path)?;
    // WAL AND A BUSY TIMEOUT, BECAUSE THIS IS NOT THE ONLY WRITER. This app
    // writes job_status directly while the engine, a separate process, writes
    // jobs during a collect.
    //
    // THE TIMEOUT IS THE ONE THAT FIXES THE REPORTED FAILURE. SQLite allows a
    // single writer at a time in BOTH journal modes, WAL included, so the two
    // sides collide either way; the default timeout of 0 is what made the
    // second writer fail AT ONCE rather than wait, giving the person "database
    // is locked" with nothing on screen saying a refresh was running.
    //
    // WAL fixes the other half - a reader is never blocked by a writer, so the
    // UI can read the board while a collect writes to it. Verified from the
    // engine side in tests/test_concurrency.py.
    //
    // journal_mode belongs to the file and persists; busy_timeout is per
    // connection and has to be set every time. Applied via pragma_update rather
    // than execute_batch because journal_mode RETURNS a row, and execute
    // treats a returned row as an error.
    conn.busy_timeout(std::time::Duration::from_millis(BUSY_TIMEOUT_MS.into()))?;
    conn.pragma_update(None, "journal_mode", "WAL")?;
    conn.pragma_update(None, "foreign_keys", "ON")?;
    conn.execute_batch(SCHEMA_SQL)?;
    migrate(&conn)?;
    Ok(conn)
}

/// Split the existing alt rows between the two cards that now divide them.
///
/// MIRRORS ALT_REASON_BACKFILL in the engine's db.py, and the statement is
/// compared against it by a test - a data migration only one half runs is the
/// same drift as a column only one half adds, and it fails more quietly.
const ALT_REASON_BACKFILL: &str = "UPDATE jobs SET alt_reason = 'salary'      WHERE verdict = 'alt' AND screen_reasons LIKE '%fallback floor%'";
const ALT_REASON_VERSION: i64 = 1;

/// Run it once per database, tracked by a stored version.
///
/// NOT GUARDED BY "was the column just added", which was the first attempt and
/// could not work. Both halves create the jobs table, so whichever migrates
/// SECOND finds the column already there and skips the backfill - and for
/// somebody who installs an update and opens the app, the desktop always goes
/// first. Observed on 2026-09-02 on a real profile: 563 alt rows, 83 of them
/// describing a fallback floor, all left unlabelled, and the BELOW SALARY card
/// reading 0 while REQUIREMENTS NOT ALIGNED held every one of them.
fn backfill_alt_reason(conn: &Connection) -> SqlResult<()> {
    let stored: Option<String> = conn
        .query_row(
            "SELECT value FROM meta WHERE key = 'alt_reason_version'",
            [],
            |r| r.get(0),
        )
        .optional()?;
    if stored.and_then(|s| s.parse::<i64>().ok()).unwrap_or(0) >= ALT_REASON_VERSION {
        return Ok(());
    }
    conn.execute(ALT_REASON_BACKFILL, [])?;
    conn.execute(
        "INSERT OR REPLACE INTO meta (key, value) VALUES ('alt_reason_version', ?1)",
        [ALT_REASON_VERSION.to_string()],
    )?;
    Ok(())
}

/// Everything that has to happen to a database after its tables exist.
///
/// ONE ENTRY POINT so a test fixture cannot open a shape no real install has.
/// SCHEMA_SQL is the ORIGINAL layout and CREATE TABLE IF NOT EXISTS does
/// nothing to a table that is already there, so every column added since
/// arrives here - and a fixture that runs the schema without this is testing
/// against a database that has not existed since the first release.
pub fn migrate(conn: &Connection) -> SqlResult<()> {
    ensure_columns(conn, "jobs", &ADDED_JOB_COLUMNS)?;
    ensure_columns(conn, "job_status_log", &ADDED_STATUS_LOG_COLUMNS)?;
    ensure_columns(conn, "companies", &ADDED_COMPANY_COLUMNS)?;
    backfill_alt_reason(conn)?;
    rename_retired_statuses(conn)
}

#[derive(Debug, Clone, Default)]
pub struct Company {
    // Row identity for the companies table (join key from jobs.company_id).
    // The UI looks up companies by name, not id, so this field is carried
    // for completeness rather than read directly.
    #[allow(dead_code)]
    pub id: i64,
    pub name: String,
    pub domain: Option<String>,
    pub careers_url: Option<String>,
    pub ats: Option<String>,
    pub ats_ref: Option<String>,
    pub probe_status: String,
    pub last_probed: Option<String>,
    /// Where this employer came from: shipped with the app, found by the app,
    /// typed in by hand, or carried in from somebody else's export. None for a
    /// row written before the column existed - honestly unknown rather than
    /// guessed into a group somebody is about to fetch on behalf of.
    pub origin: Option<String>,
}

pub fn list_companies(conn: &Connection) -> SqlResult<Vec<Company>> {
    let mut stmt = conn.prepare(
        "SELECT id, name, domain, careers_url, ats, ats_ref, probe_status, last_probed,
                origin
         FROM companies ORDER BY name COLLATE NOCASE ASC",
    )?;
    let rows = stmt.query_map([], |r| {
        Ok(Company {
            id: r.get(0)?,
            name: r.get(1)?,
            domain: r.get(2)?,
            careers_url: r.get(3)?,
            ats: r.get(4)?,
            ats_ref: r.get(5)?,
            probe_status: r
                .get::<_, Option<String>>(6)?
                .unwrap_or_else(|| "new".to_string()),
            last_probed: r.get(7)?,
            origin: r.get(8)?,
        })
    })?;
    rows.collect()
}

/// Creates a placeholder row so a newly typed company name shows up in the
/// table immediately; `discover` (run separately, via the CLI) fills in the
/// domain/ats/probe_status columns later. Existing names are left alone.
pub fn add_company_stub(conn: &Connection, name: &str) -> SqlResult<()> {
    conn.execute(
        "INSERT OR IGNORE INTO companies (name, probe_status) VALUES (?1, 'new')",
        params![name],
    )?;
    Ok(())
}

#[derive(Debug, Clone, Default)]
pub struct Job {
    /// How the posting is applied to - "easy-apply", "external", or
    /// absent when the collector could not tell. Empty string and NULL
    /// mean different things on purpose: one is "we looked and could not
    /// classify it", the other is "nothing ever set this".
    pub apply_kind: Option<String>,
    pub key: String,
    // Foreign key into companies; already resolved into company_name on
    // TriageRow by the join, so callers use that instead of this raw id.
    #[allow(dead_code)]
    pub company_id: Option<i64>,
    pub title: String,
    pub location: Option<String>,
    pub remote: Option<String>,
    pub remote_evidence: Option<String>,
    pub salary_min: Option<i64>,
    pub salary_max: Option<i64>,
    pub currency: Option<String>,
    /// The rate as the employer wrote it, when the posting quoted one. See
    /// the jobs table comment: salary_min/max are annualised, this is not.
    pub hourly_rate: Option<f64>,
    pub url: Option<String>,
    pub posted_at: Option<String>,
    pub fetched_at: Option<String>,
    pub description: Option<String>,
    pub score: Option<f64>,
    pub screen_reasons: Option<String>,
    /// Which collector produced the row - shown so a person can tell an
    /// employer's own board from a federal search at a glance.
    pub source: Option<String>,
    /// keep | alt | drop. `qualified` says whether it matched at all; this
    /// says how cleanly, so a fallback-tier job is visible without being
    /// counted as a clean match.
    pub verdict: Option<String>,
    /// WHICH KIND of alt - 'salary' or 'requirements', empty or absent when
    /// nothing recorded one. Carried on the row so the Match badge can name
    /// the same pile the dashboard card does; `verdict` alone says a job fell
    /// short and never says what of. That the two agree is tested by
    /// tests::the_badge_names_the_card_the_row_is_actually_counted_on.
    pub alt_reason: Option<String>,
    /// Share of the skills THIS posting asks for that the resume evidences,
    /// and the ones it does not. Both are written by the engine at screening
    /// time - the engine can read a .docx resume and this app cannot, so
    /// computing them here would report every skill as missing for anyone
    /// whose resume is a Word file.
    pub coverage_pct: Option<f64>,
    pub missing_skills: Option<String>,
    /// The seat's advertising history in one sentence, or None if this seat
    /// has only ever been advertised once. See the engine's reposts.py.
    pub repost_note: Option<String>,
    /// The advertisement this one is a new entry after: same seat, more than
    /// four weeks later. Both rows stay visible and this one points back.
    pub repost_of: Option<String>,
    /// When a successful collect first found this posting gone from its
    /// board. Machine-observed and shown on the row; it never writes the
    /// person's own status, so a job they applied to still reads "Applied"
    /// after the employer takes the listing down.
    pub delisted_at: Option<String>,
    /// What the posting demands, in a few words ("5+ yrs, BS, CDL"), so a row
    /// can be ruled out without opening it.
    pub requirements_summary: Option<String>,
    /// Why this row was grouped behind another, in words. Shown in the grouped
    /// view so the person can judge the decision rather than take it on trust -
    /// which is the whole difference between a merge they can audit and one
    /// that quietly disappeared a job.
    pub duplicate_reason: Option<String>,
    // Always true here: list_triage_jobs only ever selects qualified rows.
    // Kept on the struct so Job still mirrors the full jobs table.
    #[allow(dead_code)]
    pub qualified: bool,
}

#[derive(Debug, Clone, Default)]
pub struct TriageRow {
    pub job: Job,
    /// The first 400 characters of the description - what the hover tooltip
    /// shows, and all a list ever needs.
    ///
    /// SEPARATE FROM job.description ON PURPOSE. That field holds the FULL
    /// text and is None until the row is opened, so "loaded" and "complete"
    /// stay distinguishable. Folding them together would make
    /// ensure_description's guard meaningless and could show a truncated
    /// description in the opened view.
    pub description_preview: Option<String>,
    pub company_name: Option<String>,
    pub status: Option<String>,
    pub note: Option<String>,
    /// The other places the same opening is listed, when several postings are
    /// one requisition advertised per city. Empty for an ordinary row.
    pub other_locations: Vec<String>,
    /// When the person last set this row's status. Drives "applied 12 days
    /// ago" - applications are lost to silence far more often than to a
    /// rejection, and nothing else records how long one has been waiting.
    pub status_updated: Option<String>,
    /// When they FIRST marked this applied, from the append-only log.
    ///
    /// Not status_updated, which moves every time the status changes: mark a
    /// job Interviewed and status_updated becomes the interview date, so the
    /// one thing a person needs to answer "how long have I been waiting"
    /// would quietly reset at the moment it started mattering most.
    pub applied_at: Option<String>,
    /// Every status this row has ever carried. Drives which of the dependent
    /// statuses the dropdown can offer - see crate::status::blocked_reason for
    /// why the history and not the current status.
    pub history: std::collections::HashSet<String>,
}

/// The description for ONE job, read when a row is opened.
///
/// Lists deliberately do not carry descriptions - see SELECT_TRIAGE_COLUMNS.
/// This is the other half of that: the row a person actually opened, and only
/// that row, pays for its own text.
pub fn description_for(conn: &Connection, key: &str) -> SqlResult<Option<String>> {
    conn.query_row(
        "SELECT description FROM jobs WHERE key = ?1",
        [key],
        |r| r.get::<_, Option<String>>(0),
    )
}

/// Folds "one opening, listed once per city" down to a single row.
///
/// Measured: 75 of 290 rows in one profile were this - five postings of
/// "Enterprise Implementation Consultant" across San Francisco, Seattle, Salt
/// Lake City, New York and Vancouver, with descriptions within six characters
/// of each other. A reader scrolling that is doing the same triage five times.
///
/// The survivor is the FIRST row of its group, and callers pass rows already
/// ordered by score - so the representative is the best-scoring posting of the
/// set, deterministically, and the person's status attaches to a stable key
/// rather than whichever city happened to sort first.
///
/// Grouped on company + title only, deliberately NOT the seat key from the
/// engine: a seat includes the location precisely so it can tell reposts
/// apart, and here the differing location is the thing being folded away.
///
/// Nothing is deleted or filtered from the database. The UI offers a toggle
/// to see every location, because a person who can only work in one of those
/// cities needs the row for that city.
pub fn collapse_locations(rows: Vec<TriageRow>) -> Vec<TriageRow> {
    let mut out: Vec<TriageRow> = Vec::with_capacity(rows.len());
    let mut seen: HashMap<(String, String), usize> = HashMap::new();
    for row in rows {
        let group = (
            row.company_name.clone().unwrap_or_default().to_lowercase(),
            row.job.title.to_lowercase(),
        );
        match seen.get(&group) {
            Some(&idx) => {
                let place = row.job.location.clone().unwrap_or_default();
                // A blank location adds nothing a reader can act on, an exact
                // repeat of one already listed is noise, and the survivor's
                // OWN location is not an "other" location - without that last
                // check two postings in the same city rendered "Austin, TX +1"
                // and invented a second city.
                let already = out[idx].other_locations.contains(&place)
                    || out[idx].job.location.as_deref() == Some(place.as_str());
                if !place.trim().is_empty() && !already {
                    out[idx].other_locations.push(place);
                }
            }
            None => {
                seen.insert(group, out.len());
                out.push(row);
            }
        }
    }
    out
}

// The queue's default view: hide anything the human has already closed the
// loop on, so what remains reads as "still open to act on".
/// (Was a hand-written list here. It said pass/denied/closed and went stale
/// the moment the vocabulary grew - a job marked Declined Offer would have
/// stayed in the working queue forever.)
///
/// The columns and joins both lists read. Held in one place because they were
/// duplicated once and the two copies immediately disagreed - the row mapper
/// below indexes by position, so a column added to one query and not the other
/// silently reads the wrong field rather than failing.
const SELECT_TRIAGE_COLUMNS: &str = "SELECT jobs.key, jobs.company_id, jobs.title, jobs.location, jobs.remote,
                       jobs.remote_evidence, jobs.salary_min, jobs.salary_max, jobs.currency,
                       jobs.hourly_rate,
                       jobs.url, jobs.posted_at, jobs.fetched_at,
                       -- A PREVIEW, NOT THE DESCRIPTION. The full text is 74 MB
                       -- across this profile's 11,943 rows and no list renders
                       -- it; selecting it made every refresh pull tens of
                       -- megabytes to draw a table that shows none of it.
                       -- The hover tooltip shows 400 characters, so 400 is what
                       -- a list loads. The OPENED row fetches its own full text
                       -- through description_for.
                       substr(jobs.description, 1, 400),
                       jobs.score, jobs.screen_reasons, jobs.qualified, jobs.source, jobs.verdict,
                       jobs.coverage_pct, jobs.missing_skills, jobs.repost_note,
                       jobs.delisted_at, jobs.requirements_summary,
                       jobs.duplicate_reason,
                       companies.name, job_status.status, job_status.note,
                       job_status.updated,
                       (SELECT MIN(l.at) FROM job_status_log l
                         WHERE l.key = jobs.key AND l.status = 'applied'),
                       -- Every status this row has EVER been given, which is
                       -- what gates the dependent statuses in the dropdown.
                       -- Carried on the row rather than queried when the
                       -- dropdown opens: a query in the render path runs on a
                       -- frame that is already drawing, and this makes the
                       -- rule a pure function of data the list already holds.
                       (SELECT GROUP_CONCAT(DISTINCT l.status) FROM job_status_log l
                         WHERE l.key = jobs.key),
                       -- APPENDED AT THE END, deliberately. Every index below
                       -- is positional, so putting a new column beside the one
                       -- it belongs with would silently shift eight fields.
                       jobs.repost_of,
                       -- HOW you apply, not where. Stored since the collector
                       -- started classifying it and never once displayed.
                       jobs.apply_kind,
                       -- Also appended, same reason as repost_of above.
                       jobs.alt_reason
                FROM jobs
                LEFT JOIN job_status ON job_status.key = jobs.key
                LEFT JOIN companies ON companies.id = jobs.company_id";

/// One row mapper for every list. Positional indices are fragile by nature, so
/// there is exactly one place that knows them.
fn read_triage_rows(conn: &Connection, sql: &str) -> SqlResult<Vec<TriageRow>> {
    read_triage_rows_with(conn, sql, &[])
}

/// The same reader, for a list whose WHERE clause has values to bind.
///
/// Search is the only caller that needs this: every other list is a fixed
/// string. It exists so the one query built from typed text binds its values
/// instead of interpolating them.
fn read_triage_rows_with(
    conn: &Connection,
    sql: &str,
    params: &[&dyn rusqlite::ToSql],
) -> SqlResult<Vec<TriageRow>> {
    let mut stmt = conn.prepare(sql)?;
    let rows = stmt.query_map(params, |r| {
        Ok(TriageRow {
            job: Job {
                key: r.get(0)?,
                company_id: r.get(1)?,
                title: r.get(2)?,
                location: r.get(3)?,
                remote: r.get(4)?,
                remote_evidence: r.get(5)?,
                salary_min: r.get(6)?,
                salary_max: r.get(7)?,
                currency: r.get(8)?,
                hourly_rate: r.get(9)?,
                url: r.get(10)?,
                posted_at: r.get(11)?,
                fetched_at: r.get(12)?,
                // The FULL text, which a list does not load. Filled in by
                // ensure_description when the row is opened.
                description: None,
                score: r.get(14)?,
                screen_reasons: r.get(15)?,
                qualified: r.get::<_, i64>(16)? != 0,
                source: r.get(17)?,
                verdict: r.get(18)?,
                coverage_pct: r.get(19)?,
                missing_skills: r.get(20)?,
                repost_note: r.get(21)?,
                repost_of: r.get(31)?,
                apply_kind: r.get(32)?,
                alt_reason: r.get(33)?,
                delisted_at: r.get(22)?,
                requirements_summary: r.get(23)?,
                duplicate_reason: r.get(24)?,
            },
            description_preview: r.get(13)?,
            company_name: r.get(25)?,
            status: r.get(26)?,
            note: r.get(27)?,
            status_updated: r.get(28)?,
            applied_at: r.get(29)?,
            history: r
                .get::<_, Option<String>>(30)?
                .map(|joined| {
                    joined
                        .split(',')
                        .filter(|s| !s.is_empty())
                        .map(str::to_string)
                        .collect()
                })
                .unwrap_or_default(),
            other_locations: Vec::new(),
        })
    })?;
    rows.collect()
}

/// Rows for whichever list is on screen.
///
/// TRIAGE is the working queue: qualified rows the person has not closed out.
/// ALL JOBS is a different question - EVERYTHING still worth seeing, taken-down
/// postings included.
///
/// ALL JOBS USED TO HIDE TAKEN-DOWN ROWS unless an application was in flight.
/// That worked while "taken down" was the end of the road, and stopped working
/// the moment a closure started writing a status: a posting nobody had acted on
/// left Triage as No longer open and left All jobs at the same instant, landing
/// on no screen at all. There was then no way back to it - including no way to
/// say "I did apply to this, before it was pulled".
///
/// So the three lists now answer three questions and every row is on at least
/// one of them: Triage is live work, All jobs is everything, Removed is what
/// the person explicitly discarded. Asserted by
/// a_taken_down_row_nobody_acted_on_is_still_reachable_in_all_jobs.
pub fn list_jobs_for(
    conn: &Connection,
    scope: crate::app::ListScope,
    show_all: bool,
    search_terms: &[String],
) -> SqlResult<Vec<TriageRow>> {
    match scope {
        crate::app::ListScope::Module(module) => list_module_jobs(conn, module),
        crate::app::ListScope::Triage => list_triage_jobs(conn, show_all),
        crate::app::ListScope::All => list_all_jobs(conn),
        crate::app::ListScope::Retired => list_retired_jobs(conn),
        crate::app::ListScope::Duplicates => list_duplicate_jobs(conn),
        // The terms arrive as a parameter rather than inside the variant:
        // ListScope is Copy, and a Vec in it would force that off the enum and
        // through every place that copies the current scope.
        crate::app::ListScope::Search => search_jobs(conn, search_terms),
    }
}

/// Every job matching all of these terms, across every field worth searching.
///
/// SCOPE IS DELIBERATELY EVERYTHING - including taken-down, removed and
/// duplicate rows. The other lists exist to narrow; this one exists to find,
/// and a search that hides the row being hunted for is the one failure it
/// cannot afford. The list shows which pile each result is in, so a hit on a
/// removed posting reads as a removed posting rather than a surprise.
///
/// An empty term list returns nothing rather than everything: "search for
/// nothing" is a person who has not typed yet, and answering it with the whole
/// table would bury the box they are typing into.
pub fn search_jobs(conn: &Connection, terms: &[String]) -> SqlResult<Vec<TriageRow>> {
    let terms: Vec<String> = terms
        .iter()
        .map(|t| t.trim().to_string())
        .filter(|t| !t.is_empty())
        .collect();
    if terms.is_empty() {
        return Ok(Vec::new());
    }

    // One OR-group per term, ANDed together: every word must appear SOMEWHERE
    // in the row, not all in the same field.
    // EVERY FIELD A PERSON CAN SEE ON A ROW. If it is visible in the list or
    // in the opened posting, it is searchable - anything less makes the search
    // look broken to whoever remembers the bit it does not cover.
    //
    // STATUSES ARE DELIBERATELY ABSENT, decided 2026-08-21. A status is one of a
    // fixed handful of values, so matching it returns a category rather than a
    // find, and the words overlap ordinary text - "applied" appears in plenty
    // of descriptions - which makes every other search noisier. Those belong to
    // a filter. The status NOTE stays: it is free text somebody typed, and
    // often the only place a detail lives.
    //
    // The salary and score columns are deliberately absent. LIKE over a number
    // matches digit sequences, so "90" would return every row with 90 anywhere
    // in a salary or a score. Ranges want their own control.
    let group = "(jobs.title LIKE ?1 OR companies.name LIKE ?1 OR jobs.location LIKE ?1 \
                  OR jobs.description LIKE ?1 OR jobs.requirements_summary LIKE ?1 \
                  OR jobs.url LIKE ?1 OR jobs.source LIKE ?1 OR job_status.note LIKE ?1 \
                  OR jobs.repost_note LIKE ?1 OR jobs.key LIKE ?1 \
                  OR jobs.screen_reasons LIKE ?1 OR jobs.missing_skills LIKE ?1)";
    let clauses: Vec<String> = (1..=terms.len())
        .map(|i| group.replace("?1", &format!("?{i}")))
        .collect();

    let sql = format!(
        "{SELECT_TRIAGE_COLUMNS} WHERE {} ORDER BY jobs.score DESC, jobs.key ASC",
        clauses.join(" AND ")
    );

    // %term% - a person searching for "engineer" expects to find "Engineering",
    // and searching for a company expects to find it mid-title.
    let patterns: Vec<String> = terms.iter().map(|t| format!("%{t}%")).collect();
    let bound: Vec<&dyn rusqlite::ToSql> =
        patterns.iter().map(|p| p as &dyn rusqlite::ToSql).collect();
    read_triage_rows_with(conn, &sql, &bound)
}

/// Split what the person typed into terms, respecting "quoted phrases".
///
/// A quoted phrase is matched as one string, which is the difference between
/// finding a role called Data Engineer and finding every row that mentions
/// data and, separately, engineer.
pub fn parse_search_terms(query: &str) -> Vec<String> {
    let mut terms = Vec::new();
    let mut current = String::new();
    let mut in_quotes = false;
    for ch in query.chars() {
        match ch {
            '"' => {
                in_quotes = !in_quotes;
                if !in_quotes && !current.trim().is_empty() {
                    terms.push(current.trim().to_string());
                    current.clear();
                }
            }
            c if c.is_whitespace() && !in_quotes => {
                if !current.trim().is_empty() {
                    terms.push(current.trim().to_string());
                }
                current.clear();
            }
            c => current.push(c),
        }
    }
    if !current.trim().is_empty() {
        terms.push(current.trim().to_string());
    }
    terms
}

pub fn list_all_jobs(conn: &Connection) -> SqlResult<Vec<TriageRow>> {
    let sql = format!(
        "{SELECT_TRIAGE_COLUMNS}
         WHERE jobs.qualified = 1
           AND jobs.retired_at IS NULL
           AND jobs.duplicate_of IS NULL
         ORDER BY jobs.score DESC, jobs.key ASC"
    );
    read_triage_rows(conn, &sql)
}

/// Rows and count for one dashboard module.
///
/// THE COUNT AND THE LIST SHARE THE WHERE CLAUSE, and both apply the same two
/// exclusions on top. That is what makes a card of 53 open a list of 53: they
/// are not two readings of the same idea, they are one statement used twice.
///
/// RETIRED AND GROUPED ROWS ARE OUT of every module. A row the person removed
/// from their lists, or one folded behind its twin as a duplicate, is not
/// something a count on the landing screen should be quietly including - they
/// have their own screens for exactly that reason.
const MODULE_EXCLUSIONS: &str = "jobs.retired_at IS NULL AND jobs.duplicate_of IS NULL";

pub fn list_module_jobs(
    conn: &Connection,
    module: crate::modules::Module,
) -> SqlResult<Vec<TriageRow>> {
    let sql = format!(
        "{SELECT_TRIAGE_COLUMNS}
         WHERE {MODULE_EXCLUSIONS} AND ({clause})
         ORDER BY jobs.score DESC, jobs.key ASC",
        clause = module.where_clause(),
    );
    read_triage_rows(conn, &sql)
}

pub fn count_module(conn: &Connection, module: crate::modules::Module) -> SqlResult<i64> {
    let sql = format!(
        "SELECT COUNT(*) FROM jobs
         LEFT JOIN job_status ON job_status.key = jobs.key
         WHERE {MODULE_EXCLUSIONS} AND ({clause})",
        clause = module.where_clause(),
    );
    conn.query_row(&sql, [], |r| r.get(0))
}

/// What the person removed, newest first.
///
/// No qualified/verdict filter: they chose these rows individually, so the
/// screening that would have hidden some of them has already been overruled
/// by hand. Ordered by when they went rather than by score, because the row
/// somebody wants back is nearly always the one they just removed.
pub fn list_retired_jobs(conn: &Connection) -> SqlResult<Vec<TriageRow>> {
    let sql = format!(
        "{SELECT_TRIAGE_COLUMNS}
         WHERE jobs.retired_at IS NOT NULL
         ORDER BY jobs.retired_at DESC, jobs.key ASC"
    );
    read_triage_rows(conn, &sql)
}

/// What was grouped behind something else, newest first.
///
/// No qualified filter, same as the removed view: the grouping is a judgement
/// about identity, not about fit, and a person auditing it wants everything it
/// touched.
pub fn list_duplicate_jobs(conn: &Connection) -> SqlResult<Vec<TriageRow>> {
    let sql = format!(
        "{SELECT_TRIAGE_COLUMNS}
         WHERE jobs.duplicate_of IS NOT NULL
         ORDER BY jobs.fetched_at DESC, jobs.key ASC"
    );
    read_triage_rows(conn, &sql)
}

pub fn duplicate_count(conn: &Connection) -> SqlResult<i64> {
    conn.query_row(
        "SELECT COUNT(*) FROM jobs WHERE duplicate_of IS NOT NULL",
        [],
        |r| r.get(0),
    )
}

/// Undo a grouping. The row returns to the lists exactly as it was.
///
/// One parameterised statement per key rather than a built IN clause: a
/// hand-made selection is tens of rows, so the cost is nothing and there is no
/// dynamic SQL to get wrong.
pub fn ungroup(conn: &Connection, keys: &[String]) -> SqlResult<usize> {
    let mut changed = 0;
    for key in keys {
        changed += conn.execute(
            "UPDATE jobs SET duplicate_of = NULL, duplicate_reason = NULL WHERE key = ?1",
            params![key],
        )?;
    }
    Ok(changed)
}

/// When each named source last delivered.
///
/// Same MAX(fetched_at) the dashboard's source panel is built from, exposed so
/// the jobs list can answer the lateness question identically. Two screens
/// computing "is this collector late" from two different readings is how they
/// come to disagree in front of somebody.
pub fn source_last_seen(
    conn: &Connection,
    ids: &[String],
) -> SqlResult<Vec<(String, Option<String>)>> {
    if ids.is_empty() {
        return Ok(Vec::new());
    }
    let holes = vec!["?"; ids.len()].join(", ");
    let sql = format!(
        "SELECT source, MAX(fetched_at) FROM jobs \
         WHERE source IN ({holes}) GROUP BY source"
    );
    let mut stmt = conn.prepare(&sql)?;
    let rows = stmt.query_map(rusqlite::params_from_iter(ids.iter()), |r| {
        Ok((r.get::<_, String>(0)?, r.get::<_, Option<String>>(1)?))
    })?;
    rows.collect()
}

/// When each collector's handoff was last taken in, as the engine recorded it.
///
/// THE ENGINE'S OWN RECORD, not a guess from the rows. This used to be
/// MAX(fetched_at) for the source, which answers "when did a row from them
/// last arrive" - a different question, and the same answer only while every
/// handoff carries jobs. A file of nothing but closures imports perfectly and
/// moves no row's timestamp, so the screen reported it as never read.
pub fn collector_taken_in(
    conn: &Connection,
    ids: &[String],
) -> SqlResult<Vec<(String, Option<String>)>> {
    let mut out = Vec::new();
    for id in ids {
        let key = format!("ingest_taken:{id}");
        let when: Option<String> = conn
            .query_row("SELECT value FROM meta WHERE key = ?1", [&key], |r| r.get(0))
            .optional()?;
        out.push((id.clone(), when));
    }
    Ok(out)
}

/// This machine's offset from UTC right now, in seconds.
///
/// Asked of SQLite, which asks the operating system - so it is right on either
/// side of a daylight-saving change and no date crate had to be added to
/// answer one question. ('localtime' shifts the value and leaves it labelled
/// UTC, so the difference between the two IS the offset.)
///
/// Falls back to 0 if the query fails, which only delays a staleness badge by
/// the offset. Guessing an offset would be worse than admitting none.
pub fn local_offset_secs(conn: &Connection) -> i64 {
    conn.query_row(
        "SELECT CAST(strftime('%s', 'now', 'localtime') AS INTEGER) \
         - CAST(strftime('%s', 'now') AS INTEGER)",
        [],
        |r| r.get(0),
    )
    .unwrap_or(0)
}

/// When the BUILT-IN boards were last read, ignoring any external collector.
///
/// MAX(fetched_at) over everything answered the wrong question. The board sweep
/// runs whenever the app runs, so it is nearly always the most recent thing in
/// the table - which meant a handoff collector could be dead for a week while
/// the header still said "Collected today" every morning.
///
/// The ids come from the engine's collector list, so a source only counts as
/// external once somebody has actually configured it. On a profile with none,
/// this is MAX(fetched_at) and the wording is unchanged.
///
/// Ids are bound as parameters rather than pasted in: they come out of a config
/// file a person edits, which is not a place to take SQL from on trust.
pub fn boards_last_collected(
    conn: &Connection,
    external_ids: &[String],
) -> SqlResult<Option<String>> {
    if external_ids.is_empty() {
        return conn.query_row("SELECT MAX(fetched_at) FROM jobs", [], |r| r.get(0));
    }
    let holes = vec!["?"; external_ids.len()].join(", ");
    let sql = format!(
        "SELECT MAX(fetched_at) FROM jobs \
         WHERE source IS NULL OR source NOT IN ({holes})"
    );
    let params = rusqlite::params_from_iter(external_ids.iter());
    conn.query_row(&sql, params, |r| r.get(0))
}

/// Qualified jobs joined against their human status. `show_all` turns off
/// the default filter that hides settled rows so the queue reads as "what is
/// still open to act on" unless asked otherwise.
pub fn list_triage_jobs(conn: &Connection, show_all: bool) -> SqlResult<Vec<TriageRow>> {
    let base = format!(
        "{SELECT_TRIAGE_COLUMNS} WHERE jobs.qualified = 1 AND jobs.retired_at IS NULL
           AND jobs.duplicate_of IS NULL"
    );
    // THE SETTLED-STATUS LIST IS NO LONGER BUILT HERE. It was computed into a
    // discarded `_hidden_list` on every call, with a comment saying it was
    // kept because the "show finished jobs" toggle still means "including the
    // settled ones" - which is true, and is a fact about the WORDING rather
    // than a reason to build a SQL fragment and throw it away. The filter
    // below tests for a status existing at all; see why directly under it.
    // A PULLED POSTING NOBODY EVER TOUCHED IS NOT A DECISION. Triage named
    // qualified, retired and duplicate_of and said nothing about delisted_at,
    // so a job the employer had taken down stayed in the decide-pile for ever -
    // 76 of 726 rows on a real profile when this was measured.
    //
    // ONLY THE UNTOUCHED ONES GO. Of those 76, 41 carried no status at all and
    // 35 were APPLIED - somebody applied and the listing came down
    // afterwards. That second group is the event the dashboard interrupts
    // someone for ("N jobs you applied to have been taken down"); dropping it
    // here would be losing the thing rather than tidying it. Same line the
    // status doughnut was corrected on: never-applied and gone is noise,
    // applied and gone is news.
    // ANY STATUS AT ALL MEANS DECIDED. Applied belongs to the Pipeline, Pass
    // is a decision, and every settled status was already leaving - so the
    // test is simply whether a status exists, not which one it is.
    //
    // Measured before the change: 754 rows, 684 with no status and 70 Applied.
    // Only the applications move.
    // TWO CONDITIONS, AND THAT IS ALL IT IS. Once "no status" is required, the
    // old clause about taken-down-and-untouched collapses into "not taken
    // down" - a row with a status has already gone, whether it was pulled or
    // not. Writing the collapsed form keeps it from reading as a distinction
    // it can no longer make.
    let filter = " AND job_status.status IS NULL AND jobs.delisted_at IS NULL".to_string();
    let order = " ORDER BY jobs.score DESC, jobs.key ASC";

    let sql = if show_all {
        format!("{base}{order}")
    } else {
        format!("{base}{filter}{order}")
    };

    read_triage_rows(conn, &sql)
}

// Setting a status writes job_status (current state, upserted) and
// job_status_log (append-only history) in the same call, so the two tables can
// never drift out of step. set_status_with below is the only way in; a
// no-offer-terms shorthand lives in the test module as `mark`, because the
// prompt that gathers the note gathers the terms in the same breath and no
// caller in the app has one without the other.

/// What an Offer was, beyond whatever the person wrote about it.
///
/// Both optional: the prompt never blocks on them, because a person recording
/// an offer at 11pm should not have to go and find the number before the app
/// will let them save the fact that they got one.
#[derive(Debug, Clone, Default)]
pub struct OfferTerms {
    pub pay: Option<String>,
    pub offer_date: Option<String>,
}

impl OfferTerms {
    /// Trims and drops the empties, so a field the person tabbed through
    /// stores NULL rather than "".
    ///
    /// The distinction is load-bearing: `unlatched_checks` counts offers with
    /// no pay recorded, and an empty string would be counted as an answer.
    pub fn from_inputs(pay: &str, offer_date: &str) -> Self {
        let clean = |s: &str| {
            let t = s.trim();
            (!t.is_empty()).then(|| t.to_string())
        };
        Self {
            pay: clean(pay),
            offer_date: clean(offer_date),
        }
    }
}

/// Sets the status and records what was offered with it.
pub fn set_status_with(
    conn: &Connection,
    key: &str,
    status: &str,
    note: Option<&str>,
    terms: &OfferTerms,
) -> SqlResult<()> {
    let now = date::now_iso();
    conn.execute(
        // `note` is the note for THIS transition, not the job's standing note.
        //
        // COALESCE, so a status change carrying no note LEAVES the existing one
        // alone instead of erasing it. Passing the old note back in was the
        // previous approach and it was worse than either: it wrote a note
        // written about "Applied" into the log against "Offer", mislabelling
        // the history it was supposed to preserve.
        //
        // job_status.note is therefore the LATEST note. job_status_log is the
        // record of which note belongs to which transition, and is the truth.
        "INSERT INTO job_status (key, status, note, updated) VALUES (?1, ?2, ?3, ?4)
         ON CONFLICT(key) DO UPDATE SET status = excluded.status,
                                         note = COALESCE(excluded.note, job_status.note),
                                         updated = excluded.updated",
        params![key, status, note, now],
    )?;
    conn.execute(
        // Pay and the offer date belong to the LOG row, not to job_status:
        // they describe one event. A second offer from the same employer after
        // a re-application is a different number, and storing it as current
        // state would overwrite the first.
        "INSERT INTO job_status_log (key, status, note, at, pay, offer_date)
         VALUES (?1, ?2, ?3, ?4, ?5, ?6)",
        params![key, status, note, now, terms.pay, terms.offer_date],
    )?;
    Ok(())
}

/// A note that is not about a status change.
///
/// APPENDS. The rule for every note in the app: nothing a person wrote is
/// ever replaced by the next thing they write. The previous implementation
/// re-wrote the job's status to carry a new note, which appended a duplicate
/// row to the status history saying the status had changed to the value it
/// already had - so the timeline gained a transition that never happened every
/// time somebody jotted something down.
pub fn add_note(conn: &Connection, key: &str, note: &str) -> SqlResult<()> {
    conn.execute(
        "INSERT INTO job_note (key, note, at) VALUES (?1, ?2, ?3)",
        params![key, note, date::now_iso()],
    )?;
    // The standing note is what the list column shows, so the most recent
    // thing written is what a person sees without opening the job. The history
    // in job_note is what keeps the earlier ones.
    conn.execute(
        "UPDATE job_status SET note = ?2 WHERE key = ?1",
        params![key, note],
    )?;
    Ok(())
}

#[derive(Debug, Clone)]
pub struct Note {
    pub key: String,
    pub note: String,
    pub at: String,
}

pub fn list_notes(conn: &Connection) -> SqlResult<Vec<Note>> {
    let mut stmt = conn.prepare("SELECT key, note, at FROM job_note ORDER BY key ASC, id ASC")?;
    let rows = stmt.query_map([], |r| {
        Ok(Note {
            key: r.get(0)?,
            note: r.get(1)?,
            at: r.get(2)?,
        })
    })?;
    rows.collect()
}

/// Removes a row's status entirely, for "back to not set".
///
/// The log keeps a row saying it was cleared. job_status_log is append-only
/// on purpose - it is the person's own history, and an undo is part of that
/// history rather than a reason to pretend the earlier decision never
/// happened.
pub fn clear_status(conn: &Connection, key: &str) -> SqlResult<()> {
    let now = date::now_iso();
    conn.execute("DELETE FROM job_status WHERE key = ?1", params![key])?;
    // NULL, NOT AN EMPTY STRING, because job_status_log is shared with the
    // engine and the engine documents what a cleared row looks like: "a null
    // 'to' means the status was cleared" (status.py). This wrote '' for the
    // same event, so one table carried two spellings of one fact depending on
    // which half the person clicked in, and an exported history said `to: ""`
    // for a desktop clear and `to: null` for a CLI one.
    //
    // MEASURED BEFORE CHANGING IT: nothing observable differed today - prune
    // treats both as touched, and a re-import clears the status either way. It
    // is the contract that was wrong, not the behaviour, and a contract only
    // one side keeps is the kind that is discovered by the code that assumed
    // it.
    conn.execute(
        "INSERT INTO job_status_log (key, status, note, at) VALUES (?1, NULL, NULL, ?2)",
        params![key, now],
    )?;
    Ok(())
}

/// Take rows out of the person's lists. Returns how many moved.
///
/// HIDES rather than deletes - the same result as deleting, with a way back,
/// because a bulk action over a multi-select is one misclick
/// from erasing months of application history and that history is the whole
/// point of a job tracker. Everything is kept: the status, the append-only
/// log, the row's part in the repost record.
///
/// Also sticky against the collector - `retired_at` is not a column a collect
/// writes, so a job still live on its board is re-read and stays hidden.
pub fn retire(conn: &Connection, keys: &[String]) -> SqlResult<usize> {
    write_retired(conn, keys, Some(date::now_iso()))
}

/// Put retired rows back where they were.
pub fn restore(conn: &Connection, keys: &[String]) -> SqlResult<usize> {
    write_retired(conn, keys, None)
}

/// Record that the employer took these postings down.
///
/// TWO FACTS, AND ONLY ONE OF THEM IS THE PERSON'S. `delisted_at` is what the
/// EMPLOYER did, so it is always written. The status is what the PERSON
/// decided, so it is only filled in where they never decided anything:
///
///   * a job already in flight - applied, interviewed, offer, accepted - keeps
///     its status untouched and simply gains the fact that the advert closed.
///     Flipping it would erase that an application was ever sent, which is the
///     one piece of history a job search cannot afford to lose, and the funnel
///     would lose the row with it.
///   * a job nobody ever acted on has nothing to lose, so it is marked
///     `closed` - "No longer open" - and reads as dead rather than as blank.
///
/// The rows then leave the working lists through the filter that already
/// exists in `list_all_jobs`, which hides delisted rows EXCEPT the in-flight
/// ones. Nothing is deleted; All jobs still holds every one of them.
///
/// `closed` is deliberately not in `status::FLOW`, so it cannot be chosen from
/// any dropdown. It is a word the app writes, never one a person picks.
pub fn mark_taken_down(conn: &Connection, keys: &[String]) -> SqlResult<usize> {
    if keys.is_empty() {
        return Ok(0);
    }
    let stamp = date::now_iso();
    let mut touched = 0;
    for key in keys {
        conn.execute(
            "UPDATE jobs SET delisted_at = COALESCE(delisted_at, ?1) WHERE key = ?2",
            params![stamp, key],
        )?;
        // A job with no row in job_status has never been given a status,
        // which is exactly the case this is looking for - so "no row" and
        // "empty status" have to mean the same thing here rather than one of
        // them being an error.
        let held: String = conn
            .query_row(
                "SELECT status FROM job_status WHERE key = ?1",
                params![key],
                |r| r.get(0),
            )
            .unwrap_or_default();
        // ONLY ONTO "NOT SET". This asked `!in_flight.contains(held)`, which is
        // a wider net than it looks: in_flight is the RUNGS (applied,
        // interviewed, offer), so No Offer and Declined Offer are not in it and
        // a recorded rejection was being overwritten with "No longer open" the
        // moment the employer pulled the ad. That is the person's own record of
        // how it ended, destroyed by a posting expiring - the exact thing the
        // delisted_at-is-a-column design exists to prevent.
        //
        // Empty is the whole of the target: a row nobody ever judged. Anything
        // they did record, at any stage, is theirs and stays.
        if held.is_empty() {
            set_status_with(conn, key, crate::status::CLOSED, None, &OfferTerms::default())?;
        }
        touched += 1;
    }
    Ok(touched)
}

fn write_retired(conn: &Connection, keys: &[String], at: Option<String>) -> SqlResult<usize> {
    if keys.is_empty() {
        return Ok(0);
    }
    // Built from the COUNT of keys, never from their contents: the values go
    // through params, so a key can never reach the statement text.
    let marks = std::iter::repeat_n("?", keys.len()).collect::<Vec<_>>().join(",");
    let sql = format!("UPDATE jobs SET retired_at = ?1 WHERE key IN ({marks})");
    let mut values: Vec<&dyn rusqlite::ToSql> = Vec::with_capacity(keys.len() + 1);
    values.push(&at);
    for key in keys {
        values.push(key);
    }
    conn.execute(&sql, values.as_slice())
}

/// Copy one person's resolved employers into a new search of theirs.
///
/// WHY THIS EXISTS AT ALL: discovery is the expensive step by a wide margin -
/// a five-profile refresh ran for hours, almost entirely in employer probing.
/// Employer -> ATS resolution is a fact about the EMPLOYER (a hospital group
/// runs whatever it runs), not about the seeker or the job title, so a person's
/// second search must not pay for it twice.
///
/// FAILURES ARE CARRIED FORWARD DELIBERATELY. 22 of one seeker's 27 employers
/// had no reachable board. Copying only the hits would make every new search
/// re-burn the same hours rediscovering the same absence. They stay re-probeable
/// on demand; they just must not be re-probed automatically.
///
/// COMPANIES ONLY. Not jobs, not job_status, not job_status_log: postings go
/// stale, and screening output plus the person's own pipeline decisions are
/// strictly per-search. A second search exists precisely because it is a
/// different hunt.
///
/// Existing rows in the destination win, so seeding a search twice is a no-op
/// rather than a way to overwrite work already done in it.
pub fn seed_companies_from(dest: &Connection, source_db: &Path) -> SqlResult<usize> {
    dest.execute(
        "ATTACH DATABASE ?1 AS source",
        rusqlite::params![source_db.to_string_lossy()],
    )?;
    // PROVENANCE TRAVELS WITH THE EMPLOYER. A starter employer copied into a
    // second search is still a starter employer, and "collect the seeded ones"
    // has to find it there too.
    //
    // Probed rather than assumed: the source is ATTACHed as a file, not opened
    // through connect(), so migrate() has never run on it. A profile last
    // touched by a build older than the origin column genuinely does not have
    // it, and selecting a missing column is a hard SQLite error rather than a
    // null - the same trap ADDED_COMPANY_COLUMNS exists to keep out of the
    // triage query. Those rows arrive with no origin, which is what they are.
    let source_has_origin = dest
        .prepare("SELECT origin FROM source.companies LIMIT 0")
        .is_ok();
    let copied = dest.execute(
        if source_has_origin {
            "INSERT OR IGNORE INTO companies
                 (name, domain, careers_url, ats, ats_ref, probe_status, last_probed, origin)
             SELECT name, domain, careers_url, ats, ats_ref, probe_status, last_probed, origin
             FROM source.companies"
        } else {
            "INSERT OR IGNORE INTO companies
                 (name, domain, careers_url, ats, ats_ref, probe_status, last_probed)
             SELECT name, domain, careers_url, ats, ats_ref, probe_status, last_probed
             FROM source.companies"
        },
        [],
    );
    // Detach even if the copy failed, or the connection keeps the source file
    // locked and the next seed of the same search cannot open it.
    dest.execute_batch("DETACH DATABASE source")?;
    copied
}

/// How many rows are currently hidden, so the way back can announce itself.
/// A pile you cannot see the size of is one you forget you have.
pub fn retired_count(conn: &Connection) -> SqlResult<i64> {
    conn.query_row(
        "SELECT COUNT(*) FROM jobs WHERE retired_at IS NOT NULL",
        [],
        |r| r.get(0),
    )
}

/// Which of these the person recorded an application for.
///
/// Read before a bulk retire is confirmed. Removing a job you applied to
/// loses the row that matters most - the one you go back to when somebody
/// finally replies - so it earns a sentence of warning rather than a silent
/// count. Reads the LOG, so a job since marked No Offer still counts as one
/// that was applied to.
pub fn applied_among(conn: &Connection, keys: &[String]) -> SqlResult<usize> {
    if keys.is_empty() {
        return Ok(0);
    }
    let marks = std::iter::repeat_n("?", keys.len()).collect::<Vec<_>>().join(",");
    // Every status that PROVES an application was made, which is what the
    // warning is about - including the ones that end it. Removing a job you
    // were turned down for still removes the record of having applied.
    let applied: Vec<&str> = crate::status::FLOW
        .iter()
        .filter(|s| s.rung.is_some())
        .map(|s| s.value)
        .collect();
    let statuses = crate::status::sql_list(&applied);
    let sql = format!(
        "SELECT COUNT(DISTINCT key) FROM job_status_log WHERE key IN ({marks})
         AND status IN ({statuses})"
    );
    let values: Vec<&dyn rusqlite::ToSql> =
        keys.iter().map(|k| k as &dyn rusqlite::ToSql).collect();
    conn.query_row(&sql, values.as_slice(), |r| r.get(0))
}

#[derive(Debug, Clone, Default)]
pub struct StatusLogEntry {
    pub key: String,
    pub status: Option<String>,
    pub note: Option<String>,
    pub at: Option<String>,
    /// What was offered, on the row that records the offer. Null everywhere
    /// else, which is the honest reading: no other transition has a figure.
    pub pay: Option<String>,
    pub offer_date: Option<String>,
}

pub fn list_status_log(conn: &Connection) -> SqlResult<Vec<StatusLogEntry>> {
    // id is only needed to break ties within a key's chronological order;
    // it does not need to travel into the struct for that.
    let mut stmt = conn.prepare(
        "SELECT key, status, note, at, pay, offer_date
         FROM job_status_log ORDER BY key ASC, id ASC",
    )?;
    let rows = stmt.query_map([], |r| {
        Ok(StatusLogEntry {
            key: r.get(0)?,
            status: r.get(1)?,
            note: r.get(2)?,
            at: r.get(3)?,
            pay: r.get(4)?,
            offer_date: r.get(5)?,
        })
    })?;
    rows.collect()
}

#[derive(Debug, Clone, Default)]
pub struct CurrentStatus {
    pub status: String,
    pub updated: String,
}

pub fn current_statuses(conn: &Connection) -> SqlResult<HashMap<String, CurrentStatus>> {
    let mut stmt = conn.prepare("SELECT key, status, updated FROM job_status")?;
    let rows = stmt.query_map([], |r| {
        Ok((
            r.get::<_, String>(0)?,
            CurrentStatus {
                status: r.get(1)?,
                updated: r.get(2)?,
            },
        ))
    })?;
    let mut map = HashMap::new();
    for row in rows {
        let (k, v) = row?;
        map.insert(k, v);
    }
    Ok(map)
}

/// Descriptions of stored jobs, for the Keywords view's demand report.
/// `qualified_only` mirrors the CLI's default corpus (jobs.qualified, the
/// screener's own flag); unlike list_triage_jobs this never filters on
/// job_status, since a posting's wording does not change based on where
/// the human's pipeline tracking stands.
pub fn job_descriptions(conn: &Connection, qualified_only: bool) -> SqlResult<Vec<String>> {
    let sql = if qualified_only {
        "SELECT description FROM jobs WHERE qualified = 1"
    } else {
        "SELECT description FROM jobs"
    };
    let mut stmt = conn.prepare(sql)?;
    let rows = stmt.query_map([], |r| r.get::<_, Option<String>>(0))?;
    let mut out = Vec::new();
    for row in rows {
        out.push(row?.unwrap_or_default());
    }
    Ok(out)
}

/// Title and company name for every job, keyed by jobs.key. Used by the
/// Pipeline view so its timeline can label each key without a query per
/// row while rendering.
pub fn all_job_info(conn: &Connection) -> SqlResult<HashMap<String, (String, Option<String>)>> {
    let mut stmt = conn.prepare(
        "SELECT jobs.key, jobs.title, companies.name FROM jobs
         LEFT JOIN companies ON companies.id = jobs.company_id",
    )?;
    let rows = stmt.query_map([], |r| {
        Ok((
            r.get::<_, String>(0)?,
            (r.get::<_, String>(1)?, r.get::<_, Option<String>>(2)?),
        ))
    })?;
    let mut map = HashMap::new();
    for row in rows {
        let (k, v) = row?;
        map.insert(k, v);
    }
    Ok(map)
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The shape a database created by an early version of this app has: no
    /// hourly_rate, no verdict, none of the columns added since.
    const OLD_SCHEMA: &str = "
        CREATE TABLE companies (
          id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE,
          domain TEXT, careers_url TEXT, ats TEXT, ats_ref TEXT,
          probe_status TEXT DEFAULT 'new', last_probed TEXT);
        CREATE TABLE jobs (
          key TEXT PRIMARY KEY, company_id INTEGER, title TEXT NOT NULL,
          location TEXT, remote TEXT, remote_evidence TEXT,
          salary_min INTEGER, salary_max INTEGER, currency TEXT,
          url TEXT, posted_at TEXT, fetched_at TEXT, description TEXT,
          score REAL, screen_reasons TEXT,
          qualified INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE job_status (
          key TEXT PRIMARY KEY, status TEXT NOT NULL, note TEXT,
          updated TEXT NOT NULL);
        CREATE TABLE job_status_log (
          id INTEGER PRIMARY KEY, key TEXT, status TEXT, note TEXT, at TEXT);
    ";

    fn old_database() -> Connection {
        let conn = Connection::open_in_memory().unwrap();
        conn.execute_batch(OLD_SCHEMA).unwrap();
        conn.execute(
            "INSERT INTO jobs (key, title, qualified) VALUES ('gh:1', 'Analyst', 1)",
            [],
        )
        .unwrap();
        conn
    }

    #[test]
    fn an_older_database_gains_the_columns_this_app_selects() {
        let conn = old_database();
        conn.execute_batch(SCHEMA_SQL).unwrap();
        ensure_columns(&conn, "jobs", &ADDED_JOB_COLUMNS).unwrap();
        for (name, _) in ADDED_JOB_COLUMNS.iter() {
            let sql = format!("SELECT {name} FROM jobs LIMIT 1");
            assert!(conn.prepare(&sql).is_ok(), "column {name} still missing");
        }
    }

    #[test]
    fn triage_loads_from_a_database_written_by_an_older_version() {
        // The actual regression: before ensure_job_columns, this returned
        // "no such column: jobs.hourly_rate" and the triage table rendered
        // empty with an error banner over it.
        let conn = old_database();
        conn.execute_batch(SCHEMA_SQL).unwrap();
        ensure_columns(&conn, "jobs", &ADDED_JOB_COLUMNS).unwrap();
        let rows = list_triage_jobs(&conn, true).expect("triage query must not fail");
        assert_eq!(rows.len(), 1);
        assert_eq!(rows[0].job.title, "Analyst");
        // Columns the old database never had read as absent, not as zero.
        assert!(rows[0].job.coverage_pct.is_none());
        assert!(rows[0].job.repost_note.is_none());
    }

    /// A board read moments ago, and an external collector three days quiet.
    ///
    /// THE ORDER MATTERS. The board is the NEWEST row, which is the everyday
    /// case: the sweep runs when the app runs. Under the old MAX(fetched_at)
    /// over everything, this profile reported the board's stamp as though the
    /// collector had delivered too.
    fn a_fresh_board_and_a_quiet_collector() -> Connection {
        let conn = Connection::open_in_memory().unwrap();
        conn.execute_batch(SCHEMA_SQL).unwrap();
        ensure_columns(&conn, "jobs", &ADDED_JOB_COLUMNS).unwrap();
        conn.execute(
            "INSERT INTO jobs (key, title, qualified, source, fetched_at) VALUES
             ('gh:1', 'From a board',     1, 'greenhouse', '2026-08-25T13:00:00+00:00'),
             ('im:1', 'From a collector', 1, 'imported',   '2026-08-22T12:00:00+00:00')",
            [],
        )
        .unwrap();
        conn
    }

    #[test]
    fn the_boards_stamp_ignores_an_external_collector() {
        let conn = a_fresh_board_and_a_quiet_collector();
        let external = vec!["imported".to_string()];

        let boards = boards_last_collected(&conn, &external).unwrap();
        assert_eq!(boards.as_deref(), Some("2026-08-25T13:00:00+00:00"));
    }

    #[test]
    fn a_quiet_collector_does_not_drag_the_boards_stamp_backwards() {
        // The mirror image, and the reason this is not just "exclude the
        // newest". If the COLLECTOR is the newest row, the boards line must
        // still report the boards - an older stamp, honestly.
        let conn = Connection::open_in_memory().unwrap();
        conn.execute_batch(SCHEMA_SQL).unwrap();
        ensure_columns(&conn, "jobs", &ADDED_JOB_COLUMNS).unwrap();
        conn.execute(
            "INSERT INTO jobs (key, title, qualified, source, fetched_at) VALUES
             ('gh:1', 'From a board',     1, 'greenhouse', '2026-08-20T13:00:00+00:00'),
             ('im:1', 'From a collector', 1, 'imported',   '2026-08-25T12:00:00+00:00')",
            [],
        )
        .unwrap();

        let boards = boards_last_collected(&conn, &["imported".to_string()]).unwrap();
        assert_eq!(boards.as_deref(), Some("2026-08-20T13:00:00+00:00"));
    }

    #[test]
    fn with_no_collectors_configured_the_wording_is_unchanged() {
        // A fresh install has no external collector, and must behave exactly
        // as it did before this split existed.
        let conn = a_fresh_board_and_a_quiet_collector();
        let boards = boards_last_collected(&conn, &[]).unwrap();
        assert_eq!(boards.as_deref(), Some("2026-08-25T13:00:00+00:00"));
    }

    #[test]
    fn source_last_seen_answers_only_for_the_ids_asked_about() {
        let conn = a_fresh_board_and_a_quiet_collector();
        let seen = source_last_seen(&conn, &["imported".to_string()]).unwrap();

        assert_eq!(seen.len(), 1, "greenhouse was not asked about: {seen:?}");
        assert_eq!(seen[0].0, "imported");
        assert_eq!(seen[0].1.as_deref(), Some("2026-08-22T12:00:00+00:00"));
    }

    #[test]
    fn an_id_carrying_a_quote_is_data_not_syntax() {
        // Collector ids come out of a config file a person edits. Bound as
        // parameters, so this returns nothing rather than failing to parse.
        let conn = a_fresh_board_and_a_quiet_collector();
        let odd = vec!["it's a source".to_string()];

        assert!(source_last_seen(&conn, &odd).unwrap().is_empty());
        assert_eq!(
            boards_last_collected(&conn, &odd).unwrap().as_deref(),
            Some("2026-08-25T13:00:00+00:00"),
            "nothing was excluded, because nothing matched"
        );
    }

    /// Four jobs: still-listed and taken-down, each touched and untouched.
    fn a_board_with_pulled_postings() -> Connection {
        let conn = Connection::open_in_memory().unwrap();
        conn.execute_batch(SCHEMA_SQL).unwrap();
        ensure_columns(&conn, "jobs", &ADDED_JOB_COLUMNS).unwrap();
        conn.execute(
            "INSERT INTO jobs (key, title, qualified, score, delisted_at) VALUES
             ('gh:live-new',   'Live untouched',  1, 90.0, NULL),
             ('gh:live-app',   'Live applied',    1, 80.0, NULL),
             ('gh:gone-new',   'Pulled untouched',1, 70.0, '2026-08-20T00:00:00+00:00'),
             ('gh:gone-app',   'Pulled applied',  1, 60.0, '2026-08-20T00:00:00+00:00')",
            [],
        )
        .unwrap();
        for key in ["gh:live-app", "gh:gone-app"] {
            conn.execute(
                "INSERT INTO job_status (key, status, updated)
                 VALUES (?1, 'applied', '2026-08-19T00:00:00+00:00')",
                [key],
            )
            .unwrap();
        }
        conn
    }

    #[test]
    fn triage_drops_a_pulled_posting_nobody_ever_touched() {
        let conn = a_board_with_pulled_postings();
        let titles: Vec<String> = list_triage_jobs(&conn, false)
            .unwrap()
            .into_iter()
            .map(|r| r.job.title)
            .collect();

        // The clog: the employer pulled it and no decision was ever recorded,
        // so there is nothing left to decide about it.
        assert!(
            !titles.contains(&"Pulled untouched".to_string()),
            "a pulled posting with no status is not a decision: {titles:?}"
        );
    }

    #[test]
    fn triage_holds_only_what_carries_no_decision() {
        // THE RULE: applying moves a job to the Pipeline, Pass takes it
        // out, and a posting pulled before anyone touched it goes too.
        // All three are the same sentence - Triage is what has not been
        // decided on.
        let conn = a_board_with_pulled_postings();
        let titles: Vec<String> = list_triage_jobs(&conn, false)
            .unwrap()
            .into_iter()
            .map(|r| r.job.title)
            .collect();

        assert_eq!(
            titles,
            vec!["Live untouched".to_string()],
            "only the undecided, still-listed posting belongs here: {titles:?}"
        );
    }

    #[test]
    fn an_application_whose_posting_was_pulled_is_not_lost() {
        // WHAT THE OLD TRIAGE TEST WAS PROTECTING, kept and re-aimed. Applying
        // and then watching the listing come down is the event the dashboard
        // interrupts somebody for; it must not be tidied away just because
        // Triage stopped being the place it appears.
        //
        // Deleting that assertion because the behaviour moved is how a suite
        // quietly stops guarding the thing it was written for.
        let conn = a_board_with_pulled_postings();
        let all: Vec<String> = list_all_jobs(&conn)
            .unwrap()
            .into_iter()
            .map(|r| r.job.title)
            .collect();

        assert!(
            all.contains(&"Pulled applied".to_string()),
            "the application is still reachable in All jobs: {all:?}"
        );
        assert!(all.contains(&"Live applied".to_string()), "{all:?}");
    }

    #[test]
    fn show_all_still_reaches_the_pulled_postings() {
        // Hidden, not unreachable. "show finished jobs" lifts the status filter
        // and this clause with it, so the pile is one toggle away.
        let conn = a_board_with_pulled_postings();
        let titles: Vec<String> = list_triage_jobs(&conn, true)
            .unwrap()
            .into_iter()
            .map(|r| r.job.title)
            .collect();

        assert_eq!(titles.len(), 4, "show_all must hide nothing: {titles:?}");
        assert!(titles.contains(&"Pulled untouched".to_string()));
    }

    /// Two jobs: one nobody has touched, one already applied to.
    fn touched_and_untouched() -> Connection {
        let conn = Connection::open_in_memory().unwrap();
        conn.execute_batch(SCHEMA_SQL).unwrap();
        ensure_columns(&conn, "jobs", &ADDED_JOB_COLUMNS).unwrap();
        conn.execute(
            "INSERT INTO jobs (key, title, qualified, score) VALUES
             ('gh:untouched', 'Analyst', 1, 90.0),
             ('gh:applied', 'Engineer', 1, 80.0)",
            [],
        )
        .unwrap();
        set_status_with(&conn, "gh:applied", "applied", None, &OfferTerms::default())
            .unwrap();
        conn
    }

    /// The employer closing an advert must never undo what the person did.
    #[test]
    fn taken_down_keeps_the_status_of_a_job_already_applied_to() {
        let conn = touched_and_untouched();
        mark_taken_down(&conn, &["gh:applied".to_string()]).unwrap();

        let status: String = conn
            .query_row(
                "SELECT status FROM job_status WHERE key = 'gh:applied'",
                [],
                |r| r.get(0),
            )
            .unwrap();
        assert_eq!(status, "applied", "an application survives the advert closing");

        let delisted: Option<String> = conn
            .query_row("SELECT delisted_at FROM jobs WHERE key = 'gh:applied'", [], |r| {
                r.get(0)
            })
            .unwrap();
        assert!(delisted.is_some(), "and the closure is recorded beside it");

        // Still in the working list, because a dead application is something
        // the person has to be able to see and chase.
        let keys: Vec<String> = list_all_jobs(&conn)
            .unwrap()
            .into_iter()
            .map(|r| r.job.key)
            .collect();
        assert!(keys.contains(&"gh:applied".to_string()));
    }

    /// Every row is on at least one list. The one that used to fall through.
    ///
    /// A posting nobody acted on, then taken down, gets the closed status - so
    /// Triage hides it as settled. All jobs USED TO hide it too, unless an
    /// application was in flight. Both halves were defensible and together they
    /// left the row on no screen at all, with no way to say "I did apply to
    /// this, before it was pulled".
    ///
    /// Positive control, run 2026-08-15: restoring the old delisted clause to
    /// list_all_jobs fails this with left: false, right: true.
    #[test]
    fn a_taken_down_row_nobody_acted_on_is_still_reachable_in_all_jobs() {
        let conn = touched_and_untouched();
        mark_taken_down(&conn, &["gh:untouched".to_string()]).unwrap();

        // Gone from the working queue - that is the decluttering.
        let triage: Vec<String> = list_triage_jobs(&conn, false)
            .unwrap()
            .into_iter()
            .map(|r| r.job.key)
            .collect();
        assert!(
            !triage.contains(&"gh:untouched".to_string()),
            "a taken-down row nobody acted on leaves Triage"
        );

        // Still reachable, so the status can still be corrected afterwards.
        let all: Vec<String> = list_all_jobs(&conn)
            .unwrap()
            .into_iter()
            .map(|r| r.job.key)
            .collect();
        assert!(
            all.contains(&"gh:untouched".to_string()),
            "and is still on All jobs, or there is no way back to it"
        );
    }

    /// A recorded rejection is a decision, and the advert coming down later
    /// must not erase it.
    ///
    /// THIS IS A REGRESSION TEST FOR A SHIPPED BUG, found 2026-08-15. The guard
    /// was `!in_flight.contains(held)`, and in_flight is only the rungs
    /// (applied, interviewed, offer) - so No Offer and Declined Offer fell
    /// through it and were rewritten to "No longer open". Someone who had
    /// recorded being turned down would lose that the moment the posting
    /// expired, and nothing anywhere would say it had happened.
    ///
    /// Positive control, run before the fix: this test FAILS on the old rule
    /// with left: "closed", right: "no_offer".
    #[test]
    fn taken_down_does_not_overwrite_a_rejection_the_person_recorded() {
        let conn = touched_and_untouched();
        set_status_with(&conn, "gh:untouched", "no_offer", None, &OfferTerms::default())
            .unwrap();

        mark_taken_down(&conn, &["gh:untouched".to_string()]).unwrap();

        let status: String = conn
            .query_row(
                "SELECT status FROM job_status WHERE key = 'gh:untouched'",
                [],
                |r| r.get(0),
            )
            .unwrap();
        assert_eq!(
            status, "no_offer",
            "a rejection the person recorded survives the advert closing"
        );

        // And the closure is still recorded beside it, because both facts are
        // true and the column exists so they need not compete.
        let delisted: Option<String> = conn
            .query_row(
                "SELECT delisted_at FROM jobs WHERE key = 'gh:untouched'",
                [],
                |r| r.get(0),
            )
            .unwrap();
        assert!(delisted.is_some());
    }

    /// The noise case: a posting nobody ever acted on, now gone.
    #[test]
    fn taken_down_closes_a_job_nobody_acted_on_and_drops_it_from_the_list() {
        let conn = touched_and_untouched();
        mark_taken_down(&conn, &["gh:untouched".to_string()]).unwrap();

        let status: String = conn
            .query_row(
                "SELECT status FROM job_status WHERE key = 'gh:untouched'",
                [],
                |r| r.get(0),
            )
            .unwrap();
        assert_eq!(status, crate::status::CLOSED);
        assert_eq!(
            crate::status::label(&status),
            "Expired",
            "and it reads as words, not as a raw value"
        );

        // THE WORKING LIST IS TRIAGE, and that is the one it has to leave.
        //
        // This asserted against list_all_jobs, which passed only because All
        // jobs hid taken-down rows as well - so the row left BOTH lists and
        // the test read that as success. Decluttering the queue and making a
        // row unreachable are different outcomes, and this could not tell them
        // apart. It now asserts each list separately, and the reachability half
        // is a_taken_down_row_nobody_acted_on_is_still_reachable_in_all_jobs.
        let triage: Vec<String> = list_triage_jobs(&conn, false)
            .unwrap()
            .into_iter()
            .map(|r| r.job.key)
            .collect();
        assert!(
            !triage.contains(&"gh:untouched".to_string()),
            "it stops being noise in the working list"
        );
    }

    /// THE POSITIVE CONTROL for the pair above: the two cases must come out
    /// DIFFERENTLY from the same call. A version that closed everything, or
    /// closed nothing, would still satisfy one test each - so this asserts the
    /// distinction itself, run in one go over both rows.
    #[test]
    fn taken_down_treats_the_two_cases_differently() {
        let conn = touched_and_untouched();
        mark_taken_down(
            &conn,
            &["gh:untouched".to_string(), "gh:applied".to_string()],
        )
        .unwrap();

        let mut statuses: Vec<(String, String)> = conn
            .prepare("SELECT key, status FROM job_status ORDER BY key")
            .unwrap()
            .query_map([], |r| Ok((r.get(0)?, r.get(1)?)))
            .unwrap()
            .map(Result::unwrap)
            .collect();
        statuses.sort();
        assert_eq!(
            statuses,
            vec![
                ("gh:applied".to_string(), "applied".to_string()),
                ("gh:untouched".to_string(), crate::status::CLOSED.to_string()),
            ],
            "one call, two outcomes, decided by what the person had already done"
        );
    }

    /// The button's number has to be the number of rows the ENGINE will read.
    ///
    /// It was not. This side counted `('manual','imported')` while
    /// manual.ALWAYS_RECHECKABLE had been narrowed to hand-added rows alone,
    /// so on a live profile the button offered to check 310 postings when the
    /// engine would have fetched none of them - and 410 of those rows were
    /// LinkedIn URLs, which is a site this app refuses to read automatically.
    /// The number was wrong in the direction that looks like an incident.
    ///
    /// ASSERTED AGAINST THE OTHER HALF, not against this query's own shape: a
    /// test that restated the SQL would have passed throughout.
    #[test]
    fn the_added_links_count_covers_only_what_the_engine_rechecks() {
        let conn = Connection::open_in_memory().unwrap();
        conn.execute_batch(SCHEMA_SQL).unwrap();
        ensure_columns(&conn, "jobs", &ADDED_JOB_COLUMNS).unwrap();
        conn.execute(
            "INSERT INTO jobs (key, title, qualified, source) VALUES
             ('manual:1', 'Typed in by hand', 1, 'manual'),
             ('imported:1', 'Handed over by a collector', 1, 'imported'),
             ('imported:2', 'Another one', 1, 'imported'),
             ('greenhouse:1', 'Read off a board', 1, 'greenhouse')",
            [],
        )
        .unwrap();

        let state = manual_link_state(&conn, 0.0).unwrap();
        assert_eq!(
            state.total, 1,
            "only the hand-added row is the button's business; imported rows \
             belong to whatever collector wrote them"
        );
        assert_eq!(state.due, 1, "and it is due, never having been looked at");
    }

    /// A few postings that differ in the ways a search has to tell apart.
    fn searchable_database() -> Connection {
        let conn = Connection::open_in_memory().unwrap();
        conn.execute_batch(SCHEMA_SQL).unwrap();
        ensure_columns(&conn, "jobs", &ADDED_JOB_COLUMNS).unwrap();
        conn.execute(
            "INSERT INTO companies (id, name) VALUES (1, 'Acme Robotics'), (2, 'Globex')",
            [],
        )
        .unwrap();
        conn.execute(
            "INSERT INTO jobs (key, company_id, title, location, description, qualified)
             VALUES
             ('gh:1', 1, 'Senior Data Engineer', 'Dayton OH',
              'Build pipelines in Python and dbt.', 1),
             ('gh:2', 2, 'Support Analyst', 'Remote',
              'Answer tickets. Some Python scripting helps.', 1),
             ('gh:3', 1, 'Warehouse Associate', 'Springfield OH',
              'Lift boxes. No computers involved.', 1)",
            [],
        )
        .unwrap();
        conn
    }

    fn keys(rows: &[TriageRow]) -> Vec<String> {
        rows.iter().map(|r| r.job.key.clone()).collect()
    }

    #[test]
    fn a_term_matches_the_title() {
        let conn = searchable_database();
        let rows = search_jobs(&conn, &["engineer".to_string()]).unwrap();
        assert_eq!(keys(&rows), vec!["gh:1"]);
    }

    /// The whole point of "find ANYTHING": the word is nowhere in the title,
    /// the company or the location. A title-only search would return nothing
    /// here and look like the posting does not exist.
    #[test]
    fn a_term_matches_text_that_is_only_in_the_description() {
        let conn = searchable_database();
        let rows = search_jobs(&conn, &["dbt".to_string()]).unwrap();
        assert_eq!(keys(&rows), vec!["gh:1"], "description must be searched");
    }

    #[test]
    fn a_term_matches_the_company_name() {
        let conn = searchable_database();
        let rows = search_jobs(&conn, &["globex".to_string()]).unwrap();
        assert_eq!(keys(&rows), vec!["gh:2"], "and case must not matter");
    }

    /// Terms narrow. Both words appear across the set, but only one row has
    /// both - an OR would return two and read as a worse search the more you
    /// type, which is backwards.
    #[test]
    fn every_term_must_match_and_they_may_be_in_different_fields() {
        let conn = searchable_database();
        let rows = search_jobs(
            &conn,
            &["python".to_string(), "dayton".to_string()],
        )
        .unwrap();
        assert_eq!(
            keys(&rows),
            vec!["gh:1"],
            "python is in the description, dayton in the location"
        );
    }

    /// Statuses are filtered, not searched, decided 2026-08-21. Matching a status
    /// returns a category rather than a find, and the words overlap ordinary
    /// text, so including them makes every other search noisier.
    ///
    /// The note is the opposite case and must keep working: free text the
    /// person typed, often the only place a detail lives. Both halves are
    /// asserted together because the distinction IS the behaviour.
    #[test]
    fn a_status_is_not_searched_but_the_note_beside_it_is() {
        let conn = searchable_database();
        conn.execute(
            "INSERT INTO job_status (key, status, note, updated)
             VALUES ('gh:2', 'applied', 'left a voicemail', '2026-08-01')",
            [],
        )
        .unwrap();

        let by_status = search_jobs(&conn, &["applied".to_string()]).unwrap();
        assert!(
            !keys(&by_status).contains(&"gh:2".to_string()),
            "a status value must not pull the row into a text search"
        );

        let by_note = search_jobs(&conn, &["voicemail".to_string()]).unwrap();
        assert_eq!(keys(&by_note), vec!["gh:2"], "the typed note still matches");
    }

    #[test]
    fn nothing_typed_finds_nothing_rather_than_everything() {
        let conn = searchable_database();
        assert!(search_jobs(&conn, &[]).unwrap().is_empty());
        assert!(search_jobs(&conn, &["   ".to_string()]).unwrap().is_empty());
    }

    /// THE REACH TEST, and the reason this is a scope rather than a filter.
    /// A removed posting and a taken-down one are both absent from the list
    /// the person was looking at when they started typing. If search inherited
    /// that, the row they are hunting for is exactly the row it cannot find.
    #[test]
    fn search_reaches_rows_the_other_lists_hide() {
        let conn = searchable_database();
        conn.execute(
            "UPDATE jobs SET retired_at = '2026-08-01' WHERE key = 'gh:2'",
            [],
        )
        .unwrap();
        conn.execute(
            "UPDATE jobs SET delisted_at = '2026-08-02' WHERE key = 'gh:3'",
            [],
        )
        .unwrap();

        // Neither is in All jobs any more - that is the precondition, and
        // asserting it is what stops this test passing for the wrong reason.
        let all = keys(&list_all_jobs(&conn).unwrap());
        assert!(!all.contains(&"gh:2".to_string()), "removed row precondition");

        let removed = search_jobs(&conn, &["analyst".to_string()]).unwrap();
        assert_eq!(keys(&removed), vec!["gh:2"], "a removed posting is findable");

        let taken_down = search_jobs(&conn, &["warehouse".to_string()]).unwrap();
        assert_eq!(keys(&taken_down), vec!["gh:3"], "a taken-down posting is findable");
    }

    /// An apostrophe is the character that breaks an interpolated query, and
    /// company names are full of them.
    #[test]
    fn a_term_containing_an_apostrophe_is_matched_not_executed() {
        let conn = searchable_database();
        conn.execute(
            "INSERT INTO companies (id, name) VALUES (3, \"Moody's Analytics\")",
            [],
        )
        .unwrap();
        conn.execute(
            "INSERT INTO jobs (key, company_id, title, qualified)
             VALUES ('gh:4', 3, 'Risk Analyst', 1)",
            [],
        )
        .unwrap();
        let rows = search_jobs(&conn, &["moody's".to_string()]).unwrap();
        assert_eq!(keys(&rows), vec!["gh:4"]);
    }

    #[test]
    fn a_quoted_phrase_is_one_term_and_bare_words_are_many() {
        assert_eq!(
            parse_search_terms("data engineer"),
            vec!["data".to_string(), "engineer".to_string()]
        );
        assert_eq!(
            parse_search_terms("\"data engineer\""),
            vec!["data engineer".to_string()]
        );
        assert_eq!(
            parse_search_terms("remote \"data engineer\" python"),
            vec![
                "remote".to_string(),
                "data engineer".to_string(),
                "python".to_string()
            ]
        );
        assert!(parse_search_terms("    ").is_empty());
    }

    /// A phrase finds the posting a two-word search would over-match, and the
    /// pair is asserted together so the distinction is the thing being tested.
    #[test]
    fn a_quoted_phrase_narrows_where_loose_words_do_not() {
        let conn = searchable_database();
        conn.execute(
            "INSERT INTO jobs (key, company_id, title, description, qualified)
             VALUES ('gh:5', 2, 'Data Steward',
                     'Works with an engineer on data quality.', 1)",
            [],
        )
        .unwrap();

        let loose = search_jobs(&conn, &parse_search_terms("data engineer")).unwrap();
        assert_eq!(loose.len(), 2, "both rows contain both words somewhere");

        let phrase = search_jobs(&conn, &parse_search_terms("\"data engineer\"")).unwrap();
        assert_eq!(keys(&phrase), vec!["gh:1"], "only one contains the phrase");
    }

    /// A database with one job, for exercising the status/note path.
    fn one_job_database() -> Connection {
        let conn = Connection::open_in_memory().unwrap();
        conn.execute_batch(SCHEMA_SQL).unwrap();
        ensure_columns(&conn, "jobs", &ADDED_JOB_COLUMNS).unwrap();
        conn.execute(
            "INSERT INTO jobs (key, title, qualified) VALUES ('gh:1', 'Analyst', 1)",
            [],
        )
        .unwrap();
        conn
    }

    /// A status change with no offer terms, which is every transition but one.
    fn mark(conn: &Connection, key: &str, status: &str, note: Option<&str>) {
        set_status_with(conn, key, status, note, &OfferTerms::default()).unwrap();
    }

    fn standing_note(conn: &Connection, key: &str) -> Option<String> {
        conn.query_row(
            "SELECT note FROM job_status WHERE key = ?1",
            params![key],
            |r| r.get::<_, Option<String>>(0),
        )
        .unwrap()
    }

    #[test]
    fn each_module_lists_exactly_what_its_card_counts() {
        // THE PROPERTY THE MODULE REWRITE EXISTS FOR. The card and the list are
        // one WHERE clause used twice; if they ever come apart, a person clicks
        // a 53 and lands on a screen of something else. That was live: the
        // AWAITING A REPLY card counted applications silent for 14+ days and
        // its click went to the Pipeline, which counts something different
        // again.
        //
        // Over a database with a row in every interesting state, so the
        // agreement is not "both happen to be zero".
        let conn = one_job_database();
        conn.execute_batch(
            "INSERT INTO companies (id, name) VALUES (2, 'Nimbus');
             INSERT INTO jobs (key, company_id, title, verdict, qualified, fetched_at,
                               posted_at, delisted_at, retired_at, duplicate_of) VALUES
                ('gh:alt',      2, 'Alt',      'alt',  1, '2026-08-01', date('now'), NULL, NULL, NULL),
                ('gh:gone',     2, 'Gone',     'keep', 1, '2026-08-01', date('now'), '2026-08-02', NULL, NULL),
                ('gh:old',      2, 'Old',      'keep', 1, '2026-08-01', date('now','-40 day'), NULL, NULL, NULL),
                ('gh:removed',  2, 'Removed',  'keep', 1, '2026-08-01', date('now'), NULL, '2026-08-03', NULL),
                ('gh:dupe',     2, 'Dupe',     'keep', 1, '2026-08-01', date('now'), NULL, NULL, 'gh:1'),
                -- NOT QUALIFIED, and it has to be here.
                --
                -- Every other row is qualified = 1, so a filter added to one
                -- of the two queries and not the other was INVISIBLE: a
                -- positive control that put `AND jobs.qualified = 1` into
                -- list_module_jobs alone left this test passing. A fixture
                -- that does not vary a column cannot detect a disagreement
                -- about that column.
                ('gh:unqual',   2, 'Unqualified', 'keep', 0, '2026-08-01', date('now'), NULL, NULL, NULL);
             INSERT INTO job_status (key, status, updated) VALUES
                ('gh:1', 'applied', '2026-08-01'),
                ('gh:old', 'no_offer', '2026-08-02');
             INSERT INTO job_status_log (key, status, at) VALUES
                ('gh:1', 'applied', '2026-08-01'),
                ('gh:old', 'applied', '2026-08-01'),
                ('gh:old', 'no_offer', '2026-08-02');",
        )
        .unwrap();

        let mut non_empty = 0;
        for module in crate::modules::MODULES {
            let counted = count_module(&conn, module).unwrap();
            let listed = list_module_jobs(&conn, module).unwrap().len() as i64;
            assert_eq!(
                counted,
                listed,
                "{}: card says {counted}, list has {listed}",
                module.key()
            );
            if counted > 0 {
                non_empty += 1;
            }
        }
        assert!(
            non_empty >= 5,
            "only {non_empty} modules had rows - the agreement above is close \
             to vacuous, so the fixture no longer covers the interesting states"
        );
    }

    /// THE DEFECT THAT SHIPPED, as a test. A database migrated by THIS half used
    /// to get the column and not the split, because the backfill was keyed
    /// off "did we just add the column" and lived only in the engine. Anybody
    /// who installs an update and opens the app migrates here first, so that
    /// is the common path, not the rare one.
    #[test]
    fn this_half_splits_the_existing_rows_and_not_only_the_engine() {
        let conn = Connection::open_in_memory().unwrap();
        conn.execute_batch(SCHEMA_SQL).unwrap();
        conn.execute_batch(
            "INSERT INTO jobs (key, title, verdict, qualified, screen_reasons)
             VALUES
                ('gh:pay',   'Pay',   'alt', 1,
                 'pay under the $70,000 floor (above the $52,000 fallback floor)'),
                ('gh:other', 'Other', 'alt', 1,
                 'asks for a bachelor''s degree');",
        )
        .unwrap();

        migrate(&conn).unwrap();

        let reason = |key: &str| -> Option<String> {
            conn.query_row(
                "SELECT alt_reason FROM jobs WHERE key = ?1",
                [key],
                |r| r.get(0),
            )
            .unwrap()
        };
        assert_eq!(reason("gh:pay").as_deref(), Some("salary"));
        assert_eq!(reason("gh:other"), None);

        // ONCE. A second open must not undo a re-screen that has since
        // decided the row is held back on something else.
        conn.execute("UPDATE jobs SET alt_reason = 'requirements'", []).unwrap();
        migrate(&conn).unwrap();
        assert_eq!(reason("gh:pay").as_deref(), Some("requirements"));
    }

    /// The two halves have to run the SAME statement, for the same reason
    /// they have to migrate the same columns - and a data migration that
    /// differs fails more quietly than a missing column, because both sides
    /// work and simply disagree about what is on which card.
    #[test]
    fn the_engine_backfills_the_alt_split_with_the_same_statement() {
        let py = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("..")
            .join("unlatched")
            .join("db.py");
        let text = std::fs::read_to_string(&py)
            .unwrap_or_else(|e| panic!("cannot read {}: {e}", py.display()));
        let block = text
            .split("ALT_REASON_BACKFILL = (")
            .nth(1)
            .expect("the engine's ALT_REASON_BACKFILL moved or was renamed")
            .split(")")
            .next()
            .unwrap();

        // The engine writes it as adjacent string literals over two lines.
        // Joining the quoted pieces reconstructs the statement it runs.
        let mut theirs = String::new();
        let mut rest = block;
        while let Some(open) = rest.find('"') {
            let after = &rest[open + 1..];
            match after.split_once('"') {
                Some((piece, tail)) => {
                    theirs.push_str(piece);
                    rest = tail;
                }
                None => break,
            }
        }

        let squash = |s: &str| s.split_whitespace().collect::<Vec<_>>().join(" ");
        assert!(
            !theirs.trim().is_empty(),
            "read nothing out of the engine statement - a parse bug, not drift"
        );
        assert_eq!(squash(&theirs), squash(ALT_REASON_BACKFILL));
    }

    #[test]
    fn every_alt_row_is_on_exactly_one_of_the_two_cards_that_split_them() {
        // THE GUARANTEE THAT MAKES THE SPLIT SAFE. One card used to count
        // `verdict = 'alt'` and nothing could fall out of it. Two clauses now
        // divide that pile, and a row matching NEITHER would leave the
        // dashboard silently - a job the person never sees, which is the one
        // outcome this app is written to avoid.
        //
        // The four reasons here are the four that actually occur, counted
        // from the code that writes them: 2 of 2 values screen.py assigns,
        // the empty string a row forced to alt without being screened carries
        // (importer.py and manual.py both override the verdict and not the
        // reason), and the NULL an installed profile keeps wherever the
        // migration could not recover the split.
        let conn = one_job_database();
        conn.execute_batch(
            "INSERT INTO companies (id, name) VALUES (2, 'Nimbus');
             INSERT INTO jobs (key, company_id, title, verdict, qualified,
                               fetched_at, posted_at, alt_reason) VALUES
                ('gh:pay',  2, 'Pay',  'alt', 1, '2026-08-01', date('now'), 'salary'),
                ('gh:req',  2, 'Req',  'alt', 1, '2026-08-01', date('now'), 'requirements'),
                ('gh:hand', 2, 'Hand', 'alt', 1, '2026-08-01', date('now'), ''),
                ('gh:old',  2, 'Old',  'alt', 1, '2026-08-01', date('now'), NULL);",
        )
        .unwrap();

        let keys = |module| -> Vec<String> {
            let mut found: Vec<String> = list_module_jobs(&conn, module)
                .unwrap()
                .into_iter()
                .map(|r| r.job.key)
                .collect();
            found.sort();
            found
        };
        let pay = keys(crate::modules::Module::BelowSalary);
        let rest = keys(crate::modules::Module::RequirementsNotAligned);

        assert_eq!(pay, vec!["gh:pay".to_string()]);
        // The unscreened and the un-migrated land HERE rather than nowhere.
        // This is the whole reason the clause is written as "not salary"
        // instead of "is requirements".
        assert_eq!(
            rest,
            vec!["gh:hand".to_string(), "gh:old".to_string(), "gh:req".to_string()]
        );

        // Said as a count as well, because the two assertions above would
        // both still pass if a row appeared on both cards - by construction a
        // row listed by both clauses is in both vectors, so the sum exceeds
        // the number of alt rows and only this catches it.
        let total: i64 = conn
            .query_row("SELECT COUNT(*) FROM jobs WHERE verdict = 'alt'", [], |r| {
                r.get(0)
            })
            .unwrap();
        assert_eq!(pay.len() as i64 + rest.len() as i64, total);
    }

    #[test]
    fn the_badge_names_the_card_the_row_is_actually_counted_on() {
        // TWO DECISIONS ABOUT ONE THING. The dashboard sorts a row with a
        // WHERE clause; the triage badge sorts it in Rust, from a value on a
        // row already in memory. They cannot share an implementation - one is
        // SQL - so this runs both over the same rows and compares. Without it
        // a badge could read "pay" on a job the salary card does not count,
        // and every other test would still pass - confirmed on 2026-09-02 by
        // inverting Module::for_alt_reason, which this caught and the rest of
        // the suite did not notice.
        let conn = one_job_database();
        conn.execute_batch(
            "INSERT INTO companies (id, name) VALUES (2, 'Nimbus');
             INSERT INTO jobs (key, company_id, title, verdict, qualified,
                               fetched_at, posted_at, alt_reason) VALUES
                ('gh:pay',  2, 'Pay',  'alt', 1, '2026-08-01', date('now'), 'salary'),
                ('gh:req',  2, 'Req',  'alt', 1, '2026-08-01', date('now'), 'requirements'),
                ('gh:hand', 2, 'Hand', 'alt', 1, '2026-08-01', date('now'), ''),
                ('gh:old',  2, 'Old',  'alt', 1, '2026-08-01', date('now'), NULL);",
        )
        .unwrap();

        for module in [
            crate::modules::Module::BelowSalary,
            crate::modules::Module::RequirementsNotAligned,
        ] {
            let rows = list_module_jobs(&conn, module).unwrap();
            assert!(!rows.is_empty(), "{} listed nothing to check", module.key());
            for row in rows {
                let reason = row.job.alt_reason.clone().unwrap_or_default();
                assert_eq!(
                    crate::modules::Module::for_alt_reason(&reason),
                    module,
                    "{} is on the {} card but its badge would read the other",
                    row.job.key,
                    module.key()
                );
            }
        }
    }

    /// The engine and this app both create the jobs table, so a column added
    /// to one migration list and not the other is a database that works until
    /// the other half opens it and fails its whole query with "no such
    /// column". That is not hypothetical - it is why ADDED_JOB_COLUMNS exists
    /// here at all, and until this test there was nothing but a comment
    /// asking each side to remember.
    ///
    /// Confirmed on 2026-09-02 against both ways the lists can part: a column
    /// present in the engine and missing here, and the same columns in a
    /// different order. This caught each one.
    #[test]
    fn the_engine_migrates_the_same_columns_this_app_does() {
        let py = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("..")
            .join("unlatched")
            .join("db.py");
        let text = std::fs::read_to_string(&py)
            .unwrap_or_else(|e| panic!("cannot read {}: {e}", py.display()));
        // LINE BY LINE, from the assignment to the first blank line.
        // Splitting the file on a blank line does not work: db.py is stored
        // with CRLF endings, so a blank-line terminator written as two bare
        // newlines never matches, and the slice runs to the end of the file
        // - observed: the first version of this test read straight past the
        // list and reported "PRAGMA table_info(jobs)" and a long run of the
        // engine's SELECT statements as column names, and still produced a
        // confident list.
        //
        // Comment lines are skipped because the block carries prose that
        // itself contains quoted phrases, and EVERY pair on a line is taken:
        // the engine writes two per line, so reading only the first silently
        // halved the list.
        let mut theirs: Vec<String> = Vec::new();
        let mut inside = false;
        for line in text.lines() {
            let trimmed = line.trim();
            if !inside {
                if trimmed.starts_with("ADDED_JOB_COLUMNS = (") {
                    inside = true;
                } else {
                    continue;
                }
            } else if trimmed.is_empty() {
                break;
            }
            if trimmed.starts_with('#') {
                continue;
            }
            let mut rest = trimmed;
            while let Some(at) = rest.find("(\"") {
                let after = &rest[at + 2..];
                match after.split_once('"') {
                    Some((name, tail)) => {
                        theirs.push(name.to_string());
                        rest = tail;
                    }
                    None => break,
                }
            }
        }

        let ours: Vec<String> =
            ADDED_JOB_COLUMNS.iter().map(|(n, _)| (*n).to_string()).collect();
        assert!(
            !theirs.is_empty(),
            "read no column names from the engine list - a parse bug, not a schema change"
        );
        assert_eq!(ours, theirs, "the two migration lists have drifted apart");
    }

    #[test]
    fn a_removed_or_grouped_row_is_in_no_module_at_all() {
        // Both have their own screens. A landing-screen count that quietly
        // included them would double-count a job the person had already
        // dealt with, and clicking through would show rows they thought were
        // gone.
        let conn = one_job_database();
        conn.execute_batch(
            "INSERT INTO companies (id, name) VALUES (2, 'Nimbus');
             INSERT INTO jobs (key, company_id, title, verdict, qualified, fetched_at,
                               posted_at, retired_at, duplicate_of) VALUES
                ('gh:removed', 2, 'Removed', 'keep', 1, '2026-08-01', date('now'), '2026-08-03', NULL),
                ('gh:dupe',    2, 'Dupe',    'keep', 1, '2026-08-01', date('now'), NULL, 'gh:1');",
        )
        .unwrap();

        for module in crate::modules::MODULES {
            let keys: Vec<String> = list_module_jobs(&conn, module)
                .unwrap()
                .into_iter()
                .map(|r| r.job.key)
                .collect();
            assert!(
                !keys.contains(&"gh:removed".to_string()),
                "{} lists a removed row",
                module.key()
            );
            assert!(
                !keys.contains(&"gh:dupe".to_string()),
                "{} lists a grouped duplicate",
                module.key()
            );
        }
    }

    #[test]
    fn awaiting_a_reply_falls_as_applied_holds() {
        // The point, as an assertion. Applied reads the LOG and can only
        // rise; Awaiting a reply reads the CURRENT status and falls the moment
        // something moves on. They read the same today only because nothing on
        // their board has progressed past Applied yet, and a dashboard that made
        // them one number would hide the whole pipeline.
        use crate::modules::Module;
        let conn = one_job_database();
        mark(&conn, "gh:1", "applied", None);
        assert_eq!(count_module(&conn, Module::Applied).unwrap(), 1);
        assert_eq!(count_module(&conn, Module::AwaitingReply).unwrap(), 1);

        mark(&conn, "gh:1", "interviewed", None);
        assert_eq!(
            count_module(&conn, Module::Applied).unwrap(),
            1,
            "an interview does not un-send the application"
        );
        assert_eq!(
            count_module(&conn, Module::AwaitingReply).unwrap(),
            0,
            "they replied, so it is no longer awaiting one"
        );
    }

    #[test]
    fn a_status_change_without_a_note_keeps_the_note_already_there() {
        // The note is the person's standing comment on the job. A status change
        // that says nothing new must not erase it.
        let conn = one_job_database();
        mark(&conn, "gh:1", "applied", Some("recruiter is Dana"));
        mark(&conn, "gh:1", "interviewed", None);

        assert_eq!(
            standing_note(&conn, "gh:1").as_deref(),
            Some("recruiter is Dana")
        );
    }

    #[test]
    fn a_note_appends_to_its_own_table_and_never_forges_a_transition() {
        // The defect this replaced: adding a note re-wrote the job's status to
        // carry it, so the history gained a row saying the status had "changed"
        // to the value it already had. Three notes on one application produced
        // a timeline claiming it had been marked Applied four times.
        let conn = one_job_database();
        mark(&conn, "gh:1", "applied", None);
        add_note(&conn, "gh:1", "left a voicemail").unwrap();
        add_note(&conn, "gh:1", "they called back").unwrap();

        let transitions: i64 = conn
            .query_row("SELECT COUNT(*) FROM job_status_log", [], |r| r.get(0))
            .unwrap();
        assert_eq!(transitions, 1, "two notes must not add status history");

        let notes = list_notes(&conn).unwrap();
        assert_eq!(notes.len(), 2, "both notes are kept - neither replaces the other");
        assert_eq!(notes[0].note, "left a voicemail");
        assert_eq!(notes[1].note, "they called back");
        assert_eq!(
            standing_note(&conn, "gh:1").as_deref(),
            Some("they called back"),
            "the list column shows the most recent"
        );
    }

    #[test]
    fn what_an_offer_was_is_stored_beside_the_transition_that_records_it() {
        let conn = one_job_database();
        let terms = OfferTerms::from_inputs("  $120,000  ", "2026-09-01");
        set_status_with(&conn, "gh:1", "offer", Some("verbal"), &terms).unwrap();

        let row: (Option<String>, Option<String>) = conn
            .query_row(
                "SELECT pay, offer_date FROM job_status_log WHERE key = 'gh:1'",
                [],
                |r| Ok((r.get(0)?, r.get(1)?)),
            )
            .unwrap();
        assert_eq!(row.0.as_deref(), Some("$120,000"), "trimmed as typed");
        assert_eq!(row.1.as_deref(), Some("2026-09-01"));
    }

    #[test]
    fn a_field_left_blank_stores_null_rather_than_an_empty_string() {
        // "" would be counted as an answer by anything asking how many offers
        // have a figure recorded against them.
        let terms = OfferTerms::from_inputs("   ", "");
        assert!(terms.pay.is_none());
        assert!(terms.offer_date.is_none());
    }

    #[test]
    fn the_old_name_for_a_rejection_is_renamed_on_open_in_both_tables() {
        // Denied was renamed to No Offer. A database written by an earlier
        // version has to come forward, and the HISTORY has to come with it -
        // the log is what the funnel and the export read, so leaving it behind
        // would show one word on the row and another in its own timeline.
        let conn = one_job_database();
        conn.execute_batch(
            "INSERT INTO job_status (key, status, updated)
                VALUES ('gh:1', 'denied', '2026-08-01');
             INSERT INTO job_status_log (key, status, at)
                VALUES ('gh:1', 'applied', '2026-07-01'),
                       ('gh:1', 'denied', '2026-08-01');",
        )
        .unwrap();

        rename_retired_statuses(&conn).unwrap();

        assert_eq!(standing_note(&conn, "gh:1"), None);
        let current: String = conn
            .query_row("SELECT status FROM job_status WHERE key = 'gh:1'", [], |r| {
                r.get(0)
            })
            .unwrap();
        assert_eq!(current, "no_offer");
        let history: Vec<String> = conn
            .prepare("SELECT status FROM job_status_log ORDER BY id")
            .unwrap()
            .query_map([], |r| r.get(0))
            .unwrap()
            .map(Result::unwrap)
            .collect();
        assert_eq!(history, vec!["applied", "no_offer"]);
    }

    #[test]
    fn a_note_is_logged_against_the_status_it_was_written_about() {
        // THE REGRESSION THIS EXISTS TO STOP. set_status_for used to re-read the
        // job's note and pass it back in, so the log recorded a note composed
        // about "applied" against "interviewed" as well - and an offer's terms
        // would have reappeared, mislabelled, on the entry recording that the
        // offer was declined.
        let conn = one_job_database();
        mark(&conn, "gh:1", "applied", Some("recruiter is Dana"));
        mark(&conn, "gh:1", "interviewed", None);

        let mut stmt = conn
            .prepare("SELECT status, note FROM job_status_log ORDER BY id")
            .unwrap();
        let rows: Vec<(String, Option<String>)> = stmt
            .query_map([], |r| Ok((r.get(0)?, r.get(1)?)))
            .unwrap()
            .map(Result::unwrap)
            .collect();

        assert_eq!(rows.len(), 2);
        assert_eq!(rows[0], ("applied".into(), Some("recruiter is Dana".into())));
        assert_eq!(
            rows[1],
            ("interviewed".into(), None),
            "the second transition had no note of its own and must log none"
        );
    }

    #[test]
    fn the_log_is_append_only_across_a_sequence_of_changes() {
        // A correction is a new entry, never an edit. The moment a row can be
        // rewritten the history is worth only what the last edit says.
        let conn = one_job_database();
        for status in ["applied", "interviewed", "offer", "hired"] {
            mark(&conn, "gh:1", status, None);
        }
        let count: i64 = conn
            .query_row("SELECT COUNT(*) FROM job_status_log", [], |r| r.get(0))
            .unwrap();
        assert_eq!(count, 4);
    }

    /// A database on the current schema with two qualified jobs in it.
    fn two_job_database() -> Connection {
        let conn = Connection::open_in_memory().unwrap();
        conn.execute_batch(SCHEMA_SQL).unwrap();
        // SCHEMA_SQL is the ORIGINAL shape; everything added since arrives via
        // the migration. A fixture that skips it tests a database no real
        // install has.
        ensure_columns(&conn, "jobs", &ADDED_JOB_COLUMNS).unwrap();
        for (key, title) in [("gh:1", "Analyst"), ("gh:2", "Coordinator")] {
            conn.execute(
                "INSERT INTO jobs (key, title, qualified) VALUES (?1, ?2, 1)",
                rusqlite::params![key, title],
            )
            .unwrap();
        }
        conn
    }

    fn keys_of(rows: &[TriageRow]) -> Vec<String> {
        rows.iter().map(|r| r.job.key.clone()).collect()
    }

    // "Remove from list" says it takes rows out of EVERY list, and the way back
    // is a mode of All jobs rather than a place of its own. Both halves of that
    // sentence are query behaviour, and neither was covered: the engine tests
    // prove retired_at gets written, not that any list respects it.
    /// A database ON DISK, because seeding ATTACHes a file by path and an
    /// in-memory connection has none.
    ///
    /// Cleared on the way in rather than the way out: a failing assertion
    /// leaves the files behind on purpose, where they can be opened and looked
    /// at, and the next run starts clean regardless.
    fn database_at(test: &str, which: &str) -> (std::path::PathBuf, Connection) {
        let dir = std::env::temp_dir().join(format!("unlatched-seed-{test}"));
        let _ = std::fs::create_dir_all(&dir);
        let path = dir.join(format!("{which}.db"));
        let _ = std::fs::remove_file(&path);
        let conn = open(&path).unwrap();
        (path, conn)
    }

    #[test]
    fn a_new_search_inherits_the_employers_the_person_already_resolved() {
        let (src_path, src) = database_at("inherits", "first-search");
        src.execute(
            "INSERT INTO companies (name, domain, careers_url, ats, ats_ref, probe_status,
                                    last_probed)
             VALUES ('Ridgeline Health', 'ridgelinehealth.com', 'https://x/careers',
                     'greenhouse', 'ridgeline', 'ok', '2026-08-01')",
            [],
        )
        .unwrap();

        let (_dst_path, dst) = database_at("inherits", "second-search");
        assert_eq!(seed_companies_from(&dst, &src_path).unwrap(), 1);

        let (name, ats, reference): (String, String, String) = dst
            .query_row(
                "SELECT name, ats, ats_ref FROM companies",
                [],
                |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)),
            )
            .unwrap();
        assert_eq!((name.as_str(), ats.as_str(), reference.as_str()),
                   ("Ridgeline Health", "greenhouse", "ridgeline"));
    }

    /// The Companies table reads provenance from this query and nowhere else,
    /// so a query that quietly stopped selecting it would show every employer
    /// as "unknown" while every other test still passed.
    #[test]
    fn the_company_list_carries_provenance_including_its_absence() {
        let (_path, conn) = database_at("provenance", "search");
        conn.execute_batch(
            "INSERT INTO companies (name, probe_status, origin)
                  VALUES ('Adams Health', 'ok', 'seeded'),
                         ('Bell Systems', 'ok', 'discovered');
             INSERT INTO companies (name, probe_status)
                  VALUES ('Crane Foundry', 'ok');",
        )
        .unwrap();

        let listed = list_companies(&conn).unwrap();
        let origins: Vec<Option<&str>> =
            listed.iter().map(|c| c.origin.as_deref()).collect();
        assert_eq!(origins, vec![Some("seeded"), Some("discovered"), None]);
    }

    /// A starter employer copied into a second search is still a starter
    /// employer. Without this, "collect the seeded ones" would find nothing in
    /// the new search and say so honestly while being wrong.
    #[test]
    fn where_an_employer_came_from_travels_with_it() {
        let (src_path, src) = database_at("origin", "first-search");
        src.execute(
            "INSERT INTO companies (name, ats, ats_ref, probe_status, origin)
             VALUES ('Ridgeline Health', 'greenhouse', 'ridgeline', 'ok', 'seeded')",
            [],
        )
        .unwrap();

        let (_dst_path, dst) = database_at("origin", "second-search");
        assert_eq!(seed_companies_from(&dst, &src_path).unwrap(), 1);

        let origin: Option<String> = dst
            .query_row("SELECT origin FROM companies", [], |r| r.get(0))
            .unwrap();
        assert_eq!(origin.as_deref(), Some(SEEDED));
    }

    /// The source is ATTACHed as a file, so migrate() has never run on it: a
    /// profile last written by a build without the origin column really can be
    /// on disk. Selecting a column that is not there is a hard SQLite error,
    /// which would have taken the whole seed down rather than one field.
    #[test]
    fn a_profile_written_before_the_origin_column_still_seeds() {
        let dir = std::env::temp_dir().join("unlatched-seed-oldshape");
        let _ = std::fs::create_dir_all(&dir);
        let src_path = dir.join("first-search.db");
        let _ = std::fs::remove_file(&src_path);
        // Deliberately NOT open() - this database has to keep the old shape,
        // and open() would migrate the column into it.
        let src = Connection::open(&src_path).unwrap();
        src.execute_batch(
            "CREATE TABLE companies (
               id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE,
               domain TEXT, careers_url TEXT, ats TEXT, ats_ref TEXT,
               probe_status TEXT DEFAULT 'new', last_probed TEXT);
             INSERT INTO companies (name, ats, ats_ref, probe_status)
             VALUES ('Ridgeline Health', 'greenhouse', 'ridgeline', 'ok');",
        )
        .unwrap();
        drop(src);

        let (_dst_path, dst) = database_at("oldshape", "second-search");
        assert_eq!(seed_companies_from(&dst, &src_path).unwrap(), 1);

        let (name, origin): (String, Option<String>) = dst
            .query_row("SELECT name, origin FROM companies", [], |r| {
                Ok((r.get(0)?, r.get(1)?))
            })
            .unwrap();
        assert_eq!(name, "Ridgeline Health");
        assert_eq!(origin, None, "an unknown origin must not be invented");
    }

    #[test]
    fn employers_that_resolved_to_nothing_are_carried_forward_too() {
        // The expensive knowledge is "we looked and there was no board". Losing
        // it means every new search re-burns hours proving the same absence.
        let (src_path, src) = database_at("failures", "first-search");
        src.execute(
            "INSERT INTO companies (name, probe_status, last_probed)
             VALUES ('Small Clinic', 'no board found', '2026-08-01')",
            [],
        )
        .unwrap();

        let (_dst_path, dst) = database_at("failures", "second-search");
        seed_companies_from(&dst, &src_path).unwrap();

        let status: String = dst
            .query_row("SELECT probe_status FROM companies", [], |r| r.get(0))
            .unwrap();
        assert_eq!(status, "no board found");
    }

    #[test]
    fn seeding_carries_no_postings_and_no_pipeline() {
        let (src_path, src) = database_at("nopipeline", "first-search");
        src.execute(
            "INSERT INTO companies (name, probe_status) VALUES ('Acme', 'ok')",
            [],
        )
        .unwrap();
        src.execute(
            "INSERT INTO jobs (key, title, qualified) VALUES ('gh:1', 'Analyst', 1)",
            [],
        )
        .unwrap();
        src.execute(
            "INSERT INTO job_status (key, status, updated) VALUES ('gh:1', 'applied', '2026-08-01')",
            [],
        )
        .unwrap();

        let (_dst_path, dst) = database_at("nopipeline", "second-search");
        seed_companies_from(&dst, &src_path).unwrap();

        let jobs: i64 = dst.query_row("SELECT COUNT(*) FROM jobs", [], |r| r.get(0)).unwrap();
        let statuses: i64 = dst
            .query_row("SELECT COUNT(*) FROM job_status", [], |r| r.get(0))
            .unwrap();
        assert_eq!((jobs, statuses), (0, 0), "a new search starts with no postings and no pipeline");
    }

    #[test]
    fn seeding_twice_does_not_overwrite_the_second_search() {
        let (src_path, src) = database_at("twice", "first-search");
        src.execute(
            "INSERT INTO companies (name, probe_status) VALUES ('Acme', 'no board found')",
            [],
        )
        .unwrap();

        let (_dst_path, dst) = database_at("twice", "second-search");
        seed_companies_from(&dst, &src_path).unwrap();
        // The second search has since re-probed and found a board.
        dst.execute(
            "UPDATE companies SET probe_status = 'ok', ats = 'lever' WHERE name = 'Acme'",
            [],
        )
        .unwrap();

        assert_eq!(seed_companies_from(&dst, &src_path).unwrap(), 0);
        let status: String = dst
            .query_row("SELECT probe_status FROM companies", [], |r| r.get(0))
            .unwrap();
        assert_eq!(status, "ok", "re-seeding must not undo work done in this search");
    }

    #[test]
    fn a_removed_row_leaves_triage_and_all_jobs() {
        let conn = two_job_database();
        retire(&conn, &["gh:1".to_string()]).unwrap();

        assert_eq!(keys_of(&list_triage_jobs(&conn, true).unwrap()), ["gh:2"]);
        assert_eq!(keys_of(&list_all_jobs(&conn).unwrap()), ["gh:2"]);
    }

    #[test]
    fn a_removed_row_is_exactly_what_the_removed_view_shows() {
        let conn = two_job_database();
        retire(&conn, &["gh:1".to_string()]).unwrap();

        assert_eq!(keys_of(&list_retired_jobs(&conn).unwrap()), ["gh:1"]);
        assert_eq!(retired_count(&conn).unwrap(), 1);
    }

    #[test]
    fn putting_a_row_back_returns_it_to_all_jobs_and_empties_the_removed_view() {
        let conn = two_job_database();
        retire(&conn, &["gh:1".to_string()]).unwrap();
        restore(&conn, &["gh:1".to_string()]).unwrap();

        let mut back = keys_of(&list_all_jobs(&conn).unwrap());
        back.sort();
        assert_eq!(back, ["gh:1", "gh:2"]);
        assert!(list_retired_jobs(&conn).unwrap().is_empty());
        assert_eq!(retired_count(&conn).unwrap(), 0);
    }

    fn triage_row(company: &str, title: &str, place: &str, key: &str) -> TriageRow {
        TriageRow {
            job: Job {
                key: key.to_string(),
                title: title.to_string(),
                location: Some(place.to_string()),
                ..Job::default()
            },
            company_name: Some(company.to_string()),
            ..TriageRow::default()
        }
    }

    #[test]
    fn one_opening_listed_per_city_becomes_one_row() {
        // Measured: 75 of 290 rows in a real profile were this shape.
        let rows = vec![
            triage_row("Acme", "Implementation Consultant", "San Francisco, CA", "gh:1"),
            triage_row("Acme", "Implementation Consultant", "Seattle, WA", "gh:2"),
            triage_row("Acme", "Implementation Consultant", "New York, NY", "gh:3"),
        ];
        let out = collapse_locations(rows);
        assert_eq!(out.len(), 1);
        // The FIRST row survives, and callers pass rows ordered by score, so
        // the representative is the best-scoring posting of the set.
        assert_eq!(out[0].job.key, "gh:1");
        assert_eq!(out[0].other_locations.len(), 2);
    }

    #[test]
    fn different_titles_at_one_company_stay_separate() {
        let rows = vec![
            triage_row("Acme", "Support Engineer", "Austin, TX", "gh:1"),
            triage_row("Acme", "Senior Support Engineer", "Austin, TX", "gh:2"),
        ];
        assert_eq!(collapse_locations(rows).len(), 2);
    }

    #[test]
    fn the_same_title_at_different_companies_stays_separate() {
        let rows = vec![
            triage_row("Acme", "Support Engineer", "Austin, TX", "gh:1"),
            triage_row("Nimbus", "Support Engineer", "Austin, TX", "gh:2"),
        ];
        assert_eq!(collapse_locations(rows).len(), 2);
    }

    #[test]
    fn a_repeated_location_is_not_listed_twice() {
        let rows = vec![
            triage_row("Acme", "Analyst", "Austin, TX", "gh:1"),
            triage_row("Acme", "Analyst", "Austin, TX", "gh:2"),
        ];
        let out = collapse_locations(rows);
        assert_eq!(out.len(), 1);
        assert!(out[0].other_locations.is_empty());
    }

    #[test]
    fn a_blank_location_adds_nothing_to_the_list() {
        let rows = vec![
            triage_row("Acme", "Analyst", "Austin, TX", "gh:1"),
            triage_row("Acme", "Analyst", "", "gh:2"),
        ];
        let out = collapse_locations(rows);
        assert_eq!(out.len(), 1);
        assert!(out[0].other_locations.is_empty());
    }

    #[test]
    fn migrating_twice_is_a_no_op() {
        let conn = old_database();
        conn.execute_batch(SCHEMA_SQL).unwrap();
        ensure_columns(&conn, "jobs", &ADDED_JOB_COLUMNS).unwrap();
        ensure_columns(&conn, "jobs", &ADDED_JOB_COLUMNS).expect("a second migration must not error");
    }
}

/// What the app shows about hand-added links without fetching anything.
///
/// By design, 2026-08-06: the scheduled refresh keeps handling everything
/// the app ships with. Hand-added links are re-read only when the person
/// asks - so if a collection has happened since they were last checked, the
/// app has to SAY so rather than leaving them quietly stale.
#[derive(Clone, Copy, Debug, Default)]
pub struct ManualLinkState {
    /// Live hand-added jobs.
    pub total: i64,
    /// How many are eligible for a re-check right now (once a day each).
    pub due: i64,
    /// True when a collection has run more recently than the oldest
    /// hand-added link was checked - the exact condition for the notice.
    pub stale_since_collect: bool,
}

impl ManualLinkState {
    pub fn any(self) -> bool {
        self.total > 0
    }
}

/// Read straight from SQLite rather than by asking the engine: this is
/// consulted while drawing a frame, and spawning a process per frame to
/// answer "is a button enabled" would be absurd.
pub fn manual_link_state(conn: &Connection, min_hours: f64) -> Result<ManualLinkState, String> {
    let total: i64 = conn
        .query_row(
            // HAND-ADDED ONLY, which is what the engine will actually read.
            //
            // This counted imported rows too, on reasoning from 2026-08-09
            // that the engine SUPERSEDED on 2026-08-12: a collector that
            // already read a posting asking us to read it again is a second
            // automated reader for information the app has been handed, so
            // manual.ALWAYS_RECHECKABLE dropped `imported` and this side was
            // never updated. The button then advertised 310 rows on a live
            // profile where the engine would have fetched zero.
            //
            // The comment this replaces said it out loud - "a count larger
            // than what will actually be read is a number people learn to
            // disbelieve" - and then the code did exactly that. It read as
            // the app being about to hammer a site it is careful never to
            // touch, which is a worse failure than the wrong number.
            //
            // Untouched only: a row already decided about does not need its
            // liveness checked.
            "SELECT COUNT(*) FROM jobs j \
             WHERE j.source = 'manual' AND j.delisted_at IS NULL \
               AND NOT EXISTS (SELECT 1 FROM job_status s \
                               WHERE s.key = j.key AND TRIM(s.status) != '')",
            [],
            |r| r.get(0),
        )
        .map_err(|e| e.to_string())?;
    if total == 0 {
        return Ok(ManualLinkState::default());
    }

    // julianday() is days-as-a-float, so the difference times 24 is hours -
    // and it copes with the mix of stamp formats already in this column
    // without parsing any of them here.
    let due: i64 = conn
        .query_row(
            "SELECT COUNT(*) FROM jobs j \
             WHERE j.source = 'manual' AND j.delisted_at IS NULL \
               AND NOT EXISTS (SELECT 1 FROM job_status s \
                               WHERE s.key = j.key AND TRIM(s.status) != '') \
               AND (j.last_seen IS NULL \
                    OR (julianday('now') - julianday(j.last_seen)) * 24.0 >= ?1)",
            [min_hours],
            |r| r.get(0),
        )
        .map_err(|e| e.to_string())?;

    // The notice condition, stated exactly: has a collection happened since
    // the oldest hand-added link was last looked at?
    let stale: i64 = conn
        .query_row(
            "SELECT COUNT(*) FROM jobs m \
             WHERE m.source = 'manual' AND m.delisted_at IS NULL \
               AND NOT EXISTS (SELECT 1 FROM job_status s \
                               WHERE s.key = m.key AND TRIM(s.status) != '') \
               AND (m.last_seen IS NULL OR m.last_seen < \
                    (SELECT MAX(j.fetched_at) FROM jobs j \
                      WHERE j.source != 'manual'))",
            [],
            |r| r.get(0),
        )
        .map_err(|e| e.to_string())?;

    Ok(ManualLinkState { total, due, stale_since_collect: stale > 0 })
}
