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
/// what it is - the app does not open it either way, but the person who
/// downloads it should get a file their own machine opens without ceremony.
const SNAPSHOT_NAME: &str = "posting as it read when you applied.txt";

/// Where a Save dialog opens by default. The first user: "When saving files, they should
/// default to the downloads folder, but allow the user to change target" - so
/// this only picks the starting directory and never forces it.
///
/// USERPROFILE rather than a crate: it is the variable Windows itself sets,
/// and a missing one falls back to the dialog's own default rather than
/// inventing a path that may not exist.
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
// Triage could not express the modules the first user asked for.)

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
}

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
/// path. Marking nine rejections in one gesture is one thing that happened,
/// and typing the same sentence nine times is not a feature.
pub struct PendingStatus {
    pub keys: Vec<String>,
    pub status: String,
    /// What the prompt calls the job (or "9 jobs"), settled when it opens so
    /// the heading cannot change under a person who is mid-sentence.
    pub subject: String,
    pub note: String,
    /// Only ever filled for an Offer. See status::has_offer_fields.
    pub pay: String,
    pub offer_date: String,
}

/// Which population the list is showing. Triage is the working queue -
/// qualified rows the person has not closed out. All jobs is everything still
/// worth seeing, which is a different question and needs a different query.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum ListScope {
    Triage,
    All,
    /// Rows the person removed from their lists. Its own scope rather than a
    /// separate screen so the same table, columns and actions apply - the way
    /// back has to be as ordinary as the way out, or "delete" is a cliff.
    Retired,
    /// Rows folded behind another posting as the same job. Same reasoning as
    /// Retired: a grouping the person cannot inspect and undo is a merge that
    /// disappeared a job, which is the expensive way to be wrong here.
    Duplicates,
    /// One dashboard module, as its own list.
    ///
    /// A SCOPE, NOT A FILTER. These used to be TriageFilter values applied to
    /// rows the triage query had already returned, which made every module a
    /// SUBSET of Triage - so "Taken down" and "No Offer", which Triage hides
    /// by design, could never show anything. The first user asked for each module to be
    /// its own list; this is what makes that true rather than nearly true.
    Module(crate::modules::Module),
}

/// Text-field state for the "New profile" modal (views/new_profile.rs).
/// Plain strings all the way through, same reasoning as ConfigDraft: an
/// in-progress edit never has to be a valid profile.
#[derive(Default, Clone, Debug)]
pub struct NewProfileDraft {
    /// The person. Typing one that already exists adds a search to them rather
    /// than rejecting the name - that is the whole point of the two-level
    /// model, and refusing it would push people back to "Maya Ellison (local)".
    pub name: String,
    /// What this hunt is called. Blank means the person's first search, which
    /// is named "Default" so it matches what a migrated profile looks like.
    pub search: String,
    pub home: String,
    pub resume_path: String,
    pub error: Option<String>,
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
    /// Derived from the two below via profiles::label - kept as a field because
    /// the window title and the switcher both read it every frame.
    pub profile_name: String,
    pub active_person: String,
    pub active_search: String,
    // True when UNLATCHED_HOME was set at process startup: the registry is
    // never read from disk into edits and never written back while this is
    // set, so a scripted/isolated launch (the GUI QC harness, a --home-style
    // override) can never mutate a real profiles.json. See paths::platform_default_home.
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
    /// A switch requested from a view rather than the sidebar dropdown, applied
    /// once at the end of the frame so a view never re-enters switch_profile
    /// while it is still borrowing app state.
    pub profile_switch_request: Option<(String, String)>,
    /// UNLATCHED_HOME pinned this launch to one folder, so the registry is
    /// read-only. Computed once at startup rather than re-read per frame.
    pub profile_locked: bool,
    /// Set when a Save actually CHANGED the config, so the app can offer to
    /// re-run the search. Saving an untouched form offers nothing - a prompt
    /// that appears when nothing happened is noise, and noise gets clicked
    /// through without reading.
    pub offer_run_after_save: bool,
    pub new_profile_draft: NewProfileDraft,
    window_title_set: bool,
    /// Which theme is currently applied to the egui context, so visuals are
    /// only pushed when the setting actually changes rather than every frame.
    applied_dark: Option<bool>,

    pub view: View,

