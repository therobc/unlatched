// config.json model, shared with the command-line side of the app. Every
// field is optional on disk (missing keys fall back to these defaults), so
// `#[serde(default)]` is used throughout instead of requiring a complete
// file.

use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
use std::fs;
use std::path::Path;

/// Employment types a search can accept, mirroring employment.KINDS in the
/// Python engine. A posting of an unaccepted type is flagged "alt", never
/// dropped - the person dismisses it in triage.
pub const EMPLOYMENT_KINDS: &[(&str, &str)] = &[
    ("full_time", "Full time"),
    ("part_time", "Part time"),
    ("contract", "Contract"),
    ("temporary", "Temporary"),
    ("internship", "Internship"),
];

/// ATS collectors and page-scraping fallbacks the search can draw from.
/// This list is not printed anywhere in config.json itself (the file only
/// stores which ones are enabled), so it lives here as the single place
/// the UI and the loader both consult when a name is missing from a saved
/// file and needs a default.
pub const KNOWN_SOURCES: &[&str] = &[
    "greenhouse",
    "lever",
    "ashby",
    "smartrecruiters",
    "workable",
    "recruitee",
    "workday",
    "oracle_hcm",
    "bamboohr",
    "breezy",
    "schema_org",
    "sitemap",
    "usajobs",
    "remoteok",
    "nodesk",
];

#[derive(Serialize, Deserialize, Clone, Debug, PartialEq)]
#[serde(default)]
pub struct SearchConfig {
    pub terms: Vec<String>,
    pub title_include: Vec<String>,
    pub title_exclude: Vec<String>,
    pub seniority: Vec<String>,
    pub employment_types: Vec<String>,
    pub salary_floor: Option<i64>,
    /// Pay under `salary_floor` but at or above this is recorded as an "alt"
    /// match rather than dropped: shown and flagged, kept out of the clean
    /// count. None disables the tier, so anything under the floor drops.
    pub salary_alt_floor: Option<i64>,
    pub currency: String,
    /// Places the person can work from, as "City, ST". Empty means the
    /// question was never asked and every location passes.
    pub locations: Vec<String>,
    /// Keep only postings the person can work from the United States.
    ///
    /// DEFAULTS TRUE, and the default is load-bearing: remoteness says
    /// nothing about jurisdiction, so "Remote - India" passed a
    /// remote-only search untouched until this existed. The engine
    /// records 41 of 133 matches in a real search being foreign before it.
    ///
    /// The struct's Default impl is what supplies that TRUE for a config
    /// that omits the key - serde's container-level default fills missing
    /// fields from it - so this must never be given a field-level default,
    /// which would resolve to `false` and quietly disable the filter for
    /// every existing profile.
    pub us_only: bool,
    /// Accept employers based in one of `locations` who send people out to
    /// job sites. Only consulted when a posting's own location is unclear.
    pub travel_ok: bool,
    /// Which of remote / hybrid / onsite the person will take. Empty means
    /// all three, the same way an empty employment_types means all types.
    pub work_modes: Vec<String>,
    /// Superseded by `work_modes` (decided 2026-08-05), and kept because an
    /// existing config.json may still carry it: the engine reads it only
    /// when work_modes is empty. Nothing in the UI writes it any more.
    pub remote_scope: String,
}

impl Default for SearchConfig {
    fn default() -> Self {
        SearchConfig {
            terms: Vec::new(),
            title_include: Vec::new(),
            title_exclude: Vec::new(),
            seniority: Vec::new(),
            employment_types: Vec::new(),
            salary_floor: None,
            salary_alt_floor: None,
            currency: "USD".to_string(),
            locations: Vec::new(),
            // TRUE, matching the engine's config.py. See the field.
            us_only: true,
            travel_ok: false,
            work_modes: Vec::new(),
            remote_scope: "any".to_string(),
        }
    }
}

