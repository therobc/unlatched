"""dupes.py - the same job, reached from two different places.

reposts.py answers "has this SEAT been advertised before" from company + title +
location. That is the right question for one employer's own board and the wrong
one across boards, where the two rows are the same opening wearing different
clothes:

  a REPUBLISHER lists someone else's posting under a different title, with the
  company name stripped and replaced by "the organization"
  ONE EMPLOYER posts under several ATS slugs, so no company name matches

Both surface to a person as clutter that looks like opportunity - the worst
failure a job list has, because it costs time twice, once reading and once
applying.

THE PRIMARY KEY IS THE APPLY DESTINATION, NOT THE TEXT.

A posting on one board is usually a shopfront for an application hosted on
another: a LinkedIn listing routing to apply.workable.com is the same job as the
Workable row we collected directly, and both point at the same URL. That is
exact rather than fuzzy, and it survives the rewriting that defeats text
matching.

Measured by the collector author across 38 republisher rows against 344 employer rows: the
one true duplicate scored 0.58 on description similarity - UNDER a 0.75
threshold, so it was missed - and the next candidate scored 0.45 with everything
else at 0.08 and below. Republishers ANONYMISE, which is specifically designed
to defeat text comparison. Lowering the threshold to catch the 0.58 would pull
in the 0.45 and everything drifting toward the noise floor.

So: destination first, exact. Description similarity only where there is no
destination to compare - an Easy Apply posting that never leaves its board.

OVER-FIRING IS WORSE THAN UNDER-FIRING, and this project has already paid for
that lesson once: keying reposts on company + title collapsed 18 separate Old
Dominion terminals into one phantom history. A missed duplicate costs one wasted
read. A false merge HIDES A JOB somebody wanted and they never learn it existed.
Hence title agreement on the fuzzy path, and grouping rather than deletion.
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, NamedTuple

from . import links as links_mod
from . import reposts

if TYPE_CHECKING:
    import sqlite3

# Five-word shingles: long enough that a shared sentence is meaningful, short
# enough to survive a rewritten paragraph around it.
SHINGLE = 5

# Above this, two postings with AGREEING TITLES are the same job.
#
# Measured against a real corpus rather than chosen - see
# research/dupe_threshold.md and the `dedupe --measure` verb, which prints the
# distribution this came from. It sits above the level I / level II and
# region-variant pairs, which share ~84% of their text and are genuinely
# separate requisitions.
DESCRIPTION_THRESHOLD = 0.88

# Words that carry no identity: every posting at an employer shares them, so
# leaving them in inflates every score toward each other.
BOILERPLATE = re.compile(
    r"equal opportunity employer|reasonable accommodation|regardless of race|"
    r"e-verify|drug[- ]free workplace|background check|"
    r"we offer a competitive|benefits include|401\(?k\)?|"
    r"protected veteran|disability status",
    re.IGNORECASE)

_WORD = re.compile(r"[a-z0-9]+")


class Duplicate(NamedTuple):
    """One posting judged to be another posting seen again."""

    key: str
    duplicate_of: str
    reason: str
    score: float


def normalise_words(text: str | None) -> list[str]:
    """Lowercased words, with the shared legal boilerplate removed first."""
    if not text:
        return []
    return _WORD.findall(BOILERPLATE.sub(" ", text).lower())


def shingles(text: str | None, size: int = SHINGLE) -> set[str]:
    """Overlapping word-runs, which is what gets compared."""
    words = normalise_words(text)
    if len(words) < size:
        # Too short to shingle: compare the words themselves rather than
        # returning nothing, which would read as "no overlap" and be wrong.
        return set(words)
    return {" ".join(words[i:i + size]) for i in range(len(words) - size + 1)}


def jaccard(left: set[str], right: set[str]) -> float:
    """Shared shingles over total shingles. 0.0 when either side is empty."""
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def title_agrees(left: str, right: str) -> bool:
    """Do two titles describe the same role?

    Deliberately strict. 12 near-identical pairs in the collector author's 372-row validation
    were correctly NOT linked because they were level I versus level II, or
    region and timezone variants - they share most of their text and are
    separate requisitions. A seniority marker that differs is a different job.
    """
    left_words, right_words = set(normalise_words(left)), set(normalise_words(right))
    if not left_words or not right_words:
        return False
    # The distinguishing tokens are exactly the ones that differ, so any
    # difference in a level marker is disqualifying.
    markers = {"i", "ii", "iii", "iv", "1", "2", "3", "4",
               "senior", "junior", "lead", "principal", "staff", "associate",
               "sr", "jr", "entry", "intern"}
    if (left_words & markers) != (right_words & markers):
        return False
    overlap = jaccard(left_words, right_words)
    return overlap >= 0.5


def _a_separate_round(one: dict[str, Any], other: dict[str, Any]) -> bool:
    """Are these two advertisements of the same seat rather than one posting?

    The first user's rule, 2026-08-12: a job reposted more than four weeks later is a NEW
    ENTRY. Folding it away would hide a real opening behind a round that is
    already over - and the hidden one is the LIVE one, because the keeper is
    chosen partly on age.

    WHY THIS IS NOT ALREADY SAFE. Grouping here is exact and date-blind: two
    rows sharing an apply destination are folded however far apart they were
    advertised. Measured on a real board 2026-08-13, no pair is
    currently affected - employers who re-advertise mint a new posting id AND a
    new apply URL - so this costs nothing today and is the only thing standing
    between a person and a silently hidden opening the day one of them does
    not.

    UNKNOWN DATES DO NOT SEPARATE ANYTHING. A row without a posting date says
    nothing about when it ran, and treating "no date" as "long ago" would stop
    grouping the ordinary same-day duplicates this whole module exists for.
    """
    first = reposts.posted_date(one.get("posted_at"))
    second = reposts.posted_date(other.get("posted_at"))
    if first is None or second is None:
        return False
    return abs((second - first).days) > reposts.NEW_ENTRY_DAYS


def _rows(con: sqlite3.Connection) -> list[dict[str, Any]]:
    """Rows still eligible to be grouped.

    Excludes what is already grouped, so running this twice does not re-report
    the same pair - a caller sweeping after every import needs "1 found" to
    mean one NEW one, not one that was dealt with last time. `clear` puts a row
    back in scope.

    Excludes retired rows too: somebody threw those away, and they should not
    reappear as a duplicate of something else.
    """
    return [dict(r) for r in con.execute(
        "SELECT jobs.key, jobs.title, jobs.description, jobs.apply_url, jobs.url, "
        "       jobs.fetched_at, jobs.source, jobs.delisted_at, jobs.posted_at, "
        "       companies.name AS company, "
        "       job_status.status AS status, "
        "       (SELECT COUNT(*) FROM job_status_log l WHERE l.key = jobs.key) AS history "
        "FROM jobs "
        "LEFT JOIN companies ON companies.id = jobs.company_id "
        "LEFT JOIN job_status ON job_status.key = jobs.key "
        "WHERE jobs.retired_at IS NULL AND jobs.duplicate_of IS NULL")]


# The board whose own tracker gives a free audit trail, which is the whole
# reason it is the preferred place to apply.
PREFERRED_HOSTS = ("linkedin.com",)


# Sources whose POSTING PAGE IS THE APPLICATION PAGE, so the posting URL is the
# apply destination and needs no separate field.
#
# This exists because an earlier change could not fire at all. apply_url is written only by
# `add` and `import`, so on a real board 166 rows of 7,484 carried one - and
# every one of them was on the LinkedIn side. The join had one side. Measured
# 2026-08-09: normalising these sources' posting URLs the same way surfaces 11
# genuine cross-board pairs that were invisible, all Greenhouse, Ashby and
# Workday, each a LinkedIn row and a collected row for the same requisition.
#
# NAMED EXPLICITLY rather than "anything that is not manual". An aggregator or a
# republisher forwards elsewhere, so ITS posting URL is not where the
# application happens, and treating it as one would fold a real employer route
# into an intermediary. Adding a source here is a claim that its page carries
# the form.
#
# manual and imported are deliberately absent. They state their destination in
# apply_url when they have one, and when they do not it is because the
# application never leaves the board (Easy Apply) - which must never match
# anything, since no ATS row can duplicate it.
# KEYED ON SOURCE - the collector that produced the row - NOT on host. The collector author
# read it as a host list (2026-08-09) and sent 21 ATS platforms seen in her
# apply_urls. Most of them cannot apply: a name here only ever matches if
# Unlatched HAS a collector emitting it, so listing iCIMS while nothing collects
# iCIMS adds a row that can never join. Her list did surface two real misses
# though - bamboohr and oracle_hcm are collectors here and were absent.
#
# The first version of this tuple carried jobvite, personio and teamtailor.
# There are no such collectors. I wrote plausible ATS names instead of reading
# sources.registry(), and they would have sat here looking like coverage.
# test_every_listed_source_exists now makes that impossible.
APPLICATION_IS_THE_POSTING = (
    "greenhouse", "lever", "ashby", "workable", "recruitee", "workday",
    "smartrecruiters", "breezy", "bamboohr", "oracle_hcm",
    # The employer's own careers page, reached by its JobPosting markup or its
    # sitemap. The collector author counted 31 rows across 24 single-tenant hosts - stripe.com,
    # skydio.com, careers.leidos.com - and observed that a named host list can
    # never scale to them. Keying on the SOURCE sidesteps that entirely: however
    # many employers there are, they arrive through these two collectors.
    "schema_org", "sitemap",
)

# Collectors deliberately EXCLUDED, so the omission reads as a decision rather
# than an oversight:
#
#   nodesk, remoteok   aggregators. The posting forwards somewhere else, so
#                      their URL is not where the application happens.
#   usajobs            forwards to the hiring agency's own system.
#
# This is the same line the collector author's 15 intermediary rows sit on - RemoteHunter,
# Swooped, TheLadders - where the apply host is the poster's own domain.


def _destination(row: dict[str, Any]) -> str:
    """Where applying to this row actually happens, normalised for comparison.

    A stated apply_url always wins: the row was told, and being told beats
    being inferred.
    """
    stated = links_mod.normalise_apply_url(row.get("apply_url"))
    if stated:
        return stated
    if (row.get("source") or "").lower() in APPLICATION_IS_THE_POSTING:
        return links_mod.normalise_apply_url(row.get("url"))
    return ""


def _is_imported(row: dict[str, Any]) -> bool:
    """Is this the LinkedIn side of the pair?

    Decided from the POSTING URL, not from the source label. The first attempt
    counted "manual" as LinkedIn, which made every hand-added job - from any
    site at all - look like the preferred route, and then both rows in a pair
    looked the same and the rule silently did nothing. Caught by photographing
    the grouped view and finding the LinkedIn rows folded away.
    """
    host = links_mod.host_of(row.get("url") or "")
    if host and links_mod.host_matches(host, PREFERRED_HOSTS):
        return True
    key = (row.get("key") or "").lower()
    return key.startswith(("li:", "linkedin:"))


def _has_history(row: dict[str, Any]) -> bool:
    """Has the person done anything with this row?

    Either a current status or anything in the append-only log. The log is
    checked as well as the status because a job marked applied and later marked
    denied still represents an application that was made.
    """
    return bool(row.get("status")) or bool(row.get("history"))


def _same_employer(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """Do both rows name the same employer?

    Loose on purpose - one side may write "Facet" and the other "Facet Wealth,
    Inc." - because the question being answered is only "is this an
    intermediary", and a false NO there costs a preferred route, not a job.
    """
    left = (a.get("company") or "").strip().lower()
    right = (b.get("company") or "").strip().lower()
    if not left or not right:
        return True
    return left in right or right in left


def _primary(a: dict[str, Any], b: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Which of the pair is kept, and which is folded into it.

    LINKEDIN WINS when the same job exists on both sides (decided 2026-08-09).
    Not because it is a better route - it usually forwards to the same ATS -
    but because LinkedIn records the application in its own tracker, which is
    an audit trail the person gets for free. Two of the first user's applications were
    recovered ONLY because that badge was still visible on the posting.

    EXCEPT WHEN THE LINKEDIN ROW IS A REPUBLISHER. The collector author's caveat, and it
    matters: a republisher's listing is not a preferred route, it is an
    intermediary, and for the staffing agencies among them it carries
    first-touch risk - an employer dropping a candidate rather than settle a
    fee dispute over who sourced them. Different employer named on the two rows
    is the signal, so in that case the employer's own posting is kept.

    Otherwise the one collected FIRST wins: it is the row the person may
    already have read, given a status, or applied to, and moving that history
    to a newer row would be the merge doing damage rather than tidying.
    """
    # HISTORY OUTRANKS EVERYTHING, including the LinkedIn preference.
    #
    # If somebody has already applied through the board row and the LinkedIn
    # row then arrives, folding the board row away would hide the application:
    # the visible row would read "not set" while the record of what they did
    # sits in the grouped view. That is precisely the failure that lost the
    # Facet and Carlisle applications - status recorded on one surface and
    # invisible on the one being worked from.
    #
    # The source preference is about which ROUTE to apply through. It stops
    # mattering the moment an application exists (decided 2026-08-09).
    acted_a, acted_b = _has_history(a), _has_history(b)
    if acted_a != acted_b:
        return (a, b) if acted_a else (b, a)

    # AN OPEN POSTING OUTRANKS A CLOSED ONE, below history and above the
    # LinkedIn preference.
    #
    # The preference is about which ROUTE to apply through, and a route that has
    # closed is not a route. LinkedIn ads come down well before the requisition
    # does, so without this the person is shown a dead posting with the live one
    # folded away underneath - and nothing anywhere says so.
    #
    # Below history, because if they already applied through the closed posting
    # that record is what they need to see: surfacing the open route instead
    # would hide the fact that they have been here, which is how somebody
    # applies twice to one employer.
    gone_a, gone_b = bool(a.get("delisted_at")), bool(b.get("delisted_at"))
    if gone_a != gone_b:
        return (b, a) if gone_a else (a, b)

    imported_a, imported_b = _is_imported(a), _is_imported(b)
    if imported_a != imported_b and _same_employer(a, b):
        return (a, b) if imported_a else (b, a)
    first, second = sorted((a, b), key=lambda r: (r.get("fetched_at") or "", r["key"]))
    return first, second