    /// Bumped by every change to stored data. Each cached view records the
    /// version it loaded at and reloads when its copy is stale.
    ///
    /// WHY A COUNTER AND NOT A CALL AT EACH MUTATION SITE. The call-at-each-site
    /// approach is what shipped, and it was already wrong: refresh_dashboard()
    /// ran in three places - construction, profile switch, and its own Refresh
    /// button - and NOT after a status change, so marking jobs applied left the
    /// dashboard counts stale until somebody pressed Refresh.
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
    pub triage_show_all: bool,
    /// Off by default: one opening listed in five cities is five rows of the
    /// same triage decision. On, nothing is folded.
    pub triage_every_location: bool,
    pub triage_selected: Option<String>,
    /// The row whose posting is open inline. One at a time: an accordion of
    /// several open blocks is a wall of text with no list left in it.
    pub triage_expanded: Option<String>,
    /// Bring `triage_selected` into view on the next frame, then stop.
    ///
    /// ARRIVING AT A LIST IS NOT THE SAME AS SEEING THE ROW. Opening a card on
    /// the Pipeline already selected and expanded the job and switched to All
    /// jobs - and landed at the TOP of ten thousand rows, with the open posting
    /// somewhere below the fold. From the reader's side the click did nothing
    /// but change screens.
    ///
    /// One frame only: a sticky flag would fight the scrollbar every time the
    /// person tried to look somewhere else.
    pub scroll_to_selected: bool,
    pub expanded_tab: ExpandedTab,
    /// Which list is on screen. A dashboard tile sets this to its own module
    /// rather than leaving a filter on Triage - see ListScope::Module.
    pub list_scope: ListScope,
    /// Sort column and direction for the list. Defaults to score descending,
    /// which is the order the query already returns.
    pub triage_sort: SortBy,
    pub triage_sort_desc: bool,
    pub triage_note_open: bool,
    pub triage_note_just_opened: bool,
    pub triage_note_buffer: String,
    pub triage_message: Option<String>,

    /// The job list's columns, left to right, and which of them are turned
    /// off. Held as ids rather than as the strings on disk so a typo in the
    /// settings file cannot reach the drawing code.
    pub column_order: Vec<views::columns::ColumnId>,
    pub column_hidden: Vec<views::columns::ColumnId>,
    pub show_column_settings: bool,

    /// Rows ticked for a bulk action, by jobs.key.
    ///
    /// Keys, never row indices: the list re-sorts and re-filters under the
    /// person while they are selecting, and an index-based selection would
    /// quietly come to mean different rows than the ones they ticked.
    pub selected_keys: HashSet<String>,
    /// Whether the retire confirmation is up, and what it needs to say.
    pub confirm_retire: Option<RetireConfirm>,
    /// The status change being written, while its note is being typed.
    pub pending_status: Option<PendingStatus>,
    /// The All jobs screen showing what was removed instead of what is live.
    pub show_retired: bool,
    /// The All jobs screen showing what was grouped as a duplicate.
    pub show_duplicates: bool,
    /// Where the last export went, or why it did not. Shown beside the button.
    pub export_message: Option<String>,
    /// When the automatic copy was last written, so a burst of keyboard triage
    /// does not rewrite it once per keystroke.
    pub last_backup: Option<std::time::Instant>,
    /// Set when the person followed the pointer from the job list, so Config
    /// opens with the added-links section expanded instead of dropping them on
    /// a page of collapsed headings to hunt through. Cleared once it is used.
    pub focus_added_links_setting: bool,

    pub pipeline_log: Vec<StatusLogEntry>,
    /// Notes written outside a status change. Kept apart from the log because
    /// they ARE apart in the database - see db::add_note for why a note-only
    /// row in job_status_log would read as an erased status.
    pub pipeline_notes: Vec<db::Note>,
    pub pipeline_current: HashMap<String, CurrentStatus>,
    pub pipeline_job_info: HashMap<String, (String, Option<String>)>,

    pub keywords_report: Vec<views::keywords::KeywordDemand>,
    pub keywords_show_all: bool,
    pub keywords_corpus_size: usize,
    pub keywords_message: Option<String>,

    /// Attachments for the job whose row is open, and which job they are for.
    /// Loaded per opened row rather than for the whole board: a person has
    /// thousands of jobs and looks at one at a time.
    pub attachments: Vec<crate::attachments::Attachment>,
    pub attachments_for: Option<String>,
    pub attachment_message: Option<String>,
    /// The "add a link" prompt: a modal, like the note prompt, so the open
    /// row can stay a read-only view of what is already there.
    pub link_prompt_open: bool,
    pub link_url: String,
    pub link_label: String,

    pub companies: Vec<Company>,
    /// The handoff collectors this profile has, fetched off the UI thread.
    /// Rebuilt when the profile opens and when config.json is reloaded, because
    /// both change which collectors exist.
    pub handoffs: crate::collectors::Collectors,
    pub new_company_name: String,
    pub log_lines: Vec<String>,
    /// Where in `log_lines` the endpoint test started, so its result can be
    /// shown ON the Agents card. Sending the reader to another tab to find out
    /// whether their address works is the opposite of a test button.
    pub agent_check_from: Option<usize>,

