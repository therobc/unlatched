// Small display-string helpers shared by more than one view.

/// Truncates to a display-safe length instead of relying on egui to wrap a
/// long string inside a fixed-width table cell. A pre-cut plain string is
/// simple, predictable, and avoids the label-wrapping traps that hit
/// LayoutJob-backed text in narrow containers.
pub fn truncate(s: &str, max_chars: usize) -> String {
    if s.chars().count() <= max_chars {
        s.to_string()
    } else {
        let cut: String = s.chars().take(max_chars.saturating_sub(1)).collect();
        format!("{cut}...")
    }
}

pub fn salary_range(
    min: Option<i64>,
    max: Option<i64>,
    currency: Option<&str>,
    hourly: Option<f64>,
) -> String {
    // An hourly posting is shown at its stated RATE. salary_min/max are
    // annualised so one floor can judge a mixed corpus, but that multiplies
    // by 2080 hours - our assumption, not the employer's. Printing a derived
    // "56160" for a posting that said "$27.00/hr" hides what it actually
    // said, so the rate leads and the annual equivalent follows in
    // parentheses, marked approximate.
    if let Some(rate) = hourly.filter(|r| *r > 0.0) {
        return match max {
            Some(annual) => format!("${rate:.2}/hr (~{annual}/yr)"),
            None => format!("${rate:.2}/hr"),
        };
    }
    let cur = currency.unwrap_or("");
    match (min, max) {
        (Some(a), Some(b)) => format!("{cur} {a} - {b}").trim().to_string(),
        (Some(a), None) => format!("{cur} {a}+").trim().to_string(),
        (None, Some(b)) => format!("up to {cur} {b}").trim().to_string(),
        (None, None) => String::new(),
    }
}

pub fn opt_str(v: &Option<String>) -> &str {
    v.as_deref().unwrap_or("")
}

/// How current what you are looking at is.
///
/// One function, read by the dashboard AND by the job list. The list had no
/// freshness line at all - the one screen where a person is actually deciding
/// whether to apply to something, and nothing on it said how old the pile was
/// (decided 2026-08-07). Sharing the wording also stops the two screens from
/// describing the same collection differently.
pub fn collected_line(last_collected: Option<&str>) -> String {
    let Some(stamp) = last_collected else {
        return "No boards collected yet".to_string();
    };
    let age = posted_age(stamp);
    // NAMES THE BOARDS. It used to say "Collected today" full stop, over a
    // number that was MAX(fetched_at) across everything - so the board sweep,
    // which runs whenever the app runs, answered on behalf of an external
    // collector that might not have run at all. A claim this size has to say
    // what it is about.
    if age.is_empty() {
        "Boards collected recently".to_string()
    } else if age == "today" {
        "Boards collected today".to_string()
    } else {
        format!("Boards last collected {age} ago")
    }
}

/// A clock time `in_secs` from now, given the seconds already into the day.
///
/// A TIME, NOT A COUNTDOWN. "in 2h 41m" asks a person to do arithmetic against
/// a collector that finishes at 12:30; "14:00" is the question they have.
pub fn clock_after(now_local_secs: i64, in_secs: i64) -> String {
    let at = (now_local_secs + in_secs).rem_euclid(86_400);
    format!("{:02}:{:02}", at / 3_600, (at % 3_600) / 60)
}

