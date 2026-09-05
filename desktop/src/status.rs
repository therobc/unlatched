//! The status vocabulary, in one place.
//!
//! WHY THIS MODULE EXISTS. The vocabulary was spelled out in five files:
//! `STATUS_CHOICES` in views/triage.rs; `status_colour` and `status_label` in
//! views/pipeline.rs; `status_colour` and `FUNNEL` in views/dashboard_view.rs;
//! `rung`, `is_response` and two `IN (...)` literals in dashboard.rs; and
//! `STATUSES_HIDDEN_BY_DEFAULT` plus two more `IN (...)` literals in db.rs.
//! Adding a status meant editing all of them with nothing to fail if one were
//! missed - and the copies had ALREADY drifted: dashboard.rs's own comment
//! records that `is_response` was written before Offer and Hired existed and
//! never updated, so the two best outcomes a search can produce were being
//! scored as silence.
//!
//! Everything that renders, counts, or filters on a status reads from here.
//!
//! THE FLOW:
//!
//! ```text
//!   Applied -> Rejection Email
//!       +-> No Response
//!       +-> Interviewed -> No Offer
//!                     +-> Offer -> Declined Offer
//!                             +-> Accepted Offer -> Hired
//!                                              +-> Offer Withdrawn
//! ```
//!
//!
//! THREE WAYS AN APPLICATION ENDS WITHOUT AN OFFER, and they are not one
//! outcome. An employer who writes back to say no has answered; one who never
//! replies has not; and one who interviews you first has done something
//! different again. Recorded as a single "No Offer" they were indistinguishable
//! (changed 2026-09-05), and the response rate counted silence as a reply.
//!
//! Hired and Offer Withdrawn are BOTH reachable from Accepted Offer, because
//! accepting is not the end: a background check, a rescinded req or a hiring
//! freeze lands between "I accepted" and "I started". That split is its own
//! rung, after one status was tried and could not carry both.

use std::collections::HashSet;

/// What an unset status reads as. Not a stored value - a job with no status
/// has no `job_status` row at all.
pub const NOT_SET: &str = "not set";

/// One status: how it is stored, how it reads, and every rule about it that
/// some other file used to hold its own copy of.
pub struct Status {
    /// The value in `job_status.status`. Lower-case with underscores, which is
    /// what the engine's export and every SQL filter matches on.
    pub value: &'static str,
    pub label: &'static str,
    /// How far this status PROVES a job got, for the funnel. `None` means it
    /// proves nothing about the ladder: a job can be passed over, or a posting
    /// can vanish, without anybody ever applying.
    pub rung: Option<usize>,
    /// The employer came back, either way.
    pub responded: bool,
    /// The person has closed the loop on this job, so triage hides it unless
    /// asked. Hidden, never deleted.
    pub settled: bool,
    /// A status that may only be recorded once `requires` appears in this
    /// job's HISTORY. See `blocked_reason`.
    pub requires: Option<&'static str>,
    pub colour: [u8; 3],
    /// Said in words, on hover. Two of these statuses are one letter apart in
    /// meaning and a reader should not have to infer the difference.
    pub hint: &'static str,
}

/// The one status the app DOES something about rather than only recording:
/// applying is what makes a copy of the posting worth keeping, because a
/// posting can be taken down the day after and then only the copy remains.
///
/// SPELLED OUT RATHER THAN REFERENCED IN `FLOW` BELOW, because the engine's
/// test suite reads this file as TEXT to check that both halves of the app
/// list the same statuses in the same order - it expects a string literal
/// there, and a constant reference made it see a vocabulary of eight. The
/// two spellings are held together by a test in this module instead.
pub const APPLIED: &str = "applied";

