// Triage: the qualified-jobs queue. Fast keyboard-driven review is the
// point, so the table stays a single self-scrolling widget (never a plain
// Grid inside a ScrollArea) and row identity always travels as jobs.key,
// never as a position in the currently displayed list.

use eframe::egui::{self, Key};
use egui_extras::{Column, TableBuilder};

use crate::app::{ExpandedTab, ListScope, SortBy, UnlatchedApp};
use crate::fmt;
use crate::views::columns;

/// How much of the pane the job list may take.
///
/// Was two thirds, to leave room for the description panel pinned at the
/// bottom. That panel is gone - postings now open inside the list - so the
/// list gets the whole pane, and the third that used to sit empty below it is
/// list again.
const TABLE_SHARE_OF_HEIGHT: f32 = 1.0;

// The status vocabulary lives in crate::status. It used to be a table here,
// which is why five other files each grew their own copy of it.
//
// "No longer open" is NOT in the dropdown any more. It was a status somebody
// had to set by hand for a fact the app already knows: a board taking a
// posting down sets jobs.delisted_at, and the row now says so BESIDE whatever
// the person marked rather than instead of it. Storing it as a status made
// "taken down" and "applied" mutually exclusive, when the row that matters
// most is precisely the one that is both.
use crate::status::NOT_SET;

/// Height the open row gains for its description. Fixed rather than measured:
/// egui_extras needs every row height BEFORE any row is drawn, so a height
/// derived from laid-out text is not available in time.
///
/// Sized to show a real chunk of a posting rather than a peephole - most of
/// what decides whether a job is worth applying to sits in the first screen of
/// text, and scrolling a 60-line window three lines at a time is what made the
/// old bottom panel useless. Anything longer still scrolls within the block,
/// and several list rows stay visible underneath so the list is never lost.
const EXPANDED_EXTRA: f32 = 460.0;

/// A thin band so the table never sits flush against the window frame.
///
/// The 30px strip that used to sit beside this reserved room for a
/// column-settings gear. That control is in the toolbar now, and a margin
/// nothing uses only teaches the next reader that the table is meant to stop
/// short of the edge.
const EDGE_BAND: f32 = 10.0;

/// All jobs: everything still worth seeing, taken-down postings included.
///
/// THE PARAGRAPH THAT USED TO OPEN THIS DESCRIBED A RULE THAT IS GONE. It said
/// All jobs "shows everything that has NOT been taken down - and keeps a
/// taken-down posting visible anyway if it was applied to", which was true
/// until a closure started writing a status: a posting nobody had acted on
/// then left Triage as settled AND left All jobs at the same instant, landing
/// on no screen at all. db::list_jobs_for records that change and
/// list_all_jobs no longer filters on delisted_at at all - so the paragraph
/// was documenting the defect rather than the design.
pub fn show_all_jobs(app: &mut UnlatchedApp, ui: &mut egui::Ui, ctx: &egui::Context) {
    // THE RAIL ENTRY OWNS THE MODE NOW. Removed used to be a toggle on this
    // screen and nothing else - so a person with 29 removed jobs had no way to
    // find them without noticing a chip in the toolbar of a screen they might
    // never open. It is its own entry; arriving at All jobs means All jobs.
    app.show_retired = false;
    show_scope(app, ui, ctx);
}

/// What the person took out of their lists. Reversible, and nothing was deleted.
pub fn show_removed(app: &mut UnlatchedApp, ui: &mut egui::Ui, ctx: &egui::Context) {
    app.show_retired = true;
    // Two scopes of one table, so entering either leaves the other.
    app.show_duplicates = false;
    show_scope(app, ui, ctx);
}

/// Results for what is typed in the rail.
///
/// Renders through the same show_list as every other list, so a result carries
/// the identical columns, actions and bulk operations. A search whose rows you
/// cannot act on is a lookup, not a list - and the point of finding a posting
/// is usually to do something to it.
pub fn show_search(app: &mut UnlatchedApp, ui: &mut egui::Ui, ctx: &egui::Context) {
    // The scope is normally set by run_search. Re-asserting it here keeps any
    // other route in - a restored session, a stray view change - honest about
    // which query produced the rows on screen.
    if app.list_scope != ListScope::Search {
        app.list_scope = ListScope::Search;
        app.refresh_triage();
    }
    let heading = if app.search_terms.is_empty() {
        "Search".to_string()
    } else {
        format!("Search: {}", app.search_terms.join(" "))
    };
    show_list(app, ui, ctx, &heading);
}

fn show_scope(app: &mut UnlatchedApp, ui: &mut egui::Ui, ctx: &egui::Context) {
    let wanted = if app.show_retired {
        ListScope::Retired
    } else if app.show_duplicates {
        ListScope::Duplicates
    } else {
        ListScope::All
    };
    if app.list_scope != wanted {
        app.list_scope = wanted;
        app.refresh_triage();
    }
    let heading = if app.show_retired {
        "Removed"
    } else if app.show_duplicates {
        "Grouped as duplicates"
    } else {
        "All jobs"
    };
    show_list(app, ui, ctx, heading);
}

pub fn show(app: &mut UnlatchedApp, ui: &mut egui::Ui, ctx: &egui::Context) {
    // A MODULE IS SHOWN ON THIS SCREEN AND MUST SURVIVE THE VISIT. Forcing the
    // scope back to Triage here is what made the old dashboard tiles feel
    // broken: the tile set a filter, the view reset it on the way in, and the
    // person landed on the unfiltered queue wondering what they had clicked.
    let heading = match app.list_scope {
        ListScope::Module(module) => module.heading(),
        _ => {
            app.leave_module();
            "Triage".to_string()
        }
    };
    show_list(app, ui, ctx, &heading);
}

