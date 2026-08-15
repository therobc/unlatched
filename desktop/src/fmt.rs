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
/// How current what you are looking at is.
///
/// One function, read by the dashboard AND by the job list. The list had no
/// freshness line at all - the one screen where a person is actually deciding
/// whether to apply to something, and nothing on it said how old the pile was
/// (decided 2026-08-07). Sharing the wording also stops the two screens from
/// describing the same collection differently.
pub fn collected_line(last_collected: Option<&str>) -> String {
    let Some(stamp) = last_collected else {
        return "Nothing collected yet".to_string();
    };
    let age = posted_age(stamp);
    if age.is_empty() {
        "Collected recently".to_string()
    } else if age == "today" {
        "Collected today".to_string()
    } else {
        format!("Last collected {age} ago")
    }
}

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
/// The engine now refuses to STORE such a URL, but this check is not redundant:
/// databases collected before that fix already contain whatever was offered,
/// and this is the boundary that actually touches the OS.
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
        assert_eq!(link_host("https://www.linkedin.com/jobs/view/1"), "linkedin.com");
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