/// The external collector's file, as a line for the top of the dashboard.
///
/// Answers three questions the app could already have answered and did not:
/// is the file there at all, how old is it, and when will this app next look.
///
/// THE FILE'S AGE, NOT THE ROWS'. The source panel further down shows how long
/// ago the newest imported row arrived. This shows how long ago the SENDER
/// wrote the file, which is what says whether today's collection has landed on
/// disk yet - they differ by exactly the gap this line exists to make visible.
///
/// Returns the text and whether it wants attention.
pub fn collector_file_line(
    name: &str,
    file_present: bool,
    age_hours: Option<f64>,
    rows_age_hours: Option<f64>,
    next_look: Option<String>,
) -> (String, bool) {
    if !file_present {
        // Nothing on disk is not a small detail: every age below would be
        // about a file that is not there.
        return (format!("{name}: no file yet"), true);
    }
    // The collector's NAME is already in front of this - and the default one
    // is literally "Handoff file", which made the line read "Handoff file:
    // file 23h old". The age says the age; the name says what it is about.
    let age = match age_hours {
        Some(h) if h < 1.0 => "written in the last hour".to_string(),
        Some(h) if h < 48.0 => format!("{h:.0}h old"),
        Some(h) => format!("{:.0} days old", h / 24.0),
        // Unstamped is not fresh, and must never read as if it were.
        None => "age unknown".to_string(),
    };
    // NEWER THAN ANYTHING IMPORTED means the sender has finished and this app
    // has not read it yet - the one state where pressing Refresh does
    // something, and the answer to "did the collection complete".
    //
    // An hour of tolerance: the file's time comes from the filesystem and the
    // rows' from a stamp the importer wrote, so they are never exactly equal,
    // and a line flickering between two readings of one event helps nobody.
    let waiting = match (age_hours, rows_age_hours) {
        (Some(file), Some(rows)) => rows - file > 1.0,
        // Nothing imported yet, but a file is sitting there.
        (Some(_), None) => true,
        _ => false,
    };
    if waiting {
        return (format!("{name}: new file ({age}) - not taken in yet"), true);
    }
    let when = next_look
        .map(|t| format!(" - next look {t}"))
        .unwrap_or_default();
    // WHEN IT WAS READ, said out loud. The file's age answers "has the
    // collector run"; this answers "does this app have it", and they are
    // different questions with different fixes - one is the collector's
    // problem, the other is a Refresh away.
    let taken = match rows_age_hours {
        Some(h) if h < 1.0 => ", taken in within the hour".to_string(),
        Some(h) if h < 48.0 => format!(", taken in {h:.0}h ago"),
        Some(h) => format!(", taken in {:.0} days ago", h / 24.0),
        // Unreachable while a file exists - no rows means `waiting` above
        // returned already - but saying nothing is right if it ever is.
        None => String::new(),
    };
    // Past a day with no new file is worth the colour; the collector runs daily.
    let stale = age_hours.is_none_or(|h| h >= 24.0);
    (format!("{name}: {age}{taken}{when}"), stale)
}

/// Where a configured collector stands today.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Collected {
    /// Today's handover has arrived. Nothing to say.
    Today,
    /// Nothing yet today, and it is not due to be worried about.
    NotYet,
    /// Nothing today and the window has closed.
    Late,
}

/// Whether a source has delivered today, and whether that is a problem yet.
///
/// SEPARATE FROM source_is_late BECAUSE "NOT YET" IS NOT "LATE". Reporting
/// only lateness left the whole morning silent: between midnight and 13:30 a
/// person could not tell today's import from a missing one, which is most of
/// the hours anybody is actually looking. Measured 2026-08-25 at 10:23 - the
/// boards had all run between 09:09 and 09:58 and `imported` had brought
/// nothing since 22:11 the night before, and the screen said nothing about it.
///
/// Delivered-today is `age <= seconds into the day`: anything newer than local
/// midnight arrived today. Same clock as the lateness rule, deliberately.
pub fn collected_state(stamp: Option<&str>, now_local_secs: i64) -> Collected {
    let Some(raw) = stamp.map(str::trim).filter(|s| !s.is_empty()) else {
        return Collected::NotYet;
    };
    let Some(age) = crate::date::seconds_since(raw) else {
        return Collected::NotYet;
    };
    if age <= now_local_secs {
        return Collected::Today;
    }
    if is_late_at(age, now_local_secs) {
        Collected::Late
    } else {
        Collected::NotYet
    }
}

/// The clause about collectors that have not delivered today.
///
/// Returns the text and whether ANY of them is overdue, which is what decides
/// the colour: grey is a statement of fact, amber is something to act on.
/// Empty text means every configured collector has already delivered today and
/// the caller draws nothing.
pub fn collectors_line(pending: &[(String, String, bool)]) -> (String, bool) {
    let late = pending.iter().any(|(_, _, l)| *l);
    let text = match pending {
        [] => String::new(),
        [(name, age, true)] => format!("{name} has not delivered today - last {age}"),
        [(name, age, false)] => format!("{name}: nothing yet today, last {age}"),
        many => {
            let names: Vec<&str> = many.iter().map(|(n, _, _)| n.as_str()).collect();
            let verb = if late { "have not delivered today" } else { "nothing yet today" };
            format!("{} collectors {verb}: {}", many.len(), names.join(", "))
        }
    };
    (text, late)
}