fn show_list(
    app: &mut UnlatchedApp,
    ui: &mut egui::Ui,
    ctx: &egui::Context,
    heading: &str,
) {
    // THE OPENED ROW PAYS FOR ITS OWN TEXT. Lists carry a 400-character
    // preview (see SELECT_TRIAGE_COLUMNS); the full description is fetched
    // once, here, for whichever row is open. Before the table, because the row
    // closures borrow app.triage_rows immutably for the whole draw.
    if let Some(key) = app.triage_expanded.clone() {
        app.ensure_description(&key);
    }

    // Hand-added links are re-read only when a person asks. The button is
    // disabled until each is due (once a day), so pressing it five times in
    // an afternoon cannot turn into five requests for the same page
    // (2026-08-06). Read once here: the toolbar needs it, and so does the
    // notice below it.
    //
    // Cached, not queried here: this used to be three COUNT(*) statements per
    // frame (see UnlatchedApp::manual_links).
    let links = app.manual_links;

    // When the boards were last read. The dashboard has said this since it
    // existed; the LIST - the one screen where a person is deciding whether to
    // apply to something - said nothing at all (decided 2026-08-07). Read here so
    // the toolbar can put it beside the buttons that change it.
    // THE BOARDS, not the newest row of any kind. This used to be
    // MAX(fetched_at) over everything, so a board sweep that ran on app start
    // reported "Collected today" on behalf of an external collector that had
    // not run - and would go on doing so every morning while it stayed dead.
    let external_ids = app.handoffs.configured_ids();
    let last_collected: Option<String> =
        crate::db::boards_last_collected(&app.conn, &external_ids).unwrap_or(None);

    let pending = app.collectors_pending();

    ui.horizontal(|ui| {
        // THE HEADING CARRIES THE ROW COUNT as its accessible value. The list
        // and the dashboard card that opens it are built from one WHERE
        // clause, and this is what lets that be PROVEN through the running app
        // rather than only in a unit test: the harness reads what the card
        // claims, clicks it, and reads what the list actually holds. Two
        // different code paths - count_module and list_module_jobs - so the
        // comparison can genuinely fail.
        crate::access::tag_with_value(
            ui.heading(heading),
            egui::WidgetType::Label,
            "list-heading",
            app.triage_rows.len().to_string(),
        );
        ui.add_space(16.0);
        if ui
            // Named by what the rows HAVE IN COMMON rather than by listing
            // them: the list was "pass / denied / closed" and went stale the
            // moment the vocabulary grew past three.
            .checkbox(&mut app.triage_show_all, "show finished jobs")
            .on_hover_text(
                "Jobs you have closed the loop on - passed over, turned down, \
                 hired, or an offer that ended either way.",
            )
            .changed()
        {
            app.refresh_triage();
        }
        if ui
            .checkbox(&mut app.triage_every_location, "every location")
            .on_hover_text(
                "One opening advertised in several cities is folded into a single \
                 row, with the other places on hover. Tick this to see each city \
                 as its own row.",
            )
            .changed()
        {
            app.refresh_triage();
        }
        ui.add_space(12.0);

        // ADDING comes first, then the two that REFRESH, then what they are
        // refreshing FROM (decided 2026-08-07). The freshness text reads as a
        // consequence of the buttons immediately to its left rather than as a
        // stray line somewhere else on the screen.
        //
        // Every other route into this list starts with a board we can read.
        // This one starts with a person who has already found the job and is
        // about to apply to it (decided 2026-08-06).
        if ui
            .button("Add a job by link")
            .on_hover_text(
                "Paste the link to a posting you found yourself. It joins this \
                 list and takes a status like any other job.",
            )
            .clicked()
        {
            app.show_add_job_modal = true;
            app.add_job_draft = Default::default();
        }
        if ui
            .button("Refresh")
            .on_hover_text(
                "Re-reads this list from what has already been collected. It does \
                 not go out to the boards - the daily collection does that.",
            )
            .clicked()
        {
            app.refresh_triage();
        }
        if links.any() {
            let busy = app.running_process.is_some();
            // ENABLED WHILE A COLLECT RUNS, because this queues now
            // rather than being refused. Greying it out decided FOR
            // somebody that their request had to wait, without saying so;
            // letting them press it and reporting where it sits is the
            // same wait with the person in charge of it.
            let ready = links.due > 0;
            let response = ui
                .add_enabled(ready, egui::Button::new(format!("Check added links ({})", links.total)));
            // THE REASON HAS TO BE THE REAL ONE. This said "Nothing is due
            // yet" whenever it was greyed, including while a collect was
            // running - so a person whose links WERE due was told the
            // opposite, and the only true explanation was on another screen.
            if response
                .on_hover_text(if !ready {
                    "Each added link is checked at most once a day. Nothing is due yet."
                } else if busy {
                    "Re-reads the links you added by hand. A run is in \
                     progress, so this starts when it finishes."
                } else {
                    "Re-reads the links you added by hand: still live, or taken down?"
                })
                .clicked()
            {
                app.queue_process("check added links", vec!["recheck".to_string()]);
            }
        }

        // Only on the All jobs screen, and only once there is something to go
        // back to. An empty "Removed" button on a fresh install is a promise
        // of a feature nobody has used yet.
        if app.list_scope != ListScope::Triage {
            let removed = app.retired_count;
            if removed > 0 || app.show_retired {
                let mut showing = app.show_retired;
                // The visible text carries a COUNT, so it is a different string
                // every time somebody removes a job - unusable as an identifier.
                let response = crate::access::tag_with_value(
                    ui.toggle_value(&mut showing, format!("Removed ({removed})"))
                        .on_hover_text(
                            "Jobs you took out of your lists. Nothing was deleted - \
                             tick any of them and press Put back.",
                        ),
                    egui::WidgetType::Button,
                    "show-removed",
                    format!("{removed} removed"),
                );
                if response.changed() {
                    // SWITCHES THE VIEW rather than setting the flag, because
                    // the flag is now decided by whichever rail entry is open.
                    // Kept as a chip as well as a rail entry: it carries the
                    // count, which is what tells somebody there is anything in
                    // there to go back to.
                    app.view = if showing {
                        crate::app::View::Removed
                    } else {
                        crate::app::View::AllJobs
                    };
                    app.show_duplicates = false;
                    app.selected_keys.clear();
                }
            }

            // Grouping is a JUDGEMENT the app made on the person's behalf, and
            // the only one of its kind - everything else here is either their
            // decision or a fact from the employer. So it has to be inspectable
            // and undoable, or it is a merge that quietly disappeared a job.
            let grouped = app.duplicate_count;
            if grouped > 0 || app.show_duplicates {
                let mut showing = app.show_duplicates;
                let response = crate::access::tag_with_value(
                    ui.toggle_value(&mut showing, format!("Grouped ({grouped})"))
                        .on_hover_text(
                            "Postings folded behind another as the same job. Nothing \
                             was deleted - tick any of them and press Ungroup to see \
                             them separately again.",
                        ),
                    egui::WidgetType::Button,
                    "show-grouped",
                    format!("{grouped} grouped"),
                );
                if response.changed() {
                    app.show_duplicates = showing;
                    // Grouped is a mode of All jobs, so it leaves Removed the
                    // same way Removed leaves it.
                    app.view = crate::app::View::AllJobs;
                    app.selected_keys.clear();
                }
            }
        }

        // COLUMNS, here rather than floating beside the table. It used to be a
        // gear positioned by absolute rectangle into a margin the table was
        // meant to leave free - which held only until the columns outgrew the
        // window, at which point it was drawn on top of a heading.
        ui.add_space(10.0);
        if crate::access::tag(
            ui.button("\u{2699} Columns"),
            egui::WidgetType::Button,
            "column-settings",
        )
        .on_hover_text("What is shown, and in what order")
        .clicked()
        {
            app.show_column_settings = !app.show_column_settings;
        }

        ui.add_space(12.0);
        ui.weak(fmt::collected_line(last_collected.as_deref()));

        // Whether today's handover has arrived, named here because the
        // dashboard's source panel is not where a person spends the morning.
        // Grey while it is merely not due yet, amber once it is overdue -
        // see fmt::collectors_line.
        let (clause, overdue) = fmt::collectors_line(&pending);
        if !clause.is_empty() {
            ui.add_space(8.0);
            let colour = if overdue {
                egui::Color32::from_rgb(217, 164, 65)
            } else {
                ui.visuals().weak_text_color()
            };
            ui.colored_label(colour, clause).on_hover_text(
                "The jobs it already brought are still here - this is about \
                 the handover, not the rows.",
            );
        }

        // STANDING, not a one-time notice: while this is off, a link added by
        // hand fills in nothing, and somebody who does not know that concludes
        // the feature is broken rather than switched off (decided 2026-08-08).
        // It ships off, so this is the state a new install is in.
        if !app.config.fetch.read_added_links {
            ui.add_space(8.0);
            // Says what the PERSON will experience, not what the app declines to
            // do internally. "Added links are not read" described the mechanism
            // and left the consequence - you type the details yourself - to be
            // inferred (decided 2026-08-08).
            ui.colored_label(
                egui::Color32::from_rgb(217, 164, 65),
                "Added links: you fill in the details",
            );
            // A hover cannot be found by somebody who does not already suspect
            // there is a setting, so the pointer is a control rather than a
            // tooltip: it opens Config with the right section already expanded.
            if ui
                .link("Change in Config")
                .on_hover_text(
                    "Lets the app open the link once and fill in the title, \
                     employer and description for you.",
                )
                .clicked()
            {
                app.view = crate::app::View::Config;
                app.focus_added_links_setting = true;
            }
        }
        // The collection has moved on without these. On the same line as the
        // freshness text, because that line is what a person reads to decide
        // whether what is in front of them is current - and for hand-added
        // links it would otherwise be answering for the boards only.
        if links.stale_since_collect {
            ui.add_space(8.0);
            ui.colored_label(
                egui::Color32::from_rgb(217, 164, 65),
                format!(
                    "{} added link{} not re-checked since",
                    links.total,
                    if links.total == 1 { "" } else { "s" },
                ),
            )
            .on_hover_text(if links.due > 0 {
                "Press Check added links. A posting can be taken down between \
                 collections, and nothing re-reads a link you added by hand \
                 unless you ask."
            } else {
                "Each added link is checked at most once a day, so none are due \
                 yet."
            });
        }
    });

    // A MODULE LIST SAYS WHAT IT IS, and offers the way back to the whole
    // queue. It is no longer a filter hiding rows from Triage - it is its own
    // list - so the wording is what it CONTAINS rather than what it excludes,
    // and the way out returns to the working queue rather than "clearing"
    // something the reader never set.
    if let ListScope::Module(module) = app.list_scope {
        ui.horizontal(|ui| {
            let [r, g, b] = module.colour();
            ui.colored_label(egui::Color32::from_rgb(r, g, b), module.caption());
            if crate::access::tag(
                ui.button("Back to Triage"),
                egui::WidgetType::Button,
                "module-back",
            )
            .clicked()
            {
                app.leave_module();
            }
        });
    }
    // INSTEAD of the keyboard hint, not above it. Adding a row here pushed the
    // whole table down the moment somebody ticked a box, so their second click
    // landed one row lower than the one they were aiming at - which on a
    // multi-select is the failure that matters. Swapping keeps the height
    // identical, and the hint is the right thing to give up: it describes
    // per-row keys, and the person has just started working on a set.
    if app.selected_keys.is_empty() {
        ui.label(
            "keys: up / down move, o open, a applied, p pass, d no offer, \
             i interviewed, x taken down, n note. Each status key opens the \
             note prompt - \
             Enter saves, Esc cancels.",
        );
    } else {
        bulk_bar(app, ui);
    }
    if let Some(msg) = &app.triage_message {
        ui.colored_label(egui::Color32::LIGHT_BLUE, msg);
    }
    ui.separator();

    handle_keyboard(app, ctx);

    if app.triage_note_open {
        show_note_editor(app, ui);
        ui.separator();
    }

    let row_height = 24.0;

    let selected_key = app.triage_selected.clone();
    let mut click_target: Option<String> = None;
    // Applied after the table is drawn: the row closures hold an immutable
    // borrow of app.triage_rows, so writing a status from inside one would
    // not compile - and refreshing mid-draw would renumber the rows being
    // iterated.
    let mut actions = RowActions::default();
    // Same deferred-apply reason as the others: the header closures borrow app.
    let mut sort_click: Option<SortBy> = None;
    let mut select_all: Option<bool> = None;
    let mut expand_target: Option<String> = None;
    let mut close_requested = false;
    let mut tab_switch: Option<ExpandedTab> = None;
    let mut file_action: Option<crate::views::attachments::Action> = None;
    let mut open_earlier: Option<String> = None;
    let app_tab = app.expanded_tab;
    // Read once per frame, not per row: every row compares against it. Same
    // value the freshness line above reads.
    let latest_collect = last_collected;
    // Suppressed when EVERY row qualifies. A profile that has only ever been
    // collected once has nothing but new rows, and a badge on all of them is
    // decoration - "new" only means anything against something older.
    let new_day = latest_collect.as_deref().map(|s| &s[..s.len().min(10)]);
    let all_new = new_day.is_some_and(|day| {
        app.triage_rows.iter().all(|r| {
            r.job
                .fetched_at
                .as_deref()
                .is_some_and(|f| &f[..f.len().min(10)] >= day)
        })
    });
    let latest_collect = if all_new { None } else { latest_collect };

    // Bound the table instead of letting it claim every remaining pixel.
    // Unbounded, it drew its column dividers down through empty space below
    // the last job, which reads as a run of blank rows. Capping it also
    // leaves room for the description panel underneath - the space the empty
    // grid was occupying is now the posting you are reading.
    // Captured before the table takes the space, so the open block can be
    // drawn at full width from inside a clipped column cell.
    let table_width = ui.available_width();
    let content_height = row_height * (app.triage_rows.len() as f32) + row_height;
    let available = ui.available_height();
    let table_height = content_height.min(available * TABLE_SHARE_OF_HEIGHT);

    // Which columns are drawn, in the person's order. Every one of the three
    // blocks below - widths, headings, cells - now walks this same list, so
    // they cannot fall out of step with each other the way three
    // hand-maintained sequences did.
    let shown = columns::visible(&app.column_order, &app.column_hidden);
    let flex_at = columns::flex_index(&shown);
    // NO STRIP RESERVED ANY MORE - the gear moved to the toolbar. Reserving a
    // margin only worked while the table stayed inside it, and a table with
    // fixed column widths does not: it overran the strip and drew under the
    // gear.
    let columns_width = (table_width - EDGE_BAND).max(600.0);
    // What the columns actually want. Summed from the same specs the builder
    // uses, so it cannot drift from what is drawn.
    let natural_width: f32 = shown
        .iter()
        .map(|id| columns::spec(*id).width)
        .sum::<f32>()
        .max(columns_width);

    // The table used to stop wherever its fixed column widths happened to
    // add up, leaving a dead strip at the right edge while Title, Company
    // and Location all truncated mid-word (decided 2026-08-07). It now runs to
    // the edge, minus room for the column-settings gear and a thin band so
    // nothing sits flush against the window frame.
    {
        // SIDEWAYS WHEN IT HAS TO BE. Sized to natural_width, which is the
        // window width whenever everything fits - so the flexible column still
        // reaches the edge and no scrollbar appears, exactly as before. It is
        // only wider when the columns genuinely are, and then they can be
        // scrolled to rather than truncated out of reach.
        egui::ScrollArea::horizontal()
            .auto_shrink([false, false])
            .show(ui, |ui| {
        ui.set_min_width(natural_width);
        ui.set_max_width(natural_width);

    let mut builder = TableBuilder::new(ui)
        .max_scroll_height(table_height)
        .striped(true)
        .resizable(true)
        .sense(egui::Sense::click())
        // TOP-aligned, not centred. An open row is ~260px tall and centring
        // put its job line in the middle of that space, with a large empty gap
        // above it and the detail crammed below. Ordinary rows are exactly one
        // row high, so top and centre are identical for them.
        .cell_layout(egui::Layout::left_to_right(egui::Align::Min))
        .min_scrolled_height(200.0);
    // Asked for by key, resolved to an index here, because the caller that
    // wants the row on screen knows the job and not its position - and the
    // position depends on the sort, the scope and the filters, none of which
    // are that caller's business.
    //
    // A key that is not in THIS list scrolls nowhere rather than to row 0.
    if app.scroll_to_selected {
        if let Some(index) = app.triage_selected.as_ref().and_then(|key| {
            app.triage_rows.iter().position(|r| &r.job.key == key)
        }) {
            builder = builder.scroll_to_row(index, Some(egui::Align::Center));
        }
        app.scroll_to_selected = false;
    }
    for (i, id) in shown.iter().enumerate() {
        let spec = columns::spec(*id);
        // One column stretches so the table reaches the window edge; the rest
        // keep the widths they were measured at.
        let column = if i == flex_at {
            Column::remainder().at_least(spec.min)
        } else {
            Column::initial(spec.width).at_least(spec.min)
        };
        builder = builder.column(if spec.clip { column.clip(true) } else { column });
    }
    builder
        .header(22.0, |mut header| {
            for id in &shown {
                let spec = columns::spec(*id);
                let (_, cell) = header.col(|ui| {
                    // The tick-box column's heading selects or clears every
                    // row currently listed - which is what "all" has to mean
                    // here, since a filter or a search is exactly when
                    // somebody wants to act on a set in bulk.
                    if spec.id == columns::ColumnId::Select {
                        let mut all = !app.triage_rows.is_empty()
                            && app.triage_rows.iter().all(|r| {
                                app.selected_keys.contains(&r.job.key)
                            });
                        // An unlabelled checkbox has nothing for the tree to
                        // report, so it was invisible to a screen reader and
                        // unaddressable by automation.
                        let response = crate::access::tag(
                            ui.checkbox(&mut all, "")
                                .on_hover_text("Select everything in this list"),
                            egui::WidgetType::Checkbox,
                            "select-all",
                        );
                        if response.changed() {
                            select_all = Some(all);
                        }
                        return;
                    }
                    // Clicking a heading sorts, as it always has. Reordering
                    // is done from the gear instead of by dragging a heading,
                    // so neither gesture can be mistaken for the other.
                    let response = match spec.sort {
                        Some(column) => sort_heading(ui, app, column, spec.heading),
                        None => ui.strong(spec.heading),
                    };
                    // The heading itself senses clicks too - see below for why
                    // both. Set here rather than returned so the borrow of
                    // `ui` ends with the cell.
                    if let Some(column) = spec.sort {
                        if response.clicked() {
                            sort_click = Some(column);
                        }
                    }
                    if let Some(hover) = spec.hover {
                        response.on_hover_text(hover);
                    }
                });
                // BOTH THE CELL AND THE HEADING, deliberately.
                //
                // egui_extras registers an interaction over the whole cell rect
                // AFTER the contents are drawn (StripLayout::add, last line),
                // using this table's sense - Sense::click(), so that body rows
                // can be clicked. Which of the two a given click reaches was
                // not something reading the source settled. The reported
                // symptom was that clicking BESIDE a heading sorted while
                // clicking ON it did nothing.
                //
                // So both are read. Whichever receives the click, the heading
                // sorts, and the dead strip beside the words is gone either
                // way. They cannot fight: both assign the same column to
                // `sort_click`, so a click that somehow reached both still
                // sorts once rather than toggling twice back to where it was.
                if let Some(column) = spec.sort {
                    if cell.clicked() {
                        sort_click = Some(column);
                    }
                }
            }
        })
        .body(|body| {
            // Per-row heights, so the row that is open can be taller than the
            // rest. egui_extras rows are otherwise uniform, which is what
            // forced the description into a separate panel underneath.
            let heights: Vec<f32> = app
                .triage_rows
                .iter()
                .map(|r| {
                    if app.triage_expanded.as_deref() == Some(r.job.key.as_str()) {
                        row_height + EXPANDED_EXTRA
                    } else {
                        row_height
                    }
                })
                .collect();
            body.heterogeneous_rows(heights.into_iter(), |mut row| {
                let idx = row.index();
                let r = &app.triage_rows[idx];
                let expanded_row = app.triage_expanded.as_deref() == Some(r.job.key.as_str());
                let is_selected = selected_key.as_deref() == Some(r.job.key.as_str());
                // An open row reserves ~260px for its detail, and a selection
                // FILL over that is a wall of solid colour with the job line
                // lost inside it. The open row is outlined instead - the job
                // line, the detail and the next job stay three distinct things.
                row.set_selected(is_selected && !expanded_row);

                let expanded = app.triage_expanded.as_deref() == Some(r.job.key.as_str());
                let cell = CellCtx {
                    r,
                    idx,
                    expanded,
                    selected: app.selected_keys.contains(&r.job.key),
                    row_height,
                    table_width,
                    latest_collect: latest_collect.as_deref(),
                };
                for id in &shown {
                    row.col(|ui| draw_cell(ui, *id, &cell, &mut actions));
                }

                if row.response().clicked() {
                    click_target = Some(r.job.key.clone());
                    // Clicking the open row closes it again; clicking a
                    // different one moves the open block to that row.
                    expand_target = Some(r.job.key.clone());
                }
            });
        });
        });
    }

    // The gear now lives in the toolbar - see the Columns button up there. It
    // was positioned by absolute rectangle into a margin the table was meant
    // to leave free, which held only while the table stayed inside it. With
    // enough columns it did not, and the gear was drawn over a heading.
    column_settings_window(app, ctx);

    let RowActions {
        status_change,
        taken_down,
        fit_click,
        expanded_anchor,
        toggle_select,
    } = actions;

    if let Some(key) = taken_down {
        app.mark_taken_down(&key);
    }

    if let Some((key, ticked)) = toggle_select {
        if ticked {
            app.selected_keys.insert(key);
        } else {
            app.selected_keys.remove(&key);
        }
    }
    if let Some(all) = select_all {
        let keys: Vec<String> = app.triage_rows.iter().map(|r| r.job.key.clone()).collect();
        for key in keys {
            if all {
                app.selected_keys.insert(key);
            } else {
                app.selected_keys.remove(&key);
            }
        }
    }

    if let Some(key) = click_target {
        app.triage_selected = Some(key);
    }
    if let Some(column) = sort_click {
        app.sort_by_column(column);
    }
    // Drawn in a foreground layer anchored to the open row. The row itself
    // already reserved the height, so this fills a gap rather than covering
    // another row, and it re-anchors every frame so it tracks scrolling.
    if let Some((idx, pos)) = expanded_anchor {
        if let Some(r) = app.triage_rows.get(idx) {
            let width = (table_width - 28.0).max(240.0);
            egui::Area::new(egui::Id::new(("triage_expanded", &r.job.key)))
                .order(egui::Order::Foreground)
                .fixed_pos(pos)
                .show(ui.ctx(), |ui| {
                    // OPAQUE, and hard-bounded. A foreground layer does not
                    // inherit the table's background or its clipping, so the
                    // first version drew transparent text straight over the
                    // rows underneath and ran past the height the row had
                    // reserved.
                    egui::Frame::none()
                        .fill(ui.visuals().panel_fill)
                        .stroke(egui::Stroke::new(1.0, ui.visuals().selection.bg_fill))
                        .inner_margin(egui::Margin::same(1.0))
                        .show(ui, |ui| {
                            ui.set_width(width);
                            ui.set_height(EXPANDED_EXTRA - 8.0);
                            let files = crate::views::attachments::Files {
                                rows: &app.attachments,
                                message: app.attachment_message.as_deref(),
                                download_dir: app.settings.download_dir.as_deref(),
                            };
                            match expanded_block(ui, r, app_tab, &files) {
                                BlockAction::Close => close_requested = true,
                                BlockAction::Switch(tab) => tab_switch = Some(tab),
                                BlockAction::Files(chosen) => file_action = Some(chosen),
                                BlockAction::OpenEarlier(key) => open_earlier = Some(key),
                                BlockAction::None => {}
                            }
                        });
                });
        }
    }
    if let Some(tab) = tab_switch {
        app.expanded_tab = tab;
    }
    if let Some(key) = open_earlier {
        // OPENED WHEREVER IT IS. The earlier round is usually delisted, which
        // the ordinary list does not show - so this switches to the scope that
        // holds it rather than selecting a row that is not on screen and
        // looking like it did nothing.
        app.open_job_anywhere(&key);
    }
    if let Some(chosen) = file_action {
        // The key is read from the app rather than carried out of the block:
        // the row the block drew is the open one by construction, and the app
        // is the thing that knows which that is.
        if let Some(key) = app.triage_expanded.clone() {
            use crate::views::attachments::Action;
            match chosen {
                Action::None => {}
                Action::Attach => app.attach_file(&key),
                Action::Paste => app.paste_screenshot(&key),
                Action::AddLink => app.link_prompt_open = true,
                Action::Download(id) => app.download_attachment(id),
                Action::Remove(id) => app.remove_attachment(id),
                Action::SetTrust(id, trust) => app.set_attachment_trust(id, trust),
            }
        }
    }
    link_prompt(app, ui);
    if let Some(key) = fit_click {
        // Opens the row AND switches the block to the fit breakdown, so one
        // click answers "why that number" instead of two.
        app.triage_selected = Some(key.clone());
        app.triage_expanded = Some(key);
        app.expanded_tab = ExpandedTab::Fit;
    }
    if close_requested {
        app.triage_expanded = None;
    }
    if let Some(key) = expand_target {
        app.triage_expanded = if app.triage_expanded.as_deref() == Some(key.as_str()) {
            None
        } else {
            Some(key)
        };
    }
    if let Some((key, status)) = status_change {
        if status.is_empty() {
            app.clear_status_for(&key);
        } else {
            app.set_status_for(&key, &status);
        }
    }

}

