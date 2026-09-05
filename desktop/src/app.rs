use std::collections::{HashMap, HashSet};
use std::env;
use std::fs;
use std::path::{Path, PathBuf};

use eframe::egui;
use rusqlite::Connection;

use crate::config::{self, Config};
use crate::date;
use crate::config_draft::ConfigDraft;
use crate::db::{self, Company, CurrentStatus, StatusLogEntry, TriageRow};
use crate::engine::{self, EngineMode};
use crate::paths;
use crate::process::RunningProcess;
use crate::profiles::{self, Registry};
use crate::settings::{self, DesktopSettings};
use crate::views;

/// What the kept copy of a posting is called on screen. A .txt because that is
/// what it is - by construction the app never opens this file, it only writes
/// it, so the extension exists purely for the person who downloads it and
/// wants their own machine to open it without ceremony.
const SNAPSHOT_NAME: &str = "posting as it read when you applied.txt";

/// Where a Save dialog opens by default: the Downloads folder, with the
/// target still changeable. By construction this can only suggest: it returns
/// an Option that the dialog takes as a starting directory, and has no way to
/// constrain what the person picks.
///
/// USERPROFILE rather than a crate: it is the variable Windows itself sets.
/// A missing one returns None - again by construction - so the dialog falls
/// back to its own default instead of being handed a path that may not
/// exist.
fn downloads_dir() -> Option<PathBuf> {
    let profile = env::var("USERPROFILE")
        .ok()
        .or_else(|| env::var("HOME").ok())?;
    let candidate = Path::new(&profile).join("Downloads");
    candidate.is_dir().then_some(candidate)
}

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum View {
    Dashboard,
    AllJobs,
    /// Results for what was typed in the rail. A View of its own rather than a
    /// mode of All jobs, which by construction makes leaving a search an act of
    /// navigation - there is no filter left set behind you to remember to
    /// clear.
    Search,
    /// All jobs in its removed scope, as a rail entry of its own.
    Removed,
    Resumes,
    GettingStarted,
    Triage,
    Pipeline,
    Keywords,
    Companies,
    Config,
    Profiles,
    Agent,
}

// (TriageFilter lived here: four predicates applied to rows the triage query
// had already loaded. It is gone. Each of its values is now a Module with its
// own WHERE clause and its own list - see crate::modules for why a filter over
// Triage could not express the modules these have to be.)

/// Which column the triage list is sorted by. Decided 2026-08-05: the sortable
/// list is its own tab, distinct from the dashboard - the dashboard
/// summarises and routes, the list is where scanning happens. Triage IS that
/// list, so sorting was added here rather than building a second table.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum SortBy {
    Score,
    Posted,
    Salary,
    Fit,
    Company,
    Title,
    /// Ordered by what the status MEANS, not by its label - undecided, then
    /// live, then finished. See status::sort_rank.
    Status,
}

/// How many columns the list may be sorted by at once.
///
/// THREE, because two is the pair people actually asked for - status then
/// posted, so undecided rows stay on top and newest leads them - and a third
/// is the tiebreaker that makes the order stable to look at. A fourth would
/// be a rule nobody can hold in their head while reading a table.
pub const MAX_SORT_KEYS: usize = 3;

/// Which face of an opened posting is showing. The description is what a
/// person usually wants; the fit breakdown is what they want when they clicked
/// the number and are asking why it says what it says.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum ExpandedTab {
    Posting,
    Fit,
    /// What is kept beside this job: the resume that was sent, the
    /// confirmation screen, the recruiter's scheduling link.
    Files,
}

/// What a pending retire is about to do, measured once when the confirmation
/// opens rather than recomputed per frame.
///
/// `applied` is the number of the selected rows the person recorded an
/// application for. It is carried separately because it changes what the
/// dialog SAYS: removing forty rows you never applied to is housekeeping,
/// and removing one you did is the kind of thing worth stopping over.
pub struct RetireConfirm {
    pub keys: Vec<String>,
    pub applied: usize,
}

/// A status change waiting on its note.
///
/// EVERY TRANSITION IS OFFERED A NOTE. Not mandatory - the prompt saves
/// on Enter with nothing typed - but offered, because the moment a person
/// knows why something moved is the moment it happens, and an app that never
/// asks ends up holding a column of dates and no story.
///
/// `keys` is a list rather than one key so the bulk bar goes through the same
/// path - by construction there is no single-key variant for it to diverge
/// from. Marking nine rejections in one gesture is one thing that happened,
/// and typing the same sentence nine times is not a feature.
pub struct PendingStatus {
    pub keys: Vec<String>,
    pub status: String,
    /// What the prompt calls the job (or "9 jobs"), settled when it opens.
    /// Stored rather than derived, so by construction the heading cannot
    /// change under a person who is mid-sentence.
    pub subject: String,
    pub note: String,
    /// Only ever filled for an Offer. See status::has_offer_fields.
    pub pay: String,
    pub offer_date: String,
    /// Whether the cursor has already been put in the first field.
    ///
    /// ONE SHOT. Focus is asked for on the frame the prompt opens and never
    /// again. By construction of egui's request_focus, which takes focus on
    /// the frame it runs, asking every frame would return the cursor to the
    /// note each time and leave the Pay and date boxes above it unreachable.
    pub cursor_placed: bool,
}

/// Which population the list is showing. Triage is the working queue -
/// qualified rows the person has not closed out. All jobs is everything still
/// worth seeing, which is a different question and needs a different query.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum ListScope {
    Triage,
    All,
    /// Everything matching the typed terms, across every list. Deliberately
    /// not a narrowing of the current screen: looking for a posting you half
    /// remember is exactly when it must not matter where you were standing.
    Search,
    /// Rows the person removed from their lists. A scope rather than a
    /// separate screen, which by construction gives them the same table,
    /// columns and actions - the way back has to be as ordinary as the way
    /// out, or "delete" is a cliff.
    Retired,
    /// Rows folded behind another posting as the same job. A scope for the
    /// same reason as Retired, and reachable by construction: a grouping the
    /// person cannot inspect and undo is a merge that disappeared a job,
    /// which is the expensive way to be wrong here.
    Duplicates,
    /// One dashboard module, as its own list.
    ///
    /// A SCOPE, NOT A FILTER. These were TriageFilter values applied to rows
    /// the triage query had already returned, which made every module a SUBSET
    /// of Triage - observed: "Taken down" and "No Offer", which Triage hides by
    /// design, could never show anything at all. Each module has to be its OWN
    /// list; being a scope is what makes that true rather than nearly true.
    Module(crate::modules::Module),
}

/// Text-field state for the "New profile" modal (views/new_profile.rs).
/// Plain strings all the way through, same reasoning as ConfigDraft: a
/// String holds anything by construction, so an in-progress edit never has
/// to be a valid profile on its way to becoming one.
#[derive(Default, Clone, Debug)]
pub struct NewProfileDraft {
    /// The person. Typing one that already exists adds a search to them rather
    /// than rejecting the name - by construction, since a person is a key in
    /// the registry and a search is an entry under it. That is the whole point
    /// of the two-level model; refusing it would push people back to naming
    /// two profiles "Maya Ellison (local)" and "Maya Ellison (remote)".
    pub name: String,
    /// What this hunt is called. Blank means the person's first search, which
    /// is named "Default" - the same name profiles::DEFAULT_SEARCH gives a
    /// migrated one-level profile, so by construction the two are
    /// indistinguishable afterwards.
    pub search: String,
    pub home: String,
    pub resume_path: String,
    pub error: Option<String>,
}

/// The engine call behind both halves of a criteria import.
///
/// ONE PLACE, because the two differ by a single flag and getting that wrong
/// is silent in the worst direction: an import that ran without --dry-run
/// while a person was still deciding would apply the change they were shown
/// and then show it to them again as a question.
pub fn criteria_args(path: &std::path::Path, mode: &str, dry_run: bool) -> Vec<String> {
    let mut args = vec![
        "criteria".to_string(),
        "--import".to_string(),
        path.to_string_lossy().into_owned(),
        "--mode".to_string(),
        mode.to_string(),
        "--json".to_string(),
    ];
    if dry_run {
        args.push("--dry-run".to_string());
    }
    args
}

/// A criteria file somebody has chosen but not yet accepted.
pub struct CriteriaImport {
    pub path: std::path::PathBuf,
    /// "merge" or "replace". Switching it re-asks the engine rather than
    /// re-reading the answer: the two modes produce different previews, as
    /// test_a_preview_of_a_replace_counts_what_would_be_lost measures - the
    /// same file removes two entries under replace and none under merge.
    pub mode: String,
    /// What the engine says this would do, or why it would not read the file.
    pub report: Result<crate::criteria::Report, String>,
}

pub struct UnlatchedApp {
    pub conn: Connection,
    pub active_home: PathBuf,
    pub config_path: PathBuf,
    pub settings_path: PathBuf,

    pub config: Config,
    pub config_error: Option<String>,
    pub config_draft: ConfigDraft,
    pub config_status: Option<String>,

    pub settings: DesktopSettings,
    pub engine_mode: EngineMode,

    pub profile_registry: Registry,
    /// How the current person and search are written wherever one is shown.
    /// Derived from the two below via profiles::label, and kept as a field
    /// rather than recomputed: by construction it is read every frame, by both
    /// the window title and the switcher.
    pub profile_name: String,
    pub active_person: String,
    pub active_search: String,
    // True when UNLATCHED_HOME was set at process startup. Every registry
    // write is gated on this by construction, so a scripted or isolated launch
    // - the GUI QC harness, a --home-style override - cannot mutate a real
    // profiles.json. See paths::platform_default_home.
    pub profile_env_locked: bool,
    pub profile_message: Option<String>,
    /// Result of the last Download on the Resumes page, shown on that page.
    /// Separate from profile_message, which the sidebar renders: a person who
    /// just pressed a button on this screen looks for the answer on this
    /// screen, not in the corner of a different one.
    pub resume_message: Option<String>,
    pub show_new_profile_modal: bool,
    /// The "Add a job by link" modal and what has been typed into it.
    pub show_add_job_modal: bool,
    pub add_job_draft: views::add_job::AddJobDraft,
    /// Profile awaiting a confirmed Remove. Set by the Profiles view, cleared
    /// when the modal closes either way - removal is never a single click.
    pub profile_pending_removal: Option<(String, String)>,
    /// A switch requested from a view rather than the sidebar dropdown,
    /// applied once at the end of the frame. Deferring it is what makes the
    /// borrow safe - enforced by the type, since switch_profile takes &mut
    /// self and the view still holds it.
    pub profile_switch_request: Option<(String, String)>,
    /// UNLATCHED_HOME pinned this launch to one folder, which makes the
    /// registry read-only. Read once at startup: by construction the variable
    /// cannot change under a running process, so re-reading it per frame would
    /// answer the same question every time.
    pub profile_locked: bool,
    /// Set when a Save actually CHANGED the config, so the app can offer to
    /// re-run the search. By construction an untouched form leaves this false
    /// and offers nothing - a prompt that appears when nothing happened is
    /// noise, and noise gets clicked through without reading.
    pub offer_run_after_save: bool,
    pub new_profile_draft: NewProfileDraft,
    window_title_set: bool,
    /// Which theme is currently applied to the egui context. Compared before
    /// pushing, so by construction visuals change only when the setting does
    /// rather than on every frame.
    applied_dark: Option<bool>,

    pub view: View,

    /// Bumped by every change to stored data. Each cached view records the
    /// version it loaded at and reloads when its copy is stale.
    ///
    /// WHY A COUNTER AND NOT A CALL AT EACH MUTATION SITE. The
    /// call-at-each-site approach is what shipped, and it was already wrong -
    /// observed: refresh_dashboard() ran in 3 places (construction, profile
    /// switch, and its own Refresh button) and NOT after a status change, so
    /// marking jobs applied left the dashboard counts stale until somebody
    /// pressed Refresh.
    ///
    /// A counter cannot be FORGOTTEN, because the check lives in the render
    /// path rather than at the mutation. It is also lazy: only the view being
    /// drawn reloads, so this is cheaper than reloading everything on a change.
    pub data_version: u64,
    dashboard_loaded_at: u64,
    triage_loaded_at: u64,
    pipeline_loaded_at: u64,
    keywords_loaded_at: u64,
    companies_loaded_at: u64,
    counts_loaded_at: u64,

    pub dashboard_stats: Option<crate::dashboard::DashboardStats>,
    /// What each CONFIGURED collector last delivered, read when the data
    /// changes rather than while drawing - by construction, since it is
    /// refreshed off data_version and never from the render path.
    ///
    /// The stamps are cached; whether they are late is not. See
    /// collectors_pending - a cached verdict would stop moving on the one
    /// day nothing changes, which is precisely the day a collector dies.
    /// Hours since each collector's FILE was last written, by path, with
    /// the moment it was measured. Re-statted on demand rather than taken
    /// from the engine's listing, which is only fetched when a profile
    /// opens - see file_age_hours.
    file_ages: std::collections::HashMap<String, (std::time::Instant, Option<f64>)>,
    collector_stamps: Vec<(String, Option<String>)>,
    /// Which collector ids collector_stamps was read for. The listing arrives
    /// on a background thread AFTER the first dashboard load - observed, and
    /// the reason this field exists: without it the stamps stayed empty for
    /// the whole session, so a thread that had not answered yet read exactly
    /// like a database with nothing in it.
    collector_stamps_ids: Vec<String>,
    /// When each collector's file was last taken in, as the ENGINE recorded
    /// it. Preferred over collector_stamps, which by construction can only say
    /// when a row from them last ARRIVED - a file of nothing but closures
    /// imports perfectly and moves no row's timestamp. See
    /// db::collector_taken_in.
    collector_taken: Vec<(String, Option<String>)>,
    /// This machine's UTC offset, cached with them. It moves twice a year.
    ///
    /// READ BY EVERY DATE THE SCREENS SHOW, not just the collector
    /// staleness it was added for. db::local_offset_secs is a query, and a
    /// query in the draw path runs on a frame that is already drawing.
    pub local_offset: i64,

    /// Three small counts the Triage and Dashboard headers show, held here
    /// rather than queried while drawing.
    ///
    /// MEASURED, 2026-08-14, on a real 81 MB board: these were three call
    /// sites running six `COUNT(*)` statements EVERY FRAME - `manual_link_state`
    /// alone is three correlated `NOT EXISTS` subqueries over every job, with a
    /// `TRIM()` that stops an index being used. Frames took 510-600 ms, so
    /// pointer highlights and clicks lagged by half a second on every screen
    /// that showed one of these numbers. They change only when the data does,
    /// which is exactly what `data_version` already tracks.
    pub manual_links: crate::db::ManualLinkState,
    pub retired_count: i64,
    pub duplicate_count: i64,

    pub triage_rows: Vec<TriageRow>,
    /// What is typed in the rail's search box, verbatim.
    pub search_query: String,
    /// The parsed terms the last search actually ran with. Kept apart from
    /// the raw text, so by construction the results heading names what
    /// produced them rather than what is currently in the box - a half-typed
    /// word cannot relabel a finished result set.
    pub search_terms: Vec<String>,
    /// When the box was last typed into, if a search is still owed for it.
    /// None means what is on screen already matches what is in the box.
    pub search_typed_at: Option<std::time::Instant>,
    /// How many rows the last search returned.
    ///
    /// HELD, NOT READ OFF THE CURRENT LIST. The caption sits under the
    /// search box, so it reads as a search result; taking it from
    /// triage_rows meant that navigating to Triage with a term still in
    /// the box relabelled Triage's 671 rows as matches.
    pub search_found: usize,
    pub triage_show_all: bool,
    /// Off by default: one opening listed in five cities is five rows of the
    /// same triage decision - the count comes from the engine's own grouping,
    /// see dupes.py. On, nothing is folded.
    pub triage_every_location: bool,
    pub triage_selected: Option<String>,
    /// The row whose posting is open inline. An Option rather than a set, so
    /// by construction only one can be open: an accordion of several open
    /// blocks is a wall of text with no list left in it.
    pub triage_expanded: Option<String>,
    /// Bring `triage_selected` into view on the next frame, then stop.
    ///
    /// ARRIVING AT A LIST IS NOT THE SAME AS SEEING THE ROW. Opening a card on
    /// the Pipeline already selected and expanded the job and switched to All
    /// jobs - and landed at the TOP of ten thousand rows, with the open posting
    /// somewhere below the fold. From the reader's side the click did nothing
    /// but change screens.
    ///
    /// One frame only - cleared by construction after it is consumed. A
    /// sticky flag would re-scroll on every frame and fight the scrollbar
    /// each time the person tried to look somewhere else.
    pub scroll_to_selected: bool,
    pub expanded_tab: ExpandedTab,
    /// Which list is on screen. A dashboard tile sets this to its own module
    /// rather than leaving a filter on Triage - see ListScope::Module.
    pub list_scope: ListScope,
    /// Sort column and direction for the list. Defaults to score descending,
    /// which is the order the query already returns.
    /// The sort, most significant first. Never longer than MAX_SORT_KEYS.
    ///
    /// A LIST RATHER THAN ONE COLUMN, because "newest first" is only useful
    /// among rows that are still worth reading: sorted by posted date alone, a
    /// job passed on last month sits above one that arrived this morning. The
    /// wanted default is status then posted, which one key cannot say.
    pub triage_sort: Vec<(SortBy, bool)>,
    pub triage_note_open: bool,
    pub triage_note_just_opened: bool,
    pub triage_note_buffer: String,
    pub triage_message: Option<String>,

    /// The job list's columns, left to right, and which of them are turned
    /// off. Held as ids rather than as the strings on disk: by construction an
    /// unrecognised string cannot become a ColumnId, so a typo in the settings
    /// file is dropped at the parse rather than reaching the drawing code.
    /// See views::columns::from_keys_lenient.
    pub column_order: Vec<views::columns::ColumnId>,
    pub column_hidden: Vec<views::columns::ColumnId>,
    pub show_column_settings: bool,