    pub tutorial_active: bool,
    pub tutorial_step: usize,
    /// Screen rects of the sidebar entries, refilled every frame so the
    /// spotlight follows the real control rather than a hard-coded position.
    pub tutorial_anchors: HashMap<&'static str, egui::Rect>,
    pub running_process: Option<(String, RunningProcess)>,
    /// What the last engine run did, kept AFTER it ends.
    ///
    /// An indicator that simply disappears is not an answer - it looks the
    /// same whether the run finished, was killed, or the app lost track of it.
    pub last_run_result: Option<String>,
    /// When the daily schedule was last consulted - see
    /// start_scheduled_refresh.
    last_refresh_check: std::time::Instant,
    /// How long to wait before consulting it again, as the ENGINE last
    /// reported it. None until an engine run has said, which is why the fixed
    /// interval is still the fallback.
    next_refresh_check: Option<std::time::Duration>,
}

/// Everything opened from a profile's home directory: the database
/// connection plus the paths and parsed documents that go with it. Shared
/// by `UnlatchedApp::new` and `switch_profile` so startup and a runtime
/// profile switch can never open a home's files differently from each
/// other.
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
/// UNLATCHED_HOME precedence (SPEC.md). Empty-string is treated as unset,
/// matching paths::resolve_data_dir, so `UNLATCHED_HOME=` in a launcher
/// script does not silently lock the app to "".
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
/// Half an hour, because the schedule it is asking about has slots half an
/// hour apart at their closest, and asking more often only spawns a process
/// to be told "no".
const REFRESH_CHECK_EVERY: std::time::Duration = std::time::Duration::from_secs(30 * 60);

/// The nav rail, in the order it is drawn, as data rather than as three
/// literal arrays inside the draw call.
///
/// IT IS DATA BECAUSE THE WALKTHROUGH ADDRESSES IT BY NAME. `tutorial::STEPS`
/// spotlights a rail entry by its label, and a label that no longer exists
/// spotlights nothing at all - silently, because a missing anchor just draws
/// the plain dim. Renaming "Companies" would have left a step describing a
/// screen with no arrow pointing at anything, and nothing in the build would
/// have said so. With the labels in one place, `tutorial`'s own test can hold
/// the walkthrough against them.
pub const NAV_JOBS: [(View, &str); 5] = [
    (View::Dashboard, "Dashboard"),
    (View::Triage, "Triage"),
    (View::Pipeline, "Pipeline"),
    (View::AllJobs, "All jobs"),
    // BELOW All jobs, because that is what it is a way back from. It was
    // reachable only as a chip in the All jobs toolbar, which is how a live
    // profile came to hold 29 removed rows their owner did not know they could
    // see.
    (View::Removed, "Removed"),
];
pub const NAV_TOOLS: [(View, &str); 4] = [
    (View::Keywords, "Keywords"),
    (View::Resumes, "Resumes"),
    (View::Companies, "Companies"),
    (View::Agent, "Agents"),
];
pub const NAV_SETUP: [(View, &str); 2] = [(View::Config, "Config"), (View::Profiles, "Settings")];
/// Only drawn while it is needed, so a walkthrough step must never anchor on
/// it: on a home that has finished setting up it is not on screen.
pub const NAV_GETTING_STARTED: (View, &str) = (View::GettingStarted, "Getting started");

/// Every rail entry that is ALWAYS drawn, which is the set a walkthrough step
/// may point at.
pub fn nav_entries() -> impl Iterator<Item = (View, &'static str)> {
    NAV_JOBS
        .into_iter()
        .chain(NAV_TOOLS)
        .chain(NAV_SETUP)
}

