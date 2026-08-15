//! Agents: how an assistant works WITH this app, and the optional endpoint.
//!
//! The first user asked the right question - "if a user tells an agent there's an API for
//! this app, does the agent not just connect?" - and the answer needed to live
//! somewhere a user would find it rather than in a chat log.
//!
//! There is no API and no server. Nothing listens on a port. What an agent
//! connects to is a local command-line tool and a SQLite file on this machine,
//! so it "connects" by RUNNING COMMANDS, and it can discover the whole surface
//! with --help. That is why no key and no account are needed for the case that
//! matters most: an assistant already running on the same computer.
//!
//! The optional endpoint below is a different, smaller thing - it lets the app
//! itself call a model. It is off unless configured, and scoring never touches
//! it.

use eframe::egui;

use crate::app::UnlatchedApp;
use crate::theme;

/// Setting a search up, which an assistant can do end to end. Titles are the
/// step that decides whether a search works at all, and the step people are
/// worst at - pure vocabulary generation, which is what a model is good at.
const SETUP_COMMANDS: [(&str, &str); 4] = [
    (
        "unlatched config set search.title_include \"Support Analyst,Help Desk\"",
        "The job titles to look for. A comma-separated list becomes a list: the \
         value is coerced to whatever type the setting already holds.",
    ),
    (
        "unlatched config set skills \"Customer Service,Active Directory\"",
        "The words worth tracking across postings, and the ones your resume is \
         then measured against.",
    ),
    (
        "unlatched discover --name \"Oak Ridge National Laboratory\"",
        "Find an employer's job board. Repeat per employer, or use --file with \
         one name per line.",
    ),
    (
        "unlatched collect",
        "Read every board found. Then `screen` re-judges everything against the \
         current settings.",
    ),
];

/// The commands worth putting in front of somebody once a search exists.
/// `brief` is first because it answers "what should I work on" in one call.
const COMMANDS: [(&str, &str); 4] = [
    (
        "unlatched brief --json",
        "Everything needed to improve this resume in one call, with nothing to \
         look up first: the words you are missing ranked by demand, the same \
         list broken down PER JOB TITLE in your search, the jobs matching \
         worst, and how to hand the edited copy back.",
    ),
    (
        "unlatched jobs --json",
        "Every stored job with its score, verdict and match reasons.",
    ),
    (
        "unlatched keywords --json",
        "What every collected posting asks for, ranked by how many want it, and \
         which of those your resume already shows.",
    ),
    (
        "unlatched resume attach optimized.docx --role optimized",
        "Hand the edited resume back. The assistant names the file it just \
         wrote, so nothing needs looking up; the original is kept, never \
         overwritten, so a finished search stays reproducible.",
    ),
];

/// Where the common local model runners listen by default. Mirrors
/// KNOWN_LOCAL_ENDPOINTS in the engine's agent_api.py. Offered as starting
/// points, NOT as one universal address: 11434 is Ollama's default and
/// nobody else's, and showing it alone strands anyone on a different runner,
/// a remapped container port, or another machine.
const LOCAL_ENDPOINTS: [(&str, &str); 4] = [
    ("Ollama", "http://localhost:11434/v1"),
    ("LM Studio", "http://localhost:1234/v1"),
    ("llama.cpp", "http://localhost:8080/v1"),
    ("Jan", "http://localhost:1337/v1"),
];

pub fn show(app: &mut UnlatchedApp, ui: &mut egui::Ui) {
    ui.heading("Agents");
    ui.label(
        "How an AI assistant on this computer works with Unlatched, and the \
         optional endpoint for letting the app call a model itself.",
    );
    ui.add_space(12.0);

    egui::ScrollArea::vertical().show(ui, |ui| {
        no_api_card(ui);
        ui.add_space(10.0);
        ui.columns(2, |cols| {
            cols[0].push_id("agent_commands", |ui| {
                setup_card(ui);
                ui.add_space(10.0);
                commands_card(ui);
            });
            cols[1].push_id("agent_endpoint", |ui| {
                endpoint_card(app, ui);
                ui.add_space(10.0);
                limits_card(ui);
            });
        });
    });
}

fn card(ui: &mut egui::Ui, title: &str, contents: impl FnOnce(&mut egui::Ui)) {
    egui::Frame::none()
        .inner_margin(egui::Margin::same(14.0))
        .rounding(egui::Rounding::same(6.0))
        .stroke(egui::Stroke::new(
            1.0,
            ui.visuals().widgets.noninteractive.bg_stroke.color,
        ))
        .show(ui, |ui| {
            ui.set_min_width(ui.available_width());
            ui.label(
                egui::RichText::new(title)
                    .size(theme::TEXT_LABEL)
                    .strong()
                    .color(ui.visuals().weak_text_color()),
            );
            ui.add_space(7.0);
            contents(ui);
        });
}