/// What you can do to everything you have ticked.
///
/// Only present when something IS ticked. A permanently visible row of bulk
/// actions is a row of buttons that are wrong to press almost all of the time,
/// and it costs vertical space on the screen a person spends the most time on.
fn bulk_bar(app: &mut UnlatchedApp, ui: &mut egui::Ui) {
    let count = app.selected_keys.len();
    if count == 0 {
        return;
    }
    let mut set_status: Option<&str> = None;
    let mut retire = false;
    let mut taken_down = false;
    let mut restore = false;
    let mut ungroup = false;
    let mut clear = false;

    {
        {
            ui.horizontal(|ui| {
                ui.strong(format!("{count} selected"));
                ui.add_space(10.0);

                egui::ComboBox::from_id_source("bulk_status")
                    .selected_text("Set status")
                    .width(150.0)
                    .show_ui(ui, |ui| {
                        if ui.selectable_label(false, NOT_SET).clicked() {
                            set_status = Some("");
                        }
                        // THE DEPENDENT STATUSES ARE NOT OFFERED IN BULK.
                        // Whether Offer Withdrawn is available depends on each
                        // job's own history, and a batch of nine rows has nine
                        // different answers - so the honest options here are
                        // the ones that mean the same thing for every row.
                        for status in crate::status::FLOW.iter() {
                            if status.requires.is_some() {
                                continue;
                            }
                            if ui
                                .selectable_label(false, status.label)
                                .on_hover_text(status.hint)
                                .clicked()
                            {
                                set_status = Some(status.value);
                            }
                        }
                        ui.separator();
                        ui.weak("Declined Offer and Offer Withdrawn are set one job at a time.")
                            .on_hover_text(
                                "Each depends on what that job's own history \
                                 shows, so they cannot be applied to a batch.",
                            );
                    });

                // Restore instead of remove while looking at what was removed:
                // the same tick boxes, the opposite verb, so the way back is
                // the same gesture as the way out.
                //
                // NAMED, because these two are the SAME control in two states.
                // Anything addressing it by visible text would be addressing a
                // different thing depending on which list is showing - the exact
                // instability build_standard's "stable name" wording exists for.
                if app.show_duplicates {
                    let response = crate::access::tag(
                        ui.button("Ungroup").on_hover_text(
                            "Show these separately again. Use it when two \
                             postings were folded together that are not the \
                             same job.",
                        ),
                        egui::WidgetType::Button,
                        "bulk-ungroup",
                    );
                    if response.clicked() {
                        ungroup = true;
                    }
                } else if app.show_retired {
                    let response = crate::access::tag(
                        ui.button("Put back").on_hover_text("Return these to your lists"),
                        egui::WidgetType::Button,
                        "bulk-restore",
                    );
                    if response.clicked() {
                        restore = true;
                    }
                } else {
                    let response = crate::access::tag(
                        ui.button("Remove from list").on_hover_text(
                            "Takes these out of every list. They keep their status \
                             and history and can be put back from All jobs.",
                        ),
                        egui::WidgetType::Button,
                        "bulk-remove",
                    );
                    if response.clicked() {
                        retire = true;
                    }

                    // A DIFFERENT VERB FROM REMOVE, beside it on purpose.
                    // Remove is "I do not want to see this"; taken down is
                    // "the employer withdrew it". They land in the same place
                    // - out of the working lists - for opposite reasons, and
                    // conflating them would lose the reason.
                    let response = crate::access::tag(
                        ui.button("Mark taken down").on_hover_text(
                            "The employer pulled these postings. They leave your \
                             lists and read as Expired. Anything you had \
                             already applied to KEEPS its status and stays \
                             visible - the advert closing does not undo your \
                             application.",
                        ),
                        egui::WidgetType::Button,
                        "bulk-taken-down",
                    );
                    if response.clicked() {
                        taken_down = true;
                    }
                }

                ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                    let response = crate::access::tag(
                        ui.button("Clear selection"),
                        egui::WidgetType::Button,
                        "bulk-clear",
                    );
                    if response.clicked() {
                        clear = true;
                    }
                });
            });
        }
    }

    if let Some(status) = set_status {
        app.set_status_for_selection(status);
    }
    if retire {
        app.ask_to_retire_selection();
    }
    if taken_down {
        app.mark_selection_taken_down();
    }
    if restore {
        app.restore_selection();
    }
    if ungroup {
        app.ungroup_selection();
    }
    if clear {
        app.selected_keys.clear();
    }
}

