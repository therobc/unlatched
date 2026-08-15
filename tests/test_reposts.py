"""Is this seat being advertised again, and does that mean anything?

Every case here came out of the real corpus, not from imagination. The three
that matter most are the ones that were wrong on the first pass: multi-site
employers read as reposts, boards that write "Multiple Locations" merged
unrelated openings, and reading only the most recent interval hid a genuine
40-day turnover behind a 3-day re-listing.
"""
from __future__ import annotations

from datetime import date

from unlatched import db as db_mod
from unlatched import reposts


def test_same_title_different_cities_is_not_a_repost():
    """Old Dominion posts "Class A CDL Local Driver" at terminals in dozens of
    cities. Grouping on company+title alone called that 18 reposts of one job.
    """
    a = reposts.seat_key("Old Dominion", "Class A CDL Local Driver", "Dallas, TX")
    b = reposts.seat_key("Old Dominion", "Class A CDL Local Driver", "Memphis, TN")
    assert a != b


def test_same_seat_matches_across_punctuation_and_case():
    a = reposts.seat_key("RXO", "Logistics Specialist", "Naperville, Illinois")
    b = reposts.seat_key("rxo", "logistics  specialist", "Naperville Illinois")
    assert a == b


def test_similar_titles_are_not_merged():
    """Stemming would make these one seat and invent a turnover event."""
    a = reposts.seat_key("Acme", "Support Engineer", "Austin, TX")
    b = reposts.seat_key("Acme", "Senior Support Engineer", "Austin, TX")
    assert a != b


def test_vague_locations_are_flagged():
    for place in ("Multiple Locations", "6 locations", "Various Locations",
                  "multiple location"):
        assert reposts.is_vague(reposts.seat_key("Acme", "Analyst", place)), place


def test_named_place_is_not_vague():
    assert not reposts.is_vague(reposts.seat_key("Acme", "Analyst", "Knoxville, TN"))


def test_missing_place_is_vague_not_an_identity():
    """An empty location is not evidence two postings are the same seat."""
    assert reposts.is_vague(reposts.seat_key("Acme", "Analyst", ""))


def test_epoch_millis_dates_are_read():
    """Lever writes createdAt in milliseconds; 1,199 stored rows still hold
    the raw number.
    """
    assert reposts.posted_date("1781023668000") == date(2026, 6, 9)
    assert reposts.posted_date("2026-06-09") == date(2026, 6, 9)
    assert reposts.posted_date("2026-06-09T14:03:00Z") == date(2026, 6, 9)
    assert reposts.posted_date("") is None
    assert reposts.posted_date("not a date") is None


def test_short_year_number_is_not_mistaken_for_millis():
    """A bare "2026" is a year, not an epoch timestamp."""
    assert reposts.posted_date("2026") is None


def _add(con, key, company_id, title, location, posted):
    db_mod.upsert_job(con, key, {
        "company_id": company_id, "title": title, "location": location,
        "posted_at": posted,
        "seat": reposts.seat_key("Acme", title, location),
    })


def test_history_needs_two_distinct_dates(con):
    cid = db_mod.upsert_company(con, "Acme")
    _add(con, "gh:1", cid, "Analyst", "Austin, TX", "2026-06-01")
    _add(con, "gh:2", cid, "Analyst", "Austin, TX", "2026-06-01")
    # Two ids, one date: a board listing one opening twice, not a repost.
    assert reposts.history(con) == {}


def test_history_finds_a_real_repost(con):
    cid = db_mod.upsert_company(con, "Acme")
    _add(con, "gh:1", cid, "Analyst", "Austin, TX", "2026-06-01")
    _add(con, "gh:2", cid, "Analyst", "Austin, TX", "2026-07-15")
    found = reposts.history(con)
    assert len(found) == 1
    entry = next(iter(found.values()))
    assert entry.times == 2
    assert entry.gaps == [44]
    assert entry.real_gaps == [44]
    # 44 days is past NEW_ENTRY_DAYS, so the LATER row is a new opening and the
    # earlier one is the round it followed. Two rows, two sentences.
    assert "A new opening" in entry.summary("gh:2")
    assert "see the newer entry" in entry.summary("gh:1")
    assert entry.follows("gh:2") == "gh:1"
    assert entry.follows("gh:1") is None


def test_a_few_days_apart_is_a_relisting_not_turnover(con):
    cid = db_mod.upsert_company(con, "Acme")
    _add(con, "gh:1", cid, "Analyst", "Austin, TX", "2026-06-01")
    _add(con, "gh:2", cid, "Analyst", "Austin, TX", "2026-06-04")
    entry = next(iter(reposts.history(con).values()))
    assert entry.real_gaps == []
    assert "refreshing one listing" in entry.summary()


