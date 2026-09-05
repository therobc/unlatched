// An editable mirror of Config for the Config view. Lists are edited as
// TAGS - type, press Enter, and remove with the x on each chip. Numbers,
// and the nullable salary floor, are edited as plain text so an
// in-progress edit (a half-typed number) never has to be a valid Config
// value. `to_config` turns the draft back into a real Config and reports
// every field that failed validation.

use crate::config::{
    AgentApiConfig, CollectorEntry, Config, CredentialsConfig, FetchConfig, SearchConfig,
    UsajobsCredentials,
    KNOWN_SOURCES,
};
use std::collections::BTreeMap;

/// One list field, edited as tags: existing entries are chips you can
/// remove, and `input` is the box you type the next one into.
///
/// A 55-term title list in a three-row text box meant scrolling a tiny
/// window to change one line, with no way to see the list at a glance and
/// nothing stopping a duplicate. Chips make the list the thing you look at.
#[derive(Clone, Debug, Default)]
pub struct TagField {
    pub items: Vec<String>,
    pub input: String,
}

impl TagField {
    fn from(items: &[String]) -> Self {
        TagField { items: items.to_vec(), input: String::new() }
    }

    /// Commits whatever is in the box. Trimmed, and silently ignored when it
    /// is blank or already present - re-typing an existing term should be a
    /// no-op, not a second copy of it.
    pub fn commit(&mut self) -> bool {
        let value = self.input.trim().to_string();
        self.input.clear();
        if value.is_empty() || self.items.iter().any(|i| i.eq_ignore_ascii_case(&value)) {
            return false;
        }
        self.items.push(value);
        true
    }
}

#[derive(Clone, Debug)]
pub struct ConfigDraft {
    pub terms: TagField,
    pub title_include: TagField,
    pub title_exclude: TagField,
    pub seniority: TagField,
    pub employment_types: Vec<String>,
    pub salary_floor: String,
    pub salary_alt_floor: String,
    pub currency: String,
    /// Places worth working, edited as tags with suggestions drawn from the
    /// bundled US place list - see places.rs.
    pub locations: TagField,
    /// One tick per way of working. None ticked means all three, which is
    /// how the employment-type ticks already behave on this page.
    pub work_remote: bool,
    pub work_hybrid: bool,
    pub work_onsite: bool,
    pub skills: TagField,
    pub resume_path: String,
    /// CARRIED, NOT EDITED. The pin is chosen on the Resumes tab, and this
    /// screen has no control for it - but `to_config` rebuilds the whole
    /// Config and `config::save` writes the modelled keys over what is on
    /// disk, so a field left out here would be written back empty. Pressing
    /// Save on an unrelated setting would silently un-pin the resume.
    pub resume_pinned: String,
    pub sources: BTreeMap<String, bool>,
    pub read_added_links: bool,
    pub agent_base_url: String,
    pub agent_api_key: String,
    pub agent_model: String,
    pub us_only: bool,
    pub travel_ok: bool,
    pub refresh_daily: bool,
    /// Times of day, as the person typed them. Held as text rather than parsed
    /// values so a half-typed entry does not have to be valid mid-keystroke.
    pub refresh_at: String,
    /// The same, for Saturday and Sunday. Separate because the weekend
    /// run is a different decision, and text for the same reason.
    pub refresh_weekend_at: String,
    pub refresh_weekdays_only: bool,
    pub usajobs_email: String,
    pub usajobs_api_key: String,
    /// Edited in place by the Collectors section, not parsed out of text
    /// fields like the rest of this struct: an entry is a record, and the
    /// screen edits the record.
    pub collectors: Vec<CollectorEntry>,
}

/// Is this way of working ticked?
///
/// An empty work_modes reads as ALL THREE ticked rather than none, because
/// that is what it means to the engine and a person opening this page should
/// see the search they actually have. Falls back to the superseded
/// remote_scope so a config written before the ticks existed still shows
/// remote-only as remote-only.
fn has_mode(cfg: &Config, mode: &str) -> bool {
    if !cfg.search.work_modes.is_empty() {
        return cfg.search.work_modes.iter().any(|m| m == mode);
    }
    if cfg.search.remote_scope == "remote_only" {
        return mode == "remote";
    }
    true
}

