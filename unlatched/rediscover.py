"""rediscover.py - has an employer moved to a different applicant tracking system?

THE PROBLEM THIS EXISTS FOR. An employer that changes ATS does not announce
it. `collect` keeps reading the board reference it was given, that reference
stops returning postings, and the employer simply goes quiet. Nothing in the
app distinguishes "this company is not hiring" from "this company moved to
Greenhouse eight weeks ago and we have been asking Workday ever since".

The starter pack makes it worse rather than better: fifty employers arrive at
once, measured on one day, and they age together. starter.py says so plainly -
"The list ages: employers change ATS" - and until now said it while offering
nothing to do about it.

WHAT IT DOES. Re-runs discovery over the companies already stored and compares
what comes back with what is on file. Four outcomes, and the interesting ones
are the middle two:

    unchanged     the same system, the same reference
    moved         a different system, or the same system at a new reference
    unreadable    it had a board and now nothing fingerprints
    now readable  it had no board and now one does

THE SWEEP REPORTS BY DEFAULT AND WRITES ONLY ON --apply, in the manner of
`prune` and `dedupe`. The board reference this rewrites is what every future
collect depends on, so overwriting it across fifty employers on the strength
of one probe each - any of which may have hit a site mid-migration, or a
careers page behind a temporary redirect - is not a trade worth making. A
person should be able to see the move before agreeing to it.

THE SWEEP IS NEVER SCHEDULED. Re-probing every employer's careers site on a
timer is the crawl this app refuses to be: `refresh` reads boards that publish
for programmatic access, and that is a different act from walking fifty
companies' web sites because an hour arrived. The sweep runs when somebody
asks and not otherwise.

`heal_one` IS THE EXCEPTION, AND IT IS A NARROW ONE. Both rules above are
about a fifty-employer sweep of sites that are answering fine. heal_one probes
ONE employer, because that employer's own board has returned nothing on
QUIET_RUNS consecutive collects - which is evidence, not a timer - and
collect stops after MAX_HEALS_PER_RUN of them. It writes without asking for
the same reason: the reference it replaces has already demonstrated that it
yields nothing, so there is no working value at risk. UNREADABLE remains
unwritten either way, so a failed probe can still never blank a live board.

IT REUSES discover.resolve RATHER THAN DETECTING ANYTHING ITSELF, so a
fingerprint improvement lands here for free and this can never develop its own
opinion about what a Workday reference looks like. discover.ats_of is the one
place that reads a resolve() result, shared with `discover` itself.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, NamedTuple

from . import db, discover

if TYPE_CHECKING:
    import sqlite3
    from collections.abc import Callable

UNCHANGED = "unchanged"
MOVED = "moved"
UNREADABLE = "unreadable"
NOW_READABLE = "now readable"

# The outcomes --apply writes. `unreadable` is deliberately NOT among them: a
# probe that found nothing is not evidence the board is gone, and blanking a
# working reference on one failed lookup would take an employer out of every
# future collect over what may have been a bad afternoon.
WRITES = (MOVED, NOW_READABLE)


class Finding(NamedTuple):
    company: str
    outcome: str
    was_ats: str
    was_ref: str
    now_ats: str
    now_ref: str
    careers_url: str
    note: str


def _classify(was_ats: str, was_ref: str, now_ats: str, now_ref: str) -> str:
    if not was_ats and not now_ats:
        # Still nothing, which is not an error. An employer with no ATS
        # fingerprint is an ordinary case rather than a failure - it is the
        # case the schema.org and sitemap collectors exist for, and they are
        # in the registry for exactly that reason.
        return UNCHANGED
    if not was_ats:
        return NOW_READABLE
    if not now_ats:
        return UNREADABLE
    if was_ats == now_ats and was_ref == now_ref:
        return UNCHANGED
    return MOVED


def plan(con: sqlite3.Connection,
         *, fetcher: Callable[..., Any],
         only: str | None = None) -> list[Finding]:
    """Re-probe stored employers and report what changed. Writes nothing.

    `only` limits the sweep to one company by name, because fifty discoveries
    is a long errand to run when the question is about one employer.
    """
    findings: list[Finding] = []
    for row in db.list_companies(con):
        name = str(row["name"])
        if only and name.lower() != only.lower():
            continue
        was_ats = str(row["ats"] or "")
        was_ref = str(row["ats_ref"] or "")

        res = discover.resolve(name, fetcher=fetcher)
        now_ats, now_ref = discover.ats_of(res)

        findings.append(Finding(
            company=name,
            outcome=_classify(was_ats, was_ref, now_ats, now_ref),
            was_ats=was_ats, was_ref=was_ref,
            now_ats=now_ats, now_ref=now_ref,
            careers_url=str(res.get("careers_url") or ""),
            note=str(res.get("note") or ""),
        ))
    return findings


def apply(con: sqlite3.Connection, findings: list[Finding]) -> dict[str, Any]:
    """Write the new reference for every finding worth writing.

    Returns {"written": n, "companies": [...]}. Only MOVED and NOW_READABLE are
    written - see WRITES for why an unreadable probe leaves the row alone.
    """
    written: list[str] = []
    for f in findings:
        if f.outcome not in WRITES:
            continue
        db.upsert_company(con, f.company, ats=f.now_ats, ats_ref=f.now_ref,
                          careers_url=f.careers_url,
                          probe_status="yielding" if f.now_ats else "probed")
        written.append(f.company)
    return {"written": len(written), "companies": written}


# How many consecutive collects returning ZERO postings before an employer's
# stored reference is treated as stale rather than as a quiet employer.
#
# THREE, NOT ONE, and the difference is the whole design. A board legitimately
# returns nothing all the time - a small employer between openings looks
# exactly like a moved ATS on any single run, and re-probing on the first
# empty result would turn every quiet week into a crawl. Three consecutive
# empty collects on a board that USED to answer is a different claim.
QUIET_RUNS = 3

# The most employers one collect will re-probe. A run that finds twenty quiet
# boards must not become twenty discoveries: the point is to repair the pack
# steadily in the background, not to convert a collect into a sweep the moment
# something goes wrong at scale (a network outage makes every board quiet at
# once, and that is precisely when this must not fire twenty times).
MAX_HEALS_PER_RUN = 3

# Where the per-company counter lives. `meta` already carries per-company run
# state in this shape - see the workday/oracle backfill offset - so this needs
# no column and therefore no matching migration in the desktop's db.rs.
def quiet_key(company_id: int) -> str:
    return f"quiet:{company_id}"


def note_collect_result(con: sqlite3.Connection, company_id: int,
                        collected: int) -> int:
    """Record whether this employer's board answered. Returns the quiet run.

    Reset on ANY posting at all, rather than on a qualified one: the question
    is whether the reference still reaches a board, and a board full of jobs
    the person does not want is a board that is answering perfectly.
    """
    if collected > 0:
        db.set_meta(con, quiet_key(company_id), "0")
        return 0
    runs = int(db.get_meta(con, quiet_key(company_id)) or 0) + 1
    db.set_meta(con, quiet_key(company_id), str(runs))
    return runs


def due_for_healing(row: Any, runs: int) -> bool:
    """Is this employer's stored reference worth re-probing?

    THREE CONDITIONS, and the middle one is the one that keeps this honest:

      it has gone quiet QUIET_RUNS times in a row
      IT ONCE HAD A BOARD - an employer with no ats was never reachable this
        way, so its silence says nothing about a reference having gone stale,
        and probing it would be discovery rather than repair
      it is ours to repair - seeded or discovered. A hand-added or imported
        employer's reference came from somebody else, and this app rewriting
        it would be answering a question it was not asked.
    """
    if runs < QUIET_RUNS:
        return False
    if not str(row["ats"] or ""):
        return False
    return str(row["origin"] or "") in (db.SEEDED, db.DISCOVERED)


def heal_one(con: sqlite3.Connection, name: str, *,
             fetcher: Callable[..., Any]) -> Finding | None:
    """Re-probe ONE employer and write the result if it moved.

    Returns the finding, or None if the probe said nothing useful. The counter
    is cleared either way: a probe that found nothing has still been made, and
    leaving the count above the threshold would re-probe the same employer on
    every subsequent collect for ever.
    """
    findings = plan(con, fetcher=fetcher, only=name)
    if not findings:
        return None
    finding = findings[0]
    if finding.outcome in WRITES:
        apply(con, [finding])
    return finding


def tally(findings: list[Finding]) -> dict[str, int]:
    """How many of each outcome, with every outcome present even at zero.

    A missing key and a zero read the same way to a person skimming, and this
    is what the summary line is built from.
    """
    counts = dict.fromkeys((UNCHANGED, MOVED, UNREADABLE, NOW_READABLE), 0)
    for f in findings:
        counts[f.outcome] = counts.get(f.outcome, 0) + 1
    return counts
