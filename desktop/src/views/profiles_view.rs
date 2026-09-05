//! Profile management: create, re-point, and remove job seekers.
//!
//! These used to sit as two buttons directly under the profile dropdown, in
//! the sidebar, next to the control you use every time you switch profiles.
//! Remove fired on a single click with no confirmation - one misclick and a
//! seeker left the registry. Their data survived on disk, but getting them
//! back meant knowing the exact folder path, and a profile absent from the
//! registry is invisible in the app entirely. That is not a hypothetical:
//! two of five test seekers were found in precisely that state, complete on
//! disk and unreachable in the UI.
//!
//! So the destructive and creative operations live here, behind a deliberate
//! navigation step, and the sidebar keeps only the dropdown - which is the
//! frequent, harmless action.

use eframe::egui;

use std::path::PathBuf;

use crate::app::{NewProfileDraft, UnlatchedApp};
use crate::profiles;
use crate::settings;

pub fn show(app: &mut UnlatchedApp, ui: &mut egui::Ui) {
    ui.heading("Settings");
    ui.separator();
    appearance(app, ui);
    ui.add_space(12.0);
    opening_links(app, ui);
    ui.add_space(12.0);
    note_prompts(app, ui);
    ui.add_space(12.0);
    help(app, ui);
    ui.add_space(12.0);
    your_data(app, ui);
    ui.add_space(12.0);
    criteria(app, ui);
    show_criteria_preview(app, ui);
    ui.add_space(12.0);
    which_build(ui);
    ui.add_space(12.0);

    ui.heading("Profiles");
    ui.label(
        "A person can run several named searches, each with its own criteria and its own \
         pipeline. Switching between them uses the dropdowns in the sidebar.",
    );
    ui.separator();

    if app.profile_locked {
        ui.colored_label(
            egui::Color32::LIGHT_BLUE,
            "UNLATCHED_HOME is set for this launch, so the registry is read-only. \
             Nothing here can be changed until that variable is cleared.",
        );
        return;
    }

    if crate::access::tag(ui.button("New profile"), egui::WidgetType::Button, "profiles-new")
        .clicked()
    {
        app.show_new_profile_modal = true;
        app.new_profile_draft = NewProfileDraft::default();
    }

    ui.separator();

    // One row per SEARCH, grouped under the person who owns it. A person is
    // not a row of its own: there is nothing to do to a person here that is
    // not done to their searches, and a row with no folder and no action would
    // just be a heading pretending to be data.
    let pairs: Vec<(String, String)> = profiles::people(&app.profile_registry)
        .into_iter()
        .flat_map(|person| {
            profiles::searches_for(&app.profile_registry, &person)
                .into_iter()
                .map(move |search| (person.clone(), search))
        })
        .collect();

    egui::Grid::new("profiles_grid")
        .num_columns(4)
        .striped(true)
        .spacing([12.0, 6.0])
        .show(ui, |ui| {
            ui.strong("Person");
            ui.strong("Search");
            ui.strong("Folder");
            ui.strong("");
            ui.end_row();

            let mut last_person: Option<String> = None;
            for (person, search) in &pairs {
                let home = profiles::home_for(&app.profile_registry, person, search);
                let is_active =
                    *person == app.active_person && *search == app.active_search;

                // The name is written once per person, not repeated down every
                // one of their searches - repeating it makes four searches look
                // like four people.
                if last_person.as_deref() == Some(person.as_str()) {
                    ui.label("");
                } else if is_active {
                    ui.strong(person);
                } else {
                    ui.label(person);
                }
                last_person = Some(person.clone());

                if is_active {
                    ui.strong(search);
                } else {
                    ui.label(search);
                }
                ui.label(home.display().to_string());

                if ui
                    .button("Remove")
                    .on_hover_text(
                        "Asks for confirmation. Removes this search from the list only; \
                         the folder and everything in it are kept.",
                    )
                    .clicked()
                {
                    app.profile_pending_removal = Some((person.clone(), search.clone()));
                }
                ui.end_row();
            }
        });

    show_registry_problems(app, ui);
    show_removal_confirmation(app, ui);
}

