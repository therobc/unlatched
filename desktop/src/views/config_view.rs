// Config: every key documented in config.json is editable here. Lists are
// edited one item per line; Save validates the whole form at once and
// reports every problem found rather than stopping at the first one.

use eframe::egui;

use crate::app::UnlatchedApp;
use crate::config::KNOWN_SOURCES;
use crate::config_draft::TagField;

pub fn show(app: &mut UnlatchedApp, ui: &mut egui::Ui) {
    ui.heading("Config");
    if let Some(err) = &app.config_error {
        ui.colored_label(
            egui::Color32::LIGHT_RED,
            format!("config.json problem: {err}"),
        );
    }

    // Measured HERE, outside the scroll area. Inside it - and inside a
    // collapsing section in particular - available_width is infinite, so a
    // wrapped row never reaches a wrap point and a long tag list runs off
    // the right edge, clipped rather than scrollable. A 55-term title list
    // did exactly that: the terms past the edge could not be read or
    // removed at all. The margin leaves room for the scrollbar.
    let content_width = (ui.available_width() - 24.0).max(200.0);

    egui::ScrollArea::vertical()
        .auto_shrink([false, false])
        .show(ui, |ui| {
            ui.collapsing("Search", |ui| {
                tag_editor(
                    ui,
                    content_width,
                    "Search terms",
                    "terms",
                    &mut app.config_draft.terms,
                );
                tag_editor(
                    ui,
                    content_width,
                    "Title include",
                    "title_inc",
                    &mut app.config_draft.title_include,
                );
                tag_editor(
                    ui,
                    content_width,
                    "Title exclude",
                    "title_exc",
                    &mut app.config_draft.title_exclude,
                );
                tag_editor(
                    ui,
                    content_width,
                    "Seniority",
                    "seniority",
                    &mut app.config_draft.seniority,
                );

                ui.label("Employment types you would take (none ticked = all):");
                ui.horizontal_wrapped(|ui| {
                    for (key, label) in crate::config::EMPLOYMENT_KINDS {
                        let mut on = app
                            .config_draft
                            .employment_types
                            .iter()
                            .any(|t| t == key);
                        if ui.checkbox(&mut on, *label).changed() {
                            if on {
                                app.config_draft.employment_types.push((*key).to_string());
                            } else {
                                app.config_draft.employment_types.retain(|t| t != key);
                            }
                        }
                    }
                })
                .response
                .on_hover_text(
                    "A posting of a type you did not tick is still shown, marked \"alt\", \
                     so you can dismiss it yourself. Nothing is hidden on this basis.",
                );

                ui.horizontal(|ui| {
                    ui.label("Salary floor (blank = none):");
                    ui.text_edit_singleline(&mut app.config_draft.salary_floor);
                });
                ui.horizontal(|ui| {
                    ui.label("Fallback floor (blank = none):");
                    ui.text_edit_singleline(&mut app.config_draft.salary_alt_floor);
                })
                .response
                .on_hover_text(
                    "Pay under the salary floor but at or above this is kept and marked \
                     \"alt\" instead of dropped - a fallback tier worth seeing in a thin \
                     market. Must be below the salary floor.",
                );
                ui.horizontal(|ui| {
                    ui.label("Currency:");
                    ui.text_edit_singleline(&mut app.config_draft.currency);
                });
                ui.separator();
                ui.separator();

                ui.label("Ways of working you would take:");
                ui.horizontal_wrapped(|ui| {
                    ui.checkbox(&mut app.config_draft.work_remote, "Remote");
                    ui.checkbox(&mut app.config_draft.work_hybrid, "Hybrid");
                    ui.checkbox(&mut app.config_draft.work_onsite, "On-site");
                })
                .response
                .on_hover_text(
                    "Tick only Remote and only remote roles are kept. Ticking all three, \
                     or none, keeps everything. A hybrid role is checked against the \
                     places below, because hybrid means going in.",
                );
                ui.add_space(6.0);

                tag_editor_with_suggestions(
                    ui,
                    content_width,
                    "Places you can work (blank = anywhere):",
                    "locations",
                    &mut app.config_draft.locations,
                );
            });

            // WHEN collection happens, not what it looks for. These sat
            // inside "Search" and nobody found them - the times were only
            // ever changed from the CLI.
            ui.collapsing("When this search runs", |ui| {
            ui.checkbox(
                &mut app.config_draft.refresh_daily,
                "Refresh this search daily",
            )
            .on_hover_text(
                "Pressing Search is always deliberate. This keeps an existing search \
                 current afterwards - twice on weekdays, once at weekends - so a list \
                 that is days stale does not cost you the roles worth applying to \
                 first. Closed for a few days? It catches up the moment you open it.",
            );
            ui.add_enabled_ui(app.config_draft.refresh_daily, |ui| {
                ui.horizontal(|ui| {
                    ui.label("Run at:");
                    ui.add(
                        egui::TextEdit::singleline(&mut app.config_draft.refresh_at)
                            .desired_width(140.0)
                            .hint_text("10:45, 16:30"),
                    )
                    .on_hover_text(
                        "Times of day on a 24-hour clock, separated by commas. The \
                         morning run catches the 8:00-10:30 batch once it has landed; \
                         the afternoon one catches roles approved during the day.",
                    );
                    ui.weak("24-hour clock, comma separated");
                });
                // The app has to be open for a scheduled run to happen, and
                // a schedule somebody cannot predict is one they stop
                // trusting - so say both rather than leaving it to be
                // discovered.
                ui.weak(
                    "Runs when the app is open. If it was closed, it catches up the \
                     next time you open it.",
                );
                ui.checkbox(
                    &mut app.config_draft.refresh_weekdays_only,
                    "Skip weekends",
                )
                .on_hover_text(
                    "Measured across 8,331 postings: 69% land Monday to Wednesday, \
                     Tuesday alone 27%, and the weekend 1.7% between them. The \
                     weekend run is one check rather than two for that reason - \
                     tick this to skip it entirely.",
                );
            });
            });

            ui.collapsing("Skills vocabulary", |ui| {
                tag_editor(ui, content_width, "Skills", "skills", &mut app.config_draft.skills);
            });

            // No "Resume path" box here any more (decided 2026-08-05).
            // Once a resume is ATTACHED, the app reads its own copy and the
            // path is ignored - so the box was a control that silently did
            // nothing, on a screen where every other control does something.
            // The key is still honoured by the engine for a profile made
            // before attaching existed, and saving preserves it; the Resumes
            // tab is the one place a resume is managed.

            ui.collapsing("Sources", |ui| {
                ui.label("ATS collectors and page-scraping fallbacks to use.");
                egui::Grid::new("sources_grid")
                    .num_columns(3)
                    .show(ui, |ui| {
                        let mut count = 0;
                        for name in KNOWN_SOURCES {
                            let enabled = app
                                .config_draft
                                .sources
                                .entry((*name).to_string())
                                .or_insert(true);
                            ui.checkbox(enabled, *name);
                            count += 1;
                            if count % 3 == 0 {
                                ui.end_row();
                            }
                        }
                    });
            });

            // Four controls used to sit here - max bytes, timeout, per-host
            // delay and a robots.txt tick box - and NONE of them did anything.
            // Every collector passes its own values, so a person could type in
            // this box, press Save, and change nothing at all. A control that
            // does nothing is worse than no control: it tells someone they
            // have addressed a concern they have not. Removed 2026-08-08.
            let focus = std::mem::take(&mut app.focus_added_links_setting);
            egui::CollapsingHeader::new("Adding jobs by link")
                .open(if focus { Some(true) } else { None })
                .show(ui, |ui| {
                    ui.checkbox(
                        &mut app.config_draft.read_added_links,
                        "Read the page when I add a job by link",
                    )
                    .on_hover_text(
                        "On, the app opens the link once and fills in the title, \
                         employer and description for you. Off, you type them. \
                         This is also what decides whether the app reads sites \
                         that ask automated tools not to - which it does only \
                         here, one page at a time, with you present.",
                    );
                });

            ui.collapsing("Job sources that need a key", |ui| {
                // Same map entry the Sources grid toggles, so the two views
                // of this setting cannot disagree - it is just reachable from
                // where the key is entered too, which is where someone goes
                // when they want to turn federal search on or off. Config is
                // per-profile, so this switches USAJOBS for the ACTIVE
                // profile only.
                // Read before the mutable borrow below, and used to say what
                // the tick actually does right now. The first user asked (2026-08-05)
                // what the tick does with no key: it is permission, not
                // capability. Collection skips USAJOBS with a note in the
                // log and every other source runs as normal - correct
                // behaviour, but invisible from this screen, which is where
                // somebody decides whether they are searching federal jobs.
                let usajobs_ready = !app.config_draft.usajobs_email.trim().is_empty()
                    && !app.config_draft.usajobs_api_key.trim().is_empty();
                let usajobs_on = app
                    .config_draft
                    .sources
                    .entry("usajobs".to_string())
                    .or_insert(true);
                let ticked = *usajobs_on;
                ui.checkbox(usajobs_on, "Search federal jobs for this profile");
                if ticked && !usajobs_ready {
                    ui.colored_label(
                        egui::Color32::from_rgb(217, 164, 65),
                        "No key entered yet, so federal jobs are skipped on every \
                         collection. Nothing else is affected.",
                    );
                }
                ui.label("USAJOBS (federal job postings) needs a free key.");
                // The ToS scopes retrieved data to the registering entity and
                // forbids sharing a key, so this must read as "register your
                // own", not "obtain a key from somewhere".
                ui.label(
                    "Register your own - USAJOBS ties the key to the person or \
                     organization that requested it, and keys may not be shared.",
                );
                ui.hyperlink_to(
                    "Get one at developer.usajobs.gov",
                    "https://developer.usajobs.gov",
                );
                ui.horizontal(|ui| {
                    ui.label("USAJOBS email (the one you registered with):");
                    ui.text_edit_singleline(&mut app.config_draft.usajobs_email);
                });
                ui.horizontal(|ui| {
                    ui.label("USAJOBS API key:");
                    ui.add(
                        egui::TextEdit::singleline(&mut app.config_draft.usajobs_api_key)
                            .password(true),
                    );
                });
                // Say which of the two storage states this machine is in
                // rather than implying a guarantee the platform may not
                // provide. See secrets.rs for what DPAPI does and does not
                // defend against.
                if crate::secrets::available() {
                    ui.label(
                        "Stored encrypted for your Windows account - the config \
                         file is unreadable from another account or machine.",
                    );
                } else {
                    ui.label(
                        "Stored as plain text: this system has no user-bound \
                         secret store available.",
                    );
                }
            });

            ui.collapsing("Agent API (optional)", |ui| {
                ui.horizontal(|ui| {
                    ui.label("Base URL:");
                    ui.text_edit_singleline(&mut app.config_draft.agent_base_url);
                });
                ui.horizontal(|ui| {
                    ui.label("API key:");
                    ui.add(
                        egui::TextEdit::singleline(&mut app.config_draft.agent_api_key)
                            .password(true),
                    );
                });
                ui.horizontal(|ui| {
                    ui.label("Model:");
                    ui.text_edit_singleline(&mut app.config_draft.agent_model);
                });
            });
        });

    ui.separator();
    ui.horizontal(|ui| {
        if ui.button("Save").clicked() {
            app.save_config();
        }
        if ui.button("Reload").clicked() {
            app.reload_config();
        }
        if let Some(status) = &app.config_status {
            ui.label(status);
        }
    });
}

