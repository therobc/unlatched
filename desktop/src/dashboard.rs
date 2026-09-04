// Dashboard statistics: the counts behind the landing view.
//
// Every figure here is a plain SQL count over data the app already stores.
// Nothing is derived by a model and nothing needs the network, which is the
// same constraint the rest of the app runs under.
//
// WHY THESE NUMBERS AND NOT A SCOREBOARD
// A dashboard built only on what a person records AFTER applying can be a
// pipeline tracker and nothing else: totals, a status doughnut, a funnel.
// This app COLLECTS as well as tracks, so it has a second half a tracker has
// no notion of - what arrived overnight, what was withdrawn, and which of
// your employers it cannot even read.
//
// The discovery half comes FIRST: a person opening this app on a Tuesday
// morning has not applied to most of what is on screen. So the ordering is
// what changed since you last looked, then what needs a decision from you,
// then how the search is doing.

use std::collections::HashMap;

use rusqlite::{Connection, Result as SqlResult};

/// Postings older than this are past the point where a listing is usually
/// still live. Same threshold the triage row age uses.
pub const FRESH_DAYS: i64 = 7;

/// An application with no change for this long is the thing most worth being
/// told about: applications are lost to silence far more often than to a
/// rejection, and nothing else in the app ever said how long one had waited.
pub const SILENT_DAYS: i64 = 14;

// How far each status proves a job got, and which ones mean the employer came
// back, both now live in crate::status - the two used to be hand-written match
// arms here, and one of them (is_response) had gone stale against a vocabulary
// that grew without it.

/// How many jobs are known to have reached each stage of the ladder.
///
/// Cumulative, so the bars can only shrink: somebody turned down after an
/// interview still reached the interview. It is a FLOOR - a person who marks a
/// job straight to No Offer never recorded the interview it may have had - and
/// every caller says so rather than implying a precision the data does not
/// carry.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct Reached {
    pub applied: i64,
    pub interviewed: i64,
    pub offer: i64,
    /// Offers the person took. Between Offer and Hired because accepting is
    /// not starting - a background check, a pulled requisition or a freeze
    /// still sits between the two, and that gap has to be visible rather than
    /// folded into either neighbour.
    pub accepted: i64,
    pub hired: i64,
    /// Jobs the employer came back on, either way.
    pub responded: i64,
}

impl Reached {
    /// Share of applications that drew any reply. `None` when nothing has been
    /// applied to - a rate out of zero is not 0%, it is not a number.
    pub fn response_rate(&self) -> Option<f64> {
        (self.applied > 0).then(|| 100.0 * self.responded as f64 / self.applied as f64)
    }
}

/// Fold `(key, status)` pairs from the append-only log into stage counts.
///
/// The LOG, not the current status: a job that moved Applied -> No Offer has to
/// keep counting as an application, and the current status alone has forgotten
/// that. Each key is counted once per stage no matter how many times it was
/// logged, which is what stops a person who re-marked a row from inflating
/// their own funnel.
pub fn reached_from_log<'a>(entries: impl Iterator<Item = (&'a str, &'a str)>) -> Reached {
    use std::collections::{HashMap, HashSet};
    let mut best: HashMap<&str, usize> = HashMap::new();
    let mut responded: HashSet<&str> = HashSet::new();
    for (key, status) in entries {
        if let Some(r) = crate::status::rung(status) {
            let slot = best.entry(key).or_insert(r);
            *slot = (*slot).max(r);
        }
        if crate::status::is_response(status) {
            responded.insert(key);
        }
    }
    let at_least = |r: usize| best.values().filter(|b| **b >= r).count() as i64;
    Reached {
        applied: at_least(0),
        interviewed: at_least(1),
        offer: at_least(2),
        accepted: at_least(3),
        hired: at_least(4),
        responded: responded.len() as i64,
    }
}

