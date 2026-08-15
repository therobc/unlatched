"""Remote OK: a search source that needs no employer list and no key.

An earlier change. Measured 2026-08-06: one request returns the latest ~100
postings with full descriptions. The record shapes below are copied from
that live response.
"""
from __future__ import annotations

import json

from conftest import make_fetcher

from unlatched.sources import remoteok

LEGAL = {
    "last_updated": 1786061793,
    "legal": "API Terms of Service: Please link back ...",
}

POSTING = {
    "id": "1136231",
    "slug": "remote-barista-cafe-march-21-1136231",
    "position": "Support Analyst",
    "company": "Cafe March 21",
    "location": "US only",
    "date": "2026-08-06T01:12:05+00:00",
    "salary_min": 0,
    "salary_max": 0,
    "tags": ["education", "non tech"],
    "description": "<p>Answer tickets and support end users.</p>",
    "url": "https://remoteOK.com/remote-jobs/remote-support-analyst-1136231",
    "apply_url": "https://remoteOK.com/remote-jobs/remote-support-analyst-1136231",
}


def feed(*records) -> dict:
    return {remoteok.API_URL: json.dumps(list(records))}


def test_the_legal_record_is_not_collected_as_a_job():
    """The feed's first element is a terms notice, not a posting. Skipped by
    SHAPE rather than by position, so a feed that moves it does not cost a
    posting or invent one."""
    jobs = remoteok.collect({}, fetcher=make_fetcher(feed(LEGAL, POSTING)))
    assert len(jobs) == 1
    assert jobs[0].title == "Support Analyst"


def test_a_posting_maps_onto_the_shared_job_shape():
    job = remoteok.collect({}, fetcher=make_fetcher(feed(LEGAL, POSTING)))[0]
    assert job.source == "remoteok"
    assert job.source_id == "1136231"
    assert job.employer == "Cafe March 21"
    assert job.posted.startswith("2026-08-06")
    assert "Answer tickets" in job.description


def test_the_url_is_the_remote_ok_page_because_linking_back_is_a_condition():
    """Their terms require the link back. Rewriting url to the employer's own
    apply page would break that silently."""
    job = remoteok.collect({}, fetcher=make_fetcher(feed(LEGAL, POSTING)))[0]
    assert job.url == POSTING["url"]


def test_the_location_states_remote_and_keeps_any_restriction():
    """Every posting on this board is remote, but "US only" is exactly the
    kind of restriction that decides whether somebody can take the job."""
    job = remoteok.collect({}, fetcher=make_fetcher(feed(LEGAL, POSTING)))[0]
    assert job.location == "Remote - US only"

    unrestricted = dict(POSTING, location="")
    job = remoteok.collect({}, fetcher=make_fetcher(feed(LEGAL, unrestricted)))[0]
    assert job.location == "Remote"


def test_the_description_is_html_turned_into_text():
    job = remoteok.collect({}, fetcher=make_fetcher(feed(LEGAL, POSTING)))[0]
    assert "<p>" not in job.description


def test_tags_are_kept_because_they_are_skills_evidence():
    """coverage.py reads the description for skills; the tags are the board's
    own answer to the same question, so they belong in it."""
    job = remoteok.collect({}, fetcher=make_fetcher(feed(LEGAL, POSTING)))[0]
    assert "education" in job.description


def test_a_record_with_no_id_or_no_title_is_dropped():
    assert remoteok.collect({}, fetcher=make_fetcher(feed(dict(POSTING, id="", slug="")))) == []
    assert remoteok.collect({}, fetcher=make_fetcher(feed(dict(POSTING, position="")))) == []


def test_a_failed_or_unexpected_response_is_empty_not_an_exception():
    """A source that raises kills a whole collect run for every other
    source in it."""
    assert remoteok.collect({}, fetcher=make_fetcher({remoteok.API_URL: (500, "")})) == []
    assert remoteok.collect({}, fetcher=make_fetcher({remoteok.API_URL: "not json"})) == []
    assert remoteok.collect({}, fetcher=make_fetcher({remoteok.API_URL: '{"a": 1}'})) == []
    assert remoteok.collect({}, fetcher=make_fetcher({})) == []


def test_it_needs_no_credentials():
    assert remoteok.has_credentials({}) is True


def test_it_is_registered_as_a_search_source():
    from unlatched import sources
    reg = sources.registry()
    assert "remoteok" in sources.search_sources(reg)


def test_the_company_name_is_html_unescaped():
    """The feed carries names encoded: "Crown &amp; Pearl" was being stored,
    and shown in the Companies table, exactly like that. The description gets
    unescaped by html_to_text; the company name had nothing doing it."""
    record = dict(POSTING, company="Crown &amp; Pearl")
    job = remoteok.collect({}, fetcher=make_fetcher(feed(LEGAL, record)))[0]
    assert job.employer == "Crown & Pearl"


def test_employers_are_not_labelled_federal_agencies():
    """That status was hardcoded when USAJOBS was the only search source. It
    put "federal-agency" against a cafe, in a column the Companies page
    shows."""
    assert remoteok.EMPLOYER_STATUS == "via Remote OK"
    from unlatched.sources import nodesk, usajobs
    assert nodesk.EMPLOYER_STATUS == "via NoDesk"
    assert usajobs.EMPLOYER_STATUS == "federal-agency"