impl ConfigDraft {
    pub fn from_config(cfg: &Config) -> Self {
        let mut sources = cfg.sources.clone();
        for name in KNOWN_SOURCES {
            sources.entry((*name).to_string()).or_insert(true);
        }
        ConfigDraft {
            terms: TagField::from(&cfg.search.terms),
            title_include: TagField::from(&cfg.search.title_include),
            title_exclude: TagField::from(&cfg.search.title_exclude),
            seniority: TagField::from(&cfg.search.seniority),
            employment_types: cfg.search.employment_types.clone(),
            salary_alt_floor: cfg
                .search
                .salary_alt_floor
                .map(|v| v.to_string())
                .unwrap_or_default(),
            salary_floor: cfg
                .search
                .salary_floor
                .map(|v| v.to_string())
                .unwrap_or_default(),
            currency: cfg.search.currency.clone(),
            locations: TagField::from(&cfg.search.locations),
            work_remote: has_mode(cfg, "remote"),
            work_hybrid: has_mode(cfg, "hybrid"),
            work_onsite: has_mode(cfg, "onsite"),
            skills: TagField::from(&cfg.skills),
            resume_path: cfg.resume_path.clone().unwrap_or_default(),
            resume_pinned: cfg.resume_pinned.clone(),
            sources,
            read_added_links: cfg.fetch.read_added_links,
            agent_base_url: cfg.agent_api.base_url.clone().unwrap_or_default(),
            agent_api_key: cfg.agent_api.api_key.clone().unwrap_or_default(),
            agent_model: cfg.agent_api.model.clone().unwrap_or_default(),
            us_only: cfg.search.us_only,
            travel_ok: cfg.search.travel_ok,
            refresh_daily: cfg.refresh.daily,
            refresh_at: cfg.refresh.at.join(", "),
            refresh_weekend_at: cfg.refresh.weekend_at.join(", "),
            refresh_weekdays_only: cfg.refresh.weekdays_only,
            usajobs_email: cfg.credentials.usajobs.email.clone().unwrap_or_default(),
            usajobs_api_key: cfg.credentials.usajobs.api_key.clone().unwrap_or_default(),
            collectors: cfg.collectors.clone(),
        }
    }

