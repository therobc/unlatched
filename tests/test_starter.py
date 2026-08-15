"""The starter employer pack: what a fresh install can read on day one.

An earlier change. Twelve of the thirteen collectors are per-employer and sit
idle until somebody names companies, and nobody can invent forty employer
names cold.

These tests guard the PROPERTIES of the pack rather than its contents - the
list is regenerated from a measurement (research/measure_starter_pack.py)
and is expected to change, but it must never become regional, never carry a
name with no board, and never quietly overwrite somebody's own entry.
"""
from __future__ import annotations

import re

from unlatched import db, starter


def test_the_pack_is_not_empty():
    assert starter.EMPLOYERS, "a pack with nothing in it is the problem, not the fix"


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
    """the first user, 2026-08-04, explicit: no locally specific employers. A pack
    carrying one metro's names is dead weight everywhere else and reads as
    an oversight to everyone inside it. The test profiles' own employers are
    the ones most likely to be pasted in by accident."""
    banned = {
        "covenant health", "tennova", "oak ridge national laboratory",
        "y-12", "clayton homes", "pilot company", "regal cinemas",
        "university of tennessee", "east tennessee children's hospital",
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
