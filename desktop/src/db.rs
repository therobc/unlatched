// SQLite access. The schema below is the shared contract between this app
// and its command-line sibling; both open the same database file and must
// create identical tables, so the CREATE TABLE statements are reproduced
// here exactly rather than paraphrased.

use rusqlite::{params, Connection, Result as SqlResult};
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
const ADDED_JOB_COLUMNS: [(&str, &str); 17] = [
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
/// Qualified jobs joined against their human status. `show_all` turns off
/// the default filter that hides settled rows so the queue reads as "what is
/// still open to act on" unless asked otherwise.
/// The columns and joins both lists read. Held in one place because they were
/// duplicated once and the two copies immediately disagreed - the row mapper
/// below indexes by position, so a column added to one query and not the other
/// silently reads the wrong field rather than failing.
const SELECT_TRIAGE_COLUMNS: &str = "SELECT jobs.key, jobs.company_id, jobs.title, jobs.location, jobs.remote,
                       jobs.remote_evidence, jobs.salary_min, jobs.salary_max, jobs.currency,
                       jobs.hourly_rate,
                       jobs.url, jobs.posted_at, jobs.fetched_at, jobs.description,
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
                       jobs.repost_of
                FROM jobs
                LEFT JOIN job_status ON job_status.key = jobs.key
                LEFT JOIN companies ON companies.id = jobs.company_id";

/// One row mapper for every list. Positional indices are fragile by nature, so
/// there is exactly one place that knows them.
fn read_triage_rows(conn: &Connection, sql: &str) -> SqlResult<Vec<TriageRow>> {
    let mut stmt = conn.prepare(sql)?;
    let rows = stmt.query_map([], |r| {
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
                description: r.get(13)?,
                score: r.get(14)?,
                screen_reasons: r.get(15)?,
                qualified: r.get::<_, i64>(16)? != 0,
                source: r.get(17)?,
                verdict: r.get(18)?,
                coverage_pct: r.get(19)?,
                missing_skills: r.get(20)?,
                repost_note: r.get(21)?,
                repost_of: r.get(31)?,
                delisted_at: r.get(22)?,
                requirements_summary: r.get(23)?,
                duplicate_reason: r.get(24)?,
            },
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
) -> SqlResult<Vec<TriageRow>> {
    match scope {
        crate::app::ListScope::Module(module) => list_module_jobs(conn, module),
        crate::app::ListScope::Triage => list_triage_jobs(conn, show_all),
        crate::app::ListScope::All => list_all_jobs(conn),
        crate::app::ListScope::Retired => list_retired_jobs(conn),
        crate::app::ListScope::Duplicates => list_duplicate_jobs(conn),
    }
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

pub fn list_triage_jobs(conn: &Connection, show_all: bool) -> SqlResult<Vec<TriageRow>> {
    let base = format!(
        "{SELECT_TRIAGE_COLUMNS} WHERE jobs.qualified = 1 AND jobs.retired_at IS NULL
           AND jobs.duplicate_of IS NULL"
    );
    let hidden_list = crate::status::sql_list(&crate::status::settled_values());
    let filter =
        format!(" AND (job_status.status IS NULL OR job_status.status NOT IN ({hidden_list}))");
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
/// APPENDS. The first user's rule for every note in the app: nothing a person wrote is
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
    conn.execute(
        "INSERT INTO job_status_log (key, status, note, at) VALUES (?1, '', NULL, ?2)",
        params![key, now],
    )?;
    Ok(())
}

/// Take rows out of the person's lists. Returns how many moved.
///
/// HIDES rather than deletes. The first user asked for delete; this is the same result
/// with a way back, because a bulk action over a multi-select is one misclick
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
/// Employer -> ATS resolution is a fact about the EMPLOYER (Covenant Health
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
/// count. Reads the LOG, so a job since marked Denied still counts as one
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
            "No longer open",
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
        // the first user's point, as an assertion. Applied reads the LOG and can only
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
        // the first user renamed Denied to No Offer. A database written by an earlier
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
             VALUES ('Covenant Health', 'covenanthealth.com', 'https://x/careers',
                     'greenhouse', 'covenant', 'ok', '2026-08-01')",
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
                   ("Covenant Health", "greenhouse", "covenant"));
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
             VALUES ('Covenant Health', 'greenhouse', 'covenant', 'ok', 'seeded')",
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
             VALUES ('Covenant Health', 'greenhouse', 'covenant', 'ok');",
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
        assert_eq!(name, "Covenant Health");
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
/// the first user's design, 2026-08-06: the scheduled refresh keeps handling everything
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
