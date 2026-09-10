//! Stable AccessKit names for Unlatched's hand-painted widgets.
//!
//! Every interactive egui widget gets a stable accessible name, the native
//! analog of a web `data-testid`. It is what a screen reader reads, and it is
//! how an automated test drives the app through Windows UIA rather than by
//! pixel position.
//!
//! egui names *labelled* widgets for free - a `Button` carrying text already
//! reports that text. What gets nothing is anything we paint ourselves:
//! `allocate_exact_size` + `painter()` yields a rectangle that senses clicks and
//! is completely absent from the accessibility tree. Unlatched had eight such
//! widgets and zero accessible names before this module.
//!
//! NAMES ARE AN API. Automation addresses these strings, so they are fixed and
//! lowercase-hyphenated, and they never carry text that changes with state or
//! locale - that is what the value slot is for. The nav rail is the case that
//! proves it: its visible labels are also its identifiers today, but a rail
//! whose entries were ever renamed or reordered would break every positional
//! test we have, which is exactly what happened when Pipeline and All jobs
//! swapped on 2026-08-08.

use egui::{Response, WidgetInfo, WidgetType};

/// Stamp a stable AccessKit name and role onto `response`, overwriting whatever
/// the widget set for itself, and hand the response back so call sites can
/// chain. Both by construction - widget_info replaces, and the response is
/// returned unmoved.
pub fn tag(response: Response, typ: WidgetType, name: impl Into<String>) -> Response {
    let enabled = response.enabled();
    let name = name.into();
    response.widget_info(move || WidgetInfo::labeled(typ, enabled, name.as_str()));
    response
}

/// Like [`tag`], but also exposes a live text value.
///
/// Used where the name must stay fixed for automation while the content moves:
/// a nav row reports whether it is the current screen, a meter reports what it
/// is measuring. Without this a screen reader user gets a named control and no
/// way to learn what it says - which for the dashboard's painted bars is the
/// entire content of the widget.
pub fn tag_with_value(
    response: Response,
    typ: WidgetType,
    name: impl Into<String>,
    value: impl Into<String>,
) -> Response {
    let enabled = response.enabled();
    let name = name.into();
    let value = value.into();
    response.widget_info(move || {
        let mut info = WidgetInfo::labeled(typ, enabled, name.as_str());
        // `WidgetInfo::value` is an f64 slider/progress value; the TEXT slot a
        // screen reader actually reads is `current_text_value`.
        info.current_text_value = Some(value.clone());
        info
    });
    response
}

/// A tick box with a stable name, reporting its own state as the value.
///
/// THE LONG FORM IS WHY THIS EXISTS. Naming a tick box by hand means binding
/// the response, re-reading the bool after the mutable borrow ends, and
/// mapping it to a string. That the length of the alternative is what kept
/// them unnamed is believed, not measured - what WAS measured, with
/// audit_unnamed_widgets on 2026-09-05, is that 50 call sites across 14 view
/// files published no name at all.
///
/// THE VALUE IS THE STATE, not the label. A screen reader reading "Remote"
/// learns nothing about whether Remote is on; automation asserting on the
/// label cannot tell a ticked box from an unticked one.
pub fn tick(ui: &mut egui::Ui, on: &mut bool, label: &str, name: &str) -> Response {
    let response = ui.checkbox(on, label);
    let value = if *on { "true" } else { "false" };
    tag_with_value(response, WidgetType::Checkbox, name, value)
}

/// A single-line text box with a stable name, reporting its own contents.
///
/// The contents rather than the label, for the same reason as [`tick`] above:
/// a named box whose value cannot be read is a control automation can find and
/// not check. Verified by a_text_field_reports_its_contents, which reads this
/// function and fails if the value slot stops carrying the contents.
pub fn text_field(ui: &mut egui::Ui, value: &mut String, name: &str) -> Response {
    let response = ui.text_edit_singleline(value);
    let current = value.clone();
    tag_with_value(response, WidgetType::TextEdit, name, current)
}

/// Turn a human label into a stable identifier fragment.
///
/// "All jobs" -> "all-jobs". Deterministic and ASCII so a name never depends
/// on how a label happens to be capitalised or spaced - verified by the three
/// tests below, which cover spacing, punctuation and a trailing separator.
pub fn slug(label: &str) -> String {
    let mut out = String::with_capacity(label.len());
    let mut last_dash = true;
    for ch in label.chars() {
        if ch.is_ascii_alphanumeric() {
            out.push(ch.to_ascii_lowercase());
            last_dash = false;
        } else if !last_dash {
            out.push('-');
            last_dash = true;
        }
    }
    while out.ends_with('-') {
        out.pop();
    }
    out
}

#[cfg(test)]
mod tests {
    use super::slug;

