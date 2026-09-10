// Desktop-only settings: things this front end needs (how to invoke the
// command-line tool) that are not part of the shared config.json contract
// and would have no meaning to a user editing that file by hand.

use serde::{Deserialize, Serialize};
use std::fs;
use std::path::Path;

#[derive(Serialize, Deserialize, Clone, Debug, PartialEq)]
#[serde(default)]
pub struct DesktopSettings {
    // How the CLI is invoked, e.g. "python", "python3", or a full path to
    // an interpreter. Passed as argv[0] of the child process; the "-m
    // unlatched <verb>" arguments are appended by the caller.
    pub python_invocation: String,

    /// "light" or "dark". Stored as a word rather than a bool so a third
    /// theme never has to break the file format, and so someone reading
    /// desktop_settings.json can tell what it means.
    pub theme: String,

    /// Whether the first-run walkthrough has been seen or skipped.
    pub tutorial_seen: bool,

    /// The job list's columns, left to right, by their on-disk keys. Empty
    /// means the default order.
    ///
    /// Here rather than in config.json because it is a preference about this
    /// window, not about the search: the engine has no use for it, and
    /// config.json is a file a person is invited to hand-edit. Being in the
    /// per-home settings file also makes it per PROFILE, which is what was
    /// asked for - somebody watching two searches wants different columns for
    /// a trades search than for an office one.
    pub column_order: Vec<String>,

    /// Columns the person has turned off. Separate from `column_order` so
    /// hiding a column and later showing it again puts it back where it was,
    /// rather than at the end.
    pub column_hidden: Vec<String>,

    /// Where this profile last saved an attachment, or None until it has.
    ///
    /// PER PROFILE, WHICH IS THE POINT (decided 2026-08-13): each profile
    /// remembers where it last saved, so somebody running two searches can keep
    /// their files apart without having to use the Downloads folder.
    /// This file lives inside the profile's own home directory, so two people
    /// on one machine - or one person running two searches - do not share it.
    ///
    /// None means the Downloads folder, which stays the answer until somebody
    /// saves somewhere else. Nothing has to be configured for that to work.
    pub download_dir: Option<String>,

    /// Statuses that are recorded without stopping to ask for a note, by
    /// their on-disk values.
    ///
    /// PER STATUS RATHER THAN ONE SWITCH. A note is worth asking for on an
    /// interview or a declined offer and almost never wanted on Applied, which
    /// is the one set most often - so a single "notes off" would take the
    /// useful prompts with it and then stay off.
    ///
    /// Defaults to Applied. Empty means every status asks; the default is
    /// applied via `default_quiet_statuses` so an existing settings file that
    /// predates this gains the behaviour rather than silently keeping the old
    /// two-keystroke flow.
    #[serde(default = "default_quiet_statuses")]
    pub quiet_statuses: Vec<String>,

    /// The browser job links open in: a path to an executable, or empty for
    /// whichever browser this device already opens web pages with.
    ///
    /// EMPTY IS THE SHIPPED DEFAULT and has to stay that way. A job link is a
    /// web page; the browser a person already chose for web pages is the right
    /// answer on every machine, and naming one here would be right only on the
    /// machine this was written on.
    #[serde(default)]
    pub browser: String,
}

impl Default for DesktopSettings {
    fn default() -> Self {
        DesktopSettings {
            python_invocation: "python".to_string(),
            theme: LIGHT.to_string(),
            tutorial_seen: false,
            column_order: Vec::new(),
            column_hidden: Vec::new(),
            // The same default the serde attribute applies, so a fresh install
            // and an upgraded one behave identically.
            quiet_statuses: default_quiet_statuses(),
            download_dir: None,
            browser: String::new(),
        }
    }
}

/// The status that ships with its note prompt off.
///
/// Applied, because it is the one set most often and the one people least
/// often have anything to say about at the moment they set it.
fn default_quiet_statuses() -> Vec<String> {
    vec!["applied".to_string()]
}

pub const LIGHT: &str = "light";
pub const DARK: &str = "dark";

impl DesktopSettings {
    /// Should setting this status stop to ask for a note?
    pub fn asks_for_note(&self, status: &str) -> bool {
        !self.quiet_statuses.iter().any(|s| s == status)
    }

    pub fn is_dark(&self) -> bool {
        self.theme.eq_ignore_ascii_case(DARK)
    }
}

