"""Coverage must be measured against a fixed skill vocabulary,
not the share of every distinct word in a posting. Adding wordier prose to a
description - without adding any new named skill - must not move the
number. The earlier word-share metric failed exactly this: coverage fell
purely because richer descriptions widened the denominator with prose the
resume had no reason to contain.
"""
from __future__ import annotations

from unlatched import coverage

SKILLS = ["SQL", "Zendesk", "Active Directory", "PowerShell", "REST API"]
RESUME = "Experienced with SQL, Zendesk ticketing, and PowerShell scripting."

BASE_POSTING = (
    "Requirements: SQL, Zendesk, Active Directory, PowerShell, REST API "
    "experience required."
)

WORDIER_POSTING = (
    BASE_POSTING + " We are a collaborative, passionate team of stakeholders "
    "who believe in synergy, growth mindset, and delivering delightful "
    "customer experiences across a dynamic, fast-paced environment where "
    "everyone brings their whole self to work every single day."
)


def test_coverage_percentage_is_stable_when_prose_is_added_without_new_skills():
    base = coverage.coverage(BASE_POSTING, SKILLS, RESUME)
    wordier = coverage.coverage(WORDIER_POSTING, SKILLS, RESUME)
    assert base["pct"] == wordier["pct"]
    assert set(base["asked"]) == set(wordier["asked"])
    assert set(base["covered"]) == set(wordier["covered"])
    assert set(base["missing"]) == set(wordier["missing"])


def test_coverage_only_counts_skills_actually_asked_for():
    posting = "Requirements: SQL and Zendesk experience required."
    result = coverage.coverage(posting, SKILLS, RESUME)
    assert set(result["asked"]) == {"SQL", "Zendesk"}
    assert "Active Directory" not in result["asked"]
    assert result["pct"] == 100.0