/// Has a daily collector missed its window?
///
/// The collector finishes by 12:30, so lateness is a TIME OF DAY rather than a
/// duration. "Older than 24 hours" would report a feed as fine until 12:29 the
/// following day - long after anyone could have acted - and would then heal
/// itself at midnight with nothing fixed.
///
/// An hour of grace past the usual finish: 12:30 is when it normally ends, not
/// a promise, and a long morning is not a dead collector.
///
/// NEVER TRUE WITHOUT A STAMP. A source that has delivered nothing at all is
/// not late, it is unused - and the caller only asks this about collectors
/// somebody has configured.
pub fn source_is_late(stamp: Option<&str>, now_local_secs: i64) -> bool {
    let Some(raw) = stamp.map(str::trim).filter(|s| !s.is_empty()) else {
        return false;
    };
    let Some(age) = crate::date::seconds_since(raw) else {
        return false;
    };
    is_late_at(age, now_local_secs)
}

/// The rule itself, given an age in seconds and how far into the day it is.
///
/// SEPARATE FROM THE CLOCK so it can be tested at all. Folded into
/// source_is_late, every assertion here would hold only at certain hours: the
/// same input is honestly not late at 09:00 and late at 14:00, and a test that
/// passes before lunch and fails after it teaches people to ignore the suite.
fn is_late_at(age_secs: i64, today_secs: i64) -> bool {
    const GRACE_SECS: i64 = 3_600;
    // Seconds from local midnight to the usual finish, 12:30.
    const DEADLINE_SECS: i64 = 12 * 3_600 + 30 * 60;

    if today_secs < DEADLINE_SECS + GRACE_SECS {
        // Before the window closes, nothing is late yet.
        return false;
    }
    // LATE MEANS NOTHING ARRIVED TODAY, not "arrived before 12:30". The
    // earlier wording called a handover that landed at 12:00 late at 13:31 -
    // half an hour inside the window it had made. The deadline decides when it
    // is fair to ask the question; the answer is only ever about today.
    age_secs > today_secs
}

#[cfg(test)]
mod collector_file_tests {
    use super::{clock_after, collector_file_line};

    #[test]
    fn a_missing_file_says_so_instead_of_showing_an_age() {
        // Every age is about a file that is not there, so the age is not the
        // thing to say. Wants attention: nothing has ever arrived.
        let (text, attention) = collector_file_line("imported", false, None, None, None);
        assert_eq!(text, "imported: no file yet");
        assert!(attention);
    }

    #[test]
    fn an_unstamped_file_does_not_read_as_fresh() {
        // This is how a dead collector looked healthy: no stamp, no age, and
        // nothing on screen distinguishing that from "just arrived".
        let (text, attention) = collector_file_line("imported", true, None, Some(1.0), None);
        assert!(text.contains("age unknown"), "{text}");
        assert!(attention, "an unstamped file must not pass as current");
    }

    #[test]
    fn a_file_from_today_is_quiet() {
        let (text, attention) =
            collector_file_line("imported", true, Some(3.0), Some(3.2), None);
        assert!(text.contains("3h old"), "{text}");
        assert!(!attention, "a file written this morning is not news");
    }

    #[test]
    fn a_file_older_than_a_day_wants_attention() {
        // The collector runs daily, so a file that has not moved in 24 hours
        // is the case worth colouring.
        let (_, attention) = collector_file_line("imported", true, Some(23.0), Some(23.2), None);
        assert!(!attention);
        let (text, attention) = collector_file_line("imported", true, Some(30.0), Some(30.2), None);
        assert!(attention, "{text}");
    }

    #[test]
    fn days_are_used_once_hours_stop_being_readable() {
        let (text, _) = collector_file_line("imported", true, Some(72.0), Some(72.2), None);
        assert!(text.contains("3 days old"), "{text}");
    }

    #[test]
    fn the_next_look_is_a_clock_time_not_a_countdown() {
        // "in 2h 41m" makes a person do arithmetic against a collector that
        // finishes at 12:30.
        let (text, _) = collector_file_line(
            "imported", true, Some(3.0), Some(3.2), Some("14:00".to_string()));
        assert!(text.contains("next look 14:00"), "{text}");
    }

