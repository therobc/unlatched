"""Full time, part time, contract - one vocabulary, and a flag rather than a
filter.

Every ATS names this differently. These literals were all observed in
collected data: "Full time", "Full-Time", "Fulltime-Regular", "Part time",
"Independent Contractor", and "['FULL_TIME']" - the last being a collector
handing back a list whose repr reached the column.

Somebody open only to full time will still read the contract and part-time
postings that surface, and mark them passed themselves. So an
unaccepted type is an "alt", never a drop. Dismissing a job takes two
seconds; a job never shown cannot be recovered.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from unlatched import config, employment, screen

LONG_JD = "Support customers with the platform and resolve issues. " * 20


@pytest.mark.parametrize(("raw", "expected"), [
    ("Full time", "full_time"),
    ("Full-Time", "full_time"),
    ("Fulltime-Regular", "full_time"),
    ("['FULL_TIME']", "full_time"),
    ("Part time", "part_time"),
    ("Part-Time", "part_time"),
    ("Independent Contractor", "contract"),
    ("Independent Contractor T2", "contract"),
    ("Seasonal", "temporary"),
    ("Summer Internship", "internship"),
    ("['OTHER']", None),
    ("", None),
    ("Gibberish", None),
])
def test_vendor_strings_normalise_to_one_vocabulary(raw: str, expected: str | None):
    assert employment.normalize(raw) == expected


def test_the_more_specific_kind_wins():
    """A summer internship is an internship even when it is also full time,
    and contract-to-hire is a contract rather than a hire.
    """
    assert employment.normalize("Full time internship") == "internship"
    assert employment.normalize("Contract to hire") == "contract"


def test_prose_is_a_fallback_when_no_field_was_provided():
    assert employment.detect("", "", "12+ months contract. " + LONG_JD) == "contract"
    assert employment.detect("", "Part-Time Support Analyst") == "part_time"


def test_the_structured_field_beats_the_prose():
    """The employer's own answer wins over words further down the page."""
    assert employment.detect("Full time", "", "Contract opportunities available") == "full_time"


def test_benefits_boilerplate_far_below_does_not_decide_the_type():
    jd = LONG_JD + " " * 400 + "Full-time employees are eligible for dental."
    assert employment.detect("", "Support Analyst", jd) is None


def _cfg(**search: Any) -> dict[str, Any]:
    cfg = config.defaults()
    cfg["search"]["title_include"] = ["support"]
    cfg["search"].update(search)
    return cfg


def _job(employment_type: str = "", description: str = LONG_JD) -> SimpleNamespace:
    return SimpleNamespace(
        title="Support Analyst", location="US Remote", url="",
        description=description, employment_type=employment_type)


def test_an_unaccepted_type_is_alt_and_never_a_drop():
    cfg = _cfg(employment_types=["full_time"])
    result = screen.screen_job(_job("Part time"), cfg)
    assert result["verdict"] == "alt"
    assert result["qualified"] == 1, "an unwanted type must stay visible"
    assert "part time" in result["screen_reasons"]


def test_an_accepted_type_is_a_clean_keep():
    cfg = _cfg(employment_types=["full_time"])
    assert screen.screen_job(_job("Full time"), cfg)["verdict"] == "keep"


def test_no_types_ticked_accepts_everything():
    cfg = _cfg(employment_types=[])
    for raw in ("Full time", "Part time", "Independent Contractor"):
        assert screen.screen_job(_job(raw), cfg)["verdict"] == "keep"


def test_a_posting_that_never_states_a_type_is_not_penalised():
    """Silence is not a reason to hide a job - most postings say nothing."""
    cfg = _cfg(employment_types=["full_time"])
    assert screen.screen_job(_job(""), cfg)["verdict"] == "keep"


def test_unknown_kinds_are_rejected_by_config_validation():
    cfg = _cfg(employment_types=["full_time", "freelance_ish"])
    assert any("employment_types" in p for p in config.validate(cfg))
    cfg = _cfg(employment_types=list(employment.KINDS))
    assert not [p for p in config.validate(cfg) if "employment_types" in p]
