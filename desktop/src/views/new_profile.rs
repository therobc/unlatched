// The "New profile" modal. Two required text fields (name, home folder)
// plus an optional resume path, each with a native-picker Browse button as
// an assist -- typing directly into the field is always the primary path,
// since a keyboard-only user (and the QC harness, which drives the app
// with typed keys rather than a mouse) never has to touch a Browse button
// to create a profile.

use eframe::egui;
use std::path::PathBuf;

use crate::app::UnlatchedApp;
use crate::profiles;

pub fn show(app: &mut UnlatchedApp, ctx: &egui::Context) {
    if !app.show_new_profile_modal {
        return;
    }

    let mut still_open = true;
    let mut create_clicked = false;
    let mut cancel_clicked = false;

    egui::Window::new("New profile")
        .collapsible(false)
        .resizable(false)
        .open(&mut still_open)
        .show(ctx, |ui| {
            // These four fields are named because this modal has GAINED A ROW
            // twice, and each time every field below the new one moved and a
            // harness had to be re-measured by hand.
            ui.horizontal(|ui| {
                ui.label("Person:");
                crate::access::tag(
                    ui.text_edit_singleline(&mut app.new_profile_draft.name)
                        .on_hover_text(
                            "An existing name adds another search for that person and brings \
                             their employers and resume across.",
                        ),
                    egui::WidgetType::TextEdit,
                    "new-profile-person",
                );
            });
            ui.horizontal(|ui| {
                ui.label("Search name:");
                crate::access::tag(
                    ui.text_edit_singleline(&mut app.new_profile_draft.search)
                        .on_hover_text(
                            "What this hunt is called - \"HR Generalist\", \"Administrative\". \
                             Leave blank for a first search.",
                        ),
                    egui::WidgetType::TextEdit,
                    "new-profile-search",
                );
            });
            ui.horizontal(|ui| {
                ui.label("Folder:");
                let hint = profiles::suggested_home(
                    app.new_profile_draft.name.trim(),
                    if app.new_profile_draft.search.trim().is_empty() {
                        profiles::DEFAULT_SEARCH
                    } else {
                        app.new_profile_draft.search.trim()
                    },
                );
                crate::access::tag(
                    ui.add(
                        egui::TextEdit::singleline(&mut app.new_profile_draft.home)
                            .hint_text(hint.display().to_string()),
                    ),
                    egui::WidgetType::TextEdit,
                    "new-profile-folder",
                );
                if ui.button("Browse...").clicked() {
                    if let Some(dir) = rfd::FileDialog::new().pick_folder() {
                        app.new_profile_draft.home = dir.to_string_lossy().into_owned();
                    }
                }
            });
            ui.horizontal(|ui| {
                ui.label("Resume (optional):");
                ui.text_edit_singleline(&mut app.new_profile_draft.resume_path);
                if ui.button("Browse...").clicked() {
                    if let Some(file) = rfd::FileDialog::new()
                        .add_filter("Resume", &["docx", "txt", "md"])
                        .pick_file()
                    {
                        app.new_profile_draft.resume_path = file.to_string_lossy().into_owned();
                    }
                }
            });

            if let Some(err) = &app.new_profile_draft.error {
                ui.colored_label(egui::Color32::LIGHT_RED, err);
            }

            ui.separator();
            ui.horizontal(|ui| {
                if ui.button("Create").clicked() {
                    create_clicked = true;
                }
                if ui.button("Cancel").clicked() {
                    cancel_clicked = true;
                }
            });
        });

    if !still_open || cancel_clicked {
        app.show_new_profile_modal = false;
        app.new_profile_draft = Default::default();
        return;
    }

    if create_clicked {
        match validate_and_create(app, ctx) {
            Ok(()) => {
                app.show_new_profile_modal = false;
                app.new_profile_draft = Default::default();
            }
            Err(e) => {
                app.new_profile_draft.error = Some(e);
            }
        }
    }
}

/// Validates the draft, builds the new home, registers it, and switches to
/// it. Folder "creatability" is not pre-checked separately: the attempt
/// inside `create_profile_home` either succeeds or returns a clear error,
/// same reasoning as `paths::data_dir` not probing before it acts.
fn validate_and_create(app: &mut UnlatchedApp, ctx: &egui::Context) -> Result<(), String> {
    let person = app.new_profile_draft.name.trim().to_string();
    let mut search = app.new_profile_draft.search.trim().to_string();
    let home_text = app.new_profile_draft.home.trim().to_string();
    let resume = app.new_profile_draft.resume_path.trim().to_string();

    if person.is_empty() {
        return Err("name is required".to_string());
    }
    if person == profiles::DEFAULT_PROFILE || person == profiles::ENV_PROFILE {
        return Err(format!("'{person}' is a reserved name"));
    }
    if search.is_empty() {
        search = profiles::DEFAULT_SEARCH.to_string();
    }
    // An existing PERSON is fine and expected - that is how a second hunt gets
    // added. An existing person AND search is the real collision.
    if profiles::searches_for(&app.profile_registry, &person).contains(&search) {
        return Err(format!("{person} already has a search called '{search}'"));
    }
    // Blank means "put it in the usual place": Documents/Unlatched/<Person>/
    // <Search>. Documents rather than AppData because these are files a
    // placement specialist backs up, copies to a new machine, or hands to a
    // colleague, and AppData is hidden. Never Program Files, which is not
    // writable without administrator rights.
    let home = if home_text.is_empty() {
        profiles::suggested_home(&person, &search)
    } else {
        PathBuf::from(&home_text)
    };

    // A new search inherits the person's resume automatically, so only the
    // criteria have to be given. Asking for it again would be asking somebody
    // to re-answer a question about themselves.
    let inherited = profiles::resume_for(&app.profile_registry, &person);
    let resume_opt = if !resume.is_empty() {
        Some(resume.clone())
    } else {
        inherited
    };

    profiles::create_profile_home(&home, resume_opt.as_deref())?;

    // Carry this person's already-resolved employers into the new search, so
    // discovery is paid for once per person rather than once per hunt.
    let seeded = profiles::seed_from_sibling(&app.profile_registry, &person, &home);

    // Record the resume BEFORE registering, so the one save that
    // register_and_activate performs persists both. Two writes would leave a
    // window where the search exists and the person's resume does not.
    profiles::remember_resume(&mut app.profile_registry, &person, resume_opt.as_deref());
    profiles::register_and_activate(&mut app.profile_registry, &person, &search, &home)?;
    app.switch_profile(&person, &search, home, ctx);
    if let Some(count) = seeded {
        app.profile_message = Some(format!(
            "brought {count} employer(s) across from {person}'s other search, so they \
             do not have to be found again"
        ));
    }
    Ok(())
}
