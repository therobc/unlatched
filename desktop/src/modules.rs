//! The dashboard modules: what each one counts, and the list it opens.
//!
//! ONE DEFINITION PER MODULE, read by all three places that need it - the card
//! on the dashboard, the segment in the donut, and the list you land on when
//! you click it. The requirement: each of those is its OWN list rather than a
//! filtered Triage list, and a change to one is reflected app-wide.
//!
//! THE BUG THIS SHAPE PREVENTS, which was live at the time. The card said
//! AWAITING A REPLY and counted applications silent for 14+ days; clicking it
//! went to the Pipeline, which counts something else entirely. Two numbers,
//! two definitions, no way to tell they disagreed. Here the count and the list
//! are the SAME WHERE clause, so a card showing 53 opens a list of 53 rows or
//! the discrepancy is a bug in one place rather than a difference of opinion
//! between two.
//!
//! WHY A WHERE FRAGMENT AND NOT A ROW PREDICATE. The old TriageFilter filtered
//! rows the triage query had already loaded, so every module was necessarily a
//! subset of Triage - which is exactly what "its own list" rules out. "Taken
//! down" and "No Offer" are rows Triage deliberately hides.

use crate::status;

/// Which module. The stable identity - `key()` is what the nav and the GUI
/// harness address it by, so these names are load-bearing.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Module {
    /// Kept, still live on its board, not yet closed out. The pile a person is
    /// actually working through, and the total the others are slices of.
    OpenPositions,
    NewSinceLastRun,
    PostedThisWeek,
    /// Matched, but the pay came in under the floor. Above the fallback
    /// floor, or screening would have dropped it outright.
    BelowSalary,
    /// Matched, and fell short on something that is not the money: an
    /// employment type the search did not ask for, a requirement the profile
    /// rules out, or a description too thin to judge.
    ///
    /// THE CATCH-ALL OF THE TWO, deliberately. A row can be forced to `alt`
    /// without ever being screened - a hand-added job, an import - and carry
    /// no reason at all. Its clause takes those, so every alt row is on
    /// exactly one card and none is unreachable.
    ///
    /// The four triggers are counted in the engine's screen.py, which assigns
    /// a reason at exactly four places; the partition is tested over all four
    /// stored values in db::tests, including the empty and NULL cases.
    RequirementsNotAligned,
    /// Current status is Applied: sent, nothing heard back yet.
    AwaitingReply,
    /// EVER applied, from the append-only log. Cumulative, and the reason this
    /// and AwaitingReply are not the same number once anything progresses.
    Applied,
    /// The employer pulled the posting.
    TakenDown,
    /// The employer pulled a posting this person had an application in flight
    /// on.
    ///
    /// NOT IN `MODULES`, so it draws no card. It is reached by clicking the
    /// sentence on the dashboard, which already existed and already explains
    /// why it is a sentence rather than a card - see views::dashboard_view,
    /// "Deliberately a sentence, not a sixth card".
    ///
    /// Its clause is the one `dashboard::load` counts the sentence with, so the
    /// number in the sentence and the length of the list it opens cannot drift.
    WithdrawnAfterApplying,
    /// One status, as its own list - No Offer and Declined Offer.
    Status(&'static str),
}

/// The modules on the dashboard, left to right.
///
/// ORDER IS THE READING ORDER: the size of the pile, then what changed, then
/// what needs a decision, then how far things got, then what ended. A card row
/// sorted by anything else reads as a pile of numbers rather than a state of
/// play.
pub const MODULES: [Module; 10] = [
    Module::OpenPositions,
    Module::NewSinceLastRun,
    Module::PostedThisWeek,
    Module::BelowSalary,
    Module::RequirementsNotAligned,
    Module::Applied,
    Module::AwaitingReply,
    Module::Status("no_offer"),
    Module::Status("declined_offer"),
    Module::TakenDown,
];