    /// Rows ticked for a bulk action, by jobs.key.
    ///
    /// Keys, never row indices. The list re-sorts and re-filters under the
    /// person while they are selecting, and a key names the same job however
    /// the rows move - by construction, since it is the primary key the
    /// database uses. An index-based selection would come to mean different
    /// rows than the ones they ticked.
    pub selected_keys: HashSet<String>,
    /// Whether the retire confirmation is up, and what it needs to say.
    pub confirm_retire: Option<RetireConfirm>,
    /// The status change being written, while its note is being typed.
    pub pending_status: Option<PendingStatus>,
    /// Status changes a locked database refused, kept to re-apply when it
    /// frees up. NEVER dropped: this is the person's own typing, and a
    /// collect's final passes hold the write lock for longer than any timeout
    /// it would be reasonable to freeze the window for - believed from the
    /// observed run lengths (13 to 32 minutes on a live profile), not measured
    /// against the lock itself.
    deferred_status: Vec<PendingStatus>,
    /// The All jobs screen showing what was removed instead of what is live.
    pub show_retired: bool,
    /// The All jobs screen showing what was grouped as a duplicate.
    pub show_duplicates: bool,
    /// Where the last export went, or why it did not. Shown beside the button.
    pub export_message: Option<String>,
    /// Where the last criteria export went, or why it did not.
    pub criteria_message: Option<String>,
    /// The import being decided on: what was picked, which way it would be
    /// brought in, and what that would do. `None` when no dialog is open.
    ///
    /// HELD RATHER THAN RECOMPUTED WHILE DRAWING. The preview costs a process,
    /// and a dialog that re-ran the engine every frame would spawn hundreds
    /// while somebody read it.
    pub criteria_import: Option<CriteriaImport>,
    /// When the automatic copy was last written. Compared against before
    /// writing, so by construction a burst of keyboard triage cannot rewrite
    /// it once per keystroke.
    pub last_backup: Option<std::time::Instant>,
    /// Set when the person followed the pointer from the job list, so Config
    /// opens with the added-links section expanded instead of dropping them on
    /// a page of collapsed headings to hunt through. Cleared once it is used,
    /// so by construction it steers exactly one visit.
    pub focus_added_links_setting: bool,

    pub pipeline_log: Vec<StatusLogEntry>,
    /// Notes written outside a status change. Kept apart from the log because
    /// they ARE apart in the database, by construction: job_note is its own
    /// table. See db::add_note for why a note-only row in job_status_log would
    /// read as an erased status instead.
    pub pipeline_notes: Vec<db::Note>,
    pub pipeline_current: HashMap<String, CurrentStatus>,
    pub pipeline_job_info: HashMap<String, (String, Option<String>)>,

    pub keywords_report: Vec<views::keywords::KeywordDemand>,
    pub keywords_show_all: bool,
    pub keywords_corpus_size: usize,
    pub keywords_message: Option<String>,
    /// Why the coverage half of the keywords report is empty, when it is.
    /// Without it the two causes are indistinguishable by construction - "no
    /// readable resume" and "a resume that matches nothing" both produce an
    /// empty list, and would draw the identical screen.
    pub keywords_resume_gap: Option<views::keywords::ResumeGap>,

    /// Attachments for the job whose row is open, and which job they are for.
    /// Loaded per opened row rather than for the whole board - by construction
    /// only one row is open at a time (see triage_expanded), and a live
    /// profile holds thousands of jobs.
    pub attachments: Vec<crate::attachments::Attachment>,
    pub attachments_for: Option<String>,
    pub attachment_message: Option<String>,
    /// The "add a link" prompt: a modal, like the note prompt. A modal draws
    /// over the row rather than inside it by construction, so the open row
    /// stays a read-only view of what is already there.
    pub link_prompt_open: bool,
    pub link_url: String,
    pub link_label: String,

    pub companies: Vec<Company>,
    /// The handoff collectors this profile has, fetched off the UI thread.
    /// Rebuilt when the profile opens and when config.json is reloaded.
    /// Those are the only two events that change which collectors exist,
    /// by construction: the list is read from that file, for that home.
    pub handoffs: crate::collectors::Collectors,
    pub new_company_name: String,
    pub log_lines: Vec<String>,
    /// Where in `log_lines` the endpoint test started, so its result can be
    /// shown ON the Agents card. An index rather than a copy, which
    /// by construction cannot disagree with the log it points into. Sending the
    /// reader to another tab to find out whether their address works is the
    /// opposite of a test button.
    pub agent_check_from: Option<usize>,

    pub tutorial_active: bool,
    pub tutorial_step: usize,
    /// Screen rects of the sidebar entries, refilled every frame from the
    /// widgets themselves - so by construction the spotlight follows the real
    /// control, at whatever size and position it was actually drawn.
    pub tutorial_anchors: HashMap<&'static str, egui::Rect>,
    pub running_process: Option<(String, RunningProcess)>,
    /// What the last engine run did, kept AFTER it ends: the sentence, and
    /// whether it went well.
    ///
    /// An indicator that simply disappears is not an answer - by construction
    /// an absent indicator is one state for three different endings: the run
    /// finished, it was killed, or the app lost track of it.
    ///
    /// THE FLAG IS CARRIED, NOT RE-DERIVED. The bar used to colour itself by
    /// searching the sentence for the word "failed", which is the exit code
    /// read back out of a string written for a person - so a run whose LABEL
    /// happened to contain that word would have shown red on success. The
    /// fact is known here; there is no reason to spell it into English and
    /// parse it out again.
    pub last_run_result: Option<(String, bool)>,
    /// The last thing the engine actually SAID, for the line above.
    ///
    /// "pull Handoff file finished" is not an answer to "did it take anything
    /// in". Observed on the installed build, 2026-09-03: a handoff whose
    /// contents were already imported printed "nothing new from imported" and
    /// exited 0, so the run finished, the strip said so, the dashboard did not
    /// move, and it read as a button that does nothing. The engine's own
    /// sentence went to the log, and the log renders on one screen, which is
    /// not the screen the button is on.
    pub last_engine_line: Option<String>,
    /// Person-present commands waiting for the engine to be free.
    ///
    /// WHY A QUEUE AND NOT A REFUSAL. Only one engine command runs at a time,
    /// and a scheduled collect can hold that for half an hour (measured: 32
    /// minutes on a live profile). Refusing meant somebody pasting a job they
    /// had just applied for got nothing - the dialog closed, no row was
    /// written, and the reason went to a log rendered on another screen.
    /// Waiting is what a person would have done by hand anyway.
    ///
    /// SCHEDULED WORK IS NOT QUEUED. A refresh that cannot run now comes
    /// round again on its own - by construction, since start_scheduled_refresh
    /// is consulted on a timer and this queue is only fed by queue_process.
    /// Queueing it would stack copies of the same crawl behind each other.
    pub pending: std::collections::VecDeque<(String, Vec<String>)>,
    /// When the daily schedule was last consulted - see
    /// start_scheduled_refresh. An Instant rather than a wall clock, which
    /// by construction a clock change cannot move backwards.
    last_refresh_check: std::time::Instant,
    /// How long to wait before consulting it again, as the ENGINE last
    /// reported it. None until an engine run has said so - by construction the
    /// value can only come from parse_wake_in on the engine's output - which
    /// is why the fixed interval is still the fallback.
    next_refresh_check: Option<std::time::Duration>,
}

/// Everything opened from a profile's home directory: the database
/// connection plus the paths and parsed documents that go with it. One
/// function shared by `UnlatchedApp::new` and `switch_profile`, so
/// by construction startup and a runtime profile switch cannot open a home's
/// files differently from each other.
struct OpenedHome {
    conn: Connection,
    config_path: PathBuf,
    settings_path: PathBuf,
    config: Config,
    config_error: Option<String>,
    settings: DesktopSettings,
}

fn open_home(home: &Path) -> Result<OpenedHome, String> {
    fs::create_dir_all(home).map_err(|e| format!("could not create {}: {e}", home.display()))?;

    let db_path = paths::db_path(home);
    let config_path = paths::config_path(home);
    let settings_path = paths::desktop_settings_path(home);

    let conn = db::open(&db_path)
        .map_err(|e| format!("could not open database at {}: {e}", db_path.display()))?;
    // THE CLOCK, BEFORE ANYTHING CAN BE WRITTEN. date::now_iso reads this to
    // stamp in local time, and it defaults to zero - which is UTC. Setting it
    // only when the dashboard refreshes left every write before that stamped
    // in the wrong zone, by five hours for somebody in Chicago and eight in
    // Los Angeles. This is the one point every path goes through.
    crate::date::set_local_offset(db::local_offset_secs(&conn));
    let (config, config_error) = config::load(&config_path);
    let settings = settings::load(&settings_path);

    Ok(OpenedHome {
        conn,
        config_path,
        settings_path,
        config,
        config_error,
        settings,
    })
}

/// Env var wins over the registry, exactly like the engine's own --home /
/// UNLATCHED_HOME precedence. Empty-string is treated as unset
/// by construction - the filter below drops it - matching
/// paths::resolve_data_dir, so `UNLATCHED_HOME=` in a launcher script cannot
/// silently lock the app to "".
fn env_home_override() -> Option<String> {
    env::var("UNLATCHED_HOME")
        .ok()
        .filter(|v| !v.trim().is_empty())
}

/// Height the bottom nav group needs: its heading, two 30px rows, the spacing
/// between them, and a margin off the window edge.
///
/// Measured, not guessed - the first value predated the taller nav rows and
/// pushed "Settings" half off the bottom of the window, where it was still
/// clickable but only half drawn.
const NAV_BOTTOM_GROUP_HEIGHT: f32 = 124.0;

/// How often a running app re-asks the engine whether a collection is owed.
/// Half an hour: the schedule it is asking about has slots half an hour apart
/// at their closest - see refresh.DEFAULT_ANCHORS and WEEKEND_ANCHORS - so
/// by construction asking more often can only spawn a process to be told "no".
const REFRESH_CHECK_EVERY: std::time::Duration = std::time::Duration::from_secs(30 * 60);

/// The nav rail, in the order it is drawn, as data rather than as three
/// literal arrays inside the draw call.
///
/// IT IS DATA BECAUSE THE WALKTHROUGH ADDRESSES IT BY NAME. `tutorial::STEPS`
/// spotlights a rail entry by its label, and a label that no longer exists
/// spotlights nothing at all - silently, since by construction a missing
/// anchor just draws the plain dim. Renaming "Companies" would have left a
/// step describing a screen with no arrow pointing at anything, and nothing
/// in the build would have said so. With the labels in one place,
/// tutorial::tests holds the walkthrough against them.
pub const NAV_JOBS: [(View, &str); 5] = [
    (View::Dashboard, "Dashboard"),
    (View::Triage, "Triage"),
    (View::Pipeline, "Pipeline"),
    (View::AllJobs, "All jobs"),
    // BELOW All jobs, because that is what it is a way back from. It was
    // reachable only as a chip in the All jobs toolbar - observed on a live
    // profile holding 29 removed rows whose owner did not know they could be
    // seen at all.
    (View::Removed, "Removed"),
];
pub const NAV_TOOLS: [(View, &str); 4] = [
    (View::Keywords, "Keywords"),
    (View::Resumes, "Resumes"),
    (View::Companies, "Companies"),
    (View::Agent, "Agents"),
];
pub const NAV_SETUP: [(View, &str); 2] = [(View::Config, "Config"), (View::Profiles, "Settings")];
/// Only drawn while it is needed, which is why a walkthrough step must never
/// anchor on it: by construction it is absent from the rail on a home that has
/// finished setting up, and an anchor with no widget draws no spotlight.
pub const NAV_GETTING_STARTED: (View, &str) = (View::GettingStarted, "Getting started");

/// Every rail entry that is ALWAYS drawn, which is the set a walkthrough step
/// may point at. Getting started is excluded by construction: it is drawn
/// only while it is needed.
///
/// Used by the tutorial's anchor check, which is a test.
#[cfg(test)]
pub fn nav_entries() -> impl Iterator<Item = (View, &'static str)> {
    NAV_JOBS
        .into_iter()
        .chain(NAV_TOOLS)
        .chain(NAV_SETUP)
}

/// The search box, in the rail so it is reachable from every screen.
///
/// IN THE RAIL BECAUSE IT BELONGS TO THE WINDOW. Searching from the
/// dashboard, from Triage and from All jobs are three requests for the same
/// thing: a way in that does not depend on where you are standing. Drawn once
/// in the rail, so by construction it is on every screen. The run indicator
/// is in the window chrome for the identical reason.
///
/// SEARCHES AS YOU TYPE, and also on Enter - both paths call run_search, so
/// by construction they cannot answer differently. Typing is how a person
/// explores a half-remembered posting; making them press a key first turns
/// three guesses into three round trips. Enter still works because people
/// press it.
fn show_search_box(app: &mut UnlatchedApp, ui: &mut egui::Ui) {
    let response = crate::access::tag(
        ui.add(
            egui::TextEdit::singleline(&mut app.search_query)
                .hint_text("Search all jobs")
                .desired_width(f32::INFINITY),
        ),
        egui::WidgetType::TextEdit,
        "search-box",
    );

    // WHY A PAUSE RATHER THAN A KEYSTROKE. Each search is a full refresh -
    // query, location collapse, sort, selection rebuild, table rebuild - and
    // running that per character did eight of them to produce one answer the
    // person only reads at the end. Measured before changing: the SQL itself
    // is ~1.4 ms over 176 rows, so the query was never the cost; the repetition
    // was.
    const SETTLE: std::time::Duration = std::time::Duration::from_millis(180);
    // One character matches nearly every row - by construction, since the
    // query is a substring match - which is the most expensive search and the
    // least useful answer, because "everything" is not a result.
    const MIN_CHARS: usize = 2;

    if response.changed() {
        app.search_typed_at = Some(std::time::Instant::now());
    }

    let entered = response.lost_focus() && ui.input(|i| i.key_pressed(egui::Key::Enter));
    if entered {
        // Enter means now, whatever was typed - including a single character.
        app.search_typed_at = None;
        app.run_search();
    } else if let Some(at) = app.search_typed_at {
        let waited = at.elapsed();
        if waited >= SETTLE {
            app.search_typed_at = None;
            if app.search_query.trim().chars().count() >= MIN_CHARS
                || app.search_query.trim().is_empty()
            {
                app.run_search();
            }
        } else {
            // egui only draws on events by construction, so without this the
            // pause never elapses on screen: it would sit waiting for the next
            // keystroke to notice that typing had stopped.
            ui.ctx().request_repaint_after(SETTLE - waited);
        }
    }

    // A way out that does not require selecting the text and deleting it.
    // Only drawn when there is something to clear, so by construction the rail
    // is quiet on every screen where no search is running.
    if !app.search_query.is_empty() {
        ui.horizontal(|ui| {
            ui.small(format!("{} found", app.search_found));
            if crate::access::tag(
                ui.small_button("Clear"),
                egui::WidgetType::Button,
                "search-clear",
            )
            .clicked()
            {
                app.search_query.clear();
                app.search_terms.clear();
                app.search_found = 0;
                // Cleared by construction along with the text: otherwise the
                // pending keystroke fires after the clear and puts the person
                // straight back into an empty search.
                app.search_typed_at = None;
                app.view = View::AllJobs;
                app.list_scope = ListScope::All;
                app.refresh_triage();
            }
        });
    }
}

fn nav_entry(app: &mut UnlatchedApp, ui: &mut egui::Ui, view: View, label: &'static str) {
    let response = crate::theme::nav_row(ui, label, app.view == view);
    // Recorded every frame from the response itself, so by construction the
    // walkthrough spotlights the control where it actually is, at any window
    // size.
    app.tutorial_anchors.insert(label, response.rect);
    if response.clicked() {
        app.view = view;
        // CLICKING TRIAGE MEANS TRIAGE. A dashboard module opens as its own
        // list inside this view, and that scope used to survive a nav click -
        // so the rail highlighted Triage while the screen still said
        // "Everything you have applied to", with only the small Back to
        // Triage button actually leaving. A navigation entry that lights up
        // without navigating is worse than one that does nothing.
        //
        // Found by the QC harness: it clicked Triage, then looked for a job
        // that was in the triage list and not in the module's, and could not
        // find it (2026-08-12).
        if view == View::Triage {
            app.leave_module();
        }
    }
}

/// What to do with a request that arrives while the engine is busy.
#[derive(Debug, PartialEq, Eq)]
enum QueueDecision {
    /// The identical request is already waiting.
    AlreadyWaiting,
    /// The queue is at its cap.
    Full,
    /// Add it.
    Accept,
}

/// The queueing rules, free of the app so they can be tested.
///
/// IDENTICAL MEANS LABEL AND ARGUMENTS. Pressing Add twice during a collect
/// must add the job once; two DIFFERENT jobs pasted during the same collect
/// must both survive, and by construction they differ in their arguments
/// because the URL is one of them. Both directions are tested - see
/// the_same_request_twice_waits_once and two_different_jobs_both_wait.
fn queue_decision(
    pending: &std::collections::VecDeque<(String, Vec<String>)>,
    label: &str,
    args: &[String],
) -> QueueDecision {
    if pending.iter().any(|(l, a)| l == label && a.as_slice() == args) {
        return QueueDecision::AlreadyWaiting;
    }
    if pending.len() >= MAX_PENDING {
        return QueueDecision::Full;
    }
    QueueDecision::Accept
}

/// How many person-present commands may wait at once.
///
/// A CAP, because the queue is memory and by construction nothing drains it
/// while the engine is stuck, so it would otherwise grow without limit.
/// Twenty is believed, not measured: far more than anybody queues by hand,
/// and small enough that reaching it is a real signal something is wrong.
const MAX_PENDING: usize = 20;

/// The sentence shown when a command is queued rather than run now.
///
/// SAYS WHAT IT IS WAITING FOR. "Queued" alone reads like the app absorbed
/// the request and did nothing; naming the run that holds the engine is the
/// difference between waiting and pressing the button again. The name comes
/// from running_process, so by construction it is the run actually holding
/// the lock rather than a guess at one.
fn queued_message(label: &str, running: &str, position: usize) -> (String, bool) {
    let where_in_line = if position <= 1 {
        String::new()
    } else {
        format!(" ({position} waiting)")
    };
    (
        format!("{label} is queued behind {running}{where_in_line} and will run when it finishes."),
        true,
    )
}

/// The sentence shown when a command is refused because another is running.
///
/// NAMES BOTH: what was refused and what is holding the lock, taken from
/// running_process rather than guessed - so by construction the sentence
/// cannot name a run that is not the one blocking. "Busy" alone leaves
/// somebody guessing whether the app is stuck or working, and the answer
/// decides whether they wait or investigate.
fn skipped_message(label: &str, running: &str) -> (String, bool) {
    (
        format!("{label} did not run - {running} is still going. Try again when it finishes."),
        false,
    )
}

