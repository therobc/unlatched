"""Per-posting keyword gaps: which skills THIS job asks for that the resume
does not evidence.

Exists for the person with no model, who wants a concrete list of words to
work into their own resume by hand. So the properties that matter are that
the list is specific to the posting, that it shrinks as the resume improves,
and above all that it never reports a gap it cannot actually see - telling
someone they are missing forty skills when their resume was simply unreadable
would send them rewriting a document that was already fine.
"""
from __future__ import annotations

from types import SimpleNamespace

from unlatched import screen

JD = ("We are hiring a support analyst. You will troubleshoot hardware, "
      "diagnose network faults, and own customer service for our field team. "
      "Active Directory experience required. This is a remote role in the "
      "United States.")

SKILLS = ["Troubleshooting", "Active Directory", "Customer Service", "Kubernetes"]


def _cfg(cfg):
    cfg["skills"] = SKILLS
    cfg["search"]["title_include"] = ["support analyst"]
    cfg["search"]["remote_scope"] = "remote_only"
    return cfg


def _job():
    return SimpleNamespace(title="Support Analyst", location="Remote - US",
                           description=JD, employment_type="Full-time")


def test_gaps_name_only_what_the_posting_asked_for(cfg):
    """Kubernetes is in the vocabulary but not in this posting, so it is not a
    gap against this job - coverage is covered/asked, never covered/everything.
    """
    out = screen.screen_job(_job(), _cfg(cfg), resume_text="I have done troubleshooting.")
    missing = out["missing_skills"]
    assert "Kubernetes" not in missing
    assert "Active Directory" in missing
    assert "Customer Service" in missing


def test_a_covered_skill_leaves_the_gap_list(cfg):
    resume = "Troubleshooting, Active Directory, customer service."
    out = screen.screen_job(_job(), _cfg(cfg), resume_text=resume)
    assert out["missing_skills"] == ""
    assert out["coverage_pct"] == 100.0


def test_no_resume_means_everything_asked_is_missing(cfg):
    out = screen.screen_job(_job(), _cfg(cfg), resume_text="")
    assert out["coverage_pct"] == 0.0
    assert "Active Directory" in out["missing_skills"]
    assert "Customer Service" in out["missing_skills"]


def test_a_gerund_skill_does_not_match_a_posting_using_the_bare_verb(cfg):
    """Documented asymmetry, asserted so a change to it is deliberate.

    coverage.present() inflects a term FORWARD - "diagnose" also matches
    "diagnosing" - but never backward. So a vocabulary entry typed
    "Troubleshooting" does not match a posting that says "troubleshoot", and
    the skill is not counted as asked at all. It under-reports demand rather
    than inventing it, which is the safer direction, but a user whose skill
    list is written in -ing form sees fewer gaps than really exist.
    """
    out = screen.screen_job(_job(), _cfg(cfg), resume_text="")
    assert "Troubleshooting" not in out["missing_skills"]


def test_no_vocabulary_reports_nothing_rather_than_a_false_zero(cfg):
    """With no skills configured there is nothing to measure. Reporting 0%
    would read as "you match none of this" when the truth is "not assessed".
    """
    cfg["skills"] = []
    out = screen.screen_job(_job(), cfg, resume_text="anything")
    assert out["coverage_pct"] is None
    assert out["missing_skills"] == ""


def test_gaps_do_not_change_the_verdict(cfg):
    """A missing keyword is something to go fix, not a reason to hide the job."""
    with_resume = screen.screen_job(_job(), _cfg(cfg), resume_text="troubleshooting")
    without = screen.screen_job(_job(), _cfg(cfg), resume_text="")
    assert with_resume["verdict"] == without["verdict"]
    assert with_resume["score"] == without["score"]


def test_inflections_are_not_reported_as_gaps(cfg):
    """A resume saying "diagnosed" evidences "Diagnose". Exact matching would
    invent a gap and send someone editing a resume that was already fine.
    """
    cfg = _cfg(cfg)
    cfg["skills"] = [*SKILLS, "Diagnose"]
    out = screen.screen_job(_job(), cfg, resume_text="Diagnosed network faults for 6 years.")
    assert "Diagnose" not in out["missing_skills"]