/// A list edited as tags: type a value, press Enter to add it, click the x
/// on a chip to remove it. Both act on the DRAFT, so nothing is written
/// until Save - an accidental removal is undone by leaving the view.
///
/// This replaced a three-row text box holding one item per line. With 55
/// title terms that meant scrolling a tiny window to change a single entry,
/// with no way to see the list at a glance and nothing preventing a
/// duplicate.
fn tag_editor(ui: &mut egui::Ui, width: f32, label: &str, id: &str, field: &mut TagField) {
    ui.label(label);
    chips(ui, width, field);
    entry_box(ui, id, field, "type and press Enter");
    ui.add_space(6.0);
}

/// The same editor, with spellings offered from the bundled US place list.
///
/// A location typed wrong fails silently - "Knoxvile, TN" matches nothing
/// and looks exactly like a market with no jobs in it - so this is the one
/// list where guessing at the spelling has a cost the person cannot see.
/// Suggestions, not a dropdown: anything can still be typed, including
/// places and phrasings the list does not carry.
fn tag_editor_with_suggestions(
    ui: &mut egui::Ui,
    width: f32,
    label: &str,
    id: &str,
    field: &mut TagField,
) {
    ui.label(label);
    chips(ui, width, field);
    entry_box(ui, id, field, "type a city, then pick one below");

    let picks = crate::places::suggest(&field.input, 6);
    if !picks.is_empty() {
        wrapped_row(ui, width, |ui| {
            for place in picks {
                if ui.small_button(place).clicked() {
                    field.input = place.to_string();
                    field.commit();
                }
            }
        });
    }
    ui.add_space(6.0);
}

