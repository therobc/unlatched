// Pipeline: the job_status_log timeline grouped by key, with days-since
// for anything still sitting at "applied" and a simple response-rate
// summary. This view is read-only; all writes happen from Triage.

use eframe::egui;
use std::collections::BTreeMap;

use crate::app::UnlatchedApp;
use crate::date;
use crate::fmt;

pub fn show(app: &mut UnlatchedApp, ui: &mut egui::Ui) {
    let mut open_in_list: Option<String> = None;
    ui.horizontal(|ui| {
        ui.heading("Pipeline");
        if ui.button("Refresh").clicked() {
            app.refresh_pipeline();
        }
    });
    ui.separator();

    render_summary(app, ui);
    ui.separator();

    // Group log entries by key. The query already orders by key then id,
    // so a simple linear scan into a BTreeMap preserves chronological
    // order within each group without a second sort pass.
    let mut groups: BTreeMap<String, Vec<&crate::db::StatusLogEntry>> = BTreeMap::new();
    for entry in &app.pipeline_log {
        groups.entry(entry.key.clone()).or_default().push(entry);
    }
    let mut notes_by_key: BTreeMap<&str, Vec<&crate::db::Note>> = BTreeMap::new();
    for note in &app.pipeline_notes {
        notes_by_key.entry(note.key.as_str()).or_default().push(note);
    }
    // IN PLAY ONLY: applied to, and not finished. See the module note.
    //
    // A job with notes but no application is deliberately NOT added here any
    // more - the pipeline is applications, and a note on a posting somebody was
    // thinking about is not one.
    let settled = crate::status::settled_values();
    let mut finished = 0usize;
    groups.retain(|key, entries| {
        let applied = entries
            .iter()
            // The log's status is nullable - a note-only entry carries none,
            // and a note is not an application.
            .any(|e| {
                e.status
                    .as_deref()
                    .is_some_and(|s| crate::status::rung(s).is_some())
            });
        if !applied {
            return false;
        }
        let over = app
            .pipeline_current
            .get(key)
            .is_some_and(|c| settled.contains(&c.status.as_str()));
        if over {
            finished += 1;
        }
        !over
    });

    if groups.is_empty() {
        if finished > 0 {
            // Said rather than shown as an empty screen: "nothing in play" and
            // "nothing ever happened" are different, and only one of them is
            // worth worrying about.
            ui.label(format!(
                "Nothing in play. {finished} application(s) have finished - \
                 their history is on the job itself."
            ));
        } else {
            ui.label("No applications yet. Mark a job Applied from Triage to start one.");
        }
        return;
    }

    if finished > 0 {
        // NO SILENT FILTERING. A pile that quietly shrank would be
        // indistinguishable from one that lost something.
        // The reasons come FROM the vocabulary. Typed out, this named three
        // of the five settled statuses and left out Hired - the one outcome
        // somebody most wants explained when their application disappears.
        ui.weak(format!(
            "{finished} finished application(s) not shown - {} takes a job \
             out of the pipeline.",
            crate::status::spoken_list(|s| s.settled).to_lowercase()
        ));
        ui.add_space(4.0);
    }

    egui::ScrollArea::vertical()
        .auto_shrink([false, false])
        .show(ui, |ui| {
            for (key, entries) in &groups {
                let (title, company) = app
                    .pipeline_job_info
                    .get(key)
                    .cloned()
                    .unwrap_or_else(|| (key.clone(), None));
                let company_label = company.unwrap_or_default();

                let response = ui.group(|ui| {
                    ui.vertical(|ui| {
                        ui.horizontal(|ui| {
                            ui.strong(fmt::truncate(&title, 60));
                            if !company_label.is_empty() {
                                ui.label(format!("({company_label})"));
                            }
                            if let Some(current) = app.pipeline_current.get(key) {
                                status_pill(ui, &current.status);
                                if current.status == "applied" {
                                    if let Some(days) = date::days_since(&current.updated) {
                                        ui.weak(format!("applied {days} day(s) ago"));
                                    }
                                }
                            }
                        });
                        // The header already shows the CURRENT status. A
                        // single-entry history with nothing written against it
                        // repeats that pill verbatim, which put the same badge
                        // on the card twice; the timeline is worth drawing once
                        // there is a progression, or anything written down.
                        let empty: Vec<&crate::db::Note> = Vec::new();
                        let items = timeline(
                            entries,
                            notes_by_key.get(key.as_str()).unwrap_or(&empty),
                        );
                        if items.len() > 1 || items.iter().any(|i| !i.note.is_empty()) {
                            for item in &items {
                                ui.horizontal(|ui| {
                                    stamp(ui, &item.at);
                                    if item.note_only {
                                        ui.weak("note");
                                    } else {
                                        status_pill(ui, &item.status);
                                    }
                                    if item.note.is_empty() {
                                        // A MISSING NOTE SHOWS. Never
                                        // mandatory, but a transition nobody
                                        // wrote anything about is the one a
                                        // person cannot reconstruct later, and
                                        // a silent blank looked identical to a
                                        // note that was simply too long to fit.
                                        ui.weak("- no note").on_hover_text(
                                            "Nothing was written down when this \
                                             was recorded.",
                                        );
                                    } else {
                                        ui.label(fmt::truncate(&item.note, 60))
                                            .on_hover_text(&item.note);
                                    }
                                    if let Some(terms) = item.offer_terms() {
                                        ui.weak(terms.clone()).on_hover_text(terms);
                                    }
                                });
                            }
                        }
                    });
                })
                .response;

                // A card that does nothing when clicked reads as broken. This
                // opens the posting in the list, where every action on a job
                // already lives - there is one job screen, not two.
                if response
                    .interact(egui::Sense::click())
                    .on_hover_cursor(egui::CursorIcon::PointingHand)
                    .on_hover_text("Open this job in the list")
                    .clicked()
                {
                    open_in_list = Some(key.clone());
                }
            }
        });

    if let Some(key) = open_in_list {
        app.triage_selected = Some(key.clone());
        app.triage_expanded = Some(key);
        app.list_scope = crate::app::ListScope::All;
        app.refresh_triage();
        app.view = crate::app::View::AllJobs;
        // The half that was missing. Everything above was already right - the
        // row was selected and its posting opened - and none of it was visible,
        // because All jobs draws from the top and the row is wherever score
        // ordering put it. See UnlatchedApp::scroll_to_selected.
        app.scroll_to_selected = true;
    }
}

