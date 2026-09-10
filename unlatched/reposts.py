"""reposts.py - Has this seat been advertised before?

WHY A POSTING ID CANNOT ANSWER THIS
-----------------------------------
Measured across the whole corpus: employers mint a NEW posting id when they
re-advertise. Not one board in the collection reuses the old one - all five
checked by hand, across four unrelated industries, issue a fresh id. So
`db.relist` catches almost nothing real; it only fires when a board went
briefly empty during an edit and the same id came back. A genuine repost
arrives as a stranger, and the row it replaces sits there delisted with
nothing tying the two together.

THE UNIT IS A SEAT, NOT A POSTING
---------------------------------
The first measurement of this grouped on company+title and reported that one
freight carrier re-advertised "Class A CDL Local Driver" on 18 separate
dates. That was wrong: it has terminals in dozens of cities and each posts
its own opening. Adding LOCATION separates breadth from repetition. Same company
+ same title + same place is one seat, and a seat advertised twice is a seat
somebody left or never filled.

WHAT THE GAP MEANS - THE TWO POPULATIONS
----------------------------------------
Re-advertisement intervals are not one distribution. In the measured corpus
(94 repeated seats of 6,101):

  <= 7 days   32   the employer bumping a listing for visibility, or an ATS
                   edit that minted a new id. NOT turnover, and reporting it
                   as turnover would be a lie the user acts on.
  8-28 days   23   the search failed, or a hire fell through.
  1-3 months  45   a hire that did not stick, or a seat that keeps emptying.

So the gap is reported alongside the count, and anything inside a week is
labelled a re-listing rather than a repost.

A repost is not purely a warning, either. A seat advertised again after a
month means the employer is still hiring and the previous round produced
nobody - an applicant is not competing against a backlog. The UI states the
fact and the interval; what it means is the reader's call.

WHAT THIS CANNOT SEE
--------------------
Only reposts where both the old and the new listing were captured. A seat
taken down before we ever collected it is invisible, so every count here is
a floor. That improves on its own as the daily refresh accumulates history:
`jobs` keeps delisted rows forever, so this module needs no history table of
its own - the job table IS the history.
"""
from __future__ import annotations

import re
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any, NamedTuple

if TYPE_CHECKING:
    import sqlite3

# Below this, a re-advertisement is the employer refreshing a live listing
# rather than filling the seat twice. Chosen from the measured gap
# distribution above, where a distinct cluster sits inside one week.
RELIST_DAYS = 7

# Above this, a re-advertisement is a NEW OPENING rather than the same round
# continuing - so it is its own entry, linked back to the advertisement it
# followed rather than folded into it.
#
# Decided 2026-08-12: re-advertisements usually follow closely, so a seat
# advertised again more than four weeks later is a NEW entry, linked back to the
# round it originated from rather than folded into it.
#
# MEASURED ON A REAL BOARD BEFORE BUILDING THIS, 2026-08-13, over 9,161
# postings: 151 seats advertised more than once, 175 intervals between
# advertisements - 89 inside a week, 34 in the 8-28 day band, and 52 over four
# weeks. So the rule separates roughly a third of the intervals, and it is not
# a rule about a handful of outliers.
#
# The far tail is what makes the OLD wording wrong rather than merely coarse: a
# seat re-advertised after 1,788 days was being described as "the earlier
# round did not produce a hire", which after five years is not a claim anybody
# should act on.
NEW_ENTRY_DAYS = 28

# Bumped whenever seat_key or VAGUE_PLACE changes meaning. db.connect compares
# it against what the database was last keyed with and recomputes every seat
# when they differ - see backfill_seats.
SEAT_VERSION = 2

# Lever writes createdAt as epoch MILLISECONDS. Collected rows are converted
# now, but databases collected before that fix still hold the raw number.
_MIN_PLAUSIBLE_MS = 1_000_000_000_000

# Boards aggregate multi-site openings instead of naming them: Workday writes
# "6 locations", USAJOBS writes "Multiple Locations". Two unrelated postings
# can both say that, so it is not an identity - a seat built on one is
# reported but never counted as a repost. Caught in testing: two different Air
# Force openings both filed under "Multiple Locations" would otherwise have
# been reported as one seat re-advertised after 192 days.
VAGUE_PLACE = re.compile(
    r"^(?:\d+|multiple|various|several|nationwide|multi)\s*locations?$",
    re.IGNORECASE)


