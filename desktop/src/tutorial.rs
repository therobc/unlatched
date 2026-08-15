//! A first-run walkthrough: dim the window, spotlight one control, explain it.
//!
//! Replaces a page of instructions nobody reads. The difference that matters
//! is that a spotlight points at the actual control on the actual screen, so
//! there is no translation step between "the Companies page" and finding it.
//!
//! It covers BOTH routes deliberately (decided 2026-08-05): the fastest way to
//! set a search up is to let an assistant do it through the command line, and
//! the app has to teach that - but somebody without an assistant must still be
//! walked through every manual equivalent, or the tutorial has quietly made
//! the app worse for them.
//!
//! Skippable at any point, and repeatable afterwards from Settings, because a
//! walkthrough you cannot get back is one people rush through.
//!
//! ELEVEN STEPS, AND THE COUNT IS THE DESIGN CONSTRAINT. This was ten steps
//! covering roughly half the app: seven features added after it was written -
//! attached files and who wrote them, the copy of the posting kept when you
//! apply, collectors, repost detection, duplicate grouping, removed rows, the
//! export - were all missing, and the obvious fix of appending seven steps
//! would have turned a walkthrough into the manual it exists to replace.
//!
//! So each one was FOLDED into the step whose screen it already belongs to,
//! and only one step was added: triage split into reading the list and working
//! a single job, which is how the screen is actually used. Anything a person
//! meets on their own without being hurt by meeting it late - the export, the
//! archive - is one clause on a step that was there anyway rather than a step
//! of its own.

use eframe::egui;

use crate::access;
use crate::app::{UnlatchedApp, View};
use crate::theme;

pub struct Step {
    /// Which sidebar entry to spotlight, or None for a step that talks about
    /// the window as a whole.
    pub anchor: Option<&'static str>,
    /// The view to switch to, so the thing being described is on screen.
    pub view: Option<View>,
    pub title: &'static str,
    pub body: &'static str,
}