/// Applied, responded, and the rate between them.
///
/// This screen and the dashboard funnel are two readings of one table, so they
/// share the counting (crate::dashboard::reached_from_log) rather than each
/// doing its own arithmetic. They did each do their own, and disagreed: this
/// page counted an application only if the word "applied" was logged, so
/// somebody who marked a job straight to Interviewed vanished from the
/// denominator - while the funnel next door counted them. It also treated only
/// interviewed and no-offer as replies, which was written before Offer and Hired
/// existed and left an offer counting as silence.
fn render_summary(app: &UnlatchedApp, ui: &mut egui::Ui) {
    let reached = crate::dashboard::reached_from_log(
        app.pipeline_log
            .iter()
            .map(|e| (e.key.as_str(), e.status.as_deref().unwrap_or(""))),
    );

    ui.horizontal(|ui| {
        ui.label(format!("Applied: {}", reached.applied))
            .on_hover_text(
                "Jobs you recorded an application for, including the ones that \
                 have since finished. A floor: it can only count what you marked.",
            );
        ui.separator();
        // BUILT FROM THE VOCABULARY, not typed out beside it. This sentence
        // read "Interviewed, offer, hired or denied" - one status this app
        // does not have, and four it gained afterwards left out entirely.
        ui.label(format!("Heard back: {}", reached.responded))
            .on_hover_text(format!(
                "{} - a reply either way.",
                crate::status::spoken_list(|s| s.responded)
            ));
        ui.separator();
        match reached.response_rate() {
            // Out of nothing is not 0%, it is not a number - and a big red 0%
            // on a search that has not started yet is a discouraging lie.
            None => {
                ui.label("Response rate: n/a");
            }
            Some(rate) => {
                ui.label(format!("Response rate: {rate:.1}%"));
            }
        }
    });
}


