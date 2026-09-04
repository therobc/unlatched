"""Oracle HCM (Fusion Cloud Recruiting) collector and its discover.py
fingerprint.

Unlike Workday's CXS API, TotalJobsCount here is reported accurately on
every page, not just the first - so the paging tests below check the
opposite failure mode from test_workday_paging.py: that a real, honest
total is trusted to stop the walk rather than always paging to MAX_PAGES.
"""
from __future__ import annotations

import json
import re

import pytest

from unlatched import discover
from unlatched.sources import oracle_hcm

ATS_REF = "acme.fa.us2.oraclecloud.com|CX_1"


def _board(total, page_size=oracle_hcm.PAGE_SIZE, only_site=None):
    """A fetcher mimicking the real list/detail endpoints. `only_site`, when
    given, answers real postings for exactly that siteNumber and an empty
    board for every other one - the shape a fallback-site test needs.
    """
    def fetch(url, **kw):
        if "recruitingCEJobRequisitionDetails" in url:
            job_id = re.search(r"Id=([^,&]+)", url).group(1)
            return 200, json.dumps({"items": [{
                "Id": job_id,
                "ExternalDescriptionStr": "<p>Do the work.</p>",
                "ExternalResponsibilitiesStr": "",
                "ExternalQualificationsStr": "",
                "JobSchedule": "Full time",
                "ExternalPostedStartDate": "2026-08-01T12:00:00+00:00",
            }]}), url

        site = re.search(r"siteNumber=([^,&]+)", url).group(1)
        offset = int(re.search(r"offset=(\d+)", url).group(1))
        if only_site is not None and site != only_site:
            return 200, json.dumps({"items": [{"TotalJobsCount": 0, "requisitionList": []}]}), url
        chunk = [{"Id": str(offset + i), "Title": f"Role {offset + i}",
                  "PrimaryLocation": "Remote", "PostedDate": "2026-08-01"}
                 for i in range(min(page_size, max(0, total - offset)))]
        return 200, json.dumps({"items": [{"TotalJobsCount": total,
                                            "requisitionList": chunk}]}), url

    return fetch


def test_pagination_walks_multiple_pages():
    total = oracle_hcm.PAGE_SIZE * 3 + 7
    jobs = oracle_hcm.collect(ATS_REF, fetcher=_board(total), with_detail=False)
    assert len(jobs) == total
    assert {j.source_id for j in jobs} == {str(i) for i in range(total)}


def test_total_jobs_count_stops_the_walk_without_hitting_the_page_ceiling():
    """A genuinely small board must not page all the way to MAX_PAGES just
    because nothing told it to stop earlier.
    """
    total = oracle_hcm.PAGE_SIZE + 3
    calls = []

    def counting(url, **kw):
        calls.append(url)
        return _board(total)(url, **kw)

    jobs = oracle_hcm.collect(ATS_REF, fetcher=counting, with_detail=False)
    assert len(jobs) == total
    assert len(calls) == 2  # one full page, one partial - not MAX_PAGES worth


def test_board_smaller_than_one_page():
    jobs = oracle_hcm.collect(ATS_REF, fetcher=_board(5), with_detail=False)
    assert len(jobs) == 5


def test_empty_board_returns_nothing():
    assert oracle_hcm.collect(ATS_REF, fetcher=_board(0), with_detail=False) == []


def test_a_board_just_past_the_new_window_is_finished_by_the_backfill():
    """540 postings against a 500-posting new window, and nothing left behind.

    This used to return exactly 500 and drop the last 40 on every run. The
    backfill window starts where the new window ended, so a board only a
    little too big is now complete in one go.
    """
    oversize = oracle_hcm.PAGE_SIZE * oracle_hcm.MAX_PAGES + 40
    jobs = oracle_hcm.collect(ATS_REF, fetcher=_board(oversize), with_detail=False)
    assert len(jobs) == oversize, "the backfill should have reached the tail"


def test_max_collected_is_respected():
    oversize = oracle_hcm.MAX_COLLECTED + 100
    jobs = oracle_hcm.collect(ATS_REF, fetcher=_board(oversize), with_detail=False)
    assert len(jobs) == oracle_hcm.MAX_COLLECTED
    assert len(jobs) >= oracle_hcm.MAX_COLLECTED  # what the CLI checks for truncation


def test_keys_are_stable_and_carry_the_source_name():
    jobs = oracle_hcm.collect(ATS_REF, fetcher=_board(3), with_detail=False)
    assert sorted(j.key() for j in jobs) == [
        "oracle_hcm:0", "oracle_hcm:1", "oracle_hcm:2"]


def test_detail_call_fills_description_employment_type_and_posted_date():
    jobs = oracle_hcm.collect(ATS_REF, fetcher=_board(1), with_detail=True)
    assert len(jobs) == 1
    job = jobs[0]
    assert job.description == "Do the work."
    assert job.employment_type == "Full time"
    assert job.posted == "2026-08-01"  # date-only, trimmed from the ISO timestamp


