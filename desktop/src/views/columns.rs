// The job list's columns, as things rather than as positions.
//
// This table used to be rendered POSITIONALLY: one block declaring widths, a
// second declaring headings, a third drawing cells, kept in step only by being
// written in the same order. Adding a column meant editing three places and
// hoping; letting a person REORDER them was impossible, because "the third
// column" was the only name any of the three blocks had for each other.
//
// So a column is one value that knows its own width, heading and cell. Order
// and visibility are then just a list of ids, which is a thing a person can
// rearrange and the app can save.

use eframe::egui;

use crate::app::SortBy;

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum ColumnId {
    /// The multi-select tick box. Pinned and first: it is the handle for every
    /// bulk action, and a person who hid it would have no way to reach those
    /// actions and no obvious way to work out why.
    Select,
    Title,
    Company,
    Location,
    Posted,
    Salary,
    Score,
    Fit,
    Asks,
    Source,
    Match,
    Status,
    Applied,
    FoundAt,
}

pub struct ColumnSpec {
    pub id: ColumnId,
    /// What this column is called on disk. Deliberately NOT the heading: a
    /// heading is wording and may be reworded, and a saved layout must not
    /// come back scrambled because "Match" was renamed to "Verdict".
    pub key: &'static str,
    pub heading: &'static str,
    pub hover: Option<&'static str>,
    pub width: f32,
    pub min: f32,
    pub clip: bool,
    /// May absorb the slack when the window is wider than the columns need.
    ///
    /// A FLAG on the column, not a position in the list. "The last column
    /// stretches" stops being a usable rule the moment a person can move the
    /// last column somewhere else - it would mean the widest column changed
    /// every time they rearranged anything.
    pub flex: bool,
    /// The sort this heading applies when clicked, if any.
    pub sort: Option<SortBy>,
    /// Cannot be hidden. Title only: a job list with no job names is a state a
    /// person can get into by accident and cannot read their way out of.
    pub pinned: bool,
}

const fn col(
    id: ColumnId,
    key: &'static str,
    heading: &'static str,
    width: f32,
    min: f32,
) -> ColumnSpec {
    ColumnSpec {
        id,
        key,
        heading,
        hover: None,
        width,
        min,
        clip: true,
        flex: false,
        sort: None,
        pinned: false,
    }
}

/// Every column the list can draw, in the order a fresh profile gets them.
///
/// The widths are the ones measured at the default window size. `Score`, `Fit`
/// and the two narrow ones do not clip: their contents are short enough that
/// clipping only ever cost pixels.
pub const COLUMNS: &[ColumnSpec] = &[
    ColumnSpec {
        clip: false,
        pinned: true,
        ..col(ColumnId::Select, "select", "", 28.0, 28.0)
    },
    ColumnSpec {
        flex: true,
        sort: Some(SortBy::Title),
        pinned: true,
        min: 200.0,
        ..col(ColumnId::Title, "title", "Title", 260.0, 200.0)
    },
    ColumnSpec {
        sort: Some(SortBy::Company),
        ..col(ColumnId::Company, "company", "Company", 140.0, 100.0)
    },
    col(ColumnId::Location, "location", "Location", 120.0, 80.0),
    ColumnSpec {
        sort: Some(SortBy::Posted),
        ..col(ColumnId::Posted, "posted", "Posted", 100.0, 80.0)
    },
    ColumnSpec {
        clip: false,
        sort: Some(SortBy::Salary),
        ..col(ColumnId::Salary, "salary", "Salary", 120.0, 90.0)
    },
    ColumnSpec {
        clip: false,
        sort: Some(SortBy::Score),
        ..col(ColumnId::Score, "score", "Score", 60.0, 50.0)
    },
    ColumnSpec {
        clip: false,
        sort: Some(SortBy::Fit),
        hover: Some(
            "How much of what this posting asks for your resume already shows. \
             The words it does not are listed in the panel below, ready to work \
             into your own resume.",
        ),
        ..col(ColumnId::Fit, "fit", "Fit", 55.0, 45.0)
    },
    ColumnSpec {
        hover: Some(
            "What the posting states it requires: years, education, licences, \
             clearance, travel, shift. Blank means it said none of those - not \
             that there are none.",
        ),
        ..col(ColumnId::Asks, "asks", "Asks", 130.0, 80.0)
    },
    col(ColumnId::Source, "source", "Source", 90.0, 70.0),
    col(ColumnId::Match, "verdict", "Match", 60.0, 50.0),
    // Wider than the others need to be because the cell holds a 100px
    // dropdown AND the how-long-since figure beside it. At 110 the figure was
    // clipped to its first letter - "today" rendered as a lone "t", which
    // reads as a rendering fault rather than as information.
    col(ColumnId::Status, "status", "Status", 155.0, 110.0),
    col(ColumnId::Applied, "applied", "Applied", 95.0, 75.0),
    col(ColumnId::FoundAt, "found_at", "Found at", 150.0, 90.0),
];

