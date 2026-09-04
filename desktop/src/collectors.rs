// The configured handoff collectors, as the menu needs them.
//
// ASKED OF THE ENGINE, NEVER PARSED OUT OF config.json HERE. This front end
// already models the rest of that file, so reading `collectors` directly would
// have been the shorter route - and wrong. What is really configured is not
// what the file says: a profile with no list at all still has one collector
// (the migrated `ingest.path`), an entry with a bad id or a duplicate id is
// refused, and a schedule that will not parse takes its whole entry out. All of
// that lives in collectors.py. A second copy here would drift, and the symptom
// would be a menu entry that pulls nothing.
//
// LOADED OFF THE UI THREAD. The engine is a frozen executable and starting one
// costs the better part of a second; doing that inside the frame that opens a
// menu is felt. The listing is fetched once when a profile opens and again when
// config.json is reloaded, and the menu shows what it has.

use serde::Deserialize;
use std::sync::{Arc, Mutex};

#[derive(Deserialize, Clone, Debug, Default, PartialEq)]
#[serde(default)]
pub struct Handoff {
    pub id: String,
    pub name: String,
    pub enabled: bool,
    pub path: String,
    pub schedule: Vec<String>,
    /// Hours since the SENDER stamped the file. None means it carries no stamp,
    /// which is not the same as fresh and must never be shown as if it were.
    pub age_hours: Option<f64>,
    pub file_present: bool,
}

impl Handoff {
    /// When the app looks by itself, in the menu's words.
    pub fn when(&self) -> String {
        if self.schedule.is_empty() {
            "every refresh".to_string()
        } else {
            self.schedule.join(", ")
        }
    }

    /// The hover line: where the file is, and how old the sender says it is.
    pub fn detail(&self) -> String {
        let age = match self.age_hours {
            Some(h) if h < 1.0 => "written in the last hour".to_string(),
            Some(h) => format!("written {h:.0}h ago"),
            None => "the sender does not stamp it, so its age is unknown".to_string(),
        };
        let present = if self.file_present {
            age
        } else {
            format!("nothing at that path yet ({age})")
        };
        format!("{}\nLooks {}.\n{}", self.path, self.when(), present)
    }
}

#[derive(Deserialize, Default)]
#[serde(default)]
struct Listing {
    collectors: Vec<Handoff>,
    /// Entries the engine refused, already worded for a person. Shown rather
    /// than dropped: a collector missing from this menu because of a typo three
    /// lines into a config file is otherwise indistinguishable from one nobody
    /// ever added.
    problems: Vec<String>,
}

/// The collectors, and the entries the engine refused, in that order.
type Answer = Result<(Vec<Handoff>, Vec<String>), String>;

/// What the menu reads. `None` means the answer has not arrived yet.
#[derive(Clone, Default)]
pub struct Collectors {
    inner: Arc<Mutex<Option<Answer>>>,
}

impl Collectors {
    /// A listing that is already answered.
    ///
    /// For tests, and for any caller that has the entries in hand: the normal
    /// route runs the engine on a thread, which a unit test cannot do and
    /// should not have to. Building the real type rather than a stand-in
    /// means what is tested is what the screens read.
    #[cfg(test)]
    pub fn from_answer(entries: Vec<Handoff>, problems: Vec<String>) -> Collectors {
        Collectors {
            inner: Arc::new(Mutex::new(Some(Ok((entries, problems))))),
        }
    }

    /// Runs `collectors --json` on a background thread and keeps the answer.
    pub fn load(program: String, args: Vec<String>) -> Collectors {
        let state = Collectors::default();
        let slot = Arc::clone(&state.inner);
        std::thread::spawn(move || {
            let answer = run(&program, &args);
            if let Ok(mut guard) = slot.lock() {
                *guard = Some(answer);
            }
        });
        state
    }

    pub fn ready(&self) -> Option<(Vec<Handoff>, Vec<String>)> {
        let guard = self.inner.lock().ok()?;
        match guard.as_ref()? {
            Ok(pair) => Some(pair.clone()),
            Err(_) => Some((Vec::new(), Vec::new())),
        }
    }

    /// The failure, if asking went wrong. Separate from `ready` so an engine
    /// that could not be run reads differently from a profile with no
    /// collectors - the menu says which.
    pub fn failure(&self) -> Option<String> {
        let guard = self.inner.lock().ok()?;
        match guard.as_ref()? {
            Err(e) => Some(e.clone()),
            Ok(_) => None,
        }
    }

    /// The ids of collectors that are configured AND enabled.
    ///
    /// What the dashboard uses to decide whether a source is allowed to be
    /// called late. On a profile with no second source this is empty, so
    /// nothing can be - which is the point: a fresh install must never show a
    /// staleness warning about a collector nobody set up.
    ///
    /// A DISABLED collector is excluded too. Turning one off is a decision;
    /// reporting it as late afterwards would be the app arguing with it.
    ///
    /// An answer that has not arrived yet counts as none, which is the safe
    /// direction: for the second the engine takes to reply, nothing is late.
    pub fn configured_ids(&self) -> Vec<String> {
        let Some((collectors, _)) = self.ready() else {
            return Vec::new();
        };
        collectors
            .into_iter()
            .filter(|c| c.enabled)
            .map(|c| c.id)
            .collect()
    }
}

fn run(program: &str, args: &[String]) -> Result<(Vec<Handoff>, Vec<String>), String> {
    let mut cmd = std::process::Command::new(program);
    cmd.args(args);
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        // Same reason as every other spawn: no console flashing over whatever
        // the person is doing.
        cmd.creation_flags(0x0800_0000);
    }
    let output = cmd.output().map_err(|e| e.to_string())?;
    if !output.status.success() {
        return Err(String::from_utf8_lossy(&output.stderr).trim().to_string());
    }
    let listing: Listing = serde_json::from_slice(&output.stdout)
        .map_err(|e| format!("could not read the collector list: {e}"))?;
    Ok((listing.collectors, listing.problems))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn parse(text: &str) -> Listing {
        serde_json::from_str(text).expect("valid listing")
    }

    #[test]
    fn a_listing_carries_the_fields_the_menu_shows() {
        let listing = parse(
            r#"{"collectors": [{"id": "partner", "name": "Partner app",
                 "enabled": true, "path": "C:/h/p.json", "schedule": ["13:00"],
                 "age_hours": 3.5, "file_present": true}], "problems": []}"#,
        );

        let one = &listing.collectors[0];
        assert_eq!(one.id, "partner");
        assert_eq!(one.when(), "13:00");
        assert!(one.detail().contains("written 4h ago"));
    }

    #[test]
    fn no_schedule_reads_as_every_refresh() {
        let listing = parse(
            r#"{"collectors": [{"id": "imported", "name": "Handoff file",
                 "enabled": true, "path": "C:/h/i.json", "schedule": [],
                 "age_hours": null, "file_present": false}]}"#,
        );

        let one = &listing.collectors[0];
        assert_eq!(one.when(), "every refresh");
        // An unstamped file must not read as fresh, and a missing one must say
        // so - both were how a dead collector looked healthy.
        assert!(one.detail().contains("age is unknown"));
        assert!(one.detail().contains("nothing at that path yet"));
    }

    #[test]
    fn problems_survive_the_parse() {
        // The engine words these for a person; this side must not swallow them.
        let listing = parse(
            r#"{"collectors": [], "problems": ["collector 'x': needs a path"]}"#,
        );

        assert_eq!(listing.problems.len(), 1);
        assert!(listing.problems[0].contains("needs a path"));
    }
}