/// Whether a link the person adds by hand is read to fill itself in.
///
/// This struct used to model max_bytes, timeout_s, per_host_delay_s and
/// respect_robots. Nothing in the engine read any of them - every collector
/// passes its own values - so they were four controls in config.json that did
/// nothing, and this front end wrote them back on every save, keeping them
/// looking real. `respect_robots` was the worst of the four: whether robots
/// applies is decided per endpoint by what the endpoint IS (a published API is
/// consent; somebody's HTML is not), so a global switch could not have
/// loosened or tightened anything. Removed 2026-08-08.
/// Derived, so the default is literally `false` - which is the shipped value.
/// See the note in the engine's config.py: it ships off because "a user
/// turned that on" is a materially different position from "the author
/// shipped it on", not because the behaviour is indefensible - and the app
/// says so in three places rather than failing quietly.
#[derive(Serialize, Deserialize, Clone, Debug, Default, PartialEq)]
#[serde(default)]
pub struct FetchConfig {
    pub read_added_links: bool,
}

/// Daily refresh, ON by default and switchable off per search.
///
/// Creating or changing a search and pressing Search is the deliberate act;
/// once a search exists, keeping it current is what the person already asked
/// for. See the engine's refresh.py for why the anchor is the DAY and a
/// late-morning hour rather than a timer.
#[derive(Serialize, Deserialize, Clone, Debug, PartialEq)]
#[serde(default)]
pub struct RefreshConfig {
    pub daily: bool,
    /// Times of day to refresh at, "HH:MM".
    ///
    /// This replaced `after_hour` in the engine and the desktop was never
    /// updated, so the Config screen carried a "Not before hour" box that wrote
    /// a key NOTHING READS - the engine has no mention of `after_hour` at all.
    /// Somebody could type an hour, press Save, and change nothing. That is the
    /// same defect this file's fetch block was cleared of once already: a
    /// control that does nothing is worse than no control, because it tells
    /// someone they have addressed a concern they have not.
    pub at: Vec<String>,
    /// Time(s) to run at on Saturday and Sunday.
    ///
    /// ITS OWN FIELD rather than reusing `at`, because the weekend
    /// arrivals it exists to catch are not staged to a business-hours
    /// release - see the engine's refresh.WEEKEND_ANCHORS.
    ///
    /// MODELLED HERE AT ALL because it was not: the engine has honoured
    /// refresh.weekend_at since 2026-08-09 and this struct had no field
    /// for it, so the Config screen showed the weekday times while the
    /// weekend ran at a time nobody could see or change from the app -
    /// which is the exact complaint that made the key configurable in the
    /// first place, left standing on the screen half of it.
    pub weekend_at: Vec<String>,
    pub weekdays_only: bool,
}

impl Default for RefreshConfig {
    fn default() -> Self {
        RefreshConfig {
            daily: true,
            // Postings land roughly 8:00-10:30 a.m., so the morning run catches
            // that batch once it is in; the afternoon one catches roles
            // approved during the day, which would otherwise wait until
            // tomorrow. Two is deliberate - boards change in daily batches, so
            // a third mostly re-fetches.
            // KEEP IN STEP WITH THE ENGINE. The same decision is written
            // twice more over there - config.py's DEFAULTS["refresh"]["at"]
            // and refresh.DEFAULT_ANCHORS - and this copy stayed on 10:45
            // when the other two moved to 11:00 on 2026-08-12, so a profile
            // with no "at" key showed one time and was collected at another.
            // test_refresh_anchor_defaults_agree now compares all three.
            at: vec!["11:00".to_string(), "16:30".to_string()],
            // Weekends get ONE run rather than none. The 1.7%-of-postings
            // weekend figure argues against polling, not against a single
            // catch-up run - see the engine's refresh.py. Later in the day
            // than the weekday slot, and KEPT IN STEP with the engine's
            // refresh.WEEKEND_ANCHORS the same way `at` is.
            weekend_at: vec!["11:30".to_string()],
            weekdays_only: false,
        }
    }
}

#[derive(Serialize, Deserialize, Clone, Debug, Default, PartialEq)]
#[serde(default)]
pub struct AgentApiConfig {
    pub base_url: Option<String>,
    pub api_key: Option<String>,
    pub model: Option<String>,
}

/// USAJOBS requires both: the API key from developer.usajobs.gov, and the
/// email address that key was registered with (USAJOBS sends that email
/// back as the `User-Agent` header on every search request).
#[derive(Serialize, Deserialize, Clone, Debug, Default, PartialEq)]
#[serde(default)]
pub struct UsajobsCredentials {
    pub email: Option<String>,
    pub api_key: Option<String>,
}

