"""The floor must be judged against the TOP of a posted range,
never the bottom. Comparing against the bottom drops roles that pay well
above the floor - "$67,953 - $95,000" against a $70,000 floor is a false
drop if the low end of the range is what gets compared.
"""
from __future__ import annotations

from types import SimpleNamespace

from unlatched import config, enrich, screen


def test_range_survives_when_top_clears_the_floor_even_though_bottom_does_not():
    text = "The salary range for this role is $67,953 - $95,000 depending on experience."
    salary = enrich.extract_salary(text)
    assert salary["low"] == 67953
    assert salary["high"] == 95000
    assert salary["low"] < 70000 < salary["high"]


def test_screen_job_keeps_range_whose_bottom_misses_a_70k_floor():
    cfg = config.defaults()
    cfg["search"]["salary_floor"] = 70000
    job = SimpleNamespace(
        title="Support Analyst",
        location="Remote - United States",
        description=("This is a fully remote position. The salary range for this "
                      "role is $67,953 - $95,000 depending on experience. " * 3))
    result = screen.screen_job(job, cfg)
    assert result["qualified"] == 1, result["screen_reasons"]
    assert result["salary_max"] == 95000


def test_screen_job_drops_when_the_top_itself_misses_the_floor():
    cfg = config.defaults()
    cfg["search"]["salary_floor"] = 70000
    job = SimpleNamespace(
        title="Support Analyst",
        location="Remote - United States",
        description=("This is a fully remote position. The salary range for this "
                      "role is $45,000 - $55,000 depending on experience. " * 3))
    result = screen.screen_job(job, cfg)
    assert result["qualified"] == 0
    assert "salary top" in result["screen_reasons"]