/// One thing that happened to a job: a status change, or a note written on its
/// own.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct TimelineItem {
    pub at: String,
    /// Empty when this is a note rather than a transition.
    pub status: String,
    pub note: String,
    pub pay: String,
    pub offer_date: String,
    pub note_only: bool,
}

impl TimelineItem {
    /// "$120k, starts 2026-09-01" - whichever of the two was recorded.
    ///
    /// Returns None when neither is, so an ordinary transition renders nothing
    /// rather than an empty pair of brackets.
    pub fn offer_terms(&self) -> Option<String> {
        match (self.pay.as_str(), self.offer_date.as_str()) {
            ("", "") => None,
            (pay, "") => Some(pay.to_string()),
            ("", date) => Some(format!("offered {date}")),
            (pay, date) => Some(format!("{pay}, offered {date}")),
        }
    }
}

/// Status changes and standalone notes, in the order they happened.
///
/// TWO SOURCES, ONE ORDER. The status history and the notes are separate
/// tables (see db::add_note), but a person reading their own record of a job
/// wants one column of events, not two lists to interleave in their head.
///
/// Sorted on the stored timestamp, which is ISO-8601 and therefore sorts
/// correctly as text. Ties keep the status change ahead of the note, because a
/// note written in the same second as a transition was written ABOUT it.
pub fn timeline(
    entries: &[&crate::db::StatusLogEntry],
    notes: &[&crate::db::Note],
) -> Vec<TimelineItem> {
    let mut out: Vec<TimelineItem> = entries
        .iter()
        .map(|e| TimelineItem {
            at: e.at.clone().unwrap_or_default(),
            status: e.status.clone().unwrap_or_default(),
            note: e.note.clone().unwrap_or_default(),
            pay: e.pay.clone().unwrap_or_default(),
            offer_date: e.offer_date.clone().unwrap_or_default(),
            note_only: false,
        })
        .collect();
    out.extend(notes.iter().map(|n| TimelineItem {
        at: n.at.clone(),
        note: n.note.clone(),
        note_only: true,
        ..Default::default()
    }));
    // The tie-break is the reason this is sort_by rather than sort_by_key on
    // `at` alone: the two sources are concatenated, so at an identical
    // timestamp a stable sort would order by which list the item came from.
    // `note_only` false sorts before true, putting the transition first.
    out.sort_by(|a, b| a.at.cmp(&b.at).then(a.note_only.cmp(&b.note_only)));
    out
}

/// When something happened, as a person would say it.
///
/// Was the raw column value cut to sixteen characters, which rendered as
/// "2026-07-15T09:0..." - the ISO separator and a severed clock, in monospace,
/// on every line of every card. The exact stamp is still one hover away for
/// anyone who wants it.
fn stamp(ui: &mut egui::Ui, at: &str) {
    let short = fmt::short_date(at);
    if short.is_empty() {
        return;
    }
    ui.weak(short).on_hover_text(at);
}