/// Every settable status, in the order the flow moves.
///
/// ORDER IS LOAD-BEARING TWICE OVER. It is the dropdown order, so the list
/// reads as a sequence rather than as an alphabetised pile - and "No Offer"
/// and "Offer Withdrawn" are deliberately NOT adjacent. They are the two
/// entries most easily mis-clicked for each other, and the cost of the mis-click
/// is a false record of how a search ended.
///
/// The three "ended without an offer" entries sit together at the end, before
/// Pass: they are what a person reaches for most often, and grouping them makes
/// the choice between them the visible decision rather than a hunt.
pub const FLOW: [Status; 11] = [
    Status {
        value: "applied",
        label: "Applied",
        rung: Some(0),
        responded: false,
        settled: false,
        requires: None,
        colour: [59, 130, 246],
        hint: "You sent an application and are waiting to hear back.",
    },
    Status {
        value: "interviewed",
        label: "Interviewed",
        rung: Some(1),
        responded: true,
        settled: false,
        requires: None,
        colour: [217, 119, 6],
        hint: "You spoke to them. Any round counts.",
    },
    Status {
        value: "offer",
        label: "Offer",
        rung: Some(2),
        responded: true,
        settled: false,
        requires: None,
        colour: [139, 92, 246],
        hint: "They offered you the job. You have not answered yet.",
    },
    Status {
        value: "accepted_offer",
        label: "Accepted Offer",
        rung: Some(3),
        responded: true,
        settled: false,
        requires: None,
        colour: [20, 184, 166],
        hint: "You accepted. Not the same as started - a background check, \
               a pulled requisition or a freeze still sits between the two.",
    },
    Status {
        value: "hired",
        label: "Hired",
        rung: Some(4),
        responded: true,
        settled: true,
        requires: None,
        colour: [22, 163, 74],
        hint: "You started, or have a confirmed start date. The end of the flow.",
    },
    Status {
        value: "offer_withdrawn",
        label: "Offer Withdrawn",
        rung: Some(3),
        responded: true,
        settled: true,
        // THE COMPANY pulled an offer you had already accepted. Recording it
        // needs the acceptance to exist first, or the word "withdrawn" is
        // describing something that never happened.
        requires: Some("accepted_offer"),
        colour: [190, 24, 93],
        hint: "You accepted and THEY pulled it - a failed check, a freeze, a \
               closed requisition.",
    },
    Status {
        value: "declined_offer",
        label: "Declined Offer",
        rung: Some(2),
        responded: true,
        settled: true,
        requires: Some("offer"),
        colour: [100, 116, 139],
        hint: "They offered and YOU said no.",
    },
    Status {
        value: "no_offer",
        label: "No Offer",
        // STAYS AT RUNG 0, and this is not an oversight. The funnel takes the
        // MAX rung across a job's LOG, so raising this to 1 would count every
        // No Offer already recorded - including the ones set before an
        // interview was required - as an interview that never happened.
        // Verified in dashboard::reached_from_log. Going forward the rung is
        // redundant anyway: `requires` puts `interviewed` in the history first,
        // so the job already reaches rung 1 on its own.
        rung: Some(0),
        responded: true,
        settled: true,
        // AFTER AN INTERVIEW, which is what makes it different from a
        // rejection email: No Offer is the interviewed pipeline's outcome, and
        // the applied pipeline's are Rejection Email and No Response. Enforced
        // the same way declined_offer and offer_withdrawn are - see
        // blocked_reason.
        requires: Some("interviewed"),
        colour: [220, 38, 38],
        hint: "You interviewed and they said no.",
    },
    Status {
        value: "rejection_email",
        label: "Rejection Email",
        rung: Some(0),
        // THEY ANSWERED. Verified in status::is_response, which reads this
        // field and nothing else: a rejection is a reply, and while it shared
        // a status with silence the response rate could not say so.
        responded: true,
        settled: true,
        requires: None,
        colour: [239, 68, 68],
        hint: "They wrote back to say no, without interviewing you.",
    },
    Status {
        value: "no_response",
        label: "No Response",
        rung: Some(0),
        // SILENCE IS NOT A REPLY. This is the one that was being counted as one
        // while it shared a status with No Offer.
        responded: false,
        settled: true,
        requires: None,
        colour: [120, 113, 108],
        hint: "You never heard back, and you are done waiting. The application \
               still counts as sent.",
    },
    Status {
        value: "pass",
        label: "Pass",
        rung: None,
        responded: false,
        settled: true,
        requires: None,
        colour: [148, 163, 184],
        hint: "You decided not to apply. Nothing was sent, so this never \
               enters the funnel.",
    },
];