/// A row that wraps at `width` rather than at the ui's own max_rect - see
/// the note where content_width is measured.
fn wrapped_row(ui: &mut egui::Ui, width: f32, add: impl FnOnce(&mut egui::Ui)) {
    ui.allocate_ui_with_layout(
        egui::vec2(width, 0.0),
        egui::Layout::left_to_right(egui::Align::Min).with_main_wrap(true),
        add,
    );
}

fn chips(ui: &mut egui::Ui, width: f32, field: &mut TagField) {
    wrapped_row(ui, width, |ui| {
        // Collected first, then removed after the loop: mutating the vector
        // while rendering from it would shift every index after the one
        // clicked.
        let mut remove_at: Option<usize> = None;
        for (idx, item) in field.items.iter().enumerate() {
            if chip(ui, item) {
                remove_at = Some(idx);
            }
        }
        if let Some(idx) = remove_at {
            field.items.remove(idx);
        }
    });
}

/// One tag pill. Returns true when its x was clicked.
///
/// Painted rather than composed from a Frame around a label and a button,
/// because a Frame asks for the rest of the line and therefore always
/// "fits" - so a row of them NEVER wrapped, no matter what width the layout
/// was given, and a 55-term list ran off the right edge and was clipped.
/// One allocate_exact_size is a single widget the wrapping layout can
/// measure, which is what makes the row wrap at all.
fn chip(ui: &mut egui::Ui, text: &str) -> bool {
    let font = egui::TextStyle::Button.resolve(ui.style());
    let visuals = ui.visuals().clone();
    let label = ui
        .painter()
        .layout_no_wrap(text.to_owned(), font.clone(), visuals.text_color());
    let cross = ui
        .painter()
        .layout_no_wrap("x".to_owned(), font, visuals.weak_text_color());

    const PAD_X: f32 = 8.0;
    const PAD_Y: f32 = 3.0;
    const GAP: f32 = 6.0;
    let size = egui::vec2(
        PAD_X + label.size().x + GAP + cross.size().x + PAD_X,
        label.size().y + PAD_Y * 2.0,
    );
    let (rect, response) = ui.allocate_exact_size(size, egui::Sense::click());

    // Only the x removes. Clicking the pill itself does nothing, because a
    // term that vanishes on a stray click is a search quietly changed.
    let cross_pos = egui::pos2(
        rect.max.x - PAD_X - cross.size().x,
        rect.center().y - cross.size().y / 2.0,
    );
    let cross_hit = egui::Rect::from_min_size(cross_pos, cross.size()).expand(4.0);
    let over_cross = response
        .hover_pos()
        .is_some_and(|pos| cross_hit.contains(pos));

    let painter = ui.painter();
    painter.rect_filled(
        rect,
        egui::Rounding::same(10.0),
        visuals.widgets.inactive.bg_fill,
    );
    painter.galley(
        egui::pos2(rect.min.x + PAD_X, rect.center().y - label.size().y / 2.0),
        label,
        visuals.text_color(),
    );
    let cross_color = if over_cross {
        visuals.text_color()
    } else {
        visuals.weak_text_color()
    };
    painter.galley(cross_pos, cross, cross_color);
    if over_cross {
        ui.ctx().set_cursor_icon(egui::CursorIcon::PointingHand);
    }

    // A painted pill is invisible to the accessibility tree, so a search term
    // could be neither read nor removed without a mouse. The term itself is the
    // name because it IS the identity of this chip - there is one per term.
    let response = crate::access::tag(
        response,
        egui::WidgetType::Button,
        format!("term-{}", crate::access::slug(text)),
    );

    response.clicked() && over_cross
}

