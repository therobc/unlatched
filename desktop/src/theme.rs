//! One place that decides how the app looks.
//!
//! Before this, every screen used egui's defaults and set its own ad-hoc
//! spacing, which is what made the app read as a toolkit demo rather than a
//! product: one text size everywhere, no hierarchy, a 160px rail of bare
//! labels with no hover state, and controls at whatever size the toolkit
//! happened to pick.
//!
//! The numbers below are not invented. Current desktop practice puts a
//! navigation rail at 240-280px, navigation labels at 14-16px with 1.4-1.5
//! line height, and asks for an obvious active AND hover state, icons that
//! support labels rather than replace them, and a visible split between the
//! work you do daily and the things you set up once.
//! Sources: navbar.gallery's 2026 sidebar survey and alfdesigngroup's sidebar
//! UX guide, both recorded on an earlier change.
//!
//! NO ICON FONT. The bundled font has no glyph coverage for icon sets, and a
//! missing glyph renders as a tofu box. Where an icon would go, this draws a
//! shape with the painter, which cannot fail that way.

use eframe::egui;

/// Navigation rail. 240 is the low end of current practice and the right end
/// for this app: the labels are short, and a wider rail would take width from
/// a table that already has fourteen columns.
pub const RAIL_WIDTH: f32 = 240.0;

/// Type scale. One size for everything is the single loudest amateur tell;
/// these are the four roles this app actually has.
pub const TEXT_TITLE: f32 = 21.0;
pub const TEXT_BODY: f32 = 14.0;
pub const TEXT_NAV: f32 = 14.5;
pub const TEXT_LABEL: f32 = 10.5;

/// Accent, used for the active navigation item and primary emphasis. Mid-tone
/// so one value carries on both a near-white and a near-black background -
/// the same constraint the status palette follows.
pub const ACCENT: egui::Color32 = egui::Color32::from_rgb(59, 130, 246);

pub fn apply(ctx: &egui::Context, dark: bool) {
    let mut visuals = if dark {
        egui::Visuals::dark()
    } else {
        egui::Visuals::light()
    };

    // Softer, consistent corners. egui's default mixes several radii, which
    // reads as unfinished rather than as deliberate variety.
    let rounding = egui::Rounding::same(5.0);
    visuals.widgets.noninteractive.rounding = rounding;
    visuals.widgets.inactive.rounding = rounding;
    visuals.widgets.hovered.rounding = rounding;
    visuals.widgets.active.rounding = rounding;
    visuals.widgets.open.rounding = rounding;
    visuals.window_rounding = egui::Rounding::same(7.0);
    visuals.menu_rounding = rounding;

    // A selection colour of our own rather than the toolkit's default blue,
    // so selection, the active nav item and primary emphasis are one colour.
    visuals.selection.bg_fill = ACCENT.gamma_multiply(if dark { 0.55 } else { 0.30 });
    visuals.selection.stroke = egui::Stroke::new(1.0, ACCENT);

    // Quieter borders. Heavy 1px outlines on every widget are what makes a
    // dense table look like a spreadsheet from 1998.
    let line = if dark {
        egui::Color32::from_gray(58)
    } else {
        egui::Color32::from_gray(214)
    };
    visuals.widgets.noninteractive.bg_stroke = egui::Stroke::new(1.0, line);
    visuals.widgets.inactive.bg_stroke = egui::Stroke::new(1.0, line);

    ctx.set_visuals(visuals);

    let mut style = (*ctx.style()).clone();
    style.text_styles = [
        (egui::TextStyle::Heading, egui::FontId::proportional(TEXT_TITLE)),
        (egui::TextStyle::Body, egui::FontId::proportional(TEXT_BODY)),
        (egui::TextStyle::Button, egui::FontId::proportional(TEXT_BODY)),
        (egui::TextStyle::Small, egui::FontId::proportional(TEXT_LABEL)),
        (egui::TextStyle::Monospace, egui::FontId::monospace(TEXT_BODY - 1.0)),
    ]
    .into();

    // Breathing room. The defaults are tuned for tool panels, not for a
    // window someone reads for an hour.
    style.spacing.item_spacing = egui::vec2(8.0, 7.0);
    style.spacing.button_padding = egui::vec2(10.0, 5.0);
    style.spacing.menu_margin = egui::Margin::same(8.0);
    style.spacing.indent = 18.0;
    style.spacing.scroll.bar_width = 10.0;

    ctx.set_style(style);
}

/// A navigation row: full-width hit area, hover fill, and an accent bar down
/// the left edge when active.
///
/// egui's `selectable_label` sizes itself to its text, so the rail was a
/// column of differently-sized boxes with no hover feedback at all - a control
/// that does not respond to the pointer reads as decoration, not a button.
pub fn nav_row(ui: &mut egui::Ui, label: &str, active: bool) -> egui::Response {
    let height = 30.0;
    let (rect, response) = ui.allocate_exact_size(
        egui::vec2(ui.available_width(), height),
        egui::Sense::click(),
    );

    if active {
        ui.painter()
            .rect_filled(rect, 5.0, ui.visuals().selection.bg_fill);
        // The accent bar. Reads as "you are here" even for someone who cannot
        // separate the fill from the background.
        ui.painter().rect_filled(
            egui::Rect::from_min_size(rect.min + egui::vec2(0.0, 4.0), egui::vec2(3.0, height - 8.0)),
            1.5,
            ACCENT,
        );
    } else if response.hovered() {
        ui.painter()
            .rect_filled(rect, 5.0, ui.visuals().widgets.hovered.bg_fill);
    }

    let text_colour = if active {
        ui.visuals().strong_text_color()
    } else {
        ui.visuals().text_color()
    };
    ui.painter().text(
        rect.left_center() + egui::vec2(14.0, 0.0),
        egui::Align2::LEFT_CENTER,
        label,
        egui::FontId::proportional(TEXT_NAV),
        text_colour,
    );
    // The whole rail is painted, so without this every navigation entry is
    // absent from the accessibility tree - the controls a person uses most
    // would be the ones the app never announced. The value carries which
    // screen is current, which the accent bar conveys to sighted users only.
    let response = crate::access::tag_with_value(
        response,
        egui::WidgetType::SelectableLabel,
        format!("nav-{}", crate::access::slug(label)),
        if active { "current" } else { "not current" },
    );
    response.on_hover_cursor(egui::CursorIcon::PointingHand)
}

/// A small upper-case group heading for the rail. Groups are the difference
/// between a flat list of links and a navigation structure.
pub fn nav_heading(ui: &mut egui::Ui, text: &str) {
    ui.add_space(6.0);
    ui.label(
        egui::RichText::new(text)
            .size(TEXT_LABEL)
            .strong()
            .color(ui.visuals().weak_text_color()),
    );
    ui.add_space(2.0);
}
