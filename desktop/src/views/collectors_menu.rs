//! "From a collector": the menu that takes in what another program left.
//!
//! SHARED BY THE TWO SCREENS THAT OFFER IT, rather than written twice. It
//! began on Companies, beside the board-collecting actions, and the Dashboard
//! is where somebody actually notices a collector has delivered - the file's
//! age and whether it has been taken in are both on that screen already, so
//! being sent to another page to act on what it says was a step with no
//! purpose.
//!
//! Two copies of this menu would drift the way every other pair in this app
//! has: a collector added to one list and not the other, or two screens
//! disagreeing about whether a disabled collector is offered. One definition,
//! two call sites.
//!
//! HANDOFFS ARE NOT BOARDS, which is why this is its own control wherever it
//! appears. Everything a board action does reads a site this app is allowed to
//! read; this reads a FILE another program wrote, and Unlatched never touches
//! the site those rows came from. That separation is the whole reason the
//! arrangement exists.
//!
//! ASKING IGNORES THE SCHEDULE. A schedule says when the app looks by itself;
//! somebody who opened this menu has already said when they want it.

use eframe::egui;

use crate::app::UnlatchedApp;

/// What the caller should run, as (label, engine arguments).
///
/// RETURNED RATHER THAN STARTED. The menu is drawn inside a closure that has
/// already borrowed the app to read its collector list, so starting a process
/// in there would need a second, mutable borrow. Every caller does the same
/// thing with the answer: hand it to `start_process` once the menu is closed.
pub type Pending = Option<(String, Vec<String>)>;

/// Draw the menu button and its entries. Returns what was chosen, if anything.
///
/// `label` is the button's own text, so a screen can say "From a collector"
/// where that reads naturally and something shorter where it does not - the
/// ENTRIES are what must not differ between screens, and those are here.
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
        if live.is_empty() && problems.is_empty() {
            ui.label("No collectors are set up.");
        }
        for entry in &live {
            // The name is built per collector, so this tags directly rather
            // than through a helper taking a 'static string. The harness
            // addresses these as handoff-<id>.
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
            // Shown, never dropped. A collector missing because of a typo
            // three lines into a config file otherwise looks exactly like one
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

/// Is there a collector to offer at all?
///
/// So a screen can leave the control out entirely rather than showing a menu
/// whose only content is "No collectors are set up." The Companies page keeps
/// it regardless - it sits inside a Collect menu that is always there, and its
/// absence would read as a feature having gone missing - but the Dashboard
/// header has no such context, and a permanently empty button beside Refresh
/// is a control that never does anything.
///
/// A PROBLEM COUNTS AS SOMETHING TO OFFER. A collector refused for a typo is
/// exactly the case somebody needs to see, and hiding the menu would hide the
/// only place this app says so.
pub fn has_anything_to_offer(app: &UnlatchedApp) -> bool {
    offer_from(&app.handoffs)
}

/// The decision itself, over the listing rather than the whole app.
///
/// SPLIT OUT SO IT CAN BE TESTED. Taking `&UnlatchedApp` means the only way to
/// exercise this is to build an app - a window, a database, a profile - which
/// is why a rule this small would otherwise have gone unchecked, and its two
/// empty cases are exactly the kind that get confused.
pub fn offer_from(listing: &crate::collectors::Collectors) -> bool {
    match listing.ready() {
        // Not answered yet. Offering nothing is the safe direction for the
        // second the engine takes to reply: the button appears when the answer
        // arrives, rather than flickering from empty to full.
        None => false,
        Some((entries, problems)) => {
            entries.iter().any(|c| c.enabled) || !problems.is_empty()
        }
    }
}

#[cfg(test)]
mod tests {
    use crate::collectors::{Collectors, Handoff};

    /// `has_anything_to_offer` decides whether the Dashboard draws the control
    /// at all, so the cases it has to get right are the two empty ones - and
    /// they are not the same emptiness.
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
        assert!(super::offer_from(&listing(vec![entry("partner", true)], vec![])));
    }

    /// A collector somebody turned OFF is not one to offer a pull for -
    /// turning it off is a decision, and a menu that still pulled it would be
    /// the app arguing with that.
    #[test]
    fn a_disabled_collector_is_not() {
        assert!(!super::offer_from(&listing(vec![entry("partner", false)], vec![])));
    }

    /// A PROBLEM IS SOMETHING TO OFFER. A collector refused over a typo three
    /// lines into a config file is exactly the case somebody needs to see, and
    /// hiding the menu would hide the only place this app says so.
    #[test]
    fn a_refused_entry_still_earns_the_menu() {
        assert!(super::offer_from(&listing(vec![], vec!["collector 0: needs a path".into()])));
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
