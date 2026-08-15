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
pub mod columns;
pub mod companies;
pub mod dashboard_view;
pub mod config_view;
pub mod getting_started;
pub mod keywords;
pub mod new_profile;
pub mod pipeline;
pub mod profiles_view;
pub mod resumes_view;
pub mod running_bar;
pub mod triage;
