//! "Collect": the menu that reads employer job boards.
//!
//! SHARED BY THE TWO SCREENS THAT OFFER IT, for the same reason
//! views::collectors_menu is - and that module's history is the argument. The
//! handoff submenu was added to the Dashboard as a partial version of the
//! Companies one, and the two screens then disagreed about what they offered:
//! the Dashboard could take in a handoff and could not read a board, which is
//! not a decision anybody made. Observed 2026-09-02, in the gap between the
//! two changes.
//!
//! WHY THE DASHBOARD WANTS IT AT ALL. The Dashboard is the screen that says
//! how stale the data is - "boards last collected 2 days ago" is written
//! there - and the action that answers it lived one page away. A screen that
//! reports a problem and cannot act on it sends somebody looking for the
//! button, which is exactly the trip the handoff menu was moved to save.
//!
//! FOUR WAYS, ONE COMMAND. Every entry is the engine's `collect` with a
//! filter it already understands, so this adds choices rather than a second
//! code path.
//!
//! NONE OF THESE IS THE ADDED-LINKS REFRESH. That is a different command,
//! against hosts this app may only read while somebody is watching, and it
//! stays on its own button on its own screen for exactly that reason.

use eframe::egui;

use crate::app::UnlatchedApp;
use crate::db;
use crate::views::collectors_menu::Pending;

/// The boards worth offering, from the employer list: named, trimmed, sorted,
/// each once.
///
/// A FUNCTION RATHER THAN A CLOSURE BODY, so the test exercises THIS and not a
/// copy of it. Written inline first, and the test that came with it restated
/// the same filter chain - which would have gone on passing had the menu's
/// version changed underneath it.
pub fn boards_offered(companies: &[crate::db::Company]) -> Vec<String> {
    let mut boards: Vec<&str> = companies
        .iter()
        .filter_map(|c| c.ats.as_deref())
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .collect();
    boards.sort_unstable();
    boards.dedup();
    boards.into_iter().map(str::to_string).collect()
}

/// How many employers to show before the list scrolls instead of running off
/// the bottom of the screen. The starter button alone can put fifty names into
/// somebody's list.
const EMPLOYER_LIST_HEIGHT: f32 = 320.0;

fn tag_button(response: egui::Response, name: &'static str) -> egui::Response {
    crate::access::tag(response, egui::WidgetType::Button, name)
}

/// Draw the Collect button and its entries. Returns what to run, if anything.
///
/// RETURNED RATHER THAN STARTED, like the handoff menu: the closure has
/// already borrowed the app to read its employer list, so starting a process
/// inside it would need a second, mutable borrow - enforced by the type, not
/// a convention. Callers hand the answer to `start_process` once the menu has
/// closed.
///
/// TAGGED, because these labels are copy and the harness addresses them: a
/// menu whose entries were reworded would otherwise take a passing test with
/// it.
pub fn menu(app: &UnlatchedApp, ui: &mut egui::Ui) -> Pending {
    let mut pending: Pending = None;

    let collect_menu = ui.menu_button("Collect", |ui| {
        if tag_button(ui.button("Every employer"), "collect-every")
            .on_hover_text("Reads every board in your employer list.")
            .clicked()
        {
            pending = Some(("collect".to_string(), vec!["collect".to_string()]));
        }
        if tag_button(ui.button("Seeded employers only"), "collect-seeded")
            .on_hover_text(
                "Just the national employers this app ships with, skipping the ones you added and the ones it discovered.",
            )
            .clicked()
        {
            pending = Some((
                "collect seeded".to_string(),
                vec![
                    "collect".to_string(),
                    "--origin".to_string(),
                    db::SEEDED.to_string(),
                ],
            ));
        }

        ui.separator();

        let one_employer = ui.menu_button("One employer", |ui| {
            if app.companies.is_empty() {
                ui.label("No employers yet.");
                return;
            }
            egui::ScrollArea::vertical()
                .max_height(EMPLOYER_LIST_HEIGHT)
                .show(ui, |ui| {
                    for company in &app.companies {
                        if ui.button(&company.name).clicked() {
                            pending = Some((
                                "collect".to_string(),
                                vec![
                                    "collect".to_string(),
                                    "--company".to_string(),
                                    company.name.clone(),
                                ],
                            ));
                        }
                    }
                });
        });

        let one_board = ui.menu_button("One board", |ui| {
            // Offered from what the employer list actually holds, not from
            // the list of collectors this app can drive: naming a board no
            // employer here uses would read as a promise and collect nothing.
            let boards = boards_offered(&app.companies);
            if boards.is_empty() {
                ui.label("No boards identified yet.");
                return;
            }
            for board in &boards {
                if ui.button(board).clicked() {
                    pending = Some((
                        "collect".to_string(),
                        vec![
                            "collect".to_string(),
                            "--source".to_string(),
                            board.clone(),
                        ],
                    ));
                }
            }
        });
        tag_button(one_employer.response, "collect-one-employer");
        tag_button(one_board.response, "collect-one-board");

        ui.separator();

        // Handoffs keep their own submenu wherever this appears - see
        // views::collectors_menu for why a file another program wrote is not
        // the same kind of action as reading a board.
        if let Some(chosen) = crate::views::collectors_menu::menu(app, ui, "From a collector") {
            pending = Some(chosen);
        }
    });
    tag_button(collect_menu.response, "collect-menu");

    pending
}

#[cfg(test)]
mod tests {
    use crate::db::Company;

    /// SPLIT OUT SO IT CAN BE TESTED, the same way collectors_menu::offer_from
    /// was: a rule reachable only by building a window, a database and a
    /// profile is a rule that goes unchecked.
    fn with_boards(boards: &[Option<&str>]) -> Vec<Company> {
        boards
            .iter()
            .enumerate()
            .map(|(n, ats)| Company {
                name: format!("Employer {n}"),
                ats: ats.map(str::to_string),
                ..Default::default()
            })
            .collect()
    }

    #[test]
    fn each_board_is_offered_once_however_many_employers_use_it() {
        let got = super::boards_offered(&with_boards(&[
            Some("greenhouse"),
            Some("lever"),
            Some("greenhouse"),
        ]));
        assert_eq!(got, vec!["greenhouse".to_string(), "lever".to_string()]);
    }

    /// An employer whose board was never identified carries an empty string,
    /// not a missing value - and it is the ordinary case, not an edge: counted
    /// on a working profile, 1260 of 1321 employers held an empty `ats` and
    /// none held NULL. Dropping only the missing ones would have offered 1260
    /// nameless entries, each collecting nothing.
    #[test]
    fn an_unidentified_board_is_not_offered() {
        assert!(super::boards_offered(&with_boards(&[None, Some("   "), Some("")])).is_empty());
    }
}