/// Per-source credentials, keyed by source name. USAJOBS is the only source
/// that needs one today.
#[derive(Serialize, Deserialize, Clone, Debug, Default, PartialEq)]
#[serde(default)]
pub struct CredentialsConfig {
    pub usajobs: UsajobsCredentials,
}

/// One configured handoff collector, as `config.json` holds it.
///
/// MODELLED SO IT CAN BE EDITED ON A SCREEN. Adding a collector meant opening
/// config.json in a text editor, which is the one setup step in this app that
/// still required one.
///
/// `rest` IS THE IMPORTANT FIELD. `config::save` merges over what is on disk,
/// but arrays are replaced WHOLE - deliberately, because a list here is a
/// complete answer and element-wise merging would make removing one
/// impossible. So a collector entry modelled with only the four fields this
/// screen shows would silently delete `schedule`, `we_may_refetch` and
/// `pushes_closures` the moment anybody pressed Save. Flattening the unknown
/// keys back out keeps them, and `a_collector_keeps_the_fields_this_screen_
/// does_not_model` is the guard.
#[derive(Serialize, Deserialize, Clone, Debug, Default, PartialEq)]
pub struct CollectorEntry {
    /// The namespace: it becomes jobs.source and the prefix on every key the
    /// collector writes, so two senders never overwrite each other.
    pub id: String,
    /// What the menu calls it. Falls back to the id when empty.
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub label: String,
    /// The file this collector leaves for us. Read, never written.
    pub path: String,
    #[serde(default = "default_enabled")]
    pub enabled: bool,
    /// Every other key the engine understands and this screen does not.
    #[serde(flatten)]
    pub rest: serde_json::Map<String, serde_json::Value>,
}

fn default_enabled() -> bool {
    true
}

#[derive(Serialize, Deserialize, Clone, Debug, PartialEq)]
#[serde(default)]
pub struct Config {
    pub search: SearchConfig,
    pub skills: Vec<String>,
    pub resume_path: Option<String>,
    /// Which attached copy screening reads, by file name. Empty means the
    /// automatic rule - see resumes::active_name.
    ///
    /// MODELLED HERE so the desktop resolves the resume the same way the
    /// engine does. It is written by `unlatched resume pin` and honoured
    /// first, and this struct had no field for it: the Resumes tab drew its
    /// "in use" marker from the automatic rule alone, so a pinned copy was
    /// scored against while the screen pointed at a different file. `save`
    /// already merged unmodelled keys through, so the pin was never lost -
    /// it was simply never read on this side.
    pub resume_pinned: String,
    // BTreeMap keeps the on-disk key order stable across saves, which
    // makes diffs between runs readable.
    pub sources: BTreeMap<String, bool>,
    pub fetch: FetchConfig,
    pub agent_api: AgentApiConfig,
    pub refresh: RefreshConfig,
    pub credentials: CredentialsConfig,
    /// Programs that hand rows over in a file. Empty on a profile that has
    /// none, which is most of them.
    #[serde(default)]
    pub collectors: Vec<CollectorEntry>,
}

/// A collector id, normalised, or a sentence saying what is wrong with it.
///
/// THE SAME RULE THE ENGINE APPLIES, deliberately duplicated rather than
/// deferred to. Verified against importer.check_collector_id: 1-32 characters
/// of a-z, 0-9, underscore or hyphen, first character a letter or digit. A bad
/// entry does not raise there - collectors.configured collects it into
/// `problems` and carries on - so it surfaces in the collectors menu, a
/// different screen and a later moment than the one where the id was typed.
/// Checked here, a typo is a message beside the field.
///
/// LOWERCASED, NOT REFUSED, matching that function, which strips and
/// lower-cases before matching and returns the cleaned value; its own note
/// records that everything downstream uses what it returns rather than what it
/// was handed.
pub fn normalise_collector_id(raw: &str) -> Result<String, String> {
    let cleaned = raw.trim().to_ascii_lowercase();
    let usable = (1..=32).contains(&cleaned.chars().count())
        && cleaned
            .chars()
            .all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == '_' || c == '-')
        && cleaned
            .chars()
            .next()
            .is_some_and(|c| c.is_ascii_lowercase() || c.is_ascii_digit());
    if usable {
        Ok(cleaned)
    } else {
        Err(format!(
            "Collector id {raw:?} is not usable: 1-32 characters of a-z, 0-9, \
             underscore or hyphen, starting with a letter or digit."
        ))
    }
}

