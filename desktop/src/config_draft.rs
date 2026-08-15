// An editable mirror of Config for the Config view. Lists are edited as
// TAGS - type, press Enter, and remove with the x on each chip; numbers and the nullable salary floor are
// edited as plain text so an in-progress edit (e.g. a half-typed number)
// never has to be a valid Config value. `to_config` turns the draft back
// into a real Config and reports every field that failed validation.

use crate::config::{
    AgentApiConfig, Config, CredentialsConfig, FetchConfig, SearchConfig, UsajobsCredentials,
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
    pub sources: BTreeMap<String, bool>,
    pub read_added_links: bool,
    pub agent_base_url: String,
    pub agent_api_key: String,
    pub agent_model: String,
    pub refresh_daily: bool,
    /// Times of day, as the person typed them. Held as text rather than parsed
    /// values so a half-typed entry does not have to be valid mid-keystroke.
    pub refresh_at: String,
    pub refresh_weekdays_only: bool,
    pub usajobs_email: String,
    pub usajobs_api_key: String,
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
            sources,
            read_added_links: cfg.fetch.read_added_links,
            agent_base_url: cfg.agent_api.base_url.clone().unwrap_or_default(),
            agent_api_key: cfg.agent_api.api_key.clone().unwrap_or_default(),
            agent_model: cfg.agent_api.model.clone().unwrap_or_default(),
            refresh_daily: cfg.refresh.daily,
            refresh_at: cfg.refresh.at.join(", "),
            refresh_weekdays_only: cfg.refresh.weekdays_only,
            usajobs_email: cfg.credentials.usajobs.email.clone().unwrap_or_default(),
            usajobs_api_key: cfg.credentials.usajobs.api_key.clone().unwrap_or_default(),
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
                work_modes,
                remote_scope,
            },
            skills: self.skills.items.clone(),
            resume_path: if resume_path.is_empty() {
                None
            } else {
                Some(resume_path)
            },
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
        })
    }
}

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
    const FORMAT: &str = "times go in as HH:MM on a 24-hour clock, separated by \
                          commas - for example 10:45, 16:30";
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

    if out.is_empty() {
        return Err(format!(
            "give at least one time, or untick the daily refresh. {FORMAT}"
        ));
    }
    out.sort_unstable();
    out.dedup();
    Ok(out
        .into_iter()
        .map(|(h, m)| format!("{h:02}:{m:02}"))
        .collect())
}

#[cfg(test)]
mod tag_field_tests {
    use super::TagField;

    #[test]
    fn commit_trims_and_adds() {
        let mut f = TagField::default();
        f.input = "  Help Desk Technician  ".to_string();
        assert!(f.commit());
        assert_eq!(f.items, vec!["Help Desk Technician".to_string()]);
        assert!(f.input.is_empty(), "the box clears so the next one can be typed");
    }

    #[test]
    fn blank_input_is_ignored() {
        let mut f = TagField::default();
        f.input = "   ".to_string();
        assert!(!f.commit());
        assert!(f.items.is_empty());
    }

    #[test]
    fn a_duplicate_is_a_no_op_not_a_second_copy() {
        let mut f = TagField::default();
        f.input = "Support Analyst".to_string();
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
        for bad in ["1 pm", "half past ten", "10.45", ""] {
            assert!(parse_times(bad).is_err(), "{bad:?} should not parse");
        }
    }

    #[test]
    fn an_empty_field_says_what_to_do_instead() {
        let err = parse_times("   ").unwrap_err();
        assert!(err.contains("untick"), "offer the way out, not just a refusal: {err}");
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
}