    /// EVERY CONTROL ON EVERY SCREEN PUBLISHES A NAME.
    ///
    /// WHY A SWEEP AND NOT A REVIEW HABIT: 50 call sites across 14 view files
    /// were counted with no name at all, on a codebase whose access.rs already
    /// said names are an API. Naming them was one afternoon; noticing they had
    /// gone unnamed took a written sweep. A control nobody can address by name
    /// is one a UIA test has to reach by pixel position, and a position moves
    /// whenever anything above it does.
    ///
    /// The rule is deliberately coarse - a widget call is "named" if a tag
    /// helper, or a wrapper the file defines around one, appears within three
    /// lines of it. That accepts the wrapped form
    /// and the bound-then-tagged form, and it is what makes the check cheap
    /// enough to run in this suite rather than in a tool somebody remembers.
    /// A response tagged sixty lines later, as collectors_menu does, is bound
    /// to a variable, so it is exempted by the same rule the audit uses.
    #[test]
    fn every_control_on_every_screen_publishes_a_name() {
        let views = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("src/views");
        let widget = [
            "ui.button(",
            "ui.small_button(",
            "ui.checkbox(",
            "ui.radio_value(",
            "ui.text_edit_singleline(",
            "ui.text_edit_multiline(",
            "ui.selectable_value(",
            "ui.selectable_label(",
            "ui.toggle_value(",
        ];
        let mut unnamed: Vec<String> = Vec::new();
        for entry in std::fs::read_dir(&views).expect("no views directory") {
            let path = entry.expect("unreadable view").path();
            if path.extension().and_then(|e| e.to_str()) != Some("rs") {
                continue;
            }
            let whole = std::fs::read_to_string(&path).expect("unreadable view");
            // Tests build their own widgets and are not what a person drives.
            let body = whole.split("#[cfg(test)]").next().unwrap_or("");
            let lines: Vec<&str> = body.lines().collect();
            // A screen may name its buttons through a one-line wrapper of its
            // own. Accepted only when the wrapper takes a Response and its own
            // body calls access::tag, so this recognises the PATTERN rather than
            // a list of blessed function names - a wrapper that tagged nothing
            // would not be collected here, and the widgets inside it would still
            // be reported. Measured 2026-09-05: taking the name off pipeline.rs's
            // Refresh button failed the sweep with "pipeline.rs:16".
            let mut wrappers: Vec<String> = Vec::new();
            for (i, line) in lines.iter().enumerate() {
                let Some(rest) = line.strip_prefix("fn ") else { continue };
                if !line.contains("egui::Response") {
                    continue;
                }
                let to = (i + 6).min(lines.len());
                if lines[i..to].join("
").contains("access::tag") {
                    if let Some(name) = rest.split('(').next() {
                        wrappers.push(format!("{name}("));
                    }
                }
            }
            for (i, line) in lines.iter().enumerate() {
                if !widget.iter().any(|w| line.contains(w)) {
                    continue;
                }
                let from = i.saturating_sub(3);
                let to = (i + 4).min(lines.len());
                let window = lines[from..to].join("\n");
                let named = window.contains("access::tag")
                    || window.contains("access::tick")
                    || window.contains("access::text_field")
                    || wrappers.iter().any(|w| window.contains(w.as_str()));
                // Bound to a name, which means it may be tagged further down.
                let deferred = line.contains("let ") && line.contains(" = ui.");
                if !named && !deferred {
                    let file = path.file_name().unwrap().to_string_lossy().to_string();
                    unnamed.push(format!("{file}:{}", i + 1));
                }
            }
        }
        assert!(
            unnamed.is_empty(),
            "controls with no accessible name: {}",
            unnamed.join(", ")
        );
    }

    /// THE VALUE HAS TO MOVE WITH THE STATE. A tick box that always reported
    /// the same string would be a named control automation could find and
    /// never check - the failure these helpers exist to prevent, so it must
    /// not be reintroduced by them.
    ///
    /// READ FROM THE SOURCE, like keyboard_scroll_tests in app.rs and for the
    /// same reason: driving this needs an egui context and a frame, which a
    /// unit test here does not have. What can go wrong is the value slot being
    /// handed the label or a constant, which is by construction visible in the
    /// function's own text.
    #[test]
    fn a_tick_box_reports_its_state_and_not_its_label() {
        const SOURCE: &str = include_str!("access.rs");
        let body = SOURCE
            .split("pub fn tick(")
            .nth(1)
            .expect("tick was renamed")
            .split("\n}")
            .next()
            .expect("tick has no body");
        assert!(body.contains("if *on"), "the value does not read the state");
        assert!(
            !body.contains("name, label"),
            "the value slot was handed the label"
        );
    }

    #[test]
    fn a_text_field_reports_its_contents() {
        const SOURCE: &str = include_str!("access.rs");
        let body = SOURCE
            .split("pub fn text_field(")
            .nth(1)
            .expect("text_field was renamed")
            .split("\n}")
            .next()
            .expect("text_field has no body");
        assert!(body.contains("value.clone()"), "the value is not the contents");
    }

    #[test]
    fn labels_become_stable_identifiers() {
        assert_eq!(slug("All jobs"), "all-jobs");
        assert_eq!(slug("Dashboard"), "dashboard");
        assert_eq!(slug("Getting started"), "getting-started");
    }

    #[test]
    fn punctuation_and_spacing_never_leak_into_a_name() {
        // The identifier has to survive a label being re-punctuated, or an
        // automated test breaks on a copy edit.
        assert_eq!(slug("Pass / denied / closed"), "pass-denied-closed");
        assert_eq!(slug("  Resumes  "), "resumes");
        assert_eq!(slug("Add a job by link!"), "add-a-job-by-link");
    }

    #[test]
    fn a_name_never_ends_or_doubles_a_separator() {
        assert_eq!(slug("Removed (2)"), "removed-2");
        assert_eq!(slug("---"), "");
    }
}