pub const STEPS: [Step; 11] = [
    Step {
        anchor: None,
        view: Some(View::Dashboard),
        title: "Welcome to Unlatched",
        body: "This reads employers' own job boards directly - no job site, no \
               account, nothing sent anywhere. Everything it finds stays on this \
               computer.\n\nThere are two ways to set it up. If you use an AI \
               assistant, it can do the whole thing for you. If not, every \
               step is a page in this app. This walkthrough covers both, and takes \
               about two minutes.",
    },
    Step {
        anchor: Some("Agents"),
        view: Some(View::Agent),
        title: "The fastest start: let an assistant do it",
        body: "If you have an AI assistant on this computer - Claude Code in an \
               editor, for instance - tell it: \"There is a CLI for my job search \
               app. Run: unlatched --help\".\n\nIt can then choose your job titles \
               with you, add employers, run the search, and rewrite your resume \
               against what it finds. This page has every command, each with a \
               Copy button. No API key and no account.",
    },
    Step {
        anchor: Some("Config"),
        view: Some(View::Config),
        title: "Or set it up by hand, here",
        body: "Doing it yourself is the same job in a form. Title include is the \
               important one: employers file the same work under many different \
               titles, and the ones you would think of are a fraction of them. \
               Type each one and press Enter.\n\nSet what you will accept for pay, \
               location and employment type here too.",
    },
    Step {
        anchor: Some("Companies"),
        view: Some(View::Companies),
        title: "Name the employers to watch",
        body: "Add employers by name and press Discover - the job-board systems \
               are already set up, so it usually finds the right board on its \
               own. Short of names? Add starter employers puts in a list of \
               national ones this app is known to be able to read.\n\nAdd your \
               local ones too. Internal roles - IT, HR, maintenance, accounting \
               - exist at every employer, so a wide list matters more than \
               picking an industry. Then open Collect and choose Every \
               employer.\n\nThat same menu has From a collector. Anything this \
               app will not read itself can still reach your list: a program \
               you choose to run writes a file, and this reads that file. \
               Nothing about it is automatic and nothing is set up for you.",
    },
    Step {
        anchor: Some("Triage"),
        view: Some(View::Triage),
        title: "Review what came back",
        body: "One row per job. Arrow keys move down the list and o opens the \
               highlighted posting inside this window; o again closes it. The \
               title is a link to the employer's own site, so clicking THAT \
               leaves for your browser.\n\nSet where you are with a job from the \
               dropdown on the right, or press a for applied, p for passed \
               over, i for interviewed, n to add a note.\n\nFit is how much of \
               what that posting asks for your resume already shows - click the \
               number to see exactly which words are missing. An assistant is \
               worth having here: it can work those words in where they are TRUE \
               of you, and leave out what you could not defend in an interview.",
    },
    // THE SECOND TRIAGE STEP, and the only step this walkthrough gained.
    // Everything here is what happens once a row is open, which is where a
    // person spends the actual time: the tabs, the files, and the copy the app
    // keeps for them. Folded into the step before it, this made a wall of text
    // out of the busiest screen in the app.
    Step {
        anchor: Some("Triage"),
        view: Some(View::Triage),
        title: "Working one job",
        body: "An open posting has three tabs. Posting is the advertisement, Fit \
               is why that number says what it says, and Files is anything you \
               keep beside this job - the employer's PDF, a recruiter's email, \
               the resume you tailored for it.\n\nEvery file says who wrote it: \
               yours, or employer. That badge decides what an assistant on this \
               machine may ever be shown, because text a stranger wrote is where \
               prompt injection lives. Press it to correct it. The app opens no \
               attachment itself - Download puts a copy in your Downloads \
               folder.\n\nApplying keeps the posting's own words as they read \
               that day. Employers edit live adverts and take them down, and a \
               taken-down posting cannot be read back off the web - which is \
               usually the week somebody asks you what it said.\n\nA seat \
               advertised before says so under the posting, with Open the \
               earlier round beside it. Both rounds stay; neither is folded \
               into the other.",
    },
    // Ships OFF, so this step exists to make sure nobody meets that setting
    // for the first time as a feature that appears broken (decided 2026-08-08).
    // It sits after Triage because "Add a job by link" is a button on that
    // screen, so the reader has just seen where the thing being described is.
    Step {
        anchor: Some("Config"),
        view: Some(View::Config),
        title: "Jobs you find yourself",
        body: "Found something on a job site, or sent to you directly? Add a job \
               by link on the list screen puts it in your pipeline with \
               everything else, so nothing you apply to is tracked somewhere \
               apart.\n\nBy default you type the title and description \
               yourself. Under \"Adding jobs by link\" here you can let the app \
               open the link once and fill those in for you - including on sites \
               that ask automated tools not to read them, which it will only \
               ever do here, one page at a time, with you present. Your \
               call; it is off until you make it.",
    },
    Step {
        anchor: Some("Resumes"),
        view: Some(View::Resumes),
        title: "Attach your resume",
        body: "Drop your resume on the ORIGINAL box, or press Browse. The app \
               keeps its own copy, so nothing you do later can overwrite the \
               version a search was measured against.\n\nWhen you have improved it \
               - yourself, or with an assistant - attach that as OPTIMIZED. That \
               is the one used from then on. Download puts any copy back in your \
               Downloads folder, so a resume deleted off your disk is never gone.",
    },
    Step {
        anchor: Some("Keywords"),
        view: Some(View::Keywords),
        title: "See what the market keeps asking for",
        body: "Every word the collected postings ask for, ranked by how many want \
               it, and which of them your resume already shows. This is the Fit \
               number from the last step, opened up: same measurement, whole \
               market instead of one posting.\n\nSo it is a to-do list for your \
               resume. An assistant can read the identical thing with `unlatched \
               brief --json`, broken down per job title in your search, and \
               rewrite against it in one go.",
    },
    Step {
        anchor: Some("Dashboard"),
        view: Some(View::Dashboard),
        title: "Come back here each day",
        body: "What arrived since you last looked, what needs a decision, and how \
               the search is going. Every number is clickable and takes you to the \
               jobs behind it.\n\nThe search keeps itself current twice on \
               weekdays and once a day at weekends, and catches up on whatever it \
               missed if the app has been closed for a few days. Change it or \
               switch it off in Config.\n\nNothing this app collects is ever \
               deleted. All jobs holds every posting, including the ones it \
               folded together as the same job and the ones you took out of your \
               lists - Grouped and Removed there show you each, and both undo.",
    },
    Step {
        anchor: Some("Settings"),
        view: Some(View::Profiles),
        // Also the destination of Skip, so it has to read correctly for
        // somebody who arrived here by skipping rather than by finishing.
        title: "Whenever you want it back",
        body: "Skipped or finished, this walkthrough is always here: Settings, \
               under Help. Nothing you do now closes it for good.\n\nExport to a \
               spreadsheet is on this page as well - every job, every status, the \
               whole history, as a CSV that opens anywhere. Your search does not \
               live only inside this app.\n\nAppearance and profiles are here \
               too. Good luck.",
    },
];