pub fn spec(id: ColumnId) -> &'static ColumnSpec {
    COLUMNS
        .iter()
        .find(|c| c.id == id)
        // COLUMNS is the definition of ColumnId - a variant missing from it is
        // a compile-time-adjacent mistake, not a runtime condition to handle.
        .expect("every ColumnId has a spec in COLUMNS")
}

fn by_key(key: &str) -> Option<ColumnId> {
    COLUMNS.iter().find(|c| c.key == key).map(|c| c.id)
}

pub fn default_order() -> Vec<ColumnId> {
    COLUMNS.iter().map(|c| c.id).collect()
}

/// A saved order, repaired.
///
/// Two things have to survive here, and both are ordinary rather than
/// exceptional: a key this build does not know (the person opened a profile
/// last touched by a NEWER version), and a column this build has that the
/// saved order predates. The first is dropped, the second is appended in its
/// default place - so adding a column in a later release does not require
/// everybody to reset their layout to see it.
pub fn from_keys(keys: &[String]) -> Vec<ColumnId> {
    let mut order: Vec<ColumnId> = keys.iter().filter_map(|k| by_key(k)).collect();
    order.dedup();
    for spec in COLUMNS {
        if !order.contains(&spec.id) {
            order.push(spec.id);
        }
    }
    order
}

/// The same lookup WITHOUT the completion `from_keys` does.
///
/// For the hidden list, where "fill in anything missing" would mean "hide
/// every column the saved file did not mention" - which on a first run, from
/// an empty list, is the entire table.
pub fn from_keys_lenient(keys: &[String]) -> Vec<ColumnId> {
    keys.iter().filter_map(|k| by_key(k)).collect()
}

pub fn to_keys(order: &[ColumnId]) -> Vec<String> {
    order.iter().map(|id| spec(*id).key.to_string()).collect()
}

/// The columns actually drawn, in the person's order.
///
/// Title is put back if a saved file hides it - by hand-editing, or written by
/// a build where it was not yet pinned. That pin is also why there is no
/// empty-result guard here: every order comes from `from_keys` or
/// `default_order`, both of which contain Title, and Title cannot be filtered
/// out - so the result is never empty and a fallback would be guarding
/// nothing.
pub fn visible(order: &[ColumnId], hidden: &[ColumnId]) -> Vec<ColumnId> {
    order
        .iter()
        .copied()
        .filter(|id| spec(*id).pinned || !hidden.contains(id))
        .collect()
}

/// Which of the drawn columns takes the leftover width.
///
/// The first VISIBLE flexible one. If the person has hidden every flexible
/// column, the last column takes it instead - somebody has to, or the table
/// stops short of the edge and leaves the dead strip this ticket exists to
/// remove.
pub fn flex_index(shown: &[ColumnId]) -> usize {
    shown
        .iter()
        .position(|id| spec(*id).flex)
        .unwrap_or(shown.len().saturating_sub(1))
}

