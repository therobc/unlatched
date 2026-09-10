"""keep / alt / drop, and the description getting a say.

Screening used to answer one question - does this match the search - and
record it as a bit. That threw away the distinction between a job that is
wrong and a job that is nearly right: pay a little under the floor, a stated
requirement the profile misses, a description too thin to judge. All three
landed in the same bucket as an unrelated posting.

The rule these encode: NEVER DROP ON SUSPICION. A posting that matches the
search and then raises a doubt is flagged with its evidence and left for a
person, not discarded. A job somebody never sees is one they cannot judge,
and the cost of a wrong drop is invisible in a way a wrong keep is not.
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
    # AND THE REASON WITH IT. Screening can return the right answer and the
    # dashboard still show nothing: the card queries the stored column, so a
    # reason missing from JOB_COLUMNS would be computed, discarded on write,
    # and read back as the unknown case - a job quietly on the wrong card
    # with every other test still green. Confirmed on 2026-09-02 by deleting
    # alt_reason from JOB_COLUMNS: this test failed and nothing else did.
    assert row["alt_reason"] == "salary"


def test_alt_floor_at_or_above_the_floor_is_a_config_error():
    cfg = _cfg(salary_alt_floor=70000)
    assert any("must be BELOW" in p for p in config.validate(cfg))
    cfg = _cfg(salary_alt_floor=52000)
    assert not [p for p in config.validate(cfg) if "salary_alt_floor" in p]


# --- WHICH KIND of alt ------------------------------------------------------
#
# The verdict says a row fell short. It never said what of, and the dashboard
# now asks: pay is one card, everything else is another. These pin each
# trigger to the reason it records, because the reason is what a reader is
# shown and a wrong one files the job under a heading that does not apply.
# One test per trigger: 4 of 4 places where screen_job assigns a reason.


def test_a_clean_match_records_no_alt_reason():
    assert screen.screen_job(_job(), _cfg())["alt_reason"] == ""


def test_pay_between_the_floors_records_the_salary_reason():
    jd = LONG_JD + " Salary: $58,000 - $60,000 per year."
    assert screen.screen_job(_job(description=jd), _cfg())["alt_reason"] == "salary"


def test_a_requirement_the_profile_misses_records_the_requirements_reason():
    cfg = _cfg()
    cfg["profile"]["education"] = "associate"
    jd = LONG_JD + " Requirements: Bachelor's degree in Computer Science required."
    result = screen.screen_job(_job(description=jd), cfg)
    assert result["alt_reason"] == "requirements"


def test_a_description_too_short_records_the_requirements_reason():
    result = screen.screen_job(_job(description="Remote support role."), _cfg())
    assert result["alt_reason"] == "requirements"


def test_pay_wins_when_a_job_falls_short_on_both():
    """A job that is underpaid AND a poor fit shows on the salary card.

    Not an accident of evaluation order - the money is the concrete number a
    reader dismisses it on, and a job cannot appear on both cards without the
    two counts adding up to more alt rows than exist.
    """
    cfg = _cfg()
    cfg["profile"]["education"] = "associate"
    jd = (LONG_JD + " Salary: $58,000 - $60,000 per year."
          " Requirements: Bachelor's degree in Computer Science required.")
    result = screen.screen_job(_job(description=jd), cfg)
    assert result["verdict"] == "alt"
    assert result["alt_reason"] == "salary"


def test_a_dropped_row_records_no_reason_it_did_not_earn():
    """Below both floors is a drop, not a fallback tier - verified below - so
    there is no "held back on pay" to record. A hand-added job forced to alt
    inherits this empty value, which is why the requirements card takes
    empties.
    """
    jd = LONG_JD + " Salary: $30,000 - $34,000 per year."
    result = screen.screen_job(_job(description=jd), _cfg())
    assert result["verdict"] == "drop"
    assert result["alt_reason"] == ""