def find(con: sqlite3.Connection,
         threshold: float = DESCRIPTION_THRESHOLD,
         *, use_descriptions: bool = False) -> list[Duplicate]:
    """Every posting that is another posting seen again.

    THE DESCRIPTION PATH IS OFF BY DEFAULT, and that is a measurement result
    rather than caution. Run against a real 7,189-row corpus, 33,015 pairs
    have agreeing titles and HUNDREDS SCORE 1.000 while being entirely separate
    jobs:

      Wealth Management Client Associate at Wyomissing / York / Jenkintown
      Relationship Banker at West Chester / Middletown
      Client Relationship Consultant 3 at Greenwood / Queen Anne, both Seattle

    One employer writes one description and posts it at every branch. The text
    is not similar, it is IDENTICAL, so no threshold separates a republisher's
    copy from a bank's forty openings - and merging them would hide thirty-nine
    real jobs somebody could have applied to.

    This is the Old Dominion over-fire again, at scale: keying on company+title
    once collapsed 18 separate terminals into one phantom history. The rule
    stands - a missed duplicate costs one wasted read, a false merge hides a job
    and nobody ever learns it existed.

    So the apply destination carries this feature. It is exact, it is what
    the collector author's measurement pointed at, and it is the only signal in the corpus
    that distinguishes "same application" from "same employer's boilerplate".
    Pass use_descriptions=True to enable the fuzzy path deliberately, with a
    threshold read off `dedupe --measure`.
    """
    rows = _rows(con)
    found: list[Duplicate] = []
    claimed: set[str] = set()

    # --- exact, on the apply destination ------------------------------------
    by_destination: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        # Normalise on read as well as on write: rows collected before
        # apply_url existed, or written by anything other than `add`, have not
        # been through it. _destination also supplies the destination for
        # collected ATS rows, which never carry apply_url at all - see
        # APPLICATION_IS_THE_POSTING.
        destination = _destination(row)
        if destination:
            by_destination.setdefault(destination, []).append(row)

    for destination, group in by_destination.items():
        if len(group) < 2:
            continue
        # Same keeper rule as the fuzzy path, applied pairwise against the
        # current keeper so a group of three lands on one row rather than
        # chaining A -> B -> C, which would leave B pointing at something that
        # is itself folded away.
        group.sort(key=lambda r: (r.get("fetched_at") or "", r["key"]))
        keeper = group[0]
        for other in group[1:]:
            keeper, _folded = _primary(keeper, other)
        for other in group:
            if other["key"] == keeper["key"]:
                continue
            if _a_separate_round(keeper, other):
                continue
            found.append(Duplicate(other["key"], keeper["key"],
                                   f"same application page ({destination})", 1.0))
            claimed.add(other["key"])

    # --- fallback, on the description ---------------------------------------
    #
    # Only for rows with no destination to compare. An Easy Apply posting stays
    # on its board and has no ATS row it could collide with, so text is all
    # there is.
    if not use_descriptions:
        return found

    candidates = [r for r in rows
                  if r["key"] not in claimed
                  and not links_mod.normalise_apply_url(r.get("apply_url"))
                  and r.get("description")]
    prints = {r["key"]: shingles(r["description"]) for r in candidates}

    for index, row in enumerate(candidates):
        if row["key"] in claimed:
            continue
        for other in candidates[index + 1:]:
            if other["key"] in claimed:
                continue
            if not title_agrees(row.get("title") or "", other.get("title") or ""):
                continue
            # The same rule as the exact path, and it matters MORE here: two
            # advertisements of one seat a year apart carry the employer's same
            # boilerplate and would score near 1.000 against each other.
            if _a_separate_round(row, other):
                continue
            score = jaccard(prints[row["key"]], prints[other["key"]])
            if score < threshold:
                continue
            keeper, folded = _primary(row, other)
            found.append(Duplicate(folded["key"], keeper["key"],
                                   f"near-identical description ({score:.2f})", score))
            claimed.add(folded["key"])

    return found