/// The sentence the status strip shows when a run ends.
///
/// FREE OF THE APP, so it can be tested: the four cases are a two-by-two of
/// "did it work" and "did the engine say anything", and the interesting one -
/// success with something to report - is exactly the case that used to be
/// thrown away.
///
/// TRUNCATED HERE rather than at the call site. The running half of the strip
/// already truncates what it shows; the finished half did not, because
/// by construction it only ever showed a label it had built itself - and now it
/// carries a sentence the engine wrote, of no fixed length.
fn finish_message(label: &str, code: i32, said: Option<&str>) -> (String, bool) {
    let said = said.map(|s| crate::fmt::truncate(s, 120));
    match (code == 0, said) {
        (true, Some(said)) => (format!("{label}: {said}"), true),
        (true, None) => (format!("{label} finished"), true),
        (false, Some(said)) => (format!("{label} failed (exit code {code}): {said}"), false),
        (false, None) => (format!("{label} failed (exit code {code}) - see the log"), false),
    }
}

impl UnlatchedApp {
    pub fn new() -> Self {
        let profile_env_locked = env_home_override().is_some();
        let profile_registry = profiles::load();

        let (active_person, active_search, active_home) = if profile_env_locked {
            (
                profiles::ENV_PROFILE.to_string(),
                profiles::DEFAULT_SEARCH.to_string(),
                paths::data_dir(),
            )
        } else {
            let active = &profile_registry.active;
            let person = if active.person.trim().is_empty() {
                profiles::DEFAULT_PROFILE.to_string()
            } else {
                active.person.clone()
            };
            let search = if active.search.trim().is_empty() {
                profiles::DEFAULT_SEARCH.to_string()
            } else {
                active.search.clone()
            };
            let home = profiles::home_for(&profile_registry, &person, &search);
            (person, search, home)
        };
        let profile_name = profiles::label(&active_person, &active_search);

        let opened = open_home(&active_home).unwrap_or_else(|e| {
            // A profile that cannot be opened at all is unrecoverable for
            // this session: by construction every view reads through this
            // connection, so the degraded mode would be an Option threaded
            // through all of them. Surface it immediately instead.
            panic!(
                "could not open profile '{profile_name}' at {}: {e}",
                active_home.display()
            );
        });
        let config_draft = ConfigDraft::from_config(&opened.config);
        let engine_mode = engine::resolve();
        // Read before `opened.settings` is moved into the struct below.
        let column_order = views::columns::from_keys(&opened.settings.column_order);
        let column_hidden = views::columns::from_keys_lenient(&opened.settings.column_hidden);

        let mut app = UnlatchedApp {
            conn: opened.conn,
            active_home,
            config_path: opened.config_path,
            settings_path: opened.settings_path,
            config: opened.config,
            config_error: opened.config_error,
            config_draft,
            config_status: None,
            settings: opened.settings,
            engine_mode,
            profile_registry,
            active_person,
            active_search,
            profile_name,
            profile_env_locked,
            profile_message: None,
            resume_message: None,
            show_new_profile_modal: false,
            show_add_job_modal: false,
            add_job_draft: views::add_job::AddJobDraft::default(),
            profile_pending_removal: None,
            profile_switch_request: None,
            profile_locked: std::env::var_os("UNLATCHED_HOME").is_some(),
            offer_run_after_save: false,
            new_profile_draft: NewProfileDraft::default(),
            window_title_set: false,
            applied_dark: None,
            // Replaced below once the profile has been read -
            // by construction this value cannot depend on the config, which is
            // not loaded yet. A profile with no titles and no employers ends
            // up on the guidance instead of an empty triage table.
            view: View::Dashboard,
            dashboard_stats: None,
            file_ages: std::collections::HashMap::new(),
            collector_stamps: Vec::new(),
            collector_stamps_ids: Vec::new(),
            collector_taken: Vec::new(),
            local_offset: 0,
            // Data at 1, views at 0, so by construction a cache that is
            // never loaded compares as STALE rather than current. The
            // constructor below loads all five eagerly and stamps them, so
            // nothing is actually stale on the first frame - this is the
            // safety net for the case where that eager load is later removed,
            // or a sixth cache is added without one. Starting them equal
            // would make an unloaded cache look fresh, which is the wrong
            // direction to fail in.
            data_version: 1,
            dashboard_loaded_at: 0,
            triage_loaded_at: 0,
            pipeline_loaded_at: 0,
            keywords_loaded_at: 0,
            companies_loaded_at: 0,
            counts_loaded_at: 0,

            manual_links: crate::db::ManualLinkState::default(),
            retired_count: 0,
            duplicate_count: 0,

            triage_rows: Vec::new(),
            search_query: String::new(),
            search_terms: Vec::new(),
            search_typed_at: None,
            search_found: 0,
            triage_show_all: false,
            list_scope: ListScope::Triage,
            // Undecided first, newest of those at the top, best-scoring
            // among same-day arrivals. The queue's own reading order.
            triage_sort: vec![
                (SortBy::Status, false),
                (SortBy::Posted, true),
                (SortBy::Score, true),
            ],
            triage_every_location: false,
            triage_selected: None,
            triage_expanded: None,
            scroll_to_selected: false,
            expanded_tab: ExpandedTab::Posting,
            triage_note_open: false,
            triage_note_just_opened: false,
            triage_note_buffer: String::new(),
            triage_message: None,
            column_order,
            column_hidden,
            show_column_settings: false,
            selected_keys: HashSet::new(),
            confirm_retire: None,
            show_retired: false,
            show_duplicates: false,
            export_message: None,
            criteria_message: None,
            criteria_import: None,
            last_backup: None,
            focus_added_links_setting: false,
            pending_status: None,
            deferred_status: Vec::new(),
            pipeline_log: Vec::new(),
            pipeline_notes: Vec::new(),
            pipeline_current: HashMap::new(),
            pipeline_job_info: HashMap::new(),
            keywords_report: Vec::new(),
            keywords_show_all: false,
            keywords_corpus_size: 0,
            keywords_message: None,
            keywords_resume_gap: None,
            attachments: Vec::new(),
            attachments_for: None,
            attachment_message: None,
            link_prompt_open: false,
            link_url: String::new(),
            link_label: String::new(),
            companies: Vec::new(),
            handoffs: crate::collectors::Collectors::default(),
            new_company_name: String::new(),
            log_lines: Vec::new(),
            agent_check_from: None,
            tutorial_active: false,
            tutorial_step: 0,
            tutorial_anchors: HashMap::new(),
            running_process: None,
            last_run_result: None,
            last_engine_line: None,
            pending: std::collections::VecDeque::new(),
            last_refresh_check: std::time::Instant::now(),
            next_refresh_check: None,
        };
        // Loaded here rather than left to the first frame: by construction
        // the code below decides the landing view from what these contain, so
        // it cannot run before them. An empty triage table routes a first-time
        // user to guidance instead of a dashboard of zeros. sync_caches then
        // finds them current and does not repeat the work, because the stamps
        // below record the version they loaded at.
        app.refresh_triage();
        app.refresh_pipeline();
        app.refresh_keywords();
        app.refresh_companies();
        // Off the UI thread and started here rather than when the menu opens.
        // Starting the frozen engine costs the better part of a second -
        // believed from how long a cold PyInstaller launch takes, not measured
        // here - and a menu that freezes when clicked is worse than one that
        // fills in a moment after the window appears.
        app.refresh_handoffs();
        app.refresh_dashboard();
        app.triage_loaded_at = app.data_version;
        app.pipeline_loaded_at = app.data_version;
        app.keywords_loaded_at = app.data_version;
        app.companies_loaded_at = app.data_version;
        app.dashboard_loaded_at = app.data_version;
        // Decided after the profile is loaded, because by construction it
        // reads what that profile contains. A first-time user meeting an empty
        // triage table has no idea what to do next; the guidance says so, and
        // stops appearing once it is no longer true - see
        // views::getting_started::needed.
        // The dashboard is the landing view (decided 2026-08-05): it summarises
        // and routes, while the sortable list lives in its own tab. First-run
        // guidance still wins over it - a dashboard of zeros teaches nothing
        // to someone who has not set up a search yet.
        // The walkthrough opens itself for somebody who has never seen it,
        // and by construction only once: the flag it reads is written to the
        // profile's settings. It comes ahead of the static guidance page
        // because a spotlight on the real control beats a page of prose
        // describing where that control is.
        if !app.settings.tutorial_seen {
            app.start_tutorial();
        }
        if views::getting_started::needed(&app) {
            app.view = View::GettingStarted;
        }
        app.start_scheduled_refresh();
        app
    }

    /// Asks the engine to collect, if the schedule says it is owed.
    ///
    /// On open, because by construction that is the only moment this app
    /// reliably has - it is not a service, and a schedule nothing consults is
    /// a setting that lies. The engine decides, through the same refresh.py
    /// the Config page documents, so there is one rule rather than a Rust copy
    /// drifting from a Python one. It also means somebody who was away for
    /// four days gets those four days' postings the moment they come back -
    /// see refresh.due, which treats a gap of a whole day as owed at once.
    ///
    /// Nothing is shown while it runs. It appears as jobs arriving, which is
    /// what was asked for; the reason it did or did not run is in the log.
    fn start_scheduled_refresh(&mut self) {
        if !self.config.refresh.daily || self.running_process.is_some() {
            return;
        }
        self.start_process("scheduled refresh", vec!["refresh".to_string()]);
    }

    /// "Unlatched" for the built-in Default profile, "Unlatched - <name>"
    /// otherwise - including the env-locked "(env)" pseudo-profile, which
    /// by construction is never a name a person can register under. See
    /// profiles::ENV_PROFILE.
    pub fn window_title(&self) -> String {
        if self.profile_name == profiles::DEFAULT_PROFILE {
            "Unlatched".to_string()
        } else {
            format!("Unlatched - {}", self.profile_name)
        }
    }

    /// Closes the current database connection and reopens everything (db,
    /// config, desktop settings) rooted at `home`, then refreshes every
    /// view and repoints the window title. Does not touch profiles.json:
    /// callers that are changing which profile is active register/persist
    /// that first (see profiles::register_and_activate). By construction,
    /// then, a failed reopen here cannot leave the registry pointing at a
    /// profile the app was unable to load.
    pub fn switch_profile(
        &mut self,
        person: &str,
        search: &str,
        home: PathBuf,
        ctx: &egui::Context,
    ) {
        let name = &profiles::label(person, search);
        match open_home(&home) {
            Ok(opened) => {
                self.conn = opened.conn;
                self.active_home = home;
                self.config_path = opened.config_path;
                self.settings_path = opened.settings_path;
                self.config = opened.config;
                self.config_error = opened.config_error;
                self.config_draft = ConfigDraft::from_config(&self.config);
                self.config_status = None;
                // Per profile by construction - the layout is read from that
                // home's desktop_settings.json - so switching searches brings
                // that search's columns rather than carrying the last one's.
                self.column_order = views::columns::from_keys(&opened.settings.column_order);
                self.column_hidden =
                    views::columns::from_keys_lenient(&opened.settings.column_hidden);
                self.settings = opened.settings;
                self.profile_name = name.to_string();
                self.active_person = person.to_string();
                self.active_search = search.to_string();
                self.profile_message = None;
                // Names a file belonging to the profile being left behind.
                self.resume_message = None;

                self.triage_selected = None;
                // A module opened from the previous profile's dashboard
                // counts rows that by construction do not exist in this one -
                // the keys belong to another database - so the switch lands on
                // the working queue rather than on somebody else's slice.
                self.list_scope = ListScope::Triage;
                self.triage_note_open = false;
                self.triage_note_buffer.clear();
                self.new_company_name.clear();
                self.log_lines.clear();
                self.running_process = None;

                self.mark_data_changed();

                ctx.send_viewport_cmd(egui::ViewportCommand::Title(self.window_title()));
            }
            Err(e) => {
                self.profile_message = Some(format!("could not switch to profile '{name}': {e}"));
            }
        }
    }

    /// Back to the working queue from a module's list.
    ///
    /// THE SCOPE AND THE ROWS MOVE TOGETHER. Setting one without the other
    /// leaves the module's rows on screen under the word "Triage" - the list
    /// is cached and only reloaded when the data version changes, which a
    /// scope change does not do. Three call sites needed this, two of them
    /// already remembered; the nav rail did not, and the QC harness caught it
    /// showing one applied job on a Triage screen that should have held six
    /// (2026-08-12).
    pub fn leave_module(&mut self) {
        if self.list_scope == ListScope::Triage {
            return;
        }
        self.list_scope = ListScope::Triage;
        // The open row belongs to the list being left, and its key may not be
        // in the one arriving.
        self.triage_expanded = None;
        self.refresh_triage();
    }

    /// Opens one module as its own list.
    ///
    /// Sets the scope and reloads rather than filtering what is already on
    /// screen: the module's WHERE clause is the definition of the list, and it
    /// is the same one that produced the number on the card.
    pub fn open_module(&mut self, module: crate::modules::Module) {
        self.list_scope = ListScope::Module(module);
        self.view = View::Triage;
        self.triage_selected = None;
        self.triage_expanded = None;
        self.refresh_triage();
    }

    /// Compare two rows on ONE key. The multi-key sort chains these.
    fn cmp_by(a: &TriageRow, b: &TriageRow, by: SortBy, desc: bool) -> std::cmp::Ordering {
        // A missing value always sorts LAST regardless of direction,
        // by construction of the comparator below. Sorting by salary and getting
        // a screen of blanks at the top is the reader being punished for the
        // employer not stating a figure.
        fn opt_num(v: Option<f64>, desc: bool) -> (u8, f64) {
            match v {
                Some(n) => (0, if desc { -n } else { n }),
                None => (1, 0.0),
            }
        }
        match by {
            SortBy::Score => opt_num(a.job.score, desc)
                .partial_cmp(&opt_num(b.job.score, desc))
                .unwrap_or(std::cmp::Ordering::Equal),
            SortBy::Salary => opt_num(a.job.salary_max.map(|v| v as f64), desc)
                .partial_cmp(&opt_num(b.job.salary_max.map(|v| v as f64), desc))
                .unwrap_or(std::cmp::Ordering::Equal),
            SortBy::Fit => opt_num(a.job.coverage_pct, desc)
                .partial_cmp(&opt_num(b.job.coverage_pct, desc))
                .unwrap_or(std::cmp::Ordering::Equal),
            // Newest first when descending, which is what a reader means
            // by "sort by posted". The stored value is an age in days, so
            // by construction the comparison has to be inverted to read
            // that way.
            SortBy::Posted => {
                let age = |r: &TriageRow| {
                    r.job
                        .posted_at
                        .as_deref()
                        .and_then(crate::fmt::days_since_posted)
                        .map(|d| d as f64)
                };
                opt_num(age(a), !desc)
                    .partial_cmp(&opt_num(age(b), !desc))
                    .unwrap_or(std::cmp::Ordering::Equal)
            }
            // Ascending means undecided first, which is the useful direction
            // and therefore the default. The rank comes from status::sort_rank
            // so by construction this cannot disagree with what the dropdown
            // and the queue filter treat as settled.
            SortBy::Status => {
                let rank = |r: &TriageRow| {
                    crate::status::sort_rank(r.status.as_deref().unwrap_or(""))
                };
                if desc { rank(b).cmp(&rank(a)) } else { rank(a).cmp(&rank(b)) }
            }
            SortBy::Company => {
                let key = |r: &TriageRow| {
                    r.company_name.clone().unwrap_or_default().to_lowercase()
                };
                if desc { key(b).cmp(&key(a)) } else { key(a).cmp(&key(b)) }
            }
            SortBy::Title => {
                let key = |r: &TriageRow| r.job.title.to_lowercase();
                if desc { key(b).cmp(&key(a)) } else { key(a).cmp(&key(b)) }
            }
        }
    }

    /// Sort by every key in turn, most significant first.
    ///
    /// STABLE UNDERNEATH. `sort_by` is a stable sort, so rows equal on every
    /// key keep the order the query returned - which is score order. A list
    /// that reshuffles equal rows between redraws reads as a glitch.
    fn sort_rows_multi(rows: &mut [TriageRow], keys: &[(SortBy, bool)]) {
        rows.sort_by(|a, b| {
            let mut ordering = std::cmp::Ordering::Equal;
            for (by, desc) in keys {
                ordering = Self::cmp_by(a, b, *by, *desc);
                if ordering != std::cmp::Ordering::Equal {
                    break;
                }
            }
            ordering
        });
    }

    /// Clicking the column already sorted flips direction; a different column
    /// starts descending, which is what a reader wants from every column here
    /// except the two alphabetical ones.
    pub fn sort_by_column(&mut self, column: SortBy) {
        // NO MODIFIER KEY. The usual idiom for a second sort key is
        // shift-click, which is invisible: nothing on a table says it is
        // there, so the feature exists only for people who already expect it.
        // Clicking promotes instead - the column you click becomes primary and
        // the previous one drops to second - so sorting by status and then by
        // posted date is two ordinary clicks in the order you say them.
        if self.triage_sort.first().map(|(c, _)| *c) == Some(column) {
            // Already primary: flip its direction rather than re-promoting it,
            // which would be a click that appears to do nothing.
            if let Some(first) = self.triage_sort.first_mut() {
                first.1 = !first.1;
            }
        } else {
            let previous = self
                .triage_sort
                .iter()
                .find(|(c, _)| *c == column)
                .map(|(_, desc)| *desc);
            // A column already in the sort keeps the direction it had. Losing
            // it on promotion would silently reverse an order the person set.
            let desc = previous.unwrap_or(!matches!(
                column,
                SortBy::Company | SortBy::Title | SortBy::Status
            ));
            self.triage_sort.retain(|(c, _)| *c != column);
            self.triage_sort.insert(0, (column, desc));
            self.triage_sort.truncate(MAX_SORT_KEYS);
        }
        self.refresh_triage();
    }

    /// Where `column` sits in the sort, if it is in it at all: 0 is primary.
    ///
    /// For the heading, which shows the position so a three-key sort is
    /// readable rather than three arrows with no order to them.
    pub fn sort_position(&self, column: SortBy) -> Option<(usize, bool)> {
        self.triage_sort
            .iter()
            .position(|(c, _)| *c == column)
            .map(|at| (at, self.triage_sort[at].1))
    }

