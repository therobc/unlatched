//! Which held resume the app should read, mirroring the engine's
//! `unlatched/resumes.py`.
//!
//! WHY THIS IS A MODULE AND NOT A LINE IN THE VIEW. The Keywords screen read
//! `config.resume_path` and nothing else - the LAST fallback in the engine's
//! order, kept only so a profile made before attaching existed keeps working.
//! Nothing writes that key when a resume is attached, and attaching is what
//! the app tells people to do: the Resumes tab, the walkthrough's own step,
//! and the button on the dashboard all lead there. So the ordinary setup path
//! left `resume_path` empty, Keywords read no resume text at all, and every
//! tracked skill was reported as demanded-and-not-evidenced with COVERED
//! permanently empty - on the screen the walkthrough describes as "which of
//! them your resume already shows".
//!
//! The list on the Resumes tab had its own third copy of the rule, close but
//! not identical: it ignored `resume_pinned`, so a pinned resume was read by
//! screening while the marker said "in use" over a different file. That is
//! the failure resumes.py's own docstring names as worse than offering no
//! choice at all.
//!
//! One order, in one place, and a test that holds it against the engine's.

use std::path::{Path, PathBuf};

use crate::config::Config;

/// What the person started with.
pub const ORIGINAL: &str = "original";
/// The version edited after the app's advice. Screening prefers it.
pub const OPTIMIZED: &str = "optimized";

pub fn dir(home: &Path) -> PathBuf {
    home.join("resumes")
}

/// Every attached copy, newest first.
///
/// SORTED ON THE STAMP, not the whole name, which is what the engine does
/// (`resumes.versions`). Attaching writes `role-YYYYMMDDTHHMMSS-slug`, so the
/// stamp sorts correctly as text - but a reverse sort on the FULL name orders
/// by role first and only then by date, because "original" happens to sort
/// after "optimized". That gives the right answer here for the wrong reason,
/// and would stop giving it the day a role is renamed.
pub fn versions(home: &Path) -> Vec<(String, String)> {
    let mut files: Vec<(String, String)> = match std::fs::read_dir(dir(home)) {
        Ok(entries) => entries
            .filter_map(|e| e.ok())
            .filter(|e| e.path().is_file())
            .filter_map(|e| {
                let name = e.file_name().to_string_lossy().to_string();
                let role = name.split('-').next()?.to_string();
                (role == ORIGINAL || role == OPTIMIZED).then_some((role, name))
            })
            .collect(),
        Err(_) => Vec::new(),
    };
    files.sort_by(|a, b| stamp_of(&b.1).cmp(stamp_of(&a.1)));
    files
}

/// The `YYYYMMDDTHHMMSS` written into an attached copy's name, as a slice.
fn stamp_of(name: &str) -> &str {
    let rest = name.split_once('-').map(|(_, rest)| rest).unwrap_or("");
    let end = rest.char_indices().nth(15).map_or(rest.len(), |(i, _)| i);
    &rest[..end]
}

/// The file name screening reads, or None when nothing is attached.
///
/// A PINNED COPY WINS, then the newest optimized, then the newest original -
/// `unlatched/resumes.py::active_path`, less its `resume_path` fallback,
/// which is not a held copy and so is not one of these.
///
/// A pin naming a file that is no longer attached is IGNORED rather than
/// obeyed: removing the pinned copy falls back to the automatic rule instead
/// of leaving the profile with no resume. Checking membership of `versions`
/// rather than trusting the string also means a pin cannot name a file
/// outside the folder - the path traversal the engine's copy was carrying.
pub fn active_name(home: &Path, cfg: &Config) -> Option<String> {
    let files = versions(home);
    let pinned = cfg.resume_pinned.trim();
    if !pinned.is_empty() && files.iter().any(|(_, name)| name == pinned) {
        return Some(pinned.to_string());
    }
    // The ROLE is named on both arms. Falling through to "the first entry"
    // for the original would be reading a role off the sort order.
    files
        .iter()
        .find(|(role, _)| role == OPTIMIZED)
        .or_else(|| files.iter().find(|(role, _)| role == ORIGINAL))
        .map(|(_, name)| name.clone())
}