    #[test]
    fn a_file_newer_than_the_rows_is_waiting_to_be_taken_in() {
        // The collection has finished and this app has not read it - the
        // question "did the work complete" reduces to exactly this.
        let (text, attention) =
            collector_file_line("imported", true, Some(0.2), Some(23.0), None);
        assert!(text.contains("not taken in yet"), "{text}");
        assert!(attention);
    }

    #[test]
    fn the_line_says_when_the_data_was_last_taken_in() {
        // The file's age and the reading of it are different questions: one is
        // the collector's problem, the other is this app's. The line answered
        // only the first unless something was wrong.
        let (text, _) = collector_file_line("imported", true, Some(6.0), Some(2.0), None);
        assert!(text.contains("6h old"), "{text}");
        assert!(text.contains("taken in 2h ago"), "{text}");
    }

    #[test]
    fn a_reading_within_the_hour_is_not_reported_as_zero_hours() {
        let (text, _) = collector_file_line("imported", true, Some(3.0), Some(0.4), None);
        assert!(text.contains("within the hour"), "{text}");
        assert!(!text.contains("0h"), "{text}");
    }

    #[test]
    fn a_file_with_nothing_imported_yet_is_also_waiting() {
        let (text, _) = collector_file_line("imported", true, Some(2.0), None, None);
        assert!(text.contains("not taken in yet"), "{text}");
    }

    #[test]
    fn matching_ages_are_not_reported_as_waiting() {
        // The two clocks never agree exactly; an hour of tolerance stops the
        // line flickering between two readings of the same event.
        let (text, _) =
            collector_file_line("imported", true, Some(3.0), Some(3.4), None);
        assert!(!text.contains("not taken in"), "{text}");
    }

    #[test]
    fn a_clock_time_wraps_past_midnight() {
        // 23:30 plus two hours is 01:30 tomorrow, not 25:30.
        assert_eq!(clock_after(23 * 3_600 + 30 * 60, 2 * 3_600), "01:30");
        assert_eq!(clock_after(9 * 3_600, 5 * 3_600), "14:00");
    }
}

#[cfg(test)]
mod collected_line_tests {
    use super::{collected_line, collectors_line};

    #[test]
    fn the_line_says_which_collector_it_is_about() {
        // It used to read "Collected today" full stop, over a number that
        // covered every source - so a board sweep answered for a collector
        // that had not run.
        assert!(collected_line(None).contains("boards"));
        assert!(collected_line(Some("1999-01-01T00:00:00+00:00")).contains("Boards"));
    }

    #[test]
    fn nothing_is_said_when_every_collector_has_delivered() {
        // The discipline of the line: a healthy feed is not news, and a line
        // that recites them daily is one people stop reading.
        assert_eq!(collectors_line(&[]), (String::new(), false));
    }

    #[test]
    fn not_yet_today_reads_differently_from_overdue() {
        // The distinction the morning depends on. At 10:23 with the collector
        // due at 10:45, "nothing yet today" is a fact; the same words in amber
        // at 14:00 would be a false alarm, and silence at 14:00 would be worse.
        let waiting = vec![("imported".into(), "12h ago".into(), false)];
        let (text, late) = collectors_line(&waiting);
        assert!(text.contains("nothing yet today"), "{text}");
        assert!(!late, "not due yet is not a warning");

        let overdue = vec![("imported".into(), "2 days ago".into(), true)];
        let (text, late) = collectors_line(&overdue);
        assert!(text.contains("has not delivered today"), "{text}");
        assert!(late);
    }

    #[test]
    fn several_collectors_are_counted_and_named() {
        let pending = vec![
            ("imported".to_string(), "2 days ago".to_string(), true),
            ("partner".to_string(), "5 days ago".to_string(), false),
        ];
        let (line, late) = collectors_line(&pending);
        assert!(line.starts_with("2 collectors"), "{line}");
        assert!(line.contains("imported") && line.contains("partner"), "{line}");
        // One overdue among several colours the whole clause.
        assert!(late);
    }

    #[test]
    fn a_delivery_made_today_is_reported_as_today() {
        use super::{collected_state, Collected};
        // 15:00, delivered three hours ago.
        let now = 15 * 3_600;
        let stamp = crate::date::now_iso();
        assert_eq!(collected_state(Some(&stamp), now), Collected::Today);
        // A stamp that cannot be read is not evidence of a delivery.
        assert_eq!(collected_state(None, now), Collected::NotYet);
        assert_eq!(collected_state(Some("last Tuesday"), now), Collected::NotYet);
    }
}

