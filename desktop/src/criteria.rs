//! Moving what a person is looking for between this app and another tool.
//!
//! The interchange itself lives in the engine (`unlatched criteria`). What is
//! here is the part a person can reach: writing the file somewhere they will
//! find it, and reading one back with a preview in front of it.
//!
//! WHY A PREVIEW AND NOT A CONFIRM. A criteria file is somebody else's idea of
//! the search, and "are you sure" about an unnamed change is a question nobody
//! can answer. What a person needs before deciding is which of their own
//! settings is about to move, and by how much - which is what
//! `criteria --import --dry-run --json` reports, with the import by
//! construction not yet applied.

use serde::Deserialize;

/// One key an import would change, as the engine reports it.
#[derive(Deserialize, Clone, Debug, Default, PartialEq)]
#[serde(default)]
pub struct Change {
    pub block: String,
    pub key: String,
    pub was: serde_json::Value,
    pub becomes: serde_json::Value,
    /// For a list: entries arriving that were not here.
    pub added: usize,
    /// For a list: entries here that would be gone.
    pub removed: usize,
}

impl Change {
    /// Where this sits, as a person reads it: "search.salary_floor".
    pub fn where_it_is(&self) -> String {
        if self.key.is_empty() {
            self.block.clone()
        } else {
            format!("{}.{}", self.block, self.key)
        }
    }

    /// What would happen to it, in a sentence.
    ///
    /// COUNTS FOR A LIST, VALUES FOR ANYTHING ELSE. A terms list runs to
    /// however many titles somebody searches for, and printing two of them as
    /// "was [...] becomes [...]" fills the dialog with text nobody reads. How
    /// many arrive and how many go are the two facts that decide between merge
    /// and replace.
    pub fn what_happens(&self) -> String {
        if self.was.is_array() || self.becomes.is_array() {
            let arriving = self.added;
            let leaving = self.removed;
            return match (arriving, leaving) {
                (0, 0) => "reordered".to_string(),
                (a, 0) => format!("{a} added"),
                (0, r) => format!("{r} removed"),
                (a, r) => format!("{a} added, {r} removed"),
            };
        }
        format!("{} to {}", render(&self.was), render(&self.becomes))
    }
}

/// A JSON value as a person would say it, not as JSON prints it.
fn render(value: &serde_json::Value) -> String {
    match value {
        // "null" is what serde prints and "not set" is what it means. The
        // difference matters here: a salary floor moving from unset to a
        // number is the change somebody most wants to catch.
        serde_json::Value::Null => "not set".to_string(),
        serde_json::Value::String(s) if s.is_empty() => "empty".to_string(),
        serde_json::Value::String(s) => s.clone(),
        other => other.to_string(),
    }
}

/// What `criteria --import --dry-run --json` answers.
#[derive(Deserialize, Clone, Debug, Default)]
#[serde(default)]
pub struct Report {
    /// Which blocks would change: search, skills, profile.
    pub changed: Vec<String>,
    /// False for a dry run, and false for a file that changes nothing.
    pub applied: bool,
    pub mode: String,
    pub preview: Vec<Change>,
}

impl Report {
    pub fn is_empty(&self) -> bool {
        self.changed.is_empty()
    }
}

/// Run the engine and read its answer, or the reason it could not.
///
/// THE ENGINE'S OWN WORDS ARE PASSED THROUGH. Verified in the engine's
/// criteria.read: a file is refused with a sentence naming the reason - wrong
/// format, a version newer than the build understands, or none of the three
/// blocks. Rewording those here would mean a second set of sentences to keep
/// in step with the first.
pub fn run(program: &str, args: &[String]) -> Result<Report, String> {
    let mut cmd = std::process::Command::new(program);
    cmd.args(args);
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        // Same reason as every other spawn here: no console flashing over
        // whatever the person is doing.
        cmd.creation_flags(0x0800_0000);
    }
    let output = cmd.output().map_err(|e| e.to_string())?;
    if !output.status.success() {
        let said = String::from_utf8_lossy(&output.stderr).trim().to_string();
        return Err(if said.is_empty() {
            "the engine failed without saying why".to_string()
        } else {
            said
        });
    }
    serde_json::from_slice(&output.stdout)
        .map_err(|e| format!("could not read the engine's answer: {e}"))
}

#[cfg(test)]
mod tests {
    use super::*;

    /// THE PREVIEW MUST NOT APPLY. The two calls differ by one flag, and
    /// getting it wrong is silent in the worst direction: the change lands
    /// while the person is still being asked whether they want it.
    #[test]
    fn a_preview_asks_the_engine_to_change_nothing() {
        let args = crate::app::criteria_args(
            std::path::Path::new("C:/somewhere/criteria.json"),
            "merge",
            true,
        );
        assert!(args.contains(&"--dry-run".to_string()));
        assert!(args.contains(&"--json".to_string()));
        assert_eq!(args[0], "criteria");
    }