impl Default for Config {
    fn default() -> Self {
        let mut sources = BTreeMap::new();
        for name in KNOWN_SOURCES {
            sources.insert((*name).to_string(), true);
        }
        Config {
            search: SearchConfig::default(),
            skills: Vec::new(),
            resume_path: None,
            resume_pinned: String::new(),
            sources,
            fetch: FetchConfig::default(),
            agent_api: AgentApiConfig::default(),
            refresh: RefreshConfig::default(),
            credentials: CredentialsConfig::default(),
            collectors: Vec::new(),
        }
    }
}

impl Config {
    /// Any known source missing from a loaded file (an older config, or one
    /// hand-edited to drop a key) is filled in as enabled, matching what a
    /// freshly created config.json would contain.
    pub fn fill_missing_sources(&mut self) {
        for name in KNOWN_SOURCES {
            self.sources.entry((*name).to_string()).or_insert(true);
        }
    }

    /// Every secret field in this config, so the two mappings below cannot
    /// drift apart as fields are added. Mirrors `SECRET_KEYS` in the Python
    /// engine's config.py - both front ends must agree on the set.
    fn secret_fields(&mut self) -> Vec<&mut Option<String>> {
        vec![
            &mut self.credentials.usajobs.api_key,
            &mut self.agent_api.api_key,
        ]
    }

    fn map_secrets(&mut self, f: fn(&str) -> String) {
        for field in self.secret_fields() {
            if let Some(value) = field.as_deref() {
                *field = Some(f(value));
            }
        }
    }

    /// Called after reading config.json: the rest of the app then works with
    /// plain strings and never has to know a value was wrapped on disk.
    pub fn unprotect_secrets(&mut self) {
        self.map_secrets(crate::secrets::unprotect);
    }

    /// Called on a CLONE just before writing config.json.
    pub fn protect_secrets(&mut self) {
        self.map_secrets(crate::secrets::protect);
    }
}

/// Loads config.json, falling back to defaults if the file does not exist
/// yet. A parse error is reported back to the caller (so the UI can show
/// it) rather than silently discarded; the returned config is still usable
/// defaults in that case.
pub fn load(path: &Path) -> (Config, Option<String>) {
    if !path.exists() {
        return (Config::default(), None);
    }
    match fs::read_to_string(path) {
        Ok(text) => match serde_json::from_str::<Config>(&text) {
            Ok(mut cfg) => {
                cfg.fill_missing_sources();
                cfg.unprotect_secrets();
                (cfg, None)
            }
            Err(e) => (
                Config::default(),
                Some(format!("could not parse config.json: {e}")),
            ),
        },
        Err(e) => (
            Config::default(),
            Some(format!("could not read config.json: {e}")),
        ),
    }
}

pub fn save(path: &Path, cfg: &Config) -> Result<(), String> {
    // Protect a CLONE. The caller keeps using the config it passed in, and
    // swapping its live secret for a blob underneath it would leave the
    // running app holding a key it cannot send.
    let mut on_disk = cfg.clone();
    on_disk.protect_secrets();
    let typed = serde_json::to_value(&on_disk)
        .map_err(|e| format!("could not encode config: {e}"))?;

    // Merged over whatever is on disk rather than replacing it. config.json
    // is shared with the engine, which understands keys this front end does
    // not model - profile.education, search.us_only, search.travel_ok - and
    // writing only the modelled keys DELETED them. Silently: the file still
    // looked right, and the search quietly stopped filtering by location and
    // stopped disqualifying roles needing a clearance the person cannot get.
    //
    // Re-read here rather than remembered from load, so a change made by the
    // engine or an assistant between opening this screen and pressing Save
    // survives too.
    let merged = match read_json(path) {
        Some(existing) => merge(existing, typed),
        None => typed,
    };
    let text = serde_json::to_string_pretty(&merged)
        .map_err(|e| format!("could not encode config: {e}"))?;
    fs::write(path, text).map_err(|e| format!("could not write config.json: {e}"))
}