/// Reads the file, falling back to the defaults when it cannot be read.
///
/// A FILE THAT WILL NOT PARSE IS KEPT, NOT OVERWRITTEN. Falling back is the
/// right call, since a settings file is never worth refusing to start over -
/// but the fallback used to be silent AND destructive: the defaults loaded,
/// and the next save wrote them over the only copy. A half-written file from
/// a power cut, or one hand-edited a comma wrong, took the person's column
/// layout, theme and browser choice with it and left nothing to recover from.
///
/// So the unreadable file is moved aside first. The person still lands on the
/// defaults, but their settings are still on disk beside them.
pub fn load(path: &Path) -> DesktopSettings {
    if !path.exists() {
        return DesktopSettings::default();
    }
    let Ok(text) = fs::read_to_string(path) else {
        return DesktopSettings::default();
    };
    match serde_json::from_str(&text) {
        Ok(settings) => settings,
        Err(_) => {
            // Non-clobbering, so a second bad start does not overwrite the
            // copy kept by the first.
            if let Some(dir) = path.parent() {
                let name = path
                    .file_name()
                    .map(|n| n.to_string_lossy().into_owned())
                    .unwrap_or_else(|| "desktop_settings.json".to_string());
                let kept = crate::paths::non_clobbering_path(
                    dir,
                    &format!("{name}.unreadable"),
                );
                let _ = fs::rename(path, kept);
            }
            DesktopSettings::default()
        }
    }
}

pub fn save(path: &Path, settings: &DesktopSettings) -> Result<(), String> {
    let text = serde_json::to_string_pretty(settings)
        .map_err(|e| format!("could not encode settings: {e}"))?;
    fs::write(path, text).map_err(|e| format!("could not write desktop_settings.json: {e}"))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn dir_for(test: &str) -> std::path::PathBuf {
        let dir = std::env::temp_dir().join(format!("unlatched-settings-{test}"));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).unwrap();
        dir
    }

    #[test]
    fn a_missing_file_is_the_defaults_and_writes_nothing() {
        let dir = dir_for("missing");
        let path = dir.join("desktop_settings.json");
        assert_eq!(load(&path), DesktopSettings::default());
        assert!(!path.exists(), "reading must not create the file");
        let _ = fs::remove_dir_all(&dir);
    }

    /// THE ONE THAT COST SOMETHING. An unreadable file used to load the
    /// defaults silently, and the next save wrote them over the only copy -
    /// so a half-written file took the column layout, the theme and the
    /// browser choice with it and left nothing behind to recover from.
    #[test]
    fn an_unreadable_file_is_kept_rather_than_overwritten() {
        let dir = dir_for("unreadable");
        let path = dir.join("desktop_settings.json");
        fs::write(&path, "{\"theme\": \"dark\", oh dear").unwrap();

        let loaded = load(&path);
        assert_eq!(loaded, DesktopSettings::default(), "falls back rather than failing");
        assert!(!path.exists(), "the bad file is moved out of the way");

        let kept = dir.join("desktop_settings.json.unreadable");
        assert!(kept.exists(), "and it is still on disk");
        assert!(fs::read_to_string(&kept).unwrap().contains("oh dear"));

        // Saving now cannot reach the kept copy.
        save(&path, &loaded).unwrap();
        assert!(kept.exists());
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn a_second_bad_start_does_not_overwrite_the_first_copy() {
        let dir = dir_for("twice");
        let path = dir.join("desktop_settings.json");
        fs::write(&path, "first broken file").unwrap();
        load(&path);
        fs::write(&path, "second broken file").unwrap();
        load(&path);

        let first = fs::read_to_string(dir.join("desktop_settings.json.unreadable")).unwrap();
        let second =
            fs::read_to_string(dir.join("desktop_settings.json (2).unreadable")).unwrap();
        assert_eq!(first, "first broken file");
        assert_eq!(second, "second broken file");
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn a_good_file_round_trips() {
        let dir = dir_for("round-trip");
        let path = dir.join("desktop_settings.json");
        let settings = DesktopSettings {
            theme: DARK.to_string(),
            column_order: vec!["title".to_string(), "company".to_string()],
            browser: "D:/browsers/thing.exe".to_string(),
            ..DesktopSettings::default()
        };
        save(&path, &settings).unwrap();
        assert_eq!(load(&path), settings);
        let _ = fs::remove_dir_all(&dir);
    }

    /// A file written before the note prompts existed has to GAIN the
    /// default rather than come back with every status asking - which is the
    /// old two-keystroke flow the setting exists to end.
    #[test]
    fn a_file_that_predates_a_field_gains_its_default() {
        let dir = dir_for("older");
        let path = dir.join("desktop_settings.json");
        fs::write(&path, r#"{"theme": "dark", "python_invocation": "python3"}"#).unwrap();
        let loaded = load(&path);
        assert!(loaded.is_dark());
        assert_eq!(loaded.python_invocation, "python3");
        assert!(!loaded.asks_for_note("applied"), "applied ships quiet");
        assert!(loaded.asks_for_note("interviewed"));
        assert!(loaded.browser.is_empty(), "no browser is the shipped default");
        let _ = fs::remove_dir_all(&dir);
    }
}