    pub fn start_tutorial(&mut self) {
        self.tutorial_active = true;
        self.tutorial_step = 0;
    }

    /// Ends it AND remembers, by writing the flag to the profile's settings -
    /// so by construction it cannot reappear uninvited on a later launch.
    /// Skipping counts as seeing it: somebody who skipped does not want it
    /// again next time.
    pub fn end_tutorial(&mut self) {
        self.tutorial_active = false;
        self.settings.tutorial_seen = true;
        self.save_settings();
    }

    /// Records that stored data changed, so every cached view reloads when
    /// next drawn. Cheap by construction: one increment, no queries.
    ///
    /// Call this after ANY write. Missing it is the one way this design can
    /// still fail, which is why it lives in the small number of methods that
    /// actually mutate rather than being sprinkled through the views - see
    /// cache_invalidation_tests, which holds every cache against sync_caches.
    pub fn mark_data_changed(&mut self) {
        self.data_version = self.data_version.wrapping_add(1);
    }

    /// Reloads whichever caches are stale. Called once per frame from `update`.
    ///
    /// LAZY BY VIEW: only the caches the current screen reads are refreshed,
    /// so by construction a status change on Triage cannot rebuild the keyword
    /// corpus. The dashboard is the exception - its counts are the thing most
    /// often looked at right after a change, and building them is a handful of
    /// COUNT queries.
    fn sync_caches(&mut self) {
        let version = self.data_version;
        if self.dashboard_loaded_at != version {
            self.refresh_dashboard();
            self.dashboard_loaded_at = version;
        }
        match self.view {
            // ALL THREE VIEWS THAT RENDER THE JOBS LIST. Search was missing,
            // and the symptom was a status change that the app confirmed and
            // the row went on denying: the write landed, the list was never
            // re-read, and the screen kept describing the moment before it.
            View::Triage | View::AllJobs | View::Search => {
                if self.triage_loaded_at != version {
                    self.refresh_triage();
                    self.triage_loaded_at = version;
                }
            }
            View::Pipeline => {
                if self.pipeline_loaded_at != version {
                    self.refresh_pipeline();
                    self.pipeline_loaded_at = version;
                }
            }
            View::Keywords => {
                if self.keywords_loaded_at != version {
                    self.refresh_keywords();
                    self.keywords_loaded_at = version;
                }
            }
            View::Companies => {
                if self.companies_loaded_at != version {
                    self.refresh_companies();
                    self.companies_loaded_at = version;
                }
            }
            _ => {}
        }
        // Not per-view: Triage and Dashboard both show these, and they are
        // cheap to hold and expensive to ask for (see the field docs).
        if self.counts_loaded_at != version {
            self.refresh_counts();
            self.counts_loaded_at = version;
        }
    }

    /// The header counts, read once per data change instead of once per frame.
    pub fn refresh_counts(&mut self) {
        self.manual_links =
            crate::db::manual_link_state(&self.conn, crate::views::MANUAL_RECHECK_MIN_HOURS)
                .unwrap_or_default();
        self.retired_count = crate::db::retired_count(&self.conn).unwrap_or(0);
        self.duplicate_count = crate::db::duplicate_count(&self.conn).unwrap_or(0);
    }

    /// Hours since `path` was last written, read fresh.
    ///
    /// WHY NOT THE ENGINE'S age_hours. That comes from `collectors --json`,
    /// which by construction runs only when a profile opens and when
    /// config.json is reloaded. It was therefore a snapshot from launch: a
    /// file rewritten by a collector at lunchtime left the dashboard still
    /// reporting the morning's age, so nothing on the screen ever indicated
    /// the collection had completed.
    ///
    /// The engine still owns WHICH files exist and where; this owns only "has
    /// that path changed", which is one stat call and cannot drift from a
    /// config it never reads.
    ///
    /// None when the file is not there or its time cannot be read - which is
    /// not the same as new, and the caller says so.
    pub fn file_age_hours(&mut self, path: &str) -> Option<f64> {
        const RECHECK: std::time::Duration = std::time::Duration::from_secs(5);
        if let Some((at, age)) = self.file_ages.get(path) {
            if at.elapsed() < RECHECK {
                return *age;
            }
        }
        let age = std::fs::metadata(path)
            .and_then(|m| m.modified())
            .ok()
            .and_then(|t| t.elapsed().ok())
            .map(|d| d.as_secs_f64() / 3_600.0);
        self.file_ages
            .insert(path.to_string(), (std::time::Instant::now(), age));
        age
    }

    /// True when the per-source stamps were read before the collector listing
    /// arrived, so they answer for the wrong set and must be read again.
    ///
    /// Checked from the draw path, which is why it compares two small vectors
    /// rather than touching the database. The READ is what costs, and
    /// by construction it happens once - when the background listing lands.
    pub fn stamps_are_stale(&self) -> bool {
        self.handoffs.configured_ids() != self.collector_stamps_ids
    }

    /// Hours since the newest row from `source` arrived, from the stamps the
    /// dashboard already read - so by construction it costs no query on the
    /// draw path. None when that source has produced nothing, which is not
    /// the same as arriving long ago.
    pub fn rows_age_hours(&self, source: &str) -> Option<f64> {
        // THE ENGINE'S RECORD FIRST. It says when the file was taken in, which
        // is the question being asked. The row stamp below answers "when did a
        // row from them last arrive" and only coincides while every handoff
        // carries jobs - a closure-only file moves no row and read as never
        // taken (measured 2026-08-27: 0 jobs, 384 closures, all 333 matching
        // rows correctly closed, header still saying "not taken in yet").
        //
        // FALLING BACK RATHER THAN REQUIRING IT: a profile that last imported
        // before the engine started recording this has no such key, and the
        // old approximation is better than saying nothing at all.
        if let Some((_, Some(when))) = self.collector_taken.iter().find(|(n, _)| n == source) {
            if let Some(secs) = crate::date::seconds_since(when) {
                return Some(secs as f64 / 3_600.0);
            }
        }
        let (_, stamp) = self.collector_stamps.iter().find(|(n, _)| n == source)?;
        let secs = crate::date::seconds_since(stamp.as_deref()?)?;
        Some(secs as f64 / 3_600.0)
    }

    /// How long until the app next consults the schedule, if it knows.
    ///
    /// The engine answers this - it prints "[wake-in] N" and poll_process
    /// stores it - so this is the app's own next look, not a guess. None
    /// before the first answer of a session has arrived.
    pub fn until_next_look(&self) -> Option<std::time::Duration> {
        let wait = self.next_refresh_check?;
        Some(wait.saturating_sub(self.last_refresh_check.elapsed()))
    }

    /// Configured collectors that have not delivered today, each with how long
    /// it has been and whether that is overdue yet.
    ///
    /// ONE READER FOR BOTH SCREENS, so by construction they cannot disagree.
    /// The dashboard and the jobs list each carry the freshness line, and two
    /// screens deciding "has this collector delivered today" from two separate
    /// readings of the same stamps is how they come to differ in front of
    /// somebody.
    ///
    /// Empty when every configured collector has already delivered - and empty
    /// on a profile that has none, so nothing is drawn about collectors nobody
    /// set up.
    pub fn collectors_pending(&self) -> Vec<(String, String, bool)> {
        // NO SQL HERE. This is called from the draw path, once per frame per
        // screen that shows the line. The stamps were read when the data
        // changed; only the clock is consulted now.
        let now_local = crate::date::seconds_into_local_day(self.local_offset);
        self.collector_stamps
            .iter()
            .cloned()
            .filter_map(|(name, seen)| {
                match crate::fmt::collected_state(seen.as_deref(), now_local) {
                    crate::fmt::Collected::Today => None,
                    crate::fmt::Collected::NotYet => {
                        Some((name, crate::fmt::source_age(seen.as_deref()), false))
                    }
                    crate::fmt::Collected::Late => {
                        Some((name, crate::fmt::source_age(seen.as_deref()), true))
                    }
                }
            })
            .collect()
    }

    pub fn refresh_dashboard(&mut self) {
        let external = self.handoffs.configured_ids();
        // Read here, with the rest of the dashboard's data, so
        // by construction the draw path never touches the database for it.
        self.local_offset = crate::db::local_offset_secs(&self.conn);
        // The clock every stamp is WRITTEN against, kept with the one the
        // screens read. Both move at a daylight-saving change and they must
        // not move separately.
        crate::date::set_local_offset(self.local_offset);
        self.collector_stamps =
            crate::db::source_last_seen(&self.conn, &external).unwrap_or_default();
        self.collector_taken =
            crate::db::collector_taken_in(&self.conn, &external).unwrap_or_default();
        self.collector_stamps_ids = external.clone();
        match crate::dashboard::load(&self.conn, &external) {
            Ok(stats) => self.dashboard_stats = Some(stats),
            Err(e) => {
                self.dashboard_stats = None;
                self.triage_message = Some(format!("could not build the dashboard: {e}"));
            }
        }
    }

    /// Run what is in the box, and land on the results.
    ///
    /// Clearing the box does NOT bounce the person somewhere else -
    /// by construction, since the empty case runs the search rather than changing
    /// view. Emptying a field is how you edit it, and a view that jumps away
    /// mid-edit is one that cannot be corrected.
    pub fn run_search(&mut self) {
        self.search_terms = db::parse_search_terms(&self.search_query);
        self.view = View::Search;
        self.list_scope = ListScope::Search;
        self.refresh_triage();
        // Recorded here, while the loaded list IS the search results. After
        // this the person may go anywhere, and the caption must keep saying
        // what the search found rather than what they are now looking at.
        self.search_found = self.triage_rows.len();
    }

    /// Make sure the opened row has its description, fetching it once.
    ///
    /// Called from the draw path, so it must do nothing on the frames where
    /// there is nothing to do - by construction almost all of them, since a
    /// description is fetched once per opened row. The guard is the point:
    /// without it this is a query per frame, which is the shape of problem it
    /// was written to remove.
    pub fn ensure_description(&mut self, key: &str) {
        let needs = self
            .triage_rows
            .iter()
            .any(|r| r.job.key == key && r.job.description.is_none());
        if !needs {
            return;
        }
        match db::description_for(&self.conn, key) {
            Ok(text) => {
                if let Some(row) = self.triage_rows.iter_mut().find(|r| r.job.key == key) {
                    // Store Some("") rather than None when a posting
                    // genuinely has no description: by construction None is
                    // the "not fetched yet" state, so leaving it there would
                    // re-query an empty posting on every frame for ever.
                    row.job.description = Some(text.unwrap_or_default());
                }
            }
            Err(e) => {
                self.triage_message = Some(format!("could not read the description: {e}"));
            }
        }
    }

    pub fn refresh_triage(&mut self) {
        // (The most recent collect date was read here to feed apply_filter's
        // "new since the last run" predicate. That lives in SQL now, in
        // Module::NewSinceLastRun, which by construction answers it against
        // the whole table rather than against the rows this list happened to
        // have loaded.)
        match db::list_jobs_for(
            &self.conn,
            self.list_scope,
            self.triage_show_all,
            &self.search_terms,
        ) {
            Ok(rows) => {
                // Fold one-requisition-per-city down to a single row unless
                // the person asked to see every location. Done here rather
                // than in SQL because the query has already ordered by score,
                // so by construction the survivor is the best-scoring posting
                // of the set.
                let mut rows = if self.triage_every_location {
                    rows
                } else {
                    // Collapse BEFORE sorting. The collapse keeps the first
                    // row of each group and the query orders by score, so
                    // by construction the survivor is the best-scoring posting of
                    // the set however the reader then chooses to sort.
                    db::collapse_locations(rows)
                };
                Self::sort_rows_multi(&mut rows, &self.triage_sort);
                self.triage_rows = rows;
                if let Some(sel) = &self.triage_selected {
                    if !self.triage_rows.iter().any(|r| &r.job.key == sel) {
                        self.triage_selected = self.triage_rows.first().map(|r| r.job.key.clone());
                    }
                } else {
                    self.triage_selected = self.triage_rows.first().map(|r| r.job.key.clone());
                }
            }
            Err(e) => {
                self.triage_message = Some(format!("could not load jobs: {e}"));
            }
        }
    }

    pub fn refresh_pipeline(&mut self) {
        if let Ok(log) = db::list_status_log(&self.conn) {
            self.pipeline_log = log;
        }
        if let Ok(notes) = db::list_notes(&self.conn) {
            self.pipeline_notes = notes;
        }
        if let Ok(current) = db::current_statuses(&self.conn) {
            self.pipeline_current = current;
        }
        if let Ok(info) = db::all_job_info(&self.conn) {
            self.pipeline_job_info = info;
        }
    }

    /// Recomputes the Keywords view's demand report from the current
    /// corpus (qualified jobs, or every job when keywords_show_all),
    /// config.skills, and the configured resume - see
    /// views::keywords::compute_report for the matcher itself.
    pub fn refresh_keywords(&mut self) {
        match db::job_descriptions(&self.conn, !self.keywords_show_all) {
            Ok(corpus) => {
                // THE RESUME THE APP HOLDS, not config.resume_path - which
                // is the engine's LAST fallback and, observed, is never
                // written by attaching. Reading it alone meant the ordinary
                // setup path (attach on the Resumes tab, exactly as the
                // walkthrough says) left this screen with no resume text at
                // all: every tracked skill reported as demanded-and-not-
                // evidenced, and COVERED was always empty.
                let (resume_text, gap) = views::keywords::load_resume_text(
                    crate::resumes::active_path(&self.active_home, &self.config)
                        .as_deref()
                        .and_then(std::path::Path::to_str),
                );
                self.keywords_resume_gap = gap;
                self.keywords_corpus_size = corpus.len();
                self.keywords_report =
                    views::keywords::compute_report(&corpus, &self.config.skills, &resume_text);
                self.keywords_message = None;
            }
            Err(e) => {
                self.keywords_message = Some(format!("could not load job descriptions: {e}"));
            }
        }
    }

    /// Load the attachments for the job whose row is open, if they are not
    /// already loaded. Called every frame; costs one query per opened row.
    pub fn sync_attachments(&mut self) {
        let open = self.triage_expanded.clone();
        if self.attachments_for == open {
            return;
        }
        // A different job's files are on screen, so by construction nothing
        // belonging to the last one may survive the switch.
        self.attachment_message = None;
        self.attachments = match &open {
            Some(key) => crate::attachments::list_for(&self.conn, key).unwrap_or_default(),
            None => Vec::new(),
        };
        self.attachments_for = open;
    }

    fn reload_attachments(&mut self) {
        self.attachments_for = None;
        self.sync_attachments();
    }

    /// Ask for a file and keep a copy of it beside this job.
    ///
    /// COPIED, NOT MOVED, and the person's own file stays where they left it.
    /// The class defaults to theirs: only employer-written material is held
    /// back from an assistant.
    pub fn attach_file(&mut self, key: &str) {
        let Some(picked) = rfd::FileDialog::new().pick_file() else {
            return;
        };
        let home = self.active_home.clone();
        match crate::attachments::add_file(
            &self.conn,
            &home,
            key,
            &picked,
            crate::attachments::MINE,
        ) {
            Ok(row) => {
                self.attachment_message = Some(format!("attached {}", row.display_name));
                self.reload_attachments();
            }
            Err(e) => self.attachment_message = Some(e),
        }
    }

    /// Save whatever image is on the clipboard as a .png beside this job.
    ///
    /// The confirmation screen is the single most-lost artefact of an
    /// application, and it only ever exists as a screenshot.
    pub fn paste_screenshot(&mut self, key: &str) {
        let image = match arboard::Clipboard::new().and_then(|mut c| c.get_image()) {
            Ok(image) => image,
            Err(e) => {
                self.attachment_message = Some(format!(
                    "nothing to paste - the clipboard has no image in it ({e})"
                ));
                return;
            }
        };
        let Some(buffer) = image::RgbaImage::from_raw(
            image.width as u32,
            image.height as u32,
            image.bytes.into_owned(),
        ) else {
            self.attachment_message =
                Some("the clipboard image could not be read".to_string());
            return;
        };
        let mut png: Vec<u8> = Vec::new();
        if let Err(e) = image::DynamicImage::ImageRgba8(buffer)
            .write_to(&mut std::io::Cursor::new(&mut png), image::ImageFormat::Png)
        {
            self.attachment_message = Some(format!("could not encode it as a png: {e}"));
            return;
        }
        let home = self.active_home.clone();
        let name = format!("screenshot-{}.png", date::now_iso().replace(':', "-"));
        match crate::attachments::add_bytes(
            &self.conn,
            &home,
            key,
            &name,
            &png,
            crate::attachments::MINE,
        ) {
            Ok(row) => {
                self.attachment_message = Some(format!("pasted {}", row.display_name));
                self.reload_attachments();
            }
            Err(e) => self.attachment_message = Some(e),
        }
    }

    pub fn add_link_attachment(&mut self, key: &str) {
        let url = self.link_url.trim().to_string();
        if url.is_empty() {
            return;
        }
        let label = self.link_label.trim().to_string();
        match crate::attachments::add_link(
            &self.conn,
            key,
            &url,
            &label,
            crate::attachments::MINE,
        ) {
            Ok(row) => {
                self.attachment_message = Some(format!("added {}", row.display_name));
                self.link_url.clear();
                self.link_label.clear();
                self.link_prompt_open = false;
                self.reload_attachments();
            }
            Err(e) => self.attachment_message = Some(e),
        }
    }

    /// Save a copy of an attachment wherever the person wants it.
    ///
    /// WHERE IT OPENS IS REMEMBERED PER PROFILE (decided 2026-08-13), so a
    /// profile that does not use the Downloads folder can keep its files
    /// wherever it does keep them. The setting lives in this profile's own
    /// desktop_settings.json, so two people sharing a machine - or one person
    /// running a second search - keep their attachments apart without either
    /// having to re-navigate the dialog every time.
    ///
    /// REMEMBERED FROM WHAT THEY DID, not from a preferences screen they have
    /// to find first: saving somewhere is the act of choosing it, and the
    /// Downloads folder remains the answer until they choose otherwise.
    pub fn download_attachment(&mut self, id: i64) {
        let Some(row) = self.attachments.iter().find(|a| a.id == id).cloned() else {
            return;
        };
        let Some(source) = row.path(&self.active_home) else {
            return;
        };
        let mut dialog = rfd::FileDialog::new().set_file_name(&row.display_name);
        if let Some(start) = self.download_start_dir() {
            dialog = dialog.set_directory(start);
        }
        let Some(target) = dialog.save_file() else {
            return;
        };
        self.attachment_message = match fs::copy(&source, &target) {
            Ok(_) => {
                if let Some(folder) = target.parent() {
                    self.remember_download_dir(folder);
                }
                Some(format!("saved to {}", target.display()))
            }
            Err(e) => Some(format!("could not save it: {e}")),
        };
    }

