"""country.py - Is this posting in a country the person can actually work in?

Found against real results: 41 of 133 matches in a US-only search were
foreign - "Remote - India", "Remote - UK", "Remote - Mexico", Barcelona,
Warsaw, Pune, Sydney. Two things let them through.

A remote-only search does not exclude them, because "Remote - India" IS
remote; remoteness says nothing about jurisdiction. And 33 of the 41 listed
no salary, so the pay floor - the accidental filter that catches some of
these - never engaged. The eight that did list pay were Canadian and
Australian roles whose figures cleared a USD floor while being quoted in CAD
and AUD.

The country is also frequently in the TITLE rather than the location field:
a posting titled "Support Engineer, Singapore" can carry location "(Remote)",
which a location-only check reads as a fine US remote role.

FALSE POSITIVES ARE THE HARD PART
---------------------------------
Ontario is in California. London and Paris are in Texas; Birmingham is in
Alabama; Toronto is in Ohio. So a bare city name is never evidence on its
own - a US state in the same place string always wins, the same rule
location.py uses to keep Clinton TN from matching Clinton NJ. Only an
explicit country, a country code, or a foreign city WITHOUT a US state
counts.

Silence is never foreign. A posting that names no place at all is unknown,
not excluded - the same not-stated-vs-not-required rule the rest of the
package follows.
"""
from __future__ import annotations

import re

from unlatched.location import ABBREVS, STATES, split_places

# Countries and regions, spelled out. Matched as whole words so "India" does
# not fire inside "Indiana" and "Chile" does not fire inside "Chilean".
_COUNTRY_WORDS = (
    r"canada|mexico|brazil|argentina|colombia|chile|peru|"
    r"united kingdom|england|scotland|wales|northern ireland|ireland|"
    r"germany|france|spain|portugal|italy|netherlands|belgium|switzerland|"
    r"austria|poland|czechia|czech republic|hungary|romania|bulgaria|greece|"
    r"sweden|norway|denmark|finland|iceland|estonia|latvia|lithuania|"
    r"ukraine|serbia|croatia|slovakia|slovenia|"
    r"india|china|japan|singapore|malaysia|indonesia|thailand|vietnam|"
    r"philippines|south korea|taiwan|hong kong|"
    r"australia|new zealand|"
    r"israel|turkey|united arab emirates|saudi arabia|egypt|"
    r"south africa|nigeria|kenya|morocco|"
    r"costa rica|panama|uruguay|dominican republic"
)
COUNTRY = re.compile(rf"\b(?:{_COUNTRY_WORDS})\b", re.IGNORECASE)

# Country codes as postings actually write them ("Remote - UK"). Matched
# case-SENSITIVELY, and the ones colliding with US state abbreviations are
# deliberately absent: "IN" is Indiana before India and "DE" is Delaware
# before Germany.
COUNTRY_CODE = re.compile(
    r"\b(?:UK|U\.K\.|GBR|CAN|AUS|NZL|IRL|DEU|FRA|ESP|PRT|ITA|NLD|BEL|CHE|"
    r"AUT|POL|CZE|SWE|NOR|DNK|FIN|IND|CHN|JPN|SGP|MYS|PHL|IDN|THA|VNM|KOR|"
    r"TWN|HKG|ISR|TUR|ARE|SAU|ZAF|BRA|ARG|MEX|COL|CHL|PER|CRI)\b")

# Multi-country regions. A role posted to "Remote - EMEA" is not a US role.
REGION = re.compile(r"\b(?:EMEA|APAC|LATAM|ANZ|MENA|DACH|BENELUX)\b")