/// Values that are no longer offered but may still sit in a database.
///
/// NOT MIGRATED AWAY, except `denied`, which is the same fact under a kinder
/// name and is renamed on open. `closed` is different: it meant "the opening
/// went away", which the app now DERIVES from `jobs.delisted_at` and shows
/// beside the status rather than instead of it. Rewriting an old `closed` row
/// into some other status would be inventing a decision the person never made,
/// so those rows keep their value and keep reading as a word.
// "Expired" rather than "No longer open", decided 2026-08-22. It names what the
// posting IS instead of describing a state in a sentence, and it does not read
// as something the person chose - nothing here expires by decision.
const LEGACY: [(&str, &str); 1] = [("closed", "Expired")];

/// The status the app writes when a posting is taken down and the person had
/// never acted on it.
///
/// NOT IN `FLOW`, on purpose, which is what keeps the original decision
/// intact: it cannot be chosen from any dropdown, so it never competes with
/// the statuses a person sets. It is only ever written by
/// `db::mark_taken_down`, and only onto rows carrying no decision worth
/// keeping. `label()` renders it as "Expired" through LEGACY.
pub const CLOSED: &str = "closed";

/// The rename applied on open, in both halves of the app.
///
/// "Denied" was the app's word for it. "No Offer" replaced it - the same
/// event, said without the verdict on the person. Free to do: no row in the
/// live database carries the old value.
pub const RENAMES: [(&str, &str); 1] = [("denied", "no_offer")];

/// How many entries the status dropdown shows: every status, plus "not set".
pub const POPUP_ENTRIES: usize = FLOW.len() + 1;

/// Tall enough for the whole vocabulary, so the popup never scrolls.
///
/// egui's default cap is 200 px - seven entries - and the list has been longer
/// than that for a while, which quietly hid the statuses at the END of a search
/// behind a scrollbar. Computed from the live entry count and the real row
/// height so that adding a status widens the popup rather than hiding one.
pub fn popup_height(ui: &egui::Ui) -> f32 {
    popup_height_for(
        POPUP_ENTRIES,
        ui.text_style_height(&egui::TextStyle::Button) + ui.spacing().button_padding.y * 2.0,
        ui.spacing().item_spacing.y,
        ui.spacing().menu_margin.sum().y,
    )
}

/// The arithmetic on its own, with no `Ui` to build - so the rule that matters
/// ("every entry fits") can be asserted in a unit test rather than only seen.
/// Same split as the engine's other UI-adjacent maths.
pub fn popup_height_for(entries: usize, row: f32, gap: f32, margin: f32) -> f32 {
    row * entries as f32 + gap * (entries as f32 - 1.0) + margin
}

/// egui's own default cap on a combo popup. Named here because the whole point
/// of `popup_height` is to be taller than it.
#[cfg(test)]
pub const EGUI_DEFAULT_POPUP_HEIGHT: f32 = 200.0;

pub fn get(value: &str) -> Option<&'static Status> {
    FLOW.iter().find(|s| s.value == value)
}

/// How a status reads. Falls back to capitalising whatever it was handed, so a
/// hand-edited database or an import from a future version still renders a
/// word rather than a raw token.
pub fn label(value: &str) -> String {
    if value.trim().is_empty() {
        return NOT_SET.to_string();
    }
    if let Some(status) = get(value) {
        return status.label.to_string();
    }
    if let Some((_, label)) = LEGACY.iter().find(|(v, _)| *v == value) {
        return (*label).to_string();
    }
    let mut chars = value.chars();
    match chars.next() {
        Some(first) => {
            first.to_uppercase().collect::<String>() + &chars.as_str().replace('_', " ")
        }
        None => String::new(),
    }
}

/// The colour a status carries everywhere it appears - pill, legend, donut.
///
/// Mid-tone on purpose: these have to read against a near-white panel AND a
/// near-black one, so the app does not have to keep two palettes in step.
pub fn colour(value: &str) -> [u8; 3] {
    match get(value) {
        Some(status) => status.colour,
        // Legacy and unknown values share the neutral grey. An unrecognised
        // status is not an error worth colouring loudly; it is just a row this
        // version does not have an opinion about.
        None if value == "closed" => [220, 38, 38],
        None => [120, 130, 150],
    }
}

pub fn rung(value: &str) -> Option<usize> {
    get(value).and_then(|s| s.rung)
}

pub fn is_response(value: &str) -> bool {
    get(value).is_some_and(|s| s.responded)
}