    /// Where the save dialog opens: this profile's remembered folder, else
    /// Downloads, else wherever the dialog would have gone on its own.
    ///
    /// A REMEMBERED FOLDER THAT NO LONGER EXISTS IS NOT USED - checked with
    /// is_dir before it is offered, so by construction a stale path falls
    /// through to the next choice. People move drives and delete folders, and
    /// falling back beats a dialog opening somewhere arbitrary.
    pub fn download_start_dir(&self) -> Option<PathBuf> {
        let remembered = self
            .settings
            .download_dir
            .as_deref()
            .map(PathBuf::from)
            .filter(|p| p.is_dir());
        remembered.or_else(downloads_dir)
    }

    fn remember_download_dir(&mut self, folder: &Path) {
        let folder = folder.to_string_lossy().to_string();
        if self.settings.download_dir.as_deref() == Some(folder.as_str()) {
            return;
        }
        self.settings.download_dir = Some(folder);
        self.save_settings();
    }

    pub fn set_attachment_trust(&mut self, id: i64, trust: &str) {
        let home = self.active_home.clone();
        match crate::attachments::set_trust(&self.conn, &home, id, trust) {
            Ok(()) => self.reload_attachments(),
            Err(e) => self.attachment_message = Some(e),
        }
    }

    pub fn remove_attachment(&mut self, id: i64) {
        let home = self.active_home.clone();
        match crate::attachments::remove(&self.conn, &home, id) {
            Ok(()) => self.reload_attachments(),
            Err(e) => self.attachment_message = Some(e),
        }
    }

    /// Open one posting wherever it is, switching scope if it takes that.
    ///
    /// THE EARLIER ROUND IS USUALLY DELISTED, and Triage hides those by
    /// design. Selecting the key in place would then change nothing on screen
    /// (by construction, since the row is not in that list), which reads as a
    /// dead button rather than as "that row is not here". So this moves to All
    /// jobs, the one scope that holds everything, and says so plainly if the
    /// row is not even there.
    pub fn open_job_anywhere(&mut self, key: &str) {
        self.list_scope = ListScope::All;
        self.refresh_triage();
        if self.triage_rows.iter().any(|r| r.job.key == key) {
            self.triage_selected = Some(key.to_string());
            self.triage_expanded = Some(key.to_string());
            // ARRIVING AT A LIST IS NOT THE SAME AS SEEING THE ROW. This lands
            // on All jobs, which by construction can hold thousands of rows,
            // so without the scroll the opened posting is somewhere below the
            // fold and the click reads as having only changed screens.
            self.scroll_to_selected = true;
        } else {
            self.triage_message = Some(format!(
                "that earlier advertisement ({key}) is no longer on the board"
            ));
        }
    }

    pub fn refresh_companies(&mut self) {
        if let Ok(companies) = db::list_companies(&self.conn) {
            self.companies = companies;
        }
    }

    /// Ask the engine which collectors this profile has, in the background.
    ///
    /// Shares engine_invocation with start_process, so --home is settled by
    /// the same code for both - by construction this call site cannot forget
    /// it, because it never writes it.
    pub fn refresh_handoffs(&mut self) {
        let (program, args) = engine_invocation(
            &self.engine_mode,
            &self.settings.python_invocation,
            &self.active_home,
            vec!["collectors".to_string(), "--json".to_string()],
        );
        self.handoffs = crate::collectors::Collectors::load(program, args);
    }

    pub fn selected_index(&self) -> Option<usize> {
        let sel = self.triage_selected.as_ref()?;
        self.triage_rows.iter().position(|r| &r.job.key == sel)
    }

    /// Move the highlight by `delta` rows and BRING IT WITH YOU.
    ///
    /// THE SCROLL IS THE HALF THAT WAS MISSING. This set the selection and
    /// nothing else, so on a list longer than the window the band walked off
    /// the bottom edge and kept going: the keys still worked, the highlighted
    /// row was simply somewhere the person could not see, and every status
    /// keystroke after that landed on a row they were not looking at. Reported
    /// 2026-09-04 on the installed build.
    ///
    /// The flag is the same one the Pipeline uses to open a job in the list,
    /// and the table clears it after one frame - so by construction this
    /// scrolls once rather than pinning the view against the scrollbar.
    pub fn move_selection(&mut self, delta: i32) {
        if self.triage_rows.is_empty() {
            return;
        }
        let current = self.selected_index().unwrap_or(0) as i32;
        let next = (current + delta).clamp(0, self.triage_rows.len() as i32 - 1);
        self.triage_selected = Some(self.triage_rows[next as usize].job.key.clone());
        self.scroll_to_selected = true;
    }

    pub fn set_status_for_selected(&mut self, status: &str) {
        let Some(key) = self.triage_selected.clone() else {
            return;
        };
        self.set_status_for(&key, status);
    }

    /// Opens the note prompt for one named row. The row dropdown passes the
    /// key explicitly rather than reading the selection, so by construction
    /// changing a status cannot depend on which row happens to be
    /// highlighted.
    pub fn set_status_for(&mut self, key: &str, status: &str) {
        let subject = self
            .triage_rows
            .iter()
            .find(|r| r.job.key == key)
            .map(|r| crate::fmt::truncate(&r.job.title, 60))
            .unwrap_or_else(|| key.to_string());
        self.begin_status_change(vec![key.to_string()], status, subject);
    }

    /// Puts a status change in front of the person before it is written.
    ///
    /// CLEARING SKIPS THE PROMPT. "Back to not set" is an undo, usually of a
    /// mis-click, and asking somebody to write a note about a keystroke they
    /// did not mean to press is friction with nothing on the other end of it.
    pub fn begin_status_change(&mut self, keys: Vec<String>, status: &str, subject: String) {
        if keys.is_empty() {
            return;
        }
        if status.is_empty() {
            for key in &keys {
                self.clear_status_for(key);
            }
            self.selected_keys.clear();
            return;
        }
        // QUIET STATUSES SKIP THE QUESTION, NOT THE RECORD. The status, its
        // timestamp and its entry in the append-only log are written exactly as
        // before; the person is simply not stopped to ask about it. Same shape
        // as clearing a status, which has always skipped the prompt.
        //
        // AN OFFER IS NEVER QUIET, whatever the setting says. That prompt is
        // where pay and the offer date are captured and by construction they
        // exist nowhere else - no other screen writes them - so skipping it
        // would drop the only figures the app keeps.
        if status != "offer" && !self.settings.asks_for_note(status) {
            self.pending_status = Some(PendingStatus {
                keys,
                status: status.to_string(),
                subject,
                note: String::new(),
                pay: String::new(),
                offer_date: String::new(),
                // Committed without ever being drawn - there is no cursor to
                // place, and nowhere to place it.
                cursor_placed: true,
            });
            self.commit_status_change();
            return;
        }
        self.pending_status = Some(PendingStatus {
            keys,
            status: status.to_string(),
            subject,
            note: String::new(),
            pay: String::new(),
            offer_date: String::new(),
            cursor_placed: false,
        });
    }

    /// Re-apply status changes that a locked database refused earlier.
    ///
    /// Called when an engine run finishes, which is when the write lock is
    /// free. Anything that fails a second time is reported rather than queued
    /// again - a queue that retries for ever would hide a real fault.
    fn apply_deferred_status(&mut self) {
        if self.deferred_status.is_empty() {
            return;
        }
        let waiting = std::mem::take(&mut self.deferred_status);
        let count = waiting.len();
        for pending in waiting {
            self.pending_status = Some(pending);
            self.commit_status_change();
        }
        // Only overwrite the message if commit_status_change did not already
        // say something more specific about a failure.
        if self.deferred_status.is_empty() {
            self.triage_message =
                Some(format!("{count} change(s) you made during the collection are in."));
        }
    }

    /// Writes the pending change, with whatever was typed about it.
    pub fn commit_status_change(&mut self) {
        let Some(pending) = self.pending_status.take() else {
            return;
        };
        let note = pending.note.trim();
        // NULL, not "". A status change with nothing written about it must
        // log no note at all: by construction the pipeline treats the empty
        // string as "a note exists", and the dashboard would count it.
        let note_opt = (!note.is_empty()).then_some(note);
        // Offer terms belong to an Offer - by construction, since
        // status::has_offer_fields is what admits them. Typed into the boxes
        // and then switched to another status, they are dropped rather than
        // stored against a transition that cannot mean them.
        let terms = if crate::status::has_offer_fields(&pending.status) {
            db::OfferTerms::from_inputs(&pending.pay, &pending.offer_date)
        } else {
            db::OfferTerms::default()
        };

        let count = pending.keys.len();
        let mut failed: Option<String> = None;
        for key in &pending.keys {
            if let Err(e) =
                db::set_status_with(&self.conn, key, &pending.status, note_opt, &terms)
            {
                failed = Some(e.to_string());
                break;
            }
            self.snapshot_posting_if_applied(key, &pending.status);
        }
        let label = crate::status::label(&pending.status);
        // LOCKED IS NOT LOST. A collect's closing passes hold the write lock
        // in one transaction, so this is a wait rather than a refusal - and
        // the person's typing is the last thing that should be discarded over
        // it. Kept, and by construction re-applied when the run finishes:
        // apply_deferred_status runs from the completion handler.
        if failed.as_deref().is_some_and(|e| e.contains("database is locked")) {
            self.deferred_status.push(pending);
            self.triage_message = Some(format!(
                "Collecting right now - {label} will be applied when it finishes."
            ));
            return;
        }
        self.triage_message = Some(match failed {
            Some(e) => format!("could not update status: {e}"),
            None if count == 1 => format!("{}: marked {label}", pending.subject),
            None => format!("marked {count} jobs {label}"),
        });
        self.selected_keys.clear();
        // Every view, not the two somebody remembered. The dashboard was
        // missing from this list once and its counts went stale on every status
        // change until the person pressed Refresh.
        self.mark_data_changed();
        // The readable copy is refreshed on the action that CREATES the
        // history worth keeping, so by construction the backup is never older
        // than the last thing the person did.
        self.write_backup_copy();
    }

    /// Keep the posting's own words the moment somebody applies to it.
    ///
    /// THE DESCRIPTION IS THE THING THAT VANISHES. 1,639 of the 9,161 postings
    /// on a real board are already delisted, and a delisted posting is
    /// not recoverable from the web - which is exactly when an interviewer
    /// asks what the ad said. Employers also edit live postings, so this
    /// captures the wording AS IT WAS on the day rather than as it reads
    /// later.
    ///
    /// COSTS NOTHING NEW: the bytes are already in jobs.description, this
    /// writes a copy of a column we hold. Classed as POSTING material, because
    /// that is what it is - scraped text from the employer's side, which the
    /// standing rule keeps away from any assistant.
    ///
    /// ONCE PER JOB, by construction: the write is skipped when a snapshot
    /// for that key already exists. Re-applying months later neither
    /// overwrites the first nor adds a second - the point is the wording that
    /// was actually applied to.
    fn snapshot_posting_if_applied(&mut self, key: &str, status: &str) {
        if status != crate::status::APPLIED {
            return;
        }
        let description: Option<String> = self
            .conn
            .query_row(
                "SELECT description FROM jobs WHERE key = ?1",
                rusqlite::params![key],
                |r| r.get(0),
            )
            .ok()
            .flatten();
        let Some(text) = description.filter(|d| !d.trim().is_empty()) else {
            return;
        };
        let already = crate::attachments::list_for(&self.conn, key)
            .unwrap_or_default()
            .iter()
            .any(|a| a.display_name == SNAPSHOT_NAME);
        if already {
            return;
        }
        let home = self.active_home.clone();
        if let Err(e) = crate::attachments::add_bytes(
            &self.conn,
            &home,
            key,
            SNAPSHOT_NAME,
            text.as_bytes(),
            crate::attachments::POSTING,
        ) {
            // Not fatal and not silent. The status change itself already
            // succeeded by construction - it is written before this runs - so
            // a person who never sees this message still has their status.
            self.attachment_message = Some(format!("could not keep a copy of the posting: {e}"));
        }
    }

    /// Drops the pending change without writing it. The status does not move.
    pub fn cancel_status_change(&mut self) {
        self.pending_status = None;
    }

    /// Puts a row back to "not set". Picking that from the dropdown is an
    /// undo, so it removes the status row rather than storing an empty one:
    /// by construction a present-but-blank status still counts as a decision
    /// in every query that filters on having one.
    pub fn clear_status_for(&mut self, key: &str) {
        match db::clear_status(&self.conn, key) {
            Ok(()) => {
                self.triage_message = Some(format!("{key}: status cleared"));
                self.mark_data_changed();
            }
            Err(e) => {
                self.triage_message = Some(format!("could not clear status: {e}"));
            }
        }
    }

    /// Records a note that is not about a status change.
    ///
    /// APPENDS, and needs no status to exist first. This used to re-write the
    /// job's current status in order to carry the note, which was wrong twice
    /// over - observed: a job with no status could not be annotated at all
    /// (job_status.status is NOT NULL by construction, so there was nothing to
    /// write), and every note on a job that DID have one appended a history
    /// row saying the status had changed to the value it already held. Three
    /// notes about one application produced a timeline claiming it had been
    /// marked Applied four times.
    pub fn submit_note_for_selected(&mut self) {
        let Some(key) = self.triage_selected.clone() else {
            return;
        };
        let note = self.triage_note_buffer.trim().to_string();
        if !note.is_empty() {
            match db::add_note(&self.conn, &key, &note) {
                Ok(()) => {
                    self.triage_message = Some(format!("{key}: note added"));
                    self.mark_data_changed();
                    self.write_backup_copy();
                }
                Err(e) => {
                    self.triage_message = Some(format!("could not save note: {e}"));
                }
            }
        }
        self.triage_note_open = false;
        self.triage_note_buffer.clear();
    }

    pub fn reload_config(&mut self) {
        let (cfg, err) = config::load(&self.config_path);
        self.config = cfg;
        self.config_error = err;
        self.config_draft = ConfigDraft::from_config(&self.config);
        self.config_status = Some("reloaded from config.json".to_string());
        self.refresh_keywords();
        // config.json is where collectors are declared by construction, so a
        // reload of it is exactly when the menu's list goes out of date.
        self.refresh_handoffs();
    }

    pub fn save_config(&mut self) {
        match self.config_draft.to_config() {
            Ok(cfg) => {
                // Compare BEFORE storing. By construction only the search
                // block changes which jobs match, so a changed timeout or a
                // corrected API key saves quietly rather than offering a
                // re-run that would return the same rows.
                let search_changed = cfg.search != self.config.search
                    || cfg.sources != self.config.sources;
                match config::save(&self.config_path, &cfg) {
                Ok(()) => {
                    self.config = cfg;
                    self.config_status = Some("saved".to_string());
                    self.config_error = None;
                    self.refresh_keywords();
                    self.offer_run_after_save = search_changed;
                }
                Err(e) => {
                    self.config_status = Some(format!("save failed: {e}"));
                }
                }
            }
            Err(errors) => {
                self.config_status = Some(format!("fix before saving: {}", errors.join("; ")));
            }
        }
    }

    /// Write the pipeline out where the person asked, and say where it went.
    ///
    /// Runs the ENGINE rather than reimplementing the writer here. The export
    /// is a data guarantee, and two implementations of a guarantee is one that
    /// can disagree with itself - the CLI's version is the tested one.
    /// Write the criteria out where a person will find them again.
    ///
    /// DOCUMENTS, NOT THE WORKING DIRECTORY. A file written beside the
    /// executable is one somebody has to be told where to look for, and the
    /// point of this feature is that they do not have to be told anything.
    pub fn export_criteria(&mut self) {
        let Some(target) = rfd::FileDialog::new()
            .set_title("Save your criteria")
            .set_file_name("unlatched-criteria.json")
            .set_directory(paths::documents_dir().unwrap_or_else(|| self.active_home.clone()))
            .save_file()
        else {
            return;
        };
        let args = vec![
            "criteria".to_string(),
            "--export".to_string(),
            target.to_string_lossy().into_owned(),
            "--json".to_string(),
        ];
        let (program, args) = engine_invocation(
            &self.engine_mode,
            &self.settings.python_invocation,
            &self.active_home,
            args,
        );
        self.criteria_message = match crate::criteria::run(&program, &args) {
            Ok(_) => Some(format!("saved to {}", target.display())),
            Err(e) => Some(format!("could not save: {e}")),
        };
    }

    /// Pick a criteria file and work out what taking it in would do.
    pub fn choose_criteria_file(&mut self) {
        let Some(path) = rfd::FileDialog::new()
            .set_title("Open a criteria file")
            .add_filter("Criteria", &["json"])
            .set_directory(paths::documents_dir().unwrap_or_else(|| self.active_home.clone()))
            .pick_file()
        else {
            return;
        };
        self.begin_criteria_import(path);
    }

    /// Everything after the file picker.
    ///
    /// SPLIT OFF FROM IT so this half can be tested and driven: rfd opens a
    /// native dialog, which no automated run can answer, and that would have
    /// made the whole import path untestable from the outside.
    pub fn begin_criteria_import(&mut self, path: std::path::PathBuf) {
        self.criteria_message = None;
        // Replace, verified as the engine's own default for `apply` and for
        // the --mode argument, so the dialog opens on the mode a person gets
        // from the command line too.
        let mode = "replace".to_string();
        let report = self.preview_criteria(&path, &mode);
        self.criteria_import = Some(CriteriaImport { path, mode, report });
    }

    /// Ask again in the other mode, keeping the same file.
    pub fn set_criteria_mode(&mut self, mode: &str) {
        let Some(pending) = self.criteria_import.take() else {
            return;
        };
        let report = self.preview_criteria(&pending.path, mode);
        self.criteria_import = Some(CriteriaImport {
            path: pending.path,
            mode: mode.to_string(),
            report,
        });
    }

