//! The landing view: what changed, what needs a decision, how the search is doing.
//!
//! Every tile is a count and a route. Nothing here opens a job detail of its
//! own - clicking a tile lands in Triage, Pipeline, Companies or Keywords with
//! a filter applied, so there is exactly one implementation of each screen.
//! The full sortable list stays in its own tab (decided 2026-08-05): this screen
//! summarises and routes, Triage is where scanning happens.
//!
//! LAYOUT is taken from the reference dashboard in MadsLorentzen/ai-job-search
//! (read from the recording, not from its spec sketch): cards on a panel
//! background, each with a small upper-case section label; a doughnut carrying
//! the total in its centre with a counted legend beside it; horizontal bars
//! with the value right-aligned at the end of the row; and a derived sentence
//! under the funnel rather than another number.
//!
//! Charts are drawn with the egui painter. A bar is a rectangle and a doughnut
//! is a fan of triangles; pulling in a charting crate, its fonts and its layout
//! model to draw those would cost more than it saves, and the app deliberately
//! compiles everything it runs.

use eframe::egui;

use crate::app::{UnlatchedApp, View};
use crate::dashboard::{DashboardStats, SILENT_DAYS};
/// Card accents. These are NOT the status palette - that lives in
/// crate::status and is keyed to a status value. These name the tone a CARD
/// carries (something good, something waiting, something gone), which is a
/// different question: "taken down" is not a status anybody sets.
const ACTIVE: egui::Color32 = egui::Color32::from_rgb(59, 130, 246);
const INTERVIEW: egui::Color32 = egui::Color32::from_rgb(245, 158, 11);
const HIRED: egui::Color32 = egui::Color32::from_rgb(34, 197, 94);
const NEUTRAL: egui::Color32 = egui::Color32::from_rgb(120, 130, 150);

/// Twice a normal card. The status doughnut and its counted legend need the
/// room; at half width the legend sat on top of the ring.
const BIG_CARD_HEIGHT: f32 = 210.0;

/// The rungs of the ladder, top to bottom. Read by the funnel, which has to
/// read as a sequence rather than as counts sorted by size.
///
/// Accepted Offer sits between Offer and Hired because it is a stage a person
/// genuinely occupies - sometimes for weeks, waiting on a check - and folding
/// it into either neighbour would report a job they have not started or lose
/// the fact that they said yes.
const FUNNEL: [(&str, &str); 5] = [
    ("applied", "Applied"),
    ("interviewed", "Interview"),
    ("offer", "Offer"),
    ("accepted_offer", "Accepted"),
    ("hired", "Hired"),
];

/// Both of these were hand-written tables here. They now read crate::status,
/// so a status is one colour and one word everywhere in the app.
fn status_colour(value: &str) -> egui::Color32 {
    let [r, g, b] = crate::status::colour(value);
    egui::Color32::from_rgb(r, g, b)
}

fn status_label(value: &str) -> String {
    crate::status::label(value)
}