    /// Parses every field, collecting one message per invalid field instead
    /// of stopping at the first problem, so a Save attempt shows the user
    /// everything that needs fixing in one pass.
    pub fn to_config(&self) -> Result<Config, Vec<String>> {
        let mut errors = Vec::new();

        let salary_floor = if self.salary_floor.trim().is_empty() {
            None
        } else {
            match self.salary_floor.trim().parse::<i64>() {
                Ok(v) if v >= 0 => Some(v),
                Ok(_) => {
                    errors.push("search.salary_floor must not be negative".to_string());
                    None
                }
                Err(_) => {
                    errors.push("search.salary_floor must be a whole number".to_string());
                    None
                }
            }
        };

        let salary_alt_floor = if self.salary_alt_floor.trim().is_empty() {
            None
        } else {
            match self.salary_alt_floor.trim().parse::<i64>() {
                Ok(v) if v >= 0 => Some(v),
                Ok(_) => {
                    errors.push("search.salary_alt_floor must not be negative".to_string());
                    None
                }
                Err(_) => {
                    errors.push("search.salary_alt_floor must be a whole number".to_string());
                    None
                }
            }
        };
        // An alt floor at or above the real floor describes no band at all:
        // every posting is either above the floor or below both.
        if let (Some(alt), Some(floor)) = (salary_alt_floor, salary_floor) {
            if alt >= floor {
                errors.push(format!(
                    "search.salary_alt_floor ({alt}) must be below search.salary_floor ({floor})"
                ));
            }
        }

        // An unreadable or out-of-range hour falls back to the documented
        // default rather than blocking the save: the rest of the form is
        // still worth keeping.
        let refresh_at = match parse_times(&self.refresh_at) {
            Ok(times) => times,
            Err(message) => {
                errors.push(message);
                // The PREVIOUS schedule, not the default. A rejected edit must
                // not quietly move somebody's run times to something they never
                // chose - which for an instance sequenced behind another job is
                // the difference between working and colliding.
                Vec::new()
            }
        };

        // THROUGH THE SAME parse_times, deliberately. A second copy of
        // "what is a valid time" is a second thing to keep in step, and
        // this file already carries the scar of a value that drifted.
        let refresh_weekend_at = match parse_times(&self.refresh_weekend_at) {
            Ok(times) => times,
            Err(message) => {
                errors.push(message);
                Vec::new()
            }
        };

        // A TIME IS ONLY REQUIRED FOR A RUN THAT HAPPENS, and the refusal has
        // to name the switch that would settle it. Being told to untick the
        // daily refresh over a blank WEEKEND box is how somebody turns the
        // whole schedule off to clear a message about one day of it.
        //
        // When the run is switched off, an empty list is stored and nothing is
        // blocked: the engine reads an empty list as "use the documented
        // default" (refresh._anchors), so it is inert rather than a search
        // that silently never refreshes.
        if self.refresh_daily && self.refresh_at.trim().is_empty() {
            errors.push(format!(
                "give at least one time, or untick the daily refresh. {TIME_FORMAT}"
            ));
        }
        if self.refresh_daily
            && !self.refresh_weekdays_only
            && self.refresh_weekend_at.trim().is_empty()
        {
            errors.push(format!(
                "give at least one weekend time, or tick Skip weekends. {TIME_FORMAT}"
            ));
        }

        // Written as an empty list when all three are ticked, because "I will
        // take anything" and "I ticked everything" are the same search, and
        // the empty form is the one the engine treats as no filter at all.
        let mut work_modes = Vec::new();
        if !(self.work_remote && self.work_hybrid && self.work_onsite) {
            for (on, name) in [
                (self.work_remote, "remote"),
                (self.work_hybrid, "hybrid"),
                (self.work_onsite, "onsite"),
            ] {
                if on {
                    work_modes.push(name.to_string());
                }
            }
        }
        // The ticks are the whole answer now, so the superseded key is
        // written back neutral. Leaving a "remote_only" in place would keep
        // filtering underneath ticks that say otherwise, with nothing on
        // screen to explain where the onsite roles went.
        let remote_scope = "any".to_string();

        let resume_path = self.resume_path.trim().to_string();
        let agent_base_url = self.agent_base_url.trim().to_string();
        let agent_api_key = self.agent_api_key.trim().to_string();
        let agent_model = self.agent_model.trim().to_string();
        let usajobs_email = self.usajobs_email.trim().to_string();
        let usajobs_api_key = self.usajobs_api_key.trim().to_string();

        let mut collectors: Vec<CollectorEntry> = Vec::new();
        let mut seen: Vec<String> = Vec::new();
        for entry in &self.collectors {
            let has_id = !entry.id.trim().is_empty();
            let has_path = !entry.path.trim().is_empty();
            // A WHOLLY blank row is what an empty Add button leaves behind, and
            // blocking every other field on the screen over it would be absurd.
            // A HALF-filled one is somebody who started and stopped - dropping
            // that silently would lose what they typed.
            if !has_id && !has_path {
                continue;
            }
            if !has_path {
                errors.push(format!(
                    "Collector {:?} needs the path to the file it writes.",
                    entry.id.trim()
                ));
                continue;
            }
            let ident = match crate::config::normalise_collector_id(&entry.id) {
                Ok(ident) => ident,
                Err(message) => {
                    errors.push(message);
                    continue;
                }
            };
            if seen.contains(&ident) {
                errors.push(format!(
                    "Collector {ident:?} is listed more than once. Two entries \
                     sharing an id would put two files' jobs under one name."
                ));
                continue;
            }
            seen.push(ident.clone());
            collectors.push(CollectorEntry {
                id: ident,
                path: entry.path.trim().to_string(),
                label: entry.label.trim().to_string(),
                ..entry.clone()
            });
        }

        if !errors.is_empty() {
            return Err(errors);
        }

        Ok(Config {
            search: SearchConfig {
                terms: self.terms.items.clone(),
                title_include: self.title_include.items.clone(),
                title_exclude: self.title_exclude.items.clone(),
                seniority: self.seniority.items.clone(),
                employment_types: self.employment_types.clone(),
                salary_floor,
                salary_alt_floor,
                currency: self.currency.trim().to_string(),
                locations: self.locations.items.clone(),
                us_only: self.us_only,
                travel_ok: self.travel_ok,
                work_modes,
                remote_scope,
            },
            skills: self.skills.items.clone(),
            resume_path: if resume_path.is_empty() {
                None
            } else {
                Some(resume_path)
            },
            resume_pinned: self.resume_pinned.clone(),
            sources: self.sources.clone(),
            fetch: FetchConfig {
                read_added_links: self.read_added_links,
            },
            agent_api: AgentApiConfig {
                base_url: if agent_base_url.is_empty() {
                    None
                } else {
                    Some(agent_base_url)
                },
                api_key: if agent_api_key.is_empty() {
                    None
                } else {
                    Some(agent_api_key)
                },
                model: if agent_model.is_empty() {
                    None
                } else {
                    Some(agent_model)
                },
            },
            refresh: crate::config::RefreshConfig {
                daily: self.refresh_daily,
                at: refresh_at,
                weekend_at: refresh_weekend_at,
                weekdays_only: self.refresh_weekdays_only,
            },
            credentials: CredentialsConfig {
                usajobs: UsajobsCredentials {
                    email: if usajobs_email.is_empty() {
                        None
                    } else {
                        Some(usajobs_email)
                    },
                    api_key: if usajobs_api_key.is_empty() {
                        None
                    } else {
                        Some(usajobs_api_key)
                    },
                },
            },
            collectors,
        })
    }
}

