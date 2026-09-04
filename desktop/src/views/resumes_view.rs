//! Resumes the app HOLDS: attach, list, and which one screening reads.
//!
//! Until this existed the feature was real but unreachable - copies worked,
//! screening read them, and the only way to attach one was a terminal
//! command. A person who needs a terminal to attach their resume does not
//! have the feature.
//!
//! Two ways in, because people expect both: a drop target for dragging a file
//! from a folder window, and a Browse button for people who do not drag. The
//! drop target is the primary one; egui reports dropped files on the frame
//! they land, and the whole panel is the target rather than a small strip,
//! since a drop zone you have to aim at is worse than a button.

use eframe::egui;

use crate::app::UnlatchedApp;

use crate::resumes::{OPTIMIZED, ORIGINAL};

pub fn show(app: &mut UnlatchedApp, ui: &mut egui::Ui) {
    ui.heading("Resumes");
    ui.label(
        "The app keeps its own copy of every resume you attach. Nothing is \
         overwritten, so a search always has the exact document it was screened \
         against.",
    );
    ui.add_space(10.0);

    drop_target(app, ui, ORIGINAL, "ORIGINAL", "What you started with.");
    ui.add_space(10.0);
    drop_target(
        app,
        ui,
        OPTIMIZED,
        "OPTIMIZED",
        "The edited version, after working in the words you were missing. \
         Screening reads this one when it exists.",
    );

    ui.add_space(14.0);
    ui.separator();
    versions_list(app, ui);
}

/// The bubble: a rounded, dashed-feel panel that accepts a dropped file and
/// also offers a Browse button.
fn drop_target(app: &mut UnlatchedApp, ui: &mut egui::Ui, role: &str, title: &str, note: &str) {
    // Highlighted while a file is over the WINDOW, not over this box: egui
    // reports hover position only for the pointer, and a person dragging a
    // file wants to see that the app will take it before they aim.
    let dragging = ui.ctx().input(|i| !i.raw.hovered_files.is_empty());
    let stroke = if dragging {
        egui::Stroke::new(2.0, crate::theme::ACCENT)
    } else {
        egui::Stroke::new(1.0, ui.visuals().widgets.noninteractive.bg_stroke.color)
    };

    let response = egui::Frame::none()
        .fill(if dragging {
            crate::theme::ACCENT.gamma_multiply(0.12)
        } else {
            ui.visuals().faint_bg_color
        })
        .stroke(stroke)
        .rounding(egui::Rounding::same(8.0))
        .inner_margin(egui::Margin::symmetric(16.0, 14.0))
        .show(ui, |ui| {
            ui.set_min_width(ui.available_width());
            ui.label(
                egui::RichText::new(title)
                    .size(crate::theme::TEXT_LABEL)
                    .strong()
                    .color(ui.visuals().weak_text_color()),
            );
            ui.add_space(4.0);
            ui.horizontal(|ui| {
                ui.label("Drop a file here");
                if ui.button("Browse...").clicked() {
                    if let Some(path) = rfd::FileDialog::new()
                        .add_filter("Resume", &["docx", "txt", "md", "pdf"])
                        .pick_file()
                    {
                        attach(app, &path.to_string_lossy(), role);
                    }
                }
            });
            ui.weak(note);
        })
        .response;

    // Dropped files arrive on the frame they land. Taking them here, inside
    // the box that was drawn, keeps the two roles apart - a single global
    // handler could not tell which bubble the file was meant for.
    let dropped: Vec<String> = ui.ctx().input(|i| {
        i.raw
            .dropped_files
            .iter()
            .filter_map(|f| f.path.as_ref().map(|p| p.to_string_lossy().to_string()))
            .collect()
    });
    if !dropped.is_empty() && response.rect.contains(hover_pos(ui)) {
        for path in dropped {
            attach(app, &path, role);
        }
    }
}

