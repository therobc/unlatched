"""Rebuilds us_places.txt from the US Census Gazetteer.

Run this, not a hand edit - the point of the file is that every spelling in
it is the government's own, so a typed location matches what employers write.

    python build_us_places.py            # downloads the pinned YEAR below
    python build_us_places.py local.zip  # or reads a Gazetteer zip you have

The year is PINNED rather than tracking today's date: the bundled file is
compiled into the app, so which edition produced it has to be a decision
somebody made, not whatever happened to be current on the machine that last
ran this. Bump YEAR to move it, and re-run the probes at the bottom.

Places, not cities: the Gazetteer's "places" file covers incorporated places
AND census-designated places. That second half is what makes the smaller
communities employers name in postings typeable at all - a cities-only list
would not carry them. About 30,000 in total.

Public domain data (17 U.S.C. 105), so it ships with the app.
"""
from __future__ import annotations

import io
import re
import sys
import urllib.request
import zipfile
from pathlib import Path

YEAR = 2023
URL = (f"https://www2.census.gov/geo/docs/maps-data/data/gazetteer/"
       f"{YEAR}_Gazetteer/{YEAR}_Gaz_place_national.zip")

# The Gazetteer writes the legal/statistical type into the name ("Abbeville
# city", "Powell CDP"). Nobody types that, so it comes off - but only as a
# TRAILING token, or "Village of Clarkston" would lose the wrong word.
SUFFIX = re.compile(
    r"\s+(?:CDP|city|town|village|borough|municipality|township|"
    r"charter township|city and borough|consolidated government|"
    r"metro government|metropolitan government|unified government|"
    r"urban county|corporation|plantation|reservation|comunidad|"
    r"zona urbana|county|parish|district|\(balance\))$",
    re.IGNORECASE,
)

# Exactly the states in the engine's location.py. The commute logic can only
# reason about these, so offering a place from outside them would be offering
# a typo with extra steps. Anything else can still be typed by hand - the
# suggestion list is a help, never a gate.
STATES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID",
    "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS",
    "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK",
    "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV",
    "WI", "WY", "DC",
}


def clean(name: str) -> str:
    """"Athens-Clarke County unified government (balance)" carries two
    suffixes, so this strips until nothing more comes off.
    """
    previous = None
    text = name.strip()
    while text != previous:
        previous = text
        text = SUFFIX.sub("", text).strip()
    return text


def rows_from(source: str | None) -> list[str]:
    if source:
        data = Path(source).read_bytes()
    else:
        print(f"downloading {URL}")
        # URL is the constant above - a fixed https census.gov address, not
        # anything a caller can supply.
        with urllib.request.urlopen(URL, timeout=120) as response:  # noqa: S310
            data = response.read()
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        name = archive.namelist()[0]
        return archive.read(name).decode("utf-8", errors="replace").splitlines()


def main(argv: list[str]) -> int:
    rows = rows_from(argv[1] if len(argv) > 1 else None)
    # Largest land area wins a duplicate name, so "Springfield, MO" is the
    # Springfield the file keeps rather than the far smaller Springfield, GA.
    places: dict[str, float] = {}
    for line in rows[1:]:
        parts = line.split("\t")
        if len(parts) < 9:
            continue
        state = parts[0].strip().upper()
        if state not in STATES:
            continue
        place = clean(parts[3])
        if not place:
            continue
        try:
            area = float(parts[8])
        except ValueError:
            area = 0.0
        key = f"{place}, {state}"
        places[key] = max(places.get(key, 0.0), area)

    # ORDER IS THE RANKING. The app suggests places in file order, and this
    # is the one place that decides it, so the app needs no size data of its
    # own and the file stays one plain name per line.
    #
    # Land area, because the Gazetteer carries it and population estimates do
    # not cover census-designated places - ranking by population would bury
    # Metairie, LA (a CDP, and exactly the kind of place this file exists
    # for) under every incorporated place of the same name. Area is a proxy,
    # not a truth: it gets Springfield MO, Portland OR and Chicago IL to the
    # top of their names, which is what the ranking is for.
    out = Path(__file__).with_name("us_places.txt")
    ordered = sorted(places, key=lambda name: (-places[name], name.lower()))
    out.write_text("\n".join(ordered) + "\n", encoding="utf-8", newline="\n")
    print(f"{len(ordered)} places -> {out}")

    # Named so a regeneration that quietly drops the small places - the whole
    # reason this file exists - fails loudly instead of shipping.
    # Spread across states on purpose: a probe list drawn from one metro
    # would catch the same regression and would also say which metro wrote
    # it. Half of these are census-designated rather than incorporated,
    # which is the half that goes missing first.
    for probe in ("Powell, OH", "Seymour, IN", "Xenia, OH", "Lemont, IL",
                  "Metairie, LA", "Bethesda, MD", "Cicero, IL", "Boise, ID"):
        if probe not in places:
            print(f"MISSING: {probe}")
            return 1

    # And that the ranking survived: the app's own test asserts this too, but
    # failing here says WHICH step broke it.
    for typed, expected in (("springfield", "Springfield, MO"),
                            ("metairie", "Metairie, LA"),
                            ("portland", "Portland, OR")):
        first = next(n for n in ordered if n.lower().startswith(typed))
        if first != expected:
            print(f"RANKING: {typed!r} leads with {first!r}, expected {expected!r}")
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