/// The full path screening reads, including the legacy `resume_path` for a
/// profile that predates attaching. Mirrors the engine's order completely.
pub fn active_path(home: &Path, cfg: &Config) -> Option<PathBuf> {
    if let Some(name) = active_name(home, cfg) {
        return Some(dir(home).join(name));
    }
    let legacy = cfg.resume_path.as_deref()?.trim();
    let candidate = PathBuf::from(legacy);
    (!legacy.is_empty() && candidate.is_file()).then_some(candidate)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;

    /// Its own folder per test: they run in parallel, and the tidy-up at the
    /// end of one would otherwise delete a folder another is still reading.
    /// Same pattern as config.rs, rather than a new crate dependency for it.
    struct Home(PathBuf);

    impl Drop for Home {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }

    impl Home {
        fn path(&self) -> &Path {
            &self.0
        }
    }

    fn home_with(test: &str, names: &[&str]) -> Home {
        let base = std::env::temp_dir().join(format!("unlatched-resumes-{test}"));
        let _ = fs::remove_dir_all(&base);
        fs::create_dir_all(dir(&base)).unwrap();
        for name in names {
            fs::write(dir(&base).join(name), "resume text").unwrap();
        }
        Home(base)
    }

    #[test]
    fn the_optimized_copy_wins_and_the_newest_of_those() {
        let home = home_with(
            "optimized-wins",
            &[
                "original-20260801T090000-cv.txt",
                "optimized-20260802T090000-cv.txt",
                "optimized-20260805T090000-cv.txt",
            ],
        );
        assert_eq!(
            active_name(home.path(), &Config::default()).as_deref(),
            Some("optimized-20260805T090000-cv.txt")
        );
    }

    #[test]
    fn with_no_optimized_copy_the_newest_original_is_read() {
        let home = home_with(
            "newest-original",
            &[
                "original-20260801T090000-cv.txt",
                "original-20260809T090000-cv.txt",
            ],
        );
        assert_eq!(
            active_name(home.path(), &Config::default()).as_deref(),
            Some("original-20260809T090000-cv.txt")
        );
    }

    /// An ORIGINAL attached after the optimized one does not take over: the
    /// role decides first and the date only breaks ties within it. Sorting on
    /// the whole file name gets this right by accident, because "original"
    /// sorts after "optimized"; sorting on the stamp gets it right on purpose.
    #[test]
    fn a_newer_original_does_not_displace_the_optimized_copy() {
        let home = home_with(
            "newer-original",
            &[
                "optimized-20260801T090000-cv.txt",
                "original-20260820T090000-cv.txt",
            ],
        );
        assert_eq!(
            active_name(home.path(), &Config::default()).as_deref(),
            Some("optimized-20260801T090000-cv.txt")
        );
    }

    /// The disagreement this module exists to end: the pin decides, and the
    /// list has to say so. It did not - the "in use" marker was drawn from
    /// the automatic rule alone, so screening read the pinned file while the
    /// screen pointed at a different one.
    #[test]
    fn a_pin_beats_the_automatic_rule() {
        let home = home_with(
            "pin-wins",
            &[
                "original-20260801T090000-cv.txt",
                "optimized-20260805T090000-cv.txt",
            ],
        );
        let cfg = Config {
            resume_pinned: "original-20260801T090000-cv.txt".to_string(),
            ..Config::default()
        };
        assert_eq!(
            active_name(home.path(), &cfg).as_deref(),
            Some("original-20260801T090000-cv.txt")
        );
    }

    #[test]
    fn a_pin_naming_something_not_attached_is_ignored_not_obeyed() {
        let home = home_with("pin-missing", &["optimized-20260805T090000-cv.txt"]);
        for pin in [
            "original-deleted-last-week.txt",
            // The traversal the engine's copy was carrying. Membership of
            // `versions` refuses it without needing to sanitise the string.
            "../../../etc/passwd",
        ] {
            let cfg = Config {
                resume_pinned: pin.to_string(),
                ..Config::default()
            };
            assert_eq!(
                active_name(home.path(), &cfg).as_deref(),
                Some("optimized-20260805T090000-cv.txt"),
                "{pin:?} should have fallen back to the automatic rule"
            );
        }
    }

    /// THE DEFECT THIS MODULE WAS WRITTEN FOR. Attaching is the documented
    /// way to give the app a resume and it never sets `resume_path`, so a
    /// lookup through that key alone finds nothing on the ordinary path -
    /// which is what the Keywords screen did, reporting every tracked skill
    /// as a gap for anybody who set the app up the way it tells them to.
    #[test]
    fn an_attached_resume_is_found_without_resume_path_being_set() {
        let home = home_with("attached-only", &["original-20260801T090000-cv.txt"]);
        let cfg = Config::default();
        assert!(cfg.resume_path.is_none(), "the ordinary setup leaves it unset");
        let path = active_path(home.path(), &cfg).expect("the attached copy");
        assert_eq!(fs::read_to_string(path).unwrap(), "resume text");
    }

    /// And the legacy key still works for a profile made before attaching
    /// existed - it is the last fallback, not a removed one.
    #[test]
    fn a_profile_with_only_the_legacy_key_still_resolves() {
        let home = home_with("legacy-key", &[]);
        let loose = home.path().join("somewhere-else.txt");
        fs::write(&loose, "older resume").unwrap();
        let cfg = Config {
            resume_path: Some(loose.to_string_lossy().into_owned()),
            ..Config::default()
        };
        assert_eq!(active_path(home.path(), &cfg).as_deref(), Some(loose.as_path()));
    }

    #[test]
    fn nothing_attached_and_no_legacy_key_is_none() {
        let home = home_with("empty", &[]);
        assert!(active_path(home.path(), &Config::default()).is_none());
    }

    /// Does this `active_path` body resolve pin, then optimized, then the
    /// legacy key - in that order?
    ///
    /// Takes the text so the check itself can be shown failing; see the test
    /// below it. Reads the CODE only: the docstring explains the order in
    /// prose and names every step out of sequence, so measuring that would be
    /// checking the comment against itself.
    fn resolves_in_our_order(function_text: &str) -> bool {
        let Some(code) = function_text
            .split_once("\"\"\"")
            .and_then(|(_, rest)| rest.split_once("\"\"\""))
            .map(|(_, code)| code)
        else {
            panic!("active_path lost its docstring, so this split is wrong");
        };
        match (
            code.find("pinned"),
            code.find("OPTIMIZED"),
            code.find("resume_path"),
        ) {
            (Some(pin), Some(optimized), Some(legacy)) => {
                pin < optimized && optimized < legacy
            }
            _ => panic!("a step is missing from the engine's active_path"),
        }
    }

    /// The two halves have to agree on the order, and the engine is where it
    /// is decided. A reordering there that nobody mirrored here would mean
    /// this screen reports coverage against one document while screening
    /// scores against another - and nothing would say so.
    #[test]
    fn the_engine_resolves_them_in_the_same_order() {
        let py = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("..")
            .join("unlatched")
            .join("resumes.py");
        let text = std::fs::read_to_string(&py)
            .unwrap_or_else(|e| panic!("cannot read {}: {e}", py.display()));
        let body = text
            .split("\ndef active_path(")
            .nth(1)
            .expect("the engine's active_path moved or was renamed")
            .split("\ndef ")
            .next()
            .unwrap();
        assert!(
            resolves_in_our_order(body),
            "the engine resolves resumes in a different order than this module"
        );
    }

    /// The positive control. A check that has never been seen to fail is a
    /// check nobody has evidence about - and the first version of the test
    /// above passed against a body whose steps were in the WRONG order,
    /// because it was reading the docstring's prose rather than the code.
    #[test]
    fn the_order_check_catches_a_reordered_engine() {
        // Written as concatenated lines rather than one wrapped literal: a
        // continuation inside a fixture is where an accidental run of spaces
        // hides, and this fixture's whole job is to be read precisely.
        let legacy_first = concat!(
            "
    \"\"\"docstring naming pinned, OPTIMIZED and resume_path.\"\"\"
",
            "    if cfg.get(\"resume_path\"): return legacy
",
            "    if pinned: return pinned
",
            "    for role in (OPTIMIZED, ORIGINAL): ...
",
        );
        assert!(
            !resolves_in_our_order(legacy_first),
            "a body that checks the legacy key first must not read as ours"
        );
    }
}