fn entry_box(ui: &mut egui::Ui, id: &str, field: &mut TagField, hint: &str) {
    let response = ui.add(
        egui::TextEdit::singleline(&mut field.input)
            .id_source(id)
            .desired_width(260.0)
            .hint_text(hint),
    );
    if response.lost_focus() && ui.input(|i| i.key_pressed(egui::Key::Enter)) {
        field.commit();
        // Keep focus so a list can be typed straight through without
        // reaching for the mouse between entries.
        response.request_focus();
    }
}

/// Offered after a Save that actually changed what the search looks for.
///
/// Collection is deliberate by design - the app never fetches because a
/// setting was edited. But a person who just rewrote their title terms
/// almost certainly wants to see the result, and making them find the
/// Companies tab to act on the change they just made is a step with no
/// purpose. So: an offer, with the consequence stated, and a way to decline.
pub fn show_run_prompt(app: &mut UnlatchedApp, ctx: &egui::Context) {
    if !app.offer_run_after_save {
        return;
    }
    let mut close = false;
    let mut run = false;

    egui::Window::new("Run the search?")
        .collapsible(false)
        .resizable(false)
        .anchor(egui::Align2::CENTER_CENTER, [0.0, 0.0])
        .show(ctx, |ui| {
            ui.label("Your search settings changed.");
            ui.add_space(4.0);
            ui.label(
                "Collecting fetches every employer's board again, which takes a while. \
                 Your applied and interviewed marks are never touched by it.",
            );
            ui.add_space(10.0);
            ui.horizontal(|ui| {
                if ui.button("Not now").clicked() {
                    close = true;
                }
                if ui.button("Run search").clicked() {
                    run = true;
                    close = true;
                }
            });
        });

    if run {
        app.start_process("collect", vec!["collect".to_string()]);
    }
    if close {
        app.offer_run_after_save = false;
    }
}