def apply(con: sqlite3.Connection, duplicates: list[Duplicate]) -> int:
    """Record the grouping. Returns how many rows were folded.

    GROUPED, NEVER DELETED - the same rule retirement follows. The row keeps
    its status, its history and its place in the repost record; it simply
    stops appearing beside the posting it duplicates. Undoing it is one
    UPDATE, which is what makes an over-fire recoverable rather than a loss.
    """
    for dupe in duplicates:
        con.execute(
            "UPDATE jobs SET duplicate_of = ?, duplicate_reason = ? WHERE key = ?",
            (dupe.duplicate_of, dupe.reason, dupe.key))
    con.commit()
    return len(duplicates)


def _group_rows(con: sqlite3.Connection) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """Existing (hidden, keeper) pairs, with the fields _primary judges on."""
    cols = ("SELECT jobs.key, jobs.url, jobs.fetched_at, jobs.source, "
            "       jobs.delisted_at, companies.name AS company, "
            "       job_status.status AS status, "
            "       (SELECT COUNT(*) FROM job_status_log l WHERE l.key = jobs.key) "
            "         AS history "
            "FROM jobs LEFT JOIN companies ON companies.id = jobs.company_id "
            "LEFT JOIN job_status ON job_status.key = jobs.key WHERE jobs.key = ?")
    pairs = []
    for row in con.execute(
            "SELECT key, duplicate_of FROM jobs "
            "WHERE duplicate_of IS NOT NULL AND retired_at IS NULL").fetchall():
        hidden = con.execute(cols, (row["key"],)).fetchone()
        keeper = con.execute(cols, (row["duplicate_of"],)).fetchone()
        if hidden and keeper:
            pairs.append((dict(hidden), dict(keeper)))
    return pairs