/// The statuses matching `keep`, written out as a person would say them:
/// "Interviewed, Offer, Hired or No Offer".
///
/// FOR PROSE THAT LISTS STATUSES. The Pipeline screen hand-wrote two such
/// lists and both went stale in the same way the pill colours had: one
/// promised "interviewed, offer, hired or denied" - naming a status this app
/// stopped writing, and omitting the four it had gained since. A sentence
/// about the vocabulary has to be built FROM the vocabulary, or it is a copy
/// with nothing holding it in step.
pub fn spoken_list(keep: impl Fn(&Status) -> bool) -> String {
    let labels: Vec<&str> = FLOW.iter().filter(|s| keep(s)).map(|s| s.label).collect();
    match labels.split_last() {
        None => String::new(),
        Some((last, [])) => (*last).to_string(),
        Some((last, rest)) => format!("{} or {last}", rest.join(", ")),
    }
}

/// Statuses triage hides unless the person asks to see them. Legacy `closed`
/// is included: a row somebody marked "no longer open" under the old
/// vocabulary is still one they closed the loop on.
pub fn settled_values() -> Vec<&'static str> {
    FLOW.iter()
        .filter(|s| s.settled)
        .map(|s| s.value)
        .chain(std::iter::once("closed"))
        .collect()
}

/// Where a status sits when the list is sorted by it: undecided, then live,
/// then finished.
///
/// NOT ALPHABETICAL, and that is the whole point. Sorting the Status column by
/// its own words puts "Applied" above "not set" and buries the rows nobody has
/// looked at yet under the ones already dealt with - which is the opposite of
/// what a queue is for.
///
/// DERIVED FROM `FLOW`, never written out again here. The two facts it reads -
/// `settled` and whether the status has a rung - already decide who is in
/// flight and who is finished, so by construction a status added to FLOW gets
/// a rank without this function being touched. A hand-written list would be a
/// third place to forget one.
///
/// The empty string is "not set" and ranks first, which is the ordering the
/// list is sorted by.
pub fn sort_rank(value: &str) -> u8 {
    let value = value.trim();
    if value.is_empty() {
        return 0;
    }
    match get(value) {
        Some(status) if status.settled => 2,
        Some(_) => 1,
        // A status this build does not know - a legacy `closed`, or one
        // written by a newer version - is finished rather than pending. The
        // safe direction: a row that has been dealt with must not climb back
        // to the top of the queue.
        None => 2,
    }
}

/// Statuses that mean an application is still live. The counterpart of
/// `settled_values`, and NOT its complement: "not set" is neither.
pub fn in_flight_values() -> Vec<&'static str> {
    FLOW.iter()
        .filter(|s| !s.settled && s.rung.is_some())
        .map(|s| s.value)
        .collect()
}

/// `'a', 'b', 'c'` - a list ready to drop into a SQL `IN (...)`.
///
/// Built rather than typed, because three separate hand-written IN lists in
/// dashboard.rs and db.rs were the reason a new status could be added and
/// silently counted as an open position. Values are compile-time constants
/// from FLOW above, never user input, so quoting them here cannot carry
/// anything into the statement that was not already in this file.
pub fn sql_list(values: &[&str]) -> String {
    values
        .iter()
        .map(|v| format!("'{v}'"))
        .collect::<Vec<_>>()
        .join(", ")
}

/// Why a status is not on offer for this job, or `None` if it is.
///
/// Stated, never silently absent. A dropdown that quietly lacks the
/// entry somebody is looking for reads as a bug in the app; a greyed entry
/// that says why reads as the app knowing something.
///
/// HISTORY, NOT CURRENT STATUS. The case that decides this: a job marked
/// Accepted Offer, then Hired, where the hire then falls through. The current
/// status is Hired and carries no trace of the acceptance - but the history
/// does, and Offer Withdrawn has to stay available for exactly that person.
pub fn blocked_reason(value: &str, history: &HashSet<String>) -> Option<String> {
    let required = get(value)?.requires?;
    if history.contains(required) {
        return None;
    }
    Some(format!(
        "Available once this job has been marked {} - it records what happened \
         AFTER that, so on its own it would describe something that never \
         happened.",
        label(required)
    ))
}

