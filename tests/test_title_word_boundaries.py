"""Title include/exclude/seniority terms match whole words, never bare
substrings. Found against real postings: the include term "NOC" matched
inside "Nocturnist" and qualified an "Urgent Care Nocturnist Physician"
posting for an IT support search. Short acronyms are exactly the terms a
person puts in these lists, so substring matching is never what they mean.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from unlatched import config, screen
from unlatched.screen import term_in_title, title_wants


def test_short_acronym_does_not_match_inside_a_longer_word():
    assert screen.term_in_title("NOC", "NOC Technician") is True
    assert screen.term_in_title("NOC", "Urgent Care Nocturnist Physician") is False


def test_multi_word_phrase_still_matches():
    assert screen.term_in_title("help desk", "Help Desk Analyst II") is True
    assert screen.term_in_title("service desk", "IT Service Desk Engineer") is True


def test_exclude_term_does_not_fire_on_a_longer_word():
    # "sales" must not reject "Wholesaler Support Specialist" by hiding
    # inside "Wholesaler".
    assert screen.term_in_title("sales", "Wholesaler Support Specialist") is False
    assert screen.term_in_title("sales", "Sales Engineer") is True


def test_seniority_term_still_excludes():
    assert screen.term_in_title("senior", "Senior Support Analyst") is True
    assert screen.term_in_title("lead", "Lead Support Engineer") is True
    # "lead" must not fire on "Leadership Development Support Analyst".
    assert screen.term_in_title("lead", "Leadership Program Support Analyst") is False


def test_screen_job_rejects_the_real_world_false_positive():
    cfg = config.defaults()
    cfg["search"]["title_include"] = ["support", "NOC", "help desk"]
    physician = SimpleNamespace(
        title="Urgent Care Nocturnist Physician",
        location="Remote",
        description="Provide urgent care services to patients. " * 5)
    result = screen.screen_job(physician, cfg)
    assert result["qualified"] == 0
    assert "title_include" in result["screen_reasons"]


def test_screen_job_still_keeps_a_real_noc_role():
    cfg = config.defaults()
    cfg["search"]["title_include"] = ["support", "NOC", "help desk"]
    noc = SimpleNamespace(
        title="NOC Analyst",
        location="Remote - United States",
        description="Monitor the network operations center. " * 5)
    result = screen.screen_job(noc, cfg)
    assert result["qualified"] == 1


def test_include_matches_when_words_are_separated():
    """Somebody who types "HR Specialist" means "Human Resources Onboarding
    Specialist" too. Requiring adjacency dropped exactly those postings.
    """
    assert screen.title_wants("human resources specialist",
                               "Human Resources Onboarding Specialist") is True
    assert screen.title_wants("hr specialist", "HR Operations Specialist") is True
    assert screen.title_wants("care coordinator",
                               "Care Transitions Coordinator") is True


def test_include_still_requires_every_word():
    assert screen.title_wants("hr specialist", "Marketing Specialist") is False
    assert screen.title_wants("human resources specialist",
                               "Human Resources Manager") is False


def test_exclusions_stay_exact():
    # Loose matching here would hide jobs a person should see: this title
    # is not an account executive posting just because both words occur.
    assert screen.term_in_title("account executive",
                                 "Executive Assistant, Account Services") is False
    assert screen.term_in_title("account executive", "Account Executive II") is True


def test_screen_job_now_keeps_the_interleaved_title():
    cfg = config.defaults()
    cfg["search"]["title_include"] = ["human resources specialist"]
    job = SimpleNamespace(
        title="Human Resources Onboarding Specialist",
        location="Remote - United States",
        description="Support onboarding and employee records. " * 6)
    assert screen.screen_job(job, cfg)["qualified"] == 1


def _remote_job():
    return SimpleNamespace(
        title="Support Analyst", location="Remote - United States",
        description="This is a fully remote position. " * 6)


def test_remote_does_not_flatter_a_search_that_never_asked_for_it():
    cfg = config.defaults()
    cfg["search"]["remote_scope"] = "any"
    result = screen.screen_job(_remote_job(), cfg)
    assert result["qualified"] == 1
    assert result["remote"] == "yes"          # still reported
    assert result["score"] == 60.0            # but not rewarded


def test_remote_is_rewarded_when_the_person_wants_it():
    cfg = config.defaults()
    cfg["search"]["remote_scope"] = "prefer_remote"
    assert screen.screen_job(_remote_job(), cfg)["score"] == 70.0


def test_prefer_remote_still_shows_onsite_roles():
    cfg = config.defaults()
    cfg["search"]["remote_scope"] = "prefer_remote"
    onsite = SimpleNamespace(
        title="Support Analyst", location="Columbus, Ohio",
        description="Provide desktop support in our office. " * 6)
    assert screen.screen_job(onsite, cfg)["qualified"] == 1


# ---- compound spellings -------------------------------------------------
#
# "Help Desk" and "Helpdesk" are the same job, but a word-boundary test
# shares nothing between them: there is no boundary after "help" inside
# "Helpdesk". A real "IT HelpDesk Analyst" sat uncollected in the corpus
# for exactly this reason while the term "help desk" reported zero matches
# across 6,017 postings.



@pytest.mark.parametrize(("term", "title"), [
    ("help desk", "IT HelpDesk Analyst"),
    ("helpdesk", "IT Help Desk Analyst"),
    ("help desk", "Help-Desk Technician"),
    ("service desk", "IT ServiceDesk Engineer"),
    ("healthcare", "Health Care Coordinator"),
    ("health care", "Healthcare Analyst"),
    ("onboarding", "On-Boarding Specialist"),
])
def test_compound_spelling_matches_either_way(term: str, title: str):
    assert title_wants(term, title)


@pytest.mark.parametrize(("term", "title"), [
    # The trailing \b is what stops each of these.
    ("support", "Supportive Housing Manager"),
    ("support", "Supporting Actor"),
    ("sales", "Salesforce Administrator"),
    ("help desk", "Helpful Desktop Publisher"),
    # Below the length floor, so the variant pattern never even builds.
    ("care", "Career Coach"),
    ("IT", "Digital Marketing"),
])
def test_compound_matching_does_not_invite_substring_false_positives(
        term: str, title: str):
    assert not title_wants(term, title)


def test_exclusions_are_untouched_by_this_and_stay_exact():
    """title_wants got looser; term_in_title, which drives exclude and
    seniority, must not - a loose exclusion costs a job the person never
    sees.
    """
    assert not term_in_title("sales", "Salesforce Administrator")
    assert not term_in_title("help desk", "Helpdesk Analyst")
    assert term_in_title("account executive", "Senior Account Executive")


# ---- singular vs plural -------------------------------------------------
#
# Dana's search asked for "human resources generalist" and the posting was
# titled "Human Resource Generalist". The exact role she was hunting scored
# "title matches none of search.title_include".

@pytest.mark.parametrize(("term", "title"), [
    ("human resources generalist", "Human Resource Generalist"),
    ("human resource generalist", "Human Resources Generalist"),
    ("benefits coordinator", "Benefit Coordinator"),
    ("hr specialist", "HR Specialists"),
    ("facilities technician", "Facility Technician"),
])
def test_titles_match_in_either_number(term: str, title: str):
    assert title_wants(term, title)


@pytest.mark.parametrize(("term", "title"), [
    # Number is the ONLY inflection a title varies by. Accepting -ing/-ed
    # (as coverage.present does, correctly, for prose) would let an IT
    # support search qualify an acting job.
    ("support", "Supporting Actor"),
    ("support", "Supportive Housing Manager"),
    ("sales", "Salesforce Administrator"),
])
def test_number_tolerance_does_not_extend_to_verb_forms(term: str, title: str):
    assert not title_wants(term, title)


def test_short_words_are_not_pluralised():
    """Stripping a letter off a 3-character token invents matches - "was"
    must not become "wa", nor "hr" become "h".
    """
    assert not title_wants("was", "Wa Analyst")
    assert title_wants("hr", "HR Analyst")