/// Where the pointer was when the file was released. egui does not attach a
/// position to a dropped file, so the pointer's last known position is what
/// decides which bubble received it.
fn hover_pos(ui: &egui::Ui) -> egui::Pos2 {
    ui.ctx()
        .input(|i| i.pointer.latest_pos())
        .unwrap_or(egui::Pos2::ZERO)
}

fn attach(app: &mut UnlatchedApp, path: &str, role: &str) {
    app.start_process(
        &format!("attach {role} resume"),
        vec![
            "resume".to_string(),
            "attach".to_string(),
            path.to_string(),
            "--role".to_string(),
            role.to_string(),
        ],
    );
}

fn versions_list(app: &mut UnlatchedApp, ui: &mut egui::Ui) {
    ui.add_space(8.0);
    ui.strong("Attached copies");
    ui.weak("Newest first. The one screening reads is marked.");
    ui.add_space(6.0);

    let dir = crate::resumes::dir(&app.active_home);
    let files = crate::resumes::versions(&app.active_home);

    if files.is_empty() {
        ui.weak("Nothing attached yet.");
        return;
    }
    // THROUGH THE SHARED RESOLVER, so this marker cannot disagree with what
    // screening reads. It had its own copy of the rule that ignored a pin,
    // which put "in use" over a document the engine was not reading.
    let active = crate::resumes::active_name(&app.active_home, &app.config);

    let mut download_me: Option<String> = None;

    egui::Grid::new("resume_versions")
        .num_columns(4)
        .striped(true)
        .spacing([12.0, 6.0])
        .show(ui, |ui| {
            for (role, name) in &files {
                let is_active = active.as_deref() == Some(name.as_str());
                if is_active {
                    ui.colored_label(crate::theme::ACCENT, "in use");
                } else {
                    ui.label("");
                }
                ui.label(role);
                ui.monospace(name);
                if ui
                    .button("Download")
                    .on_hover_text("Puts a copy in your Downloads folder.")
                    .clicked()
                {
                    // Recorded and acted on after the grid, so the copy is not
                    // made while `files` is still borrowed.
                    download_me = Some(name.clone());
                }
                ui.end_row();
            }
        });

    if let Some(name) = download_me {
        app.resume_message = Some(download(&dir.join(&name), &name));
    }
    if let Some(message) = &app.resume_message {
        ui.add_space(6.0);
        ui.colored_label(crate::theme::ACCENT, message);
    }

    ui.add_space(6.0);
    if ui.button("Open the resumes folder").clicked() {
        // The person may want to hand a copy to an assistant, or keep one.
        // Opening the folder is the least surprising way to offer that.
        let _ = std::process::Command::new("explorer").arg(&dir).spawn();
    }
}

/// Copies one held resume back out of the app, and says where it went.
///
/// The point of this button (decided 2026-08-05) is recovery: the app holds the
/// only surviving copy once the original is deleted from disk, and a document
/// you cannot get back out is not really yours. Downloads is the default
/// because it is where every other program on the machine puts a file it hands
/// you, so nobody has to be told where to look - but if it cannot be found,
/// the person is asked rather than guessed at.
fn download(source: &std::path::Path, name: &str) -> String {
    let (target, to_downloads) = match crate::paths::downloads_dir() {
        Some(dir) => (crate::paths::non_clobbering_path(&dir, name), true),
        None => match rfd::FileDialog::new().set_file_name(name).save_file() {
            Some(chosen) => (chosen, false),
            None => return "Download cancelled.".to_string(),
        },
    };

    if let Err(e) = std::fs::copy(source, &target) {
        return format!("Could not save {name}: {e}");
    }

    // Names the file as SAVED, not as it was asked for: if the name was
    // already taken this is now "... (2).docx", and being told the wrong
    // name is how a downloaded file gets lost.
    let saved = target
        .file_name()
        .map(|n| n.to_string_lossy().to_string())
        .unwrap_or_else(|| name.to_string());
    if to_downloads {
        format!("Saved {saved} to your Downloads folder.")
    } else {
        format!("Saved {saved} to {}.", target.display())
    }
}