/// The statuses this job can move to, each with its blocking reason if any.
pub fn choices_for(history: &HashSet<String>) -> Vec<(&'static Status, Option<String>)> {
    FLOW.iter()
        .map(|s| (s, blocked_reason(s.value, history)))
        .collect()
}

/// Whether a status carries structured fields of its own.
///
/// Only Offer does, and only pay and the offer date: they are the two facts a
/// person needs months later and the two least likely to be recoverable from
/// memory. Everything else about a transition is freeform, because inventing
/// fields for it would be guessing at what somebody wants to write down.
pub fn has_offer_fields(value: &str) -> bool {
    value == "offer"
}

#[cfg(test)]
mod sort_rank_tests {
    use super::{sort_rank, FLOW};

    /// UNDECIDED FIRST. The queue exists to surface rows nobody has judged,
    /// and sorting the Status column alphabetically buries them under the ones
    /// already dealt with.
    #[test]
    fn not_set_ranks_before_everything_else() {
        assert_eq!(sort_rank(""), 0);
        assert_eq!(sort_rank("   "), 0);
        for status in FLOW {
            assert!(
                sort_rank(status.value) > 0,
                "{} ranked with not-set",
                status.value
            );
        }
    }

    /// IN FLIGHT SITS ABOVE FINISHED. An application still waiting on an
    /// answer is work; one that ended is history.
    #[test]
    fn live_applications_rank_above_settled_ones() {
        assert!(sort_rank("applied") < sort_rank("no_offer"));
        assert!(sort_rank("interviewed") < sort_rank("pass"));
        assert!(sort_rank("offer") < sort_rank("declined_offer"));
    }

    /// A status this build has never heard of - a legacy `closed`, or one
    /// written by a newer version - ranks as finished. The safe direction: a
    /// row already dealt with must not climb back to the top of the queue.
    #[test]
    fn an_unknown_status_is_treated_as_finished() {
        assert_eq!(sort_rank("closed"), 2);
        assert_eq!(sort_rank("something_from_a_later_build"), 2);
    }

