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


def test_a_board_just_past_the_new_window_is_finished_by_the_backfill():
    """225 postings, a 200-posting new window, and nothing left behind.

    This used to return exactly 200 and drop the last 25 on every run. The
    backfill window starts where the new window ended, so a board only a
    little too big is now complete in one go.
    """
    oversize = workday.PAGE_SIZE * workday.MAX_PAGES + 25
    jobs = workday.collect(ATS_REF, fetcher=_board(oversize, oversize),
                            with_detail=False)
    assert len(jobs) == oversize, "the backfill should have reached the tail"


def test_board_larger_than_the_whole_budget_returns_what_it_got():
    """A board past BOTH windows still hands back everything collected -
    throwing the pages away to signal truncation would lose real jobs to
    report a fact. The CLI notices `len(jobs) >= MAX_COLLECTED` and says so in
    the summary instead.
    """
    oversize = workday.MAX_COLLECTED + 100
    jobs = workday.collect(ATS_REF, fetcher=_board(oversize, oversize),
                            with_detail=False)
    assert len(jobs) == workday.MAX_COLLECTED
    assert len(jobs) >= workday.MAX_COLLECTED  # what the CLI checks


def test_board_exactly_at_the_page_budget_is_not_an_error():
    exact = workday.PAGE_SIZE * workday.MAX_PAGES
    jobs = workday.collect(ATS_REF, fetcher=_board(exact, exact), with_detail=False)
    assert len(jobs) == exact


# ---- the detail request is what the ceiling was really bounded by ----------
#
# Measured across the fifty starter employers, one first page each: 41 of 48
# boards held more than their collector's ceiling and 100,202 postings were
# never read on any run. CVS Health kept 200 of 19,277.
#
# The number was not arbitrary. This collector fetched a DETAIL request for
# every posting, so 200 postings cost 200 requests, and raising the ceiling
# would have raised the request count with it. oracle_hcm had already solved
# the same problem by asking the person's own title filter first, so that is
# what this now does - and the ceiling could go to 1,000 for fewer requests
# than 200 used to cost.

def _counting_board(total_postings, titles):
    """A board of `titles`, recording which paths got a DETAIL request."""
    detail_paths: list[str] = []

    def fetch(url, **kw):
        if kw.get("data") is None:
            detail_paths.append(url.rsplit("/job/", 1)[-1])
            return 200, json.dumps({"jobPostingInfo": {"canApply": True}}), url
        offset = json.loads(kw["data"].decode()).get("offset", 0)
        page = [{"externalPath": f"/job/{offset + i}",
                 "title": titles[(offset + i) % len(titles)]}
                for i in range(min(workday.PAGE_SIZE,
                                   max(0, total_postings - offset)))]
        return 200, json.dumps({
            "total": total_postings if offset == 0 else 0,
            "jobPostings": page,
        }), url

    return fetch, detail_paths


def test_no_detail_request_is_made_for_a_title_the_filter_rejects():
    """The load-bearing assertion is the count of DETAIL requests.

    A test that only counted returned jobs would pass against a version that
    still fetched every description and threw the answers away - which is
    exactly the cost that held the ceiling at 200.
    """
    fetcher, details = _counting_board(
        4, ["Support Analyst", "Petroleum Geologist",
            "Support Analyst", "Line Cook"])

    jobs = workday.collect(ATS_REF, fetcher=fetcher,
                           title_include=["Support Analyst"])

    assert len(details) == 2, f"fetched detail for {len(details)}, wanted 2"
    assert len(jobs) == 4, "a posting that fails the filter is still returned"


def test_with_no_filter_every_posting_still_gets_its_description():
    """The old behaviour, unchanged for a profile that has not set a filter
    and for every caller that passes nothing."""
    fetcher, details = _counting_board(3, ["Anything At All"])
    jobs = workday.collect(ATS_REF, fetcher=fetcher)
    assert len(details) == 3
    assert len(jobs) == 3


def test_the_collector_declares_that_it_wants_the_filter():
    # cli.py only passes title_include to collectors that opt in, because they
    # do not share a signature.
    assert workday.WANTS_TITLE_INCLUDE is True


def test_a_run_reads_more_than_the_newest_window():
    """A regression guard on the shape of the fix, not on one number.

    At 200 per run and no backfill, this collector saw 1.0% of CVS Health's
    board and 4.1% of Kroger's - on every run, for ever, because paging always
    restarted at offset 0. Removing the backfill window would restore that
    silently: the run still succeeds, it just stops seeing most of the
    employer.
    """
    assert workday.BACKFILL_PAGES > 0, "the backlog would never be walked"
    assert workday.MAX_COLLECTED > workday.PAGE_SIZE * workday.MAX_PAGES, (
        "a run must return more than the newest window")
    assert workday.WANTS_BACKFILL is True, (
        "cli.py only remembers an offset for collectors that opt in")


def test_detail_requests_are_capped_even_with_no_title_filter():
    """The cost bound the pre-filter does NOT provide.

    title_may_pass returns True when there is no filter, so without this cap a
    profile that has not set search.title_include - which is what a new one
    looks like - would make one detail request per posting and pay five times
    the old cost for the deeper paging. Caught by checking the change rather
    than by anything failing.
    """
    total = workday.MAX_DETAIL + 150
    fetcher, details = _counting_board(total, ["Anything At All"])

    jobs = workday.collect(ATS_REF, fetcher=fetcher)

    assert len(details) == workday.MAX_DETAIL, (
        f"made {len(details)} detail requests with no filter set; "
        f"the cap is {workday.MAX_DETAIL}")
    assert len(jobs) == total, (
        "postings past the detail cap must still be returned from the list "
        "page - a visible partial, not an invisible absence")