impl Module {
    /// Which of the two cards that split the alt pile a row belongs to.
    ///
    /// The dashboard decides with a WHERE clause and the triage badge decides
    /// with this, from a value on a row already loaded - two decisions about
    /// the same thing, which is how a badge comes to name a card the row is
    /// not counted on. They are held together by a test that runs both over
    /// the same database rather than by matching code, because they cannot
    /// share an implementation: one is SQL and one is not.
    ///
    /// ANYTHING THAT IS NOT THE PAY CASE lands on the requirements card,
    /// including the empty string a row forced to alt without being screened
    /// carries. See that module's clause for why it is written that way.
    pub fn for_alt_reason(reason: &str) -> Module {
        if reason == "salary" {
            Module::BelowSalary
        } else {
            Module::RequirementsNotAligned
        }
    }

    /// Stable identity for the nav and the harness. Never shown to a reader.
    pub fn key(self) -> String {
        match self {
            Module::OpenPositions => "open-positions".to_string(),
            Module::NewSinceLastRun => "new-this-collection".to_string(),
            Module::PostedThisWeek => "posted-this-week".to_string(),
            Module::BelowSalary => "below-salary".to_string(),
            Module::RequirementsNotAligned => "requirements-not-aligned".to_string(),
            Module::AwaitingReply => "awaiting-reply".to_string(),
            Module::Applied => "applied".to_string(),
            Module::TakenDown => "taken-down".to_string(),
            Module::WithdrawnAfterApplying => "withdrawn-after-applying".to_string(),
            Module::Status(value) => format!("status-{}", value.replace('_', "-")),
        }
    }

    /// The card label, upper-cased by the card itself.
    pub fn label(self) -> String {
        match self {
            Module::OpenPositions => "OPEN POSITIONS".to_string(),
            Module::NewSinceLastRun => "NEW THIS COLLECTION".to_string(),
            Module::PostedThisWeek => "POSTED THIS WEEK".to_string(),
            Module::BelowSalary => "BELOW SALARY".to_string(),
            Module::RequirementsNotAligned => "REQUIREMENTS NOT ALIGNED".to_string(),
            Module::AwaitingReply => "AWAITING A REPLY".to_string(),
            Module::Applied => "APPLIED".to_string(),
            Module::TakenDown => "TAKEN DOWN".to_string(),
            Module::WithdrawnAfterApplying => "TAKEN DOWN AFTER YOU APPLIED".to_string(),
            Module::Status(value) => status::label(value).to_uppercase(),
        }
    }

    /// The heading on the list it opens. Sentence case, because it is a title
    /// rather than a label on a figure.
    pub fn heading(self) -> String {
        match self {
            Module::OpenPositions => "Open positions".to_string(),
            Module::NewSinceLastRun => "New this collection".to_string(),
            Module::PostedThisWeek => "Posted this week".to_string(),
            Module::BelowSalary => "Below salary".to_string(),
            Module::RequirementsNotAligned => "Requirements not aligned".to_string(),
            Module::AwaitingReply => "Awaiting a reply".to_string(),
            Module::Applied => "Everything you have applied to".to_string(),
            Module::TakenDown => "Taken down".to_string(),
            Module::WithdrawnAfterApplying =>
                "Taken down after you applied".to_string(),
            Module::Status(value) => status::label(value),
        }
    }

    /// Said in words, on hover and under the list heading. Every one of these
    /// answers "why is this number not the one I expected".
    pub fn caption(self) -> String {
        match self {
            Module::OpenPositions =>
                "Matches still live on their board that you have not closed out."
                    .to_string(),
            Module::NewSinceLastRun =>
                "Arrived in the most recent collection - new to you, whatever \
                 the posting date says."
                    .to_string(),
            Module::PostedThisWeek =>
                "Posted in the last 7 days, however long we have had them."
                    .to_string(),
            Module::BelowSalary =>
                "The pay is under your salary floor but above the fallback \
                 floor you set, so it is held back rather than dropped \
                 outright. Nothing else about it fell short."
                    .to_string(),
            Module::RequirementsNotAligned =>
                "Matched your search, then fell short on something other \
                 than the money: an employment type you did not ask for, a \
                 requirement your profile rules out, or a description too \
                 thin to judge. Jobs you added by hand land here too."
                    .to_string(),
            Module::AwaitingReply =>
                "Applications with no reply recorded yet. Falls as each one \
                 moves on, which is why it is not the same as Applied."
                    .to_string(),
            Module::Applied =>
                "Every job you ever recorded an application for, including the \
                 ones that have since ended. It can only go up."
                    .to_string(),
            Module::TakenDown =>
                "The employer removed the posting. Whatever you recorded about \
                 it is untouched."
                    .to_string(),
            Module::WithdrawnAfterApplying =>
                "You had an application in flight and the employer pulled the \
                 posting. Worth closing out: record the rejection if one came, \
                 or give up on the ones that stayed silent."
                    .to_string(),
            Module::Status(value) => match status::get(value) {
                Some(s) => s.hint.to_string(),
                None => String::new(),
            },
        }
    }