fn no_api_card(ui: &mut egui::Ui) {
    card(ui, "THERE IS NO API, AND THAT IS THE POINT", |ui| {
        ui.label(
            "Nothing listens on a port and there is no account to create. This app \
             is a command-line tool plus a database file on this machine, so an \
             assistant already running here - Claude Code in an editor, say - works \
             with it by running commands and reading what comes back.",
        );
        ui.add_space(8.0);
        ui.label("Tell your assistant this much and it can work out the rest:");
        ui.add_space(4.0);
        copyable(ui, "There is a CLI for my job search app. Run: unlatched --help");
        ui.add_space(8.0);
        ui.weak(
            "No API key, no endpoint, and nothing leaves this computer - the \
             assistant doing the reading is already on your side of the door.",
        );
    });
}

fn setup_card(ui: &mut egui::Ui) {
    card(ui, "SETTING UP A SEARCH - AN ASSISTANT CAN DO ALL OF THIS", |ui| {
        ui.label(
            "Deciding which job titles to search for is the step that decides \
             whether the search works, and the one most people get wrong. An \
             assistant can talk it through with you and write the answer straight \
             in.",
        );
        ui.add_space(8.0);
        for (command, note) in SETUP_COMMANDS {
            copyable(ui, command);
            ui.indent(command, |ui| {
                ui.weak(note);
            });
            ui.add_space(9.0);
        }
        ui.weak(
            "Best done twice: once to start, then again after a collection - by \
             then the app knows what employers actually posted, so the second \
             pass is grounded in evidence instead of guesswork.",
        );
    });
}

fn commands_card(ui: &mut egui::Ui) {
    card(ui, "THE COMMANDS THAT MATTER", |ui| {
        for (command, note) in COMMANDS {
            copyable(ui, command);
            ui.indent(command, |ui| {
                ui.weak(note);
            });
            ui.add_space(9.0);
        }
    });
}

/// A command in monospace with a Copy button. Reading a command off a screen
/// and retyping it is where the typo comes from.
fn copyable(ui: &mut egui::Ui, text: &str) {
    ui.horizontal(|ui| {
        egui::Frame::none()
            .fill(ui.visuals().extreme_bg_color)
            .rounding(egui::Rounding::same(4.0))
            .inner_margin(egui::Margin::symmetric(8.0, 4.0))
            .show(ui, |ui| {
                ui.add(
                    egui::Label::new(egui::RichText::new(text).monospace()).selectable(true),
                );
            });
        if ui.small_button("Copy").clicked() {
            ui.ctx().copy_text(text.to_string());
        }
    });
}