def test_a_posting_past_the_detail_cap_keeps_its_list_page_fields():
    """It arrives without a description, which is the same shape a posting
    gets when it fails the title filter."""
    total = workday.MAX_DETAIL + 5
    fetcher, _ = _counting_board(total, ["Anything At All"])

    jobs = workday.collect(ATS_REF, fetcher=fetcher)

    last = jobs[-1]
    assert last.title, "a capped posting still has its title"
    assert last.url, "a capped posting still has its link"
    assert not last.description, "and no description, because none was fetched"


def test_successive_runs_walk_the_whole_backlog():
    """THE POINT OF THE BACKFILL, and the thing no single run can show.

    A board of 1,000 against a 600-posting run budget. One run can never see
    all of it. What matters is that the runs after it see the REST rather than
    the same 600 again, which is what the old collector did on every run for
    ever - the reason CVS Health sat at 200 of 19,277.

    The offset handed in only ever increases; the collector takes it modulo
    the board's real size, so the caller never has to know how big it is.
    """
    total = 1000
    seen: set[str] = set()
    offset = 0
    for _ in range(6):
        jobs = workday.collect(ATS_REF, fetcher=_board(total, total),
                               with_detail=False, backfill_from=offset)
        seen.update(j.source_id for j in jobs)
        offset += workday.BACKFILL_STRIDE

    assert len(seen) == total, (
        f"after six runs {len(seen)} of {total} postings had been seen; the "
        "backlog is not being walked")


def test_one_run_alone_does_not_see_the_whole_board():
    """A positive control for the test above.

    If the fixture board were smaller than one run's budget, the convergence
    test would pass without any backfill happening at all.
    """
    total = 1000
    jobs = workday.collect(ATS_REF, fetcher=_board(total, total),
                           with_detail=False)
    assert len(jobs) < total, "the fixture board is too small to prove anything"
    assert len(jobs) == workday.MAX_COLLECTED


def test_the_backfill_never_re_reads_the_newest_window():
    """The two windows must not overlap, or the backfill spends its budget
    re-reading what the new window just fetched and never advances."""
    total = 1000
    fetched: list[int] = []

    def recording(url, **kw):
        if kw.get("data") is not None:
            fetched.append(json.loads(kw["data"].decode()).get("offset", 0))
        return _board(total, total)(url, **kw)

    workday.collect(ATS_REF, fetcher=recording, with_detail=False,
                    backfill_from=0)

    new_window = workday.PAGE_SIZE * workday.MAX_PAGES
    backfill = [o for o in fetched if o >= new_window]
    assert backfill, "no page past the newest window was ever requested"
    assert min(backfill) == new_window, (
        f"the backfill started at {min(backfill)}, not where the new window "
        f"ended ({new_window})")


# ---- the offset has to survive between runs, or none of the above happens --

def test_the_backlog_offset_is_remembered_and_advances(home, monkeypatch):
    """END TO END THROUGH cmd_collect, because the wiring is where this would
    silently stop working.

    The collector would keep its parameter and simply be handed 0 every run -
    which is the defect the backfill exists to fix, reproduced one layer up
    and just as invisible.
    """
    from unlatched import cli, db

    con = db.connect(home)
    db.upsert_company(con, "Acme", ats="workday", ats_ref=ATS_REF,
                      probe_status="probed")
    company_id = db.get_company(con, "Acme")["id"]
    con.close()

    monkeypatch.setattr(cli.fetch_mod, "fetch", _board(1000, 1000))

    key = f"backfill:workday:{company_id}"

    assert cli.main(["--home", str(home), "collect"]) == 0
    con = db.connect(home)
    after_one = int(db.get_meta(con, key) or 0)
    con.close()
    assert after_one == workday.BACKFILL_STRIDE, (
        f"offset after one run was {after_one}, expected one stride")

    assert cli.main(["--home", str(home), "collect"]) == 0
    con = db.connect(home)
    after_two = int(db.get_meta(con, key) or 0)
    con.close()
    assert after_two == workday.BACKFILL_STRIDE * 2, (
        "the offset stopped advancing, so the walk would never move on")


def test_a_failed_collect_does_not_advance_the_offset(home, monkeypatch):
    """Moving past a slice nobody looked at is how a gap becomes permanent."""
    from unlatched import cli, db

    con = db.connect(home)
    db.upsert_company(con, "Acme", ats="workday", ats_ref=ATS_REF,
                      probe_status="probed")
    company_id = db.get_company(con, "Acme")["id"]
    con.close()

    def explode(*_a, **_kw):
        raise RuntimeError("board unreachable")

    monkeypatch.setattr(cli.fetch_mod, "fetch", explode)
    assert cli.main(["--home", str(home), "collect"]) == 0

    con = db.connect(home)
    stored = db.get_meta(con, f"backfill:workday:{company_id}")
    con.close()
    assert not stored, "the offset advanced past a slice that was never read"