    fn preview_criteria(
        &self,
        path: &std::path::Path,
        mode: &str,
    ) -> Result<crate::criteria::Report, String> {
        let (program, args) = engine_invocation(
            &self.engine_mode,
            &self.settings.python_invocation,
            &self.active_home,
            criteria_args(path, mode, true),
        );
        crate::criteria::run(&program, &args)
    }

    /// Take in the file the dialog is showing, in the mode it is showing.
    ///
    /// RUNS THE IMPORT AGAIN RATHER THAN APPLYING THE PREVIEW. The preview is
    /// a description, and applying a description would mean a second
    /// implementation of the merge on this side - which could then disagree
    /// with the one the person was shown.
    pub fn apply_criteria_import(&mut self) {
        let Some(pending) = self.criteria_import.take() else {
            return;
        };
        let (program, args) = engine_invocation(
            &self.engine_mode,
            &self.settings.python_invocation,
            &self.active_home,
            criteria_args(&pending.path, &pending.mode, false),
        );
        self.criteria_message = match crate::criteria::run(&program, &args) {
            Ok(report) if report.is_empty() => {
                Some("nothing to change - the criteria already match".to_string())
            }
            Ok(report) => {
                // Reloaded, or the Config screen would keep showing the search
                // that was there before the file arrived.
                self.reload_config();
                Some(format!("updated {}", report.changed.join(", ")))
            }
            Err(e) => Some(format!("could not import: {e}")),
        };
    }

    pub fn export_pipeline(&mut self) {
        let target = paths::downloads_dir()
            .map(|dir| paths::non_clobbering_path(&dir, "unlatched-pipeline.csv"))
            .unwrap_or_else(|| self.active_home.join(BACKUP_CSV_NAME));
        self.export_message = match self.run_export(&target) {
            Ok(()) => Some(format!("saved to {}", target.display())),
            // Named, not swallowed - by construction the message carries the
            // error's own text. Somebody exporting is usually doing it because
            // they are about to need the file.
            Err(e) => Some(format!("could not export: {e}")),
        };
    }

    /// The copy kept beside the database, refreshed whenever a status changes.
    ///
    /// Automatic rather than a reminder: a nudge depends on the person acting,
    /// and that is the assumption that already failed once. Failure here is
    /// deliberately SILENT - this is a safety net running behind an action the
    /// person took for another reason, and an error toast about a backup they
    /// did not ask for would train them to dismiss messages from this app.
    fn write_backup_copy(&mut self) {
        // Measured on a real profile: 7,189 jobs is a 1.63 MB file. Triage is
        // driven by single keypresses, so a person clearing a morning's list
        // can set a dozen statuses in a few seconds - rewriting that file on
        // each one would spawn a dozen processes and be felt as lag.
        //
        // A minute of exposure is the right trade: the DATABASE is the record
        // and is written immediately, and this copy exists for the case where
        // the database is gone entirely, not for the last sixty seconds of it.
        const MIN_GAP: std::time::Duration = std::time::Duration::from_secs(60);
        if self.last_backup.is_some_and(|at| at.elapsed() < MIN_GAP) {
            return;
        }
        self.last_backup = Some(std::time::Instant::now());
        let target = self.active_home.join(BACKUP_CSV_NAME);
        let _ = self.run_export(&target);
    }

    fn run_export(&mut self, target: &Path) -> Result<(), String> {
        // Shares engine_invocation with start_process, so --home is settled
        // by the same code and the same tests - by construction this call site
        // cannot forget it, because it never writes it.
        let (program, args) = engine_invocation(
            &self.engine_mode,
            &self.settings.python_invocation,
            &self.active_home,
            vec![
                "export".to_string(),
                "--to".to_string(),
                target.to_string_lossy().into_owned(),
            ],
        );

        let mut cmd = std::process::Command::new(&program);
        cmd.args(&args);
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            // Same reason as every other spawn here: a console flashing over
            // whatever the person is doing.
            cmd.creation_flags(0x0800_0000);
        }
        let output = cmd.output().map_err(|e| e.to_string())?;
        if output.status.success() {
            Ok(())
        } else {
            Err(String::from_utf8_lossy(&output.stderr).trim().to_string())
        }
    }

    /// Apply one status to every ticked row.
    ///
    /// Through the same `set_status_for` each row's dropdown uses, one row at
    /// a time, so by construction every one gets its own append-only log
    /// entry. A single bulk UPDATE would be faster and would leave the history
    /// saying nothing happened - and that history is what the Applied column,
    /// the funnel and the response rate are all read from.
    pub fn set_status_for_selection(&mut self, status: &str) {
        // SORTED, so the same nine rows produce the same order every time.
        // By construction a HashSet has no order of its own, so the prompt's
        // heading and the log's timestamps would otherwise shuffle between
        // runs.
        let mut keys: Vec<String> = self.selected_keys.iter().cloned().collect();
        keys.sort();
        let count = keys.len();
        let subject = format!("{count} job{}", if count == 1 { "" } else { "s" });
        self.begin_status_change(keys, status, subject);
    }

    /// Open the retire confirmation for the current selection.
    ///
    /// Measures the applied count HERE, once, so the dialog can say what it
    /// is about to cost instead of asking "are you sure" about a number. Held
    /// rather than recomputed while drawing, so by construction the figure the
    /// person reads is the one the action was sized against.
    pub fn ask_to_retire_selection(&mut self) {
        let keys: Vec<String> = self.selected_keys.iter().cloned().collect();
        if keys.is_empty() {
            return;
        }
        let applied = db::applied_among(&self.conn, &keys).unwrap_or(0);
        self.confirm_retire = Some(RetireConfirm { keys, applied });
    }

    pub fn retire_confirmed(&mut self) {
        let Some(pending) = self.confirm_retire.take() else {
            return;
        };
        match db::retire(&self.conn, &pending.keys) {
            Ok(n) => {
                self.selected_keys.clear();
                self.triage_message = Some(format!(
                    "removed {n} job(s). All jobs -> Removed puts them back."
                ));
            }
            Err(e) => self.triage_message = Some(format!("could not remove: {e}")),
        }
        self.mark_data_changed();
        // A removal changes the record as much as a status does, and
        // by construction the export carries removed_on - so a thrown-away row
        // is still recoverable from the copy.
        self.write_backup_copy();
    }

    /// Undo a grouping for the selected rows.
    ///
    /// The counterpart to Put back, and it exists for the same reason: a
    /// judgement the person cannot reverse is one they have to trust blindly.
    /// Grouping hides rather than deletes - by construction, since it sets
    /// duplicate_of rather than removing the row - so this can put it back.
    pub fn ungroup_selection(&mut self) {
        let keys: Vec<String> = self.selected_keys.iter().cloned().collect();
        match db::ungroup(&self.conn, &keys) {
            Ok(n) => {
                self.selected_keys.clear();
                self.triage_message = Some(format!("ungrouped {n} job(s)"));
            }
            Err(e) => self.triage_message = Some(format!("could not ungroup: {e}")),
        }
        self.mark_data_changed();
    }

    pub fn restore_selection(&mut self) {
        let keys: Vec<String> = self.selected_keys.iter().cloned().collect();
        match db::restore(&self.conn, &keys) {
            Ok(n) => {
                self.selected_keys.clear();
                self.triage_message = Some(format!("put {n} job(s) back"));
            }
            Err(e) => self.triage_message = Some(format!("could not restore: {e}")),
        }
        self.mark_data_changed();
        self.write_backup_copy();
    }

    /// Record that the employer pulled ONE posting, from the row itself.
    ///
    /// The same fact as the bulk button, reachable where somebody triaging a
    /// row at a time actually is. That button needs tick-boxes
    /// by construction, so a person working down the list with the Status
    /// dropdown never met it - and the case it is for, an advert that closed
    /// between two collections, is one they meet constantly.
    pub fn mark_taken_down(&mut self, key: &str) {
        match db::mark_taken_down(&self.conn, std::slice::from_ref(&key.to_string())) {
            Ok(_) => {
                self.triage_message = Some(
                    "marked taken down; it leaves this list and anything you \
                     recorded about it is kept"
                        .to_string(),
                );
            }
            Err(e) => {
                self.triage_message = Some(format!("could not mark taken down: {e}"));
            }
        }
        self.mark_data_changed();
        self.write_backup_copy();
    }

    /// Record that the employer pulled the selected postings.
    ///
    /// NOT ROUTED THROUGH THE RETIRE CONFIRMATION. Removing a job hides
    /// something the person still wanted and deserves a second thought;
    /// recording that an advert closed is reporting a fact, and
    /// by construction it is reversible - relist() clears delisted_at. Asking
    /// "are you sure the employer did that" would be asking them to confirm
    /// the world.
    pub fn mark_selection_taken_down(&mut self) {
        let keys: Vec<String> = self.selected_keys.iter().cloned().collect();
        match db::mark_taken_down(&self.conn, &keys) {
            Ok(n) => {
                self.selected_keys.clear();
                self.triage_message = Some(format!(
                    "marked {n} posting(s) taken down; anything already applied \
                     to kept its status"
                ));
            }
            Err(e) => {
                self.triage_message = Some(format!("could not mark taken down: {e}"));
            }
        }
        self.mark_data_changed();
        self.write_backup_copy();
    }

    /// Write `self.config` straight to disk, bypassing the Config screen's
    /// draft-and-validate path.
    ///
    /// For a switch flipped from somewhere OTHER than that screen - the
    /// add-a-job prompt turning link-reading on. Going through save_config()
    /// would serialise whatever the Config screen's draft happens to hold,
    /// which is not what the person just asked to change.
    pub fn save_config_now(&mut self) {
        match config::save(&self.config_path, &self.config) {
            Ok(()) => self.config_error = None,
            Err(e) => self.config_status = Some(format!("save failed: {e}")),
        }
    }

    pub fn save_settings(&mut self) {
        let _ = settings::save(&self.settings_path, &self.settings);
    }

    /// Write the column layout back to this profile's settings file.
    ///
    /// Called on every change from the gear rather than on closing it:
    /// by construction that panel has no Save button, so a person who rearranges
    /// their columns and then closes the app has not asked for anything to be
    /// lost.
    pub fn save_column_layout(&mut self) {
        self.settings.column_order = views::columns::to_keys(&self.column_order);
        self.settings.column_hidden = views::columns::to_keys(&self.column_hidden);
        self.save_settings();
    }

    /// True while an engine command is running - by construction the same
    /// condition start_process and queue_process test - so a caller can say
    /// why it is not doing the thing it offered rather than refusing in
    /// silence.
    pub fn busy(&self) -> bool {
        self.running_process.is_some()
    }

    /// Put a sentence where the last run's result goes - the one status line
    /// that persists rather than vanishing on the next frame.
    pub fn say(&mut self, message: &str) {
        self.last_run_result = Some((message.to_string(), true));
    }

    /// Starts an engine command. `subcommand_args` is just the verb and its
    /// own flags (e.g. `["collect", "--company", name]`), never the `-m
    /// unlatched` prefix. That prefix, and which program is invoked at all,
    /// is decided by engine_invocation from `self.engine_mode` - so
    /// by construction a call site cannot get it wrong, because it never
    /// writes it.
    /// Run it now. The caller has already established the engine is free.
    fn spawn_process(&mut self, label: &str, subcommand_args: Vec<String>) {
        let (program, args) = engine_invocation(
            &self.engine_mode,
            &self.settings.python_invocation,
            &self.active_home,
            subcommand_args,
        );
        self.log_lines
            .push(format!("$ {} {}", program, args.join(" ")));
        // The previous run's sentence must not be reported as this one's.
        self.last_engine_line = None;
        match RunningProcess::spawn(&program, &args) {
            Ok(proc) => {
                self.running_process = Some((label.to_string(), proc));
            }
            Err(e) => {
                self.log_lines.push(format!("[error] {e}"));
                self.last_run_result = Some((format!("{label} could not start: {e}"), false));
            }
        }
    }

    /// Run it when the engine is free, rather than refusing.
    ///
    /// FOR WHAT A PERSON ASKED FOR, never for scheduled work -
    /// by construction, since start_scheduled_refresh calls start_process
    /// instead. See `pending`.
    pub fn queue_process(&mut self, label: &str, subcommand_args: Vec<String>) {
        if self.running_process.is_none() {
            self.spawn_process(label, subcommand_args);
            return;
        }
        match queue_decision(&self.pending, label, &subcommand_args) {
            QueueDecision::AlreadyWaiting => return,
            QueueDecision::Full => {
                self.last_run_result = Some((
                    format!("{label} was not queued - {MAX_PENDING} commands are already waiting"),
                    false,
                ));
                return;
            }
            QueueDecision::Accept => {}
        }
        let running = self
            .running_process
            .as_ref()
            .map(|(l, _)| l.clone())
            .unwrap_or_default();
        self.pending.push_back((label.to_string(), subcommand_args));
        self.last_run_result = Some(queued_message(label, &running, self.pending.len()));
    }

    pub fn start_process(&mut self, label: &str, subcommand_args: Vec<String>) {
        if let Some((running, _)) = self.running_process.as_ref() {
            // SAID ON SCREEN, not only to the log.
            //
            // This refused silently. The log line below renders on one screen
            // and the person is never on it - so "Add a job by link" pressed
            // during a scheduled refresh closed its dialog, added nothing, and
            // reported nothing. A job somebody meant to record was simply
            // gone, which is the exact failure this app exists to prevent.
            // Found 2026-09-03 on a live profile: a posting applied for that
            // afternoon, and no row for it anywhere.
            let message = skipped_message(label, running);
            self.log_lines.push(message.0.clone());
            self.last_run_result = Some(message);
            return;
        }
        self.spawn_process(label, subcommand_args);
    }

    /// What the engine last said it was doing, for the global indicator.
    ///
    /// THE LATEST LINE, not the whole log. A person glancing at the window
    /// wants "still going, currently on Nimbus" - the log is where they go if
    /// they want the rest, and it lives on one screen.
    ///
    /// Blank while the engine has said nothing yet, which is a real state: the
    /// process is up and the first board has not answered.
    pub fn running_detail(&self) -> Option<(&str, &str)> {
        let (label, _) = self.running_process.as_ref()?;
        let latest = self
            .log_lines
            .iter()
            .rev()
            // The command echo and our own bracketed notes are not progress.
            .find(|l| !l.starts_with('$') && !l.starts_with('['))
            .map(String::as_str)
            .unwrap_or("");
        Some((label.as_str(), latest))
    }

    pub fn poll_process(&mut self) {
        let mut finished_label: Option<(String, i32)> = None;
        let mut wake_in: Option<std::time::Duration> = None;
        if let Some((label, proc)) = self.running_process.as_mut() {
            for line in proc.poll() {
                if let Some(secs) = parse_wake_in(&line) {
                    wake_in = Some(secs);
                } else if !line.trim().is_empty() {
                    // The wake-in marker is machinery, not something the
                    // engine is telling anybody, so by construction it is
                    // filtered out before a line can become the sentence a
                    // person is shown.
                    self.last_engine_line = Some(line.clone());
                }
                self.log_lines.push(line);
            }
            if proc.finished {
                let code = proc.exit_code.unwrap_or(-1);
                self.log_lines
                    .push(format!("[{label} finished, exit code {code}]"));
                finished_label = Some((label.clone(), code));
            }
        }
        if let Some(secs) = wake_in {
            self.next_refresh_check = Some(secs);
        }
        if let Some((label, code)) = finished_label {
            self.running_process = None;
            // A COMPLETION LINE THAT PERSISTS. The indicator disappearing is
            // not an answer: it looks identical whether the run finished, was
            // killed, or the app forgot about it. This outlives the run and
            // says which of those happened.
            let said = self.last_engine_line.take();
            self.last_run_result = Some(finish_message(&label, code, said.as_deref()));
            // An engine run can change anything - rows, companies, groupings,
            // delistings. Naming two caches here was how the dashboard stayed
            // stale after every collect.
            self.mark_data_changed();
            // And the lock is free now - by construction, since the process
            // that held it has just been cleared - so anything the person
            // typed while it was held goes in.
            self.apply_deferred_status();
            self.report_add_job(&label, code);
            // THE NEXT THING SOMEBODY ASKED FOR. Started here rather than on
            // the next frame, so by construction there is no window between
            // the collect finishing and the queued add starting in which a
            // scheduled refresh could take the engine first.
            if let Some((queued, args)) = self.pending.pop_front() {
                self.spawn_process(&queued, args);
            }
        }
        // Cap the log: by construction a long run appends without limit, so
        // without this the app's memory footprint grows across a session.
        // (see report_add_job below for what happens when an add fails)
        if self.log_lines.len() > 2000 {
            let drop = self.log_lines.len() - 2000;
            self.log_lines.drain(0..drop);
        }
    }

    /// Says what happened to an "Add a job by link", on the screen the person
    /// pressed the button on.
    ///
    /// It used to say nothing at all. The modal closed the moment the engine
    /// was SPAWNED, and the engine's answer - including "no title found on
    /// the page, so one has to be given" - went into the log, which is only
    /// rendered on the Companies page - so adding a job could close the modal
    /// and produce neither a job nor a reason (2026-08-06). A failure nobody is
    /// shown is the same class of defect as a board that silently collects
    /// zero.
    ///
    /// On failure the modal comes BACK, with everything still typed in it, so
    /// the fix is one field away rather than a re-entry.
    fn report_add_job(&mut self, label: &str, code: i32) {
        if label != ADD_JOB_LABEL {
            return;
        }
        // THE STAGED POSTING TEXT GOES, whichever way the add ended. A
        // pasted description is handed over as a file rather than an argument
        // (see views::add_job), and nothing was removing it afterwards -
        // observed: a copy of the last job somebody pasted sat in their
        // profile folder next to the database indefinitely, for a reason no
        // screen ever mentions.
        // Deleted here rather than by the engine, because the engine is
        // pointed at whatever path it is given and must not delete a file
        // that might be the person's own.
        let _ = std::fs::remove_file(self.active_home.join(STAGED_DESCRIPTION));
        if code == 0 {
            let added = self
                .log_lines
                .iter()
                .rev()
                .find(|line| line.starts_with("added "))
                .cloned();
            self.triage_message = Some(added.unwrap_or_else(|| "job added".to_string()));
            return;
        }
        // The engine prints why on the line before the exit-code marker.
        let reason = self
            .log_lines
            .iter()
            .rev()
            .find(|line| !line.starts_with('[') && !line.starts_with("$ ") && !line.trim().is_empty())
            .cloned()
            .unwrap_or_else(|| "the job could not be added".to_string());
        self.add_job_draft.error = Some(reason.clone());
        self.show_add_job_modal = true;
        self.triage_message = Some(reason);
    }
}