/// Draws the overlay. Returns true while the walkthrough is still running.
pub fn show(app: &mut UnlatchedApp, ctx: &egui::Context) -> bool {
    if !app.tutorial_active {
        return false;
    }
    let index = app.tutorial_step.min(STEPS.len() - 1);
    let step = &STEPS[index];

    // Put the view being described on screen before dimming it.
    if let Some(view) = step.view {
        if app.view != view {
            app.view = view;
        }
    }

    let screen = ctx.screen_rect();
    let spotlight = step
        .anchor
        .and_then(|name| app.tutorial_anchors.get(name).copied());

    let mut advance = false;
    let mut back = false;
    let mut finish = false;
    let mut skip_to_end = false;

    egui::Area::new(egui::Id::new("tutorial_overlay"))
        .order(egui::Order::Foreground)
        .fixed_pos(screen.min)
        .show(ctx, |ui| {
            let painter = ui.painter();
            let dim = egui::Color32::from_black_alpha(150);
            match spotlight {
                // Four rectangles AROUND the target rather than one with a
                // hole: egui has no cut-out fill, and four rects is exact
                // where a blurred ring would only approximate it.
                Some(hole) => {
                    let hole = hole.expand(4.0);
                    painter.rect_filled(
                        egui::Rect::from_min_max(screen.min, egui::pos2(screen.max.x, hole.min.y)),
                        0.0,
                        dim,
                    );
                    painter.rect_filled(
                        egui::Rect::from_min_max(egui::pos2(screen.min.x, hole.max.y), screen.max),
                        0.0,
                        dim,
                    );
                    painter.rect_filled(
                        egui::Rect::from_min_max(
                            egui::pos2(screen.min.x, hole.min.y),
                            egui::pos2(hole.min.x, hole.max.y),
                        ),
                        0.0,
                        dim,
                    );
                    painter.rect_filled(
                        egui::Rect::from_min_max(
                            egui::pos2(hole.max.x, hole.min.y),
                            egui::pos2(screen.max.x, hole.max.y),
                        ),
                        0.0,
                        dim,
                    );
                    painter.rect_stroke(hole, 5.0, egui::Stroke::new(2.0, theme::ACCENT));
                }
                None => {
                    painter.rect_filled(screen, 0.0, dim);
                }
            }
        });

    // The callout is its own Area so it sits above the dimming and stays
    // clickable while everything under it is covered.
    // Clamped against the callout's REAL height, remembered from the last
    // frame. A fixed reserve was wrong for the taller steps: spotlighting
    // Settings, which sits at the bottom of the rail, pushed the callout
    // down until Finish, Back and Skip were off the bottom of the window -
    // so the step reached by pressing Skip was the one you could not
    // dismiss. Off by a frame at most, on the first frame of a step.
    let height = ctx
        .data(|d| d.get_temp::<f32>(callout_height_id()))
        .unwrap_or(240.0);
    let lowest = (screen.max.y - height - 20.0).max(screen.min.y + 20.0);
    let callout_pos = match spotlight {
        Some(hole) => egui::pos2(
            (hole.max.x + 18.0).min(screen.max.x - CALLOUT_WIDTH - 20.0),
            hole.min.y.min(lowest),
        ),
        None => egui::pos2(
            screen.center().x - CALLOUT_WIDTH * 0.5,
            (screen.center().y - height * 0.5).min(lowest),
        ),
    };

    let callout = egui::Area::new(egui::Id::new("tutorial_callout"))
        .order(egui::Order::Tooltip)
        .fixed_pos(callout_pos)
        .show(ctx, |ui| {
            egui::Frame::none()
                .fill(ui.visuals().panel_fill)
                .stroke(egui::Stroke::new(1.5, theme::ACCENT))
                .rounding(egui::Rounding::same(8.0))
                .inner_margin(egui::Margin::same(16.0))
                .show(ui, |ui| {
                    ui.set_width(CALLOUT_WIDTH);
                    // NAMED FOR AUTOMATION, and this is the whole reason the
                    // walkthrough went stale unnoticed: no test had ever driven
                    // it, because the harness fixture marked it seen. A name
                    // with a live value lets a test read WHICH step it is on
                    // and WHAT that step says, rather than matching pixels.
                    //
                    // The visible text is "STEP 3 OF 11" and it changes every
                    // step, so it goes in the value slot; the name stays put.
                    access::tag_with_value(
                        ui.label(
                            egui::RichText::new(format!("STEP {} OF {}", index + 1, STEPS.len()))
                                .size(theme::TEXT_LABEL)
                                .strong()
                                .color(theme::ACCENT),
                        ),
                        egui::WidgetType::Label,
                        STEP_NAME,
                        format!("{} of {}", index + 1, STEPS.len()),
                    );
                    ui.add_space(5.0);
                    access::tag_with_value(
                        ui.label(egui::RichText::new(step.title).size(17.0).strong()),
                        egui::WidgetType::Label,
                        TITLE_NAME,
                        step.title,
                    );
                    ui.add_space(7.0);
                    ui.add(egui::Label::new(step.body).wrap());
                    ui.add_space(12.0);
                    ui.horizontal(|ui| {
                        // The button in this position is Next for ten steps and
                        // Finish for the eleventh, so its NAME cannot be its
                        // text: a test that pressed "Next" would fall off the
                        // end of the walkthrough with no way to say so.
                        if index + 1 == STEPS.len() {
                            if access::tag(
                                ui.button("Finish"),
                                egui::WidgetType::Button,
                                FINISH_NAME,
                            )
                            .clicked()
                            {
                                finish = true;
                            }
                        } else if access::tag(
                            ui.button("Next"),
                            egui::WidgetType::Button,
                            NEXT_NAME,
                        )
                        .clicked()
                        {
                            advance = true;
                        }
                        if index > 0
                            && access::tag(ui.button("Back"), egui::WidgetType::Button, BACK_NAME)
                                .clicked()
                        {
                            back = true;
                        }
                        ui.with_layout(
                            egui::Layout::right_to_left(egui::Align::Center),
                            |ui| {
                                // Always available. A walkthrough that traps
                                // somebody is worse than none.
                                if access::tag(
                                    ui.button("Skip"),
                                    egui::WidgetType::Button,
                                    SKIP_NAME,
                                )
                                .clicked()
                                {
                                    // Skipping jumps to the LAST step rather
                                    // than closing, so somebody who skipped
                                    // still learns the walkthrough can be
                                    // replayed - that sentence is already
                                    // written there, so it is shown rather
                                    // than duplicated. From the last step,
                                    // Skip closes.
                                    if index + 1 == STEPS.len() {
                                        finish = true;
                                    } else {
                                        skip_to_end = true;
                                    }
                                }
                            },
                        );
                    });
                });
        });

    ctx.data_mut(|d| d.insert_temp(callout_height_id(), callout.response.rect.height()));

    if advance {
        app.tutorial_step = index + 1;
    }
    if back {
        app.tutorial_step = index.saturating_sub(1);
    }
    if skip_to_end {
        app.tutorial_step = STEPS.len() - 1;
    }
    if finish {
        app.end_tutorial();
    }
    true
}