fn nav_entry(app: &mut UnlatchedApp, ui: &mut egui::Ui, view: View, label: &'static str) {
    let response = crate::theme::nav_row(ui, label, app.view == view);
    // Recorded every frame so the walkthrough spotlights the control where it
    // actually is, at any window size.
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
            // this session; there is no sensible degraded mode to fall
            // back to, so surface the problem immediately rather than
            // limping along with a None connection scattered through
            // every view.
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
            // Replaced below once the profile has been read: a profile with
            // no titles and no employers opens on the guidance instead of an
            // empty triage table.
            view: View::Dashboard,
            dashboard_stats: None,
            // Data at 1, views at 0, so a cache that is never loaded reads as
            // STALE rather than current. The constructor below loads all five
            // eagerly and stamps them, so nothing is actually stale on the
            // first frame - this is the safety net for the case where that
            // eager load is later removed or a sixth cache is added without
            // one. Starting them equal would make an unloaded cache look
            // fresh, which is the wrong direction to fail in.
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
            triage_show_all: false,
            list_scope: ListScope::Triage,
            triage_sort: SortBy::Score,
            triage_sort_desc: true,
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
            last_backup: None,
            focus_added_links_setting: false,
            pending_status: None,
            pipeline_log: Vec::new(),
            pipeline_notes: Vec::new(),
            pipeline_current: HashMap::new(),
            pipeline_job_info: HashMap::new(),
            keywords_report: Vec::new(),
            keywords_show_all: false,
            keywords_corpus_size: 0,
            keywords_message: None,
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
            last_refresh_check: std::time::Instant::now(),
            next_refresh_check: None,
        };
        // Loaded here rather than left to the first frame because the code
        // below decides the landing view from what these contain - an empty
        // triage table routes a first-time user to guidance instead of a
        // dashboard of zeros. sync_caches then finds them current and does not
        // repeat the work, because this stamps the version it loaded at.
        app.refresh_triage();
        app.refresh_pipeline();
        app.refresh_keywords();
        app.refresh_companies();
        // Off the UI thread and started here rather than when the menu opens:
        // starting the frozen engine costs the better part of a second, and a
        // menu that freezes when clicked is worse than one that fills in a
        // moment after the window appears.
        app.refresh_handoffs();
        app.refresh_dashboard();
        app.triage_loaded_at = app.data_version;
        app.pipeline_loaded_at = app.data_version;
        app.keywords_loaded_at = app.data_version;
        app.companies_loaded_at = app.data_version;
        app.dashboard_loaded_at = app.data_version;
        // Decided after the profile is loaded, because it depends on whether
        // that profile has anything set up. A first-time user meeting an
        // empty triage table has no idea what to do next; the guidance says
        // so, and stops appearing once it is no longer true.
        // The dashboard is the landing view (decided 2026-08-05): it summarises
        // and routes, while the sortable list lives in its own tab. First-run
        // guidance still wins over it - a dashboard of zeros teaches nothing
        // to someone who has not set up a search yet.
        // The walkthrough opens itself for somebody who has never seen it. It
        // is the FIRST thing a new user meets, ahead of the static guidance
        // page, because a spotlight on the real control beats a page of prose
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
    /// On open, because that is the only moment this app reliably has: it is
    /// not a service, and a schedule nothing consults is a setting that lies.
    /// The engine decides - the same refresh.py the Config page documents -
    /// so there is one rule, not a Rust copy of it drifting from a Python
    /// one. It also means somebody who was away for four days gets those
    /// four days' postings the moment they come back.
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
    /// otherwise (including the env-locked "(env)" pseudo-profile), per
    /// SPEC.md.
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
    /// that first (see profiles::register_and_activate), so a failed
    /// reopen here never leaves the registry pointing at a profile the app
    /// could not actually load.
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
                // Per profile, so switching searches brings that search's
                // columns with it rather than carrying the last one's over.
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
                // A module opened from the previous profile's dashboard counts
                // rows that do not exist in this one, so the switch lands on
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

    /// Opens one module as its own list.
    ///
    /// Sets the scope and reloads rather than filtering what is already on
    /// screen: the module's WHERE clause is the definition of the list, and it
    /// is the same one that produced the number on the card.
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

    pub fn open_module(&mut self, module: crate::modules::Module) {
        self.list_scope = ListScope::Module(module);
        self.view = View::Triage;
        self.triage_selected = None;
        self.triage_expanded = None;
        self.refresh_triage();
    }

    fn sort_rows(rows: &mut [TriageRow], by: SortBy, desc: bool) {
        // A missing value always sorts LAST regardless of direction. Sorting
        // by salary and getting a screen of blanks at the top is the reader
        // being punished for the employer not stating a figure.
        fn opt_num(v: Option<f64>, desc: bool) -> (u8, f64) {
            match v {
                Some(n) => (0, if desc { -n } else { n }),
                None => (1, 0.0),
            }
        }
        rows.sort_by(|a, b| {
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
                // by "sort by posted" - so the age in days is inverted.
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
        });
    }

    /// Clicking the column already sorted flips direction; a different column
    /// starts descending, which is what a reader wants from every column here
    /// except the two alphabetical ones.
    pub fn sort_by_column(&mut self, column: SortBy) {
        if self.triage_sort == column {
            self.triage_sort_desc = !self.triage_sort_desc;
        } else {
            self.triage_sort = column;
            self.triage_sort_desc = !matches!(column, SortBy::Company | SortBy::Title);
        }
        self.refresh_triage();
    }

    pub fn start_tutorial(&mut self) {
        self.tutorial_active = true;
        self.tutorial_step = 0;
    }

    /// Ends it AND remembers, so it never reappears uninvited. Skipping counts
    /// as seeing it - somebody who skipped does not want it again next launch.
    pub fn end_tutorial(&mut self) {
        self.tutorial_active = false;
        self.settings.tutorial_seen = true;
        self.save_settings();
    }

    /// Records that stored data changed, so every cached view reloads when next
    /// drawn. Cheap: one increment, no queries.
    ///
    /// Call this after ANY write. Missing it is the one way this design can
    /// still fail, which is why it lives in the small number of methods that
    /// actually mutate rather than being sprinkled through the views.
    pub fn mark_data_changed(&mut self) {
        self.data_version = self.data_version.wrapping_add(1);
    }

    /// Reloads whichever caches are stale. Called once per frame from `update`.
    ///
    /// LAZY BY VIEW: only the caches the current screen reads are refreshed, so
    /// a status change on Triage does not rebuild the keyword corpus. The
    /// dashboard is the exception - its counts are the thing most often looked
    /// at right after a change, and building them is a handful of COUNT queries.
    fn sync_caches(&mut self) {
        let version = self.data_version;
        if self.dashboard_loaded_at != version {
            self.refresh_dashboard();
            self.dashboard_loaded_at = version;
        }
        match self.view {
            View::Triage | View::AllJobs => {
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

    pub fn refresh_dashboard(&mut self) {
        match crate::dashboard::load(&self.conn) {
            Ok(stats) => self.dashboard_stats = Some(stats),
            Err(e) => {
                self.dashboard_stats = None;
                self.triage_message = Some(format!("could not build the dashboard: {e}"));
            }
        }
    }

    pub fn refresh_triage(&mut self) {
        // (The most recent collect date was read here to feed apply_filter's
        // "new since the last run" predicate. That lives in SQL now, in
        // Module::NewSinceLastRun, so the query answers it against the whole
        // table rather than against the rows this list happened to load.)
        match db::list_jobs_for(&self.conn, self.list_scope, self.triage_show_all) {
            Ok(rows) => {
                // Fold one-requisition-per-city down to a single row unless
                // the person asked to see every location. Done here rather
                // than in SQL so the survivor is the best-scoring posting of
                // the set, which the query has already ordered for us.
                let mut rows = if self.triage_every_location {
                    rows
                } else {
                    // Collapse BEFORE sorting: the collapse keeps the first
                    // row of each group and the query orders by score, so the
                    // survivor is the best-scoring posting of the set however
                    // the reader then chooses to sort.
                    db::collapse_locations(rows)
                };
                Self::sort_rows(&mut rows, self.triage_sort, self.triage_sort_desc);
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
                let resume_text =
                    views::keywords::load_resume_text(self.config.resume_path.as_deref());
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
        // A different job's files are on screen, so nothing that belongs to
        // the last one may survive the switch.
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
    /// The class defaults to theirs: the first user's rule is that only employer-written
    /// material is held back from an assistant.
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
    /// WHERE IT OPENS IS REMEMBERED PER PROFILE (Decided 2026-08-13: "remember
    /// user profile choice for download location. That way multiple profiles
    /// can keep their files organized if they choose to not use Downloads
    /// folder"). The setting lives in this profile's own
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
    /// A REMEMBERED FOLDER THAT NO LONGER EXISTS IS NOT USED. People move
    /// drives and delete folders; falling back is better than a dialog that
    /// opens somewhere arbitrary because the path it was given is gone.
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
    /// THE EARLIER ROUND IS USUALLY DELISTED, and Triage hides those by design.
    /// Selecting the key in place would then change nothing on screen, which
    /// reads as a dead button rather than as "that row is not in this list" -
    /// so this moves to All jobs, which is the one scope that holds everything,
    /// and says so plainly if the row is not even there.
    pub fn open_job_anywhere(&mut self, key: &str) {
        self.list_scope = ListScope::All;
        self.refresh_triage();
        if self.triage_rows.iter().any(|r| r.job.key == key) {
            self.triage_selected = Some(key.to_string());
            self.triage_expanded = Some(key.to_string());
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
    /// Shares engine_invocation with start_process, so --home is guaranteed by
    /// the same code rather than by this call site remembering it.
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

    pub fn move_selection(&mut self, delta: i32) {
        if self.triage_rows.is_empty() {
            return;
        }
        let current = self.selected_index().unwrap_or(0) as i32;
        let next = (current + delta).clamp(0, self.triage_rows.len() as i32 - 1);
        self.triage_selected = Some(self.triage_rows[next as usize].job.key.clone());
    }

    pub fn set_status_for_selected(&mut self, status: &str) {
        let Some(key) = self.triage_selected.clone() else {
            return;
        };
        self.set_status_for(&key, status);
    }

    /// Opens the note prompt for one named row. The row dropdown uses this
    /// rather than acting on the selection, so changing a status never depends
    /// on which row happens to be highlighted.
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
        self.pending_status = Some(PendingStatus {
            keys,
            status: status.to_string(),
            subject,
            note: String::new(),
            pay: String::new(),
            offer_date: String::new(),
        });
    }

    /// Writes the pending change, with whatever was typed about it.
    pub fn commit_status_change(&mut self) {
        let Some(pending) = self.pending_status.take() else {
            return;
        };
        let note = pending.note.trim();
        // NULL, not "": a status change with nothing written about it must log
        // no note at all, because the empty string is what the pipeline reads
        // as "a note exists" and the dashboard would count as one.
        let note_opt = (!note.is_empty()).then_some(note);
        // Offer terms belong to an Offer. Typed into the boxes and then
        // switched to another status, they are dropped rather than stored
        // against a transition that cannot mean them.
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
        // The readable copy is refreshed on the action that CREATES the history
        // worth keeping, so the backup is never older than the last thing the
        // person did.
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
    /// ONCE PER JOB. Re-applying months later does not overwrite the first
    /// snapshot or add a second: the point is the wording that was applied to.
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
            // Not fatal and not silent: the status change itself succeeded,
            // and a person who never sees this message still has their status.
            self.attachment_message = Some(format!("could not keep a copy of the posting: {e}"));
        }
    }

    /// Drops the pending change without writing it. The status does not move.
    pub fn cancel_status_change(&mut self) {
        self.pending_status = None;
    }

    /// Puts a row back to "not set". Picking that from the dropdown is an
    /// undo, so it removes the status rather than storing an empty one - a
    /// blank status row would still count as a decision in every query that
    /// filters on having one.
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
    /// job's current status in order to carry the note, which meant two things
    /// that were both wrong: a job with no status could not be annotated at all
    /// (job_status.status is NOT NULL, so there was nothing to write), and
    /// every note on a job that DID have one appended a history row saying the
    /// status had changed to the value it already held. A person who wrote
    /// three notes about one application ended up with a timeline claiming
    /// they had marked it Applied four times.
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
        // config.json is where collectors are declared, so a reload is exactly
        // when the menu's list goes out of date.
        self.refresh_handoffs();
    }

    pub fn save_config(&mut self) {
        match self.config_draft.to_config() {
            Ok(cfg) => {
                // Compare BEFORE storing: what the search looks for is the
                // only thing worth re-running over, so a changed timeout or
                // a corrected API key saves quietly.
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

    /// Apply one status to every ticked row.
    ///
    /// Through the same `set_status_for` each row's dropdown uses, one row at
    /// a time, so every one of them gets its own append-only log entry. A
    /// single bulk UPDATE would be faster and would leave the history saying
    /// nothing happened - and that history is what the Applied column, the
    /// funnel and the response rate are all read from.
    /// Write the pipeline out where the person asked, and say where it went.
    ///
    /// Runs the ENGINE rather than reimplementing the writer here. The export
    /// is a data guarantee, and two implementations of a guarantee is one that
    /// can disagree with itself - the CLI's version is the tested one.
    pub fn export_pipeline(&mut self) {
        let target = paths::downloads_dir()
            .map(|dir| paths::non_clobbering_path(&dir, "unlatched-pipeline.csv"))
            .unwrap_or_else(|| self.active_home.join(BACKUP_CSV_NAME));
        self.export_message = match self.run_export(&target) {
            Ok(()) => Some(format!("saved to {}", target.display())),
            // Named, not swallowed. Somebody exporting is usually doing it
            // because they are about to need it.
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
        // Shares engine_invocation with start_process, so --home is guaranteed
        // by the same code (and the same tests) rather than by this call site
        // remembering to do it.
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

    pub fn set_status_for_selection(&mut self, status: &str) {
        // SORTED, so the same nine rows produce the same order every time. The
        // set they come from has no order of its own, and the prompt's heading
        // and the log's timestamps would otherwise shuffle between runs.
        let mut keys: Vec<String> = self.selected_keys.iter().cloned().collect();
        keys.sort();
        let count = keys.len();
        let subject = format!("{count} job{}", if count == 1 { "" } else { "s" });
        self.begin_status_change(keys, status, subject);
    }

    /// Open the retire confirmation for the current selection.
    ///
    /// Measures the applied count HERE, once, so the dialog can say what it
    /// is about to cost instead of asking "are you sure" about a number.
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
        // A removal changes the record as much as a status does - the export
        // carries removed_on precisely so a thrown-away row is still
        // recoverable from the copy.
        self.write_backup_copy();
    }

    /// Undo a grouping for the selected rows.
    ///
    /// The counterpart to Put back, and it exists for the same reason: a
    /// judgement the person cannot reverse is one they have to trust blindly,
    /// and this one decides whether a job they wanted is visible at all.
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

    /// Record that the employer pulled the selected postings.
    ///
    /// NOT ROUTED THROUGH THE RETIRE CONFIRMATION. Removing a job is a
    /// decision that needs a second thought because it hides something the
    /// person still wanted; recording that an advert closed is reporting a
    /// fact, and asking "are you sure the employer did that" would be asking
    /// them to confirm the world.
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
    /// Called on every change from the gear rather than on closing it: there
    /// is no Save button on that panel, so a person who rearranges their
    /// columns and then closes the app has not asked for anything to be lost.
    pub fn save_column_layout(&mut self) {
        self.settings.column_order = views::columns::to_keys(&self.column_order);
        self.settings.column_hidden = views::columns::to_keys(&self.column_hidden);
        self.save_settings();
    }

    /// Starts an engine command. `subcommand_args` is just the verb and its
    /// own flags (e.g. `["collect", "--company", name]`), never the `-m
    /// unlatched` prefix: that prefix, and which program is invoked at
    /// all, depends on `self.engine_mode` and is decided here.
    pub fn start_process(&mut self, label: &str, subcommand_args: Vec<String>) {
        if self.running_process.is_some() {
            self.log_lines.push(format!(
                "[skipped] {label}: another command is still running"
            ));
            return;
        }
        let (program, args) = engine_invocation(
            &self.engine_mode,
            &self.settings.python_invocation,
            &self.active_home,
            subcommand_args,
        );
        self.log_lines
            .push(format!("$ {} {}", program, args.join(" ")));
        match RunningProcess::spawn(&program, &args) {
            Ok(proc) => {
                self.running_process = Some((label.to_string(), proc));
            }
            Err(e) => {
                self.log_lines.push(format!("[error] {e}"));
            }
        }
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
            self.last_run_result = Some(if code == 0 {
                format!("{label} finished")
            } else {
                format!("{label} failed (exit code {code}) - see the log")
            });
            // An engine run can change anything - rows, companies, groupings,
            // delistings. Naming two caches here was how the dashboard stayed
            // stale after every collect.
            self.mark_data_changed();
            self.report_add_job(&label, code);
        }
        // Cap the log so an unbounded run cannot slowly grow the app's
        // memory footprint across a long session.
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
    /// rendered on the Companies page. The first user added a job, watched the modal
    /// close, and got no job and no reason (2026-08-06). A failure nobody is
    /// shown is the same class of defect as a board that silently collects
    /// zero.
    ///
    /// On failure the modal comes BACK, with everything still typed in it, so
    /// the fix is one field away rather than a re-entry.
    fn report_add_job(&mut self, label: &str, code: i32) {
        if label != ADD_JOB_LABEL {
            return;
        }
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

/// The process label for an add. Matched on, so it lives in one place rather
/// than being typed twice.
/// Build the exact command line for an engine subcommand.
///
/// SEPARATE AND PUBLIC so it can be asserted on. The bug it fixes is invisible
/// from outside - the command runs, exits zero, and writes to the wrong
/// database - so the only way to catch it is to look at the arguments.
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

pub const ADD_JOB_LABEL: &str = "add a job";

/// The readable copy kept beside the database. Named so it is obvious what it
/// is to somebody who finds the folder without the app - which is the whole
/// situation it exists for.
pub const BACKUP_CSV_NAME: &str = "pipeline-backup.csv";

/// How long the engine says to wait before asking it about the schedule again.
///
/// `[wake-in] 12345` on a line of its own, in seconds. Parsed rather than
/// computed here because the schedule has exactly one owner - refresh.py - and
/// a Rust reimplementation would be a second one to drift from it.
///
/// SECONDS RATHER THAN A TIMESTAMP, so this can be added to a MONOTONIC clock.
/// A wall-clock time would have to be parsed and would then be wrong across a
/// suspend, a timezone change, or the hour the clocks go back - three separate
/// ways to sleep through a day's postings.
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
        // startup, so switching theme takes effect immediately and a profile
        // switch picks up that profile's own choice.
        if self.applied_dark != Some(self.settings.is_dark()) {
            self.applied_dark = Some(self.settings.is_dark());
            crate::theme::apply(ctx, self.settings.is_dark());
        }
        if !self.window_title_set {
            // Set once here rather than in main.rs's ViewportBuilder: the
            // active profile (and therefore the title) is only known once
            // UnlatchedApp::new has resolved the registry, and there is no
            // egui::Context available yet at that point in main.rs.
            self.window_title_set = true;
            ctx.send_viewport_cmd(egui::ViewportCommand::Title(self.window_title()));
        }

        self.poll_process();
        // BEFORE anything draws. Reloading whichever caches are stale here,
        // rather than at each mutation site, is what makes a missed refresh
        // impossible: a view cannot render from a snapshot older than the data
        // because the check sits between the change and the paint.
        self.sync_caches();
        // The open row's files, and whatever is needed to show the one being
        // looked at. Both are no-ops unless the answer has changed.
        self.sync_attachments();
        if self.running_process.is_some() {
            ctx.request_repaint_after(std::time::Duration::from_millis(100));
        }
        // An app left open all day would otherwise only ever see the schedule
        // once, at launch, and the afternoon slot would never come round.
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
        if self.last_refresh_check.elapsed() >= wait {
            self.last_refresh_check = std::time::Instant::now();
            self.start_scheduled_refresh();
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

                // TWO GROUPS. The daily work is at the top; the things you
                // set up once and rarely touch sit at the bottom, separated by
                // space rather than jumbled in with them. Config sits directly
                // above Settings because they are the same kind of errand.
                // Three groups: the jobs themselves, the tools that act on
                // them, and the setup you touch once. Eight undifferentiated
                // links is a list; this is a structure.
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

        // Drawn last so it covers everything, and it also stops the tutorial's
        // own view switching from fighting a click underneath it.
        crate::tutorial::show(self, ctx);

        // A RUN IS VISIBLE FROM EVERY SCREEN. It used to be visible from one -
        // the log, on the Companies page - so a collect the first user started was, from
        // anywhere else in the app, indistinguishable from nothing happening.
        // A bottom strip rather than a line inside a view: it belongs to the
        // window, not to whatever is being looked at.
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
        // Drawn from update() rather than from the Triage view, because a
        // status can be set from the bulk bar on All jobs and from the row
        // dropdown on either list. A prompt that only existed on one screen
        // would silently swallow the change made from the other.
        views::triage::status_note_window(self, ctx);
        // Rendered outside the central panel so navigating to another view
        // does not strand a prompt the person has not answered yet.
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
            // keeping the current search name, because two people rarely name
            // their hunts the same thing and a stale name would resolve to
            // nothing.
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
    /// "nothing was left out" without a full app instance - which needs a
    /// profile registry, a database and an egui context, none of which exist
    /// in a unit test here.
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
    fn every_cache_is_stamped_after_the_startup_load() {
        // The constructor loads all five eagerly because the landing view is
        // chosen from what they contain. Without stamping, sync_caches would
        // find them stale on the first frame and repeat all five queries.
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
        // The whole defect. Without --home the engine reads UNLATCHED_HOME from
        // the app's environment, which is absent whenever a registry profile is
        // active - so a collect could write into the default profile while the
        // person watched a different one fail to change.
        let bundled = EngineMode::Bundled(PathBuf::from("engine.exe"));
        let args = args_for(&bundled, "D:/people/dana/hr");
        assert_eq!(&args[..2], ["--home", "D:/people/dana/hr"]);
    }

    #[test]
    fn home_comes_before_the_subcommand() {
        // --home is a top-level argument on the engine's parser; argparse will
        // not accept it after the verb, so ordering is load-bearing rather than
        // cosmetic.
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
            UnlatchedApp::sort_rows(&mut rows, SortBy::Salary, desc);
            assert_eq!(rows[0].job.title, "has salary", "desc={desc}");
        }
    }

    #[test]
    fn score_descending_puts_the_best_first() {
        let mut rows = vec![
            row("low", "Acme", Some(60.0), None),
            row("high", "Nimbus", Some(95.0), None),
        ];
        UnlatchedApp::sort_rows(&mut rows, SortBy::Score, true);
        assert_eq!(rows[0].job.title, "high");
        UnlatchedApp::sort_rows(&mut rows, SortBy::Score, false);
        assert_eq!(rows[0].job.title, "low");
    }

    #[test]
    fn company_sorts_alphabetically_ignoring_case() {
        let mut rows = vec![
            row("b", "zeta", Some(1.0), None),
            row("a", "Alpha", Some(1.0), None),
        ];
        UnlatchedApp::sort_rows(&mut rows, SortBy::Company, false);
        assert_eq!(rows[0].company_name.as_deref(), Some("Alpha"));
    }

    #[test]
    fn posted_descending_means_newest_first() {
        let mut rows = vec![
            row("old", "Acme", None, None),
            row("new", "Nimbus", None, None),
        ];
        rows[0].job.posted_at = Some("2020-01-01".to_string());
        rows[1].job.posted_at = Some("2026-08-01".to_string());
        UnlatchedApp::sort_rows(&mut rows, SortBy::Posted, true);
        assert_eq!(rows[0].job.title, "new");
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
    // they covered - that Alt keeps only near misses, that Taken down keeps
    // only delisted rows - is now a WHERE clause, so it is tested against a
    // real database in db::tests::each_module_lists_exactly_what_its_card_counts
    // rather than against a hand-built Vec that could never disagree with the
    // query it was standing in for.)
}