pub fn show(app: &mut UnlatchedApp, ui: &mut egui::Ui) {
    let Some(stats) = app.dashboard_stats.clone() else {
        ui.label("Loading...");
        return;
    };

    ui.horizontal(|ui| {
        ui.heading("Dashboard");
        ui.add_space(10.0);
        if ui.button("Refresh").clicked() {
            app.refresh_dashboard();
        }
        ui.add_space(10.0);
        ui.weak(crate::fmt::collected_line(stats.last_collected.as_deref()));
        // Right next to the freshness line, because that line is what a
        // person reads to decide whether the list in front of them is
        // current - and for hand-added links it would otherwise be
        // answering for the boards only (decided 2026-08-06).
        // Cached, not queried while drawing (see UnlatchedApp::manual_links).
        let links = app.manual_links;
        if links.stale_since_collect {
            ui.add_space(10.0);
            ui.colored_label(
                egui::Color32::from_rgb(217, 164, 65),
                format!(
                    "{} added link{} not re-checked",
                    links.total,
                    if links.total == 1 { "" } else { "s" }
                ),
            )
            .on_hover_text(
                "Links you added by hand are re-read only when you ask, on the \
                 Triage screen. The scheduled refresh covers the employer boards \
                 and job sources the app ships with.",
            );
        }
        ui.add_space(10.0);
        ui.weak(format!(
            "{} match{} kept of {} collected",
            stats.keeps,
            if stats.keeps == 1 { "" } else { "es" },
            stats.jobs_total
        ));
    });
    ui.add_space(8.0);

    egui::ScrollArea::vertical().show(ui, |ui| {
        stat_cards(app, ui, &stats);

        if stats.nothing_collected() {
            ui.add_space(14.0);
            card(ui, "GETTING STARTED", |ui| {
                ui.label(
                    "Nothing has been collected yet. Add employers on the Companies \
                     page, then open Collect there and choose Every employer - the \
                     rest of this screen fills in once there are jobs to talk about.",
                );
                ui.add_space(4.0);
                if ui.button("Go to Companies").clicked() {
                    app.view = View::Companies;
                }
            });
            return;
        }

        ui.add_space(12.0);
        // STATUS BREAKDOWN gets the full width and twice the height: the
        // doughnut plus its legend is the densest thing on the screen, and at
        // half width the legend crowded the ring.
        card(ui, "STATUS BREAKDOWN", |ui| {
            ui.set_min_height(BIG_CARD_HEIGHT);
            pipeline_doughnut(app, ui, &stats);
        });

        ui.add_space(10.0);
        ui.columns(2, |cols| {
            cols[0].push_id("funnel_card", |ui| {
                card(ui, "APPLICATION FUNNEL", |ui| funnel(ui, &stats));
            });
            cols[1].push_id("gaps_card", |ui| {
                card(ui, "WORDS YOUR RESUME IS MISSING", |ui| {
                    if stats.top_gaps.is_empty() {
                        ui.weak(
                            "Nothing to report. Set the skills you want tracked on the \
                             Config tab and attach a resume on the Resumes tab.",
                        );
                    } else {
                        let bars: Vec<(String, i64, egui::Color32)> = stats
                            .top_gaps
                            .iter()
                            .map(|(skill, count)| (skill.clone(), *count, INTERVIEW))
                            .collect();
                        bar_chart(ui, &bars);
                        ui.add_space(2.0);
                        ui.weak("Counted across the jobs worth having. Work the true ones in.");
                        ui.horizontal(|ui| {
                            if ui.small_button("Open Keywords").clicked() {
                                app.view = View::Keywords;
                            }
                        });
                    }
                });
            });
        });

        ui.add_space(10.0);
        ui.columns(2, |cols| {
            cols[0].push_id("source_card", |ui| {
                card(ui, "WHERE YOUR MATCHES COME FROM", |ui| {
                    let bars: Vec<(String, i64, egui::Color32)> = stats
                        .by_source
                        .iter()
                        .map(|(name, count)| (name.clone(), *count, ACTIVE))
                        .collect();
                    if bars.is_empty() {
                        ui.weak("No matches yet.");
                    } else {
                        bar_chart(ui, &bars);
                    }
                });
            });
            cols[1].push_id("coverage_card", |ui| {
                card(ui, "EMPLOYER COVERAGE", |ui| coverage(app, ui, &stats));
            });
        });

    });
}

// -------------------------------------------------------------- stat cards ---

/// The row of clickable counts across the top, in the reference's shape: a
/// large figure, a small label under it, and a coloured left border tying the
/// card to what it counts.
fn stat_cards(app: &mut UnlatchedApp, ui: &mut egui::Ui, stats: &DashboardStats) {
    // ONE CARD PER MODULE, GENERATED. Each was hand-written with its own count
    // and its own click behaviour, which is how AWAITING A REPLY came to count
    // applications silent for 14+ days while its click went to a screen
    // counting something else. Now the number, the colour, the words and the
    // list all come from the same definition - add a module and the card, the
    // donut segment and the list arrive together.
    let mut open: Option<crate::modules::Module> = None;
    ui.horizontal_wrapped(|ui| {
        for module in crate::modules::MODULES {
            let count = stats.module_counts.get(&module.key()).copied().unwrap_or(0);
            let [r, g, b] = module.colour();
            stat(
                ui,
                count,
                &module.label(),
                egui::Color32::from_rgb(r, g, b),
                &module.caption(),
                &module.key(),
                || open = Some(module),
            );
        }
    });
    if let Some(module) = open {
        app.open_module(module);
    }

    // Deliberately a sentence, not a sixth card. "A job you applied to was
    // pulled" is a different kind of event from a listing you never acted on
    // expiring, and it is worth interrupting someone for.
    if stats.withdrawn_after_applying > 0 {
        ui.add_space(4.0);
        let module = crate::modules::Module::WithdrawnAfterApplying;
        let [r, g, b] = module.colour();
        let text = format!(
            "{} job{} you applied to {} been taken down.",
            stats.withdrawn_after_applying,
            if stats.withdrawn_after_applying == 1 { "" } else { "s" },
            if stats.withdrawn_after_applying == 1 { "has" } else { "have" },
        );
        // A LINK RATHER THAN A LABEL. The count on its own is a dead end - the
        // person already knows something went wrong and still has to find the
        // rows by hand. What they came to do is close them out: record the
        // rejection that did arrive, or give up on the ones that stayed silent.
        let clicked = crate::access::tag_with_value(
            ui.add(egui::Link::new(
                egui::RichText::new(&text).color(egui::Color32::from_rgb(r, g, b)),
            )),
            egui::WidgetType::Link,
            format!("module-{}", module.key()),
            stats.withdrawn_after_applying.to_string(),
        )
        .on_hover_text(
            "Open these, to record the rejections you were sent and give up on \
             the ones that stayed silent.",
        )
        .clicked();
        if clicked {
            app.open_module(module);
        }
    }
    if stats.waiting_on_reply > 0 {
        ui.weak(format!(
            "{} application{} silent for {SILENT_DAYS}+ days. Applications are lost to \
             silence far more often than to a rejection.",
            stats.waiting_on_reply,
            if stats.waiting_on_reply == 1 { "" } else { "s" },
        ));
    }
}