def test_a_long_gap_is_not_hidden_by_a_later_short_one(con):
    """Included Health's Florida NP seat went 41 days, then 3. Reading only
    the last interval called the whole thing a re-listing.
    """
    cid = db_mod.upsert_company(con, "Acme")
    _add(con, "gh:1", cid, "Analyst", "Austin, TX", "2026-05-01")
    _add(con, "gh:2", cid, "Analyst", "Austin, TX", "2026-06-11")
    _add(con, "gh:3", cid, "Analyst", "Austin, TX", "2026-06-14")
    entry = next(iter(reposts.history(con).values()))
    assert entry.last_gap == 3
    assert entry.real_gaps == [41]
    # The 41-day gap still shows. What changed is which ROW it is a claim
    # about: gh:2 opened the new round, and gh:3 three days later is part of
    # that round rather than another one.
    assert "A new opening" in entry.summary("gh:2")
    assert entry.follows("gh:2") == "gh:1"
    assert entry.follows("gh:3") is None
    assert "1 month" in entry.summary("gh:1"), "and the 41 days is not lost"


def test_vague_seats_are_never_reported_as_reposts(con):
    """Two unrelated Air Force openings both filed under "Multiple Locations"
    were reported as one seat re-advertised after 192 days.
    """
    cid = db_mod.upsert_company(con, "Acme")
    _add(con, "gh:1", cid, "Specialist", "Multiple Locations", "2026-01-01")
    _add(con, "gh:2", cid, "Specialist", "Multiple Locations", "2026-07-12")
    assert reposts.history(con) == {}


def test_undated_postings_are_skipped_not_guessed(con):
    cid = db_mod.upsert_company(con, "Acme")
    _add(con, "gh:1", cid, "Analyst", "Austin, TX", "2026-06-01")
    _add(con, "gh:2", cid, "Analyst", "Austin, TX", None)
    assert reposts.history(con) == {}


def test_backfill_gives_existing_rows_a_seat(con):
    """An installed database predates this column; without a backfill it could
    never report a repost, because a repost is a comparison with the past.
    """
    cid = db_mod.upsert_company(con, "Acme")
    db_mod.upsert_job(con, "gh:1", {"company_id": cid, "title": "Analyst",
                                     "location": "Austin, TX",
                                     "posted_at": "2026-06-01"})
    con.execute("UPDATE jobs SET seat = NULL")
    con.commit()
    assert reposts.backfill_seats(con) == 1
    row = db_mod.get_job(con, "gh:1")
    assert row is not None
    assert row["seat"] == reposts.seat_key("Acme", "Analyst", "Austin, TX")


def test_version_bump_recomputes_every_seat(con):
    """A change to what counts as a seat must not leave old keys in place: a
    stale key silently groups the wrong rows, which is worse than no key.
    """
    cid = db_mod.upsert_company(con, "Acme")
    db_mod.upsert_job(con, "gh:1", {"company_id": cid, "title": "Analyst",
                                     "location": "Austin, TX",
                                     "posted_at": "2026-06-01",
                                     "seat": "stale|key|value"})
    assert reposts.backfill_seats(con, stored_version=0) == 1
    row = db_mod.get_job(con, "gh:1")
    assert row is not None
    assert row["seat"] != "stale|key|value"


def test_annotate_writes_the_note_onto_every_row_of_the_seat(con):
    cid = db_mod.upsert_company(con, "Acme")
    _add(con, "gh:1", cid, "Analyst", "Austin, TX", "2026-05-01")
    _add(con, "gh:2", cid, "Analyst", "Austin, TX", "2026-07-01")
    assert reposts.annotate(con) == 2
    for key in ("gh:1", "gh:2"):
        row = db_mod.get_job(con, key)
        assert row is not None
        assert (row["repost_note"] or "").strip(), f"{key} should carry a note"
    # AND THE LINK IS ON THE NEWER ROW ONLY. The first user's rule is "treat it as a new
    # entry and link the original", so the direction matters: the new opening
    # points back, and the original does not point forward at something that
    # did not exist when it ran.
    assert db_mod.get_job(con, "gh:2")["repost_of"] == "gh:1"
    assert db_mod.get_job(con, "gh:1")["repost_of"] is None


def test_annotate_clears_a_note_that_no_longer_applies(con):
    """A row whose partner was removed must stop claiming a repost."""
    cid = db_mod.upsert_company(con, "Acme")
    _add(con, "gh:1", cid, "Analyst", "Austin, TX", "2026-05-01")
    _add(con, "gh:2", cid, "Analyst", "Austin, TX", "2026-07-01")
    reposts.annotate(con)
    con.execute("DELETE FROM jobs WHERE key = 'gh:2'")
    con.commit()
    reposts.annotate(con)
    row = db_mod.get_job(con, "gh:1")
    assert row is not None
    assert row["repost_note"] is None
