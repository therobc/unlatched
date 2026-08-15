// profiles.json: the desktop app's registry of known searches.
//
// TWO LEVELS, NOT ONE. A PERSON is a job seeker: they own their
// identity and the resume they brought in. A SEARCH is one named hunt that
// person is running - its own criteria, its own database, its own pipeline.
//
//     Documents/Unlatched/<Person>/<Search>/{config.json, unlatched.db}
//
// The one-level version equated a person with a single search, so anyone
// wanting two angles on the same candidate had to register two profiles named
// "Maya Ellison (remote)" and "Maya Ellison (local)" - which made the dropdown
// lie about what a profile was, and duplicated the resume.
//
// A search is still nothing more than a home directory that the CLI already
// supports via --home / UNLATCHED_HOME. This file only adds the bookkeeping,
// so the engine contract does not change at all.
//
// The registry always lives in the platform-default home
// (paths::platform_default_home), never in whatever UNLATCHED_HOME happens to
// point at - see the comment on that function for why.

use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
use std::fs;
use std::path::{Path, PathBuf};

use crate::config::{self, Config};
use crate::db;
use crate::paths;

pub const DEFAULT_PROFILE: &str = "Default";
/// Every person has at least one search, and a migrated one-level profile
/// becomes exactly this.
pub const DEFAULT_SEARCH: &str = "Default";
// Shown in the switcher when UNLATCHED_HOME is set at startup. Reserved:
// never a name a user can register under (see new_profile.rs).
pub const ENV_PROFILE: &str = "(env)";
pub const REGISTRY_VERSION: u32 = 2;

/// Which person and search the app is looking at.
#[derive(Serialize, Deserialize, Clone, Debug, PartialEq, Eq)]
pub struct Active {
    pub person: String,
    pub search: String,
}

impl Default for Active {
    fn default() -> Self {
        Active {
            person: DEFAULT_PROFILE.to_string(),
            search: DEFAULT_SEARCH.to_string(),
        }
    }
}

#[derive(Serialize, Deserialize, Clone, Debug, PartialEq, Eq, Default)]
#[serde(default)]
pub struct Person {
    /// The resume this person brought in. Each search gets its own COPY, never
    /// a reference: a seeker must not be able to lose their original by running
    /// an optimisation against one search.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub resume_path: Option<String>,
    /// Search name -> home directory.
    pub searches: BTreeMap<String, String>,
}

#[derive(Serialize, Deserialize, Clone, Debug, PartialEq, Eq)]
#[serde(default)]
pub struct Registry {
    pub version: u32,
    pub active: Active,
    pub people: BTreeMap<String, Person>,
}

impl Default for Registry {
    fn default() -> Self {
        Registry {
            version: REGISTRY_VERSION,
            active: Active::default(),
            people: BTreeMap::new(),
        }
    }
}

#[cfg(not(test))]
pub fn registry_path() -> PathBuf {
    paths::platform_default_home().join("profiles.json")
}

/// Under test, the registry is a scratch file and never the real one.
///
/// This is a hard guard, not tidiness. `remember_resume` used to save() as a
/// side effect of mutating, so a unit test that called it replaced a live
/// registry of five seekers with its own two-line fixture - silently, and with
/// nothing in the test suggesting it touched the filesystem at all. Save
/// discipline in each test is not enough, because the test that breaks the rule
/// is the one nobody realised was writing.
#[cfg(test)]
pub fn registry_path() -> PathBuf {
    std::env::temp_dir().join("unlatched-test-profiles.json")
}

/// The default root for NEW people: Documents/Unlatched.
///
/// Not AppData, which works but is hidden - wrong for files a placement
/// specialist needs to back up, copy to a new machine, or hand to a colleague.
/// Never Program Files, which is not writable without administrator rights and
/// is shared across accounts.
///
/// Existing setups are never moved: this only decides where a new person's
/// folder is offered.
pub fn default_people_root() -> PathBuf {
    let base = std::env::var("USERPROFILE")
        .or_else(|_| std::env::var("HOME"))
        .ok()
        .map(PathBuf::from);
    match base {
        Some(dir) if dir.join("Documents").is_dir() => dir.join("Documents").join("Unlatched"),
        Some(dir) => dir.join("Unlatched"),
        None => paths::platform_default_home().join("People"),
    }
}

/// Missing file or unparseable JSON both fall back to a registry with only the
/// implicit Default person: there is nothing to recover partially from a
/// corrupt file, and a fresh install has no file at all yet.
pub fn load() -> Registry {
    let path = registry_path();
    if !path.exists() {
        return Registry::default();
    }
    match fs::read_to_string(&path) {
        Ok(text) => parse(&text),
        Err(_) => Registry::default(),
    }
}

