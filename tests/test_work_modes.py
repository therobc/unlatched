"""Remote, hybrid, or onsite - and which of the three a search accepts.

Decided 2026-08-05: three tick boxes, and ticking only one intuitively means
only that. Hybrid is the one that earns the change: it used to read as
remote (a hybrid posting nearly always says "remote" somewhere), so someone
searching for remote work got postings that expect them in the office three
days a week.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from unlatched import config, screen


def _job(location: str, description: str,
         title: str = "Support Analyst") -> SimpleNamespace:
    return SimpleNamespace(title=title, location=location, url="",
                           description=description, employment_type="")


def mode(description: str, title: str = "Support Analyst",
         location: str = "Knoxville, TN") -> str:
    return screen.work_mode(title, location, description)[0]


def test_a_plain_remote_posting_is_remote():
    assert mode("This is a fully remote position.") == "remote"
    assert mode("Work from home, anywhere in the US.") == "remote"


def test_a_posting_with_no_wording_at_all_is_onsite():
    """Most onsite postings never say so - to the employer writing it, that
    is simply what a job is."""
    assert mode("Answer tickets and support end users.") == "onsite"


def test_hybrid_is_not_remote():
    assert mode("This is a hybrid role, 3 days in the office.") == "hybrid"
    assert mode("Hybrid schedule with remote work on Fridays.") == "hybrid"
    assert mode("Remote-friendly. 2 days per week in the office.") == "hybrid"


def test_hybrid_the_technology_is_not_hybrid_the_schedule():
    """The trap this pattern exists for: IT postings are full of the word.
    Every one of these is a fully remote job that must not become hybrid.
    """
    for text in (
        "Fully remote. Experience with hybrid cloud required.",
        "Remote position supporting a hybrid Exchange environment.",
        "100% remote. Hybrid AD/Entra migration experience preferred.",
    ):
        assert mode(text) == "remote", text


def test_explicit_onsite_language_beats_a_careless_location_field():
    assert mode("Must be on-site daily.", location="Remote") == "onsite"


def test_a_search_with_no_ticks_accepts_everything():
    assert screen.wanted_modes({}) == []
    assert screen.wanted_modes({"work_modes": []}) == []


def test_ticking_one_means_only_that_one():
    assert screen.wanted_modes({"work_modes": ["remote"]}) == ["remote"]
    assert screen.wanted_modes(
        {"work_modes": ["remote", "hybrid"]}) == ["remote", "hybrid"]


def test_an_older_config_keeps_the_search_it_had():
    """remote_scope was the setting before the ticks. A config still
    carrying it must not silently start accepting onsite work."""
    assert screen.wanted_modes({"remote_scope": "remote_only"}) == ["remote"]
    assert screen.wanted_modes({"remote_scope": "any"}) == []
    # And the ticks win once they are set.
    assert screen.wanted_modes(
        {"remote_scope": "remote_only", "work_modes": ["onsite"]}) == ["onsite"]


def test_nonsense_in_work_modes_is_ignored_not_obeyed():
    """A typo must not silently filter everything out - an empty result
    means no restriction, which is the safe direction to fail in."""
    assert screen.wanted_modes({"work_modes": ["Remote", "wfh"]}) == ["remote"]
    assert screen.wanted_modes({"work_modes": ["wfh"]}) == []


def test_validate_rejects_a_mode_that_is_not_one_of_the_three():
    problems = config.validate({"search": {"work_modes": ["remote", "wfh"]}})
    assert any("work_modes" in p and "wfh" in p for p in problems)
    assert config.validate({"search": {"work_modes": ["remote"]}}) == []
    assert any("must be a list" in p
               for p in config.validate({"search": {"work_modes": "remote"}}))


@pytest.mark.parametrize(("modes", "expected_kept"), [
    (["remote"], {"remote"}),
    (["hybrid"], {"hybrid"}),
    (["onsite"], {"onsite"}),
    (["remote", "hybrid"], {"remote", "hybrid"}),
    ([], {"remote", "hybrid", "onsite"}),
])
def test_screening_keeps_exactly_the_ticked_modes(modes, expected_kept):
    postings = {
        "remote": "This is a fully remote position. " + "Support work. " * 40,
        "hybrid": "Hybrid role, 3 days in the office. " + "Support work. " * 40,
        "onsite": "Join our Knoxville team. " + "Support work. " * 40,
    }
    cfg = config.defaults()
    cfg["search"]["work_modes"] = modes
    cfg["search"]["title_include"] = ["analyst"]

    kept = set()
    for name, description in postings.items():
        result = screen.screen_job(_job("Knoxville, TN", description), cfg)
        if result["qualified"]:
            kept.add(name)
    assert kept == expected_kept


def test_a_hybrid_role_is_checked_against_the_places_you_can_work():
    """Hybrid means going in, so the commute question applies - it is skipped
    only for genuinely remote roles."""
    cfg = config.defaults()
    cfg["search"]["title_include"] = ["analyst"]
    cfg["search"]["locations"] = ["Knoxville, TN"]
    cfg["search"]["work_modes"] = ["hybrid"]
    result = screen.screen_job(
        _job("Phoenix, AZ", "Hybrid role, 3 days in the office. " + "Work. " * 60),
        cfg)
    assert not result["qualified"]
    assert "location" in result["screen_reasons"]