fn stat(
    ui: &mut egui::Ui,
    value: i64,
    label: &str,
    colour: egui::Color32,
    caption: &str,
    name: &str,
    mut on_click: impl FnMut(),
) {
    let response = egui::Frame::none()
        .inner_margin(egui::Margin {
            left: 14.0,
            right: 18.0,
            top: 8.0,
            bottom: 8.0,
        })
        .rounding(egui::Rounding::same(4.0))
        .fill(ui.visuals().faint_bg_color)
        .show(ui, |ui| {
            ui.vertical(|ui| {
                // THE NAME AND THE COUNT GO ON THIS LABEL, a real laid-out
                // widget, NOT on the interact() overlay below.
                //
                // They were on the overlay, and it hung the accessibility tree
                // outright: the first UIA walk of every run blocked and three
                // harness runs had to be killed. An interact() region is not a
                // widget egui has placed - it is a hit area over one already
                // drawn - so attaching widget_info to nine of them per frame
                // produced a tree the provider could not answer for.
                //
                // Diagnosed by breadcrumb, not by reading: the harness now
                // records each step to disk, and it stopped at exactly
                // "walking the accessibility tree" every time.
                crate::access::tag_with_value(
                    ui.label(
                        egui::RichText::new(value.to_string())
                            .size(26.0)
                            .strong()
                            .color(colour),
                    ),
                    egui::WidgetType::Label,
                    format!("module-{name}"),
                    value.to_string(),
                );
                ui.label(egui::RichText::new(label).size(9.5).weak());
            });
        })
        .response;

    // The coloured left border of the reference's stat cards, painted after
    // the frame so it sits over the fill rather than under it.
    let bar = egui::Rect::from_min_size(
        response.rect.min,
        egui::vec2(3.0, response.rect.height()),
    );
    ui.painter().rect_filled(bar, 1.0, colour);

    // interact() over the whole frame: a number you can see but not click is
    // a dead end, and the figure is a bigger target than any button on it.
    //
    // DELIBERATELY UNTAGGED - see the label above. The card's name and count
    // live on the label, which is a widget egui actually laid out.
    let hit = ui
        .interact(response.rect, ui.id().with(name), egui::Sense::click())
        .on_hover_cursor(egui::CursorIcon::PointingHand)
        .on_hover_text(caption);
    if hit.clicked() {
        on_click();
    }
    ui.add_space(8.0);
}

// ------------------------------------------------------------------- cards ---

/// A titled panel. The upper-case label is small and quiet on purpose: it
/// names the card without competing with the figures inside it.
fn card(ui: &mut egui::Ui, title: &str, contents: impl FnOnce(&mut egui::Ui)) {
    egui::Frame::none()
        .inner_margin(egui::Margin::same(12.0))
        .rounding(egui::Rounding::same(4.0))
        .stroke(egui::Stroke::new(1.0, ui.visuals().widgets.noninteractive.bg_stroke.color))
        .show(ui, |ui| {
            // A Frame shrinks to its content, so a card holding a narrow chart
            // ended up a quarter of the width of the card under it and the
            // grid stopped reading as a grid. Cards fill their column.
            ui.set_min_width(ui.available_width());
            ui.label(
                egui::RichText::new(title)
                    .size(9.5)
                    .strong()
                    .color(ui.visuals().weak_text_color()),
            );
            ui.add_space(6.0);
            contents(ui);
        });
}