/// The one confirmation in this app, because this is the one action that
/// removes something in bulk.
///
/// It states the COST rather than asking whether you are sure: how many rows,
/// and how many of them you applied to. "Are you sure" is a question nobody
/// has the information to answer.
pub fn confirm_retire_window(app: &mut UnlatchedApp, ctx: &egui::Context) {
    let Some(pending) = &app.confirm_retire else {
        return;
    };
    let count = pending.keys.len();
    let applied = pending.applied;
    let mut go = false;
    let mut cancel = false;

    egui::Window::new("Remove from your lists")
        .collapsible(false)
        .resizable(false)
        .anchor(egui::Align2::CENTER_CENTER, [0.0, 0.0])
        .show(ctx, |ui| {
            ui.set_width(420.0);
            ui.label(format!(
                "{count} job{} will be taken out of every list.",
                if count == 1 { "" } else { "s" }
            ));
            ui.add_space(6.0);
            if applied > 0 {
                // Phrased with the count as the object rather than the subject.
                // The earlier wording agreed the verb but not the noun, so any
                // count above one read "2 of them are one you applied to."
                ui.colored_label(
                    egui::Color32::from_rgb(217, 164, 65),
                    if count == 1 {
                        "You applied to this one.".to_string()
                    } else {
                        format!("You applied to {applied} of them.")
                    },
                );
                ui.add_space(6.0);
            }
            ui.weak(
                "Nothing is deleted. They keep their status and their history, \
                 and All jobs -> Removed puts them back.",
            );
            ui.add_space(12.0);
            ui.horizontal(|ui| {
                // Named because this dialog CHANGES HEIGHT: it grows a line
                // when it has an applied count to report, which moved these
                // buttons 14px down and made a positional click land on body
                // text while the run carried on regardless.
                let go_response = crate::access::tag(
                    ui.button("Remove them"),
                    egui::WidgetType::Button,
                    "confirm-remove",
                );
                if go_response.clicked() {
                    go = true;
                }
                let cancel_response = crate::access::tag(
                    ui.button("Cancel"),
                    egui::WidgetType::Button,
                    "confirm-cancel",
                );
                if cancel_response.clicked() {
                    cancel = true;
                }
            });
        });

    if go {
        app.retire_confirmed();
    }
    if cancel {
        app.confirm_retire = None;
    }
}