/// Parse either registry format, lifting v1 into v2.
///
/// Split out from `load` so the migration is testable without touching the
/// filesystem - the migration is the part that would silently lose somebody's
/// profiles, so it is the part that has to be provable.
pub fn parse(text: &str) -> Registry {
    let value: serde_json::Value = match serde_json::from_str(text) {
        Ok(value) => value,
        Err(_) => return Registry::default(),
    };
    // v1 has a "profiles" map and a string "active"; v2 has "people".
    if value.get("people").is_none() && value.get("profiles").is_some() {
        return migrate_v1(&value);
    }
    serde_json::from_value(value).unwrap_or_default()
}

/// v1 -> v2. Each flat profile becomes a person of the same name owning one
/// search called "Default", pointing at the SAME folder.
///
/// NOTHING ON DISK MOVES. The folder a person's data is already in stays
/// exactly where it is and is only referenced from a new place in the registry,
/// which is what makes this migration safe to run unattended.
fn migrate_v1(value: &serde_json::Value) -> Registry {
    let mut people: BTreeMap<String, Person> = BTreeMap::new();
    if let Some(map) = value.get("profiles").and_then(|p| p.as_object()) {
        for (name, home) in map {
            let Some(home) = home.as_str() else { continue };
            let mut searches = BTreeMap::new();
            searches.insert(DEFAULT_SEARCH.to_string(), home.to_string());
            people.insert(
                name.clone(),
                Person {
                    resume_path: None,
                    searches,
                },
            );
        }
    }
    let active_person = value
        .get("active")
        .and_then(|a| a.as_str())
        .unwrap_or(DEFAULT_PROFILE)
        .to_string();
    Registry {
        version: REGISTRY_VERSION,
        active: Active {
            person: active_person,
            search: DEFAULT_SEARCH.to_string(),
        },
        people,
    }
}

pub fn save(reg: &Registry) -> Result<(), String> {
    let path = registry_path();
    let text = serde_json::to_string_pretty(reg)
        .map_err(|e| format!("could not encode profiles.json: {e}"))?;
    fs::write(&path, text).map_err(|e| format!("could not write profiles.json: {e}"))
}

/// Resolves a person and search to a home directory.
///
/// Falls back to the platform-default home for the implicit Default profile and
/// for anything missing from the map, so the app always has somewhere valid to
/// open rather than needing a separate error path here. `preflight` is what
/// reports a registry that has gone wrong; this one keeps the app running.
pub fn home_for(reg: &Registry, person: &str, search: &str) -> PathBuf {
    reg.people
        .get(person)
        .and_then(|p| p.searches.get(search))
        .map(PathBuf::from)
        .unwrap_or_else(paths::platform_default_home)
}

/// Where a new search's folder is offered, given who it is for and what it is
/// called: `<root>/<Person>/<Search>`.
///
/// Offered, never imposed - the field stays editable, and an existing setup is
/// never moved.
pub fn suggested_home(person: &str, search: &str) -> PathBuf {
    default_people_root().join(sanitise(person)).join(sanitise(search))
}

