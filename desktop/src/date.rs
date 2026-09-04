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

/// Seconds between now and an ISO stamp, honouring its UTC offset.
///
/// WHY NOT days_since. That one reads the first ten characters and ignores any
/// offset, which is correct for a posting date - a day is the unit and nobody
/// acts on the difference. This measures whether a feed is alive, where four
/// hours is the whole question, and this app holds stamps in BOTH shapes:
/// jobs.fetched_at is written "+00:00" and a collector stamps its handoff
/// "-04:00". Dropping the suffix would age a fresh local stamp by the offset.
///
/// Returns None for anything it cannot read. A stamp we cannot date is not a
/// stamp we have just received, and the caller must say so rather than
/// defaulting to now.
pub fn seconds_since(stamp: &str) -> Option<i64> {
    let s = stamp.trim();
    if s.len() < 19 {
        return None;
    }
    let y: i64 = s.get(0..4)?.parse().ok()?;
    let mo: i64 = s.get(5..7)?.parse().ok()?;
    let d: i64 = s.get(8..10)?.parse().ok()?;
    let h: i64 = s.get(11..13)?.parse().ok()?;
    let mi: i64 = s.get(14..16)?.parse().ok()?;
    let sec: i64 = s.get(17..19)?.parse().ok()?;

    let mut epoch = days_from_civil(y, mo, d) * 86_400 + h * 3_600 + mi * 60 + sec;

    // The offset, when there is one. Anything after the seconds: "Z", "+00:00",
    // "-04:00", or a fractional part followed by one of those.
    let tail = &s[19..];
    if let Some(idx) = tail.find(['+', '-']) {
        let off = &tail[idx..];
        if off.len() >= 6 {
            if let (Ok(oh), Ok(om)) = (off[1..3].parse::<i64>(), off[4..6].parse::<i64>()) {
                let delta = oh * 3_600 + om * 60;
                // A stamp at -04:00 is FOUR HOURS LATER in UTC than its digits
                // read, so the offset is subtracted to get back to UTC.
                epoch += if off.starts_with('-') { delta } else { -delta };
            }
        }
    }

    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|x| x.as_secs() as i64)
        .unwrap_or(0);
    Some(now - epoch)
}

/// Seconds elapsed since LOCAL midnight, given the local UTC offset.
///
/// Everything else in this app is UTC on purpose, and this is the one place
/// that cannot be: "the collector finishes by 12:30" is a wall clock in the
/// house it runs in. Counted in UTC here, that window would open at 09:30
/// local and put a stale badge beside a collector that was not yet due, every
/// morning of the year.
///
/// The offset is passed in rather than looked up - see
/// dashboard::local_offset_secs for where it comes from and why.
pub fn seconds_into_local_day(offset_secs: i64) -> i64 {
    let secs = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs() as i64)
        .unwrap_or(0);
    (secs + offset_secs).rem_euclid(86_400)
}