/// How a time is written, quoted by every refusal about one so that the
/// answer travels with the complaint.
const TIME_FORMAT: &str = "times go in as HH:MM on a 24-hour clock, separated by \
                           commas - for example 11:00, 16:30";

/// Turn typed times into the "HH:MM" list the engine reads.
///
/// Accepts what is unambiguous and refuses the rest by NAME rather than
/// guessing. The refusal matters more than the tolerance here: this decides
/// when collection runs, and a value that is stored but never fires looks
/// exactly like a schedule that works until somebody notices nothing has
/// collected for a week.
///
/// "1:30" is read as 01:30, not half past thirteen - a 24-hour clock is what
/// the field says it wants, and inventing an afternoon from a bare number would
/// be a guess about the most consequential digit.
pub fn parse_times(raw: &str) -> Result<Vec<String>, String> {
    const FORMAT: &str = TIME_FORMAT;
    let mut out: Vec<(u32, u32)> = Vec::new();

    for piece in raw.split(',') {
        let text = piece.trim();
        if text.is_empty() {
            continue;
        }
        let (hour_text, minute_text) = match text.split_once(':') {
            Some(pair) => pair,
            // A bare number is an hour on the hour. Common enough to type that
            // refusing it would be pedantry, and it cannot mean anything else.
            None => (text, "00"),
        };
        let hour: u32 = hour_text
            .trim()
            .parse()
            .map_err(|_| format!("{text:?} is not a time. {FORMAT}"))?;
        let minute: u32 = minute_text
            .trim()
            .parse()
            .map_err(|_| format!("{text:?} is not a time. {FORMAT}"))?;
        if hour > 23 || minute > 59 {
            return Err(format!("{text:?} is not a time on the clock. {FORMAT}"));
        }
        out.push((hour, minute));
    }

    // A BLANK FIELD IS NOT THIS FUNCTION'S DECISION. It used to refuse here
    // with one message about the daily refresh, whichever box was empty - and
    // the box that would satisfy that message is disabled whenever the run it
    // schedules is switched off, so "Skip weekends" plus a cleared weekend
    // time left the whole Config screen unsaveable with the only control that
    // could fix it greyed out. What an empty list means depends on whether
    // the run happens, which only the caller knows; see to_config.
    out.sort_unstable();
    out.dedup();
    Ok(out
        .into_iter()
        .map(|(h, m)| format!("{h:02}:{m:02}"))
        .collect())
}

#[cfg(test)]
mod collector_tests {
    use super::ConfigDraft;
    use crate::config::{CollectorEntry, Config};