/// Strip what a folder name cannot contain, so a person's name can be typed
/// naturally and still become a directory.
fn sanitise(name: &str) -> String {
    let cleaned: String = name
        .chars()
        .map(|c| if r#"\/:*?"<>|"#.contains(c) { '-' } else { c })
        .collect();
    let trimmed = cleaned.trim().trim_matches('.').trim();
    if trimmed.is_empty() {
        "Unnamed".to_string()
    } else {
        trimmed.to_string()
    }
}

/// Every search a person owns, in a stable order for a picker.
pub fn searches_for(reg: &Registry, person: &str) -> Vec<String> {
    reg.people
        .get(person)
        .map(|p| p.searches.keys().cloned().collect())
        .unwrap_or_default()
}

pub fn people(reg: &Registry) -> Vec<String> {
    reg.people.keys().cloned().collect()
}

/// How a person-and-search pair is written wherever one is shown.
///
/// A person whose only search is the migrated "Default" is displayed as just
/// their name: every one-level profile becomes that on migration, so spelling
/// out "Dana Whitfield / Default" would make the upgrade look like it had
/// renamed everybody.
pub fn label(person: &str, search: &str) -> String {
    if search == DEFAULT_SEARCH || search.is_empty() {
        person.to_string()
    } else {
        format!("{person} / {search}")
    }
}

/// Registers person/search -> home, makes it active, and persists.
///
/// Overwrites an existing entry of the same pair: re-running "New search" with
/// a name already in use is treated as "point it here now" rather than
/// rejected, since the uniqueness check callers run first already covers the
/// by-accident case.
pub fn register_and_activate(
    reg: &mut Registry,
    person: &str,
    search: &str,
    home: &Path,
) -> Result<(), String> {
    let entry = reg.people.entry(person.to_string()).or_default();
    entry
        .searches
        .insert(search.to_string(), home.to_string_lossy().into_owned());
    reg.active = Active {
        person: person.to_string(),
        search: search.to_string(),
    };
    save(reg)
}

/// Removes a search from the registry only; the folder and everything in it are
/// left untouched (per SPEC.md: deleting from the UI only unregisters).
///
/// Removing a person's last search removes the person too - an entry with no
/// searches is not a person anyone can select, it is a stranded name in a
/// dropdown.
pub fn unregister_search(reg: &mut Registry, person: &str, search: &str) {
    if let Some(entry) = reg.people.get_mut(person) {
        entry.searches.remove(search);
        if entry.searches.is_empty() {
            reg.people.remove(person);
        }
    }
    if reg.active.person == person && reg.active.search == search {
        reg.active = Active::default();
    }
}

/// The resume this person brought in, if the registry knows one.
pub fn resume_for(reg: &Registry, person: &str) -> Option<String> {
    reg.people.get(person).and_then(|p| p.resume_path.clone())
}

/// Record the person's resume so their next search inherits it without asking.
///
/// Mutates ONLY. Persisting is the caller's decision, and it has to be: this
/// used to call save() itself, which meant every caller wrote to the real
/// profiles.json in the platform-default home - including a unit test, which
/// silently replaced a live registry of five seekers with its own fixture.
/// Nothing in this module writes to disk unless a caller asks it to.
///
/// The first resume recorded wins. A later search must not silently repoint the
/// original the person brought in.
pub fn remember_resume(reg: &mut Registry, person: &str, resume: Option<&str>) {
    // or_default so this works for a person who does not exist yet - it is
    // called before register_and_activate precisely so that one save persists
    // both, and a get_mut would silently do nothing for every new person.
    let entry = reg.people.entry(person.to_string()).or_default();
    if entry.resume_path.is_none() {
        entry.resume_path = resume.map(str::to_string);
    }
}

/// Seed a brand-new search from another search the same person already runs.
///
/// Returns how many employers were carried across, or None when this is the
/// person's first search and there is nothing to carry.
///
/// Collection still runs fresh: postings go stale, employer resolution does
/// not. See db::seed_companies_from for why the failures come too.
pub fn seed_from_sibling(reg: &Registry, person: &str, new_home: &Path) -> Option<usize> {
    let sibling = reg
        .people
        .get(person)?
        .searches
        .values()
        .map(PathBuf::from)
        .find(|home| home != new_home && paths::db_path(home).exists())?;

    let conn = db::open(&paths::db_path(new_home)).ok()?;
    db::seed_companies_from(&conn, &paths::db_path(&sibling)).ok()
}

/// Something wrong with a registered search, stated plainly enough to act on.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Problem {
    pub person: String,
    pub search: String,
    pub detail: String,
}

/// Check every registered search can actually be opened.
///
/// This exists because both failure modes here have already happened silently
/// and cost real time: two seekers had complete configs, resumes and employer
/// lists on disk but were absent from profiles.json, so they could not be
/// selected at all; and one had no database, which a refresh script stepped
/// straight over with "no database, skipping". Neither said anything a person
/// would notice. A registry problem should be loud.
pub fn preflight(reg: &Registry) -> Vec<Problem> {
    let mut problems = Vec::new();
    for (person, entry) in &reg.people {
        if entry.searches.is_empty() {
            problems.push(Problem {
                person: person.clone(),
                search: String::new(),
                detail: "registered with no searches, so it cannot be opened".to_string(),
            });
        }
        for (search, home) in &entry.searches {
            let home = PathBuf::from(home);
            let detail = if !home.is_dir() {
                Some(format!("folder is missing: {}", home.display()))
            } else if !paths::db_path(&home).exists() {
                Some(format!(
                    "no database in {} - it was never opened, or it was moved",
                    home.display()
                ))
            } else {
                None
            };
            if let Some(detail) = detail {
                problems.push(Problem {
                    person: person.clone(),
                    search: search.clone(),
                    detail,
                });
            }
        }
    }
    problems
}