fn read_json(path: &Path) -> Option<serde_json::Value> {
    let text = fs::read_to_string(path).ok()?;
    serde_json::from_str(&text).ok()
}

/// `overlay` wins wherever both name the same key; objects merge key by key,
/// so unknown keys nested anywhere survive.
///
/// Arrays are replaced whole, not appended: a list in this config is the
/// complete answer to a question ("these are my job titles"), and merging
/// element-wise would make removing one impossible.
fn merge(base: serde_json::Value, overlay: serde_json::Value) -> serde_json::Value {
    match (base, overlay) {
        (serde_json::Value::Object(mut base), serde_json::Value::Object(overlay)) => {
            for (key, value) in overlay {
                let merged = match base.remove(&key) {
                    Some(existing) => merge(existing, value),
                    None => value,
                };
                base.insert(key, merged);
            }
            serde_json::Value::Object(base)
        }
        (_, overlay) => overlay,
    }
}

#[cfg(test)]
mod tests {

    /// THE ID RULE, CHECKED AGAINST THE ENGINE'S. Every case here was read off
    /// importer.check_collector_id and verified against it: the colon case is
    /// the one that function names as its reason for existing, because "a
    /// collector that could put a colon in its own id could claim another
    /// collector's namespace". The front end must not accept what the engine
    /// will reject.
    #[test]
    fn a_collector_id_is_checked_the_way_the_engine_checks_it() {
        assert_eq!(normalise_collector_id("partner").unwrap(), "partner");
        assert_eq!(normalise_collector_id("  MyBoard  ").unwrap(), "myboard");
        assert_eq!(normalise_collector_id("board-2_x").unwrap(), "board-2_x");

        for bad in [
            "", "   ", "_leading", "-leading", "has space", "has:colon", "has/slash",
        ] {
            assert!(normalise_collector_id(bad).is_err(), "{bad:?} was accepted");
        }
        assert!(normalise_collector_id(&"a".repeat(33)).is_err());
        assert!(normalise_collector_id(&"a".repeat(32)).is_ok());
    }


    /// SAVING FROM THIS SCREEN MUST NOT EAT THE ENGINE'S FIELDS.
    ///
    /// `merge` replaces arrays whole, so a collector entry round-tripped
    /// through a struct that models four keys would come back as four keys -
    /// and `schedule`, `we_may_refetch` and `pushes_closures` would be gone
    /// from a file the person only meant to rename something in. Silently:
    /// the file still parses and the collector still runs, just on every
    /// refresh instead of at 13:00.
    #[test]
    fn a_collector_keeps_the_fields_this_screen_does_not_model() {
        let raw = serde_json::json!({
            "id": "partner",
            "label": "Partner app",
            "path": "C:/handoff/partner.json",
            "enabled": true,
            "schedule": ["13:00"],
            "we_may_refetch": false,
            "pushes_closures": true
        });
        let entry: CollectorEntry = serde_json::from_value(raw).unwrap();
        assert_eq!(entry.id, "partner");
        assert_eq!(entry.rest.len(), 3, "unmodelled keys were dropped on read");

        let back = serde_json::to_value(&entry).unwrap();
        assert_eq!(back["schedule"], serde_json::json!(["13:00"]));
        assert_eq!(back["we_may_refetch"], serde_json::json!(false));
        assert_eq!(back["pushes_closures"], serde_json::json!(true));
    }

    /// The smallest entry the engine accepts is id plus path - every other
    /// field has a default there, so this side must not demand more than the
    /// contract does.
    #[test]
    fn the_smallest_usable_entry_reads() {
        let entry: CollectorEntry = serde_json::from_value(serde_json::json!({
            "id": "partner", "path": "C:/handoff/partner.json"
        }))
        .unwrap();
        assert!(entry.enabled, "a collector with no enabled key is on");
        assert!(entry.label.is_empty());
    }