def rebalance(con: sqlite3.Connection) -> list[tuple[str, str]]:
    """Re-judge every existing group, and swap the ones now facing the wrong way.

    THE FAILURE THIS PREVENTS, which is the expensive kind because it is
    invisible: a LinkedIn ad and an ATS requisition are grouped, LinkedIn is
    kept because applying there gives a free audit trail, and then the ad
    expires. LinkedIn ads come down well before the requisition does. The
    person is now looking at a closed posting, and the route they could still
    apply through is folded away underneath it.

    find() cannot catch this. It only considers rows with duplicate_of IS NULL,
    so a group is decided once and never revisited, however the world moves
    afterwards - and the world does move: the collector author's collector pushes `delist` for
    LinkedIn rows every day.

    IT RE-RUNS _primary RATHER THAN TESTING FOR THAT ONE CASE. The first version
    hard-coded "keeper closed and hidden open", which handled the common
    direction and silently ignored every other way a group can go stale. The one
    that found it: she reopens a wrongly-closed posting with `delist --back`, and
    the group stayed flipped forever, because nothing was watching for the
    reverse. Re-judging with the same function find() uses means there is ONE
    keeper rule and existing groups obey it for the same reasons new ones do.

    SWAPPED, NOT UNGROUPED. Ungrouping would put the duplicate back on the board
    and undo the thing the person asked for; swapping keeps one row visible and
    makes it the one they can act on.

    Converges rather than oscillating: _primary is deterministic on the pair, so
    once the arrangement matches it, a second pass changes nothing.

    Returns the (new keeper, now hidden) pairs it changed.
    """
    swapped: list[tuple[str, str]] = []
    for hidden, keeper in _group_rows(con):
        should_keep, _folded = _primary(keeper, hidden)
        if should_keep["key"] == keeper["key"]:
            continue
        reason = con.execute("SELECT duplicate_reason FROM jobs WHERE key = ?",
                             (hidden["key"],)).fetchone()[0]
        reason = (reason or "same application page").split(";")[0]
        if keeper.get("delisted_at") and not hidden.get("delisted_at"):
            reason = f"{reason}; kept row's posting has closed"
        con.execute("UPDATE jobs SET duplicate_of = NULL, duplicate_reason = NULL "
                    "WHERE key = ?", (hidden["key"],))
        con.execute("UPDATE jobs SET duplicate_of = ?, duplicate_reason = ? "
                    "WHERE key = ?", (hidden["key"], reason, keeper["key"]))
        swapped.append((hidden["key"], keeper["key"]))
    if swapped:
        con.commit()
    return swapped


