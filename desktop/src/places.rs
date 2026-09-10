//! US place names, bundled, so a typed location is spelled the way employers
//! spell it.
//!
//! Asked for directly (2026-08-05): a location typed wrong throws off the whole
//! search, silently - "Sacremento, CA" matches nothing and looks exactly like a
//! market with no jobs in it. Suggestions make the correct spelling the easy
//! one to pick.
//!
//! The list is the Census Gazetteer's PLACES file, which covers incorporated
//! places and census-designated places both. That second half is the point:
//! plenty of the communities employers name in postings are census-designated
//! rather than incorporated, and a cities-only list would not have them. See
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
            .flat_map(|line| {
                let mut entries = vec![(normalise(line), line)];
                if let Some(short) = city_and_state(line) {
                    entries.push((short, line));
                }
                entries
            })
            .collect()
    })
}

/// "Lynchburg, Moore, TN" -> "lynchburg tn", or None for an ordinary name.
///
/// A handful of Gazetteer names carry an administrative middle: Lynchburg is a
/// consolidated city-county, and Islamorada's legal name really is "Islamorada,
/// Village of Islands". Both are correct, and neither is what somebody types.
/// Without this the natural spelling - city and state, the way every other
/// place in the file is written - is the one spelling that finds nothing,
/// while typing the city alone works.
///
/// The full name is still what gets shown and inserted. This only adds a
/// second way to reach it.
fn city_and_state(line: &str) -> Option<String> {
    let parts: Vec<&str> = line.split(',').map(str::trim).collect();
    if parts.len() < 3 {
        return None;
    }
    Some(normalise(&format!("{} {}", parts[0], parts[parts.len() - 1])))
}

/// Comparison form: lowercase, and commas dropped so "powell oh" finds
/// "Powell, OH". People type the comma about half the time.
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
    // so "springf" leads with Springfield, MO rather than Springfield, IL, and
    // nothing here has to carry size data to know that. See
    // data/build_us_places.py.
    //
    // DEDUPED, because a place with a middle segment has two keys and a
    // query like "lynchburg" matches both of them. Every other row has one
    // key and cannot collide, so this changes nothing for them.
    let mut starts: Vec<&'static str> = Vec::new();
    let mut contains: Vec<&'static str> = Vec::new();
    for (key, original) in index() {
        if key.starts_with(&query) {
            if !starts.contains(original) {
                starts.push(original);
            }
            if starts.len() >= limit {
                break;
            }
        } else if contains.len() < limit
            && key.contains(&query)
            && !contains.contains(original)
        {
            contains.push(original);
        }
    }

    // A name typed in the middle ("ridge") is a worse match than one typed
    // from the start, so those fill the remaining room and never displace.
    // The second pass catches a place that reached starts by one key and
    // contains by the other.
    let mut out: Vec<&'static str> = Vec::new();
    for candidate in starts.into_iter().chain(contains) {
        if !out.contains(&candidate) {
            out.push(candidate);
        }
        if out.len() >= limit {
            break;
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn finds_the_small_places_that_are_the_whole_point() {
        // The case that matters: smaller communities that employers name in
        // postings, which a cities-only list would not carry at all.
        assert!(suggest("powell", 8).contains(&"Powell, OH"));
        assert!(suggest("seymour", 8).contains(&"Seymour, IN"));
    }

    #[test]
    fn a_typed_comma_is_optional() {
        assert!(suggest("springfield, mo", 8).contains(&"Springfield, MO"));
        assert!(suggest("springfield mo", 8).contains(&"Springfield, MO"));
    }

    #[test]
    fn the_obvious_answer_comes_first() {
        // Dozens of states have a Springfield. Somebody typing it in
        // Missouri should not have to hunt past Illinois for theirs.
        assert_eq!(suggest("springf", 8).first(), Some(&"Springfield, MO"));
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
        // refuses to resolve - Clinton, IA must never satisfy Clinton, MS.
        //
        // EVERY LINE, not the first two thousand. This took a `.take(2000)`,
        // which checks the largest places - the ones least likely to be
        // malformed - and said nothing about the other 29,000, where a
        // regenerated file would actually go wrong. The whole pass is a
        // string compare over 31,000 short lines and costs nothing.
        let mut checked = 0;
        for (_, line) in index().iter() {
            let (_, state) = line.rsplit_once(", ").unwrap_or_else(|| {
                panic!("{line} has no state");
            });
            assert!(
                state.len() == 2 && state.chars().all(|c| c.is_ascii_uppercase()),
                "{line} does not end in a two-letter state code"
            );
            checked += 1;
        }
        assert!(checked > 30_000, "the bundled list shrank to {checked} entries");
    }

    #[test]
    fn a_name_with_an_administrative_middle_is_reachable_by_city_and_state() {
        // Lynchburg is a consolidated city-county and Islamorada's legal name
        // includes "Village of Islands", so both index as three words. Typing
        // city and state - the way every other place in the file is written -
        // found neither of them, while typing the city alone worked.
        assert!(suggest("lynchburg tn", 8).contains(&"Lynchburg, Moore, TN"));
        assert!(suggest("lynchburg, tn", 8).contains(&"Lynchburg, Moore, TN"));
        assert!(
            suggest("islamorada fl", 8).contains(&"Islamorada, Village of Islands, FL")
        );
    }

    #[test]
    fn the_full_census_name_is_still_what_gets_inserted() {
        // The second index entry is a way IN, not a rename. What the app puts
        // in the box has to stay the government's own spelling, because that
        // is the entire reason the list is bundled.
        let hits = suggest("lynchburg tn", 8);
        assert_eq!(hits, vec!["Lynchburg, Moore, TN"]);
    }

    #[test]
    fn a_place_with_two_keys_is_only_offered_once() {
        // "lynchburg" matches BOTH of its keys - "lynchburg moore tn" and the
        // added "lynchburg tn" - so without deduping it would be suggested
        // twice, which reads as two different towns with the same name.
        let hits = suggest("lynchburg", 8);
        let times = hits.iter().filter(|h| **h == "Lynchburg, Moore, TN").count();
        assert_eq!(times, 1, "offered {times} times: {hits:?}");
    }

    #[test]
    fn ordinary_names_gain_no_second_entry() {
        // The alias exists only for names carrying a middle segment. A plain
        // "City, ST" must not gain a duplicate key, or every one of the 31,000
        // rows would double the index for nothing.
        assert_eq!(city_and_state("Powell, OH"), None);
        assert_eq!(city_and_state("Springfield, MO"), None);
        assert_eq!(
            city_and_state("Lynchburg, Moore, TN"),
            Some("lynchburg tn".to_string())
        );
    }
}