    /// SAVING A BROKEN ENTRY IS REFUSED, not silently corrected. The engine
    /// would report it hours later, on a screen nobody is looking at.
    #[test]
    fn a_collector_with_an_unusable_id_fails_the_save() {
        let mut draft = ConfigDraft::from_config(&Config::default());
        draft.collectors.push(CollectorEntry {
            id: "has space".to_string(),
            path: "C:/handoff/partner.json".to_string(),
            enabled: true,
            ..CollectorEntry::default()
        });
        let errors = draft.to_config().unwrap_err();
        assert!(errors.iter().any(|e| e.contains("has space")), "{errors:?}");
    }

    /// TWO ENTRIES CANNOT SHARE A NAMESPACE. Verified in collectors.configured:
    /// the second entry is skipped and a line is appended to `problems`, which
    /// this app does show - in the collectors menu, on a later visit. Until
    /// then both entries sit on the Config screen looking configured while one
    /// of them never runs.
    #[test]
    fn two_collectors_cannot_share_an_id() {
        let mut draft = ConfigDraft::from_config(&Config::default());
        for path in ["C:/a.json", "C:/b.json"] {
            draft.collectors.push(CollectorEntry {
                id: "Partner".to_string(),
                path: path.to_string(),
                enabled: true,
                ..CollectorEntry::default()
            });
        }
        let errors = draft.to_config().unwrap_err();
        assert!(errors.iter().any(|e| e.contains("more than once")), "{errors:?}");
    }

    /// The id is stored normalised, because that is the form the engine writes
    /// into jobs.source. Verified in importer.check_collector_id, which returns
    /// the stripped and lower-cased value and records that everything
    /// downstream uses what it returns. Saving "MyBoard" and matching on
    /// "myboard" later would look like the collector had never run.
    #[test]
    fn a_saved_collector_id_is_lowercased() {
        let mut draft = ConfigDraft::from_config(&Config::default());
        draft.collectors.push(CollectorEntry {
            id: "MyBoard".to_string(),
            path: "C:/handoff/partner.json".to_string(),
            enabled: true,
            ..CollectorEntry::default()
        });
        let cfg = draft.to_config().unwrap();
        assert_eq!(cfg.collectors[0].id, "myboard");
    }

    /// A row somebody added and did not fill in is DROPPED rather than
    /// refused. An Add button leaves a blank behind, and that is not a reason
    /// to block every other field on the screen from saving.
    #[test]
    fn a_blank_collector_row_is_dropped_not_refused() {
        let mut draft = ConfigDraft::from_config(&Config::default());
        draft.collectors.push(CollectorEntry::default());
        let cfg = draft.to_config().expect("a blank row blocked the save");
        assert!(cfg.collectors.is_empty());
    }

    /// A HALF-FILLED row is a different thing from a blank one: somebody
    /// started and stopped. Dropping it would lose what they typed with
    /// nothing on screen to say so - by construction, since the drop branch
    /// pushes no message.
    #[test]
    fn a_half_filled_collector_row_is_refused() {
        let mut draft = ConfigDraft::from_config(&Config::default());
        draft.collectors.push(CollectorEntry {
            id: "partner".to_string(),
            ..CollectorEntry::default()
        });
        let errors = draft.to_config().unwrap_err();
        assert!(errors.iter().any(|e| e.contains("file")), "{errors:?}");
    }
}

#[cfg(test)]
mod tag_field_tests {
    use super::TagField;

    #[test]
    fn commit_trims_and_adds() {
        let mut f = TagField {
            input: "  Help Desk Technician  ".to_string(),
            ..Default::default()
        };
        assert!(f.commit());
        assert_eq!(f.items, vec!["Help Desk Technician".to_string()]);
        assert!(f.input.is_empty(), "the box clears so the next one can be typed");
    }

    #[test]
    fn blank_input_is_ignored() {
        let mut f = TagField {
            input: "   ".to_string(),
            ..Default::default()
        };
        assert!(!f.commit());
        assert!(f.items.is_empty());
    }

    #[test]
    fn a_duplicate_is_a_no_op_not_a_second_copy() {
        let mut f = TagField {
            input: "Support Analyst".to_string(),
            ..Default::default()
        };
        f.commit();
        // Case-insensitively: "support analyst" is the same search term.
        f.input = "support analyst".to_string();
        assert!(!f.commit());
        assert_eq!(f.items.len(), 1);
    }
}