/// Anything registered that cannot actually be opened, stated where it will be
/// seen.
///
/// Both failure modes here have already happened silently and cost real time:
/// two seekers had complete configs, resumes and employer lists on disk but were
/// absent from the registry, so they could not be selected at all; and one had
/// no database, which a refresh script stepped straight over with "no database,
/// skipping". Nothing said a word. A registry problem should be loud, and this
/// is the screen a person is on when they are wondering where somebody went.
fn show_registry_problems(app: &mut UnlatchedApp, ui: &mut egui::Ui) {
    let problems = profiles::preflight(&app.profile_registry);
    if problems.is_empty() {
        return;
    }
    ui.add_space(10.0);
    ui.colored_label(
        egui::Color32::from_rgb(217, 164, 65),
        format!(
            "{} registered search{} cannot be opened",
            problems.len(),
            if problems.len() == 1 { "" } else { "es" }
        ),
    );
    for problem in &problems {
        ui.weak(format!(
            "{} - {}",
            profiles::label(&problem.person, &problem.search),
            problem.detail
        ));
    }
    ui.weak(
        "Nothing has been deleted by the app. A folder that moved can be re-added as a \
         new search pointing at its current location.",
    );
}

/// A modal that names the search and states plainly what survives. A
/// confirmation that only says "are you sure?" teaches nothing; this one
/// answers the question the person actually has, which is whether their
/// collected jobs are about to be destroyed.
fn show_removal_confirmation(app: &mut UnlatchedApp, ui: &mut egui::Ui) {
    let Some((person, search)) = app.profile_pending_removal.clone() else {
        return;
    };
    let name = profiles::label(&person, &search);
    let home = profiles::home_for(&app.profile_registry, &person, &search);
    let mut close = false;
    let mut confirmed = false;

    egui::Window::new("Remove search")
        .collapsible(false)
        .resizable(false)
        .anchor(egui::Align2::CENTER_CENTER, [0.0, 0.0])
        .show(ui.ctx(), |ui| {
            ui.label(format!("Remove \"{name}\" from the profile list?"));
            ui.add_space(6.0);
            ui.label("Nothing on disk is deleted. This folder and every job in it are kept:");
            ui.monospace(home.display().to_string());
            ui.add_space(6.0);
            ui.weak(
                "To use this seeker again afterwards you would create a new profile \
                 pointing at that same folder, so keep the path if you need it.",
            );
            ui.add_space(10.0);
            ui.horizontal(|ui| {
                if crate::access::tag(ui.button("Cancel"), egui::WidgetType::Button,
                    "profiles-delete-cancel").clicked()
                {
                    close = true;
                }
                if ui
                    .button(egui::RichText::new("Remove from list").color(egui::Color32::LIGHT_RED))
                    .clicked()
                {
                    confirmed = true;
                    close = true;
                }
            });
        });

    if confirmed {
        let was_active = app.active_person == person && app.active_search == search;
        profiles::unregister_search(&mut app.profile_registry, &person, &search);
        if let Err(e) = profiles::save(&app.profile_registry) {
            app.profile_message = Some(format!("could not save profiles.json: {e}"));
        } else {
            app.profile_message =
                Some(format!("removed \"{name}\" from the list; its data is untouched"));
        }
        // Only leave the removed search if it was the one being viewed.
        if was_active {
            app.profile_switch_request = Some((
                profiles::DEFAULT_PROFILE.to_string(),
                profiles::DEFAULT_SEARCH.to_string(),
            ));
        }
    }
    if close {
        app.profile_pending_removal = None;
    }
}

/// Light or dark, saved per profile in desktop_settings.json.
///
/// Per profile rather than machine-wide because a profile is already the unit
/// that owns its own home directory and its own settings file, and adding a
/// second, global place for preferences to live would mean two files to keep
/// in step for one checkbox.
fn appearance(app: &mut UnlatchedApp, ui: &mut egui::Ui) {
    ui.strong("Appearance");
    ui.horizontal(|ui| {
        let mut dark = app.settings.is_dark();
        // Both radios are drawn before either result is tested. Written as
        // `a.clicked() || b.clicked()` the short-circuit skips drawing the
        // second one on any frame the first is clicked, so the Dark option
        // would vanish for a frame exactly when it was being chosen.
        let chose_light = crate::access::tag_with_value(
            ui.radio_value(&mut dark, false, "Light"),
            egui::WidgetType::RadioButton,
            "theme-light",
            if dark { "false" } else { "true" },
        )
        .clicked();
        let chose_dark = crate::access::tag_with_value(
            ui.radio_value(&mut dark, true, "Dark"),
            egui::WidgetType::RadioButton,
            "theme-dark",
            if dark { "true" } else { "false" },
        )
        .clicked();
        if chose_light || chose_dark {
            app.settings.theme = if dark {
                settings::DARK.to_string()
            } else {
                settings::LIGHT.to_string()
            };
            // Written immediately. A theme that reverts on restart reads as
            // the setting not having worked, and there is no Save button on
            // this screen for it to belong to.
            match settings::save(&app.settings_path, &app.settings) {
                Ok(()) => app.profile_message = None,
                Err(e) => {
                    app.profile_message = Some(format!("could not save the theme: {e}"));
                }
            }
        }
    });
}

