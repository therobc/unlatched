//! The dashboard modules: what each one counts, and the list it opens.
//!
//! ONE DEFINITION PER MODULE, read by all three places that need it - the card
//! on the dashboard, the segment in the donut, and the list you land on when
//! you click it. The first user's requirement in his own words: "I don't want a filtered
//! Triage list, I want each one of those to be its own list and for changes to
//! reflect app-wide."
//!
//! THE BUG THIS SHAPE PREVENTS, which was live when he asked. The card said
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
    /// Matched but fell short somewhere - the first user asked for these to be reachable
    /// rather than buried in the list with a marker.
    Alt,
    /// Current status is Applied: sent, nothing heard back yet.
    AwaitingReply,
    /// EVER applied, from the append-only log. Cumulative, and the reason this
    /// and AwaitingReply are not the same number once anything progresses.
    Applied,
    /// The employer pulled the posting.
    TakenDown,
    /// One status, as its own list. The first user named No Offer and Declined Offer.
    Status(&'static str),
}

/// The modules on the dashboard, left to right.
///
/// ORDER IS THE READING ORDER: the size of the pile, then what changed, then
/// what needs a decision, then how far things got, then what ended. A card row
/// sorted by anything else reads as a pile of numbers rather than a state of
/// play.
pub const MODULES: [Module; 9] = [
    Module::OpenPositions,
    Module::NewSinceLastRun,
    Module::PostedThisWeek,
    Module::Alt,
    Module::Applied,
    Module::AwaitingReply,
    Module::Status("no_offer"),
    Module::Status("declined_offer"),
    Module::TakenDown,
];

impl Module {
    /// Stable identity for the nav and the harness. Never shown to a reader.
    pub fn key(self) -> String {
        match self {
            Module::OpenPositions => "open-positions".to_string(),
            Module::NewSinceLastRun => "new-this-collection".to_string(),
            Module::PostedThisWeek => "posted-this-week".to_string(),
            Module::Alt => "near-misses".to_string(),
            Module::AwaitingReply => "awaiting-reply".to_string(),
            Module::Applied => "applied".to_string(),
            Module::TakenDown => "taken-down".to_string(),
            Module::Status(value) => format!("status-{}", value.replace('_', "-")),
        }
    }

    /// The card label, upper-cased by the card itself.
    pub fn label(self) -> String {
        match self {
            Module::OpenPositions => "OPEN POSITIONS".to_string(),
            Module::NewSinceLastRun => "NEW THIS COLLECTION".to_string(),
            Module::PostedThisWeek => "POSTED THIS WEEK".to_string(),
            Module::Alt => "NEAR MISSES".to_string(),
            Module::AwaitingReply => "AWAITING A REPLY".to_string(),
            Module::Applied => "APPLIED".to_string(),
            Module::TakenDown => "TAKEN DOWN".to_string(),
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
            Module::Alt => "Near misses".to_string(),
            Module::AwaitingReply => "Awaiting a reply".to_string(),
            Module::Applied => "Everything you have applied to".to_string(),
            Module::TakenDown => "Taken down".to_string(),
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
            Module::Alt =>
                "Matched your search but fell short somewhere: pay under the \
                 floor, a stated requirement, or a description too thin to judge."
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
            Module::Alt => [217, 119, 6],
            Module::AwaitingReply => status::colour("applied"),
            Module::Applied => [99, 102, 241],
            Module::TakenDown => [120, 130, 150],
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
            Module::Alt => "jobs.verdict = 'alt'".to_string(),
            Module::AwaitingReply => "job_status.status = 'applied'".to_string(),
            // THE LOG, not the current status. A job that has since been
            // rejected was still applied to, and this is the number a person
            // uses to answer "how many have I sent".
            Module::Applied =>
                "jobs.key IN (SELECT key FROM job_status_log WHERE status = 'applied')"
                    .to_string(),
            Module::TakenDown =>
                "jobs.qualified = 1 AND jobs.delisted_at IS NOT NULL".to_string(),
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
        for module in MODULES.iter() {
            assert!(!module.where_clause().trim().is_empty(), "{module:?}");
            assert!(!module.heading().trim().is_empty(), "{module:?}");
            assert!(!module.caption().trim().is_empty(), "{module:?}");
        }
    }
}
