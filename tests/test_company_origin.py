"""Where each employer came from, which nothing could answer before.

The first user asked for a "refresh the seeded companies" action. It cannot be expressed
without this: starter.seed() and discover BOTH write probe_status="yielding",
so a shipped employer and one the app found by crawling were indistinguishable.

probe_status is the wrong field to infer it from anyway - it records whether a
board ANSWERS, which changes over time and for reasons that have nothing to do
with where the row came from.
"""
from __future__ import annotations

from unlatched import db, manual, starter


def test_every_employer_the_pack_ships_says_it_was_seeded(con):
    added, _ = starter.seed(con)

    origins = {r["name"]: r["origin"] for r in con.execute(
        "SELECT name, origin FROM companies")}
    assert added, "seed() added nothing - the test proves nothing"
    assert origins, "and nothing reached the companies table"
    assert set(origins.values()) == {db.SEEDED}, (
        "the whole shipped pack has to be labelled, not the first one: a "
        "'refresh the seeded companies' action reads this set")


def test_an_employer_added_by_hand_says_so(con, cfg):
    manual.add(con, cfg, "https://boards.greenhouse.io/acme/jobs/1",
               title="Support Analyst", company="Acme", no_fetch=True)

    origin = con.execute(
        "SELECT origin FROM companies WHERE name = 'Acme'").fetchone()[0]
    assert origin == db.MANUAL


def test_the_origin_is_not_overwritten_when_the_row_is_updated(con):
    """A shipped employer the crawler later rediscovers is still shipped.

    THE POINT OF THE WHOLE COLUMN. If discovery could relabel it, "refresh the
    seeded companies" would mean a different set every day - and it would
    quietly shrink, because discovery runs far more often than seeding.
    """
    db.upsert_company(con, "Acme", ats="greenhouse", ats_ref="acme",
                      origin=db.SEEDED)
    db.upsert_company(con, "Acme", careers_url="https://acme.example/careers",
                      probe_status="yielding", origin=db.DISCOVERED)

    row = con.execute(
        "SELECT origin, careers_url FROM companies WHERE name = 'Acme'").fetchone()
    assert row["origin"] == db.SEEDED, "the later write must not relabel it"
    assert row["careers_url"], "the update itself still had to apply"


def test_a_row_that_predates_the_column_reads_as_unknown_not_as_a_guess(con):
    """Honest emptiness. Inferring 'seeded' for every old row would put
    employers into the seeded set that nobody shipped, and a refresh action
    would then hammer boards on their behalf."""
    con.execute("INSERT INTO companies (name, probe_status) VALUES ('Old', 'yielding')")
    con.commit()

    origin = con.execute(
        "SELECT origin FROM companies WHERE name = 'Old'").fetchone()[0]
    assert not origin


def test_probe_status_cannot_stand_in_for_it(con):
    """The reason this column exists, asserted rather than asserted-about.

    A seeded employer and a discovered one both end up 'yielding', so any
    attempt to tell them apart by probe_status returns the same answer for
    both - which is why the earlier plan to infer it was dropped.
    """
    db.upsert_company(con, "Seeded co", probe_status="yielding", origin=db.SEEDED)
    db.upsert_company(con, "Found co", probe_status="yielding",
                      origin=db.DISCOVERED)

    rows = {r["name"]: (r["probe_status"], r["origin"]) for r in con.execute(
        "SELECT name, probe_status, origin FROM companies")}
    assert rows["Seeded co"][0] == rows["Found co"][0], (
        "if these ever differ, this test is no longer describing the problem")
    assert rows["Seeded co"][1] != rows["Found co"][1]


def test_imported_employers_are_their_own_kind(con):
    """Not 'discovered'. We hold no board for them, so a refresh of the seeded
    or discovered sets must not sweep them up and find nothing to read."""
    db.upsert_company(con, "From the collector author", probe_status="imported",
                      origin=db.IMPORTED)

    origin = con.execute(
        "SELECT origin FROM companies WHERE name = 'From the collector author'").fetchone()[0]
    assert origin == db.IMPORTED
    assert origin not in (db.SEEDED, db.DISCOVERED, db.MANUAL)
