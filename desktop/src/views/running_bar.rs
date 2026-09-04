//! The strip that says whether the engine is working, from any screen.
//!
//! WHY IT IS NOT A LINE INSIDE A VIEW. A run belongs to the WINDOW, not to
//! whatever is being looked at. The only place a run was previously visible
//! was the log on the Companies page, so a collect started from the dashboard
//! was, from anywhere else, indistinguishable from nothing happening at all.
//!
//! IT SHOWS WHAT THE ENGINE LAST SAID, not a spinner. A spinner proves the UI
//! thread is alive, which was never in doubt; the useful fact is which board
//! is being read right now, and the engine prints that per company.
//!
//! AND IT OUTLIVES THE RUN. An indicator that simply vanishes is not an
//! answer - it looks identical whether the run finished, was killed, or the
//! app lost track of it. The completion line stays until the next run starts.

use eframe::egui;

use crate::app::UnlatchedApp;

/// Green enough to read as "went fine" on either theme, and a red that is not
/// the alarm red used for destructive actions - a failed collect is worth
/// noticing, not worth panicking about.
const DONE: egui::Color32 = egui::Color32::from_rgb(22, 163, 74);
const FAILED: egui::Color32 = egui::Color32::from_rgb(220, 38, 38);
const WORKING: egui::Color32 = egui::Color32::from_rgb(59, 130, 246);

pub fn show(app: &mut UnlatchedApp, ctx: &egui::Context) {
    let running = app.running_detail().map(|(l, d)| (l.to_string(), d.to_string()));
    let finished = app.last_run_result.clone();
    if running.is_none() && finished.is_none() {
        return;
    }

    egui::TopBottomPanel::bottom("running-bar").show(ctx, |ui| {
        ui.add_space(2.0);
        ui.horizontal(|ui| {
            match &running {
                Some((label, detail)) => {
                    // A REPAINT IS REQUESTED WHILE A RUN IS LIVE. egui only
                    // draws on input by default, so without this the strip
                    // would freeze on whichever line happened to be current
                    // when the mouse last moved - which is worse than no
                    // indicator, because it reads as a stalled run.
                    ctx.request_repaint_after(std::time::Duration::from_millis(400));
                    ui.add(egui::Spinner::new().size(14.0));
                    ui.colored_label(WORKING, label);
                    let said = if detail.trim().is_empty() {
                        // A real state, not a gap: the process is up and the
                        // first board has not answered yet.
                        "starting..."
                    } else {
                        detail.as_str()
                    };
                    crate::access::tag_with_value(
                        ui.weak(crate::fmt::truncate(said, 90)),
                        egui::WidgetType::Label,
                        "engine-status",
                        said,
                    )
                    .on_hover_text(said);
                }
                None => {
                    let (line, went_well) = finished.clone().unwrap_or_default();
                    // FROM THE EXIT CODE, carried here - not from searching the
                    // sentence for the word "failed", which is the exit code
                    // read back out of English.
                    let colour = if went_well { DONE } else { FAILED };
                    crate::access::tag_with_value(
                        ui.colored_label(colour, &line),
                        egui::WidgetType::Label,
                        "engine-status",
                        &line,
                    );
                    ui.with_layout(
                        egui::Layout::right_to_left(egui::Align::Center),
                        |ui| {
                            if crate::access::tag(
                                ui.small_button("Dismiss"),
                                egui::WidgetType::Button,
                                "engine-status-dismiss",
                            )
                            .on_hover_text("Hide this until the next run.")
                            .clicked()
                            {
                                app.last_run_result = None;
                            }
                        },
                    );
                }
            }
        });
        ui.add_space(2.0);
    });
}