/// Build the exact command line for an engine subcommand.
///
/// SEPARATE AND PUBLIC so it can be asserted on - see
/// engine_invocation_tests. The bug it fixes is invisible from outside: the
/// command runs, exits zero, and writes to the wrong database, so
/// by construction the only way to catch it is to look at the arguments.
///
/// --home IS ALWAYS PASSED. It used to be omitted, leaving the engine to read
/// UNLATCHED_HOME from this process's environment. That variable is set only
/// when the app was LAUNCHED pinned to a folder; with a registry profile active
/// it is absent and the engine falls back to the platform-default home. So
/// collect, discover, screen and add could all run against a different profile
/// than the one on screen, and the failure points the wrong way: the active
/// profile's list simply does not change, which reads as "the collectors found
/// nothing" - a conclusion this app produces legitimately, with a dashboard
/// panel explaining it.
pub fn engine_invocation(
    mode: &EngineMode,
    python_invocation: &str,
    home: &Path,
    subcommand_args: Vec<String>,
) -> (String, Vec<String>) {
    // Before the subcommand: --home is a top-level argument on the engine's
    // parser, and argparse will not accept it after the verb.
    let mut args = vec!["--home".to_string(), home.to_string_lossy().into_owned()];
    args.extend(subcommand_args);
    match mode {
        // The bundled engine is a standalone frozen executable: its own argv[0]
        // already plays the role of `python -m unlatched`.
        EngineMode::Bundled(path) => (path.to_string_lossy().into_owned(), args),
        EngineMode::Python => {
            args.splice(0..0, ["-m".to_string(), "unlatched".to_string()]);
            (python_invocation.to_string(), args)
        }
    }
}

/// The process label for an add. Matched on in report_add_job, so it lives in
/// one place - by construction the two cannot drift apart.
pub const ADD_JOB_LABEL: &str = "add a job";

/// Where a pasted posting description is staged for the engine to read. Named
/// once, so by construction the writer and the clean-up cannot point at
/// different files.
pub const STAGED_DESCRIPTION: &str = "pending-description.txt";

/// The readable copy kept beside the database. Named so it is obvious what it
/// is to somebody who finds the folder without the app - which is
/// by construction the situation it exists for, since anyone with the app would
/// read the database instead.
pub const BACKUP_CSV_NAME: &str = "pipeline-backup.csv";

/// How long the engine says to wait before asking it about the schedule again.
///
/// `[wake-in] 12345` on a line of its own, in seconds. Parsed rather than
/// computed here because the schedule has exactly one owner - refresh.py - and
/// a Rust reimplementation would be a second one to drift from it.
///
/// SECONDS RATHER THAN A TIMESTAMP, so by construction it can be added to a
/// MONOTONIC clock. A wall-clock time would have to be parsed and would then
/// be wrong across a suspend, a timezone change, or the hour the clocks go
/// back - three separate ways to sleep through a day's postings.
///
/// A silly value is refused rather than obeyed: a bad parse that yielded zero
/// would spin the engine, and one that yielded a week would look exactly like
/// the schedule having quietly stopped.
fn parse_wake_in(line: &str) -> Option<std::time::Duration> {
    const MAX_WAIT_SECS: u64 = 60 * 60 * 24 * 2;
    let secs: u64 = line.trim().strip_prefix("[wake-in]")?.trim().parse().ok()?;
    (1..=MAX_WAIT_SECS)
        .contains(&secs)
        .then(|| std::time::Duration::from_secs(secs))
}

impl eframe::App for UnlatchedApp {
    fn update(&mut self, ctx: &egui::Context, _frame: &mut eframe::Frame) {
        // UNLATCHED_PERF=1 prints the wall time of every frame that took longer
        // than a 60 Hz budget, with the row count and view that produced it.
        // Off by default and costs one Instant::now() when off. Added because
        // "the app is slow" is not something to fix by reading code and
        // guessing - the first measurement has to say WHICH frame is expensive.
        let perf_started = std::env::var_os("UNLATCHED_PERF").map(|_| std::time::Instant::now());
        // Applied every frame from the stored setting rather than once at
        // startup, so by construction switching theme takes effect at once and
        // a profile switch picks up that profile's own choice.
        if self.applied_dark != Some(self.settings.is_dark()) {
            self.applied_dark = Some(self.settings.is_dark());
            crate::theme::apply(ctx, self.settings.is_dark());
        }
        // The same reasoning, for the same reason: a link drawn anywhere in
        // the window has to open where THIS profile says, and a profile switch
        // has to change that without a reload.
        crate::browse::use_browser(&self.settings.browser);
        if !self.window_title_set {
            // Set once here rather than in main.rs's ViewportBuilder.
            // By construction the active profile - and therefore the title - is
            // known only after UnlatchedApp::new has resolved the registry,
            // and no egui::Context exists yet at that point in main.rs.
            self.window_title_set = true;
            ctx.send_viewport_cmd(egui::ViewportCommand::Title(self.window_title()));
        }

        self.poll_process();
        // BEFORE anything draws. Reloading whichever caches are stale here,
        // rather than at each mutation site, is what makes a missed refresh
        // impossible by construction: the check sits between the change and
        // the paint, so a view cannot render from a snapshot older than the
        // data.
        self.sync_caches();
        // The open row's files, and whatever is needed to show the one being
        // looked at. Both are no-ops unless the answer has changed.
        self.sync_attachments();
        if self.running_process.is_some() {
            ctx.request_repaint_after(std::time::Duration::from_millis(100));
        }
        // An app left open all day would otherwise see the schedule once, at
        // launch - by construction, since start_scheduled_refresh runs from
        // the constructor - and the afternoon slot would never come round.
        //
        // WHEN TO ASK AGAIN IS THE ENGINE'S ANSWER, NOT A TIMER HERE. It
        // prints "[wake-in] N" seconds - the time until its own next anchor -
        // and poll_process picks that up. Between anchors the answer to "is a
        // refresh due" is always no, so a fixed poll spent every wake-up
        // learning nothing while still being able to fire minutes late.
        //
        // The fixed interval survives as the FALLBACK for the first check of a
        // session and for an engine too old to say: a schedule that stops
        // being consulted because one line went missing is worse than one that
        // is occasionally early.
        let wait = self.next_refresh_check.unwrap_or(REFRESH_CHECK_EVERY);
        let since = self.last_refresh_check.elapsed();
        if since >= wait {
            self.last_refresh_check = std::time::Instant::now();
            self.start_scheduled_refresh();
        } else {
            // AND ASK TO BE WOKEN FOR IT. Everything above is inside update(),
            // which egui calls only when something happens - an input event, an
            // animation, or a repaint somebody requested. An idle app with no
            // process running is asked to draw by nobody, so without this line
            // the check above is simply never reached and the schedule is
            // consulted exactly once, at launch.
            //
            // Measured on 2026-08-25: open 13:50 to 21:48, a single 14:00
            // anchor, last collection 09:58 - plainly due, and nothing ran.
            ctx.request_repaint_after(wait - since);
        }

        egui::SidePanel::left("nav")
            .resizable(false)
            .exact_width(crate::theme::RAIL_WIDTH)
            .show(ctx, |ui| {
                ui.add_space(14.0);
                ui.heading("Unlatched");
                ui.add_space(8.0);

                show_profile_switcher(self, ui, ctx);
                ui.separator();
                ui.add_space(4.0);

                show_search_box(self, ui);
                ui.add_space(4.0);

                // THREE GROUPS, by construction: NAV_JOBS, NAV_TOOLS and
                // NAV_SETUP - the jobs themselves, the tools that act on them,
                // and the setup you touch once. Daily work at the top; the
                // things you set up once sit at the bottom, separated by space
                // rather than jumbled in. Config sits directly above Settings
                // because they are the same kind of errand. A flat list of
                // every link is a list; this is a structure.
                if views::getting_started::needed(self) {
                    let (view, label) = NAV_GETTING_STARTED;
                    nav_entry(self, ui, view, label);
                }
                // Ordered by how often a person needs it, which is also the
                // order the work happens in: see the day, decide on new
                // postings, then track the ones you acted on. All jobs is the
                // archive you consult, not a step - so it sits last, below
                // Pipeline (decided 2026-08-08).
                crate::theme::nav_heading(ui, "JOBS");
                for (view, label) in NAV_JOBS {
                    nav_entry(self, ui, view, label);
                }

                crate::theme::nav_heading(ui, "TOOLS");
                for (view, label) in NAV_TOOLS {
                    nav_entry(self, ui, view, label);
                }

                // Pushes the setup group down to roughly a fifth from the
                // bottom, wherever the window is sized.
                let remaining = ui.available_height();
                ui.add_space((remaining - NAV_BOTTOM_GROUP_HEIGHT).max(12.0));
                crate::theme::nav_heading(ui, "SETUP");
                for (view, label) in NAV_SETUP {
                    nav_entry(self, ui, view, label);
                }
            });

        // Drawn last, so by construction it covers everything - and that also
        // stops the tutorial's own view switching from fighting a click
        // underneath it.
        crate::tutorial::show(self, ctx);

        // A RUN IS VISIBLE FROM EVERY SCREEN. It used to be visible from one
        // - the log, on the Companies page - so a running collect was, from
        // anywhere else in the app, indistinguishable from nothing happening.
        // A bottom strip rather than a line inside a view, so by construction
        // it belongs to the window rather than to whatever is being looked
        // at.
        crate::views::running_bar::show(self, ctx);

        egui::CentralPanel::default().show(ctx, |ui| match self.view {
            View::GettingStarted => views::getting_started::show(self, ui),
            View::Triage => views::triage::show(self, ui, ctx),
            View::Pipeline => views::pipeline::show(self, ui),
            View::Keywords => views::keywords::show(self, ui),
            View::Companies => views::companies::show(self, ui),
            View::Config => views::config_view::show(self, ui),
            View::Dashboard => views::dashboard_view::show(self, ui),
            View::AllJobs => views::triage::show_all_jobs(self, ui, ctx),
            View::Search => views::triage::show_search(self, ui, ctx),
            View::Removed => views::triage::show_removed(self, ui, ctx),
            View::Resumes => views::resumes_view::show(self, ui),
            View::Profiles => views::profiles_view::show(self, ui),
            View::Agent => views::agent::show(self, ui),
        });

        views::new_profile::show(self, ctx);
        views::add_job::show(self, ctx);
        // Outside the central panel for the same reason as the run prompt: a
        // confirmation the person has not answered must not be stranded by
        // navigating somewhere else.
        views::triage::confirm_retire_window(self, ctx);
        // Drawn from update() rather than from the Triage view: a status can
        // be set from the bulk bar on All jobs and from the row dropdown on
        // either list, so by construction a prompt living on one screen would
        // silently swallow the change made from the other.
        views::triage::status_note_window(self, ctx);
        // Rendered outside the central panel, so by construction navigating to
        // another view cannot strand a prompt the person has not answered.
        views::config_view::show_run_prompt(self, ctx);

        if let Some(started) = perf_started {
            let ms = started.elapsed().as_secs_f32() * 1000.0;
            if ms > 16.0 {
                eprintln!(
                    "[perf] frame {ms:.1} ms  view={:?}  triage_rows={}  pipeline_log={}",
                    self.view,
                    self.triage_rows.len(),
                    self.pipeline_log.len(),
                );
            }
        }
    }
}

/// Active-profile dropdown plus New/Remove buttons, shown above the view
/// list. Disabled as a whole (with an explanatory hover) when
/// `profile_env_locked`: UNLATCHED_HOME already decided the home for this
/// launch, and letting the registry be edited anyway would both do nothing
/// useful (the env var wins again on next launch) and, worse, write to the
/// real profiles.json from what is meant to be an isolated run.
fn show_profile_switcher(app: &mut UnlatchedApp, ui: &mut egui::Ui, ctx: &egui::Context) {
    let locked = app.profile_env_locked;
    let mut switch_to: Option<(String, String)> = None;

    let people = profiles::people(&app.profile_registry);
    let searches = profiles::searches_for(&app.profile_registry, &app.active_person);

    let combo_response = ui
        .add_enabled_ui(!locked, |ui| {
            // WHO. Picking a person moves to their first search rather than
            // keeping the current search name: searches are named per person
            // by construction, so a name carried across would usually resolve
            // to nothing.
            egui::ComboBox::from_id_source("person_switcher")
                .width(150.0)
                .selected_text(app.active_person.clone())
                .show_ui(ui, |ui| {
                    if ui
                        .selectable_label(
                            app.active_person == profiles::DEFAULT_PROFILE,
                            profiles::DEFAULT_PROFILE,
                        )
                        .clicked()
                    {
                        switch_to = Some((
                            profiles::DEFAULT_PROFILE.to_string(),
                            profiles::DEFAULT_SEARCH.to_string(),
                        ));
                    }
                    for person in &people {
                        if ui
                            .selectable_label(&app.active_person == person, person)
                            .clicked()
                        {
                            let first = profiles::searches_for(&app.profile_registry, person)
                                .first()
                                .cloned()
                                .unwrap_or_else(|| profiles::DEFAULT_SEARCH.to_string());
                            switch_to = Some((person.clone(), first));
                        }
                    }
                });

            // WHICH HUNT. Only shown once this person is running more than
            // one: a picker with a single entry is furniture, and every
            // migrated profile has exactly one.
            if searches.len() > 1 {
                egui::ComboBox::from_id_source("search_switcher")
                    .width(150.0)
                    .selected_text(app.active_search.clone())
                    .show_ui(ui, |ui| {
                        for search in &searches {
                            if ui
                                .selectable_label(&app.active_search == search, search)
                                .clicked()
                            {
                                switch_to = Some((app.active_person.clone(), search.clone()));
                            }
                        }
                    });
            }
        })
        .response;

    if locked {
        combo_response.on_hover_text(
            "UNLATCHED_HOME is set in the environment, so this launch is locked to that \
             folder and the profile registry is read-only (this keeps isolated or scripted \
             launches from touching your real profiles).",
        );
    }

    // New/Remove deliberately do NOT live here. Sitting beside the dropdown,
    // Remove was one misclick away from making a seeker invisible - and it
    // fired with no confirmation. Both moved to the Profiles view; the
    // sidebar keeps only the frequent, harmless action of switching.

    // A view (Profiles) can ask for a switch; apply it here, where the
    // borrow of app state is already unwound.
    if let Some(requested) = app.profile_switch_request.take() {
        switch_to = Some(requested);
    }

    if let Some((person, search)) = switch_to {
        if person != app.active_person || search != app.active_search {
            let home = profiles::home_for(&app.profile_registry, &person, &search);
            app.profile_registry.active = profiles::Active {
                person: person.clone(),
                search: search.clone(),
            };
            if let Err(e) = profiles::save(&app.profile_registry) {
                app.profile_message = Some(format!("could not save profiles.json: {e}"));
            }
            app.switch_profile(&person, &search, home, ctx);
        }
    }

    if let Some(msg) = &app.profile_message {
        ui.colored_label(egui::Color32::LIGHT_BLUE, msg);
    }
}

#[cfg(test)]
mod keyboard_scroll_tests {
    /// MOVING THE HIGHLIGHT HAS TO MOVE THE VIEW WITH IT.
    ///
    /// THE BUG THIS GUARDS, reported 2026-09-04 on the installed build:
    /// move_selection set `triage_selected` and nothing else, so on a list
    /// longer than the window the band walked off the bottom edge and kept
    /// going. The keys still worked - the highlighted row was simply somewhere
    /// the person could not see - and every status keystroke after that landed
    /// on a row they were not looking at.
    ///
    /// READ FROM THE SOURCE, like cache_invalidation_tests below and for the
    /// same reason: calling move_selection needs a whole app - a profile
    /// registry, a database and an egui context - none of which exist in a
    /// unit test here. Blunt, and it is the only thing that can assert the
    /// scroll was not simply forgotten again.
    #[test]
    fn moving_the_selection_by_keyboard_also_scrolls_to_it() {
        const SOURCE: &str = include_str!("app.rs");
        let body = SOURCE
            .split("pub fn move_selection(&mut self, delta: i32) {")
            .nth(1)
            .expect("move_selection was renamed")
            .split("\n    }")
            .next()
            .expect("move_selection has no body");
        assert!(
            body.contains("scroll_to_selected = true"),
            "move_selection changes the highlight without bringing it into view"
        );
    }

    /// The counterpart: a row opened from another screen lands on All jobs,
    /// which can hold thousands of rows. Selecting it without scrolling reads
    /// as a click that only changed screens.
    #[test]
    fn opening_a_job_from_elsewhere_scrolls_to_it() {
        const SOURCE: &str = include_str!("app.rs");
        let body = SOURCE
            .split("pub fn open_job_anywhere(&mut self, key: &str) {")
            .nth(1)
            .expect("open_job_anywhere was renamed")
            .split("\n    }")
            .next()
            .expect("open_job_anywhere has no body");
        assert!(
            body.contains("scroll_to_selected = true"),
            "open_job_anywhere selects a row it never brings into view"
        );
    }
}

#[cfg(test)]
mod cache_invalidation_tests {
    /// Every cache this app holds must be reachable from `sync_caches`.
    ///
    /// THE BUG THIS GUARDS AGAINST IS THE ONE THAT SHIPPED. Reloads used to be
    /// hand-written at each mutation site, and `refresh_dashboard` was simply
    /// missing from `set_status_for` - so marking jobs applied left the counts
    /// stale until somebody pressed Refresh. The counter fixes that, but only
    /// while every cache is actually consulted.
    ///
    /// Adding a sixth cached view and forgetting to add it to `sync_caches`
    /// reintroduces exactly the original defect for that one view, silently.
    /// Reading our own source is blunt, but it is the only way to assert
    /// "nothing was left out" without a full app instance - which
    /// by construction needs a profile registry, a database and an egui context,
    /// none of which exist in a unit test here.
    const SOURCE: &str = include_str!("app.rs");