    /// DERIVED FROM FLOW, not written again. Every settable status has to get
    /// a rank without this function naming it - otherwise adding one is a
    /// second place to forget.
    #[test]
    fn every_status_in_the_flow_has_a_rank() {
        for status in FLOW {
            let rank = sort_rank(status.value);
            assert!(rank == 1 || rank == 2, "{} ranked {rank}", status.value);
            assert_eq!(rank == 2, status.settled, "{} rank disagrees with settled",
                       status.value);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn history(items: &[&str]) -> HashSet<String> {
        items.iter().map(|s| (*s).to_string()).collect()
    }

    /// APPLIED is written out separately from the FLOW entry it names (see
    /// the note there for why), so the one thing that can go wrong is the two
    /// drifting apart - at which point applying to a job would silently stop
    /// keeping a copy of the posting, with nothing on screen to say so.
    #[test]
    fn the_applied_constant_names_a_status_that_exists() {
        let found = FLOW.iter().find(|s| s.value == APPLIED);
        assert!(found.is_some(), "APPLIED is {APPLIED:?}, which is not in FLOW");
        assert_eq!(found.unwrap().rung, Some(0), "and it is the first rung");
    }

    /// The dropdown scrolled for a second time: it had once shown every
    /// status without scrolling, and then regressed. egui caps a combo popup
    /// at 200 px, which fits seven entries - and the entries that fall off
    /// the bottom are the ones somebody reaches for at the END of a search.
    ///
    /// The oracle is the requirement, not the formula: every entry must fit,
    /// and the answer must beat the default that caused the bug. A future
    /// tenth, eleventh or twelfth status has to widen the popup rather than
    /// silently hide itself behind a scrollbar again.
    #[test]
    fn the_status_popup_is_tall_enough_for_every_entry() {
        // Typical egui body metrics; the rule must hold, not these numbers.
        let (row, gap, margin) = (20.0, 4.0, 12.0);

        let height = popup_height_for(POPUP_ENTRIES, row, gap, margin);
        assert!(
            height >= row * POPUP_ENTRIES as f32,
            "{POPUP_ENTRIES} entries of {row} px do not fit in {height} px"
        );
        assert!(
            height > EGUI_DEFAULT_POPUP_HEIGHT,
            "{height} px is under egui's own {EGUI_DEFAULT_POPUP_HEIGHT} px cap, so \
             setting it changes nothing and the popup still scrolls"
        );

        // ...and it has to keep holding as the vocabulary grows, which is the
        // regression that already happened twice.
        let grown = popup_height_for(POPUP_ENTRIES + 3, row, gap, margin);
        assert!(
            grown > height,
            "adding statuses did not make the popup taller, so they would hide"
        );
    }

    /// POPUP_ENTRIES is what the height is computed from, so if it drifts from
    /// what the dropdown actually renders the height is right for the wrong
    /// list. The dropdown draws every FLOW status plus "not set".
    #[test]
    fn the_popup_entry_count_matches_what_the_dropdown_draws() {
        assert_eq!(POPUP_ENTRIES, FLOW.len() + 1);
    }

    #[test]
    fn the_two_easiest_to_confuse_are_never_side_by_side() {
        // The rule, and the reason for it: "No Offer" and "Offer Withdrawn"
        // are one mis-click apart and record opposite stories about how far a
        // search got. A future reorder that puts them together has to fail
        // here rather than in front of a person marking the end of a job hunt.
        let no_offer = FLOW.iter().position(|s| s.value == "no_offer").unwrap();
        let withdrawn = FLOW
            .iter()
            .position(|s| s.value == "offer_withdrawn")
            .unwrap();
        assert!(
            no_offer.abs_diff(withdrawn) > 1,
            "No Offer and Offer Withdrawn are adjacent in the dropdown"
        );
    }

    #[test]
    fn offer_withdrawn_reads_the_history_not_the_current_status() {
        // THE case this rule exists for. Accepted, then Hired, then the hire
        // falls through. Current status is Hired, which is not the acceptance -
        // and a check against the current status alone would lock this person
        // out of recording what actually happened to them.
        let after_hire = history(&["applied", "offer", "accepted_offer", "hired"]);
        assert!(blocked_reason("offer_withdrawn", &after_hire).is_none());
    }

    #[test]
    fn a_dependent_status_is_blocked_with_a_reason_rather_than_missing() {
        let fresh = history(&["applied"]);
        let reason = blocked_reason("offer_withdrawn", &fresh)
            .expect("must be blocked without an acceptance");
        assert!(
            reason.contains("Accepted Offer"),
            "the reason must name what is missing, got: {reason}"
        );
        assert!(blocked_reason("declined_offer", &fresh).is_some());

        // NO OFFER NEEDS AN INTERVIEW. It is the interviewed pipeline's
        // outcome; the applied pipeline's are Rejection Email and No Response,
        // and offering all three unconditionally is what made them one status.
        let no_offer = blocked_reason("no_offer", &fresh)
            .expect("must be blocked without an interview");
        assert!(
            no_offer.contains("Interviewed"),
            "the reason must name what is missing, got: {no_offer}"
        );
        assert!(blocked_reason("no_offer", &history(&["interviewed"])).is_none());

        // The three an application reaches on its own carry no `requires`
        // by construction, so none of them can be blocked.
        assert!(blocked_reason("applied", &fresh).is_none());
        assert!(blocked_reason("rejection_email", &fresh).is_none());
        assert!(blocked_reason("no_response", &fresh).is_none());
    }

    #[test]
    fn every_choice_is_offered_and_the_blocked_ones_carry_their_reason() {
        // `interviewed` joined this history when No Offer began requiring it
        // on 2026-09-05. Without it the job holds an offer it was never
        // interviewed for, which is not the "everything unlocked" case this
        // is describing.
        let full = history(&["interviewed", "offer", "accepted_offer"]);
        let choices = choices_for(&full);
        assert_eq!(choices.len(), FLOW.len(), "no status may be dropped");
        assert!(choices.iter().all(|(_, reason)| reason.is_none()));
    }

    #[test]
    fn declining_an_offer_still_proves_the_offer_happened() {
        // Both of these end the search, and both are worth counting: a person
        // who turned down two offers had a very different month from one who
        // got none, and a funnel that scores them the same is lying.
        assert_eq!(rung("declined_offer"), Some(2));
        assert_eq!(rung("offer_withdrawn"), Some(3));
        assert!(is_response("declined_offer"));
        assert!(is_response("no_offer"));
    }

    #[test]
    fn passing_on_a_job_never_enters_the_funnel() {
        assert_eq!(rung("pass"), None);
        assert!(!is_response("pass"));
        assert_eq!(rung("closed"), None, "the retired value proves nothing either");
    }

    #[test]
    fn the_legacy_value_still_reads_as_a_word() {
        assert_eq!(label("closed"), "Expired");
        assert_eq!(label(""), NOT_SET);
        // A value from a hand-edited database or a newer export.
        assert_eq!(label("second_interview"), "Second interview");
    }

    #[test]
    fn settled_and_in_flight_do_not_overlap() {
        let settled = settled_values();
        for value in in_flight_values() {
            assert!(
                !settled.contains(&value),
                "{value} is both settled and in flight"
            );
        }
        // The whole point of the split: an open position is one that is in
        // flight or undecided, and hiding a status has to be a deliberate
        // property rather than an omission from a hand-written SQL list.
        assert!(settled.contains(&"no_offer"));
        assert!(settled.contains(&"closed"), "the legacy value stays hidden");
        assert!(in_flight_values().contains(&"accepted_offer"));
    }

    #[test]
    fn the_sql_list_quotes_what_the_queries_interpolate() {
        assert_eq!(sql_list(&["a", "b"]), "'a', 'b'");
        // Every value that reaches sql_list comes from FLOW, so this is the
        // shape check rather than an injection test - but the values must not
        // contain a quote of their own, or the statement they build breaks.
        assert!(FLOW.iter().all(|s| !s.value.contains('\'')));
    }

    #[test]
    fn stored_values_and_labels_are_both_unique() {
        // Two statuses sharing a stored value would make one unwritable; two
        // sharing a label would make them indistinguishable in the dropdown.
        let values: HashSet<&str> = FLOW.iter().map(|s| s.value).collect();
        let labels: HashSet<&str> = FLOW.iter().map(|s| s.label).collect();
        assert_eq!(values.len(), FLOW.len());
        assert_eq!(labels.len(), FLOW.len());
    }

    #[test]
    fn a_dependency_names_a_status_that_exists() {
        for status in FLOW.iter() {
            if let Some(required) = status.requires {
                assert!(
                    get(required).is_some(),
                    "{} requires {required}, which is not a status",
                    status.value
                );
            }
        }
    }
}

#[cfg(test)]
mod spoken_list_tests {
    use super::{spoken_list, FLOW};

    /// The Pipeline screen used to hand-write these sentences, and both went
    /// stale the same way the pill colours had: one read "Interviewed, offer,
    /// hired or denied" - naming a status this app does not write, and leaving
    /// out the four it gained after that line was typed. Every label in the
    /// sentence has to be a label the vocabulary actually holds.
    #[test]
    fn every_name_in_the_sentence_is_a_status_that_exists() {
        for sentence in [spoken_list(|s| s.responded), spoken_list(|s| s.settled)] {
            assert!(!sentence.is_empty());
            for name in sentence.split(" or ").flat_map(|p| p.split(", ")) {
                assert!(
                    FLOW.iter().any(|s| s.label == name),
                    "{name:?} is not a status this app has: {sentence}"
                );
            }
        }
    }

    /// And nothing the vocabulary holds is left out of the sentence about it.
    #[test]
    fn no_status_is_quietly_missing_from_the_sentence_about_it() {
        let responded = spoken_list(|s| s.responded);
        for status in FLOW.iter().filter(|s| s.responded) {
            assert!(
                responded.contains(status.label),
                "{} counts as a reply but the hover does not say so: {responded}",
                status.label
            );
        }
        let settled = spoken_list(|s| s.settled);
        for status in FLOW.iter().filter(|s| s.settled) {
            assert!(
                settled.contains(status.label),
                "{} takes a job out of the pipeline unmentioned: {settled}",
                status.label
            );
        }
    }

    #[test]
    fn one_name_needs_no_or_and_none_reads_as_nothing() {
        assert_eq!(spoken_list(|s| s.value == "hired"), "Hired");
        assert_eq!(spoken_list(|_| false), "");
        assert_eq!(
            spoken_list(|s| s.value == "hired" || s.value == "pass"),
            "Hired or Pass"
        );
    }
}
