"""keep / alt / drop, and the description getting a say.

Screening used to answer one question - does this match the search - and
record it as a bit. That threw away the distinction between a job that is
wrong and a job that is nearly right: pay a little under the floor, a stated
requirement the profile misses, a description too thin to judge. All three
landed in the same bucket as an unrelated posting.

The rule these encode, from the predecessor pipeline: NEVER DROP ON
SUSPICION. A posting that matches the search and then raises a doubt is
flagged with its evidence and left for a person, not discarded.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from unlatched import config, screen

LONG_JD = "Support customers with our platform and resolve issues. " * 20


def _cfg(**search: Any) -> dict[str, Any]:
    cfg = config.defaults()
    cfg["search"]["title_include"] = ["support", "analyst"]
    cfg["search"]["salary_floor"] = 70000
    cfg["search"]["salary_alt_floor"] = 52000
    cfg["search"].update(search)
    return cfg


def _job(title: str = "Support Analyst", description: str = LONG_JD) -> SimpleNamespace:
    return SimpleNamespace(
        title=title, location="US Remote", url="", description=description)


def test_clean_match_is_keep():
    result = screen.screen_job(_job(), _cfg())
    assert result["verdict"] == "keep"
    assert result["qualified"] == 1


def test_pay_between_the_two_floors_is_alt_not_a_drop():
    """A fallback tier in a thin market. Shown and flagged, but distinct from
    a clean match so it does not flatter the count.
    """
    jd = LONG_JD + " Salary: $58,000 - $60,000 per year."
    result = screen.screen_job(_job(description=jd), _cfg())
    assert result["verdict"] == "alt"
    assert result["qualified"] == 1
    assert "fallback floor" in result["screen_reasons"]


def test_pay_below_both_floors_still_drops():
    jd = LONG_JD + " Salary: $30,000 - $34,000 per year."
    result = screen.screen_job(_job(description=jd), _cfg())
    assert result["verdict"] == "drop"
    assert result["qualified"] == 0


def test_no_alt_floor_configured_means_under_the_floor_drops():
    jd = LONG_JD + " Salary: $58,000 - $60,000 per year."
    result = screen.screen_job(_job(description=jd), _cfg(salary_alt_floor=None))
    assert result["verdict"] == "drop"


def test_undisclosed_pay_is_never_penalised():
    """Most strong roles post no range. Dropping them would hide the best
    listings, so silence skips the floor entirely.
    """
    assert screen.screen_job(_job(), _cfg())["verdict"] == "keep"


def test_a_stated_requirement_the_profile_misses_is_alt():
    cfg = _cfg()
    cfg["profile"]["education"] = "associate"
    jd = LONG_JD + " Requirements: Bachelor's degree in Computer Science required."
    result = screen.screen_job(_job(description=jd), cfg)
    assert result["verdict"] == "alt"
    assert "bachelor" in result["screen_reasons"].lower()


def test_a_preferred_degree_is_not_a_blocker_and_stays_keep():
    cfg = _cfg()
    cfg["profile"]["education"] = "associate"
    jd = LONG_JD + " Minimum Education: Bachelor's degree preferred."
    assert screen.screen_job(_job(description=jd), cfg)["verdict"] == "keep"


def test_a_degree_with_equivalent_experience_stays_keep():
    cfg = _cfg()
    cfg["profile"]["education"] = "associate"
    jd = LONG_JD + " Bachelor's degree or equivalent experience."
    assert screen.screen_job(_job(description=jd), cfg)["verdict"] == "keep"


def test_a_description_too_short_to_judge_is_alt_not_a_drop():
    """Scoring badly on 40 characters means we failed to READ the posting,
    not that the job is wrong.
    """
    result = screen.screen_job(_job(description="Remote support role."), _cfg())
    assert result["verdict"] == "alt"
    assert result["qualified"] == 1
    assert "too short" in result["screen_reasons"]


def test_a_real_miss_is_still_a_drop():
    result = screen.screen_job(_job(title="Chef de Partie"), _cfg())
    assert result["verdict"] == "drop"
    assert result["qualified"] == 0


def test_verdict_reaches_the_database(tmp_path, monkeypatch):
    from unlatched import db

    monkeypatch.setenv("UNLATCHED_HOME", str(tmp_path))
    con = db.connect(tmp_path)
    company_id = db.upsert_company(con, "Acme")
    jd = LONG_JD + " Salary: $58,000 - $60,000 per year."
    fields = screen.screen_job(_job(description=jd), _cfg())
    fields.update({"company_id": company_id, "title": "Support Analyst"})
    db.upsert_job(con, "greenhouse:1", fields)
    row = db.get_job(con, "greenhouse:1")
    con.close()
    assert row is not None
    assert row["verdict"] == "alt"


def test_alt_floor_at_or_above_the_floor_is_a_config_error():
    cfg = _cfg(salary_alt_floor=70000)
    assert any("must be BELOW" in p for p in config.validate(cfg))
    cfg = _cfg(salary_alt_floor=52000)
    assert not [p for p in config.validate(cfg) if "salary_alt_floor" in p]
