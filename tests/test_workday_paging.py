"""Workday paging must not trust the per-page total.

The list endpoint reports a real count on the FIRST page only and answers
zero on every page after it. Treating each page's value as authority made
page two look like the end of the board, so a carrier with 246 openings
collected 40 and nothing said otherwise.
"""
from __future__ import annotations

import json

from unlatched.sources import workday

ATS_REF = "acme|wd1|acme_careers"


def _board(total_postings, first_page_total, page_size=workday.PAGE_SIZE):
    """A fetcher mimicking the real API: a truthful count on the first page,
    zero on the rest, and detail calls that add nothing.
    """
    def fetch(url, **kw):
        if kw.get("data") is None:
            # Detail call for one posting.
            return 200, json.dumps({"jobPostingInfo": {"canApply": True}}), url
        offset = json.loads(kw["data"].decode()).get("offset", 0)
        page = [{"externalPath": f"/job/{offset + i}", "title": f"Role {offset + i}"}
                for i in range(min(page_size, max(0, total_postings - offset)))]
        return 200, json.dumps({
            "total": first_page_total if offset == 0 else 0,
            "jobPostings": page,
        }), url

    return fetch


def test_paging_continues_past_a_zero_total_on_later_pages():
    jobs = workday.collect(ATS_REF, fetcher=_board(55, 55), with_detail=False)
    assert len(jobs) == 55


def test_single_short_page_is_complete():
    jobs = workday.collect(ATS_REF, fetcher=_board(7, 7), with_detail=False)
    assert len(jobs) == 7


def test_board_larger_than_the_page_budget_returns_what_it_got():
    """A board past the ceiling still hands back everything collected -
    throwing the pages away to signal truncation would lose 200 real jobs
    to report a fact. The CLI notices `len(jobs) >= MAX_COLLECTED` and says
    so in the summary instead.
    """
    oversize = workday.PAGE_SIZE * workday.MAX_PAGES + 25
    jobs = workday.collect(ATS_REF, fetcher=_board(oversize, oversize),
                            with_detail=False)
    assert len(jobs) == workday.MAX_COLLECTED
    assert len(jobs) >= workday.MAX_COLLECTED  # what the CLI checks


def test_board_exactly_at_the_page_budget_is_not_an_error():
    exact = workday.PAGE_SIZE * workday.MAX_PAGES
    jobs = workday.collect(ATS_REF, fetcher=_board(exact, exact), with_detail=False)
    assert len(jobs) == exact