# Foreign cities common in tech postings. Only consulted when NO US state
# appears in the same place string, because several have US namesakes.
_FOREIGN_CITY_WORDS = (
    r"toronto|vancouver|montreal|ottawa|calgary|"
    r"london|manchester|edinburgh|dublin|belfast|"
    r"berlin|munich|hamburg|frankfurt|d[uü]sseldorf|cologne|stuttgart|"
    r"paris|lyon|marseille|amsterdam|rotterdam|brussels|zurich|geneva|vienna|"
    r"madrid|barcelona|valencia|lisbon|porto|milan|rome|turin|"
    r"warsaw|krakow|prague|budapest|bucharest|sofia|athens|"
    r"stockholm|oslo|copenhagen|helsinki|tallinn|riga|vilnius|"
    r"bangalore|bengaluru|hyderabad|pune|mumbai|chennai|delhi|noida|gurgaon|"
    r"beijing|shanghai|shenzhen|tokyo|osaka|seoul|taipei|"
    r"sydney|melbourne|brisbane|perth|auckland|wellington|"
    r"tel aviv|dubai|abu dhabi|istanbul|cairo|"
    r"s[aã]o paulo|rio de janeiro|buenos aires|bogot[aá]|"
    r"mexico city|guadalajara|monterrey|san jos[eé] costa rica"
)
FOREIGN_CITY = re.compile(rf"\b(?:{_FOREIGN_CITY_WORDS})\b", re.IGNORECASE)

# Explicit US markers. Any of these in a place string settles it as domestic,
# whatever city name sits alongside.
US_MARKER = re.compile(
    r"\b(?:usa|u\.s\.a\.|united states|u\.s\.|us remote|remote us|"
    r"remote - us|america)\b", re.IGNORECASE)


def _names_a_us_state(place: str) -> bool:
    lowered = place.lower()
    if any(re.search(rf"\b{re.escape(name)}\b", lowered) for name in STATES):
        return True
    # ABBREVS holds lowercase values while a location writes them uppercase,
    # so comparing them as stored matched nothing - "London, KY" read as
    # foreign. Compared as UPPERCASE rather than case-insensitively on
    # purpose: bare "in" and "or" are ordinary English words, while "IN" and
    # "OR" in a place string are Indiana and Oregon.
    return any(re.search(rf"\b{abbrev.upper()}\b", place) for abbrev in ABBREVS)


def foreign_evidence(text: str) -> str:
    """The phrase showing this place is outside the US, or "" if none.

    A US marker or a US state anywhere in the string wins outright: "Ontario,
    California" and "London, KY" are domestic, and reading the city alone
    would call them foreign.
    """
    place = (text or "").strip()
    if not place:
        return ""
    if US_MARKER.search(place) or _names_a_us_state(place):
        return ""
    for pattern in (COUNTRY, COUNTRY_CODE, REGION, FOREIGN_CITY):
        match = pattern.search(place)
        if match:
            return match.group(0)
    return ""


def is_foreign(location: str, title: str = "") -> tuple[bool, str]:
    """(foreign, evidence) for a posting.

    Each comma-separated place is judged on its own, so a multi-site posting
    listing "Austin, TX; London, England" is not excused by the domestic half
    - but equally, one US location among several is enough to keep it, since
    the person could take that one.

    The TITLE is checked too. "Support Engineer, Singapore" routinely carries
    location "(Remote)", and a location-only check reads that as a US remote
    role.
    """
    places = split_places(location or "")
    foreign_places = [foreign_evidence(place) for place in places]
    if places and all(foreign_places):
        return True, foreign_places[0]

    # The location did not settle it. A bare "(Remote)" or "Remote" says
    # nothing about jurisdiction, so the title gets a look - "Support
    # Engineer, Singapore" routinely carries exactly that location. Skipped
    # when the location explicitly said US, which outranks a title.
    if not any(US_MARKER.search(place) or _names_a_us_state(place) for place in places):
        from_title = foreign_evidence(title or "")
        if from_title:
            return True, from_title
    return False, ""


def accepted(location: str, title: str, us_only: bool) -> tuple[bool, str]:
    """Returns (ok, reason). With `us_only` off nothing is filtered."""
    if not us_only:
        return True, ""
    foreign, evidence = is_foreign(location, title)
    if foreign:
        return False, f"outside the US ({evidence})"
    return True, ""