const CALLOUT_WIDTH: f32 = 340.0;

/// Accessible names the walkthrough publishes. Automation addresses these
/// strings, so they are an API: fixed, and never the visible text, which
/// changes with the step.
pub const STEP_NAME: &str = "tutorial-step";
pub const TITLE_NAME: &str = "tutorial-title";
pub const NEXT_NAME: &str = "tutorial-next";
pub const BACK_NAME: &str = "tutorial-back";
pub const SKIP_NAME: &str = "tutorial-skip";
pub const FINISH_NAME: &str = "tutorial-finish";

/// Steps whose spotlight would land on nothing, or on the wrong rail entry.
///
/// A step names the control it points at and the screen it puts on show, and
/// those two have to agree: the anchor has to be a rail entry that is always
/// drawn, and that entry has to be the one for the step's own view. Get it
/// wrong and the walkthrough still runs - it dims the window, draws no
/// spotlight or spotlights the wrong row, and says nothing. That silence is
/// the whole problem this function exists to break.
///
/// Returns one line per offending step, empty when the walkthrough is sound.
pub fn anchor_problems(steps: &[Step]) -> Vec<String> {
    let mut problems = Vec::new();
    for (i, step) in steps.iter().enumerate() {
        let Some(anchor) = step.anchor else { continue };
        let known = crate::app::nav_entries().find(|(_, label)| *label == anchor);
        match known {
            None => problems.push(format!(
                "step {} anchors on {anchor:?}, which is not a nav entry that is \
                 always drawn",
                i + 1
            )),
            Some((view, _)) if step.view != Some(view) => problems.push(format!(
                "step {} anchors on {anchor:?} but shows {:?}, so the spotlight \
                 and the screen disagree",
                i + 1,
                step.view
            )),
            Some(_) => {}
        }
    }
    problems
}

