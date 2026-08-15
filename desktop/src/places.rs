//! US place names, bundled, so a typed location is spelled the way employers
//! spell it.
//!
//! The first user asked for this directly (2026-08-05): a location typed wrong throws
//! off the whole search, silently - "Knoxvile, TN" matches nothing and looks
//! exactly like a market with no jobs in it. Suggestions make the correct
//! spelling the easy one to pick.
//!
//! The list is the Census Gazetteer's PLACES file, which covers incorporated
//! places and census-designated places both. That second half is the point:
//! Powell and Seymour are Knoxville-area communities that employers write in
//! postings and that a cities-only list would not have. See
//! data/build_us_places.py for how the file is rebuilt.
//!
//! Compiled in rather than read from disk. It is 434 KB, it never changes
//! between releases, and a data file next to the exe is a data file that can
//! go missing - at which point the feature fails on the machine it shipped
//! to rather than on the machine it was built on.

use std::sync::OnceLock;

const RAW: &str = include_str!("../data/us_places.txt");

/// Lowercased, comma-free forms of every place, paired with what to insert.
/// Built once on first use: 31,000 short strings, a few milliseconds, and
/// nothing at all for somebody who never opens Config.
fn index() -> &'static Vec<(String, &'static str)> {
    static INDEX: OnceLock<Vec<(String, &'static str)>> = OnceLock::new();
    INDEX.get_or_init(|| {
        RAW.lines()
            .map(str::trim)
            .filter(|line| !line.is_empty())
            .map(|line| (normalise(line), line))
            .collect()
    })
}

/// Comparison form: lowercase, and commas dropped so "powell tn" finds
/// "Powell, TN". People type the comma about half the time.
fn normalise(text: &str) -> String {
    text.chars()
        .filter(|c| *c != ',')
        .flat_map(char::to_lowercase)
        .collect()
}

/// Up to `limit` places matching what has been typed, best first.
///
/// Empty for a query under two characters: one letter matches thousands of
/// places, and a list of thousands is not a suggestion.
pub fn suggest(typed: &str, limit: usize) -> Vec<&'static str> {
    let query = normalise(typed.trim());
    if query.len() < 2 {
        return Vec::new();
    }

    // FILE ORDER IS THE RANKING - the list is written largest place first,
    // so "knoxv" leads with Knoxville, TN rather than Knoxville, AR, and
    // nothing here has to carry size data to know that. See
    // data/build_us_places.py.
    let mut starts: Vec<&'static str> = Vec::new();
    let mut contains: Vec<&'static str> = Vec::new();
    for (key, original) in index() {
        if key.starts_with(&query) {
            starts.push(original);
            if starts.len() >= limit {
                break;
            }
        } else if contains.len() < limit && key.contains(&query) {
            contains.push(original);
        }
    }

    // A name typed in the middle ("ridge") is a worse match than one typed
    // from the start, so those fill the remaining room and never displace.
    starts.into_iter().chain(contains).take(limit).collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn finds_the_small_places_that_are_the_whole_point() {
        // these two are the case that matters: Knoxville-area, not Knoxville, and both are
        // census-designated places rather than incorporated cities.
        assert!(suggest("powell", 8).contains(&"Powell, TN"));
        assert!(suggest("seymour", 8).contains(&"Seymour, TN"));
    }

    #[test]
    fn a_typed_comma_is_optional() {
        assert!(suggest("knoxville, tn", 8).contains(&"Knoxville, TN"));
        assert!(suggest("knoxville tn", 8).contains(&"Knoxville, TN"));
    }

    #[test]
    fn the_obvious_answer_comes_first() {
        // Nine states have a Knoxville. Somebody typing it in Tennessee
        // should not have to hunt past Arkansas and Iowa for theirs.
        assert_eq!(suggest("knoxv", 8).first(), Some(&"Knoxville, TN"));
        assert_eq!(suggest("chicag", 8).first(), Some(&"Chicago, IL"));
        assert_eq!(suggest("portland", 8).first(), Some(&"Portland, OR"));
    }

    #[test]
    fn typing_the_state_narrows_it() {
        assert_eq!(suggest("springfield mo", 8), vec!["Springfield, MO"]);
        assert_eq!(suggest("springfield, mo", 8), vec!["Springfield, MO"]);
    }

    #[test]
    fn one_letter_suggests_nothing() {
        assert!(suggest("k", 8).is_empty());
        assert!(suggest("", 8).is_empty());
    }

    #[test]
    fn never_returns_more_than_asked_for() {
        assert!(suggest("spring", 5).len() <= 5);
    }

    #[test]
    fn every_line_carries_a_state() {
        // A place without its state is exactly the ambiguity location.py
        // refuses to resolve - Clinton, NJ must never satisfy Clinton, TN.
        for (_, line) in index().iter().take(2000) {
            assert!(line.contains(", "), "{line} has no state");
        }
    }
}
