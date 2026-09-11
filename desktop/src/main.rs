// Hide the console window in release builds; keep it in debug so dev
// runs still show panic output and child-process logs - by definition
// of the windows_subsystem attribute below, which only applies when
// debug_assertions is off.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

// Unlatched desktop front end. Reads and writes the same SQLite
// database and config.json as the command-line tool - verified
// 2026-09-10: db.rs names the same "unlatched.db" file db.py does, and
// config.rs's own header calls config.json "shared with the
// command-line side of the app". Long-running network work is never
// done here - verified 2026-09-10: no reqwest, TcpStream or TcpListener
// appears anywhere in desktop/src - only by spawning that CLI as a
// child process and streaming its output into a log pane.

mod access;
mod app;
mod attachments;
mod browse;
mod collectors;
mod config;
mod config_draft;
mod criteria;
mod dashboard;
mod date;
mod db;
mod engine;
mod fmt;
mod modules;
mod paths;
mod places;
mod process;
mod profiles;
mod resumes;
mod secrets;
mod settings;
mod status;
mod theme;
mod tutorial;
mod views;

fn window_icon() -> Option<eframe::egui::IconData> {
    let png = include_bytes!("../assets/icon.png");
    let img = image::load_from_memory(png).ok()?.into_rgba8();
    let (width, height) = img.dimensions();
    Some(eframe::egui::IconData {
        rgba: img.into_raw(),
        width,
        height,
    })
}

fn main() -> eframe::Result<()> {
    let mut viewport = eframe::egui::ViewportBuilder::default()
        .with_title("Unlatched")
        .with_inner_size([1100.0, 720.0])
        .with_min_inner_size([700.0, 480.0]);
    if let Some(icon) = window_icon() {
        viewport = viewport.with_icon(icon);
    }
    let options = eframe::NativeOptions {
        viewport,
        ..Default::default()
    };

    eframe::run_native(
        "Unlatched",
        options,
        Box::new(|_cc| Ok(Box::new(app::UnlatchedApp::new()))),
    )
}