#[cfg(test)]
mod refresh_time_tests {
    use super::{parse_times, ConfigDraft};
    use crate::config::{Config, RefreshConfig};

    #[test]
    fn ordinary_times_come_back_canonical_and_sorted() {
        assert_eq!(
            parse_times("16:30, 10:45").unwrap(),
            ["10:45", "16:30"],
            "order typed must not decide order run"
        );
        assert_eq!(parse_times("9:05").unwrap(), ["09:05"]);
    }

    #[test]
    fn a_bare_hour_is_on_the_hour() {
        assert_eq!(parse_times("13").unwrap(), ["13:00"]);
        assert_eq!(parse_times("13, 16:30").unwrap(), ["13:00", "16:30"]);
    }

    #[test]
    fn the_same_time_twice_is_one_run() {
        assert_eq!(parse_times("13:00, 13:00").unwrap(), ["13:00"]);
    }

    #[test]
    fn a_time_that_is_not_on_the_clock_is_refused_by_name() {
        // The important half. Silently keeping a value that can never fire is
        // indistinguishable from a working schedule until a week has passed
        // with nothing collected.
        for bad in ["25:00", "10:75", "-1:00"] {
            let err = parse_times(bad).unwrap_err();
            assert!(err.contains(bad), "the message must name what was rejected: {err}");
            assert!(err.contains("24-hour"), "and say what the format is: {err}");
        }
    }

    #[test]
    fn words_and_am_pm_are_refused_rather_than_guessed_at() {
        // "1 pm" is not accepted, and that is deliberate: guessing the
        // afternoon from a bare number would be a guess about the digit that
        // matters most.
        for bad in ["1 pm", "half past ten", "10.45"] {
            assert!(parse_times(bad).is_err(), "{bad:?} should not parse");
        }
    }

    /// An empty field is not a malformed time, and this function no longer
    /// pretends to know what it means. It said "untick the daily refresh"
    /// whichever box was blank, which is the wrong instruction for the
    /// weekend one - see the two tests below, where the caller that DOES know
    /// whether the run happens makes that call.
    #[test]
    fn an_empty_field_is_left_for_the_caller_to_judge() {
        assert_eq!(parse_times("   ").unwrap(), Vec::<String>::new());
        assert_eq!(parse_times("").unwrap(), Vec::<String>::new());
        // A blank ENTRY among real ones is still just skipped.
        assert_eq!(parse_times("11:00, , 16:30").unwrap(), ["11:00", "16:30"]);
    }

    #[test]
    fn a_rejected_edit_does_not_silently_move_the_schedule() {
        // What the person had must survive a typo. For an instance sequenced
        // behind another job, a schedule quietly reset to the default is the
        // difference between running after it and colliding with it.
        let cfg = Config {
            refresh: RefreshConfig {
                daily: true,
                at: vec!["13:00".to_string(), "16:30".to_string()],
                weekend_at: vec!["12:15".to_string()],
                weekdays_only: false,
            },
            ..Config::default()
        };
        let mut draft = ConfigDraft::from_config(&cfg);
        assert_eq!(draft.refresh_at, "13:00, 16:30");

        draft.refresh_at = "25:00".to_string();
        let errors: Vec<String> = draft.to_config().unwrap_err();
        assert!(errors.iter().any(|e| e.contains("25:00")));
        // The saved config is never written when to_config errors, so the file
        // still holds 13:00 - this asserts the caller is given an error rather
        // than a config carrying a substituted default.
    }

    #[test]
    fn the_weekend_time_survives_a_round_trip_through_the_draft() {
        // The engine has honoured refresh.weekend_at since 2026-08-09 while
        // this struct had no field for it, so the Config screen could not show
        // or change the time the weekend actually ran at. A round trip is what
        // proves the box is wired to the file rather than merely drawn.
        let cfg = Config {
            refresh: RefreshConfig {
                daily: true,
                at: vec!["11:00".to_string(), "16:30".to_string()],
                weekend_at: vec!["12:15".to_string()],
                weekdays_only: false,
            },
            ..Config::default()
        };

        let mut draft = ConfigDraft::from_config(&cfg);
        assert_eq!(draft.refresh_weekend_at, "12:15");

        draft.refresh_weekend_at = "9:30, 18:00".to_string();
        let saved = draft.to_config().expect("both times are valid");
        // Normalised and sorted by the same parse_times the weekday box uses -
        // one rule for both fields, which is the whole reason it is shared.
        assert_eq!(saved.refresh.weekend_at, vec!["09:30", "18:00"]);
        // And the weekday times are untouched by editing the weekend one.
        assert_eq!(saved.refresh.at, vec!["11:00", "16:30"]);
    }