def clear(con: sqlite3.Connection, keys: list[str] | None = None) -> int:
    """Ungroup - everything, or just the keys given."""
    if keys is None:
        cur = con.execute(
            "UPDATE jobs SET duplicate_of = NULL, duplicate_reason = NULL "
            "WHERE duplicate_of IS NOT NULL")
        con.commit()
        return cur.rowcount
    count = 0
    for key in keys:
        cur = con.execute(
            "UPDATE jobs SET duplicate_of = NULL, duplicate_reason = NULL WHERE key = ?",
            (key,))
        count += cur.rowcount
    con.commit()
    return count


def distribution(con: sqlite3.Connection) -> list[tuple[float, str, str]]:
    """Every scored pair with agreeing titles, highest first.

    The threshold is not a guess and this is how it stops being one: run it,
    read where the natural break falls, and put the constant above it. Boilerplate
    is the whole difficulty - EEO statements and benefits blocks are identical
    across genuinely different roles at one employer - so the trimming in
    normalise_words has to be judged from this output too.
    """
    rows = [r for r in _rows(con) if r.get("description")]
    prints = {r["key"]: shingles(r["description"]) for r in rows}
    scored: list[tuple[float, str, str]] = []
    for index, row in enumerate(rows):
        for other in rows[index + 1:]:
            if not title_agrees(row.get("title") or "", other.get("title") or ""):
                continue
            score = jaccard(prints[row["key"]], prints[other["key"]])
            if score > 0.0:
                scored.append((score, row["key"], other["key"]))
    scored.sort(reverse=True)
    return scored
