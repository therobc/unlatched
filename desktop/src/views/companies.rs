// Companies: probe status table, a way to add a company and run discovery
// against it, and a streamed log of whatever the command-line tool prints
// while it works. The desktop app never talks to a network itself here;
// it only launches the CLI process and reads what it writes to stdout.

use eframe::egui;
use egui_extras::{Column, TableBuilder};

use crate::app::UnlatchedApp;
use crate::db;
use crate::engine::EngineMode;
use crate::fmt;

/// A menu control that automation can address by a name that does not move
/// when the wording does. Returns the response so the call site keeps chaining
/// hover text and `.clicked()`.
fn tag_button(response: egui::Response, name: &'static str) -> egui::Response {
    crate::access::tag(response, egui::WidgetType::Button, name)
}

pub fn show(app: &mut UnlatchedApp, ui: &mut egui::Ui) {
    ui.heading("Companies");

    // The CLI invocation setting only matters in dev mode: a bundled
    // engine is a standalone executable and never touches this field.
    if matches!(app.engine_mode, EngineMode::Python) {
        ui.horizontal(|ui| {
            ui.label("CLI invocation:");
            if ui
                .text_edit_singleline(&mut app.settings.python_invocation)
                .lost_focus()
            {
                app.save_settings();
            }
            ui.label("(e.g. python, python3, or a full interpreter path)");
        });
        ui.separator();
    }

    // Set inside the menu closure and acted on after it: the menu reads the
    // company list while it draws, and starting a process there would mean
    // borrowing the app mutably in the middle of that read.
    let mut pending: Option<(String, Vec<String>)> = None;

    ui.horizontal(|ui| {
        ui.label("Company name:");
        ui.text_edit_singleline(&mut app.new_company_name);
        let name = app.new_company_name.trim().to_string();
        let has_name = !name.is_empty();

        if ui.add_enabled(has_name, egui::Button::new("Add")).clicked() {
            if let Err(e) = db::add_company_stub(&app.conn, &name) {
                app.log_lines
                    .push(format!("[error] could not add company: {e}"));
            }
            app.refresh_companies();
        }
        if ui
            .add_enabled(has_name, egui::Button::new("Discover"))
            .clicked()
        {
            app.start_process(
                "discover",
                vec!["discover".to_string(), "--name".to_string(), name.clone()],
            );
        }
        // COLLECTING BY HAND, FOUR WAYS. Every one is the same `collect`
        // command with a filter the engine already understands, so the menu
        // adds choices rather than a second code path.
        //
        // NONE OF THESE IS THE ADDED-LINKS REFRESH. That is a different
        // command against hosts this app may only read while somebody is
        // watching, and it stays on its own button for exactly that reason.
        //
        // TAGGED, because these labels are copy and the harness addresses
        // them: a menu whose entries were reworded would otherwise take a
        // passing test with it.
        let collect_menu = ui.menu_button("Collect", |ui| {
            if tag_button(ui.button("Every employer"), "collect-every")
                .on_hover_text("Reads every board in the table below.")
                .clicked()
            {
                pending = Some(("collect".to_string(), vec!["collect".to_string()]));
            }
            if tag_button(ui.button("Seeded employers only"), "collect-seeded")
                .on_hover_text(
                    "Just the national employers this app ships with, skipping the \
                     ones you added and the ones it discovered.",
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
                // As long as the person's own list of employers, which the
                // starter button alone can put fifty names into (counted in
                // starter.py), so it scrolls rather than running off the
                // bottom of the screen.
                egui::ScrollArea::vertical()
                    .max_height(320.0)
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
                // Offered from what the table actually holds, not from the
                // list of collectors this app can drive: naming a board no
                // employer here uses would read as a promise and collect
                // nothing.
                let mut boards: Vec<&str> = app
                    .companies
                    .iter()
                    .filter_map(|c| c.ats.as_deref())
                    .map(str::trim)
                    .filter(|s| !s.is_empty())
                    .collect();
                boards.sort_unstable();
                boards.dedup();
                if boards.is_empty() {
                    ui.label("No boards identified yet.");
                    return;
                }
                for board in boards {
                    if ui.button(board).clicked() {
                        pending = Some((
                            "collect".to_string(),
                            vec![
                                "collect".to_string(),
                                "--source".to_string(),
                                board.to_string(),
                            ],
                        ));
                    }
                }
            });
            tag_button(one_employer.response, "collect-one-employer");
            tag_button(one_board.response, "collect-one-board");

            ui.separator();

            // HANDOFFS ARE NOT BOARDS, and the menu says so by keeping them
            // apart. Everything above reads a board this app is allowed to
            // read. These read a FILE another program wrote - Unlatched never
            // touches the site those rows came from, which is the whole reason
            // the arrangement exists.
            //
            // ASKING IGNORES THE SCHEDULE. A schedule says when the app looks
            // by itself; somebody who opened this menu has already said when
            // they want it.
            let handoffs = ui.menu_button("From a collector", |ui| {
                let Some((entries, problems)) = app.handoffs.ready() else {
                    ui.label("checking...");
                    return;
                };
                if let Some(why) = app.handoffs.failure() {
                    // The engine could not be asked. Said out loud rather than
                    // shown as an empty list: "you have no collectors" and "I
                    // could not find out" are different answers.
                    ui.label(format!("could not read the list: {why}"));
                    return;
                }
                let live: Vec<_> = entries.iter().filter(|c| c.enabled).collect();
                if live.is_empty() && problems.is_empty() {
                    ui.label("No collectors are set up.");
                }
                for entry in &live {
                    // access::tag directly rather than the file's tag_button:
                    // the name is built per collector, and tag_button takes a
                    // 'static one because every other control here has a fixed
                    // name. The harness addresses these as handoff-<id>.
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
                    if tag_button(ui.button("All of them"), "handoff-all")
                        .on_hover_text("Takes in whatever each of them has left, now.")
                        .clicked()
                    {
                        pending =
                            Some(("pull collectors".to_string(), vec!["ingest".to_string()]));
                    }
                }
                for problem in &problems {
                    // Shown, never dropped. A collector missing because of a
                    // typo three lines into a config file otherwise looks
                    // exactly like one nobody ever added.
                    ui.colored_label(egui::Color32::LIGHT_RED, problem);
                }
            });
            tag_button(handoffs.response, "collect-handoffs");
        });
        tag_button(collect_menu.response, "collect-menu");
        // Twelve of the thirteen collectors are per-employer and sit idle
        // until this list has something in it, and nobody can invent forty
        // employer names cold. Offered, never forced: it writes into the
        // person's own list, so it is a button rather than something that
        // happens to them on first run.
        if ui
            .button("Add starter employers")
            .on_hover_text(
                "National employers with boards this app can read, measured rather \
                 than assumed. Yours are kept as they are; this only adds names you \
                 do not already have, and you can delete any of them.",
            )
            .clicked()
        {
            app.start_process(
                "add starter employers",
                vec!["starter".to_string(), "--add".to_string()],
            );
        }
        if ui.button("Refresh table").clicked() {
            app.refresh_companies();
        }
    });

    if let Some((label, args)) = pending {
        app.start_process(&label, args);
    }

    if app.running_process.is_some() {
        ui.colored_label(egui::Color32::LIGHT_YELLOW, "a command is running...");
    }

    ui.separator();
    // GIVEN A HEIGHT THAT FITS ITS ROWS. A TableBuilder draws its column
    // dividers down the whole height its parent offers, which here is the rest
    // of the window: two employers produced separator lines running past the
    // output log to the bottom of the screen (seen in QC shot 03_companies,
    // 2026-08-12).
    let table_height = (28.0 + 26.0 * app.companies.len() as f32).clamp(60.0, 340.0);
    ui.allocate_ui(egui::vec2(ui.available_width(), table_height), |ui| {
        render_table(app, ui);
    });

    ui.separator();
    ui.label("Output:");
    egui::ScrollArea::vertical()
        .id_source("companies_log")
        .max_height(220.0)
        .stick_to_bottom(true)
        .auto_shrink([false, false])
        .show(ui, |ui| {
            for line in &app.log_lines {
                ui.monospace(line);
            }
        });

    ui.separator();
    ui.horizontal(|ui| {
        ui.label("Engine:");
        ui.monospace(app.engine_mode.label());
    });
}