    use super::*;

    /// `name` keeps each test in its own folder: they run in parallel, and
    /// sharing one meant the tidy-up at the end of one test deleted the file
    /// another was still reading.
    fn round_trip(name: &str, existing: &str) -> serde_json::Value {
        let dir = std::env::temp_dir().join(format!("unlatched-config-{name}"));
        let _ = fs::create_dir_all(&dir);
        let path = dir.join("config.json");
        fs::write(&path, existing).unwrap();

        let (cfg, err) = load(&path);
        assert!(err.is_none(), "{err:?}");
        save(&path, &cfg).unwrap();
        let written: serde_json::Value =
            serde_json::from_str(&fs::read_to_string(&path).unwrap()).unwrap();
        let _ = fs::remove_dir_all(&dir);
        written
    }

    #[test]
    fn a_config_that_omits_us_only_still_filters_to_the_us() {
        // THE ASYMMETRIC DEFAULT. Every other boolean on SearchConfig is
        // false-by-default and would be unharmed by serde falling back to
        // Default::default(); this one is true, and it hard-drops postings
        // with positive evidence of a foreign location. A field-level
        // #[serde(default)] here - or losing the Default impl's `true` -
        // resolves to false and silently switches the filter off for every
        // config that predates the key, which is most of them.
        let cfg: Config = serde_json::from_str(r#"{"search": {"terms": ["hr"]}}"#).unwrap();
        assert!(
            cfg.search.us_only,
            "a config with no us_only key must still filter to the US"
        );
        // And the ordinary false default is genuinely false, so this test is
        // not just asserting that booleans exist.
        assert!(!cfg.search.travel_ok);
    }

    #[test]
    fn the_search_scope_ticks_survive_a_round_trip() {
        // Both were engine-only until the Config screen modelled them, so the
        // thing worth proving is that a value set here reaches the file.
        let written = round_trip(
            "scope-ticks",
            r#"{"search": {"us_only": false, "travel_ok": true}}"#,
        );
        assert_eq!(written["search"]["us_only"], serde_json::json!(false));
        assert_eq!(written["search"]["travel_ok"], serde_json::json!(true));
    }

    #[test]
    fn saving_keeps_keys_this_front_end_does_not_model() {
        // Every one of these is read by the engine and was previously wiped
        // by pressing Save on the Config page.
        let written = round_trip(
            "unmodelled-keys",
            r#"{
                "search": {"terms": ["analyst"], "us_only": true, "travel_ok": true},
                "profile": {"education": "bachelors", "clearance_ok": false}
            }"#,
        );
        assert_eq!(written["search"]["us_only"], serde_json::json!(true));
        assert_eq!(written["search"]["travel_ok"], serde_json::json!(true));
        assert_eq!(written["profile"]["education"], serde_json::json!("bachelors"));
        assert_eq!(written["profile"]["clearance_ok"], serde_json::json!(false));
        // And still writes what it does model.
        assert_eq!(written["search"]["terms"], serde_json::json!(["analyst"]));
    }

    #[test]
    fn a_modelled_list_is_replaced_not_appended() {
        let written = round_trip("list-replaced", r#"{"search": {"terms": ["analyst", "support"]}}"#);
        // Loaded then written back unchanged - the point is that it is not
        // ["analyst", "support", "analyst", "support"].
        assert_eq!(written["search"]["terms"], serde_json::json!(["analyst", "support"]));
    }

    #[test]
    fn an_unparseable_file_is_not_merged_into() {
        let dir = std::env::temp_dir().join("unlatched-config-merge-bad");
        let _ = fs::create_dir_all(&dir);
        let path = dir.join("config.json");
        fs::write(&path, "{not json").unwrap();
        // load() reports the problem and hands back defaults; saving those
        // must not choke on the broken text still sitting there.
        let (cfg, err) = load(&path);
        assert!(err.is_some());
        save(&path, &cfg).unwrap();
        let written: serde_json::Value =
            serde_json::from_str(&fs::read_to_string(&path).unwrap()).unwrap();
        assert_eq!(written["search"]["currency"], serde_json::json!("USD"));
        let _ = fs::remove_dir_all(&dir);
    }
}