/// A filled pill, drawn rather than coloured text: on a dark theme a coloured
/// label is easy to miss among other coloured labels, where a filled badge
/// still reads at a glance.
///
/// The colour and the wording both come from crate::status, which is why this
/// file no longer carries a copy of either. It carried both, and the copy here
/// had "denied" in it - a value the app stopped writing.
fn status_pill(ui: &mut egui::Ui, value: &str) {
    if value.trim().is_empty() {
        return;
    }
    let [r, g, b] = crate::status::colour(value);
    let colour = egui::Color32::from_rgb(r, g, b);
    egui::Frame::none()
        .fill(colour)
        .rounding(egui::Rounding::same(8.0))
        .inner_margin(egui::Margin::symmetric(7.0, 1.0))
        .show(ui, |ui| {
            // White on every pill: each colour above is dark enough to carry
            // white text, and a per-colour text choice is one more thing to
            // keep right in two themes.
            let text = egui::RichText::new(crate::status::label(value))
                .color(egui::Color32::WHITE)
                .size(11.0);
            let response = ui.label(text);
            if let Some(status) = crate::status::get(value) {
                response.on_hover_text(status.hint);
            }
        });
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::db::{Note, StatusLogEntry};

    fn entry(at: &str, status: &str, note: Option<&str>) -> StatusLogEntry {
        StatusLogEntry {
            key: "gh:1".to_string(),
            status: Some(status.to_string()),
            note: note.map(str::to_string),
            at: Some(at.to_string()),
            pay: None,
            offer_date: None,
        }
    }

    fn note(at: &str, text: &str) -> Note {
        Note {
            key: "gh:1".to_string(),
            note: text.to_string(),
            at: at.to_string(),
        }
    }

    #[test]
    fn a_note_written_between_two_transitions_lands_between_them() {
        // The whole reason the two tables are merged rather than shown as two
        // lists: a person who jotted "recruiter said two weeks" after applying
        // and before the interview needs to see it in that gap, not in a
        // separate pile below.
        let entries = [
            entry("2026-08-01T09:00:00", "applied", None),
            entry("2026-08-10T09:00:00", "interviewed", None),
        ];
        let notes = [note("2026-08-04T12:00:00", "recruiter said two weeks")];
        let refs: Vec<&StatusLogEntry> = entries.iter().collect();
        let note_refs: Vec<&Note> = notes.iter().collect();
        let items = timeline(&refs, &note_refs);

        let shape: Vec<(&str, bool)> = items
            .iter()
            .map(|i| (i.status.as_str(), i.note_only))
            .collect();
        assert_eq!(
            shape,
            vec![("applied", false), ("", true), ("interviewed", false)]
        );
    }

    #[test]
    fn a_note_written_in_the_same_second_as_a_change_follows_it() {
        // Identical timestamps happen: the note prompt saves both in one
        // action. The note was written ABOUT the transition, so it reads after
        // it - and without the tie-break the order would depend on which of
        // the two lists was concatenated first.
        let entries = [entry("2026-08-01T09:00:00", "offer", None)];
        let notes = [note("2026-08-01T09:00:00", "verbal, written to follow")];
        let refs: Vec<&StatusLogEntry> = entries.iter().collect();
        let note_refs: Vec<&Note> = notes.iter().collect();
        let items = timeline(&refs, &note_refs);
        assert!(!items[0].note_only, "the transition comes first");
        assert!(items[1].note_only);
    }

    #[test]
    fn a_transition_with_nothing_written_about_it_carries_an_empty_note() {
        // What the "- no note" marker keys on. If this ever came back as a
        // placeholder string instead, the marker would stop appearing and the
        // gap it exists to show would be invisible again.
        let entries = [entry("2026-08-01T09:00:00", "applied", None)];
        let refs: Vec<&StatusLogEntry> = entries.iter().collect();
        let items = timeline(&refs, &[]);
        assert_eq!(items[0].note, "");
    }

    #[test]
    fn offer_terms_read_as_a_sentence_or_not_at_all() {
        let mut item = TimelineItem {
            at: "2026-08-01T09:00:00".to_string(),
            status: "offer".to_string(),
            ..Default::default()
        };
        assert_eq!(item.offer_terms(), None, "an offer with no figures says nothing");
        item.pay = "$120,000".to_string();
        assert_eq!(item.offer_terms().as_deref(), Some("$120,000"));
        item.offer_date = "2026-09-01".to_string();
        assert_eq!(
            item.offer_terms().as_deref(),
            Some("$120,000, offered 2026-09-01")
        );
        item.pay.clear();
        assert_eq!(item.offer_terms().as_deref(), Some("offered 2026-09-01"));
    }

    #[test]
    fn a_job_with_only_notes_still_produces_a_timeline() {
        // Notes are not always tied to a status change. A person
        // researching an employer before applying has a history worth keeping
        // and no transition to hang it on.
        let notes = [note("2026-08-01T09:00:00", "same recruiter as the last one")];
        let note_refs: Vec<&Note> = notes.iter().collect();
        let items = timeline(&[], &note_refs);
        assert_eq!(items.len(), 1);
        assert!(items[0].note_only);
        assert_eq!(items[0].status, "");
    }
}