#[derive(Debug, Clone, Default)]
pub struct DashboardStats {
    /// When the BUILT-IN boards were last read. Deliberately NOT the
    /// newest row overall: see db::boards_last_collected.
    pub last_collected: Option<String>,
    /// Withdrawn AND you had marked it Applied. A different event from a job
    /// you never acted on expiring, and the one worth interrupting someone for.
    ///
    /// Not a module: it is a SENTENCE on the dashboard, because it is an
    /// intersection of two other cards rather than a pile of its own.
    pub withdrawn_after_applying: i64,
    /// Applications with no change for SILENT_DAYS. Also a sentence, for the
    /// same reason - "silent for a fortnight" is a property of the Awaiting a
    /// reply pile, not a separate one.
    pub waiting_on_reply: i64,

    // (new_since_last_run, open_positions, withdrawn, fresh_matches and
    // alt_pile stood here. Every one of them was a second, hand-written
    // reading of a number that now comes from the module's own WHERE clause -
    // exactly the duplication that let a card and its list disagree. They are
    // in module_counts.)

    // Band 3 - how the search is doing.
    /// Ladder counts for the funnel, read from the append-only log so a job
    /// that has since been denied still counts as the application it was.
    pub reached: Reached,
    pub by_status: Vec<(String, i64)>,
    /// Source, how many it brought, and when it last brought one.
    ///
    /// The timestamp is what makes a dead feed visible: a count alone stays
    /// true and stops meaning anything the moment a collector stops.
    pub by_source: Vec<(String, i64, Option<String>)>,
    /// This machine's offset from UTC, for the one rule that needs a wall
    /// clock. See local_offset_secs.
    ///
    /// THE OFFSET, NOT THE TIME. Storing "seconds into the day" here read the
    /// clock when the stats were built, and stats are only rebuilt when the
    /// data changes - so on the day a collector went silent, nothing would
    /// change, this number would stay frozen at whenever the app was opened,
    /// and the deadline would never be reached. The view reads the time itself.
    pub local_offset_secs: i64,
    pub employers_total: i64,
    pub employers_readable: i64,
    pub top_gaps: Vec<(String, i64)>,
    pub keeps: i64,
    /// Every stored posting, verdict or not. See nothing_collected().
    pub jobs_total: i64,
    /// One count per dashboard module, keyed by Module::key().
    ///
    /// COUNTED BY THE MODULE'S OWN WHERE CLAUSE - the same one that builds the
    /// list it opens. The fields above are the older hand-written counts, kept
    /// for the sentences and the funnel that read them; anything on a CARD
    /// comes from here, so a card and its list cannot disagree.
    pub module_counts: HashMap<String, i64>,
}

impl DashboardStats {
    /// True when nothing has been collected yet: the whole screen below the
    /// freshness line would be rows of zeros, which reads as a broken app
    /// rather than an empty one.
    ///
    /// Counts JOBS, not verdicts. Keying this on "no keeps and no alts" made
    /// the dashboard contradict itself on the first real screenshot - it said
    /// "nothing has been collected yet" directly under "6 new in the last
    /// collection". Rows collected before the verdict column existed have no
    /// verdict, which is precisely the state a database being migrated is in.
    pub fn nothing_collected(&self) -> bool {
        self.jobs_total == 0
    }

    /// True when the person has not recorded a single decision. Their pipeline
    /// is not empty-looking, it genuinely has not started, and a doughnut of
    /// zeros says the wrong thing about that.
    pub fn nothing_applied_to(&self) -> bool {
        self.by_status.is_empty()
    }
}

/// Source, count, and the newest row that source produced.
///
/// MAX(fetched_at) rather than a separate "last run" table: the newest row IS
/// the evidence the source delivered, and a table recording that it ran would
/// be a second place for the same fact to be wrong.
///
/// THIS IS THE TIME OF THE LAST DELIVERY, NOT THE LAST NEW JOB, which is what
/// makes it safe to raise a staleness badge from. Measured rather than assumed:
/// of the 63 jobs in the 2026-08-24 handoff, 11 were already in the database
/// from two days before, and every one of them took the new stamp anyway. So a
/// collector that runs and finds nothing new still reads as alive - otherwise
/// the warning would land on precisely the quiet day when nothing was wrong.
///
/// A collector that delivers an EMPTY file is still indistinguishable from one
/// that never ran, and that much is fair: from here they are the same event.
fn source_rows(
    conn: &Connection,
    sql: &str,
) -> SqlResult<Vec<(String, i64, Option<String>)>> {
    let mut stmt = conn.prepare(sql)?;
    let rows = stmt.query_map([], |r| {
        Ok((r.get::<_, String>(0)?, r.get::<_, i64>(1)?, r.get::<_, Option<String>>(2)?))
    })?;
    rows.collect()
}