    /// THE DEAD END. Every empty time field went through one refusal that
    /// named the daily refresh, whichever box was blank - and the box a
    /// person would have to use to satisfy it is DISABLED whenever the run
    /// it schedules is switched off. Tick "Skip weekends", clear the weekend
    /// time, and Save reports a problem about the daily refresh while the
    /// only control that could fix it is greyed out: the Config screen
    /// cannot be saved at all, by any route, and nothing on it says why.
    ///
    /// A run that is switched off does not need a time. The engine already
    /// treats an empty list as "use the documented default" (refresh._anchors),
    /// so storing one is inert rather than a search that never refreshes.
    #[test]
    fn a_run_that_is_switched_off_does_not_need_a_time() {
        let mut draft = ConfigDraft::from_config(&Config::default());
        draft.refresh_weekdays_only = true;
        draft.refresh_weekend_at = String::new();
        let saved = draft
            .to_config()
            .expect("weekends are skipped, so a blank weekend time blocks nothing");
        assert!(saved.refresh.weekend_at.is_empty());

        let mut draft = ConfigDraft::from_config(&Config::default());
        draft.refresh_daily = false;
        draft.refresh_at = String::new();
        let saved = draft
            .to_config()
            .expect("the daily refresh is off, so a blank time blocks nothing");
        assert!(saved.refresh.at.is_empty());
    }

    /// A run that IS switched on still needs a time, and the refusal has to
    /// name the control that would settle it. "Untick the daily refresh" is
    /// the wrong instruction for the weekend box - the way out there is
    /// "Skip weekends", and being told to reach for the other switch is how
    /// somebody ends up turning off the whole schedule to clear a message.
    #[test]
    fn an_empty_time_for_a_run_that_happens_names_its_own_way_out() {
        let mut draft = ConfigDraft::from_config(&Config::default());
        draft.refresh_daily = true;
        draft.refresh_at = String::new();
        let errors = draft.to_config().unwrap_err();
        assert!(
            errors.iter().any(|e| e.contains("untick the daily refresh")),
            "{errors:?}"
        );

        let mut draft = ConfigDraft::from_config(&Config::default());
        draft.refresh_daily = true;
        draft.refresh_weekdays_only = false;
        draft.refresh_weekend_at = String::new();
        let errors = draft.to_config().unwrap_err();
        assert!(
            errors.iter().any(|e| e.contains("Skip weekends")),
            "the weekend box must point at its own switch: {errors:?}"
        );
    }

    /// A SETTING THIS SCREEN DOES NOT SHOW MUST SURVIVE ITS SAVE. The pin is
    /// chosen on the Resumes tab, `to_config` rebuilds the whole Config, and
    /// config::save writes every modelled key over what is on disk - so a
    /// field the draft forgot would be written back empty and pressing Save
    /// on an unrelated setting would silently un-pin somebody's resume.
    #[test]
    fn the_resume_pin_survives_a_save_from_this_screen() {
        let cfg = Config {
            resume_pinned: "optimized-20260805T090000-cv.txt".to_string(),
            ..Config::default()
        };
        let mut draft = ConfigDraft::from_config(&cfg);
        // An edit to something else entirely, which is the realistic path.
        draft.currency = "USD".to_string();
        let saved = draft.to_config().expect("the form is valid");
        assert_eq!(saved.resume_pinned, "optimized-20260805T090000-cv.txt");
    }

    #[test]
    fn a_bad_weekend_time_is_reported_like_any_other() {
        let mut draft = ConfigDraft::from_config(&Config::default());
        draft.refresh_weekend_at = "half eleven".to_string();
        let errors: Vec<String> = draft.to_config().unwrap_err();
        assert!(
            errors.iter().any(|e| e.contains("24-hour clock")),
            "a weekend time that does not parse must be named, not swallowed: {errors:?}"
        );
    }
}
