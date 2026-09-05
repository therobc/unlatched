//! Add a job by link.
//!
//! Every other route into the list starts with a board the app can read.
//! This one starts with a person who has already found the job themselves,
//! is about to apply, and wants it tracked with everything else.
//!
//! The link is kept whatever the site. Whether the app goes and READS that
//! link is the engine's decision, not this form's - see the engine's
//! manual.py, which refuses to fetch LinkedIn and the aggregators and fills
//! in what it can from anywhere else. This form therefore says nothing about
//! what will be fetched; it just asks for what a person would have to type
//! if nothing could be.

use eframe::egui;

use crate::app::UnlatchedApp;

#[derive(Default, Clone, Debug)]
pub struct AddJobDraft {
    pub url: String,
    pub title: String,
    pub company: String,
    pub description: String,
    pub error: Option<String>,
}

pub fn show(app: &mut UnlatchedApp, ctx: &egui::Context) {
    if !app.show_add_job_modal {
        return;
    }
    let mut close = false;
    let mut submit = false;
    let mut enable_reading = false;

    egui::Window::new("Add a job by link")
        .collapsible(false)
        .resizable(false)
        .anchor(egui::Align2::CENTER_CENTER, [0.0, 0.0])
        .show(ctx, |ui| {
            ui.set_width(560.0);
            ui.label(
                "For a job you found somewhere this app does not collect from - a \
                 job site, an email, a friend. It joins your list and takes a \
                 status like any other job.",
            );
            ui.add_space(10.0);

            // Said HERE, at the moment it matters, rather than left for someone
            // to work out from an empty row afterwards (decided 2026-08-08). The
            // setting ships off, so this is what a new install shows, and the
            // switch is offered inline - being told where a setting lives while
            // you are mid-task is worse than being able to change it.
            if !app.config.fetch.read_added_links {
                egui::Frame::none()
                    .fill(ui.visuals().faint_bg_color)
                    .inner_margin(egui::Margin::same(10.0))
                    .rounding(egui::Rounding::same(4.0))
                    .show(ui, |ui| {
                        ui.label(
                            egui::RichText::new("You will need to type the details")
                                .strong(),
                        );
                        ui.add_space(4.0);
                        ui.label(
                            "The app is set not to open links you add, so it will \
                             not fill in the title, employer or description for \
                             you. Enter what you can below - the link and a \
                             title are enough to track it.",
                        );
                        ui.add_space(6.0);
                        if ui
                            .button("Let the app read the page instead")
                            .on_hover_text(
                                "Turns on \"Read the page when I add a job by \
                                 link\". It opens the link once, with you here, \
                                 and never during a collection. Changeable any \
                                 time on the Config tab.",
                            )
                            .clicked()
                        {
                            enable_reading = true;
                        }
                    });
                ui.add_space(10.0);
            }

            ui.label("Link to the posting");
            ui.add(
                egui::TextEdit::singleline(&mut app.add_job_draft.url)
                    .desired_width(f32::INFINITY)
                    .hint_text("https://..."),
            );
            ui.add_space(8.0);

            ui.horizontal(|ui| {
                ui.vertical(|ui| {
                    ui.label("Job title");
                    ui.add(
                        egui::TextEdit::singleline(&mut app.add_job_draft.title)
                            .desired_width(250.0),
                    );
                });
                ui.add_space(10.0);
                ui.vertical(|ui| {
                    ui.label("Employer");
                    ui.add(
                        egui::TextEdit::singleline(&mut app.add_job_draft.company)
                            .desired_width(250.0),
                    );
                });
            });
            ui.add_space(4.0);
            ui.weak(if app.config.fetch.read_added_links {
                "Left blank, these are read from the posting page. A page that \
                 will not open - a sign-in wall, a site that declines - leaves \
                 them blank, so type them if the job comes back bare."
            } else {
                "Type these: the app is not opening the link."
            });

            ui.add_space(10.0);
            ui.label("Posting text (optional)");
            ui.add(
                egui::TextEdit::multiline(&mut app.add_job_draft.description)
                    .desired_width(f32::INFINITY)
                    .desired_rows(6)
                    .hint_text("Paste the description here"),
            );
            // Said plainly, because otherwise the Fit column is simply empty
            // for this row and nobody can tell why.
            ui.weak(
                "Fit and the missing-words list are measured against this text. \
                 Without it the job is still tracked, just not scored.",
            );

            if let Some(err) = &app.add_job_draft.error {
                ui.add_space(6.0);
                ui.colored_label(egui::Color32::LIGHT_RED, err);
            }

            ui.add_space(12.0);
            ui.horizontal(|ui| {
                let ready = !app.add_job_draft.url.trim().is_empty();
                if ui
                    .add_enabled(ready, egui::Button::new("Add the job"))
                    .clicked()
                {
                    submit = true;
                }
                if crate::access::tag(ui.button("Cancel"), egui::WidgetType::Button, "add-job-cancel")
                    .clicked()
                {
                    close = true;
                }
            });
        });

    if enable_reading {
        // Written through the same save path the Config screen uses, so it
        // lands in config.json and the engine sees it on the very next add -
        // setting it only in memory would turn the button into a lie.
        app.config.fetch.read_added_links = true;
        app.config_draft.read_added_links = true;
        app.save_config_now();
    }
    if submit {
        match add(app) {
            Ok(()) => close = true,
            Err(e) => app.add_job_draft.error = Some(e),
        }
    }
    if close {
        app.show_add_job_modal = false;
    }
}

/// Hands the job to the engine, which is where the decision about what may
/// be fetched lives - and where screening lives, so a hand-added job is
/// scored by exactly the same code as a collected one.
fn add(app: &mut UnlatchedApp) -> Result<(), String> {
    let draft = app.add_job_draft.clone();
    let mut args = vec!["add".to_string(), draft.url.trim().to_string()];
    if !draft.title.trim().is_empty() {
        args.push("--title".to_string());
        args.push(draft.title.trim().to_string());
    }
    if !draft.company.trim().is_empty() {
        args.push("--company".to_string());
        args.push(draft.company.trim().to_string());
    }
    if !draft.description.trim().is_empty() {
        // Through a FILE, not an argument. A pasted job description is
        // routinely thousands of characters with quotes and newlines in it,
        // which is neither safe nor reliable to hand over on a command line.
        // Removed again when the add finishes - see app::report_add_job.
        let path = app.active_home.join(crate::app::STAGED_DESCRIPTION);
        std::fs::write(&path, draft.description.as_bytes())
            .map_err(|e| format!("could not stage the posting text: {e}"))?;
        args.push("--description-file".to_string());
        args.push(path.to_string_lossy().into_owned());
    }
    // QUEUED, NOT REFUSED. A scheduled collect holds the engine for up to
    // half an hour, and this is somebody recording a job they have just
    // applied for - the one thing that must not be dropped on the floor.
    app.queue_process(crate::app::ADD_JOB_LABEL, args);
    Ok(())
}
