"""The starter employer pack: what a fresh install can read on day one.

Twelve of the fifteen collectors are per-employer and sit idle until somebody
names companies, and nobody can invent forty employer names cold. This pack is
what stands between a fresh install and an empty screen.

These tests guard the PROPERTIES of the pack rather than its contents - the
list is regenerated from a measurement and is expected to change, but it must
never become regional, never carry a name with no board, and never quietly
overwrite somebody's own entry.
"""
from __future__ import annotations

import re
import urllib.parse

import pytest

from unlatched import db, starter


def test_the_pack_is_the_size_the_rest_of_the_app_says_it_is():
    """Ten files across both halves describe this pack as fifty employers.

    A bare "not empty" check passes with one, and a regeneration that lost
    most of the pack is exactly the failure that would produce - a fresh
    install reads a handful of boards, sees almost nothing, and every comment
    and document goes on saying fifty. Held to a range rather than a number,
    because the pack is regenerated from a measurement and is meant to move a
    little; ten files' worth of prose is what stops it moving a lot without
    anybody deciding to.
    """
    assert 40 <= len(starter.EMPLOYERS) <= 60, (
        f"the pack is {len(starter.EMPLOYERS)} employers, but the app "
        f"describes it as fifty in ten places - regenerate it, or change what "
        f"they say")


def test_every_entry_names_a_board_we_can_read():
    """A name that cannot be read is worse than no name: it teaches somebody
    on their first afternoon that the tool does not work."""
    from unlatched import sources
    registry = sources.registry()
    for e in starter.EMPLOYERS:
        assert e.ats in registry, f"{e.name} uses {e.ats!r}, which has no collector"
        assert e.ats_ref, f"{e.name} has no board reference"


def test_every_entry_was_measured_returning_postings():
    for e in starter.EMPLOYERS:
        assert e.postings > 0, f"{e.name} returned nothing when measured"


def test_the_measurement_date_is_recorded():
    """The pack ages - employers change ATS. A date is what lets somebody
    tell a stale entry from a broken one."""
    assert re.fullmatch(r"\d{4}-\d\d-\d\d", starter.MEASURED_ON), starter.MEASURED_ON


def test_no_duplicate_employers():
    names = [e.name for e in starter.EMPLOYERS]
    assert len(names) == len(set(names))


def test_the_pack_spans_sectors():
    """Internal roles exist at every employer, so the pack targets a wide
    employer base rather than an industry. Two or three sectors would be an
    industry list wearing a different name."""
    sectors = {e.sector for e in starter.EMPLOYERS}
    assert len(sectors) >= 5, sorted(sectors)


def test_nothing_regional_slipped_in():
    """Decided 2026-08-04: no locally specific employers. A pack carrying one
    metro's names is dead weight everywhere else and reads as an oversight to
    everyone inside it.

    The sample below is deliberately spread across several regions rather than
    drawn from one. A single metro's list would catch the same mistake and
    would also tell a reader which metro wrote the test."""
    banned = {
        "covenant health", "geisinger", "ochsner health",
        "banner health", "baptist memorial", "sentara",
        "university of vermont", "university of nebraska", "wawa",
    }
    for e in starter.EMPLOYERS:
        assert e.name.lower() not in banned, f"{e.name} is a regional employer"


def test_seeding_adds_every_employer(home):
    con = db.connect(home)
    added, skipped = starter.seed(con)
    assert added == len(starter.EMPLOYERS)
    assert skipped == 0
    assert len(db.list_companies(con)) == len(starter.EMPLOYERS)
    con.close()


def test_seeding_twice_adds_nothing_the_second_time(home):
    con = db.connect(home)
    starter.seed(con)
    added, skipped = starter.seed(con)
    assert added == 0
    assert skipped == len(starter.EMPLOYERS)
    con.close()


def test_seeding_never_overwrites_what_the_person_already_had(home):
    """Their entry may be a correction - a board they fixed by hand after
    ours went stale. A seed that silently reverts a correction is worse than
    one that skips."""
    con = db.connect(home)
    first = starter.EMPLOYERS[0]
    db.upsert_company(con, first.name, ats="greenhouse", ats_ref="theirs",
                      probe_status="yielding")
    starter.seed(con)
    row = db.get_company(con, first.name)
    assert row["ats"] == "greenhouse"
    assert row["ats_ref"] == "theirs"
    con.close()


def test_by_sector_covers_the_whole_pack():
    grouped = starter.by_sector()
    assert sum(len(v) for v in grouped.values()) == len(starter.EMPLOYERS)


# Words that carry no identity of their own - a host will not contain them and
# should not have to.
NOISE = {"the", "and", "of", "inc", "corp", "corporation", "company",
         "companies", "group", "industries", "health", "systems", "services",
         "international", "holdings", "stores", "brands", "financial"}

