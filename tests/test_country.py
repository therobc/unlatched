"""US-only screening: remoteness says nothing about jurisdiction.

Found against real results - 41 of 133 matches in a US-only search were
foreign: "Remote - India", "Remote - UK", "Remote - Mexico", Barcelona,
Warsaw, Pune, Sydney. Two reasons. A remote-only search does not exclude
them, because "Remote - India" IS remote. And 33 of the 41 listed no salary,
so the pay floor - the accidental filter catching some of these - never
engaged; the eight that did list pay were CAD and AUD figures clearing a USD
floor.

The false positives are the hard half: Ontario is in California, London and
Paris are in Texas, Toronto is in Ohio.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from unlatched import config, country, screen

LONG_JD = "Support customers with the platform and resolve issues. " * 20


@pytest.mark.parametrize(("location", "title"), [
    ("Remote - India", ""),
    ("Remote - UK", ""),
    ("Remote - Mexico", ""),
    ("Remote - EMEA", ""),
    ("United Kingdom - London", ""),
    ("Spain - Barcelona", ""),
    ("Poland - Warsaw", ""),
    ("Vancouver, British Columbia, Canada", ""),
    ("Sydney, Australia", ""),
    ("India - Pune", ""),
    ("Amsterdam", ""),
    # The country lives in the TITLE and the location says only "(Remote)".
    ("(Remote)", "Support Engineer, Singapore"),
    ("Remote", "Technical Support Engineer - Canada"),
])
def test_foreign_postings_are_recognised(location: str, title: str):
    foreign, evidence = country.is_foreign(location, title)
    assert foreign is True
    assert evidence


@pytest.mark.parametrize(("location", "title"), [
    # US cities sharing a name with a foreign one. A state always wins.
    ("London, KY", ""),
    ("Paris, TX", ""),
    ("Toronto, OH", ""),
    ("Ontario, California", ""),
    # Abbreviations that are also ordinary words, uppercase in a location.
    ("Portland, OR", ""),
    ("Indianapolis, IN", ""),
    ("US Remote", "Support Engineer"),
    ("New York, NY, United States", ""),
    # An explicit US location outranks a country word in the title.
    ("Remote - US", "Engineer, Canada Team"),
    # Silence is not evidence of anything.
    ("", ""),
])
def test_domestic_postings_are_not_mistaken_for_foreign(location: str, title: str):
    assert country.is_foreign(location, title)[0] is False


def test_one_us_option_among_several_keeps_the_posting():
    """A multi-site role the person could take in Austin is still a job."""
    assert country.is_foreign("Austin, TX; London, England", "")[0] is False


def _job(location: str, title: str = "Technical Support Engineer") -> SimpleNamespace:
    return SimpleNamespace(
        title=title, location=location, url="", description=LONG_JD,
        employment_type="")


def _cfg(us_only: bool = True) -> dict:
    cfg = config.defaults()
    cfg["search"]["title_include"] = ["support"]
    cfg["search"]["remote_scope"] = "remote_only"
    cfg["search"]["us_only"] = us_only
    return cfg


def test_a_foreign_remote_role_is_dropped_despite_being_remote():
    result = screen.screen_job(_job("Remote - India"), _cfg())
    assert result["verdict"] == "drop"
    assert "outside the US" in result["screen_reasons"]


def test_a_us_remote_role_is_kept():
    assert screen.screen_job(_job("US Remote"), _cfg())["verdict"] == "keep"


def test_turning_us_only_off_stops_filtering():
    assert screen.screen_job(_job("Remote - India"), _cfg(us_only=False))["verdict"] == "keep"