fn endpoint_card(app: &mut UnlatchedApp, ui: &mut egui::Ui) {
    let base_url = app.config.agent_api.base_url.clone().unwrap_or_default();
    let model = app.config.agent_api.model.clone().unwrap_or_default();
    let mut go_to_config = false;
    let mut test_requested = false;
    // Captured before the card is drawn, since the closure borrows `app`.
    let checking = app.running_process.is_some();
    let check_lines: Vec<String> = match app.agent_check_from {
        Some(from) => app.log_lines.get(from..).unwrap_or_default().to_vec(),
        None => Vec::new(),
    };

    card(ui, "OPTIONAL: LET THE APP CALL A MODEL", |ui| {
        if base_url.trim().is_empty() {
            ui.horizontal(|ui| {
                ui.colored_label(theme::ACCENT, "Not configured");
                ui.weak("- and nothing above needs it.");
            });
            ui.add_space(7.0);
            // "OpenAI-compatible endpoint" was the phrase here, and it reads to
            // a normal user as "you need an OpenAI account" - the opposite of
            // the truth, since the option being recommended is a free model on
            // their own machine. What it actually means is that nearly every
            // model service accepts the same shape of request, so there is
            // nothing for the reader to choose between. Say that instead.
            ui.label(
                "This is the other direction: the APP calling a model, rather than \
                 an assistant calling the app. It works with a free model running \
                 on this computer, or with a paid service if you already have one \
                 - almost all of them accept the same kind of request, so there is \
                 nothing to pick between here.",
            );
            ui.add_space(7.0);
            ui.label(
                "A model on your own computer costs nothing and needs no account. \
                 Whichever one you run, its address goes in the box - these are \
                 just the usual defaults:",
            );
            ui.add_space(4.0);
            for (name, url) in LOCAL_ENDPOINTS {
                ui.horizontal(|ui| {
                    ui.add_sized(
                        [110.0, 18.0],
                        egui::Label::new(egui::RichText::new(name).weak()),
                    );
                    copyable(ui, url);
                });
            }
            ui.add_space(4.0);
            ui.weak(
                "Yours will differ if you changed the port, run it in a container, \
                 or run it on another machine - any address works, these are only \
                 starting points.",
            );
            ui.add_space(7.0);
            ui.horizontal(|ui| {
                ui.weak("Set it as agent_api.base_url on");
                if ui.small_button("Config").clicked() {
                    go_to_config = true;
                }
            });
        } else {
            ui.horizontal(|ui| {
                ui.colored_label(egui::Color32::from_rgb(34, 197, 94), "Configured");
                ui.monospace(&base_url);
            });
            if !model.trim().is_empty() {
                ui.horizontal(|ui| {
                    ui.weak("Model");
                    ui.monospace(&model);
                });
            }
            ui.add_space(7.0);
            ui.horizontal(|ui| {
                // Verifying beats guessing. A wrong address otherwise shows up
                // much later as a suggestion that silently never arrives.
                if ui.button("Test connection").clicked() {
                    test_requested = true;
                }
                ui.weak("asks the endpoint what models it has");
            });
            show_test_result(ui, &check_lines, checking);
            ui.add_space(7.0);
            ui.label("Suggestions run from a terminal, not from this screen:");
            copyable(ui, "unlatched agent suggest-terms");
        }
    });

    // Applied after the card is drawn: the closure above borrows `app`.
    if go_to_config {
        app.view = crate::app::View::Config;
    }
    if test_requested {
        // Remember where this test's output starts so it can be rendered
        // below, on this card. The view does NOT change.
        app.agent_check_from = Some(app.log_lines.len());
        app.start_process(
            "test agent endpoint",
            vec!["agent".to_string(), "check".to_string()],
        );
    }
}

fn limits_card(ui: &mut egui::Ui) {
    card(ui, "WHAT IS NEVER SENT, AND WHY", |ui| {
        for line in [
            "Job descriptions are never sent anywhere. They are the employers' \
             text, collected for you to read - not ours to pass on.",
            "No model is ever involved in deciding whether a job matches. Scores \
             are plain term matching, so the same search means the same thing for \
             everyone, whether or not they pay for a model.",
            "The optional endpoint only ever receives text you wrote yourself - \
             your resume, your own prompt.",
        ] {
            // horizontal_top does NOT wrap, so the longest of these ran off
            // the right edge of the window. The bullet is drawn, then the text
            // is given the rest of the width with wrapping on.
            ui.horizontal_top(|ui| {
                ui.label(egui::RichText::new("-").color(theme::ACCENT).strong());
                ui.allocate_ui_with_layout(
                    egui::vec2(ui.available_width(), 0.0),
                    egui::Layout::top_down(egui::Align::LEFT),
                    |ui| {
                        ui.add(egui::Label::new(line).wrap());
                    },
                );
            });
            ui.add_space(6.0);
        }
    });
}


/// The endpoint test's own output, in place. A result that appears on a
/// different screen is not a result the person asked for.
fn show_test_result(ui: &mut egui::Ui, lines: &[String], running: bool) {
    if lines.is_empty() && !running {
        return;
    }
    ui.add_space(6.0);
    egui::Frame::none()
        .fill(ui.visuals().extreme_bg_color)
        .rounding(egui::Rounding::same(4.0))
        .inner_margin(egui::Margin::symmetric(9.0, 7.0))
        .show(ui, |ui| {
            ui.set_min_width(ui.available_width());
            if running && lines.is_empty() {
                ui.horizontal(|ui| {
                    ui.spinner();
                    ui.weak("asking the endpoint...");
                });
                return;
            }
            for line in lines {
                // The runner echoes the command it spawned, which here is the
                // full path to the bundled engine executable. Useful in the
                // log; on this card it is a wall of path nobody asked for, and
                // it pushes the actual answer below the fold.
                if line.starts_with("$ ") || line.starts_with("[test agent endpoint finished") {
                    continue;
                }
                // The engine prints "OK - ..." or "FAILED - ...". Colouring
                // those two saves the reader parsing a wall of grey.
                if line.starts_with("OK") {
                    ui.colored_label(egui::Color32::from_rgb(34, 197, 94), line);
                } else if line.starts_with("FAILED") {
                    ui.colored_label(egui::Color32::from_rgb(220, 38, 38), line);
                } else {
                    ui.add(egui::Label::new(egui::RichText::new(line).monospace()).wrap());
                }
            }
        });
}