fn render_table(app: &mut UnlatchedApp, ui: &mut egui::Ui) {
    let mut collect_target: Option<String> = None;

    TableBuilder::new(ui)
        .striped(true)
        .resizable(true)
        .column(Column::initial(200.0).at_least(120.0).clip(true)) // name
        .column(Column::initial(160.0).at_least(100.0).clip(true)) // domain
        .column(Column::initial(90.0).at_least(70.0)) // ats
        .column(Column::initial(95.0).at_least(70.0)) // origin
        .column(Column::initial(90.0).at_least(70.0)) // probe status
        .column(Column::initial(120.0).at_least(90.0)) // last probed
        .column(Column::remainder().at_least(80.0)) // action
        .min_scrolled_height(160.0)
        .max_scroll_height(320.0)
        .header(22.0, |mut header| {
            header.col(|ui| {
                ui.strong("Name");
            });
            header.col(|ui| {
                ui.strong("Domain");
            });
            header.col(|ui| {
                ui.strong("ATS");
            });
            header.col(|ui| {
                ui.strong("Added by")
                    .on_hover_text("How this employer got onto your list.");
            });
            header.col(|ui| {
                ui.strong("Probe status");
            });
            header.col(|ui| {
                ui.strong("Last probed");
            });
            header.col(|ui| {
                ui.strong("");
            });
        })
        .body(|body| {
            body.rows(24.0, app.companies.len(), |mut row| {
                let idx = row.index();
                let c = &app.companies[idx];
                row.col(|ui| {
                    let name = fmt::truncate(&c.name, 30);
                    // careers_url is discovered by following links on remote
                    // pages, so it gets the same scheme guard as a job URL -
                    // only http(s) is handed to the OS. See fmt::safe_link.
                    let response = match c.careers_url.as_deref().and_then(fmt::safe_link) {
                        Some(url) => ui.hyperlink_to(name, url),
                        None => ui.label(name),
                    };
                    if let Some(ats_ref) = &c.ats_ref {
                        if !ats_ref.trim().is_empty() {
                            response.on_hover_text(format!("ats ref: {ats_ref}"));
                        }
                    }
                });
                row.col(|ui| {
                    ui.label(fmt::truncate(c.domain.as_deref().unwrap_or(""), 26));
                });
                row.col(|ui| {
                    ui.label(c.ats.clone().unwrap_or_default());
                });
                row.col(|ui| {
                    let (text, why) = origin_label(c.origin.as_deref());
                    // Named per employer, with the word in the value slot, so
                    // a test can ask what this cell says rather than hunting
                    // for a bare word in the tree - and so a screen reader
                    // reads a cell that otherwise has no context.
                    let cell = crate::access::tag_with_value(
                        ui.label(text),
                        egui::WidgetType::Label,
                        format!("origin-{}", crate::access::slug(&c.name)),
                        text,
                    );
                    cell.on_hover_text(why);
                });
                row.col(|ui| {
                    ui.label(c.probe_status.clone());
                });
                row.col(|ui| {
                    ui.label(c.last_probed.clone().unwrap_or_default());
                });
                row.col(|ui| {
                    if ui.small_button("Collect").clicked() {
                        collect_target = Some(c.name.clone());
                    }
                });
            });
        });

    if let Some(name) = collect_target {
        app.start_process(
            "collect",
            vec!["collect".to_string(), "--company".to_string(), name],
        );
    }
}

