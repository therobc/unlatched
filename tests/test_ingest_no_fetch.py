"""--no-fetch: the caller already read the page, so this app must not.

An earlier change asked for one writer to LinkedIn, ENFORCED rather than observed.
Before this flag it was only observed: add() consulted a user SETTING, so a
collector pushing rows it had already read would make this app fetch the same
pages again the moment somebody turned that setting on.

These tests assert on whether a REQUEST HAPPENS, not on the row that comes out.
The row was always correct - typed values beat fetched ones - which is exactly
why the breach would have been silent.
"""
from __future__ import annotations

import pytest

from unlatched import manual

LINKEDIN = "https://www.linkedin.com/jobs/view/4012345"
ORDINARY = "https://boards.greenhouse.io/acme/jobs/99"


class RecordingFetcher:
    """Stands in for the network and remembers whether it was asked."""

    def __init__(self, html: str = ""):
        self.calls: list[str] = []
        self.html = html

    def __call__(self, url, **kwargs):
        self.calls.append(url)
        return (200, self.html, url)


def add(con, cfg, url, fetcher, **kwargs):
    return manual.add(con, cfg, url, title="Support Analyst",
                      fetcher=fetcher, **kwargs)


@pytest.fixture
def reading_on():
    return {"fetch": {"read_added_links": True}}


def test_no_fetch_stops_the_request_even_with_reading_switched_on(con, reading_on):
    """The case the flag exists for. Without it, a collector that has already
    read a LinkedIn page makes this app read it a second time."""
    fetcher = RecordingFetcher()
    add(con, reading_on, LINKEDIN, fetcher, no_fetch=True)
    assert fetcher.calls == [], "nothing may be requested when the caller says it has the data"


def test_without_no_fetch_the_setting_still_governs(con, reading_on):
    """The flag must not change what happens when it is absent - a person
    adding a link by hand is unaffected by any of this."""
    fetcher = RecordingFetcher()
    add(con, reading_on, LINKEDIN, fetcher)
    assert fetcher.calls == [LINKEDIN]


def test_no_fetch_can_only_suppress_a_request_never_cause_one(con):
    """It overrides the preference in the safe direction only. With reading
    off, the flag changes nothing."""
    fetcher = RecordingFetcher()
    add(con, {"fetch": {"read_added_links": False}}, LINKEDIN, fetcher, no_fetch=True)
    assert fetcher.calls == []


def test_no_fetch_applies_to_ordinary_boards_too(con, reading_on):
    """Not a LinkedIn special case: a caller supplying data for any host is
    saying the same thing, and a second read is waste wherever it lands."""
    fetcher = RecordingFetcher()
    add(con, reading_on, ORDINARY, fetcher, no_fetch=True)
    assert fetcher.calls == []


def test_the_supplied_row_is_stored_intact_when_nothing_is_fetched(con, reading_on):
    """The data half. What the caller passed has to survive being the only
    source, including the two fields that previously had no way in."""
    fetcher = RecordingFetcher()
    result = manual.add(
        con, reading_on, LINKEDIN,
        title="Technology Operations Support Analyst", company="Northwind",
        location="Remote - US", description="Support the operations team.",
        posted="2026-08-01",
        apply_url="https://apply.workable.com/northwind/j/ABC123/?utm_source=linkedin",
        no_fetch=True, fetcher=fetcher)

    assert fetcher.calls == []
    row = con.execute(
        "SELECT title, location, posted_at, apply_url, description FROM jobs WHERE key = ?",
        (result["key"],)).fetchone()
    assert row["title"] == "Technology Operations Support Analyst"
    assert row["location"] == "Remote - US"
    assert row["posted_at"] == "2026-08-01"
    # Stored NORMALISED, so the join is a plain equality test at query time.
    assert row["apply_url"] == "https://apply.workable.com/northwind/j/ABC123"


def test_two_boards_pointing_at_one_application_agree_on_the_key(con, reading_on):
    """The cross-board join, end to end through the real add path: a LinkedIn
    row whose apply link is wrapped, and the ATS row it forwards to."""
    fetcher = RecordingFetcher()
    manual.add(con, reading_on, LINKEDIN, title="Analyst", no_fetch=True,
               apply_url=("https://www.linkedin.com/safety/go/?url="
                          "https%3A%2F%2Fapply%2Eworkable%2Ecom%2Fnorthwind%2Fj%2FABC123%2F"
                          "&trk=public_jobs_apply-link-offsite"),
               fetcher=fetcher)
    manual.add(con, reading_on, "https://apply.workable.com/northwind/j/ABC123/",
               title="Analyst", no_fetch=True,
               apply_url="https://apply.workable.com/northwind/j/ABC123/",
               fetcher=fetcher)

    keys = con.execute(
        "SELECT apply_url, COUNT(*) AS n FROM jobs WHERE apply_url != '' "
        "GROUP BY apply_url").fetchall()
    assert len(keys) == 1, "both rows must land on ONE apply key"
    assert keys[0]["n"] == 2