    /// The colour on the card, the donut segment and the list heading.
    ///
    /// Status modules take the status palette, so a No Offer card, its donut
    /// slice and the pill on the row are recognisably one thing. The rest take
    /// a tone that says what KIND of pile it is rather than borrowing a status
    /// colour that would imply a decision nobody made.
    pub fn colour(self) -> [u8; 3] {
        match self {
            Module::OpenPositions => [34, 197, 94],
            Module::NewSinceLastRun | Module::PostedThisWeek => [59, 130, 246],
            // Two shades of the same amber rather than two unrelated hues:
            // they are one pile split in two, and a reader should see that
            // before reading either label.
            Module::BelowSalary => [217, 119, 6],
            Module::RequirementsNotAligned => [180, 83, 9],
            Module::AwaitingReply => status::colour("applied"),
            Module::Applied => [99, 102, 241],
            Module::TakenDown => [120, 130, 150],
            // THE DEFINITION, not a copy of one. This red was a private const
            // in views::dashboard_view used by nothing but that sentence;
            // it now reads this, so the sentence and the list it opens cannot
            // end up different colours.
            Module::WithdrawnAfterApplying => [239, 68, 68],
            Module::Status(value) => status::colour(value),
        }
    }

    /// The WHERE fragment that defines this module, over the triage join.
    ///
    /// THE SAME STRING COUNTS THE CARD AND BUILDS THE LIST. That is the whole
    /// point of this module - see the header.
    ///
    /// Every value interpolated here is a compile-time constant from this file
    /// or from crate::status; nothing user-supplied reaches it.
    pub fn where_clause(self) -> String {
        match self {
            // NO MODULE INHERITS TRIAGE'S HIDE-THE-SETTLED FILTER - each list
            // is built from its own clause, and No Offer would show nothing if
            // it did. So the exclusion that makes this pile "still to act on"
            // is written into the clause itself rather than assumed from the
            // screen it happens to be drawn on.
            Module::OpenPositions => format!(
                "jobs.verdict = 'keep' AND jobs.delisted_at IS NULL
                 AND jobs.key NOT IN (SELECT key FROM job_status WHERE status IN ({}))",
                status::sql_list(&status::settled_values())
            ),
            // Compared on the DATE part only. A collect runs for minutes and
            // stamps each row as it lands, so an exact timestamp match would
            // count the last row of the run and none of the others.
            Module::NewSinceLastRun =>
                "jobs.qualified = 1 AND jobs.fetched_at >= \
                 (SELECT substr(MAX(fetched_at), 1, 10) FROM jobs)"
                    .to_string(),
            // The threshold lives in dashboard.rs, which is also where the
            // triage row age reads it from - two definitions of "fresh" one
            // number apart is a difference nobody would ever notice.
            Module::PostedThisWeek => format!(
                "jobs.verdict = 'keep' AND jobs.posted_at >= date('now', '-{} day')",
                crate::dashboard::FRESH_DAYS
            ),
            Module::BelowSalary =>
                "jobs.verdict = 'alt' AND jobs.alt_reason = 'salary'".to_string(),
            // EVERY OTHER alt row, including the ones carrying no reason at
            // all: a job added by hand or imported is forced to `alt`
            // without being screened, and a profile that predates the column
            // has NULL wherever the migration could not recover the split.
            // Written as "not salary" rather than "is requirements" so those
            // land here instead of on no card at all. Tested over all four
            // stored values by
            // db::tests::every_alt_row_is_on_exactly_one_of_the_two_cards_that_split_them.
            Module::RequirementsNotAligned =>
                "jobs.verdict = 'alt' AND (jobs.alt_reason IS NULL \
                 OR jobs.alt_reason <> 'salary')"
                    .to_string(),
            Module::AwaitingReply => "job_status.status = 'applied'".to_string(),
            // THE LOG, not the current status. A job that has since been
            // rejected was still applied to, and this is the number a person
            // uses to answer "how many have I sent".
            Module::Applied =>
                "jobs.key IN (SELECT key FROM job_status_log WHERE status = 'applied')"
                    .to_string(),
            Module::TakenDown =>
                "jobs.qualified = 1 AND jobs.delisted_at IS NOT NULL".to_string(),
            // IN FLIGHT, not "ever applied": a posting pulled after the person
            // was already turned down is just an old ad coming down, and
            // putting it in this list would bury the ones still worth chasing.
            //
            // NO `qualified` FILTER, unlike TakenDown above. Screening decides
            // what to show someone who has not acted; an application already
            // sent overrules it.
            Module::WithdrawnAfterApplying => format!(
                "jobs.delisted_at IS NOT NULL AND job_status.status IN ({})",
                status::sql_list(&status::in_flight_values())
            ),
            Module::Status(value) => format!("job_status.status = '{value}'"),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn every_module_has_a_distinct_key_and_label() {
        // The key addresses the list from the nav and the harness; a collision
        // would make one of them unreachable and the other ambiguous.
        let keys: std::collections::HashSet<String> =
            MODULES.iter().map(|m| m.key()).collect();
        let labels: std::collections::HashSet<String> =
            MODULES.iter().map(|m| m.label()).collect();
        assert_eq!(keys.len(), MODULES.len());
        assert_eq!(labels.len(), MODULES.len());
    }

    // (A test here asserted that Applied's clause mentions job_status_log and
    // AwaitingReply's mentions job_status.status. It was string-matching the
    // implementation - it would have passed for any clause naming those
    // tables, whatever it did with them. The real distinction is behavioural
    // and is asserted against a database in
    // db::tests::awaiting_a_reply_falls_as_applied_holds.)

    #[test]
    fn a_status_module_names_a_status_that_exists() {
        for module in MODULES.iter() {
            if let Module::Status(value) = module {
                assert!(
                    status::get(value).is_some(),
                    "{value} is a module but not a status"
                );
            }
        }
    }

    #[test]
    fn open_positions_excludes_every_settled_status() {
        // Hand-written IN lists here are how a new status silently kept
        // counting as an open position. Built from status::settled_values, so
        // adding one cannot be forgotten.
        let clause = Module::OpenPositions.where_clause();
        for value in status::settled_values() {
            assert!(clause.contains(value), "{value} is settled but not excluded");
        }
    }

    #[test]
    fn no_clause_is_empty() {
        // An empty fragment would silently widen its list to every row in the
        // database rather than failing, and the card above it would still show
        // a plausible-looking number.
        //
        // EVERY VARIANT, not just the ones on a card. WithdrawnAfterApplying
        // is deliberately absent from MODULES - it is reached by clicking the
        // sentence on the dashboard - so a loop over MODULES alone leaves the
        // one module nobody would think to check as the only unchecked one.
        for module in every_module() {
            assert!(!module.where_clause().trim().is_empty(), "{module:?}");
            assert!(!module.heading().trim().is_empty(), "{module:?}");
            assert!(!module.caption().trim().is_empty(), "{module:?}");
            assert!(!module.label().trim().is_empty(), "{module:?}");
            assert!(!module.key().trim().is_empty(), "{module:?}");
        }
    }

    /// Every module this file can produce, card or not.
    fn every_module() -> Vec<Module> {
        let mut all = MODULES.to_vec();
        all.push(Module::WithdrawnAfterApplying);
        all
    }

    /// The off-card module needs a key of its own too: the nav and the
    /// harness address it exactly like the others, and a collision would
    /// send one of them to the wrong list.
    #[test]
    fn the_off_card_module_does_not_collide_with_a_card() {
        let keys: std::collections::HashSet<String> =
            every_module().iter().map(|m| m.key()).collect();
        assert_eq!(keys.len(), every_module().len(), "two modules share a key");
    }
}
