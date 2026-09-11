//! "From a collector": the menu that takes in what another program left.
//!
//! SHARED BY THE TWO SCREENS THAT OFFER IT, rather than written twice -
//! verified 2026-09-10: companies.rs and dashboard_view.rs are the only two
//! call sites of collect_menu::menu, which draws this menu in turn. It began
//! on Companies, beside the board-collecting actions; that the Dashboard
//! saves a trip because it already shows a collector's age and whether it
//! has been taken in is believed, not measured.
//!
//! Two copies of this menu drifting the way other pairs in this app have is
//! believed, not measured - this file avoids the risk with one definition
//! and two call sites rather than pointing at a documented incident.
//!
//! HANDOFFS ARE NOT BOARDS, which is why this is its own control wherever it
//! appears - verified 2026-09-10: cmd_ingest (cli.py) only reads a configured
//! local path, never fetches a URL, matching COLLECTORS.md's "we pull, you
//! never push" contract. That separation is the whole reason the arrangement
//! exists.
//!
//! ASKING IGNORES THE SCHEDULE - verified 2026-09-10: cmd_ingest's own
//! docstring in cli.py says "on demand ignores the schedule".

use eframe::egui;

use crate::app::UnlatchedApp;

/// What the caller should run, as (label, engine arguments).
///
/// RETURNED RATHER THAN STARTED - by construction: `menu` takes `app:
/// &UnlatchedApp`, an immutable borrow already captured by the closure
/// below, and `start_process` needs `&mut self` - a second, mutable borrow
/// the borrow checker refuses inside that closure. Every caller does the
/// same thing with the answer: hand it to `start_process` once the menu is
/// closed.
pub type Pending = Option<(String, Vec<String>)>;

/// Draw the menu button and its entries. Returns what was chosen, if anything.
///
/// `label` is the button's own text - by construction: it is passed
/// straight to `ui.menu_button(label, ...)` below, so a screen can say
/// "From a collector" where that reads naturally and something shorter
/// where it does not. The ENTRIES are what must not differ between
/// screens, and those are here.
pub fn menu(app: &UnlatchedApp, ui: &mut egui::Ui, label: &str) -> Pending {
    let mut pending: Pending = None;

    let response = ui.menu_button(label, |ui| {
        let Some((entries, problems)) = app.handoffs.ready() else {
            ui.label("checking...");
            return;
        };
        if let Some(why) = app.handoffs.failure() {
            // The engine could not be asked. Said out loud rather than shown
            // as an empty list: "you have no collectors" and "I could not
            // find out" are different answers.
            ui.label(format!("could not read the list: {why}"));
            return;
        }
        let live: Vec<_> = entries.iter().filter(|c| c.enabled).collect();
        // Through offer_from rather than repeating the condition here: the
        // same rule used to be written twice, once for whether the Dashboard
        // drew a button at all and once for what this menu says when empty.
        // The Dashboard's copy went when the full Collect menu replaced the
        // handoff-only one, and this is what keeps the surviving rule attached
        // to the tests that check its two empty cases.
        if !offer_from(&app.handoffs) {
            ui.label("No collectors are set up.");
        }
        for entry in &live {
            // The name is built per collector, so this tags directly with a
            // formatted String rather than through a helper expecting a fixed name -
            // by construction: format!("handoff-{}", entry.id) below builds a
            // different name per collector. Every access::tag call is addressable by
            // an automated test through Windows UIA (see access.rs), which is what
            // these names are for.
            if crate::access::tag(
                ui.button(&entry.name),
                egui::WidgetType::Button,
                format!("handoff-{}", entry.id),
            )
            .on_hover_text(entry.detail())
            .clicked()
            {
                pending = Some((
                    format!("pull {}", entry.name),
                    vec![
                        "ingest".to_string(),
                        "--collector".to_string(),
                        entry.id.clone(),
                    ],
                ));
            }
        }
        if live.len() > 1 {
            ui.separator();
            if crate::access::tag(
                ui.button("All of them"),
                egui::WidgetType::Button,
                "handoff-all",
            )
            .on_hover_text("Takes in whatever each of them has left, now.")
            .clicked()
            {
                pending = Some(("pull collectors".to_string(), vec!["ingest".to_string()]));
            }
        }
        for problem in &problems {
            // Shown, never dropped - by construction: the loop below renders every
            // entry in `problems` unfiltered. A collector missing because of a typo
            // three lines into a config file would otherwise look exactly like one
            // nobody ever added.
            ui.colored_label(egui::Color32::LIGHT_RED, problem);
        }
    });

    crate::access::tag(
        response.response,
        egui::WidgetType::Button,
        "collect-handoffs",
    );
    pending
}

