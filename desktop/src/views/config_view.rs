// Config: every key documented in config.json is editable here. Lists are
// edited one item per line; Save validates the whole form at once and
// reports every problem found rather than stopping at the first one.
//
// EVERY SECTION HAS A NAME, and that is not decoration. A collapsing heading
// published nothing to the accessibility tree, so no automation could open
// one - the QC scripts reached them by clicking a measured pixel offset,
// which is a number that is right until any section above moves. It went
// wrong the first two times it was tried against this screen on 2026-09-02,
// silently photographing a collapsed section as though it were open.
//
// That is also how the weekend-run time and the two search filters came to
// ship with no picture of them: there was no way to ask for the section they
// live in. The names below are an API in the same sense the nav rail's are -
// fixed, lowercase-hyphenated, and never the visible text.

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
            let search = ui.collapsing("Search", |ui| {
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
                        let name = format!("config-employment-{key}");
                        if crate::access::tick(ui, &mut on, label, &name).changed() {
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
                    crate::access::text_field(
                        ui,
                        &mut app.config_draft.salary_floor,
                        "config-salary-floor",
                    );
                });
                ui.horizontal(|ui| {
                    ui.label("Fallback floor (blank = none):");
                    crate::access::text_field(
                        ui,
                        &mut app.config_draft.salary_alt_floor,
                        "config-fallback-floor",
                    );
                })
                .response
                .on_hover_text(
                    "Pay under the salary floor but at or above this is kept and marked \
                     \"alt\" instead of dropped - a fallback tier worth seeing in a thin \
                     market. Must be below the salary floor.",
                );
                ui.horizontal(|ui| {
                    ui.label("Currency:");
                    crate::access::text_field(ui, &mut app.config_draft.currency, "config-currency");
                });
                ui.separator();

                ui.label("Ways of working you would take:");
                ui.horizontal_wrapped(|ui| {
                    crate::access::tick(ui, &mut app.config_draft.work_remote, "Remote",
                        "config-work-remote");
                    crate::access::tick(ui, &mut app.config_draft.work_hybrid, "Hybrid",
                        "config-work-hybrid");
                    crate::access::tick(ui, &mut app.config_draft.work_onsite, "On-site",
                        "config-work-onsite");
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

                // BOTH OF THESE WERE ENGINE-ONLY. The screening code has acted
                // on them all along and this page modelled neither, so the only
                // way to change either was to edit config.json by hand - and
                // us_only defaults ON, which means a filter was running that
                // nothing on screen mentioned.
                //
                // NAMED, so a check can assert they are on screen rather than
                // only photograph the place they should be. These two were
                // engine-only for long enough that nothing noticed; a picture
                // proves what it caught, a name proves they are still there.
                ui.add_space(6.0);
                crate::access::tag(
                    ui.checkbox(
                        &mut app.config_draft.us_only,
                        "Only jobs I can work from the United States",
                    ),
                    egui::WidgetType::Checkbox,
                    "us-only",
                )
                .on_hover_text(
                    "Remote says nothing about jurisdiction: \"Remote - India\" is a \
                     remote job in India, and it passed a remote-only search \
                     untouched before this existed - 41 of 133 matches in one real \
                     search were foreign. Untick it for a search that spans \
                     countries.",
                );
                crate::access::tag(
                    ui.checkbox(
                        &mut app.config_draft.travel_ok,
                        "Include employers who send people out to job sites",
                    ),
                    egui::WidgetType::Checkbox,
                    "travel-ok",
                )
                .on_hover_text(
                    "For an employer based in one of the places above whose work \
                     happens at customer sites. Only consulted when a posting's own \
                     location is unclear, so it widens what counts as commutable \
                     rather than adding postings from anywhere.",
                );
            });
            crate::access::tag(search.header_response,
                egui::WidgetType::Button, "config-section-search");

            // WHEN collection happens, not what it looks for. These sat
            // inside "Search" and nobody found them - the times were only
            // ever changed from the CLI.
            let when_this_search_runs = ui.collapsing("When this search runs", |ui| {
            crate::access::tick(
                ui,
                &mut app.config_draft.refresh_daily,
                "Refresh this search daily",
                "config-refresh-daily",
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
                            .hint_text("11:00, 16:30"),
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
                crate::access::tick(
                    ui,
                    &mut app.config_draft.refresh_weekdays_only,
                    "Skip weekends",
                    "config-skip-weekends",
                )
                .on_hover_text(
                    "Measured across 8,331 postings: 69% land Monday to Wednesday, \
                     Tuesday alone 27%, and the weekend 1.7% between them. The \
                     weekend run is one check rather than two for that reason - \
                     tick this to skip it entirely.",
                );
                // DISABLED WHEN WEEKENDS ARE SKIPPED. A time for a run that
                // will not happen is a control that does nothing, which is
                // the failure this whole field exists to correct.
                ui.add_enabled_ui(!app.config_draft.refresh_weekdays_only, |ui| {
                    ui.horizontal(|ui| {
                        ui.label("Weekend run at:");
                        ui.add(
                            egui::TextEdit::singleline(
                                &mut app.config_draft.refresh_weekend_at,
                            )
                            .desired_width(140.0)
                            .hint_text("11:30"),
                        )
                        .on_hover_text(
                            "Saturday and Sunday get one run rather than two, \
                             later in the day - weekend postings are not staged \
                             to a business-hours release, so there is no morning \
                             batch to wait for.",
                        );
                    });
                });
            });
            });
            crate::access::tag(when_this_search_runs.header_response,
                egui::WidgetType::Button, "config-section-when-this-search-runs");

            let skills_vocabulary = ui.collapsing("Skills vocabulary", |ui| {
                tag_editor(ui, content_width, "Skills", "skills", &mut app.config_draft.skills);
            });
            crate::access::tag(skills_vocabulary.header_response,
                egui::WidgetType::Button, "config-section-skills-vocabulary");

            // No "Resume path" box here any more (decided 2026-08-05).
            // Once a resume is ATTACHED, the app reads its own copy and the
            // path is ignored - so the box was a control that silently did
            // nothing, on a screen where every other control does something.
            // The key is still honoured by the engine for a profile made
            // before attaching existed, and saving preserves it; the Resumes
            // tab is the one place a resume is managed.

            let sources = ui.collapsing("Sources", |ui| {
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
                            crate::access::tick(ui, enabled, name, &format!("config-source-{name}"));
                            count += 1;
                            if count % 3 == 0 {
                                ui.end_row();
                            }
                        }
                    });
            });
            crate::access::tag(sources.header_response,
                egui::WidgetType::Button, "config-section-sources");

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
                    crate::access::tick(
                        ui,
                        &mut app.config_draft.read_added_links,
                        "Read the page when I add a job by link",
                        "config-read-added-links",
                    )
                    .on_hover_text(
                        "On, the app opens the link once and fills in the title, \
                         employer and description for you. Off, you type them. \
                         This is also what decides whether the app reads sites \
                         that ask automated tools not to - which it does only \
                         here, one page at a time, with you present.",
                    );
                });

            let job_sources_that_need_a_key = ui.collapsing("Job sources that need a key", |ui| {
                // Same map entry the Sources grid toggles, so the two views
                // of this setting cannot disagree - it is just reachable from
                // where the key is entered too, which is where someone goes
                // when they want to turn federal search on or off. Config is
                // per-profile, so this switches USAJOBS for the ACTIVE
                // profile only.
                // Read before the mutable borrow below, and used to say what
                // the tick actually does right now (2026-08-05). With no key
                // the tick is permission, not capability. Collection skips
                // USAJOBS with a note in the log and every other source runs
                // as normal - correct behaviour, but invisible from this
                // screen, which is where somebody decides whether they are
                // searching federal jobs.
                let usajobs_ready = !app.config_draft.usajobs_email.trim().is_empty()
                    && !app.config_draft.usajobs_api_key.trim().is_empty();
                let usajobs_on = app
                    .config_draft
                    .sources
                    .entry("usajobs".to_string())
                    .or_insert(true);
                let ticked = *usajobs_on;
                crate::access::tick(
                    ui,
                    usajobs_on,
                    "Search federal jobs for this profile",
                    "config-usajobs-on",
                );
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
                crate::browse::link(
                    ui,
                    "Get one at developer.usajobs.gov",
                    "https://developer.usajobs.gov",
                );
                ui.horizontal(|ui| {
                    ui.label("USAJOBS email (the one you registered with):");
                    crate::access::text_field(
                        ui,
                        &mut app.config_draft.usajobs_email,
                        "config-usajobs-email",
                    );
                });
                ui.horizontal(|ui| {
                    ui.label("USAJOBS API key:");
                    let key_box = ui.add(
                        egui::TextEdit::singleline(&mut app.config_draft.usajobs_api_key)
                            .password(true),
                    );
                    crate::access::tag(key_box, egui::WidgetType::TextEdit, "config-usajobs-key");
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
            crate::access::tag(job_sources_that_need_a_key.header_response,
                egui::WidgetType::Button, "config-section-job-sources-that-need-a-key");

            let collectors = ui.collapsing("Collectors", |ui| {
                ui.label(
                    "Other programs that hand jobs over in a file. Unlatched reads \
                     the file; it never writes one.",
                );
                // ADDED AT ALL because there was no way to add a collector
                // except by editing config.json in a text editor. Verified
                // before writing this: the Sources block above holds tick
                // boxes for the built-in collectors and nothing else on this
                // screen wrote a `collectors` entry, and the Config struct had
                // no field for one to write into.
                let mut remove: Option<usize> = None;
                for (index, entry) in app.config_draft.collectors.iter_mut().enumerate() {
                    ui.separator();
                    ui.horizontal(|ui| {
                        let tick = ui.checkbox(&mut entry.enabled, "Use this one");
                        crate::access::tag_with_value(
                            tick,
                            egui::WidgetType::Checkbox,
                            format!("collector-enabled-{index}"),
                            if entry.enabled { "true" } else { "false" },
                        );
                        // Remove is deliberately not a confirm: what it
                        // discards is four fields on a screen that has not
                        // saved yet, and Reload puts them all back.
                        if crate::access::tag(
                            ui.button("Remove"),
                            egui::WidgetType::Button,
                            format!("collector-remove-{index}"),
                        )
                        .clicked()
                        {
                            remove = Some(index);
                        }
                    });
                    ui.horizontal(|ui| {
                        ui.label("Name:");
                        let field = ui.text_edit_singleline(&mut entry.id);
                        crate::access::tag_with_value(
                            field,
                            egui::WidgetType::TextEdit,
                            format!("collector-id-{index}"),
                            &entry.id,
                        );
                        ui.label("a-z, 0-9, _ or -");
                    });
                    ui.horizontal(|ui| {
                        ui.label("Shown as:");
                        let field = ui.text_edit_singleline(&mut entry.label);
                        crate::access::tag_with_value(
                            field,
                            egui::WidgetType::TextEdit,
                            format!("collector-label-{index}"),
                            &entry.label,
                        );
                        ui.label("optional");
                    });
                    ui.horizontal(|ui| {
                        ui.label("File:");
                        let field = ui.add(
                            egui::TextEdit::singleline(&mut entry.path)
                                .desired_width(content_width * 0.6),
                        );
                        crate::access::tag_with_value(
                            field,
                            egui::WidgetType::TextEdit,
                            format!("collector-path-{index}"),
                            &entry.path,
                        );
                    });
                    if !entry.rest.is_empty() {
                        // SAID OUT LOUD rather than hidden. This screen edits
                        // four fields; verified in the engine's
                        // collectors.DEFAULTS, an entry may carry three more -
                        // schedule, we_may_refetch, pushes_closures. Somebody
                        // who cannot see them here has no way to tell a save
                        // kept them.
                        let mut kept: Vec<&str> =
                            entry.rest.keys().map(String::as_str).collect();
                        kept.sort_unstable();
                        ui.label(format!(
                            "Also set in config.json, kept as it is: {}",
                            kept.join(", ")
                        ));
                    }
                }
                if let Some(index) = remove {
                    app.config_draft.collectors.remove(index);
                }
                ui.separator();
                if crate::access::tag(
                    ui.button("Add a collector"),
                    egui::WidgetType::Button,
                    "collector-add",
                )
                .clicked()
                {
                    // enabled: true, matching the engine's own default for an
                    // entry with no `enabled` key - verified in
                    // collectors.DEFAULTS. Adding a row that arrived switched
                    // off would also mean the same entry behaved differently
                    // depending on whether it was typed here or into the file.
                    app.config_draft.collectors.push(crate::config::CollectorEntry {
                        enabled: true,
                        ..Default::default()
                    });
                }
                if app.config_draft.collectors.is_empty() {
                    ui.label("None configured.");
                }
            });
            crate::access::tag(collectors.header_response,
                egui::WidgetType::Button, "config-section-collectors");

            let agent_api_optional = ui.collapsing("Agent API (optional)", |ui| {
                ui.horizontal(|ui| {
                    ui.label("Base URL:");
                    crate::access::text_field(
                        ui,
                        &mut app.config_draft.agent_base_url,
                        "config-agent-base-url",
                    );
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
                    crate::access::text_field(
                        ui,
                        &mut app.config_draft.agent_model,
                        "config-agent-model",
                    );
                });
            });
            crate::access::tag(agent_api_optional.header_response,
                egui::WidgetType::Button, "config-section-agent-api-optional");
        });

    ui.separator();
    ui.horizontal(|ui| {
        if crate::access::tag(ui.button("Save"), egui::WidgetType::Button, "config-save").clicked() {
            app.save_config();
        }
        if crate::access::tag(ui.button("Reload"), egui::WidgetType::Button, "config-reload").clicked()
        {
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
/// A location typed wrong fails silently - "Sacremento, CA" matches nothing
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
                if crate::access::tag(
                    ui.small_button(place),
                    egui::WidgetType::Button,
                    format!("place-suggestion-{}", crate::access::slug(place)),
                )
                .clicked()
                {
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
                if crate::access::tag(
                    ui.button("Not now"),
                    egui::WidgetType::Button,
                    "config-saved-not-now",
                )
                .clicked()
                {
                    close = true;
                }
                if crate::access::tag(
                    ui.button("Run search"),
                    egui::WidgetType::Button,
                    "config-saved-run-search",
                )
                .clicked()
                {
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