/// Which browser a job link opens in.
///
/// SHIPS AS THE DEVICE DEFAULT, and that is not a placeholder. A posting is a
/// web page, so the browser this machine already opens web pages with is
/// correct until somebody says otherwise; naming one in the code would be
/// right on exactly one machine.
///
/// WHY ANYBODY WOULD CHANGE IT: a job hunt has its own logins - the ATS
/// accounts, the saved profile, the autofill - and the browser holding those is
/// often not the one that opens email. Sending postings to that browser and
/// only that browser is what the setting is for.
fn opening_links(app: &mut UnlatchedApp, ui: &mut egui::Ui) {
    ui.strong("Open job links in");
    let mut choice = app.settings.browser.clone();
    let installed = crate::browse::installed();
    ui.horizontal(|ui| {
        egui::ComboBox::from_id_source("browser_choice")
            .selected_text(crate::browse::label(&choice))
            .show_ui(ui, |ui| {
                crate::access::tag(
                    ui.selectable_value(&mut choice, String::new(), "System default"),
                    egui::WidgetType::SelectableLabel,
                    "browser-system-default",
                );
                for (name, path) in &installed {
                    crate::access::tag(
                        ui.selectable_value(&mut choice, path.clone(), name),
                        egui::WidgetType::SelectableLabel,
                        format!("browser-{}", crate::access::slug(name)),
                    );
                }
            });
        if ui
            .button("Choose...")
            .on_hover_text("Pick a browser this list does not know about")
            .clicked()
        {
            if let Some(picked) = rfd::FileDialog::new().pick_file() {
                choice = picked.to_string_lossy().into_owned();
            }
        }
    });
    // A CHOSEN BROWSER CAN BE UNINSTALLED. Said here rather than discovered on
    // the day a link opens somewhere unexpected - the fallback is deliberate,
    // and silent fallbacks are the ones that read as bugs.
    if !choice.is_empty() && !std::path::Path::new(&choice).is_file() {
        ui.weak(format!(
            "{} is not there any more - links will open in the system default \
             until you choose again.",
            crate::browse::label(&choice)
        ));
    }
    if choice != app.settings.browser {
        app.settings.browser = choice;
        // Written immediately, for the same reason the theme is: there is no
        // Save button on this screen for it to belong to.
        match settings::save(&app.settings_path, &app.settings) {
            Ok(()) => app.profile_message = None,
            Err(e) => {
                app.profile_message = Some(format!("could not save the browser choice: {e}"));
            }
        }
    }
}

/// Getting the pipeline out, and saying plainly that it is already being kept.
///
/// Two applications were once lost because their status existed in exactly one
/// place with no way to read it out. A single authoritative store is the right
/// architecture and also a single point of loss, so the way out has to be
/// somewhere a person can find on the day they need it - which is not a day
/// they will spend reading documentation.
/// Moving the search between this app and another tool.
///
/// ON SETTINGS RATHER THAN CONFIG, beside "Your data": what this writes is a
/// file about the search, not part of the search. Somebody on the Config
/// screen is editing what they are looking for; somebody here is moving it.
fn criteria(app: &mut UnlatchedApp, ui: &mut egui::Ui) {
    ui.strong("Your criteria, in another tool");
    ui.horizontal(|ui| {
        if crate::access::tag(
            ui.button("Save to a file"),
            egui::WidgetType::Button,
            "criteria-export",
        )
        .on_hover_text(
            "The titles, skills, places and floors - what you are looking for. \
             No keys, no resume, no schedule: those belong to this install, not \
             to the search.",
        )
        .clicked()
        {
            app.export_criteria();
        }
        if crate::access::tag(
            ui.button("Take one in"),
            egui::WidgetType::Button,
            "criteria-import",
        )
        .on_hover_text("Shows you what it would change before anything happens.")
        .clicked()
        {
            app.choose_criteria_file();
        }
        if let Some(message) = &app.criteria_message {
            ui.weak(message);
        }
    });
}