/// Whether there is anything at all to put in this menu.
///
/// A PROBLEM COUNTS AS SOMETHING TO OFFER. A collector refused for a typo is
/// exactly the case somebody needs to see, and reading "No collectors are set
/// up" over it would hide the only place this app says so.
///
/// OVER THE LISTING, NOT THE WHOLE APP, so it can be tested: a rule reached
/// only through `&UnlatchedApp` needs a window, a database and a profile to
/// exercise, which is how something this small goes unchecked.
pub fn offer_from(listing: &crate::collectors::Collectors) -> bool {
    match listing.ready() {
        // Not answered yet. Offering nothing is the safe direction for the
        // second the engine takes to reply: the button appears when the answer
        // arrives, rather than flickering from empty to full.
        None => false,
        Some((entries, problems)) => entries.iter().any(|c| c.enabled) || !problems.is_empty(),
    }
}

#[cfg(test)]
mod tests {
    use crate::collectors::{Collectors, Handoff};

    /// `offer_from` decides whether the handoff submenu shows "No collectors
    /// are set up" in place of any entries - verified 2026-09-10: not whether
    /// the surrounding menu is drawn at all, since dashboard_view.rs now
    /// always draws the Collect button (see its own "NO EMPTINESS GATE HERE"
    /// note). The cases this function has to get right are the two empty
    /// ones - and they are not the same emptiness.
    ///
    /// Built from the same `Collectors` the app holds, so the answer comes
    /// from the type the screen actually reads rather than from a stand-in.
    fn listing(entries: Vec<Handoff>, problems: Vec<String>) -> Collectors {
        Collectors::from_answer(entries, problems)
    }

    fn entry(id: &str, enabled: bool) -> Handoff {
        Handoff {
            id: id.to_string(),
            name: id.to_string(),
            enabled,
            path: format!("C:/nowhere/{id}.json"),
            schedule: Vec::new(),
            age_hours: None,
            file_present: false,
        }
    }

    #[test]
    fn a_configured_collector_is_worth_offering() {
        assert!(super::offer_from(&listing(
            vec![entry("partner", true)],
            vec![]
        )));
    }

    /// A collector somebody turned OFF is not one to offer a pull for -
    /// turning it off is a decision, and a menu that still pulled it would be
    /// the app arguing with that.
    #[test]
    fn a_disabled_collector_is_not() {
        assert!(!super::offer_from(&listing(
            vec![entry("partner", false)],
            vec![]
        )));
    }

    /// A PROBLEM IS SOMETHING TO OFFER - by construction: offer_from returns
    /// true whenever `problems` is non-empty, whatever `entries` holds (see
    /// offer_from below). Problems are rendered unconditionally by the loop
    /// in `menu` regardless of this function's answer; what offer_from alone
    /// decides is whether the submenu also shows "No collectors are set up."
    #[test]
    fn a_refused_entry_still_earns_the_menu() {
        assert!(super::offer_from(&listing(
            vec![],
            vec!["collector 0: needs a path".into()]
        )));
    }

    #[test]
    fn a_profile_with_no_collectors_gets_no_control() {
        assert!(!super::offer_from(&listing(vec![], vec![])));
    }

    /// NOT ANSWERED YET IS NOT "NONE". The engine takes about a second to
    /// reply, and offering nothing for that second is the safe direction: the
    /// button appears once the answer arrives rather than flickering from
    /// empty to full in front of somebody.
    #[test]
    fn an_unanswered_listing_offers_nothing_yet() {
        assert!(!super::offer_from(&Collectors::default()));
    }
}