/// Days between now and an epoch-millisecond stamp. Lever writes createdAt
/// this way, and rows collected before that was normalised still hold it.
pub fn days_since_epoch_ms(ms: i64) -> i64 {
    today_day_count() - ms.div_euclid(86_400_000)
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The transcribed algorithm, against dates whose day number is known
    /// independently of it - the epoch itself, the leap day, and the century
    /// year that IS a leap year where the naive rule says it is not.
    #[test]
    fn the_civil_conversion_agrees_with_known_dates() {
        assert_eq!(days_from_civil(1970, 1, 1), 0);
        assert_eq!(days_from_civil(1970, 1, 2), 1);
        assert_eq!(days_from_civil(1969, 12, 31), -1);
        // 2000 is divisible by 400, so it has a February 29th; 1900 does not.
        assert_eq!(civil_from_days(days_from_civil(2000, 2, 29)), (2000, 2, 29));
        assert_eq!(days_from_civil(1900, 3, 1) - days_from_civil(1900, 2, 28), 1);
        assert_eq!(days_from_civil(2000, 3, 1) - days_from_civil(2000, 2, 28), 2);
    }

    /// Every day across a span that covers leap years, century boundaries and
    /// both sides of the epoch has to survive the round trip. A conversion
    /// that is wrong by one on some days would age a posting by a day at a
    /// time nobody could reproduce.
    #[test]
    fn every_day_round_trips() {
        for day in -30_000..30_000 {
            let (y, m, d) = civil_from_days(day);
            assert_eq!(days_from_civil(y, m as i64, d as i64), day, "day {day}");
            assert!((1..=12).contains(&m), "day {day} gave month {m}");
            assert!((1..=31).contains(&d), "day {day} gave day {d}");
        }
    }

    /// THE OFFSET DIRECTION, which is the thing in this file most easily got
    /// backwards - and getting it backwards ages a fresh stamp by the offset,
    /// which is how a live collector reads as hours stale.
    ///
    /// The oracle is independent of the arithmetic: these are the SAME INSTANT
    /// written three ways, so whatever "now" happens to be, all three must
    /// come back equal.
    #[test]
    fn one_instant_spelled_three_ways_is_one_answer() {
        let utc = seconds_since("2026-01-01T12:00:00+00:00").expect("utc");
        let zulu = seconds_since("2026-01-01T12:00:00Z").expect("zulu");
        let eastern = seconds_since("2026-01-01T08:00:00-04:00").expect("eastern");
        let ahead = seconds_since("2026-01-01T14:30:00+02:30").expect("ahead");
        assert_eq!(utc, zulu);
        assert_eq!(utc, eastern, "a -04:00 stamp is four hours later in UTC");
        assert_eq!(utc, ahead, "a +02:30 stamp is two and a half hours earlier");
    }

    /// An hour apart is 3600 seconds apart, whichever way the offsets are
    /// written - so the offset is applied, not merely tolerated.
    #[test]
    fn a_later_stamp_is_fewer_seconds_ago() {
        let earlier = seconds_since("2026-01-01T12:00:00+00:00").expect("earlier");
        let later = seconds_since("2026-01-01T13:00:00+00:00").expect("later");
        assert_eq!(earlier - later, 3_600);
    }

    /// A stamp that cannot be read is None, never zero. The caller's own
    /// comment turns on this: "a stamp we cannot date is not a stamp we have
    /// just received", and a 0 would read as one that arrived this second.
    #[test]
    fn an_unreadable_stamp_is_none_rather_than_now() {
        for bad in ["", "not a date", "2026-01-01", "2026-01-01T12:00", "x"] {
            assert!(seconds_since(bad).is_none(), "{bad:?} should not parse");
        }
        assert!(days_since("2026-01").is_none());
        assert!(days_since("").is_none());
    }

    /// A fractional-second part sits between the seconds and the offset, and
    /// both of the shapes this app stores have to survive it.
    #[test]
    fn a_fractional_second_does_not_swallow_the_offset() {
        let plain = seconds_since("2026-01-01T12:00:00+00:00").expect("plain");
        assert_eq!(seconds_since("2026-01-01T12:00:00.123456+00:00"), Some(plain));
        assert_eq!(seconds_since("2026-01-01T08:00:00.5-04:00"), Some(plain));
    }

    #[test]
    fn now_iso_is_the_shape_the_database_sorts_on() {
        let now = now_iso();
        assert_eq!(now.len(), 19, "{now}");
        assert_eq!(&now[4..5], "-");
        assert_eq!(&now[10..11], "T");
        assert_eq!(&now[13..14], ":");
        assert!(now.chars().filter(|c| *c == ':').count() == 2, "{now}");
        // Sortable as text is the whole contract: today must sort after any
        // earlier day, as plain strings.
        assert!(now.as_str() > "2020-01-01T00:00:00");
        // And it must be readable back by the function that reads stamps.
        assert_eq!(days_since(&now), Some(0));
    }

    #[test]
    fn an_epoch_millisecond_stamp_dates_the_same_day_as_its_iso_form() {
        // Lever writes createdAt in milliseconds. Zero is the epoch, so the
        // two routes to "how many days ago was 1970-01-01" have to agree.
        assert_eq!(days_since_epoch_ms(0), days_since("1970-01-01").unwrap());
        // And a millisecond before midnight still belongs to the day before.
        assert_eq!(
            days_since_epoch_ms(86_400_000 - 1),
            days_since("1970-01-01").unwrap()
        );
        assert_eq!(
            days_since_epoch_ms(86_400_000),
            days_since("1970-01-02").unwrap()
        );
    }

    /// Local midnight, from an offset. The wrap is the part that matters:
    /// an offset that pushes past midnight has to come back to the start of
    /// the day rather than going negative or past 86,400.
    #[test]
    fn seconds_into_the_local_day_always_lands_inside_one_day() {
        for offset in [-43_200, -14_400, 0, 3_600, 19_800, 50_400] {
            let secs = seconds_into_local_day(offset);
            assert!((0..86_400).contains(&secs), "offset {offset} gave {secs}");
        }
        // Twelve hours apart is twelve hours apart, modulo the day.
        let a = seconds_into_local_day(0);
        let b = seconds_into_local_day(3_600);
        assert_eq!((b - a).rem_euclid(86_400), 3_600);
    }
}