    /// And the real import must not carry it, or accepting the file would do
    /// nothing at all and say it had worked.
    #[test]
    fn an_import_does_not_carry_the_dry_run_flag() {
        let args = crate::app::criteria_args(
            std::path::Path::new("C:/somewhere/criteria.json"),
            "replace",
            false,
        );
        assert!(!args.contains(&"--dry-run".to_string()));
    }

    /// THE MODE REACHES THE ENGINE. The person picks between merge and
    /// replace on the dialog, and a mode that never reached the command line
    /// would leave them choosing between two identical outcomes. Measured
    /// 2026-09-05 by hard-coding "replace" here: this is the guard that
    /// caught it.
    #[test]
    fn the_chosen_mode_is_passed_through() {
        for mode in ["merge", "replace"] {
            let args = crate::app::criteria_args(
                std::path::Path::new("C:/somewhere/criteria.json"),
                mode,
                true,
            );
            let at = args.iter().position(|a| a == "--mode").expect("no --mode");
            assert_eq!(args[at + 1], mode);
        }
    }

    /// One argument, whatever is in the path. That much is enforced by the type:
    /// criteria_args builds a Vec<String> and the path is one element of it.
    /// Asserted anyway because the natural way to break it is a later refactor
    /// that joins these into a command line, and then a path with a space
    /// reaches the engine as a file it cannot find plus a stray argument it
    /// does not know.
    #[test]
    fn a_path_with_a_space_stays_one_argument() {
        let args = crate::app::criteria_args(
            std::path::Path::new("C:/My Documents/my criteria.json"),
            "merge",
            true,
        );
        assert!(args.contains(&"C:/My Documents/my criteria.json".to_string()));
    }

    fn parse(text: &str) -> Report {
        serde_json::from_str(text).expect("a readable report")
    }

    /// The shape the engine sends, copied from the payload
    /// `criteria --import --dry-run --json` builds.
    ///
    /// WHAT THIS DOES NOT CATCH, said plainly because the obvious reading is
    /// the wrong one: a field the engine RENAMES. Measured 2026-09-05 - renaming
    /// "mode" to "how" in the engine left this whole suite green. Every field
    /// here carries serde(default), so a renamed key does not fail to parse; it
    /// comes back empty and the dialog reads "Nothing would change". This
    /// proves the struct parses the payload written below it, not the one the
    /// engine sends. The engine's real output is compared against these field
    /// names on the Python side, where the command can actually be run - see
    /// test_the_desktop_reads_the_keys_the_engine_actually_emits.
    #[test]
    fn the_engines_preview_reads() {
        let report = parse(
            r#"{"changed": ["search"], "applied": false, "mode": "merge",
                "preview": [
                  {"block": "search", "key": "terms", "was": ["fitter"],
                   "becomes": ["fitter", "welder"], "added": 1, "removed": 0},
                  {"block": "search", "key": "salary_floor", "was": null,
                   "becomes": 72000, "added": 0, "removed": 0}]}"#,
        );
        assert_eq!(report.changed, vec!["search"]);
        assert!(!report.applied);
        assert_eq!(report.mode, "merge");
        assert_eq!(report.preview.len(), 2);
        assert_eq!(report.preview[0].where_it_is(), "search.terms");
        assert_eq!(report.preview[0].what_happens(), "1 added");
    }

    /// A LIST IS COUNTED, NOT PRINTED. Two whole terms lists rendered as JSON
    /// arrays is a dialog nobody reads, and the two counts are what decide
    /// between merge and replace.
    #[test]
    fn a_list_is_described_by_what_arrives_and_what_leaves() {
        let change = Change {
            block: "search".to_string(),
            key: "terms".to_string(),
            was: serde_json::json!(["fitter", "welder"]),
            becomes: serde_json::json!(["machinist"]),
            added: 1,
            removed: 2,
        };
        assert_eq!(change.what_happens(), "1 added, 2 removed");
    }

    /// NOT SET IS NOT "null". A salary floor moving from nothing to a number
    /// is the change somebody most wants to catch before it lands, and it is
    /// exactly the one that would read as jargon.
    #[test]
    fn an_unset_value_is_said_in_words() {
        let change = Change {
            block: "search".to_string(),
            key: "salary_floor".to_string(),
            was: serde_json::Value::Null,
            becomes: serde_json::json!(72000),
            ..Change::default()
        };
        assert_eq!(change.what_happens(), "not set to 72000");
    }

    /// A whole block replaced carries no key. Naming it "search." would put a
    /// trailing dot in front of somebody deciding whether to accept it.
    #[test]
    fn a_whole_block_is_named_without_a_trailing_dot() {
        let change = Change {
            block: "skills".to_string(),
            ..Change::default()
        };
        assert_eq!(change.where_it_is(), "skills");
    }

    /// A file that changes nothing has to LOOK like it changes nothing. The
    /// engine answers with an empty list, and the dialog reads that rather
    /// than counting rows it drew.
    #[test]
    fn a_file_that_changes_nothing_reports_nothing() {
        let report = parse(r#"{"changed": [], "applied": false, "mode": "replace",
                              "preview": []}"#);
        assert!(report.is_empty());
        assert!(report.preview.is_empty());
    }
}