/// What a criteria file would do, before it does it.
///
/// A CENTRED WINDOW, built the same way show_removal_confirmation is: egui
/// 0.28 has no modal, so what is behind stays clickable. What this DOES
/// guarantee is that the dialog never closes by accident - it carries no X
/// (egui only draws one when given an `open` flag) and nothing outside the
/// Cancel and apply branches clears `criteria_import`. That matters here
/// because this is the one moment the change is visible, and a dialog that
/// closed on a stray click would take that moment away while applying
/// nothing, which reads as the app having ignored the file.
fn show_criteria_preview(app: &mut UnlatchedApp, ui: &mut egui::Ui) {
    let Some(pending) = &app.criteria_import else {
        return;
    };
    let mut close = false;
    let mut apply = false;
    let mut switch_to: Option<String> = None;
    let mode = pending.mode.clone();

    egui::Window::new("Take in these criteria")
        .collapsible(false)
        .resizable(false)
        .anchor(egui::Align2::CENTER_CENTER, [0.0, 0.0])
        .show(ui.ctx(), |ui| {
            ui.weak(pending.path.display().to_string());
            ui.add_space(6.0);

            match &pending.report {
                Err(why) => {
                    // The engine's own sentence. It names what is wrong with
                    // the file - format, version, or carrying none of the
                    // three blocks - which is what somebody needs to fix it.
                    ui.colored_label(egui::Color32::LIGHT_RED, why);
                }
                Ok(report) if report.is_empty() => {
                    ui.label("Nothing would change - these criteria already match yours.");
                }
                Ok(report) => {
                    ui.horizontal(|ui| {
                        ui.label("Lists in this file:");
                        for (value, label, hint) in [
                            ("replace", "replace mine",
                             "Your titles, skills and places become the ones in the file."),
                            ("merge", "add to mine",
                             "Anything new in the file is added. Nothing of yours is removed."),
                        ] {
                            let chosen = mode == value;
                            if crate::access::tag_with_value(
                                ui.selectable_label(chosen, label),
                                egui::WidgetType::RadioButton,
                                format!("criteria-mode-{value}"),
                                if chosen { "true" } else { "false" },
                            )
                            .on_hover_text(hint)
                            .clicked()
                            {
                                switch_to = Some(value.to_string());
                            }
                        }
                    });
                    ui.add_space(6.0);
                    ui.label(format!(
                        "{} change{}:",
                        report.preview.len(),
                        if report.preview.len() == 1 { "" } else { "s" }
                    ));
                    egui::ScrollArea::vertical().max_height(260.0).show(ui, |ui| {
                        egui::Grid::new("criteria_preview")
                            .num_columns(2)
                            .striped(true)
                            .show(ui, |ui| {
                                for change in &report.preview {
                                    ui.label(change.where_it_is());
                                    ui.label(change.what_happens());
                                    ui.end_row();
                                }
                            });
                    });
                }
            }

            ui.add_space(10.0);
            ui.horizontal(|ui| {
                let can_apply = matches!(&pending.report, Ok(r) if !r.is_empty());
                let label = if mode == "merge" { "Add these" } else { "Replace mine" };
                if crate::access::tag(
                    ui.add_enabled(can_apply, egui::Button::new(label)),
                    egui::WidgetType::Button,
                    "criteria-apply",
                )
                .clicked()
                {
                    apply = true;
                }
                if crate::access::tag(ui.button("Cancel"), egui::WidgetType::Button, "criteria-cancel")
                    .clicked()
                {
                    close = true;
                }
            });
        });

    if let Some(mode) = switch_to {
        app.set_criteria_mode(&mode);
    } else if apply {
        app.apply_criteria_import();
    } else if close {
        app.criteria_import = None;
        app.criteria_message = None;
    }
}

fn your_data(app: &mut UnlatchedApp, ui: &mut egui::Ui) {
    ui.strong("Your data");
    ui.horizontal(|ui| {
        if ui
            .button("Export to a spreadsheet")
            .on_hover_text(
                "Every job, its status, when you applied and the whole history - \
                 including ones you removed or that were taken down. Opens in \
                 Excel, Numbers or Sheets.",
            )
            .clicked()
        {
            app.export_pipeline();
        }
        if let Some(message) = &app.export_message {
            ui.weak(message);
        } else {
            ui.weak("a CSV you can open anywhere");
        }
    });
    ui.weak(format!(
        "A copy is also kept as {} beside your database, refreshed as you work, \
         so a readable one is always there without you having to remember.",
        crate::app::BACKUP_CSV_NAME
    ));
}

