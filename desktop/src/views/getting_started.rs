//! First-run guidance, shown until a search has been set up.
//!
//! The hardest part of a first search is not the software, it is knowing
//! what to type. A person knows the work they do; they usually do not know
//! the twenty different titles employers file that work under. A real search
//! only worked because someone researched 55 real role titles first -
//! "Technical Support Engineer", "Application Support Specialist",
//! "Escalation Engineer" - and a term list built from a job seeker's own
//! vocabulary misses most of what is posted.
//!
//! So the guidance is mostly about that one step, and it teaches a method
//! rather than handing over a list: a list ages and only fits one trade.

use eframe::egui;

use crate::app::{UnlatchedApp, View};

/// Shown when the active profile has no title terms and no employers - the
/// state a brand new profile is in. It disappears on its own once either
/// exists, so nobody has to dismiss it.
pub fn needed(app: &UnlatchedApp) -> bool {
    app.config.search.title_include.is_empty() && app.companies.is_empty()
}

pub fn show(app: &mut UnlatchedApp, ui: &mut egui::Ui) {
    ui.heading("Set up your first search");
    ui.label(
        "Unlatched reads employers' own job boards directly. Nothing is sent anywhere, \
         and everything it finds stays on this computer.",
    );
    ui.add_space(10.0);

    step(ui, 1, "Find the real job titles for the work you do");
    ui.indent("titles", |ui| {
        ui.label(
            "This is the step that decides whether the search works, and it is worth \
             fifteen minutes. Employers file the same job under many different titles, \
             and the ones you would think of are usually a fraction of them.",
        );
        ui.add_space(4.0);
        ui.label("In a browser, search for the work you do plus the word jobs:");
        ui.add_space(2.0);
        ui.monospace("    customer support jobs remote");
        ui.monospace("    hr generalist jobs knoxville tn");
        ui.monospace("    carpenter foreman jobs");
        ui.add_space(4.0);
        ui.label(
            "Then read the results and write down the TITLES, not the companies. Ten or \
             twenty minutes of this typically turns three titles you thought of into \
             twenty-five that employers actually use - Support Analyst, Application \
             Support Specialist, Service Desk Technician, Implementation Consultant, \
             Technical Account Manager.",
        );
        ui.add_space(4.0);
        ui.weak(
            "Add every variant you would take. A title you leave out is a job you will \
             never be shown.",
        );
    });

    ui.add_space(8.0);
    step(ui, 2, "Put those titles into the search");
    ui.indent("config", |ui| {
        ui.horizontal_wrapped(|ui| {
            ui.label("Open");
            if ui.button("Config").clicked() {
                app.view = View::Config;
            }
            ui.label(
                "and type each title into Title include, pressing Enter after each one. \
                 Set what you will accept for pay, location and employment type there too.",
            );
        });
    });

    ui.add_space(8.0);
    step(ui, 3, "Name the employers to watch");
    ui.indent("companies", |ui| {
        ui.horizontal_wrapped(|ui| {
            ui.label("Open");
            if ui.button("Companies").clicked() {
                app.view = View::Companies;
            }
            ui.label(
                "and add employers by name, then press Discover to find their job board. \
                 Start with anywhere you would genuinely work - the large employers in \
                 your area, and any company you already have in mind.",
            );
        });
        ui.add_space(4.0);
        ui.weak(
            "Internal roles like IT, HR, maintenance and accounting exist at every \
             employer, so a wide list of employers matters more than picking an industry.",
        );
        ui.add_space(6.0);
        // The hardest part of step 3 is the blank page. A measured list of
        // national employers is something to start FROM, so the first
        // collection returns real postings the same afternoon.
        ui.horizontal_wrapped(|ui| {
            ui.label("Short of names?");
            if ui.button("Add starter employers").clicked() {
                app.start_process(
                    "add starter employers",
                    vec!["starter".to_string(), "--add".to_string()],
                );
                app.view = View::Companies;
            }
            ui.label(
                "adds national employers whose boards this app can read. Add your own \
                 local ones too - those are the jobs you are most likely to get.",
            );
        });
    });

    ui.add_space(8.0);
    step(ui, 4, "Run the search");
    ui.indent("run", |ui| {
        ui.label(
            "On the Companies page, open Collect and choose Every employer. It takes a \
             while the first time. After that the search keeps itself current on its own \
             - twice on weekdays, once a day at weekends, and it catches up on whatever \
             it missed if the app has been closed for a few days. Change it or switch it \
             off in Config.",
        );
    });

    ui.add_space(12.0);
    ui.separator();
    ui.weak(
        "This page goes away by itself once you have added titles or employers.",
    );
}

fn step(ui: &mut egui::Ui, number: u8, title: &str) {
    ui.horizontal(|ui| {
        ui.strong(format!("{number}."));
        ui.strong(title);
    });
}