/// The note that goes with a status change.
///
/// WHY A PROMPT AND NOT A FIELD SOMEWHERE. A note written a week later is a
/// reconstruction; the only moment somebody actually knows what happened is
/// the moment they record it. So the app asks then, every time, and never
/// insists - Enter with an empty box saves the change and logs no note.
///
/// IT OPENS WITH THE CURSOR IN THE FIELD. It did not, once, so that Enter
/// could mean save - a multiline box takes Enter as a new line, and an
/// auto-focused one would have saved on the first paragraph break. That kept
/// 'a' then Enter working and made every note start with a click. Both work
/// now: plain Enter is taken out of the input queue before the box is drawn,
/// and Shift+Enter reaches it as a new line.
///
/// Save / Skip / Cancel are three different things and each is reachable:
/// Save writes the change WITH what was typed, Skip writes it with nothing,
/// and Cancel leaves the status where it was. A two-button dialog would have
/// made "I do not want to write a note" and "I did not mean to click that"
/// the same gesture.
pub fn status_note_window(app: &mut UnlatchedApp, ctx: &egui::Context) {
    let Some(pending) = &app.pending_status else {
        return;
    };
    let subject = pending.subject.clone();
    let status_label = crate::status::label(&pending.status);
    let hint = crate::status::get(&pending.status).map(|s| s.hint);
    let wants_offer_fields = crate::status::has_offer_fields(&pending.status);
    let mut save = false;
    let mut cancel = false;
    let mut place_cursor = !app.pending_status.as_ref().is_some_and(|p| p.cursor_placed);

    // DRAGGABLE, WHICH .anchor() MADE IMPOSSIBLE. An anchor is re-applied
    // every frame, so egui moved the window back under the cursor mid-drag
    // and it read as a window that simply would not move.
    //
    // .pivot + .default_pos gives the same opening position - centred - and
    // then leaves it alone. It has to be movable because this prompt covers
    // the row it is asking about: somebody writing a note about a posting
    // frequently wants to see that posting while they write.
    egui::Window::new(format!("Mark {subject} {status_label}"))
        .id(egui::Id::new("status-note"))
        .collapsible(false)
        .resizable(false)
        .pivot(egui::Align2::CENTER_CENTER)
        .default_pos(ctx.screen_rect().center())
        .show(ctx, |ui| {
            ui.set_width(440.0);
            // TAKEN OUT OF THE QUEUE BEFORE ANY FIELD IS DRAWN, which is the
            // whole trick: the note box never sees an unmodified Enter, so it
            // cannot turn one into a new line, and Enter therefore saves
            // whether or not the cursor is sitting in it. Shift+Enter still
            // reaches the box and starts a new line.
            //
            // NOT input.consume_key. That matches modifiers LOGICALLY - its
            // own documentation says extra Shift and Alt are ignored - so
            // asking it for a plain Enter also eats Shift+Enter, and the new
            // line this is supposed to protect would never arrive.
            if ui.input_mut(|i| {
                let mut pressed = false;
                i.events.retain(|event| {
                    let is_plain_enter = matches!(
                        event,
                        egui::Event::Key {
                            key: Key::Enter,
                            modifiers,
                            pressed: true,
                            ..
                        } if modifiers.is_none()
                    );
                    pressed |= is_plain_enter;
                    !is_plain_enter
                });
                pressed
            }) {
                save = true;
            }
            if let Some(hint) = hint {
                ui.weak(hint);
                ui.add_space(6.0);
            }

            if wants_offer_fields {
                // Pay and the date are their own boxes rather than a sentence
                // inside the note, because they are the two facts somebody is
                // asked for months later and the two a paragraph buries.
                let Some(pending) = &mut app.pending_status else {
                    return;
                };
                egui::Grid::new("offer-terms")
                    .num_columns(2)
                    .spacing([8.0, 6.0])
                    .show(ui, |ui| {
                        ui.label("Pay");
                        let pay = crate::access::tag(
                            ui.text_edit_singleline(&mut pending.pay),
                            egui::WidgetType::TextEdit,
                            "offer-pay",
                        );
                        // AN OFFER OPENS HERE, not on the note. Pay and the
                        // date are why this prompt is never skippable, and
                        // they are the first thing it asks for.
                        if place_cursor {
                            place_cursor = false;
                            pending.cursor_placed = true;
                            pay.request_focus();
                        }
                        ui.end_row();
                        ui.label("Offer date");
                        crate::access::tag(
                            ui.text_edit_singleline(&mut pending.offer_date),
                            egui::WidgetType::TextEdit,
                            "offer-date",
                        );
                        ui.end_row();
                    });
                ui.weak("Both optional. Written as you type them - no format is imposed.");
                ui.add_space(8.0);
            }

            ui.label("Note (optional)");
            let Some(pending) = &mut app.pending_status else {
                return;
            };
            let edit = crate::access::tag(
                ui.add(
                    egui::TextEdit::multiline(&mut pending.note)
                        .desired_rows(3)
                        .desired_width(f32::INFINITY),
                ),
                egui::WidgetType::TextEdit,
                "status-note",
            );
            if place_cursor {
                pending.cursor_placed = true;
                edit.request_focus();
            }
            ui.add_space(10.0);
            ui.weak("Enter saves. Shift+Enter starts a new line. Esc leaves the status alone.");
            ui.add_space(4.0);

            ui.horizontal(|ui| {
                // NAMED, all three: this dialog changes height with the status
                // (an Offer adds two rows), so a positional click would land on
                // a different button depending on what was being recorded.
                if crate::access::tag(
                    ui.button("Save"),
                    egui::WidgetType::Button,
                    "status-note-save",
                )
                .clicked()
                {
                    save = true;
                }
                if crate::access::tag(
                    ui.button("Skip"),
                    egui::WidgetType::Button,
                    "status-note-skip",
                )
                .on_hover_text("Record the change without writing anything about it.")
                .clicked()
                {
                    save = true;
                }
                ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                    if crate::access::tag(
                        ui.button("Cancel"),
                        egui::WidgetType::Button,
                        "status-note-cancel",
                    )
                    .on_hover_text("Leave the status as it was.")
                    .clicked()
                    {
                        cancel = true;
                    }
                });
            });
        });

    // Escape cancels. Enter saves, and is claimed inside the window above
    // rather than here - by the time this runs a plain Enter has already been
    // consumed, so this only has to catch the Ctrl+Enter that used to be the
    // save for somebody typing. Kept because it was the documented gesture and
    // costs nothing to honour.
    ctx.input(|inp| {
        if inp.key_pressed(Key::Escape) {
            cancel = true;
        }
        if inp.key_pressed(Key::Enter) && inp.modifiers.command {
            save = true;
        }
    });

    if save {
        app.commit_status_change();
    } else if cancel {
        app.cancel_status_change();
    }
}

/// The gear's panel, in a window rather than a popup.
///
/// A popup closes the moment a click lands outside it, and rearranging columns
/// is a several-click job done WHILE watching the table change underneath -
/// which means clicking the table, which would dismiss a popup every time.
///
/// Saved on every change: there is no Save button on it, so somebody who
/// rearranges their columns and then closes the app has not asked to lose
/// anything.
fn column_settings_window(app: &mut UnlatchedApp, ctx: &egui::Context) {
    if !app.show_column_settings {
        return;
    }
    let mut open = true;
    let mut order = app.column_order.clone();
    let mut hidden = app.column_hidden.clone();
    let mut changed = false;
    egui::Window::new("Columns")
        .open(&mut open)
        .collapsible(false)
        .resizable(false)
        .anchor(egui::Align2::RIGHT_TOP, [-16.0, 90.0])
        .show(ctx, |ui| {
            changed = columns::settings_panel(ui, &mut order, &mut hidden);
        });
    if changed {
        app.column_order = order;
        app.column_hidden = hidden;
        app.save_column_layout();
    }
    app.show_column_settings = open;
}

/// What one row needs in order to draw any of its cells.
///
/// Bundled because the cells are no longer written out in a fixed sequence
/// inside the row closure - they are dispatched by column id, so each one has
/// to be handed the row and the per-frame facts explicitly.
struct CellCtx<'a> {
    r: &'a crate::db::TriageRow,
    idx: usize,
    expanded: bool,
    selected: bool,
    row_height: f32,
    table_width: f32,
    /// When the most recent collection ran, for the NEW badge. None when every
    /// row would qualify - see where it is computed.
    latest_collect: Option<&'a str>,
}

/// What the cells want done after the table is drawn.
///
/// Deferred rather than applied in place for the same borrow reason every
/// other deferred action in this file exists: the row closures hold an
/// immutable borrow of app.triage_rows, and refreshing mid-draw would renumber
/// the rows being iterated.
#[derive(Default)]
struct RowActions {
    status_change: Option<(String, String)>,
    /// A row the person says the employer has pulled.
    taken_down: Option<String>,
    fit_click: Option<String>,
    expanded_anchor: Option<(usize, egui::Pos2)>,
    /// A tick box that changed, as (key, now selected).
    toggle_select: Option<(String, bool)>,
}

