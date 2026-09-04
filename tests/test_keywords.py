"""The keyword demand report aggregates coverage.present() over every
description in a corpus instead of one posting, and cross-checks each skill
against the resume once. These tests protect the invariants that make it
trustworthy: demand counting stays inflection-aware (the same matcher as
per-job coverage), an empty vocabulary or empty corpus returns an empty
report instead of raising or dividing by zero, and the ranking is sorted by
demand with a stable tie-break.
"""
from __future__ import annotations

import json

from unlatched import cli, db, keywords

SKILLS = ["SQL", "Zendesk", "PowerShell", "Diagnose"]
RESUME = "Experienced with SQL and PowerShell scripting."

CORPUS = [
    "Requirements: SQL, Zendesk, PowerShell experience required.",
    "Looking for someone skilled in diagnosing network issues with SQL.",
    "General office role, no technical requirements listed.",
]


def test_demand_counts_documents_mentioning_the_skill():
    report = keywords.demand_report(CORPUS, SKILLS, RESUME)
    by_skill = {r["skill"]: r for r in report}
    assert by_skill["SQL"]["demand"] == 2
    assert by_skill["Zendesk"]["demand"] == 1


def test_demand_is_inflection_aware():
    # "Diagnose" only ever appears in the corpus as "diagnosing" - the
    # matcher has to credit that as the same skill, not a literal miss.
    report = keywords.demand_report(CORPUS, SKILLS, RESUME)
    by_skill = {r["skill"]: r for r in report}
    assert by_skill["Diagnose"]["demand"] == 1


def test_skill_in_no_posting_reports_demand_zero():
    report = keywords.demand_report(CORPUS, [*SKILLS, "Kubernetes"], RESUME)
    by_skill = {r["skill"]: r for r in report}
    assert by_skill["Kubernetes"]["demand"] == 0
    assert by_skill["Kubernetes"]["pct"] == 0.0


def test_evidenced_flips_against_the_resume():
    report = keywords.demand_report(CORPUS, SKILLS, RESUME)
    by_skill = {r["skill"]: r for r in report}
    assert by_skill["SQL"]["evidenced"] is True
    assert by_skill["Zendesk"]["evidenced"] is False


def test_evidenced_is_false_for_everything_with_no_resume_text():
    report = keywords.demand_report(CORPUS, SKILLS, "")
    assert all(r["evidenced"] is False for r in report)


def test_ordering_is_by_demand_descending_with_stable_ties():
    # SQL leads at demand 2; Zendesk/PowerShell/Diagnose tie at 1 and keep
    # their original vocabulary order (Python's sort is stable).
    report = keywords.demand_report(CORPUS, SKILLS, RESUME)
    assert [r["skill"] for r in report] == ["SQL", "Zendesk", "PowerShell", "Diagnose"]


def test_empty_vocabulary_returns_empty_report():
    assert keywords.demand_report(CORPUS, [], RESUME) == []


def test_empty_corpus_returns_empty_report():
    assert keywords.demand_report([], SKILLS, RESUME) == []


def test_pct_is_demand_over_corpus_size():
    report = keywords.demand_report(CORPUS, SKILLS, RESUME)
    by_skill = {r["skill"]: r for r in report}
    assert by_skill["SQL"]["pct"] == round(100.0 * 2 / 3, 1)
    assert by_skill["Zendesk"]["pct"] == round(100.0 * 1 / 3, 1)


def test_cli_keywords_json_shape_on_empty_vocabulary(tmp_path, capsys):
    home = tmp_path / "home"
    con = db.connect(home)
    db.upsert_job(con, "greenhouse:1", {"title": "Support Analyst",
                                         "description": "SQL and Zendesk required.",
                                         "qualified": 1})
    con.close()
    # No config.json is written for this home, so config.skills defaults to
    # [] - this is the "empty vocabulary" case the report must not error on.

    rc = cli.main(["--home", str(home), "keywords", "--json"])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == []


def test_cli_keywords_human_output_shows_gaps_and_covered(tmp_path, capsys):
    home = tmp_path / "home"
    con = db.connect(home)
    db.upsert_job(con, "greenhouse:1", {"title": "Support Analyst",
                                         "description": "SQL, Zendesk, PowerShell required.",
                                         "qualified": 1})
    con.close()
    resume = tmp_path / "resume.txt"
    resume.write_text("Experienced with SQL scripting.", encoding="utf-8")
    rc = cli.main(["--home", str(home), "config", "set", "skills",
                   json.dumps(["SQL", "Zendesk"])])
    assert rc == 0
    rc = cli.main(["--home", str(home), "config", "set", "resume_path", str(resume)])
    assert rc == 0
    capsys.readouterr()

    rc = cli.main(["--home", str(home), "keywords"])

    assert rc == 0
    out = capsys.readouterr().out
    assert "GAPS" in out
    assert "Zendesk" in out
    assert "COVERED" in out
    assert "SQL" in out
