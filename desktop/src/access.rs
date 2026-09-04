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
/// the widget set for itself. Returns the response so call sites can chain.
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

/// Turn a human label into a stable identifier fragment.
///
/// "All jobs" -> "all-jobs". Kept deterministic and ASCII so a name never
/// depends on how a label happens to be capitalised or spaced.
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