/// Where the last frame's callout height is kept, so this frame can keep the
/// callout on screen without guessing at it.
fn callout_height_id() -> egui::Id {
    egui::Id::new("tutorial_callout_height")
}

#[cfg(test)]
mod tests {
    use super::{anchor_problems, Step, STEPS};
    use crate::app::View;

    #[test]
    fn every_spotlight_lands_on_a_control_that_exists() {
        let problems = anchor_problems(&STEPS);
        assert!(problems.is_empty(), "{}", problems.join("\n"));
    }

    /// The positive control for the test above. A check that has never been
    /// seen to fail is a check nobody has evidence about - and this whole
    /// ticket exists because the walkthrough drifted for two releases under
    /// gates that were all green.
    #[test]
    fn the_check_catches_a_renamed_control_and_a_wrong_screen() {
        let renamed = [Step {
            anchor: Some("Companies (renamed)"),
            view: Some(View::Companies),
            title: "t",
            body: "b",
        }];
        assert_eq!(anchor_problems(&renamed).len(), 1);

        // Anchor and view both real, and pointing at different screens: the
        // spotlight would sit on the Companies row while the Config page was
        // on show.
        let crossed = [Step {
            anchor: Some("Companies"),
            view: Some(View::Config),
            title: "t",
            body: "b",
        }];
        assert_eq!(anchor_problems(&crossed).len(), 1);
    }

    /// The row title is a hyperlink to the employer's site,
    /// so "click a row to read the posting" sent people out to a browser -
    /// the exact trap that cost a harness debugging session on 2026-08-12.
    #[test]
    fn nobody_is_told_to_click_a_row() {
        for step in &STEPS {
            let body = step.body.to_lowercase();
            assert!(
                !body.contains("click a row") && !body.contains("click the row"),
                "step {:?} still tells people to click a row",
                step.title
            );
        }
        let triage = STEPS
            .iter()
            .find(|s| s.title == "Review what came back")
            .expect("the triage step");
        assert!(
            triage.body.contains(" o opens "),
            "the triage step has to name the key that opens a posting in place"
        );
    }

    /// Seven features shipped after the walkthrough was written and none of
    /// them were in it, which nothing detected.
    ///
    /// THIS TEST CANNOT JUDGE THE WRITING. It proves each subject is present,
    /// not that it is explained well - that stays a reading job. What it does
    /// hold is the failure that actually happened: a feature arriving and the
    /// walkthrough never hearing about it.
    #[test]
    fn the_features_added_after_this_was_written_are_covered() {
        let all = STEPS
            .iter()
            .map(|s| s.body)
            .collect::<Vec<_>>()
            .join(" ")
            .to_lowercase()
            .replace('\n', " ");
        for (subject, needle) in [
            ("attached files", "files is anything you"),
            ("who wrote a file", "yours, or employer"),
            ("the copy kept when you apply", "keeps the posting's own words"),
            ("collectors", "from a collector"),
            ("repost detection", "open the earlier round"),
            ("duplicate grouping", "folded together as the same job"),
            ("removed rows", "removed there show you each"),
            ("the export", "export to a spreadsheet"),
        ] {
            assert!(
                all.contains(needle),
                "the walkthrough says nothing about {subject}"
            );
        }
    }
}