// ---------------------------------------------------------------- doughnut ---

fn pipeline_doughnut(app: &mut UnlatchedApp, ui: &mut egui::Ui, stats: &DashboardStats) {
    if stats.nothing_applied_to() {
        ui.weak("Nothing applied to yet. Statuses you set in Triage show up here.");
        return;
    }
    let slices: Vec<(String, i64, egui::Color32)> = stats
        .by_status
        .iter()
        .map(|(value, count)| (status_label(value), *count, status_colour(value)))
        .collect();
    let total: i64 = slices.iter().map(|(_, n, _)| *n).sum();

    ui.horizontal(|ui| {
        doughnut(ui, &slices, total);
        ui.add_space(10.0);
        ui.vertical(|ui| {
            for (label, count, colour) in &slices {
                ui.horizontal(|ui| {
                    let (rect, _) =
                        ui.allocate_exact_size(egui::vec2(9.0, 9.0), egui::Sense::hover());
                    ui.painter().rect_filled(rect, 2.0, *colour);
                    ui.label(label);
                    ui.weak(count.to_string());
                });
            }
            ui.add_space(4.0);
            ui.horizontal(|ui| {
                if ui.small_button("Open Pipeline").clicked() {
                    app.view = View::Pipeline;
                }
            });
        });
    });
}

/// A ring with the total in the middle, drawn as a fan of triangles per slice
/// and then punched out with a centre disc in the panel colour.
///
/// The centre figure is the point: the reference puts the total inside the
/// ring so the eye lands on "how many applications" before it lands on the
/// proportions, and that is the right order for this number.
fn doughnut(ui: &mut egui::Ui, slices: &[(String, i64, egui::Color32)], total: i64) {
    const SIZE: f32 = 150.0;
    const SEGMENTS_PER_TURN: usize = 96;

    // The one painted figure on this screen with no text equivalent beside it.
    // Every other meter here - the legend dots, the source bars, the coverage
    // strip - sits next to a real label carrying the same number, so naming
    // them would announce every figure twice. The ring's centre total is only
    // ever painted, so without this it is unreadable to anything but an eye.
    let (rect, response) = ui.allocate_exact_size(egui::vec2(SIZE, SIZE), egui::Sense::hover());
    crate::access::tag_with_value(
        response,
        egui::WidgetType::Label,
        "pipeline-total",
        format!("{total} applications"),
    );
    let centre = rect.center();
    let radius = SIZE * 0.5 - 2.0;
    let painter = ui.painter();

    if total <= 0 {
        painter.circle_filled(centre, radius, NEUTRAL.gamma_multiply(0.3));
    } else {
        // Start at twelve o'clock so the largest slice begins where a reader
        // expects a chart to start, rather than at three o'clock.
        let mut angle = -std::f32::consts::FRAC_PI_2;
        for (_, count, colour) in slices {
            let sweep = std::f32::consts::TAU * (*count as f32 / total as f32);
            let steps = ((sweep / std::f32::consts::TAU) * SEGMENTS_PER_TURN as f32).ceil() as usize;
            let steps = steps.max(1);
            let mut points = Vec::with_capacity(steps + 2);
            points.push(centre);
            for step in 0..=steps {
                let a = angle + sweep * (step as f32 / steps as f32);
                points.push(centre + egui::vec2(a.cos(), a.sin()) * radius);
            }
            painter.add(egui::Shape::convex_polygon(
                points,
                *colour,
                egui::Stroke::NONE,
            ));
            angle += sweep;
        }
    }

    // The hole. Filled with the window background so the ring reads as a ring
    // in both themes rather than as a pie with a grey dot on it.
    painter.circle_filled(centre, radius * 0.62, ui.visuals().panel_fill);
    painter.text(
        centre - egui::vec2(0.0, 9.0),
        egui::Align2::CENTER_CENTER,
        total.to_string(),
        egui::FontId::proportional(30.0),
        ui.visuals().strong_text_color(),
    );
    painter.text(
        centre + egui::vec2(0.0, 14.0),
        egui::Align2::CENTER_CENTER,
        if total == 1 { "application" } else { "applications" },
        egui::FontId::proportional(11.0),
        ui.visuals().weak_text_color(),
    );
}

// ------------------------------------------------------------------ funnel ---