/// One cell. Which one is decided by `id`, not by where the call sits.
fn draw_cell(ui: &mut egui::Ui, id: columns::ColumnId, c: &CellCtx, out: &mut RowActions) {
    use columns::ColumnId as C;
    let r = c.r;
    match id {
        C::Select => {
            let mut ticked = c.selected;
            // Named per JOB KEY, not per row index. The list re-sorts and
            // re-filters underneath somebody while they are selecting, so a
            // name built from the position would refer to different jobs from
            // one moment to the next - the same reason the selection itself is
            // held by key.
            let response = crate::access::tag_with_value(
                ui.checkbox(&mut ticked, ""),
                egui::WidgetType::Checkbox,
                format!("select-{}", crate::access::slug(&r.job.key)),
                if ticked { "selected" } else { "not selected" },
            );
            if response.changed() {
                out.toggle_select = Some((r.job.key.clone(), ticked));
            }
        }

        C::Title => {
            if c.expanded {
                // Only the ANCHOR is taken here. A cell is clipped to its own
                // column, so anything drawn from inside one that reaches past
                // it is simply not painted - the first attempt at this
                // produced a tall empty row. The block itself is drawn after
                // the table, in its own layer, which nothing clips.
                let top_left = ui.min_rect().min;
                out.expanded_anchor =
                    Some((c.idx, top_left + egui::vec2(0.0, c.row_height - 4.0)));
                // Painted over the job line only, not the reserved detail
                // space below it.
                ui.painter().rect_stroke(
                    egui::Rect::from_min_size(
                        top_left - egui::vec2(4.0, 3.0),
                        egui::vec2((c.table_width - 20.0).max(240.0), c.row_height),
                    ),
                    2.0,
                    egui::Stroke::new(1.5, ui.visuals().selection.bg_fill),
                );
            }
            // Arrived in the most recent collect. The dashboard has shown the
            // COUNT since the dashboard existed; this is the other half -
            // which rows they actually are.
            if c.latest_collect
                .zip(r.job.fetched_at.as_deref())
                .is_some_and(|(latest, fetched)| {
                    fetched[..fetched.len().min(10)] >= latest[..latest.len().min(10)]
                })
            {
                ui.label(
                    egui::RichText::new("NEW")
                        .size(9.0)
                        .strong()
                        .color(crate::theme::ACCENT),
                )
                .on_hover_text("Arrived in the most recent collection.");
            }
            let title = fmt::truncate(&r.job.title, 40);
            let withdrawn = r
                .job
                .delisted_at
                .as_deref()
                .is_some_and(|d| !d.trim().is_empty());
            let response = match &r.job.url {
                // Struck through, not hidden or deleted. The person may have
                // applied to it, and a posting they applied to being pulled is
                // information they need - the row has to outlive the listing.
                _ if withdrawn => ui
                    .label(egui::RichText::new(title).strikethrough().weak())
                    .on_hover_text(
                        "No longer on the employer's board. Your own status for it \
                         is untouched.",
                    ),
                // Only http(s) becomes clickable. A posting that nominates any
                // other scheme still shows, still screens and still keeps its
                // row - it just is not something this app will ask the
                // operating system to open. See fmt::safe_link.
                Some(url) => match fmt::safe_link(url) {
                    Some(safe) => crate::browse::link(ui, title, safe),
                    None => ui.label(title),
                },
                _ => ui.label(title),
            };
            response.on_hover_ui(|ui| {
                // FIRST, because in the grouped view it is the only thing the
                // person is here to judge: this row was folded away by the
                // app's own decision, and they are deciding whether it was
                // right. Everything else about the posting is secondary to
                // that.
                if let Some(why) = &r.job.duplicate_reason {
                    if !why.trim().is_empty() {
                        ui.label(
                            egui::RichText::new(format!("grouped: {why}"))
                                .color(egui::Color32::from_rgb(217, 164, 65)),
                        );
                    }
                }
                if let Some(remote) = &r.job.remote {
                    ui.label(format!("remote: {remote}"));
                }
                if let Some(posted) = &r.job.posted_at {
                    ui.label(format!("posted: {posted}"));
                }
                if let Some(fetched) = &r.job.fetched_at {
                    ui.label(format!("fetched: {fetched}"));
                }
                if let Some(reasons) = &r.job.screen_reasons {
                    if !reasons.trim().is_empty() {
                        ui.label(format!("screen: {reasons}"));
                    }
                }
                // The preview, not job.description: a list does not carry the
                // full text, and this tooltip never showed more than 400
                // characters of it anyway.
                if let Some(desc) = &r.description_preview {
                    if !desc.trim().is_empty() {
                        ui.label(fmt::truncate(desc, 400));
                    }
                }
            });
        }

        C::Company => {
            ui.label(fmt::truncate(fmt::opt_str(&r.company_name), 28));
        }

        C::Location => {
            let place = fmt::truncate(fmt::opt_str(&r.job.location), 24);
            if r.other_locations.is_empty() {
                ui.label(place);
            } else {
                // One requisition advertised per city. The count is the useful
                // part; the cities themselves are on hover for anyone who can
                // only work in one of them.
                let extra = r.other_locations.len();
                ui.label(format!("{place} +{extra}"))
                    .on_hover_text(format!(
                        "Also listed in:\n{}",
                        r.other_locations.join("\n")
                    ));
            }
        }

        C::Posted => {
            // An age, not a date. The exact date is still one hover away, but
            // "3 days" answers the question a reader is actually asking and a
            // date makes them do arithmetic.
            let raw = fmt::opt_str(&r.job.posted_at);
            let age = fmt::posted_age(raw);
            let posted = if age.is_empty() {
                fmt::truncate(raw, 12)
            } else {
                age
            };
            // Softened once a posting is old enough to often be filled. Never
            // hidden - plenty of real openings sit for months, so this is a
            // cue, not a judgement.
            let text = if fmt::is_stale(raw) {
                egui::RichText::new(posted).weak()
            } else {
                egui::RichText::new(posted)
            };
            let exact = format!("posted {raw}");
            match &r.job.repost_note {
                // A seat advertised before is worth seeing at a glance, but it
                // cuts both ways - the employer is still hiring and the last
                // round produced nobody - so the marker states the fact and the
                // hover explains it, rather than colouring it as a warning.
                Some(note) if !note.trim().is_empty() => {
                    // The note is a whole sentence from the engine. It used to
                    // be a fragment this line completed with "This seat was ",
                    // which put half the wording of an engine-owned rule in
                    // Rust - and the half that broke first.
                    ui.label(text).on_hover_text(format!("{exact}\n{note}"));
                }
                _ => {
                    ui.label(text).on_hover_text(exact);
                }
            }
        }

        C::Salary => {
            ui.label(fmt::salary_range(
                r.job.salary_min,
                r.job.salary_max,
                r.job.currency.as_deref(),
                r.job.hourly_rate,
            ));
        }

        C::Score => {
            let score = r.job.score.map(|s| format!("{s:.2}")).unwrap_or_default();
            ui.label(score);
        }

        C::Fit => {
            // No number means not assessed - no skills configured, or this
            // posting asked for none of them. Showing 0% there would read as
            // "you match none of this", which is a different and untrue
            // statement.
            match r.job.coverage_pct {
                Some(pct) => {
                    let gaps = fmt::opt_str(&r.job.missing_skills);
                    let hover = if gaps.is_empty() {
                        "Your resume already shows everything this posting asks \
                         for. Click for the detail."
                            .to_string()
                    } else {
                        format!("Not shown on your resume: {gaps}\n\nClick to drill in.")
                    };
                    // A number that explains a decision should be able to show
                    // its working. Clicking opens the posting with the
                    // breakdown on top, rather than a second job screen.
                    let colour = if pct >= 80.0 {
                        egui::Color32::from_rgb(34, 197, 94)
                    } else if pct >= 50.0 {
                        egui::Color32::from_rgb(217, 119, 6)
                    } else {
                        egui::Color32::from_rgb(220, 38, 38)
                    };
                    if ui
                        .add(
                            egui::Label::new(
                                egui::RichText::new(format!("{pct:.0}%")).color(colour),
                            )
                            .sense(egui::Sense::click()),
                        )
                        .on_hover_cursor(egui::CursorIcon::PointingHand)
                        .on_hover_text(hover)
                        .clicked()
                    {
                        out.fit_click = Some(r.job.key.clone());
                    }
                }
                None => {
                    ui.label("-").on_hover_text(
                        "Not assessed - add the skills you want tracked on the \
                         Config tab, and point the app at your resume.",
                    );
                }
            }
        }

        C::Asks => {
            let asks = fmt::opt_str(&r.job.requirements_summary);
            if asks.is_empty() {
                ui.label("");
            } else {
                ui.label(fmt::truncate(asks, 22)).on_hover_text(asks);
            }
        }

        C::Source => {
            ui.label(fmt::truncate(fmt::opt_str(&r.job.source), 12));
        }

        C::Match => {
            // "alt" is a fallback-tier match - shown, but it should never read
            // as a clean one at a glance, and WHICH kind is the useful half.
            //
            // THE CARD IT BELONGS TO, not a second opinion about it. The
            // colour, the name and the explanation all come from the module
            // that counts this row on the dashboard, so a badge and the card
            // it clicks through to cannot end up saying different things -
            // which is exactly what the hand-written amber and hand-written
            // tooltip here were free to do. Agreement is tested in
            // db::tests::the_badge_names_the_card_the_row_is_actually_counted_on.
            let verdict = fmt::opt_str(&r.job.verdict);
            if verdict == "alt" {
                // The column is 60px. A word that fits is the constraint;
                // the hover carries the card name in full.
                let module = crate::modules::Module::for_alt_reason(
                    fmt::opt_str(&r.job.alt_reason),
                );
                let word = if module == crate::modules::Module::BelowSalary {
                    "pay"
                } else {
                    "fit"
                };
                let [red, green, blue] = module.colour();
                ui.colored_label(egui::Color32::from_rgb(red, green, blue), word)
                    .on_hover_text(format!(
                        "{}. {} The reason is in the panel below.",
                        module.heading(),
                        module.caption()
                    ));
            } else {
                ui.label(verdict);
            }
        }

        C::Status => {
            // A dropdown per row. The keyboard shortcuts still work for anyone
            // who wants them, but they act only on the highlighted row, and
            // picking a status should not require first selecting the right
            // line.
            let current = fmt::opt_str(&r.status);
            let shown = crate::status::label(current);
            ui.horizontal(|ui| {
                egui::ComboBox::from_id_source(("status", &r.job.key))
                    .selected_text(&shown)
                    // Wide enough for the longest label ("Offer Withdrawn")
                    // without the ellipsis. A truncated status is exactly the
                    // kind of thing somebody misreads as its neighbour.
                    .width(140.0)
                    // THE WHOLE VOCABULARY, NO SCROLLING. egui caps a combo
                    // popup at 200 px, which fits seven entries - so the list
                    // has silently scrolled ever since it grew past that, and
                    // the statuses at the bottom are the ones a person reaches
                    // for at the END of a search, when they are least likely to
                    // go hunting for them. Derived from the count rather than
                    // typed, so adding a status can never quietly reintroduce
                    // the scrollbar. `status_popup_height` is unit-tested.
                    .height(crate::status::popup_height(ui))
                    .show_ui(ui, |ui| {
                        // Every choice is listed, blocked ones included, so the
                        // list does not change shape between jobs and nobody
                        // hunts for an entry that is simply absent.
                        if ui.selectable_label(current.is_empty(), NOT_SET).clicked() {
                            out.status_change = Some((r.job.key.clone(), String::new()));
                        }
                        for (status, blocked) in crate::status::choices_for(&r.history) {
                            match blocked {
                                Some(reason) => {
                                    ui.add_enabled(
                                        false,
                                        egui::Button::new(status.label).frame(false),
                                    )
                                    .on_disabled_hover_text(reason);
                                }
                                None => {
                                    let picked = ui
                                        .selectable_label(current == status.value, status.label)
                                        .on_hover_text(status.hint);
                                    if picked.clicked() {
                                        out.status_change = Some((
                                            r.job.key.clone(),
                                            status.value.to_string(),
                                        ));
                                    }
                                }
                            }
                        }
                        // IN THE LIST, not set apart. Underneath, this is not a
                        // status - it records that the EMPLOYER pulled the
                        // advert, which is why an application the row already
                        // carries survives it. But that distinction only ever
                        // surfaces on a row somebody applied to and then marked
                        // by hand, and the answer to that (2026-08-26) is
                        // that nobody would: no reason to set
                        // a new status by hand on something already applied to -
                        // the next refresh catches it. So it
                        // is offered as what it actually is in use: the thing
                        // you pick when you open an untriaged job and find it
                        // gone.
                        if r.job.delisted_at.is_none()
                            && ui
                                .selectable_label(false, "Posting taken down")
                                .on_hover_text(
                                    "You opened it and the advert had closed. \
                                     Takes it out of this list without waiting \
                                     for the next collection to notice.",
                                )
                                .clicked()
                            {
                                out.taken_down = Some(r.job.key.clone());
                            }
                    });
                // TAKEN DOWN IS SHOWN BESIDE THE STATUS, NEVER INSTEAD OF IT.
                // It used to be a status somebody set by hand, which made
                // "applied" and "taken down" mutually exclusive - and the row
                // where both are true is the one a person most needs to see.
                if r.job.delisted_at.is_some() {
                    ui.weak("taken down").on_hover_text(
                        "The employer removed this posting. Whatever you \
                         recorded about it is untouched.",
                    );
                }
                // How long the row has sat in its current state. Applications
                // are lost to silence more than to rejection, and nothing else
                // in the app says how long it has been since you acted on one.
                if !current.is_empty() {
                    if let Some(days) = r
                        .status_updated
                        .as_deref()
                        .and_then(crate::date::days_since)
                    {
                        let text = match days {
                            0 => "today".to_string(),
                            1 => "1d".to_string(),
                            d => format!("{d}d"),
                        };
                        ui.weak(text).on_hover_text(format!(
                            "Marked {shown} {}.",
                            match days {
                                0 => "today".to_string(),
                                1 => "yesterday".to_string(),
                                d => format!("{d} days ago"),
                            }
                        ));
                    }
                }
            });
        }

        // WHEN they applied, as a date.
        //
        // The "12d" beside the dropdown answers "how long since I touched
        // this"; a person chasing an application needs the actual date they
        // sent it, and needs it scannable down a column rather than folded into
        // another cell.
        //
        // Read from the append-only log, so marking a job Interviewed later
        // does not move the date they applied.
        //
        // Orange past a fortnight - the same SILENT_DAYS the dashboard counts
        // silence by, so the list and the dashboard cannot disagree about when
        // an application has gone quiet.
        C::Applied => {
            let Some(applied) = r.applied_at.as_deref() else {
                return;
            };
            let days = crate::date::days_since(applied);
            let text = fmt::short_date(applied);
            let label = match days {
                Some(d) if d >= crate::dashboard::SILENT_DAYS => {
                    ui.colored_label(egui::Color32::from_rgb(217, 164, 65), text)
                }
                _ => ui.label(text),
            };
            if let Some(d) = days {
                label.on_hover_text(match d {
                    0 => "Applied today.".to_string(),
                    1 => "Applied yesterday.".to_string(),
                    d if d >= crate::dashboard::SILENT_DAYS => format!(
                        "Applied {d} days ago - silent for over {} days. \
                         Applications are lost to silence far more often than to \
                         a rejection.",
                        crate::dashboard::SILENT_DAYS
                    ),
                    d => format!("Applied {d} days ago."),
                });
            }
        }

        // WHERE this came from, as somewhere you can actually go.
        //
        // The Source column already says "lever" or "greenhouse", but that is
        // the collector's name, not a place. Both are shown so a reader can
        // work from either (2026-08-07): the host names the board, and clicking it
        // opens the posting.
        C::FoundAt => {
            // This column exists so a person can judge where a job came from,
            // which is exactly the judgement a spoofed host would defeat -
            // link_host now parses rather than splits, so
            // "https://boards.greenhouse.io@evil.com/x" reads as evil.com.
            let Some(url) = fmt::safe_link(fmt::opt_str(&r.job.url)) else {
                return;
            };
            // EASY APPLY IS THE FACT WORTH NAMING. Anything else is just the
            // host, which is what this cell already said - so the label
            // changes only when there is something to say.
            //
            // THE LINK GOES WHERE THE POSTING WAS FOUND, deliberately, and an
            // apply_url does NOT redirect it (decided 2026-08-26). The board
            // the posting was found on is where the reader wants to land: it
            // is where their own application record lives. The apply
            // destination exists so that the same job collected from an
            // employer's board can be GROUPED with the board listing - a join
            // key, not a place to send somebody. See dupes.py.
            let label = match r.job.apply_kind.as_deref() {
                Some("easy-apply") => "Easy Apply".to_string(),
                _ => fmt::link_host(url),
            };
            crate::browse::link(ui, label, url).on_hover_text(url);
        }
    }
}

