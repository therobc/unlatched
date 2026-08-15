"""A case-folding mismatch (needle lowercased, haystack not, no
re.I) previously produced two impossible results side by side on the same
run - "100% coverage, 0 gaps" for one document, and a real skill scoring
zero demand across the whole corpus. Normalisation happens on exactly one
side, once, and is documented: `present()` lowercases the term itself, and
callers are expected to lowercase the haystack before calling it.
"""
from __future__ import annotations

from unlatched import coverage


def test_mixed_case_haystack_matches_lowercase_term():
    text_lower = "Experience with SQL Server".lower()
    assert coverage.present("sql server", text_lower) is True


def test_mixed_case_term_matches_lowercase_haystack():
    text_lower = "we use powershell heavily here".lower()
    assert coverage.present("PowerShell", text_lower) is True


def test_coverage_end_to_end_is_not_case_sensitive():
    skills = ["Active Directory", "SQL", "PowerShell"]
    posting = "REQUIREMENTS: ACTIVE DIRECTORY, SQL, AND POWERSHELL EXPERIENCE."
    resume = "administered active directory, wrote sql queries, powershell scripting"
    result = coverage.coverage(posting, skills, resume)
    assert result["pct"] == 100.0
    assert result["missing"] == []


def test_a_real_skill_does_not_score_zero_demand_from_a_case_mismatch():
    # The original defect: an unnormalised comparison reported a real skill
    # ("Active Directory") as having zero demand across a corpus that
    # plainly asked for it, purely because of capitalisation.
    postings = [
        "Requirements: ACTIVE DIRECTORY administration required.",
        "Nice to have: active Directory experience.",
    ]
    demand = sum(1 for p in postings if coverage.present("Active Directory", p.lower()))
    assert demand == 2