def test_job_url_points_at_the_candidate_experience_site():
    jobs = oracle_hcm.collect(ATS_REF, fetcher=_board(1), with_detail=False)
    assert jobs[0].url == (
        "https://acme.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/job/0")


def test_falls_back_to_a_conventional_site_when_the_captured_one_is_empty():
    """The site discover.py captured can be stale or wrong; a real board
    living under a fallback identifier must still be found.
    """
    ref = "acme.fa.us2.oraclecloud.com|CX_9999"
    jobs = oracle_hcm.collect(ref, fetcher=_board(4, only_site="CX_1"), with_detail=False)
    assert len(jobs) == 4
    assert jobs[0].url.split("/sites/")[1].split("/job/")[0] == "CX_1"


def test_no_working_site_found_returns_empty_not_an_error():
    jobs = oracle_hcm.collect("acme.fa.us2.oraclecloud.com|NOTHING_HERE",
                               fetcher=_board(4, only_site="SOME_OTHER_SITE"),
                               with_detail=False)
    assert jobs == []


def test_malformed_ats_ref_returns_nothing():
    # A bare host is NOT malformed: discovery frequently finds only the
    # Oracle host, with no candidate-experience path to name the site, and
    # the collector resolves a working site from the host alone.
    for bad in ("", "|missing-host", "   "):
        assert oracle_hcm.collect(bad, fetcher=_board(5), with_detail=False) == []


def test_bare_host_without_a_site_still_collects():
    jobs = oracle_hcm.collect("tenant.fa.us2.oraclecloud.com",
                               fetcher=_board(5), with_detail=False)
    assert len(jobs) == 5


def test_truncated_board_response_raises_instead_of_collecting_zero():
    from unlatched.sources import JSON_API_MAX_BYTES

    huge_invalid = '{"items": ['.ljust(JSON_API_MAX_BYTES, "x")

    def fake_fetch(url, **kw):
        return 200, huge_invalid, url

    with pytest.raises(RuntimeError, match="truncated at the fetch cap"):
        oracle_hcm.collect(ATS_REF, fetcher=fake_fetch, with_detail=False)


def test_short_malformed_body_is_just_no_data():
    def fake_fetch(url, **kw):
        return 200, "<html>not json</html>", url

    assert oracle_hcm.collect(ATS_REF, fetcher=fake_fetch, with_detail=False) == []


# ------------------------------------------------------- discover.py -----

def test_discover_fingerprint_extracts_host_and_site():
    html = """
    <html><body>
      <h1>Acme careers</h1>
      <p>Search open roles at
        <a href="https://tenant.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/requisitions">
          our jobs board</a>.
      </p>
    </body></html>
    """
    found = discover.detect_ats(html)
    assert {"provider": "oracle_hcm", "parts": ["tenant.fa.us2.oraclecloud.com", "CX_1"]} in found


def test_discover_fingerprint_handles_a_non_numeric_site_slug():
    html = ('<a href="https://acmeco.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/'
            'en/sites/GroupCareers/requisitions">Careers</a>')
    found = discover.detect_ats(html)
    assert {"provider": "oracle_hcm",
            "parts": ["acmeco.fa.us2.oraclecloud.com", "GroupCareers"]} in found


def test_bare_oracle_host_is_matched_on_purpose():
    """An Oracle host with no recruiting path still counts as a lead.

    Two of three real employers checked referenced their Oracle host
    without ever linking the candidate-experience path, so requiring that
    path made the collector unreachable for them. The cost of the looser
    rule is bounded: a tenant running some other Oracle product resolves no
    site, returns no postings, and is dropped after a couple of requests.
    Missing a live 15,000-posting board is the worse failure.
    """
    html = '<a href="https://example.fa.us2.oraclecloud.com/some/other/product">x</a>'
    found = discover.detect_ats(html)
    assert any(f["provider"] == "oracle_hcm" for f in found)


def test_the_precise_fingerprint_is_preferred_when_both_could_match():
    # Discovery reports matches in pattern order, and the caller takes the
    # first, so a page that DOES name its site must yield the site rather
    # than the bare host.
    html = ('<a href="https://example.fa.us2.oraclecloud.com/hcmUI/'
             'CandidateExperience/en/sites/GroupCareers/requisitions">jobs</a>')
    found = [f for f in discover.detect_ats(html) if f["provider"] == "oracle_hcm"]
    assert found[0]["parts"] == ["example.fa.us2.oraclecloud.com", "GroupCareers"]


def test_a_non_oracle_host_is_not_matched():
    html = '<a href="https://careers.example.com/openings">x</a>'
    assert not any(f["provider"] == "oracle_hcm"
                    for f in discover.detect_ats(html))