/// What the open block wants the caller to do afterwards. Returned rather
/// than mutating app state directly, for the same borrow reason every other
/// deferred action in this file exists.
pub enum BlockAction {
    None,
    Close,
    Switch(ExpandedTab),
    /// Something on the Files face, carried out by the caller for the same
    /// borrow reason as everything else here.
    Files(crate::views::attachments::Action),
    /// Open the advertisement this new entry followed, by key.
    OpenEarlier(String),
}

/// "Files", or "Files (3)" once there are some. The count is the whole reason
/// the tab is worth glancing at: it says there is something here without
/// anybody having to open it.
fn files_tab_label(files: &crate::views::attachments::Files<'_>) -> String {
    if files.rows.is_empty() {
        "Files".to_string()
    } else {
        format!("Files ({})", files.rows.len())
    }
}

/// The "add a link" prompt. A modal, like the note prompt, because the open
/// row is drawn from borrowed data and cannot hold a text field of its own.
fn link_prompt(app: &mut UnlatchedApp, ui: &mut egui::Ui) {
    if !app.link_prompt_open {
        return;
    }
    // Two flags, not one: `open` is handed to the window itself (its X button
    // writes through it), so the Cancel button inside the closure needs its
    // own or the same variable is borrowed twice.
    let mut open = true;
    let mut confirmed = false;
    let mut cancelled = false;
    egui::Window::new("Add a link")
        .collapsible(false)
        .resizable(false)
        .open(&mut open)
        .anchor(egui::Align2::CENTER_CENTER, [0.0, 0.0])
        .show(ui.ctx(), |ui| {
            ui.label("A link costs nothing to keep and often outlives the posting.");
            ui.horizontal(|ui| {
                ui.label("Address:");
                ui.text_edit_singleline(&mut app.link_url);
            });
            ui.horizontal(|ui| {
                ui.label("Call it:");
                ui.text_edit_singleline(&mut app.link_label);
            });
            ui.horizontal(|ui| {
                if ui.button("Add").clicked() {
                    confirmed = true;
                }
                if ui.button("Cancel").clicked() {
                    cancelled = true;
                }
            });
        });
    if confirmed {
        if let Some(key) = app.triage_expanded.clone() {
            app.add_link_attachment(&key);
        }
    } else if cancelled || !open {
        app.link_prompt_open = false;
        app.link_url.clear();
        app.link_label.clear();
    }
}

/// The posting, opened underneath its own row.
///
/// This replaced a panel pinned to the bottom of the screen. The panel was
/// detached from the row it described - you clicked a job at the top of a long
/// table and the text appeared at the very bottom, with nothing joining them -
/// and it permanently ate a third of the height whether or not anything was
/// selected. Opening in place costs nothing when closed and puts the text where
/// the eye already is.
fn expanded_block(
    ui: &mut egui::Ui,
    r: &crate::db::TriageRow,
    tab: ExpandedTab,
    files: &crate::views::attachments::Files<'_>,
) -> BlockAction {
    let mut action = BlockAction::None;
    let description = r.job.description.clone().unwrap_or_default();
    let reasons = r.job.screen_reasons.clone().unwrap_or_default();
    let missing = r.job.missing_skills.clone().unwrap_or_default();
    let repost_note = r.job.repost_note.clone().unwrap_or_default();
    // Deferred like every other action here: this block is drawn from borrowed
    // data and cannot change what the caller is iterating over.
    let mut open_earlier: Option<String> = None;

    // A HEADER BAR, not a line of text jammed against the row above. The open
    // posting gets its own banded strip - title, company, size on the left,
    // Close set in from the right - and that band is what separates "the job
    // you are reading" from "the row you clicked" and from "the next job".
    // Without it the three run together.
    egui::Frame::none()
        .fill(ui.visuals().faint_bg_color)
        .inner_margin(egui::Margin {
            left: 10.0,
            right: 8.0,
            top: 6.0,
            bottom: 6.0,
        })
        .show(ui, |ui| {
            ui.horizontal(|ui| {
                ui.strong(fmt::truncate(&r.job.title, 60));
                ui.add_space(6.0);
                ui.weak(fmt::truncate(fmt::opt_str(&r.company_name), 28));
                ui.add_space(6.0);
                ui.weak(format!("{} characters", description.chars().count()));
                if let Some(link) = r.job.url.as_deref().and_then(fmt::safe_link) {
                    ui.add_space(6.0);
                    crate::browse::link(ui, "Open posting", link);
                }
                ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                    if ui.button("Close").clicked() {
                        action = BlockAction::Close;
                    }
                    // Two faces of one job, not two screens. Fit answers "why
                    // that number"; Posting is the text itself.
                    if ui.selectable_label(tab == ExpandedTab::Fit, "Fit").clicked() {
                        action = BlockAction::Switch(ExpandedTab::Fit);
                    }
                    // Files sits beside the other two rather than on a screen
                    // of its own: attachments belong to the JOB, so the
                    // place to find them is the job.
                    //
                    // The visible label carries a COUNT, so it is a different
                    // string every time something is attached - unusable as
                    // an identifier, which is why the count goes in the value
                    // slot instead and the name stays put.
                    if crate::access::tag_with_value(
                        ui.selectable_label(tab == ExpandedTab::Files, files_tab_label(files)),
                        egui::WidgetType::Button,
                        "tab-files",
                        files.rows.len().to_string(),
                    )
                    .clicked()
                    {
                        action = BlockAction::Switch(ExpandedTab::Files);
                    }
                    if ui
                        .selectable_label(tab == ExpandedTab::Posting, "Posting")
                        .clicked()
                    {
                        action = BlockAction::Switch(ExpandedTab::Posting);
                    }
                });
            });
        });
    ui.add_space(6.0);

    // The screening facts, indented as a group so they read as notes ABOUT the
    // posting rather than as the first lines of it.
    ui.indent("posting_facts", |ui| {
        if !reasons.trim().is_empty() {
            ui.weak(format!("screen: {reasons}"));
        }
        let remote_evidence = r.job.remote_evidence.clone().unwrap_or_default();
        if !remote_evidence.trim().is_empty() {
            ui.weak(format!("remote: {remote_evidence}"));
        }
        if !repost_note.trim().is_empty() {
            ui.weak(&repost_note);
            // A new entry after four weeks points back at the round it
            // followed, and both stay visible - so this offers the earlier
            // posting rather than describing it.
            if let Some(original) = r.job.repost_of.clone().filter(|k| !k.is_empty()) {
                if ui
                    .small_button("Open the earlier round")
                    .on_hover_text(
                        "The advertisement this one followed. It is kept as its own \
                         entry, not folded into this one.",
                    )
                    .clicked()
                {
                    open_earlier = Some(original);
                }
            }
        }
        if !missing.trim().is_empty() {
            ui.horizontal_wrapped(|ui| {
                ui.strong("Not on your resume:");
                ui.add(
                    egui::Label::new(egui::RichText::new(&missing).monospace())
                        .selectable(true)
                        .wrap(),
                );
            });
        }
    });
    ui.add_space(6.0);

    if let Some(key) = open_earlier {
        action = BlockAction::OpenEarlier(key);
    }

    if tab == ExpandedTab::Fit {
        fit_breakdown(ui, r);
        return action;
    }

    if tab == ExpandedTab::Files {
        let chosen = crate::views::attachments::show(ui, files);
        if !matches!(chosen, crate::views::attachments::Action::None) {
            action = BlockAction::Files(chosen);
        }
        return action;
    }

    egui::ScrollArea::vertical()
        .id_source(("desc", &r.job.key))
        .max_height(EXPANDED_EXTRA - 120.0)
        .auto_shrink([false, false])
        .show(ui, |ui| {
            ui.indent("posting_body", |ui| {
                if description.trim().is_empty() {
                    ui.weak("No description was captured for this posting.");
                } else {
                    ui.add(
                        egui::Label::new(egui::RichText::new(&description).monospace())
                            .wrap(),
                    );
                }
            });
        });
    action
}