#[cfg(test)]
mod lateness_tests {
    use super::{is_late_at, source_age, source_is_late};

    const AFTERNOON: i64 = 15 * 3_600;
    const MORNING: i64 = 9 * 3_600;

    #[test]
    fn nothing_is_late_before_the_window_closes() {
        // 09:00, and the last delivery was a week ago. Still not called late:
        // today's collector has not had its chance yet, and a badge that
        // appears every single morning is one people stop reading.
        assert!(!is_late_at(7 * 86_400, MORNING));
    }

    #[test]
    fn the_hour_of_grace_is_real() {
        // 13:00 - past 12:30, inside the hour of slack. A collector running
        // long is not a collector that died.
        assert!(!is_late_at(7 * 86_400, 13 * 3_600));
        // 13:31 is past the grace, and a week of silence now counts.
        assert!(is_late_at(7 * 86_400, 13 * 3_600 + 31 * 60));
    }

    #[test]
    fn a_delivery_made_today_is_never_late_today() {
        // 15:00, handover landed at 12:07 - which is what the real collector
        // did on the day this was built. Three hours later it must still read
        // as served.
        // This is the case the first version got wrong: 12:00 is before the
        // 12:30 deadline, and it read that as "predates the deadline" and so
        // late - twenty-nine minutes after the handover had made its window.
        assert!(!is_late_at(AFTERNOON - (12 * 3_600 + 7 * 60), AFTERNOON));
        // A collector that ran long and landed at 13:45 is still today's.
        assert!(!is_late_at(AFTERNOON - (13 * 3_600 + 45 * 60), AFTERNOON));
    }

    #[test]
    fn yesterdays_delivery_is_late_this_afternoon() {
        // Newest handover is from 12:07 YESTERDAY: today's never came. This is
        // the failure the panel exists to show - the count beside the source
        // stays true and healthy-looking while nothing new arrives.
        let yesterday_noon = AFTERNOON + 86_400 - (12 * 3_600 + 7 * 60);
        assert!(is_late_at(yesterday_noon, AFTERNOON));
    }

    #[test]
    fn a_source_that_never_delivered_is_never_late() {
        // Not late, unused. Only configured collectors are asked about at all
        // (dashboard_view::source_chart), but a stamp that cannot be read must
        // not turn into a warning either.
        assert!(!source_is_late(None, AFTERNOON));
        assert!(!source_is_late(Some("   "), AFTERNOON));
        assert!(!source_is_late(Some("last Tuesday"), AFTERNOON));
    }

    #[test]
    fn an_undatable_stamp_reads_as_never_not_now() {
        assert_eq!(source_age(None), "never");
        assert_eq!(source_age(Some("")), "never");
        assert_eq!(source_age(Some("2026-08-24")), "never");
    }
}

/// How long ago a source last delivered, in hours until it is not worth it.
///
/// SEPARATE FROM posted_age BECAUSE THE RESOLUTION MATTERS HERE. That one
/// answers "how old is this posting", where a day is the smallest unit anyone
/// acts on. This answers "is this feed alive", and a daily collector that
/// should have finished by lunchtime is still "today" for another twelve hours
/// - which is precisely the window a silent failure hides in.
///
/// An unparseable or absent stamp returns "never", not "now". A source we
/// cannot date is not a source we have just heard from.
pub fn source_age(stamp: Option<&str>) -> String {
    let Some(raw) = stamp.map(str::trim).filter(|s| !s.is_empty()) else {
        return "never".to_string();
    };
    let Some(secs) = crate::date::seconds_since(raw) else {
        return "never".to_string();
    };
    match secs {
        s if s < 0 => "just now".to_string(),
        s if s < 3_600 => format!("{}m ago", (s / 60).max(1)),
        s if s < 86_400 => format!("{}h ago", s / 3_600),
        s if s < 172_800 => "1 day ago".to_string(),
        s => format!("{} days ago", s / 86_400),
    }
}

