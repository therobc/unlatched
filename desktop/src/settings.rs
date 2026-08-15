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
    /// PER PROFILE, WHICH IS THE POINT (decided 2026-08-13): "remember user
    /// profile choice for download location. That way multiple profiles can
    /// keep their files organized if they choose to not use Downloads folder."
    /// This file lives inside the profile's own home directory, so two people
    /// on one machine - or one person running two searches - do not share it.
    ///
    /// None means the Downloads folder, which stays the answer until somebody
    /// saves somewhere else. Nothing has to be configured for that to work.
    pub download_dir: Option<String>,
}

impl Default for DesktopSettings {
    fn default() -> Self {
        DesktopSettings {
            python_invocation: "python".to_string(),
            theme: LIGHT.to_string(),
            tutorial_seen: false,
            column_order: Vec::new(),
            column_hidden: Vec::new(),
            download_dir: None,
        }
    }
}

pub const LIGHT: &str = "light";
pub const DARK: &str = "dark";

impl DesktopSettings {
    pub fn is_dark(&self) -> bool {
        self.theme.eq_ignore_ascii_case(DARK)
    }

}

pub fn load(path: &Path) -> DesktopSettings {
    if !path.exists() {
        return DesktopSettings::default();
    }
    fs::read_to_string(path)
        .ok()
        .and_then(|text| serde_json::from_str(&text).ok())
        .unwrap_or_default()
}

pub fn save(path: &Path, settings: &DesktopSettings) -> Result<(), String> {
    let text = serde_json::to_string_pretty(settings)
        .map_err(|e| format!("could not encode settings: {e}"))?;
    fs::write(path, text).map_err(|e| format!("could not write desktop_settings.json: {e}"))
}
