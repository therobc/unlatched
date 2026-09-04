/// Each hand-added link is re-read at most once a day. Mirrors
/// manual.RECHECK_MIN_HOURS in the engine, which is the authority - this copy
/// only decides whether a button is greyed out.
///
/// ONE COPY. Triage and the Dashboard each had their own `const ... = 20.0`,
/// which is two things to keep in step with the engine instead of one.
pub const MANUAL_RECHECK_MIN_HOURS: f64 = 20.0;

pub mod add_job;
pub mod agent;
pub mod attachments;
pub mod collect_menu;
pub mod collectors_menu;
pub mod columns;
pub mod companies;
pub mod config_view;
pub mod dashboard_view;
pub mod getting_started;
pub mod keywords;
pub mod new_profile;
pub mod pipeline;
pub mod profiles_view;
pub mod resumes_view;
pub mod running_bar;
pub mod triage;

#[cfg(test)]
mod tests {
    /// The engine is the authority on this number and this file says so, but
    /// nothing held the two together - which is exactly how the default
    /// refresh times came to differ between the halves for four days, with
    /// three copies of the same decision and no check between any of them.
    ///
    /// A drift here greys out the Re-check button while the engine would have
    /// re-read the link, or offers it while the engine refuses - a control
    /// that lies about what pressing it does.
    #[test]
    fn the_engine_agrees_on_how_long_a_link_waits() {
        let py = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("..")
            .join("unlatched")
            .join("manual.py");
        let text = std::fs::read_to_string(&py)
            .unwrap_or_else(|e| panic!("cannot read {}: {e}", py.display()));
        let theirs: f64 = text
            .split("\nRECHECK_MIN_HOURS = ")
            .nth(1)
            .expect("the engine's RECHECK_MIN_HOURS moved or was renamed")
            .lines()
            .next()
            .unwrap()
            .trim()
            .parse()
            .expect("not a number");
        assert_eq!(
            theirs,
            super::MANUAL_RECHECK_MIN_HOURS,
            "the two halves disagree about how long a hand-added link waits"
        );
    }
}
