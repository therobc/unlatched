// Minimal calendar-date math so the app does not need a date/time crate
// just to stamp a status update and show "N days ago".
//
// The civil <-> day-count conversions below are the well known
// days_from_civil / civil_from_days algorithms (proleptic Gregorian,
// valid across the full i64 range); they are reproduced here from their
// public description rather than pulled in as a dependency.

use std::time::{SystemTime, UNIX_EPOCH};

fn days_from_civil(y: i64, m: i64, d: i64) -> i64 {
    let y = if m <= 2 { y - 1 } else { y };
    let era = if y >= 0 { y } else { y - 399 } / 400;
    let yoe = y - era * 400;
    let mp = if m > 2 { m - 3 } else { m + 9 };
    let doy = (153 * mp + 2) / 5 + d - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    era * 146097 + doe - 719468
}

fn civil_from_days(z: i64) -> (i64, u32, u32) {
    let z = z + 719468;
    let era = if z >= 0 { z } else { z - 146096 } / 146097;
    let doe = z - era * 146097;
    let yoe = (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365;
    let y = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = (doy - (153 * mp + 2) / 5 + 1) as u32;
    let m = if mp < 10 { mp + 3 } else { mp - 9 } as u32;
    let y = if m <= 2 { y + 1 } else { y };
    (y, m, d)
}

/// Current UTC time as "YYYY-MM-DDTHH:MM:SS". Both front ends only need a
/// sortable, unambiguous string in the TEXT status columns; neither side
/// depends on sub-second precision or a timezone suffix.
pub fn now_iso() -> String {
    let secs = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs() as i64)
        .unwrap_or(0);
    let day = secs.div_euclid(86400);
    let tod = secs.rem_euclid(86400);
    let (y, m, d) = civil_from_days(day);
    let (h, mi, s) = (tod / 3600, (tod % 3600) / 60, tod % 60);
    format!("{:04}-{:02}-{:02}T{:02}:{:02}:{:02}", y, m, d, h, mi, s)
}

fn today_day_count() -> i64 {
    let secs = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs() as i64)
        .unwrap_or(0);
    secs.div_euclid(86400)
}

/// Days between now and a stamp whose first 10 characters are "YYYY-MM-DD".
/// Any timezone offset or fractional-second suffix on the stamp is ignored;
/// this is a display convenience, not a precise duration.
pub fn days_since(stamp: &str) -> Option<i64> {
    if stamp.len() < 10 {
        return None;
    }
    let y: i64 = stamp.get(0..4)?.parse().ok()?;
    let m: i64 = stamp.get(5..7)?.parse().ok()?;
    let d: i64 = stamp.get(8..10)?.parse().ok()?;
    let then = days_from_civil(y, m, d);
    Some(today_day_count() - then)
}

/// Days between now and an epoch-millisecond stamp. Lever writes createdAt
/// this way, and rows collected before that was normalised still hold it.
pub fn days_since_epoch_ms(ms: i64) -> i64 {
    today_day_count() - ms.div_euclid(86_400_000)
}
