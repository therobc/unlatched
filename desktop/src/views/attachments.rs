// The Files face of an opened job: what is kept beside it, and the three ways
// to add something.
//
// DOWNLOAD-ONLY, WITHOUT EXCEPTION (decided 2026-08-13). Nothing here opens a
// file - not an image, not plain text - so the app carries no decoder and no
// parser for any attachment format, and there is no code path where a file
// somebody else wrote is interpreted. The hover says "Download to view", or
// "Unsupported file type, download to view" for something it cannot even name.
//
// This replaced an in-app preview for images and text. Deleting it was the
// point: a preview that only handles safe formats still has to decide what is
// safe on every file it is handed.
//
// DRAWN FROM DATA, ACTED ON AFTERWARDS. This block is rendered inside a
// closure that already holds the job row borrowed from the app, so it cannot
// touch the app itself. Every button returns an Action for the caller to
// carry out, which is the same shape every other deferred action in the
// triage view uses.

use eframe::egui;

use crate::attachments::{Attachment, Kind};
use crate::fmt;

/// What the person asked for while looking at the files.
pub enum Action {
    None,
    Attach,
    Paste,
    AddLink,
    Download(i64),
    Remove(i64),
    SetTrust(i64, &'static str),
}

/// Everything the block needs that lives on the app rather than on the row.
pub struct Files<'a> {
    pub rows: &'a [Attachment],
    pub message: Option<&'a str>,
    /// Where the last download went, or None if none has. Shown so the person
    /// can see which folder this profile is using without opening a dialog.
    pub download_dir: Option<&'a str>,
    /// This machine's UTC offset, for the dates below. Passed rather than
    /// looked up: db::local_offset_secs is a query, and this runs while a
    /// frame is already drawing.
    pub local_offset: i64,
}

/// The teaching line under the paste button.
///
/// Somebody who does not know the shortcut cannot use the feature at all, and
/// the confirmation screen is the artefact people most often wish they had
/// kept.
pub const SNIP_HINT: &str = "Win+Shift+S to snip part of your screen, then paste it here";

pub fn show(ui: &mut egui::Ui, files: &Files<'_>) -> Action {
    let mut action = Action::None;

    ui.horizontal(|ui| {
        if crate::access::tag(
            ui.button("Attach a file"),
            egui::WidgetType::Button,
            "attachments-attach-file",
        )
        .clicked()
        {
            action = Action::Attach;
        }
        if crate::access::tag(
            ui.button("Paste a screenshot"),
            egui::WidgetType::Button,
            "attachments-paste-screenshot",
        )
        .clicked()
        {
            action = Action::Paste;
        }
        if crate::access::tag(
            ui.button("Add a link"),
            egui::WidgetType::Button,
            "attachments-add-link",
        )
        .clicked()
        {
            action = Action::AddLink;
        }
    });
    ui.weak(SNIP_HINT);
    // WHERE DOWNLOADS GO, ON SCREEN. The choice is remembered per profile so
    // several people on one machine can keep their files apart;
    // a remembered folder nobody can see is one they cannot tell from the
    // default, which is the whole difference this setting makes.
    if let Some(folder) = files.download_dir {
        ui.weak(format!("Downloads go to {folder}"))
            .on_hover_text(
                "Where this profile last saved an attachment. The save dialog \
                 opens here, and picking somewhere else moves it.",
            );
    }
    if let Some(message) = files.message {
        ui.colored_label(crate::theme::ACCENT, message);
    }
    ui.separator();

    if files.rows.is_empty() {
        ui.weak(
            "Nothing kept beside this job yet. The resume you sent, the \
             confirmation screen, an offer letter, the recruiter's scheduling \
             link - they all belong here, and they stay with the job.",
        );
        return action;
    }

    egui::ScrollArea::vertical()
        .id_source("attachment_list")
        .max_height(160.0)
        .auto_shrink([false, false])
        .show(ui, |ui| {
            for row in files.rows {
                if let Some(chosen) = attachment_row(ui, row, files.local_offset) {
                    action = chosen;
                }
            }
        });
    action
}