/// How old a posting is, in the shortest form that still reads clearly.
///
/// A date tells a reader nothing without arithmetic, and freshness is the
/// thing that decides whether an application gets read at all - a posting
/// three days old and one from ten weeks ago are different propositions
/// filed under identically-shaped strings.
///
/// Handles the epoch-millisecond stamps Lever writes. Collected rows are
/// converted now, but rows collected before that fix still hold the raw
/// number, and parsing "1781023668000" as a calendar date yields the year
/// 1781 and an age of roughly ninety thousand days.
pub fn posted_age(raw: &str) -> String {
    let stamp = raw.trim();
    if stamp.is_empty() {
        return String::new();
    }
    let days = if stamp.chars().all(|c| c.is_ascii_digit()) && stamp.len() >= 12 {
        match stamp.parse::<i64>() {
            Ok(ms) => crate::date::days_since_epoch_ms(ms),
            Err(_) => return String::new(),
        }
    } else {
        match crate::date::days_since(stamp) {
            Some(d) => d,
            None => return String::new(),
        }
    };
    age_label(days)
}

/// Days to a label. Weeks up to two months, then months: past a certain age
/// the exact day stops mattering and the number just gets harder to read.
fn age_label(days: i64) -> String {
    match days {
        d if d < 0 => String::new(), // a future date is a board error, not an age
        0 => "today".to_string(),
        1 => "1 day".to_string(),
        d if d < 14 => format!("{d} days"),
        d if d < 60 => format!("{} wks", d / 7),
        d => format!("{} mo", d / 30),
    }
}

/// True once a posting is old enough that it is often already filled. Used
/// only to soften the row, never to hide or drop it - plenty of real
/// openings sit unfilled for months, and the reader decides.
pub fn is_stale(raw: &str) -> bool {
    let stamp = raw.trim();
    if stamp.is_empty() {
        return false;
    }
    let days = if stamp.chars().all(|c| c.is_ascii_digit()) && stamp.len() >= 12 {
        stamp
            .parse::<i64>()
            .map(crate::date::days_since_epoch_ms)
            .unwrap_or(0)
    } else {
        crate::date::days_since(stamp).unwrap_or(0)
    };
    days >= STALE_AFTER_DAYS
}

/// Four weeks. Chosen from our own corpus rather than a rule of thumb: the
/// measured gap between a seat being advertised and re-advertised clusters
/// past this point, which is the same thing seen from the other side - by a
/// month, the first round has usually failed or finished.
const STALE_AFTER_DAYS: i64 = 28;

/// Age of a posting in days, from whatever the board wrote. Shared by the
/// triage row and the dashboard's fresh-matches filter so the two can never
/// disagree about what "posted this week" means.
pub fn days_since_posted(raw: &str) -> Option<i64> {
    let stamp = raw.trim();
    if stamp.is_empty() {
        return None;
    }
    if stamp.chars().all(|c| c.is_ascii_digit()) && stamp.len() >= 12 {
        return stamp.parse::<i64>().ok().map(crate::date::days_since_epoch_ms);
    }
    crate::date::days_since(stamp)
}

/// "2026-07-24T13:02:11+00:00" -> "24 Jul". A date somebody reads down a
/// column, not a timestamp.
///
/// The year is left off deliberately: a job search runs in months, the column
/// is narrow, and a year on every row is noise that pushes the part that
/// varies off the edge. The full date is on the hover.
pub fn short_date(raw: &str) -> String {
    const MONTHS: [&str; 12] = [
        "Jan", "Feb", "Mar", "Apr", "May", "Jun",
        "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
    ];
    if raw.len() < 10 {
        return String::new();
    }
    let month: usize = match raw.get(5..7).and_then(|m| m.parse::<usize>().ok()) {
        Some(m) if (1..=12).contains(&m) => m,
        _ => return String::new(),
    };
    let day = raw.get(8..10).unwrap_or("").trim_start_matches('0');
    if day.is_empty() {
        return String::new();
    }
    format!("{day} {}", MONTHS[month - 1])
}

#[cfg(test)]
mod short_date_tests {
    use super::short_date;

    #[test]
    fn reads_as_a_date_a_person_would_write() {
        assert_eq!(short_date("2026-07-24T13:02:11+00:00"), "24 Jul");
        assert_eq!(short_date("2026-01-05"), "5 Jan");
    }

    #[test]
    fn nonsense_yields_nothing_rather_than_a_wrong_date() {
        assert_eq!(short_date(""), "");
        assert_eq!(short_date("not-a-date"), "");
        assert_eq!(short_date("2026-13-01"), "");
    }
}