fn count(conn: &Connection, sql: &str) -> SqlResult<i64> {
    conn.query_row(sql, [], |r| r.get(0))
}

/// DISTINCT because a person who marks the same job Applied twice has made one
/// application; the fold would collapse it anyway, but not reading the
/// duplicates keeps this cheap on a long history.
fn reached(conn: &Connection) -> SqlResult<Reached> {
    let mut stmt = conn.prepare(
        "SELECT DISTINCT key, COALESCE(status, '') FROM job_status_log",
    )?;
    let rows: Vec<(String, String)> = stmt
        .query_map([], |r| Ok((r.get(0)?, r.get(1)?)))?
        .collect::<SqlResult<_>>()?;
    Ok(reached_from_log(
        rows.iter().map(|(k, s)| (k.as_str(), s.as_str())),
    ))
}

/// `external_ids` are the configured collectors, from the engine's own list.
/// They are excluded from the freshness stamp so the board sweep - which runs
/// whenever the app runs - stops answering on their behalf.
pub fn load(conn: &Connection, external_ids: &[String]) -> SqlResult<DashboardStats> {
    let last_collected: Option<String> = crate::db::boards_last_collected(conn, external_ids)?;

    Ok(DashboardStats {
        last_collected,
        local_offset_secs: crate::db::local_offset_secs(conn),
        // In flight, so a posting pulled while the person was still waiting on
        // it counts - and one pulled after they had already been turned down
        // does not, because that is just an old ad coming down.
        // COUNTED THROUGH THE MODULE, not by a query that says the same thing
        // in its own words. The sentence is now a link to
        // Module::WithdrawnAfterApplying's list, and this is the same function
        // that list is built from - so the number in the sentence and the rows
        // it opens are one statement used twice rather than two that agree
        // today. Asserted by the_sentence_count_matches_the_list_it_opens.
        //
        // It also gains MODULE_EXCLUSIONS, which the hand-written query above
        // lacked: a row the person retired or one folded behind its duplicate
        // is no longer counted here, because clicking through would not have
        // shown it.
        withdrawn_after_applying: crate::db::count_module(
            conn,
            crate::modules::Module::WithdrawnAfterApplying,
        )?,
        // Compared against the job_status timestamp, which is when the person
        // last moved the row - not when the job was posted or collected.
        waiting_on_reply: conn.query_row(
            &format!(
                "SELECT COUNT(*) FROM job_status
                 WHERE status = 'applied' AND updated < date('now', '-{SILENT_DAYS} day')"
            ),
            [],
            |r| r.get(0),
        )?,
        keeps: count(conn, "SELECT COUNT(*) FROM jobs WHERE verdict = 'keep'")?,
        jobs_total: count(conn, "SELECT COUNT(*) FROM jobs")?,
        reached: reached(conn)?,
        // DECISIONS THE PERSON MADE, not every row carrying a status.
        //
        // Auto-close writes 'closed' onto postings that were sitting at "not
        // set" when the employer pulled them - 805 of 863 on the profile this
        // was measured against. Counting those made the ring 93% a colour that
        // means "nobody looked at this", and the centre called the total
        // applications.
        //
        // A posting that was applied to and THEN pulled keeps its own status
        // (see the taken-down guard), so this excludes the untouched ones
        // without hiding a single application.
        by_status: pairs(
            conn,
            "SELECT status, COUNT(*) FROM job_status
             WHERE status != 'closed'
             GROUP BY status ORDER BY 2 DESC",
        )?,
        // WHICH COLLECTOR FOUND THE JOB - answered from data the app already
        // holds, rather than from a field a human has to type.
        by_source: source_rows(
            conn,
            "SELECT COALESCE(source, 'unknown'), COUNT(*), MAX(fetched_at)
             FROM jobs WHERE verdict = 'keep' GROUP BY 1 ORDER BY 2 DESC",
        )?,
        employers_total: count(conn, "SELECT COUNT(*) FROM companies")?,
        // Readable means we resolved a job board we can actually collect
        // from. This is the number that explains a thin result set: a person
        // with forty employers and eleven readable boards is not looking at a
        // broken app, and nothing else on the screen would tell them that.
        employers_readable: count(
            conn,
            "SELECT COUNT(*) FROM companies WHERE ats IS NOT NULL AND ats != ''",
        )?,
        top_gaps: top_gaps(conn)?,
        module_counts: module_counts(conn)?,
    })
}