/// The provenance cell: the stored word, and a sentence saying what it means.
///
/// THE WORD IS SHOWN AS STORED rather than prettied up, because it is the same
/// word the Collect menu uses out loud ("seeded employers only") and the same
/// one `collect --origin` takes. A display name here would quietly make three
/// vocabularies out of one.
fn origin_label(origin: Option<&str>) -> (&str, &'static str) {
    match origin.map(str::trim).unwrap_or("") {
        "seeded" => (
            "seeded",
            "Shipped with the app as a starter employer, and added because you \
             asked for them.",
        ),
        "discovered" => (
            "discovered",
            "The app found this employer's board itself, while collecting.",
        ),
        "manual" => ("manual", "You added this one by hand."),
        "imported" => (
            "imported",
            "Carried in from a file you imported. This app holds no board of its \
             own for it, so collecting will not reach it.",
        ),
        "" => (
            "unknown",
            "Added before the app started recording where employers came from.",
        ),
        other => (
            other,
            "An origin recorded by a different version of the app than this one.",
        ),
    }
}

#[cfg(test)]
mod tests {
    use super::origin_label;
    use crate::db;

    /// The menu passes `db::SEEDED` to `collect --origin`, whose argparse
    /// `choices` list rejects any other spelling. If that constant ever drifts
    /// from the word this table knows, the column would read "unknown" for
    /// exactly the rows the menu claims to collect - so the two are checked
    /// against each other rather than each against itself.
    #[test]
    fn the_word_the_menu_sends_is_the_word_the_column_explains() {
        let (shown, why) = origin_label(Some(db::SEEDED));
        assert_eq!(shown, db::SEEDED);
        assert!(
            why.contains("Shipped with the app"),
            "seeded fell through to the unrecognised arm: {why}"
        );
    }

    #[test]
    fn every_origin_the_engine_writes_has_its_own_explanation() {
        // The four values unlatched.db can write, plus the two shapes a row
        // can have when nothing wrote one.
        let mut seen: Vec<&str> = Vec::new();
        for origin in ["seeded", "discovered", "manual", "imported"] {
            let (shown, why) = origin_label(Some(origin));
            assert_eq!(shown, origin);
            assert!(!seen.contains(&why), "{origin} reuses another explanation");
            seen.push(why);
        }
        assert_eq!(origin_label(None).0, "unknown");
        assert_eq!(origin_label(Some("   ")).0, "unknown");
    }

    /// A value this build does not know is shown as it is, not swallowed into
    /// "unknown": a row written by a newer version is a different situation
    /// from a row written before the column existed, and reading the two the
    /// same way would hide an upgrade problem.
    #[test]
    fn an_unrecognised_origin_is_shown_rather_than_hidden() {
        let (shown, why) = origin_label(Some("partner-feed"));
        assert_eq!(shown, "partner-feed");
        assert!(why.contains("different version"));
    }
}