/// The host a link points at: "https://jobs.lever.co/softdocs/5ea..." ->
/// "jobs.lever.co".
///
/// Shown in the list as the answer to "where did this come from", which the
/// source column answers only in our own vocabulary - "lever" is the
/// collector's name, not somewhere a person can go.
pub fn link_host(url: &str) -> String {
    let Some(authority) = authority_of(url) else {
        return String::new();
    };
    // Everything up to and including the LAST '@' is userinfo, not the host.
    // Splitting on the last one matters: a password may itself contain '@'.
    let host = authority
        .rsplit_once('@')
        .map(|(_, host)| host)
        .unwrap_or(authority);
    // An IPv6 literal is bracketed and full of colons, so the port can only be
    // stripped after the closing bracket.
    let host = match host.strip_prefix('[') {
        Some(rest) => return format!("[{}]", rest.split(']').next().unwrap_or(rest)),
        None => host.split(':').next().unwrap_or(host),
    };
    host.trim_start_matches("www.").to_lowercase()
}

/// The authority of an http(s) URL, or None for anything else.
///
/// The scheme check is deliberately here rather than at the call site: this
/// function and `safe_link` are the two places the desktop decides whether a
/// string is a link, and they must not be able to disagree.
fn authority_of(url: &str) -> Option<&str> {
    let rest = url
        .strip_prefix("https://")
        .or_else(|| url.strip_prefix("http://"))?;
    let authority = rest.split(['/', '?', '#']).next().unwrap_or("");
    (!authority.is_empty()).then_some(authority)
}

/// A URL this app will hand to the operating system, or None.
///
/// egui's `hyperlink_to` ends at `webbrowser::open`, which is the Windows
/// shell - so whatever reaches it gets opened with the handler registered for
/// its scheme. Job URLs arrive from remote JSON-LD, so without this a posting
/// could nominate `file://198.51.100.5/share/x` and one click on the job title
/// would hand over the person's NTLM credentials (found by a red-team review).
///
/// Both halves now refuse to STORE such a URL - the engine's collectors and
/// hand-add path, and `attachments::add_link` on this side - but this check is
/// not redundant: databases collected before those fixes already contain
/// whatever was offered, and this is the boundary that actually touches the OS.
pub fn safe_link(url: &str) -> Option<&str> {
    let trimmed = url.trim();
    authority_of(trimmed).map(|_| trimmed)
}

#[cfg(test)]
mod link_host_tests {
    use super::{link_host, safe_link};

    #[test]
    fn names_somewhere_a_person_could_actually_go() {
        assert_eq!(link_host("https://jobs.lever.co/softdocs/5eaba021"), "jobs.lever.co");
        assert_eq!(link_host("https://www.example.com/jobs/view/1"), "example.com");
        assert_eq!(link_host("https://boards.greenhouse.io/brex?x=1"), "boards.greenhouse.io");
    }

    #[test]
    fn userinfo_cannot_impersonate_a_host() {
        // The spoof this column was wide open to: the visible text said
        // greenhouse, the click went to evil.com, and the column CLIPS - so
        // what a person actually saw was "boards.greenhouse.io...".
        assert_eq!(
            link_host("https://boards.greenhouse.io@evil.com/jobs/1"),
            "evil.com"
        );
        assert_eq!(link_host("https://user:pa@ss@evil.com/x"), "evil.com");
    }

    #[test]
    fn a_port_is_not_part_of_the_host() {
        assert_eq!(link_host("http://careers.example.com:8443/jobs/1"), "careers.example.com");
        assert_eq!(link_host("http://[::1]:8080/jobs/1"), "[::1]");
    }

    #[test]
    fn only_http_is_a_link() {
        // Everything here would previously have rendered as a clickable link.
        for hostile in [
            "file://198.51.100.5/share/apply",
            "file:///C:/Windows/System32/calc.exe",
            "javascript:alert(1)",
            "ms-msdt:/id PCWDiagnostic",
            "not a url",
            "",
        ] {
            assert_eq!(safe_link(hostile), None, "{hostile} must not be openable");
            assert_eq!(link_host(hostile), "", "{hostile} must not name a host");
        }
    }

    #[test]
    fn a_real_posting_link_still_works() {
        let url = "https://jobs.lever.co/softdocs/5eaba021";
        assert_eq!(safe_link(url), Some(url));
        assert_eq!(safe_link("  https://boards.greenhouse.io/brex  "),
                   Some("https://boards.greenhouse.io/brex"));
    }
}