class Repost(NamedTuple):
    """One seat's advertising history."""

    seat: str
    dates: list[date]
    keys: list[str]

    @property
    def times(self) -> int:
        return len(self.dates)

    @property
    def gaps(self) -> list[int]:
        return [(self.dates[i + 1] - self.dates[i]).days
                for i in range(len(self.dates) - 1)]

    @property
    def last_gap(self) -> int | None:
        gaps = self.gaps
        return gaps[-1] if gaps else None

    @property
    def real_gaps(self) -> list[int]:
        """Intervals long enough to mean the seat actually emptied again.

        Judged over EVERY interval, not just the most recent one. Caught in
        testing: one seat went 41 days and then 3, and reading only the last
        gap called the whole history a re-listing - hiding the 41-day
        turnover that is the thing worth knowing.
        """
        return [g for g in self.gaps if g > RELIST_DAYS]

    def follows(self, key: str) -> str | None:
        """The advertisement this one is a new entry after, if it is one.

        None when this key is the seat's first advertisement, or when it came
        close enough behind the previous one to be the same round still going.
        """
        try:
            index = self.keys.index(key)
        except ValueError:
            return None
        if index == 0:
            return None
        if (self.dates[index] - self.dates[index - 1]).days <= NEW_ENTRY_DAYS:
            return None
        return self.keys[index - 1]

    def summary(self, key: str | None = None) -> str:
        """One line for the row hover and the detail panel.

        WRITTEN FOR THE ROW WHEN ONE IS NAMED. A seat's history is not one fact:
        the row that OPENED a new round after a long gap is a different thing
        from the rows in the round before it, and giving both the same sentence
        was what made a five-year-old seat read as a failed search.

        A WHOLE SENTENCE, ENDING IN A FULL STOP. The desktop used to compose one
        by prefixing "This seat was ", which quietly made the engine's wording
        depend on a fragment held in Rust - and the moment a note stopped
        beginning with a past participle it read as nonsense. The engine owns
        the rule and now owns the sentence; the app renders what it is given.
        """
        real = self.real_gaps
        if not self.gaps:
            return "Advertised once."
        if key is not None and self.follows(key):
            index = self.keys.index(key)
            gap = (self.dates[index] - self.dates[index - 1]).days
            return (f"A new opening for a seat last advertised {_span(gap)} "
                    "earlier - linked to that round, not part of it.")
        if not real:
            return (f"Listed {self.times} times within a few days of each other "
                    "- the employer refreshing one listing, not a seat filled "
                    "twice.")
        longest = max(real)
        again = (f", and {len(real)} times in all" if len(real) > 1 else "")
        if longest > NEW_ENTRY_DAYS:
            # The seat came back, but this row is not the one that brought it
            # back - so it is stated as history rather than as a claim about
            # what happened to this posting.
            return (f"This seat was advertised again {_span(longest)} later"
                    f"{again} - see the newer entry.")
        return (f"Advertised again after {_span(longest)}{again} - the earlier "
                "round did not produce a hire, or the seat emptied.")


def _span(days: int) -> str:
    """A gap in the coarsest unit that is still honest about it."""
    months = days // 30
    if months >= 12:
        years = months // 12
        return f"{years} year{'s' if years != 1 else ''}"
    if months:
        return f"{months} month{'s' if months != 1 else ''}"
    return f"{days} days"


