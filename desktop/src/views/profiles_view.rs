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

use crate::app::{NewProfileDraft, UnlatchedApp};
use crate::profiles;
use crate::settings;

pub fn show(app: &mut UnlatchedApp, ui: &mut egui::Ui) {
    ui.heading("Settings");
    ui.separator();
    appearance(app, ui);
    ui.add_space(12.0);
    help(app, ui);
    ui.add_space(12.0);
    your_data(app, ui);
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

    if ui.button("New profile").clicked() {
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
                if ui.button("Cancel").clicked() {
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
        let chose_light = ui.radio_value(&mut dark, false, "Light").clicked();
        let chose_dark = ui.radio_value(&mut dark, true, "Dark").clicked();
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


/// Help. Currently one thing, but it is the thing people look for by name
/// after they have skipped a walkthrough and wished they had not.
/// Getting the pipeline out, and saying plainly that it is already being kept.
///
/// Two applications were once lost because their status existed in exactly one
/// place with no way to read it out. A single authoritative store is the right
/// architecture and also a single point of loss, so the way out has to be
/// somewhere a person can find on the day they need it - which is not a day
/// they will spend reading documentation.
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

fn help(app: &mut UnlatchedApp, ui: &mut egui::Ui) {
    ui.strong("Help");
    ui.horizontal(|ui| {
        if ui.button("Run the walkthrough again").clicked() {
            app.start_tutorial();
        }
        ui.weak("a guided tour of the app, about a minute");
    });
}
