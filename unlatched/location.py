"""location.py - Can this person actually get to this job?

Only ever asked about postings that are not remote: a remote role has no
commute to judge, so it never reaches here.

Employers write locations inconsistently ("Dayton, OH", "Dayton, Ohio",
"Dayton, OH, USA", "Amsterdam; London", or nothing at all with the city
buried in the description), so the comparison normalizes both sides rather
than trusting either format.

Two rules earn their keep:

  * A place name without its state is not enough. City names repeat across
    the country, so a posting in Springfield, Illinois must not satisfy
    someone who can reach Springfield, Missouri. When a posting states a state
    that contradicts the wanted one, it is refused.
  * Some employers are based in one area and send crews out. Someone who
    accepts that kind of work should still see those postings, which is
    what `travel_ok` allows: a posting whose stated location is unclear
    still qualifies when it talks about travel AND names the person's area
    somewhere in the description.
"""
from __future__ import annotations

import re

# State names and postal abbreviations, so "OH" and "Ohio" are one
# place. Nothing else in a location string is interpreted; the rest is
# matched literally.
STATES = {
    "alabama": "al", "alaska": "ak", "arizona": "az", "arkansas": "ar",
    "california": "ca", "colorado": "co", "connecticut": "ct", "delaware": "de",
    "florida": "fl", "georgia": "ga", "hawaii": "hi", "idaho": "id",
    "illinois": "il", "indiana": "in", "iowa": "ia", "kansas": "ks",
    "kentucky": "ky", "louisiana": "la", "maine": "me", "maryland": "md",
    "massachusetts": "ma", "michigan": "mi", "minnesota": "mn",
    "mississippi": "ms", "missouri": "mo", "montana": "mt", "nebraska": "ne",
    "nevada": "nv", "new hampshire": "nh", "new jersey": "nj",
    "new mexico": "nm", "new york": "ny", "north carolina": "nc",
    "north dakota": "nd", "ohio": "oh", "oklahoma": "ok", "oregon": "or",
    "pennsylvania": "pa", "rhode island": "ri", "south carolina": "sc",
    "south dakota": "sd", "tennessee": "tn", "texas": "tx", "utah": "ut",
    "vermont": "vt", "virginia": "va", "washington": "wa",
    "west virginia": "wv", "wisconsin": "wi", "wyoming": "wy",
    "district of columbia": "dc",
}
ABBREVS = set(STATES.values())

TRAVEL = re.compile(
    r"\b(travel|traveling|travelling|per\s?diem|road work|out of town|"
    r"project sites?|job sites?|field based|multiple sites?)\b", re.IGNORECASE)


def split_places(location: str) -> list[str]:
    """A posting can list several sites. Any one being reachable is enough."""
    return [part.strip() for part in re.split(r"[;|/]|\bor\b", location or "")
            if part.strip()]


def normalize(place: str) -> tuple[str, str]:
    """(cleaned text, state abbreviation). The state is empty when none was
    named, which is common and means unknown rather than mismatched.
    """
    lowered = (place or "").lower()
    # A DOTTED ABBREVIATION IS ONE TOKEN. Stripping punctuation first turns
    # "D.C." into "d c" - two single letters - so the state search below finds
    # no two-letter token and falls back to the full-name pass, where
    # "washington" matches and the District becomes Washington state again.
    # Joined before the strip, so "D.C." and "N.Y." survive as "dc" and "ny".
    lowered = re.sub(r"\b([a-z])\.\s*([a-z])\.", r"\1\2", lowered)
    text = re.sub(r"[^a-z0-9,\s]", " ", lowered)
    text = re.sub(r"\s+", " ", text).strip()

    # AN EXPLICIT ABBREVIATION WINS, AND THE LAST ONE WINS. Full state names
    # used to be checked first, in dictionary order, and the first hit ended
    # the search - which got the District of Columbia exactly backwards,
    # because "Washington, DC" contains "washington":
    #
    #   "Washington, DC"  ->  state "wa"
    #
    # So a District posting satisfied a Washington-STATE search (verified: a
    # job 2,300 miles away, which is the false positive the docstring above
    # says this function exists to prevent) and a District posting did NOT
    # satisfy a "DC" search - so somebody in Washington DC filtering on their
    # own city saw none of it. That is the worse half, and this app ships a
    # USAJOBS collector aimed at the densest federal job market there is.
    #
    # The same reversal hits every town named after another state: Oregon OH,
    # Indiana PA, Nevada MO, Delaware OH. All of them read as the state they
    # are named after rather than the one they are in.
    #
    # LAST rather than first, because a two-letter word earlier in the string
    # can collide with an abbreviation - "La Crosse, WI" opens with "la",
    # which is Louisiana. The state is written at the end.
    state = ""
    for token in re.findall(r"\b([a-z]{2})\b", text):
        if token in ABBREVS:
            state = token
    if not state:
        for name, abbrev in STATES.items():
            if re.search(r"\b" + re.escape(name) + r"\b", text):
                state = abbrev
                break
    return text, state


def city_of(wanted: str) -> str:
    """The city part of a wanted entry, with the state removed. An entry of
    just a state returns an empty string, which callers read as
    "anywhere in that state".
    """
    text, state = normalize(wanted)
    city = text.replace(",", " ")
    if state:
        city = re.sub(r"\b" + re.escape(state) + r"\b", " ", city)
        for name, abbrev in STATES.items():
            if abbrev == state:
                city = re.sub(r"\b" + re.escape(name) + r"\b", " ", city)
    return re.sub(r"\s+", " ", city).strip()


def place_is_acceptable(job_place: str, wanted: str) -> bool:
    """Does one posting location satisfy one entry from search.locations?"""
    job_text, job_state = normalize(job_place)
    if not job_text:
        return False
    want_city = city_of(wanted)
    _want_text, want_state = normalize(wanted)

    if not want_city:
        return bool(want_state) and job_state == want_state
    if job_state and want_state and job_state != want_state:
        return False
    return re.search(r"\b" + re.escape(want_city) + r"\b", job_text) is not None


def is_commutable(location: str, description: str, wanted: list[str],
                   travel_ok: bool = False) -> tuple[bool, str]:
    """Returns (ok, reason). With no locations configured, this is not a
    question the user asked and everything passes.
    """
    places = [w for w in wanted if w and w.strip()]
    if not places:
        return True, ""

    candidates = split_places(location)
    for candidate in candidates:
        for want in places:
            if place_is_acceptable(candidate, want):
                return True, f"location: {candidate}"

    head = (description or "")[:6000]
    named_here = next(
        (city_of(w) for w in places
         if city_of(w) and re.search(r"\b" + re.escape(city_of(w)) + r"\b",
                                      head, re.IGNORECASE)), "")

    if travel_ok and named_here and TRAVEL.search(head):
        return True, f"travel role based near {named_here}"

    if not candidates:
        if named_here:
            return True, f"location named in the description: {named_here}"
        return False, "no location stated and none found in the description"

    return False, f"location {location.strip()!r} is outside search.locations"