    fn body_of(function: &str) -> &'static str {
        let start = SOURCE
            .find(function)
            .unwrap_or_else(|| panic!("{function} not found - was it renamed?"));
        let rest = &SOURCE[start..];
        // The next `\n    }` at method indentation closes it. Crude, and it
        // only has to be right for one small function.
        let end = rest.find("\n    }").expect("unterminated function");
        &rest[..end]
    }

    #[test]
    fn the_collector_line_does_no_database_work_while_drawing() {
        // Called once per frame by both the dashboard and the jobs list. It
        // used to run the timezone query and a GROUP BY over every job row
        // there, which is the per-frame-query shape this app already had to
        // remove once - and worse, under the contention of a running collect
        // the read failed and unwrap_or_default reported "nothing pending",
        // turning a staleness warning into silence exactly when the app was
        // busiest. Seen on screen 2026-08-25 at 21:53.
        let body = body_of("fn collectors_pending");

        for forbidden in ["source_last_seen", "local_offset_secs", "self.conn"] {
            assert!(
                !body.contains(forbidden),
                "collectors_pending touches {forbidden} in the draw path:\n{body}"
            );
        }
        // It must still consult the CLOCK every frame - caching the verdict
        // would freeze the badge on the one day nothing changes, which is the
        // day a dead collector produces.
        assert!(
            body.contains("seconds_into_local_day"),
            "the lateness verdict is no longer recomputed as it draws:\n{body}"
        );
    }

    #[test]
    fn the_schedule_check_asks_to_be_woken_for_its_own_deadline() {
        // update() runs when egui has a reason to draw. The refresh check sits
        // inside it, so unless something requests a repaint for the moment the
        // check comes due, an idle app never reaches it and the schedule is
        // consulted exactly once, at launch.
        //
        // That is not hypothetical: measured 2026-08-25, open 13:50 to 21:48
        // with a due 14:00 anchor, nothing collected, every test green.
        let body = SOURCE
            // The FULL expression, not a prefix. `let wait =
            // self.next_refresh_check` alone stopped being unique the moment
            // until_next_look was added, and this guard then read the wrong
            // function and failed - which is the right failure for a locator
            // that has gone ambiguous, but the locator is what needed fixing.
            .find("let wait = self.next_refresh_check.unwrap_or(REFRESH_CHECK_EVERY)")
            .map(|start| &SOURCE[start..start + 1800])
            .expect("the refresh-check block was renamed or removed");

        assert!(
            body.contains("request_repaint_after"),
            "nothing wakes the app for its next anchor, so the check below \
             will only run if a person happens to touch the window:\n{body}"
        );
        assert!(
            body.contains("start_scheduled_refresh"),
            "the block no longer asks the engine anything:\n{body}"
        );
    }

    #[test]
    fn sync_caches_covers_every_cache_the_app_holds() {
        let declared: Vec<&str> = SOURCE
            .lines()
            .filter_map(|line| {
                let t = line.trim();
                t.strip_suffix(": u64,")
                    .filter(|name| name.ends_with("_loaded_at"))
            })
            .collect();

        assert!(
            declared.len() >= 5,
            "expected the five known caches, found {declared:?} - has the \
             field naming convention changed?"
        );

        let sync = body_of("fn sync_caches");
        for field in &declared {
            assert!(
                sync.contains(field),
                "{field} is never consulted in sync_caches, so that view will \
                 render from a stale snapshot after any change"
            );
        }
    }

    #[test]
    fn every_view_that_shows_the_jobs_list_reloads_it() {
        // The sibling test above proves every CACHE is consulted. It cannot
        // prove every VIEW reaches the arm that consults it - and that is the
        // gap that shipped, observed: Search rendered the jobs list, was
        // absent from sync_caches, and showed a status the app had already
        // changed.
        //
        // Any view whose draw path calls list_jobs_for is showing that list.
        let sync = body_of("fn sync_caches");
        for view in ["Triage", "AllJobs", "Search"] {
            assert!(
                sync.contains(&format!("View::{view}")),
                "View::{view} renders the jobs list but sync_caches never \
                 reloads it, so it will show data the app has already changed"
            );
        }
    }

    #[test]
    fn every_cache_is_stamped_after_the_startup_load() {
        // The constructor loads all five eagerly because the landing view is
        // chosen from what they contain. Without stamping, sync_caches finds
        // them stale on the first frame by construction - views start at 0,
        // data at 1 - and repeats all five queries.
        let new_body = body_of("pub fn new()");
        for field in ["triage_loaded_at", "dashboard_loaded_at"] {
            assert!(
                new_body.contains(field),
                "{field} is loaded at startup but never stamped, so the first \
                 frame reloads it for nothing"
            );
        }
    }
}

#[cfg(test)]
mod engine_invocation_tests {
    use super::{engine_invocation, EngineMode};
    use std::path::{Path, PathBuf};

    fn args_for(mode: &EngineMode, home: &str) -> Vec<String> {
        engine_invocation(mode, "python", Path::new(home), vec!["collect".to_string()]).1
    }

    #[test]
    fn every_engine_command_names_the_home_it_is_for() {
        // The whole defect. Without --home the engine reads UNLATCHED_HOME
        // from the app's environment, which by construction is absent whenever
        // a registry profile is active - so a collect could write into the
        // default profile while the person watched a different one fail to
        // change.
        let bundled = EngineMode::Bundled(PathBuf::from("engine.exe"));
        let args = args_for(&bundled, "D:/people/dana/hr");
        assert_eq!(&args[..2], ["--home", "D:/people/dana/hr"]);
    }

    #[test]
    fn home_comes_before_the_subcommand() {
        // --home is a top-level argument on the engine's parser, and argparse
        // by construction will not accept it after the verb - so the ordering
        // here is load-bearing rather than cosmetic.
        let bundled = EngineMode::Bundled(PathBuf::from("engine.exe"));
        let args = args_for(&bundled, "D:/x");
        assert_eq!(args, ["--home", "D:/x", "collect"]);
    }

    #[test]
    fn the_python_path_puts_the_module_flags_first() {
        // `python -m unlatched --home X collect`, not `python --home X -m ...`,
        // which python itself would reject.
        let (program, args) =
            engine_invocation(&EngineMode::Python, "py -3", Path::new("D:/x"),
                              vec!["screen".to_string()]);
        assert_eq!(program, "py -3");
        assert_eq!(args, ["-m", "unlatched", "--home", "D:/x", "screen"]);
    }

    #[test]
    fn the_subcommands_own_arguments_survive() {
        let bundled = EngineMode::Bundled(PathBuf::from("engine.exe"));
        let (_program, args) = engine_invocation(
            &bundled, "python", Path::new("D:/x"),
            vec!["add".to_string(), "https://example.com/j/1".to_string(),
                 "--title".to_string(), "Analyst".to_string()]);
        assert_eq!(
            args,
            ["--home", "D:/x", "add", "https://example.com/j/1", "--title", "Analyst"]
        );
    }
}

#[cfg(test)]
mod sort_tests {
    use super::*;
    use crate::db::Job;

    fn row(title: &str, company: &str, score: Option<f64>, salary: Option<i64>) -> TriageRow {
        TriageRow {
            job: Job {
                key: title.to_string(),
                title: title.to_string(),
                score,
                salary_max: salary,
                ..Job::default()
            },
            company_name: Some(company.to_string()),
            ..TriageRow::default()
        }
    }

    #[test]
    fn a_missing_value_sorts_last_in_both_directions() {
        // Sorting by salary and getting a screen of blanks at the top is the
        // reader being punished for the employer not stating a figure.
        for desc in [true, false] {
            let mut rows = vec![
                row("no salary", "Acme", Some(90.0), None),
                row("has salary", "Nimbus", Some(80.0), Some(120_000)),
            ];
            UnlatchedApp::sort_rows_multi(&mut rows, &[(SortBy::Salary, desc)]);
            assert_eq!(rows[0].job.title, "has salary", "desc={desc}");
        }
    }

    #[test]
    fn score_descending_puts_the_best_first() {
        let mut rows = vec![
            row("low", "Acme", Some(60.0), None),
            row("high", "Nimbus", Some(95.0), None),
        ];
        UnlatchedApp::sort_rows_multi(&mut rows, &[(SortBy::Score, true)]);
        assert_eq!(rows[0].job.title, "high");
        UnlatchedApp::sort_rows_multi(&mut rows, &[(SortBy::Score, false)]);
        assert_eq!(rows[0].job.title, "low");
    }

    #[test]
    fn company_sorts_alphabetically_ignoring_case() {
        let mut rows = vec![
            row("b", "zeta", Some(1.0), None),
            row("a", "Alpha", Some(1.0), None),
        ];
        UnlatchedApp::sort_rows_multi(&mut rows, &[(SortBy::Company, false)]);
        assert_eq!(rows[0].company_name.as_deref(), Some("Alpha"));
    }

    /// THE SORT THAT WAS ASKED FOR: undecided first, newest of those on top.
    ///
    /// One key cannot say this. Sorted by posted alone, a job passed on last
    /// month sits above one that arrived this morning; sorted by status alone,
    /// the undecided rows are in whatever order the query returned.
    #[test]
    fn status_then_posted_keeps_undecided_rows_on_top() {
        let mut rows = vec![
            row("passed today", "Acme", None, None),
            row("undecided old", "Nimbus", None, None),
            row("undecided today", "Vex", None, None),
        ];
        rows[0].status = Some("pass".to_string());
        rows[0].job.posted_at = Some("2026-09-04".to_string());
        rows[1].job.posted_at = Some("2020-01-01".to_string());
        rows[2].job.posted_at = Some("2026-09-04".to_string());

        UnlatchedApp::sort_rows_multi(
            &mut rows,
            &[(SortBy::Status, false), (SortBy::Posted, true)],
        );
        assert_eq!(rows[0].job.title, "undecided today");
        assert_eq!(rows[1].job.title, "undecided old");
        // The settled row falls to the bottom even though it is the newest,
        // which is the whole point of the first key.
        assert_eq!(rows[2].job.title, "passed today");
    }

    /// A LATER KEY ONLY BREAKS TIES. If the second key could reorder rows the
    /// first key had already separated, "status then posted" would not mean
    /// what it says.
    #[test]
    fn a_second_key_never_overrides_the_first() {
        let mut rows = vec![
            row("settled newest", "Acme", None, None),
            row("open oldest", "Nimbus", None, None),
        ];
        rows[0].status = Some("no_offer".to_string());
        rows[0].job.posted_at = Some("2026-09-04".to_string());
        rows[1].job.posted_at = Some("2019-01-01".to_string());
        UnlatchedApp::sort_rows_multi(
            &mut rows,
            &[(SortBy::Status, false), (SortBy::Posted, true)],
        );
        assert_eq!(rows[0].job.title, "open oldest");
    }

    /// Clicking a heading promotes it and pushes the previous primary down,
    /// which is what makes a two-key sort reachable without a modifier.
    #[test]
    fn clicking_a_heading_promotes_it_and_keeps_the_old_one() {
        let mut keys = vec![(SortBy::Status, false), (SortBy::Posted, true)];
        // Simulating sort_by_column's list surgery, which is the part that
        // has to be right; the method itself needs an app to call.
        let column = SortBy::Score;
        keys.retain(|(c, _)| *c != column);
        keys.insert(0, (column, true));
        keys.truncate(MAX_SORT_KEYS);
        assert_eq!(keys[0].0, SortBy::Score);
        assert_eq!(keys[1].0, SortBy::Status);
        assert_eq!(keys.len(), 3);
    }

    /// THE CAP HOLDS. A fourth column pushes the least significant one out
    /// rather than growing a sort nobody can read.
    #[test]
    fn the_sort_never_grows_past_three_keys() {
        let mut keys = vec![
            (SortBy::Status, false),
            (SortBy::Posted, true),
            (SortBy::Score, true),
        ];
        keys.insert(0, (SortBy::Company, false));
        keys.truncate(MAX_SORT_KEYS);
        assert_eq!(keys.len(), MAX_SORT_KEYS);
        assert!(!keys.iter().any(|(c, _)| *c == SortBy::Score));
    }

    #[test]
    fn posted_descending_means_newest_first() {
        let mut rows = vec![
            row("old", "Acme", None, None),
            row("new", "Nimbus", None, None),
        ];
        rows[0].job.posted_at = Some("2020-01-01".to_string());
        rows[1].job.posted_at = Some("2026-08-01".to_string());
        UnlatchedApp::sort_rows_multi(&mut rows, &[(SortBy::Posted, true)]);
        assert_eq!(rows[0].job.title, "new");
    }

    /// THE CASE THAT SENT SOMEBODY LOOKING FOR A BROKEN BUTTON. A handoff
    /// whose contents were already taken in prints this and exits 0: the run
    /// worked, nothing changed on screen, and the strip said "finished" -
    /// true, and an answer to nothing. Reported on the installed build,
    /// 2026-09-02, as the menu entry doing nothing at all.
    /// THE DEFECT THIS REPLACED. Pressing "Add a job by link" during a
    /// scheduled collect used to write a line to a log rendered on another
    /// screen and do nothing else - so a posting somebody had just applied
    /// for left no row at all. Found 2026-09-03 on a live profile with a
    /// 13-minute collect running: zero rows with source 'manual' in the whole
    /// database.
    #[test]
    fn a_queued_command_says_what_it_is_waiting_for() {
        let (line, ok) = queued_message("add a job", "scheduled refresh", 1);
        assert!(ok, "waiting is not a failure");
        assert!(line.contains("scheduled refresh"), "{line}");
        // No position for the first in line: "(1 waiting)" reads like a count
        // of a problem rather than "you are next".
        assert!(!line.contains('('), "{line}");
    }

    #[test]
    fn a_queue_with_others_in_it_says_how_many() {
        let (line, _) = queued_message("add a job", "scheduled refresh", 3);
        assert!(line.contains("3 waiting"), "{line}");
    }

    /// Pressing Add twice while a collect runs must add the job ONCE.
    #[test]
    fn the_same_request_twice_waits_once() {
        let mut q = std::collections::VecDeque::new();
        let args = vec!["add".to_string(), "https://x/1".to_string()];
        assert_eq!(queue_decision(&q, "add a job", &args), QueueDecision::Accept);
        q.push_back(("add a job".to_string(), args.clone()));
        assert_eq!(
            queue_decision(&q, "add a job", &args),
            QueueDecision::AlreadyWaiting
        );
    }

    /// TWO DIFFERENT JOBS PASTED DURING ONE COLLECT MUST BOTH SURVIVE. A
    /// dedupe on the label alone would silently drop the second, which is the
    /// same lost-work defect wearing a different hat.
    #[test]
    fn two_different_jobs_both_wait() {
        let mut q = std::collections::VecDeque::new();
        q.push_back((
            "add a job".to_string(),
            vec!["add".to_string(), "https://x/1".to_string()],
        ));
        let other = vec!["add".to_string(), "https://x/2".to_string()];
        assert_eq!(queue_decision(&q, "add a job", &other), QueueDecision::Accept);
    }

    #[test]
    fn the_queue_has_a_cap() {
        let mut q = std::collections::VecDeque::new();
        for n in 0..MAX_PENDING {
            q.push_back(("add a job".to_string(), vec![format!("https://x/{n}")]));
        }
        assert_eq!(
            queue_decision(&q, "add a job", &["https://x/new".to_string()]),
            QueueDecision::Full
        );
    }

    #[test]
    fn a_run_that_did_nothing_says_what_it_did_instead_of_just_finishing() {
        let (line, ok) =
            finish_message("pull Handoff file", 0, Some("nothing new from imported"));
        assert!(ok);
        assert_eq!(line, "pull Handoff file: nothing new from imported");
    }

    /// A command that printed nothing still has to report. Saying only the
    /// label is right HERE - there is no answer to pass on - and it is what
    /// used to be said after every run, whatever the engine had reported.
    #[test]
    fn a_silent_run_still_reports_that_it_finished() {
        assert_eq!(finish_message("collect", 0, None).0, "collect finished");
    }

    /// A failure carries the engine's last word too. Sending somebody to the
    /// log for a one-line reason is the same trip this removes.
    #[test]
    fn a_failure_carries_the_reason_when_there_is_one() {
        let (line, ok) = finish_message("collect", 2, Some("no such company"));
        assert!(!ok);
        assert!(line.contains("no such company"), "{line}");
        assert!(line.contains("exit code 2"), "{line}");
    }

    /// WENT-WELL COMES FROM THE EXIT CODE, never from the words - the strip
    /// colours on that flag. An engine line reading "failed to read 3 boards"
    /// must not turn a successful run red, nor "all good" turn a failure
    /// green, which is what reading the sentence would do.
    #[test]
    fn the_verdict_is_the_exit_code_and_not_the_wording() {
        assert!(finish_message("collect", 0, Some("failed to read 3 boards")).1);
        assert!(!finish_message("collect", 1, Some("all good")).1);
    }

    #[test]
    fn the_engines_wake_time_is_read_from_its_output() {
        assert_eq!(
            parse_wake_in("[wake-in] 5400"),
            Some(std::time::Duration::from_secs(5400))
        );
        // Whitespace and the surrounding log noise a real run produces.
        assert_eq!(
            parse_wake_in("  [wake-in]   60  "),
            Some(std::time::Duration::from_secs(60))
        );
    }

    #[test]
    fn a_line_that_is_not_a_wake_time_is_ignored() {
        // Every other line of a collect goes past this. A loose parse would
        // pick a number out of "Acme [greenhouse] 12 collected" and reschedule
        // the whole app around it.
        for line in [
            "Acme                    [greenhouse] 12 collected, 3 new",
            "not due: before 11:00",
            "[wake-in]",
            "[wake-in] soon",
            "$ unlatched refresh",
        ] {
            assert_eq!(parse_wake_in(line), None, "{line}");
        }
    }

    #[test]
    fn a_silly_wake_time_is_refused_rather_than_obeyed() {
        // Zero would spin the engine; a fortnight would look exactly like the
        // schedule having quietly stopped. Both are likelier from a bad parse
        // or a future config than from anything refresh.py would print.
        assert_eq!(parse_wake_in("[wake-in] 0"), None);
        assert_eq!(parse_wake_in("[wake-in] 999999999"), None);
        assert_eq!(parse_wake_in("[wake-in] -5"), None);
    }

    // (The two apply_filter tests that stood here went with TriageFilter. What
    // they covered - that Alt keeps only below-salary rows, that Taken down keeps
    // only delisted rows - is now a WHERE clause, so it is tested against a
    // real database in db::tests::each_module_lists_exactly_what_its_card_counts
    // rather than against a hand-built Vec that could never disagree with the
    // query it was standing in for.)
}