# A path that names one posting rather than a board: a /job/ or /jobs/ segment
# followed by something carrying a long number, which is what a requisition id
# looks like in every board this app reads.
ONE_POSTING = re.compile(r"/jobs?/[^/]*\d{4,}")


def _tokens(name: str) -> list[str]:
    parts = re.split(r"[^a-z0-9]+", name.lower())
    return [p for p in parts if p and p not in NOISE]


@pytest.mark.parametrize("employer", starter.EMPLOYERS, ids=lambda e: e.name)
def test_no_careers_url_points_at_a_single_posting(employer):
    """The pack seeds an employer, not a job.

    One entry pointed at a single posting rather than a board - dead the
    moment that job is filled, and carrying a live requisition id off a real
    board into every install. The id is not repeated here; that would put it
    back into the tree this test exists to keep it out of.
    """
    assert not ONE_POSTING.search(employer.careers_url), (
        f"{employer.name}'s careers_url looks like one posting rather than a "
        f"board: {employer.careers_url}")


@pytest.mark.parametrize("employer", starter.EMPLOYERS, ids=lambda e: e.name)
def test_every_careers_url_is_hosted_somewhere_that_names_the_employer(employer):
    """A person clicking through should land somewhere they recognise.

    Albertsons' entry was a raw Oracle tenant host, which is where the
    collector reads from - correctly - but is not a page to hand somebody. The
    ats_ref field already carries the tenant; careers_url is the human's door.
    """
    host = (urllib.parse.urlsplit(employer.careers_url).hostname or "").lower()
    flat = host.replace("-", "")
    tokens = _tokens(employer.name)
    squashed = "".join(tokens)
    named = (any(t in flat or t in host for t in tokens)
             or (squashed and squashed in flat))
    assert named, (
        f"{employer.name}'s careers_url is hosted at {host}, which does not "
        f"name the employer: {employer.careers_url}")


def test_those_two_rules_would_actually_catch_something():
    """A positive control for both checks above.

    Both are assertions that something is ABSENT, so both pass just as happily
    against a pattern that never matches. These are the two real values they
    were written for.
    """
    assert ONE_POSTING.search(
        "https://careers.example.com/jobs/r-8675309/retail-sales-associate/")
    assert not ONE_POSTING.search("https://mycareer.example.com/")

    # The tenant host that named no employer.
    host = "eofd.fa.us6.oraclecloud.com"
    assert not any(t in host for t in _tokens("Albertsons"))
    # ...while the replacement does.
    assert any(t in "www.albertsons.com" for t in _tokens("Albertsons"))


def test_every_board_reference_has_the_shape_its_collector_expects():
    """`ats_ref` being non-empty is not the same as it being usable.

    The shapes differ by collector: discover.COMPOUND_REF says workday and
    oracle_hcm store several parts joined by "|" - workday is
    tenant|wdN|site, oracle is a host with an optional site - and everything
    else is a single slug. discover.ats_of builds them that way, so a ref of
    the wrong shape is one no collector can use.

    THE FAILURE IT PREVENTS IS SILENT. A malformed ref asks a board that
    cannot exist, gets nothing back, and the employer reads as one that is
    simply not hiring. Worth catching where a hand-edit happens rather than
    three empty collections later.
    """
    from unlatched import discover

    for e in starter.EMPLOYERS:
        parts = e.ats_ref.split("|")
        assert e.ats_ref == e.ats_ref.strip(), (
            f"{e.name}: {e.ats_ref!r} has leading or trailing whitespace")
        assert " " not in e.ats_ref, (
            f"{e.name}: {e.ats_ref!r} contains a space")
        if e.ats == "workday":
            assert len(parts) == 3, (
                f"{e.name}: a workday ref is tenant|wdN|site, got "
                f"{e.ats_ref!r} with {len(parts)} part(s)")
            assert re.fullmatch(r"wd\d+", parts[1]), (
                f"{e.name}: {parts[1]!r} is not a Workday data-centre id")
        elif e.ats == "oracle_hcm":
            assert len(parts) in (1, 2), (
                f"{e.name}: an oracle ref is a host with an optional site, got "
                f"{e.ats_ref!r}")
            assert "." in parts[0], (
                f"{e.name}: {parts[0]!r} is not a tenant host")
        else:
            assert len(parts) == 1, (
                f"{e.name}: {e.ats} takes a single slug, got {e.ats_ref!r}")
            assert e.ats not in discover.COMPOUND_REF, (
                f"{e.ats} is listed as a compound ref but is checked here as a "
                f"single slug - the two rules have drifted apart")