fn attachment_row(
    ui: &mut egui::Ui,
    row: &Attachment,
    local_offset: i64,
) -> Option<Action> {
    let mut action = None;
    ui.horizontal(|ui| {
        ui.label(icon_for(row.kind));

        match row.kind {
            // A link is the one kind with somewhere to go, so it goes there.
            // fmt::safe_link is the same http(s)-only guard every other
            // outbound link in this app passes through.
            Kind::Link => {
                let label = fmt::truncate(&row.display_name, 44);
                match row.url.as_deref().and_then(fmt::safe_link) {
                    Some(url) => {
                        crate::browse::link(ui, label, url).on_hover_text(url);
                    }
                    None => {
                        ui.label(label).on_hover_text(
                            "This link is not http or https, so the app will not open it.",
                        );
                    }
                }
            }
            // EVERY FILE IS A LABEL, NOT A CONTROL. There is nothing to click
            // it for: the app opens no attachment, so a name that looked
            // pressable would promise something that never happens. The hover
            // says what to do instead.
            Kind::Image | Kind::Text | Kind::Pdf | Kind::Office | Kind::Other => {
                let label = ui.label(fmt::truncate(&row.display_name, 44));
                if let Some(hover) = row.kind.hover() {
                    label.on_hover_text(hover);
                }
            }
        }

        if let Some(bytes) = row.bytes {
            ui.weak(human_size(bytes));
        }
        // WHEN, because attachments belong to the job rather than to one
        // application: re-applying months later adds a second resume beside
        // the first, and the date is what tells them apart.
        ui.weak(fmt::short_date(&row.added_at, local_offset))
            .on_hover_text(format!("added {} for {}", row.added_at, row.key));

        // WHO WROTE IT, ON SCREEN. A protection nobody can see is one nobody
        // can trust - and this badge is also the control for it.
        let (badge, hover, next) = if row.is_mine() {
            (
                "yours",
                "Your own file. An assistant on this machine may read it - that is \
                 what makes it useful for writing.",
                crate::attachments::POSTING,
            )
        } else {
            (
                "employer",
                "Came from the employer's side. Its contents are kept away from \
                 assistants, because text a stranger wrote is where prompt \
                 injection lives.",
                crate::attachments::MINE,
            )
        };
        if crate::access::tag(
            ui.small_button(badge),
            egui::WidgetType::Button,
            format!("attachment-trust-{}", row.id),
        )
        .on_hover_text(hover)
        .clicked()
        {
            action = Some(Action::SetTrust(row.id, next));
        }

        ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
            if crate::access::tag(
                ui.small_button("Remove"),
                egui::WidgetType::Button,
                format!("attachment-remove-{}", row.id),
            )
            .clicked()
            {
                action = Some(Action::Remove(row.id));
            }
            if row.stored_name.is_some()
                && crate::access::tag(
                    ui.small_button("Download"),
                    egui::WidgetType::Button,
                    format!("attachment-download-{}", row.id),
                )
                .clicked()
            {
                action = Some(Action::Download(row.id));
            }
        });
    });
    action
}

fn icon_for(kind: Kind) -> &'static str {
    match kind {
        Kind::Image => "[img]",
        Kind::Text => "[txt]",
        Kind::Pdf => "[pdf]",
        Kind::Office => "[doc]",
        Kind::Link => "[link]",
        Kind::Other => "[file]",
    }
}

fn human_size(bytes: i64) -> String {
    const KB: i64 = 1024;
    const MB: i64 = KB * 1024;
    if bytes >= MB {
        format!("{:.1} MB", bytes as f64 / MB as f64)
    } else if bytes >= KB {
        format!("{} KB", bytes / KB)
    } else {
        format!("{bytes} bytes")
    }
}

#[cfg(test)]
mod tests {
    use super::{human_size, icon_for, SNIP_HINT};
    use crate::attachments::Kind;

    #[test]
    fn the_paste_control_teaches_the_shortcut() {
        // The shortcut has to be IN the string. Without it, a person who does
        // not already know it cannot use the feature at all.
        assert!(SNIP_HINT.contains("Win+Shift+S"));
        assert!(SNIP_HINT.contains("paste"));
    }

    #[test]
    fn every_kind_is_distinguishable_at_a_glance() {
        let icons = [
            icon_for(Kind::Image),
            icon_for(Kind::Text),
            icon_for(Kind::Pdf),
            icon_for(Kind::Office),
            icon_for(Kind::Link),
            icon_for(Kind::Other),
        ];
        let mut unique = icons.to_vec();
        unique.sort_unstable();
        unique.dedup();
        assert_eq!(unique.len(), icons.len(), "two kinds share an icon");
    }

    #[test]
    fn sizes_read_as_sizes() {
        assert_eq!(human_size(512), "512 bytes");
        assert_eq!(human_size(2048), "2 KB");
        assert_eq!(human_size(5 * 1024 * 1024), "5.0 MB");
    }
}