/// Applied -> Interview -> Offer -> Hired, in pipeline order, with the rates
/// underneath as a sentence.
///
/// Every stage counts everyone who REACHED it, so the bars can only shrink -
/// counting only those currently sitting at a stage produces a funnel where
/// the middle is wider than the top, which is nonsense to look at.
fn funnel(ui: &mut egui::Ui, stats: &DashboardStats) {
    if stats.nothing_applied_to() {
        ui.weak("Nothing applied to yet.");
        return;
    }
    // Counted in dashboard::reached_from_log, which the pipeline summary reads
    // too. Two screens deriving "how many applications" from the same table by
    // different arithmetic is how they came to contradict each other.
    let r = stats.reached;
    let reached = [r.applied, r.interviewed, r.offer, r.accepted, r.hired];

    // The bar takes the colour of the status it counts, from the one palette,
    // so a rung here and a pill on the pipeline are recognisably the same
    // thing rather than two designs that happen to agree.
    let bars: Vec<(String, i64, egui::Color32)> = FUNNEL
        .iter()
        .enumerate()
        .map(|(i, (value, label))| {
            ((*label).to_string(), reached[i], status_colour(value))
        })
        .collect();
    bar_chart(ui, &bars);

    ui.add_space(4.0);
    if r.applied > 0 {
        let past_screen = 100.0 * r.interviewed as f32 / r.applied as f32;
        ui.weak(format!(
            "At least {past_screen:.0}% got past the resume screen. Counts everyone \
             known to have reached each stage, so it is a floor."
        ));
    }
}

// -------------------------------------------------------------- bar charts ---

/// Horizontal bars with the value right-aligned at the end of the row.
///
/// Bars are sized against the LARGEST value, not the total: with a total, one
/// dominant category flattens every other bar into an invisible sliver, and
/// the comparison between the small ones is usually the interesting part.
fn bar_chart(ui: &mut egui::Ui, bars: &[(String, i64, egui::Color32)]) {
    let max = bars.iter().map(|(_, n, _)| *n).max().unwrap_or(0).max(1);
    let full = (ui.available_width() - 190.0).max(50.0);
    for (label, count, colour) in bars {
        ui.horizontal(|ui| {
            ui.add_sized(
                [132.0, 15.0],
                egui::Label::new(crate::fmt::truncate(label, 24)).truncate(),
            );
            let width = full * (*count as f32 / max as f32);
            let (rect, _) =
                ui.allocate_exact_size(egui::vec2(width.max(2.0), 13.0), egui::Sense::hover());
            ui.painter().rect_filled(rect, 2.0, *colour);
            ui.label(egui::RichText::new(count.to_string()).weak());
        });
        ui.add_space(2.0);
    }
}

// ---------------------------------------------------------------- coverage ---

/// The number that explains a thin result set, and the one the reference has
/// no equivalent for because it has no collector. Every disappointing search
/// this project produced traced back to it: one seeker matched a single job
/// because four of her twenty-seven employers had a board we could read.
fn coverage(app: &mut UnlatchedApp, ui: &mut egui::Ui, stats: &DashboardStats) {
    let unreadable = stats.employers_total - stats.employers_readable;
    ui.horizontal(|ui| {
        ui.label(
            egui::RichText::new(format!(
                "{} of {}",
                stats.employers_readable, stats.employers_total
            ))
            .size(20.0)
            .strong()
            .color(if unreadable > 0 { INTERVIEW } else { HIRED }),
        );
        ui.label("of your employers have a job board we can read.");
    });
    if unreadable > 0 && stats.employers_total > 0 {
        let readable = stats.employers_readable as f32 / stats.employers_total as f32;
        let full = (ui.available_width() - 120.0).max(60.0);
        ui.horizontal(|ui| {
            let (rect, _) =
                ui.allocate_exact_size(egui::vec2(full * readable, 10.0), egui::Sense::hover());
            ui.painter().rect_filled(rect, 2.0, HIRED);
            let (rest, _) = ui.allocate_exact_size(
                egui::vec2(full * (1.0 - readable), 10.0),
                egui::Sense::hover(),
            );
            ui.painter().rect_filled(rest, 2.0, NEUTRAL.gamma_multiply(0.4));
        });
        ui.weak(format!(
            "{unreadable} have no board we can collect from, so nothing they post can \
             appear here. That is usually why a search looks thin."
        ));
    }
    ui.add_space(4.0);
    ui.horizontal(|ui| {
        if ui.small_button("Open Companies").clicked() {
            app.view = View::Companies;
        }
    });
}