def _normalise(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def seat_key(company: str, title: str, location: str) -> str:
    """Identity of a seat: one company, one title, one place.

    Deliberately literal. Stemming titles or dropping words would merge
    "Support Engineer" with "Senior Support Engineer" and invent reposts that
    never happened, and a false turnover warning is worse than a missing one.
    """
    place = (location or "").strip()
    if VAGUE_PLACE.match(place):
        # Kept in the key so the seat still groups its own duplicates, but
        # flagged so repost counting can refuse to trust it.
        place = "?multi"
    return f"{_normalise(company)}|{_normalise(title)}|{_normalise(place) or '?'}"


def is_vague(seat: str) -> bool:
    """Does this seat rest on a location too vague to identify a seat?"""
    return seat.endswith(("|multi", "|?"))


def posted_date(raw: Any) -> date | None:
    """A posting's date from whatever the board wrote, or None."""
    text = str(raw or "").strip()
    if not text:
        return None
    if text.isdigit() and int(text) >= _MIN_PLAUSIBLE_MS:
        try:
            # UTC, not local: a posting stamped near midnight would otherwise
            # land on a different calendar day depending on the reader's
            # timezone, and every gap here is measured in whole days.
            return datetime.fromtimestamp(int(text) / 1000, tz=UTC).date()
        except (OverflowError, OSError, ValueError):
            return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def history(con: sqlite3.Connection) -> dict[str, Repost]:
    """Every seat that has been advertised more than once, by seat key.

    Reads delisted rows too - a seat's previous advertisement is usually the
    row that went away, and excluding it would leave nothing to compare
    against.
    """
    rows = con.execute(
        "SELECT key, seat, posted_at FROM jobs "
        "WHERE seat IS NOT NULL AND seat != ''").fetchall()
    by_seat: dict[str, list[tuple[date, str]]] = {}
    for row in rows:
        when = posted_date(row["posted_at"])
        if when is None:
            continue
        by_seat.setdefault(row["seat"], []).append((when, row["key"]))

    out: dict[str, Repost] = {}
    for seat, entries in by_seat.items():
        if is_vague(seat):
            continue
        # One seat advertised on one date is not a repost however many rows
        # carry it: boards list a single opening under several ids.
        unique = sorted(dict(entries).items())
        if len(unique) < 2:
            continue
        out[seat] = Repost(seat=seat, dates=[d for d, _ in unique],
                           keys=[k for _, k in unique])
    return out


# How many row updates to make before releasing the write lock. See annotate.
COMMIT_EVERY = 500


def annotate(con: sqlite3.Connection) -> int:
    """Write each job's repost history onto its row. Returns rows touched.

    Stored rather than derived by the front end on demand. The desktop app
    would otherwise have to re-implement seat keying, epoch-millis dates and
    the re-listing threshold in Rust, and this codebase already carries one
    documented divergence of that kind (the keyword matcher). One engine, one
    rule, and the app renders what it is given.
    """
    found = history(con)
    con.execute("UPDATE jobs SET repost_note = NULL WHERE repost_note IS NOT NULL")
    con.execute("UPDATE jobs SET repost_of = NULL WHERE repost_of IS NOT NULL")
    # THE CLEAR IS ITS OWN TRANSACTION. Two full-table updates plus every row
    # below, committed together, held the write lock for the whole pass - long
    # enough that a person setting a status during a collect was told the
    # database was locked and had their typing discarded.
    con.commit()

    touched = 0
    for repost in found.values():
        for key in repost.keys:
            # PER ROW, not once per seat. The row that opened a new round after
            # a long gap gets a different sentence from the rows before it, and
            # only that row carries the link back.
            con.execute("UPDATE jobs SET repost_note = ?, repost_of = ? "
                        "WHERE key = ?",
                        (repost.summary(key), repost.follows(key), key))
            touched += 1
            # LET GO PERIODICALLY. The result is identical either way; this
            # only decides how long anything else has to wait to write. 500 is
            # large enough for the commit cost to stay negligible against the
            # updates and small enough that the wait is a fraction of a second.
            if touched % COMMIT_EVERY == 0:
                con.commit()
    con.commit()
    return touched


def backfill_seats(con: sqlite3.Connection, *, stored_version: int = 0) -> int:
    """Give every stored job a seat key. Returns how many were written.

    Runs over history as well as new rows, so an existing database gains
    repost detection the moment it is opened rather than only for jobs
    collected afterwards.

    `stored_version` is what this database was last keyed with. When it lags
    SEAT_VERSION every row is recomputed, not just the empty ones: a change to
    what counts as a seat leaves already-written keys wrong, and a stale key
    is worse than a missing one because it silently groups the wrong rows.
    Widening VAGUE_PLACE to cover "Multiple Locations" is exactly such a
    change, and without this the old keys would have survived it.
    """
    where = ("" if stored_version < SEAT_VERSION
             else " WHERE j.seat IS NULL OR j.seat = ''")
    rows = con.execute(
        "SELECT j.key, j.title, j.location, c.name AS company FROM jobs j "  # noqa: S608
        "LEFT JOIN companies c ON c.id = j.company_id" + where).fetchall()
    written = 0
    for row in rows:
        seat = seat_key(row["company"] or "", row["title"] or "",
                        row["location"] or "")
        con.execute("UPDATE jobs SET seat = ? WHERE key = ?", (seat, row["key"]))
        written += 1
    if written:
        con.commit()
    return written