/// Why the Fit number says what it says.
///
/// The percentage is covered/asked for THIS posting - not a share of every
/// skill tracked - so a posting that never mentions a skill is not counted
/// against the resume. Spelling that out matters: a bare percentage invites
/// the reader to assume the wrong denominator.
fn fit_breakdown(ui: &mut egui::Ui, r: &crate::db::TriageRow) {
    let missing: Vec<&str> = fmt::opt_str(&r.job.missing_skills)
        .split(", ")
        .filter(|s| !s.trim().is_empty())
        .collect();
    match r.job.coverage_pct {
        None => {
            ui.weak(
                "Not assessed. Add the skills you want tracked on the Config tab, \
                 and attach a resume on the Resumes tab.",
            );
        }
        Some(pct) => {
            ui.horizontal(|ui| {
                ui.label(
                    egui::RichText::new(format!("{pct:.0}%"))
                        .size(26.0)
                        .strong()
                        .color(crate::theme::ACCENT),
                );
                ui.label("of what this posting asks for is already on your resume.");
            });
            ui.add_space(6.0);
            if missing.is_empty() {
                ui.label("Nothing missing. This posting asks for nothing your resume omits.");
            } else {
                ui.strong(format!(
                    "{} word{} to work in:",
                    missing.len(),
                    if missing.len() == 1 { "" } else { "s" }
                ));
                ui.add_space(4.0);
                ui.indent("fit_missing", |ui| {
                    for word in &missing {
                        ui.add(
                            egui::Label::new(egui::RichText::new(*word).monospace())
                                .selectable(true),
                        );
                    }
                });
                ui.add_space(8.0);
                ui.weak(
                    "Only claim what is true. A resume that passes a keyword screen \
                     and fails the interview is worse than no match at all.",
                );
            }
            let asks = fmt::opt_str(&r.job.requirements_summary);
            if !asks.is_empty() {
                ui.add_space(8.0);
                ui.label(format!("This posting also states: {asks}"));
            }
        }
    }
}

fn handle_keyboard(app: &mut UnlatchedApp, ctx: &egui::Context) {
    // A note text field steals every key while focused; ignore triage
    // shortcuts entirely while any widget in the window has keyboard focus
    // so typing a note never doubles as a status command.
    let something_focused = ctx.memory(|m| m.focused().is_some());

    // THE NOTE PROMPT SWALLOWS THE SHORTCUTS WHILE IT IS UP. Its note field is
    // deliberately not focused on open, so without this a person typing
    // "already applied" into it would fire 'a', 'd', 'p' and 'i' against the
    // rows behind the dialog - four status changes nobody asked for, made
    // while they thought they were writing a sentence.
    //
    // The prompt handles Enter and Escape itself; nothing else reaches here.
    if app.pending_status.is_some() {
        return;
    }

    if app.triage_note_open {
        if !something_focused && ctx.input(|i| i.key_pressed(Key::Escape)) {
            app.triage_note_open = false;
            app.triage_note_buffer.clear();
        }
        return;
    }

    if something_focused {
        return;
    }

    let (mut move_up, mut move_down, mut a, mut p, mut d, mut i_, mut n) =
        (false, false, false, false, false, false, false);
    let mut open_row = false;
    let mut pulled = false;
    ctx.input(|inp| {
        // Arrows are what a person reaches for in a list (decided 2026-08-07).
        // j/k stay as an alias for anyone who lives in vim, but they are no
        // longer the only way through - and neither fires while a widget has
        // focus, which is what keeps typing in an open description from
        // walking the selection.
        move_down = inp.key_pressed(Key::ArrowDown) || inp.key_pressed(Key::J);
        move_up = inp.key_pressed(Key::ArrowUp) || inp.key_pressed(Key::K);
        a = inp.key_pressed(Key::A);
        p = inp.key_pressed(Key::P);
        d = inp.key_pressed(Key::D);
        i_ = inp.key_pressed(Key::I);
        // 'c' used to set "closed", which is no longer a status somebody sets -
        // the app reads it from jobs.delisted_at. The key is NOT reassigned:
        // muscle memory would have kept pressing it, and any new meaning would
        // have been applied to rows the person believed they were closing.
        n = inp.key_pressed(Key::N);
        // 'o' OPENS THE HIGHLIGHTED ROW, and closes it again.
        //
        // Clicking a row already does this, but this screen is the
        // keyboard-driven one - up and down to move, a/p/d/i/n to act - and
        // reading the posting was the one thing in that loop that needed the
        // mouse. It is also what makes the open row REACHABLE BY A TEST: a
        // click depends on where a row happens to be on screen, a keystroke
        // does not.
        open_row = inp.key_pressed(Key::O);
        // 'x' reports that the advert closed. Not a status and not on the
        // a/p/d/i row of judgements: those say what the PERSON decided, this
        // says what the employer did, and both can be true of one job.
        pulled = inp.key_pressed(Key::X);
    });
    if move_down {
        app.move_selection(1);
    }
    if move_up {
        app.move_selection(-1);
    }
    if a {
        app.set_status_for_selected("applied");
    }
    if p {
        app.set_status_for_selected("pass");
    }
    if d {
        app.set_status_for_selected("no_offer");
    }
    if i_ {
        app.set_status_for_selected("interviewed");
    }
    if pulled {
        if let Some(key) = app.triage_selected.clone() {
            app.mark_taken_down(&key);
        }
    }
    if open_row {
        // Toggles, like the click does: pressing it again on the same row
        // closes the block rather than leaving the person to find the Close
        // button with the mouse they were not using.
        if let Some(key) = app.triage_selected.clone() {
            app.triage_expanded = if app.triage_expanded.as_deref() == Some(key.as_str()) {
                None
            } else {
                Some(key)
            };
        }
    }
    if n {
        // OPENS EMPTY. It used to be pre-filled with the job's standing note,
        // which made sense when saving REPLACED that note; now that notes
        // append, pre-filling would write the previous note a second time
        // unless the person remembered to clear the box first.
        app.triage_note_buffer.clear();
        app.triage_note_open = true;
        app.triage_note_just_opened = true;
    }
}

fn show_note_editor(app: &mut UnlatchedApp, ui: &mut egui::Ui) {
    let request_focus = app.triage_note_just_opened;
    app.triage_note_just_opened = false;
    ui.group(|ui| {
        let selected = app
            .triage_selected
            .as_ref()
            .and_then(|key| app.triage_rows.iter().find(|r| &r.job.key == key));
        let title = selected.map(|r| r.job.title.clone()).unwrap_or_default();
        let standing = selected.and_then(|r| r.note.clone());
        ui.label(format!("Note for: {title}"));
        // The last thing written, shown rather than loaded into the box.
        // Notes APPEND now, so putting it in the box would re-save it; showing
        // it means a person adding to a thread can see what they said last.
        if let Some(previous) = standing.as_deref().filter(|n| !n.trim().is_empty()) {
            ui.weak(format!("Last note: {}", fmt::truncate(previous, 80)))
                .on_hover_text(previous);
        }
        let edit_response = ui.text_edit_multiline(&mut app.triage_note_buffer);
        if request_focus {
            edit_response.request_focus();
        }
        ui.horizontal(|ui| {
            if ui.button("Save").clicked() {
                app.submit_note_for_selected();
            }
            if ui.button("Cancel (Esc)").clicked() {
                app.triage_note_open = false;
                app.triage_note_buffer.clear();
            }
        });
    });
}

/// Draw a sortable column heading. The arrow marks the active column and its
/// direction - a table that silently reorders when clicked, with nothing
/// showing why, reads as a glitch rather than a feature.
///
/// Senses clicks as well as drawing, because the containing cell senses them
/// too and it is not settled which one a click reaches - see the header loop.
fn sort_heading(
    ui: &mut egui::Ui,
    app: &UnlatchedApp,
    column: SortBy,
    label: &str,
) -> egui::Response {
    let active = app.triage_sort == column;
    let text = if active {
        format!("{label} {}", if app.triage_sort_desc { "v" } else { "^" })
    } else {
        label.to_string()
    };
    ui.add(
        egui::Label::new(egui::RichText::new(text).strong())
            // NOT SELECTABLE, and this is the whole bug. An egui label is
            // selectable TEXT by default, and a click that lands on the
            // letters starts a selection drag and is consumed there. From
            // the reader's side that is: clicking the word highlights the
            // word, and the column only sorts when the click lands beside
            // it. Beside the letters there is no label, so the click
            // reached the cell underneath and sorted. Nothing about a column
            // heading wants to be selectable text.
            .selectable(false)
            .sense(egui::Sense::click()),
    )
}