/// Prepares a search home: creates the directory, ensures a config.json exists,
/// and opens the database once so its schema exists.
///
/// An EXISTING config.json is never replaced. Pointing a new search at a folder
/// that already holds someone's work would otherwise reset their settings to
/// defaults, which is data loss for the exact workflow this feature serves.
pub fn create_profile_home(home: &Path, resume_path: Option<&str>) -> Result<(), String> {
    fs::create_dir_all(home).map_err(|e| format!("could not create {}: {e}", home.display()))?;

    let config_path = paths::config_path(home);
    let (mut cfg, existing) = if config_path.exists() {
        (config::load(&config_path).0, true)
    } else {
        (Config::default(), false)
    };
    if let Some(resume) = resume_path {
        cfg.resume_path = Some(resume.to_string());
    }
    if !existing || resume_path.is_some() {
        config::save(&config_path, &cfg)?;
    }

    let db_path = paths::db_path(home);
    db::open(&db_path)
        .map_err(|e| format!("could not create database at {}: {e}", db_path.display()))?;

    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    const V1: &str = r#"{
        "active": "sample",
        "profiles": {
            "Dana Whitfield": "D:/seekers/hr",
            "Darius Webb": "D:/seekers/logistics",
            "sample": "C:/example"
        }
    }"#;

    #[test]
    fn a_v1_registry_becomes_three_people_each_with_one_search() {
        let reg = parse(V1);

        assert_eq!(reg.version, REGISTRY_VERSION);
        assert_eq!(people(&reg), ["Dana Whitfield", "Darius Webb", "sample"]);
        for person in people(&reg) {
            assert_eq!(
                searches_for(&reg, &person),
                [DEFAULT_SEARCH],
                "{person} should own exactly one search"
            );
        }
    }

    #[test]
    fn migration_points_at_the_same_folders_it_found() {
        // The half that matters: a migration that renamed or relocated a folder
        // would strand a person's entire history.
        let reg = parse(V1);
        assert_eq!(
            home_for(&reg, "Dana Whitfield", DEFAULT_SEARCH),
            PathBuf::from("D:/seekers/hr")
        );
        assert_eq!(
            home_for(&reg, "sample", DEFAULT_SEARCH),
            PathBuf::from("C:/example")
        );
    }

    #[test]
    fn the_active_profile_survives_migration() {
        let reg = parse(V1);
        assert_eq!(reg.active.person, "sample");
        assert_eq!(reg.active.search, DEFAULT_SEARCH);
    }

    #[test]
    fn a_v2_registry_round_trips_unchanged() {
        let mut reg = Registry::default();
        let mut searches = BTreeMap::new();
        searches.insert("HR Generalist".to_string(), "D:/people/dana/hr".to_string());
        searches.insert("Administrative".to_string(), "D:/people/dana/admin".to_string());
        reg.people.insert(
            "Dana Whitfield".to_string(),
            Person {
                resume_path: Some("D:/people/dana/resume.docx".to_string()),
                searches,
            },
        );
        reg.active = Active {
            person: "Dana Whitfield".to_string(),
            search: "Administrative".to_string(),
        };

        let text = serde_json::to_string(&reg).unwrap();
        assert_eq!(parse(&text), reg);
    }

    #[test]
    fn one_person_can_own_several_searches_without_renaming_themselves() {
        // The whole point of the two-level model: two angles on one candidate,
        // one person, one resume.
        let mut reg = Registry::default();
        let entry = reg.people.entry("Maya Ellison".to_string()).or_default();
        entry.searches.insert("Remote".to_string(), "D:/m/remote".to_string());
        entry.searches.insert("Local".to_string(), "D:/m/local".to_string());

        assert_eq!(searches_for(&reg, "Maya Ellison"), ["Local", "Remote"]);
        assert_eq!(people(&reg), ["Maya Ellison"]);
    }

    #[test]
    fn removing_the_last_search_removes_the_person() {
        let mut reg = parse(V1);
        unregister_search(&mut reg, "Darius Webb", DEFAULT_SEARCH);
        assert!(
            !reg.people.contains_key("Darius Webb"),
            "a person with no searches is a stranded name in a dropdown"
        );
        // The others are untouched.
        assert_eq!(people(&reg), ["Dana Whitfield", "sample"]);
    }

    #[test]
    fn removing_the_active_search_falls_back_to_something_openable() {
        let mut reg = parse(V1);
        unregister_search(&mut reg, "sample", DEFAULT_SEARCH);
        assert_eq!(reg.active, Active::default());
    }

    #[test]
    fn a_suggested_folder_is_person_then_search_under_one_root() {
        let home = suggested_home("Dana Whitfield", "HR Generalist");
        let parts: Vec<_> = home
            .components()
            .rev()
            .take(3)
            .map(|c| c.as_os_str().to_string_lossy().into_owned())
            .collect();
        assert_eq!(parts, ["HR Generalist", "Dana Whitfield", "Unlatched"]);
    }

    #[test]
    fn a_name_that_cannot_be_a_folder_still_becomes_one() {
        // Typed names are person names, not identifiers. A colon or a slash
        // would fail directory creation with an error naming neither.
        let home = suggested_home("Ray Kessler, Jr.", "Trades / Maintenance");
        let parts: Vec<_> = home
            .components()
            .rev()
            .take(2)
            .map(|c| c.as_os_str().to_string_lossy().into_owned())
            .collect();
        assert_eq!(parts, ["Trades - Maintenance", "Ray Kessler, Jr"]);
    }

    #[test]
    fn a_blank_name_never_produces_a_folder_with_no_name() {
        let home = suggested_home("   ", "  ");
        let parts: Vec<_> = home
            .components()
            .rev()
            .take(2)
            .map(|c| c.as_os_str().to_string_lossy().into_owned())
            .collect();
        assert_eq!(parts, ["Unnamed", "Unnamed"]);
    }

    #[test]
    fn a_persons_resume_is_remembered_once_and_inherited_after() {
        // The second search must not ask somebody to re-answer a question
        // about themselves.
        let mut reg = Registry::default();
        reg.people.entry("Maya Ellison".to_string()).or_default();
        assert_eq!(resume_for(&reg, "Maya Ellison"), None);

        remember_resume(&mut reg, "Maya Ellison", Some("D:/m/resume.docx"));
        assert_eq!(
            resume_for(&reg, "Maya Ellison"),
            Some("D:/m/resume.docx".to_string())
        );

        // The first one wins: a later search must not silently repoint the
        // person's original resume.
        remember_resume(&mut reg, "Maya Ellison", Some("D:/m/other.docx"));
        assert_eq!(
            resume_for(&reg, "Maya Ellison"),
            Some("D:/m/resume.docx".to_string())
        );
    }

    #[test]
    fn a_migrated_person_is_labelled_by_name_alone() {
        // Every one-level profile becomes "<name> / Default" internally.
        // Showing that would make the upgrade look like it renamed everybody.
        assert_eq!(label("Dana Whitfield", DEFAULT_SEARCH), "Dana Whitfield");
        assert_eq!(
            label("Dana Whitfield", "Administrative"),
            "Dana Whitfield / Administrative"
        );
    }

    #[test]
    fn garbage_and_empty_files_land_on_the_default_registry() {
        assert_eq!(parse("not json at all"), Registry::default());
        assert_eq!(parse("{}"), Registry::default());
    }

    #[test]
    fn preflight_names_a_search_whose_folder_is_gone() {
        let reg = parse(V1);
        let problems = preflight(&reg);
        // Every path in V1 is fictional, so all three are reported - the point
        // is that they are reported at all rather than skipped in silence.
        assert_eq!(problems.len(), 3);
        assert!(problems.iter().all(|p| p.detail.contains("folder is missing")));
        assert!(problems.iter().any(|p| p.person == "Dana Whitfield"));
    }

    #[test]
    fn preflight_is_quiet_when_a_search_is_openable() {
        let dir = std::env::temp_dir().join("unlatched-preflight-ok");
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).unwrap();
        fs::write(paths::db_path(&dir), b"").unwrap();

        let mut reg = Registry::default();
        let entry = reg.people.entry("Ray Kessler".to_string()).or_default();
        entry
            .searches
            .insert(DEFAULT_SEARCH.to_string(), dir.to_string_lossy().into_owned());

        assert_eq!(preflight(&reg), Vec::new());
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn preflight_catches_the_failure_that_was_skipped_in_silence() {
        // Ray Kessler existed on disk with a complete config and employer list
        // but no database, and a refresh script stepped over him with
        // "no database, skipping". That is the case this assertion exists for.
        let dir = std::env::temp_dir().join("unlatched-preflight-nodb");
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).unwrap();

        let mut reg = Registry::default();
        let entry = reg.people.entry("Ray Kessler".to_string()).or_default();
        entry
            .searches
            .insert(DEFAULT_SEARCH.to_string(), dir.to_string_lossy().into_owned());

        let problems = preflight(&reg);
        assert_eq!(problems.len(), 1);
        assert!(problems[0].detail.contains("no database"));
        let _ = fs::remove_dir_all(&dir);
    }
}