/// Which build this is.
///
/// THE WINDOW COULD NOT ANSWER "AM I ON THE LATEST", and the question came up
/// twice in one day of shipping several times. The version was on the exe's
/// file properties and in the Windows apps list, and nowhere a person looking
/// at the app could see it.
///
/// IT ALSO SAYS WHEN THIS IS NOT THE INSTALLED COPY. A test build is compiled
/// from the same source and carries the same version number, so a version
/// alone cannot tell the two apart - which is exactly the confusion it is here
/// to end. The install location is the only thing that differs, so that is
/// what is read.
fn which_build(ui: &mut egui::Ui) {
    let version = env!("CARGO_PKG_VERSION");
    let installed = std::env::var_os("LOCALAPPDATA")
        .map(std::path::PathBuf::from)
        .map(|base| base.join("Programs").join("Unlatched"));
    let here = std::env::current_exe().ok().and_then(|p| p.parent().map(PathBuf::from));
    let is_installed = match (&installed, &here) {
        (Some(want), Some(got)) => want == got,
        // Nowhere to compare against - say nothing rather than guess. On a
        // platform without that variable this is simply the version line.
        _ => true,
    };
    if is_installed {
        ui.weak(format!("Unlatched {version}"));
    } else {
        ui.weak(format!("Unlatched {version} - test build, not the installed copy"))
            .on_hover_text(
                here.map(|p| p.display().to_string())
                    .unwrap_or_else(|| "running from an unknown location".to_string()),
            );
    }
}

/// Which status changes stop to ask for a note.
///
/// The note is worth asking for on an interview or a declined offer and almost
/// never wanted on Applied, which is the one set most often - so this is per
/// status rather than one switch that would take the useful prompts with it.
fn note_prompts(app: &mut UnlatchedApp, ui: &mut egui::Ui) {
    ui.strong("Ask for a note when I set");
    ui.weak(
        "Unticked, the status is recorded straight away with no prompt. \
         Nothing else changes - it is still written to the job's history.",
    );
    ui.add_space(4.0);

    // FROM THE VOCABULARY ITSELF, so a status added later shows up here
    // without anybody remembering to come back.
    let mut changed = false;
    for spec in crate::status::FLOW.iter() {
        if spec.value == "offer" {
            ui.horizontal(|ui| {
                let mut always = true;
                ui.add_enabled(false, egui::Checkbox::new(&mut always, spec.label));
                ui.weak("always asks - this is where pay and the date are recorded");
            });
            continue;
        }
        let mut asks = app.settings.asks_for_note(spec.value);
        if crate::access::tag(
            ui.checkbox(&mut asks, spec.label),
            egui::WidgetType::Checkbox,
            format!("note-prompt-{}", spec.value),
        )
        .changed()
        {
            app.settings.quiet_statuses.retain(|s| s != spec.value);
            if !asks {
                app.settings.quiet_statuses.push(spec.value.to_string());
            }
            changed = true;
        }
    }

    if changed {
        // Written immediately - there is no Save button on this screen, and a
        // setting that reverts on restart reads as one that did not work.
        match settings::save(&app.settings_path, &app.settings) {
            Ok(()) => app.profile_message = None,
            Err(e) => {
                app.profile_message = Some(format!("could not save the setting: {e}"));
            }
        }
    }
}

/// How long the walkthrough takes, as told to somebody deciding whether to
/// start it. The walkthrough's own opening step makes the same promise, and
/// the two drifted apart once already - this screen said a minute over
/// eleven steps. Held as a constant so a test can hold them together.
const TOUR_LENGTH: &str = "two minutes";

/// Help. Currently one thing, but it is the thing people look for by name
/// after they have skipped a walkthrough and wished they had not.
fn help(app: &mut UnlatchedApp, ui: &mut egui::Ui) {
    ui.strong("Help");
    ui.horizontal(|ui| {
        if crate::access::tag(
            ui.button("Run the walkthrough again"),
            egui::WidgetType::Button,
            "settings-run-walkthrough",
        )
        .clicked()
        {
            app.start_tutorial();
        }
        ui.weak(format!("a guided tour of the app, about {TOUR_LENGTH}"));
    });
}

#[cfg(test)]
mod tests {
    use super::TOUR_LENGTH;

    /// One promise about the same walkthrough, made in two places. Settings
    /// said "about a minute" for eleven steps while the walkthrough's own
    /// first step said two - and whichever a person read, the other one made
    /// the app look like it had lost track of itself.
    #[test]
    fn the_settings_button_and_the_walkthrough_agree_on_how_long_it_takes() {
        let opening = crate::tutorial::STEPS[0].body;
        assert!(
            opening.contains(TOUR_LENGTH),
            "the walkthrough opens by promising something other than {TOUR_LENGTH:?}:              {opening}"
        );
    }
}