/// Every module's count, from the module's own definition.
fn module_counts(conn: &Connection) -> SqlResult<HashMap<String, i64>> {
    let mut out = HashMap::new();
    for module in crate::modules::MODULES {
        out.insert(module.key(), crate::db::count_module(conn, module)?);
    }
    Ok(out)
}

fn pairs(conn: &Connection, sql: &str) -> SqlResult<Vec<(String, i64)>> {
    let mut stmt = conn.prepare(sql)?;
    let rows = stmt.query_map([], |r| Ok((r.get::<_, String>(0)?, r.get::<_, i64>(1)?)))?;
    rows.collect()
}

/// The skills most often asked for by jobs worth having, that the resume does
/// not evidence. Split in Rust rather than SQL because missing_skills is
/// stored as one comma-joined string per row - a shape chosen so the triage
/// row can print it without a join.
fn top_gaps(conn: &Connection) -> SqlResult<Vec<(String, i64)>> {
    let mut stmt = conn.prepare(
        "SELECT missing_skills FROM jobs
         WHERE verdict = 'keep' AND missing_skills IS NOT NULL AND missing_skills != ''",
    )?;
    let rows = stmt.query_map([], |r| r.get::<_, String>(0))?;
    let mut tally: std::collections::HashMap<String, i64> = std::collections::HashMap::new();
    for row in rows {
        for skill in row?.split(", ") {
            let skill = skill.trim();
            if !skill.is_empty() {
                *tally.entry(skill.to_string()).or_insert(0) += 1;
            }
        }
    }
    let mut out: Vec<(String, i64)> = tally.into_iter().collect();
    // Count first, then name, so the order is stable between runs rather than
    // shuffling with the hash map's iteration order.
    out.sort_by(|a, b| b.1.cmp(&a.1).then_with(|| a.0.cmp(&b.0)));
    out.truncate(6);
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Every status beyond 'applied' stays in the breakdown.
    ///
    /// Worth pinning because a board whose applications all sit at 'applied'
    /// has none of these rungs populated yet, so a regression here would be
    /// invisible on screen until the first interview.
    ///
    /// The list is taken from status::FLOW rather than retyped, so a status
    /// added to the app is automatically covered instead of quietly missing.
    #[test]
    fn every_status_a_person_can_set_stays_in_the_breakdown() {
        let conn = Connection::open_in_memory().unwrap();
        conn.execute_batch(crate::db::SCHEMA_SQL).unwrap();

        for (i, status) in crate::status::FLOW.iter().enumerate() {
            conn.execute(
                "INSERT INTO job_status (key, status, updated) VALUES (?1, ?2, '2026-08-01')",
                rusqlite::params![format!("gh:{i}"), status.value],
            )
            .unwrap();
        }
        // And one auto-closure, which must be the only thing dropped.
        conn.execute(
            "INSERT INTO job_status (key, status, updated)
             VALUES ('gh:closed', 'closed', '2026-08-01')",
            [],
        )
        .unwrap();

        let rows = pairs(
            &conn,
            "SELECT status, COUNT(*) FROM job_status
             WHERE status != 'closed'
             GROUP BY status ORDER BY 2 DESC",
        )
        .unwrap();
        let names: Vec<&str> = rows.iter().map(|(s, _)| s.as_str()).collect();

        for status in crate::status::FLOW.iter() {
            assert!(
                names.contains(&status.value),
                "{} fell out of the status breakdown",
                status.value
            );
        }
        assert!(!names.contains(&"closed"), "the auto-closure must be dropped");
        assert_eq!(
            rows.len(),
            crate::status::FLOW.len(),
            "exactly the settable statuses, nothing more: {rows:?}"
        );
    }

    /// The status breakdown counts DECISIONS, not every row with a status.
    ///
    /// Auto-close writes 'closed' onto postings that were at "not set" when the
    /// employer pulled them. Counting those made the ring 805 of 863 on the
    /// live profile - a breakdown of applications that was 93% postings nobody
    /// had looked at, under a centre figure reading "863 applications".
    ///
    /// THE SECOND HALF IS THE ONE THAT MATTERS: a posting applied to and THEN
    /// pulled keeps its own status and must still be counted. A fix that
    /// dropped every delisted row would pass a test that only checked the
    /// first half, and would quietly erase real applications.
    #[test]
    fn the_breakdown_drops_untouched_closures_but_keeps_applications() {
        let conn = Connection::open_in_memory().unwrap();
        conn.execute_batch(crate::db::SCHEMA_SQL).unwrap();
        conn.execute(
            "INSERT INTO job_status (key, status, updated) VALUES
             ('gh:1', 'closed',  '2026-08-01'),
             ('gh:2', 'closed',  '2026-08-01'),
             ('gh:3', 'applied', '2026-08-01'),
             ('gh:4', 'pass',    '2026-08-01')",
            [],
        )
        .unwrap();

        let rows = pairs(
            &conn,
            "SELECT status, COUNT(*) FROM job_status
             WHERE status != 'closed'
             GROUP BY status ORDER BY 2 DESC",
        )
        .unwrap();

        let names: Vec<&str> = rows.iter().map(|(s, _)| s.as_str()).collect();
        assert!(!names.contains(&"closed"), "untouched closures must not count");
        assert!(names.contains(&"applied"), "an application still counts");
        assert!(names.contains(&"pass"), "a decision to pass still counts");

        let total: i64 = rows.iter().map(|(_, n)| *n).sum();
        assert_eq!(total, 2, "two decisions, not four rows: {rows:?}");
    }

    use crate::db;

    /// What a module's card shows, by key.
    ///
    /// The tests below assert on THIS rather than on a field of DashboardStats
    /// because it is what a person reads: the module count is the number on
    /// the card and the length of the list behind it.
    fn module_count(stats: &DashboardStats, module: crate::modules::Module) -> i64 {
        *stats
            .module_counts
            .get(&module.key())
            .unwrap_or_else(|| panic!("no count for module {}", module.key()))
    }

    fn seeded() -> Connection {
        let conn = Connection::open_in_memory().unwrap();
        conn.execute_batch(db::SCHEMA_SQL).unwrap();
        // SCHEMA_SQL is the ORIGINAL shape. Without this the fixture is a
        // database no install has had since the first release, and every
        // module clause reading a migrated column fails on it.
        db::migrate(&conn).unwrap();
        conn.execute_batch(
            "INSERT INTO companies (id, name, ats) VALUES
                (1, 'Acme', 'greenhouse'), (2, 'Nimbus', NULL), (3, 'Vex', 'lever');
             INSERT INTO jobs (key, company_id, title, verdict, qualified, source,
                               missing_skills, fetched_at, posted_at, alt_reason) VALUES
                ('gh:1', 1, 'Analyst', 'keep', 1, 'greenhouse', 'SQL, Excel',
                 '2026-08-05T09:00:00', date('now'), ''),
                ('gh:2', 1, 'Engineer', 'keep', 1, 'greenhouse', 'SQL',
                 '2026-08-05T09:00:00', date('now','-30 day'), ''),
                ('lv:3', 3, 'Support', 'alt', 1, 'lever', '',
                 '2026-08-01T09:00:00', date('now','-2 day'), 'requirements'),
                ('gh:4', 1, 'Coordinator', 'alt', 1, 'greenhouse', '',
                 '2026-08-01T09:00:00', date('now','-2 day'), 'salary');",
        )
        .unwrap();
        conn
    }

    #[test]
    fn open_positions_excludes_decided_and_taken_down_rows() {
        let conn = seeded();
        conn.execute_batch(
            "UPDATE jobs SET delisted_at = '2026-08-05' WHERE key = 'gh:2';
             INSERT INTO job_status (key, status, updated)
             VALUES ('lv:3', 'pass', '2026-08-01');",
        )
        .unwrap();
        let stats = load(&conn, &[]).unwrap();
        // Two keeps; one was taken down, and the alt row was passed on.
        assert_eq!(module_count(&stats, crate::modules::Module::OpenPositions), 1);
    }

    #[test]
    fn counts_come_from_the_stored_data() {
        let stats = load(&seeded(), &[]).unwrap();
        assert_eq!(stats.keeps, 2);
        // The two halves of the old single alt card. Asserted separately
        // because one clause matching both would have counted 2 and 0 and
        // still looked like a working split.
        assert_eq!(module_count(&stats, crate::modules::Module::BelowSalary), 1);
        assert_eq!(
            module_count(&stats, crate::modules::Module::RequirementsNotAligned),
            1
        );
        assert_eq!(stats.employers_total, 3);
        // Nimbus has no resolved board, so it is not readable.
        assert_eq!(stats.employers_readable, 2);
    }

    #[test]
    fn posted_this_week_means_recently_posted_not_recently_collected() {
        let stats = load(&seeded(), &[]).unwrap();
        // Two keeps, but the 30-day-old one was not posted this week.
        assert_eq!(module_count(&stats, crate::modules::Module::PostedThisWeek), 1);
    }

    #[test]
    fn gaps_are_tallied_across_rows_and_ranked() {
        let stats = load(&seeded(), &[]).unwrap();
        assert_eq!(stats.top_gaps[0], ("SQL".to_string(), 2));
        assert!(stats.top_gaps.iter().any(|(s, n)| s == "Excel" && *n == 1));
    }

    #[test]
    fn collected_jobs_without_a_verdict_still_count_as_collected() {
        // The screenshot bug: rows collected before the verdict column existed
        // have no verdict, and the dashboard announced "nothing collected yet"
        // directly above a count of what it had just collected.
        let conn = seeded();
        conn.execute_batch("UPDATE jobs SET verdict = NULL").unwrap();
        let stats = load(&conn, &[]).unwrap();
        assert_eq!(stats.keeps, 0);
        assert!(!stats.nothing_collected());
    }

    #[test]
    fn an_empty_database_reports_empty_rather_than_erroring() {
        let conn = Connection::open_in_memory().unwrap();
        conn.execute_batch(db::SCHEMA_SQL).unwrap();
        db::migrate(&conn).unwrap();
        let stats = load(&conn, &[]).unwrap();
        assert!(stats.nothing_collected());
        assert!(stats.nothing_applied_to());
        assert!(stats.last_collected.is_none());
        // EVERY module reports, and reports zero. A missing key would panic in
        // module_count; a module quietly absent from the map would leave its
        // card showing 0 forever whatever the database held.
        for module in crate::modules::MODULES {
            assert_eq!(module_count(&stats, module), 0, "{}", module.key());
        }
    }

    #[test]
    fn a_withdrawn_job_you_applied_to_is_counted_separately() {
        let conn = seeded();
        conn.execute_batch(
            "UPDATE jobs SET delisted_at = '2026-08-05' WHERE key IN ('gh:1','gh:2');
             INSERT INTO job_status (key, status, updated)
             VALUES ('gh:1', 'applied', '2026-08-01T09:00:00');",
        )
        .unwrap();
        let stats = load(&conn, &[]).unwrap();
        assert_eq!(module_count(&stats, crate::modules::Module::TakenDown), 2);
        assert_eq!(stats.withdrawn_after_applying, 1);
    }

    #[test]
    fn waiting_on_reply_counts_only_silence_past_the_threshold() {
        let conn = seeded();
        conn.execute_batch(
            "INSERT INTO job_status (key, status, updated) VALUES
                ('gh:1', 'applied', date('now','-40 day')),
                ('gh:2', 'applied', date('now','-1 day')),
                ('lv:3', 'interviewed', date('now','-40 day'));",
        )
        .unwrap();
        let stats = load(&conn, &[]).unwrap();
        // Only the 40-day-old *applied* row. A recent one is not silence, and
        // an interview is not waiting on a first reply.
        assert_eq!(stats.waiting_on_reply, 1);
    }

    fn fold(pairs: &[(&str, &str)]) -> Reached {
        reached_from_log(pairs.iter().map(|(k, s)| (*k, *s)))
    }

    #[test]
    fn a_job_marked_straight_to_interviewed_still_counts_as_an_application() {
        // The defect a real pipeline screenshot exposed. Somebody who lands an
        // interview and marks it as such never types the word "applied", and
        // the old summary dropped them out of the denominator entirely - so
        // the response rate was computed against fewer applications than had
        // been made, and read far higher than the truth.
        let r = fold(&[("a", "interviewed"), ("b", "applied")]);
        assert_eq!(r.applied, 2);
        assert_eq!(r.interviewed, 1);
        assert_eq!(r.response_rate(), Some(50.0));
    }

    #[test]
    fn an_offer_is_a_response() {
        // Offer and Hired arrived with the funnel and this count was never
        // updated, so the two best outcomes a search can produce were being
        // scored as silence.
        let r = fold(&[
            ("a", "applied"),
            ("a", "offer"),
            ("b", "applied"),
            ("b", "hired"),
        ]);
        assert_eq!(r.responded, 2);
        assert_eq!(r.hired, 1);
        assert_eq!(r.offer, 2, "hired implies the offer it came from");
    }

    #[test]
    fn passing_on_a_job_or_watching_it_close_is_not_an_application() {
        // Both are things a person records about a job they never applied to.
        // Counting them would inflate the top of the funnel with jobs that
        // never entered it.
        let r = fold(&[("a", "pass"), ("b", "closed"), ("c", "")]);
        assert_eq!(r, Reached::default());
        assert_eq!(r.response_rate(), None, "a rate out of zero is not a number");
    }

    #[test]
    fn a_rejection_proves_an_application_and_nothing_further() {
        let r = fold(&[("a", "applied"), ("a", "no_offer")]);
        assert_eq!((r.applied, r.interviewed, r.responded), (1, 0, 1));
    }

    #[test]
    fn accepting_an_offer_is_its_own_rung_between_offer_and_hired() {
        // The gap that has to be visible. A person who accepted and is waiting on a
        // background check has not started, and folding them into Hired would
        // report a job they do not yet have.
        let r = fold(&[("a", "offer"), ("a", "accepted_offer")]);
        assert_eq!((r.offer, r.accepted, r.hired), (1, 1, 0));
    }

    #[test]
    fn an_offer_withdrawn_after_acceptance_keeps_the_acceptance() {
        // Accepted, hired, then the hire fell through. Every rung the person
        // genuinely reached stays counted: what happened afterwards does not
        // un-happen the offer they were given.
        let r = fold(&[
            ("a", "applied"),
            ("a", "offer"),
            ("a", "accepted_offer"),
            ("a", "offer_withdrawn"),
        ]);
        assert_eq!((r.applied, r.offer, r.accepted, r.hired), (1, 1, 1, 0));
        assert_eq!(r.responded, 1);
    }

    #[test]
    fn turning_an_offer_down_still_counts_as_having_had_one() {
        let r = fold(&[("a", "offer"), ("a", "declined_offer")]);
        assert_eq!((r.applied, r.offer, r.accepted), (1, 1, 0));
    }

    #[test]
    fn re_marking_one_job_does_not_inflate_its_own_funnel() {
        // A person correcting a mis-click appends another log row. Counting
        // rows rather than jobs would let that person out-apply themselves.
        let r = fold(&[("a", "applied"), ("a", "applied"), ("a", "applied")]);
        assert_eq!(r.applied, 1);
    }

    #[test]
    fn going_backwards_keeps_the_furthest_rung_reached() {
        // Marking an interviewed job back to Applied by mistake, or recording
        // a second round out of order, must not erase the interview.
        let r = fold(&[("a", "interviewed"), ("a", "applied")]);
        assert_eq!(r.interviewed, 1);
    }
}
