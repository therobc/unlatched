"""A dollar amount sitting in a benefits sentence is not pay -
"401(k) up to $5,000" and "$250/year for gym memberships" both contain
money that is not compensation, and the parser needs a guard on the
surrounding context before it accepts a figure as a salary at all.
"""
from __future__ import annotations

from unlatched import screen


def test_401k_match_is_not_treated_as_compensation():
    text = "We offer a 401(k) matching contribution up to $5,000 per year."
    assert screen.salary_is_credible(text, "$5,000") is False


def test_gym_stipend_is_not_treated_as_compensation():
    text = "Wellness benefits include a $250/year gym membership stipend."
    assert screen.salary_is_credible(text, "$250") is False


def test_real_salary_range_is_still_credible():
    text = "The base salary range for this role is $75,000 - $95,000 annually."
    assert screen.salary_is_credible(text, "$75,000 - $95,000") is True


def test_screen_job_discards_benefit_figure_and_records_why():
    from types import SimpleNamespace

    from unlatched import config

    cfg = config.defaults()
    job = SimpleNamespace(
        title="Support Analyst",
        location="Remote",
        description=("This is a remote role. We offer a 401(k) matching "
                      "contribution up to $5,000 per year and no other stated pay."))
    result = screen.screen_job(job, cfg)
    assert result["salary_max"] is None
    assert "benefits context" in result["screen_reasons"]