/// The gear's panel: what is shown, and in what order.
///
/// Reordering is done with explicit move buttons rather than by dragging a
/// heading. Clicking a heading already SORTS by it, and a control where a
/// short drag reorders while a click sorts is one a person triggers by
/// accident in both directions.
///
/// Returns true when something changed and the layout needs saving.
pub fn settings_panel(
    ui: &mut egui::Ui,
    order: &mut Vec<ColumnId>,
    hidden: &mut Vec<ColumnId>,
) -> bool {
    let mut changed = false;
    ui.label("Tick to show. Use the arrows to move a column left or right.");
    ui.add_space(6.0);

    let mut swap: Option<(usize, usize)> = None;
    // Tall enough for every column this app has, so the usual case needs no
    // scrolling at all. The ScrollArea stays because a later release adding
    // columns must not push "Reset to default" off the bottom of the panel -
    // that button is the way out of a layout somebody has made unreadable.
    egui::ScrollArea::vertical()
        .max_height(470.0)
        .show(ui, |ui| {
            for i in 0..order.len() {
                let id = order[i];
                let spec = spec(id);
                ui.horizontal(|ui| {
                    let mut shown = spec.pinned || !hidden.contains(&id);
                    // The tick-box column's heading is deliberately blank, so
                    // it needs a name here or it lists as an empty row.
                    let label = if spec.heading.is_empty() {
                        "Select"
                    } else {
                        spec.heading
                    };
                    let tick = ui.add_enabled(
                        !spec.pinned,
                        egui::Checkbox::new(&mut shown, label),
                    );
                    if spec.pinned {
                        tick.on_hover_text(
                            "Always shown. Hiding this one would leave no way \
                             to reach what it controls.",
                        );
                    } else if tick.changed() {
                        if shown {
                            hidden.retain(|h| *h != id);
                        } else {
                            hidden.push(id);
                        }
                        changed = true;
                    }

                    ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                        // Left and right, not up and down: this list is
                        // vertical but the thing it moves is a column in a
                        // horizontal table, and the person is watching the
                        // table while they press these.
                        if ui
                            .add_enabled(i + 1 < order.len(), egui::Button::new(">"))
                            .on_hover_text("Move right")
                            .clicked()
                        {
                            swap = Some((i, i + 1));
                        }
                        if ui
                            .add_enabled(i > 0, egui::Button::new("<"))
                            .on_hover_text("Move left")
                            .clicked()
                        {
                            swap = Some((i, i - 1));
                        }
                    });
                });
            }
        });

    // Applied after the loop: swapping mid-iteration would move a row out from
    // under the index the loop is still using.
    if let Some((a, b)) = swap {
        order.swap(a, b);
        changed = true;
    }

    ui.add_space(8.0);
    ui.separator();
    if ui.button("Reset to default").clicked() {
        *order = default_order();
        hidden.clear();
        changed = true;
    }
    changed
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn every_column_id_has_exactly_one_spec() {
        // spec() panics on a missing one, so this proves the reverse: no two
        // entries claim the same id, and no two claim the same on-disk key.
        let mut keys: Vec<&str> = COLUMNS.iter().map(|c| c.key).collect();
        keys.sort_unstable();
        let before = keys.len();
        keys.dedup();
        assert_eq!(keys.len(), before, "two columns share an on-disk key");
        for spec in COLUMNS {
            assert_eq!(super::spec(spec.id).key, spec.key);
        }
    }

    #[test]
    fn a_saved_order_from_an_older_build_gains_the_new_columns() {
        // The case that would otherwise strand somebody: they saved a layout,
        // a later release added a column, and their list silently never shows
        // it because their saved order does not mention it.
        let order = from_keys(&["company".to_string(), "title".to_string()]);
        assert_eq!(order[0], ColumnId::Company);
        assert_eq!(order[1], ColumnId::Title);
        assert_eq!(order.len(), COLUMNS.len(), "every column is present");
    }

    #[test]
    fn a_key_this_build_does_not_know_is_dropped_not_fatal() {
        let order = from_keys(&["title".to_string(), "invented_by_a_newer_build".to_string()]);
        assert_eq!(order.len(), COLUMNS.len());
        assert_eq!(order[0], ColumnId::Title);
    }

    #[test]
    fn title_cannot_be_hidden_even_by_a_hand_edited_file() {
        let shown = visible(&default_order(), &[ColumnId::Title, ColumnId::Score]);
        assert!(shown.contains(&ColumnId::Title));
        assert!(!shown.contains(&ColumnId::Score));
    }

    #[test]
    fn the_slack_follows_the_flexible_column_not_a_position() {
        // Title moved to the end still takes the slack. This is the whole
        // reason flex is a flag: with a positional rule, rearranging columns
        // would silently hand the extra width to whatever landed last.
        let mut order = default_order();
        let title = order.remove(0);
        order.push(title);
        let shown = visible(&order, &[]);
        assert_eq!(shown[flex_index(&shown)], ColumnId::Title);
    }

    #[test]
    fn a_layout_with_no_flexible_column_still_reaches_the_edge() {
        // Not reachable through the UI today - Title is the only flexible
        // column and it is pinned - but flex_index must not return a
        // meaningless index if that ever stops being true.
        let shown = vec![ColumnId::Company, ColumnId::Status];
        assert_eq!(flex_index(&shown), 1);
    }

    #[test]
    fn keys_round_trip_through_disk() {
        let order = default_order();
        assert_eq!(from_keys(&to_keys(&order)), order);
    }

    #[test]
    fn an_empty_hidden_list_hides_nothing() {
        // from_keys() completes a partial list with every missing column,
        // which is right for the ORDER and catastrophic for the hidden set:
        // on a first run the saved list is empty, and completing it would
        // turn off the whole table.
        assert!(from_keys_lenient(&[]).is_empty());
        assert_eq!(visible(&default_order(), &from_keys_lenient(&[])).len(), COLUMNS.len());
    }
}
