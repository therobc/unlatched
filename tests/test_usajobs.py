"""USAJOBS is the one search source in this package - a national
keyword+location search over every federal agency's postings, not a
per-employer board. These tests cover: response-to-Job mapping (including
the UserArea.Details concatenation and the PositionRemuneration-into-
description fold that lets enrich.extract_salary find it), pagination,
the missing-credentials no-op path, stable keys, location/agency mapping,
malformed-response handling (same rule as every other JSON collector), and
that `unlatched collect` actually dispatches to a search source after the
company loop.

Nothing here touches the network - every fetcher is a fake with the same
(status, text, final_url) signature as unlatched.fetch.fetch.
"""
from __future__ import annotations

import json
import urllib.parse
from typing import Any

import pytest

from unlatched import cli, config, db
from unlatched import enrich as enrich_mod
from unlatched.sources import JSON_API_MAX_BYTES, usajobs

CREDS_CFG = {"credentials": {"usajobs": {"email": "me@example.com", "api_key": "KEY123"}}}


def _cfg(**search_overrides: Any) -> dict[str, Any]:
    cfg = config.defaults()
    cfg["credentials"]["usajobs"] = dict(CREDS_CFG["credentials"]["usajobs"])
    cfg["search"].update(search_overrides)
    return cfg


def _descriptor(position_id: str = "ANL-1", title: str = "Nuclear Engineer",
                 **overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "PositionID": position_id,
        "PositionTitle": title,
        "PositionURI": f"https://www.usajobs.gov/job/{position_id}",
        "PositionLocationDisplay": "Argonne, Illinois",
        "OrganizationName": "Argonne National Laboratory",
        "DepartmentName": "Department of Energy",
        "PublicationStartDate": "2026-08-01",
        "UserArea": {
            "Details": {
                "JobSummary": "Work on reactor safety systems.",
                "MajorDuties": ["Design systems.", "Review safety cases."],
                "QualificationSummary": "BS in nuclear engineering required.",
            },
        },
        "PositionRemuneration": [
            {"MinimumRange": "95000", "MaximumRange": "130000",
             "RateIntervalCode": "Per Year"},
        ],
    }
    base.update(overrides)
    return base


def _item(**overrides: Any) -> dict[str, Any]:
    return {"MatchedObjectId": overrides.get("PositionID", "ANL-1"),
            "MatchedObjectDescriptor": _descriptor(**overrides)}


def _single_page_fetcher(items: list[dict[str, Any]], total: int | None = None):
    payload = {"SearchResult": {
        "SearchResultCount": len(items),
        "SearchResultCountAll": total if total is not None else len(items),
        "SearchResultItems": items,
    }}

    def fetch(url: str, **_kw: Any) -> tuple[int, str, str]:
        return 200, json.dumps(payload), url
    return fetch


# ------------------------------------------------------------- mapping ---

def test_response_maps_to_job_fields():
    jobs = usajobs.collect(_cfg(), fetcher=_single_page_fetcher([_item()]))
    assert len(jobs) == 1
    job = jobs[0]
    assert job.source == "usajobs"
    assert job.source_id == "ANL-1"
    assert job.title == "Nuclear Engineer"
    assert job.location == "Argonne, Illinois"
    assert job.url == "https://www.usajobs.gov/job/ANL-1"
    assert job.posted == "2026-08-01"
    assert job.employer == "Argonne National Laboratory"
    assert "Work on reactor safety systems." in job.description
    assert "Design systems." in job.description
    assert "Review safety cases." in job.description
    assert "BS in nuclear engineering required." in job.description


def test_position_remuneration_folds_into_description_for_existing_salary_parser():
    jobs = usajobs.collect(_cfg(), fetcher=_single_page_fetcher([_item()]))
    salary = enrich_mod.extract_salary(jobs[0].description)
    assert salary["low"] == 95000
    assert salary["high"] == 130000


def test_stable_key_is_source_colon_position_id():
    jobs = usajobs.collect(_cfg(), fetcher=_single_page_fetcher([_item(PositionID="AB-42")]))
    assert jobs[0].key() == "usajobs:AB-42"


def test_location_falls_back_to_position_location_array_when_display_is_absent():
    item = _item(PositionLocationDisplay="", PositionLocation=[
        {"LocationName": "Argonne, Illinois"},
        {"LocationName": "Lemont, Illinois"},
    ])
    jobs = usajobs.collect(_cfg(), fetcher=_single_page_fetcher([item]))
    assert jobs[0].location == "Argonne, Illinois; Lemont, Illinois"


def test_employer_falls_back_to_department_name_then_unknown():
    with_dept_only = _item(OrganizationName="", DepartmentName="Department of Energy")
    jobs = usajobs.collect(_cfg(), fetcher=_single_page_fetcher([with_dept_only]))
    assert jobs[0].employer == "Department of Energy"

    with_neither = _item(OrganizationName="", DepartmentName="")
    jobs = usajobs.collect(_cfg(), fetcher=_single_page_fetcher([with_neither]))
    assert jobs[0].employer == "Unknown Agency"


def test_item_missing_position_id_or_title_is_dropped():
    no_id = _item(PositionID="")
    no_title = _item(PositionTitle="")
    jobs = usajobs.collect(_cfg(), fetcher=_single_page_fetcher([no_id, no_title]))
    assert jobs == []


# ----------------------------------------------------------- pagination ---

def _paged_fetcher(total_postings: int):
    def fetch(url: str, **_kw: Any) -> tuple[int, str, str]:
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        page = int(query.get("Page", ["1"])[0])
        per_page = int(query.get("ResultsPerPage", [str(usajobs.RESULTS_PER_PAGE)])[0])
        start = (page - 1) * per_page
        items = [_item(PositionID=f"POS-{i}", PositionTitle=f"Role {i}")
                 for i in range(start, min(start + per_page, total_postings))]
        payload = {"SearchResult": {
            "SearchResultCount": len(items),
            "SearchResultCountAll": total_postings,
            "SearchResultItems": items,
        }}
        return 200, json.dumps(payload), url
    return fetch


def test_pagination_collects_every_page_of_one_query():
    total = usajobs.RESULTS_PER_PAGE * 2 + 15
    jobs = usajobs.collect(_cfg(), fetcher=_paged_fetcher(total))
    assert len(jobs) == total


def test_pagination_stops_at_this_query_stream_page_ceiling():
    oversize = usajobs.RESULTS_PER_PAGE * usajobs.MAX_PAGES_PER_QUERY + 40
    jobs = usajobs.collect(_cfg(), fetcher=_paged_fetcher(oversize))
    assert len(jobs) == usajobs.RESULTS_PER_PAGE * usajobs.MAX_PAGES_PER_QUERY


def test_single_short_page_is_complete():
    jobs = usajobs.collect(_cfg(), fetcher=_paged_fetcher(3))
    assert len(jobs) == 3


def test_each_title_include_term_and_location_becomes_its_own_query():
    seen_keywords = []
    seen_locations = []

    def fetch(url: str, **_kw: Any) -> tuple[int, str, str]:
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        seen_keywords.append(query.get("Keyword", [""])[0])
        seen_locations.append(query.get("LocationName", [""])[0])
        return 200, json.dumps({"SearchResult": {
            "SearchResultCount": 0, "SearchResultCountAll": 0, "SearchResultItems": [],
        }}), url

    cfg = _cfg(title_include=["nuclear engineer", "reactor operator"],
               locations=["Argonne, IL", "Lemont, IL"])
    usajobs.collect(cfg, fetcher=fetch)
    assert sorted(set(seen_keywords)) == ["nuclear engineer", "reactor operator"]
    assert sorted(set(seen_locations)) == ["Argonne, IL", "Lemont, IL"]


# --------------------------------------------------------- credentials ---

def test_has_credentials_false_when_either_field_missing():
    cfg = config.defaults()
    assert usajobs.has_credentials(cfg) is False
    cfg["credentials"]["usajobs"]["email"] = "me@example.com"
    assert usajobs.has_credentials(cfg) is False
    cfg["credentials"]["usajobs"]["api_key"] = "KEY123"
    assert usajobs.has_credentials(cfg) is True


def test_collect_is_a_no_op_and_never_calls_the_fetcher_without_credentials():
    calls: list[str] = []

    def exploding_fetch(url: str, **_kw: Any) -> tuple[int, str, str]:
        calls.append(url)
        raise AssertionError("fetcher must not be called without credentials")

    cfg = config.defaults()
    jobs = usajobs.collect(cfg, fetcher=exploding_fetch)
    assert jobs == []
    assert calls == []


def test_search_request_carries_the_documented_auth_headers():
    seen_headers = {}

    def fetch(url: str, **kw: Any) -> tuple[int, str, str]:
        seen_headers.update(kw.get("headers") or {})
        return 200, json.dumps({"SearchResult": {
            "SearchResultCount": 0, "SearchResultCountAll": 0, "SearchResultItems": [],
        }}), url

    usajobs.collect(_cfg(), fetcher=fetch)
    assert seen_headers == {
        "Host": "data.usajobs.gov",
        "User-Agent": "me@example.com",
        "Authorization-Key": "KEY123",
    }


# ------------------------------------------------------- malformed data ---

def test_short_malformed_body_is_treated_as_no_data():
    def fetch(url: str, **_kw: Any) -> tuple[int, str, str]:
        return 200, "<html>Not Found</html>", url

    assert usajobs.collect(_cfg(), fetcher=fetch) == []


def test_truncated_body_at_the_fetch_cap_raises_like_every_other_json_collector():
    huge_invalid = '{"SearchResult": {'.ljust(JSON_API_MAX_BYTES, "x")

    def fetch(url: str, **_kw: Any) -> tuple[int, str, str]:
        return 200, huge_invalid, url

    with pytest.raises(RuntimeError, match="truncated at the fetch cap"):
        usajobs.collect(_cfg(), fetcher=fetch)


def test_unremarkable_non_200_yields_no_jobs_not_an_exception():
    def fetch(url: str, **_kw: Any) -> tuple[int, str, str]:
        return 500, "", url

    assert usajobs.collect(_cfg(), fetcher=fetch) == []


@pytest.mark.parametrize("status", [401, 403])
def test_a_rejected_key_is_loud_rather_than_reading_as_no_federal_jobs(status: int):
    """A disabled key (ToS section 7 revokes for inactivity) must not be
    indistinguishable from a search that legitimately matched nothing.
    """
    def fetch(url: str, **_kw: Any) -> tuple[int, str, str]:
        return status, "", url

    with pytest.raises(RuntimeError, match="rejected the API key"):
        usajobs.collect(_cfg(), fetcher=fetch)


def test_throttling_is_loud_and_names_the_settings_that_reduce_query_volume():
    def fetch(url: str, **_kw: Any) -> tuple[int, str, str]:
        return 429, "", url

    with pytest.raises(RuntimeError, match="throttling"):
        usajobs.collect(_cfg(), fetcher=fetch)


# --------------------------------------------------------------- CLI ---

def test_collect_runs_usajobs_after_the_company_loop_and_creates_an_agency_company(
        tmp_path, monkeypatch, capsys):
    home = tmp_path / "home"
    monkeypatch.setenv("UNLATCHED_HOME", str(home))
    cfg = _cfg(title_include=["nuclear engineer"], locations=["Argonne, IL"])
    config.save(cfg, home)

    def fetch(url: str, **_kw: Any) -> tuple[int, str, str]:
        return 200, json.dumps({"SearchResult": {
            "SearchResultCount": 1, "SearchResultCountAll": 1,
            "SearchResultItems": [_item()],
        }}), url

    monkeypatch.setattr(cli.fetch_mod, "fetch", fetch)
    rc = cli.main(["--home", str(home), "collect", "--json"])
    assert rc == 0

    payload = json.loads(capsys.readouterr().out)
    usajobs_entries = [e for e in payload if e.get("source") == "usajobs"]
    assert len(usajobs_entries) == 1
    assert usajobs_entries[0]["collected"] == 1
    assert usajobs_entries[0]["qualified"] == 1

    con = db.connect(home)
    row = db.get_job(con, "usajobs:ANL-1")
    assert row is not None
    assert row["title"] == "Nuclear Engineer"
    company = con.execute("SELECT * FROM companies WHERE id = ?",
                           (row["company_id"],)).fetchone()
    con.close()
    assert company["name"] == "Argonne National Laboratory"


def test_collect_with_missing_usajobs_credentials_prints_hint_and_does_not_fail(
        tmp_path, monkeypatch, capsys):
    home = tmp_path / "home"
    monkeypatch.setenv("UNLATCHED_HOME", str(home))
    # No config.json at all -> defaults, which enable usajobs but carry no
    # credentials. This is the state a fresh install is in.
    con = db.connect(home)
    con.close()

    def exploding_fetch(url: str, **_kw: Any) -> tuple[int, str, str]:
        raise AssertionError("must not fetch without credentials")

    monkeypatch.setattr(cli.fetch_mod, "fetch", exploding_fetch)
    rc = cli.main(["--home", str(home), "collect"])
    assert rc == 0

    out = capsys.readouterr().out
    assert usajobs.CREDENTIALS_HINT in out

    con = db.connect(home)
    count = con.execute(
        "SELECT COUNT(*) FROM jobs WHERE key LIKE 'usajobs:%'").fetchone()[0]
    con.close()
    assert count == 0


def test_collect_narrowed_to_one_company_does_not_also_run_usajobs(
        tmp_path, monkeypatch, capsys):
    home = tmp_path / "home"
    monkeypatch.setenv("UNLATCHED_HOME", str(home))
    cfg = _cfg()
    config.save(cfg, home)
    con = db.connect(home)
    db.upsert_company(con, "Acme Co", ats="greenhouse", ats_ref="acme")
    con.close()

    def fetch(url: str, **_kw: Any) -> tuple[int, str, str]:
        if "greenhouse" in url:
            return 200, json.dumps({"jobs": []}), url
        raise AssertionError(f"unexpected fetch for a narrowed run: {url}")

    monkeypatch.setattr(cli.fetch_mod, "fetch", fetch)
    rc = cli.main(["--home", str(home), "collect", "--company", "Acme Co", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert all(e.get("source") != "usajobs" for e in payload)


def test_a_truncated_query_stream_says_so():
    """The existing ceiling test asserts the stream STOPS. This asserts it
    admits it, which is the half that was missing.

    Every board collector reports its ceiling by the CLI comparing what came
    back against MAX_COLLECTED. That cannot work here: collect() de-duplicates
    across query streams into a dict, so twelve streams of 500 return far
    fewer than 6,000 unique postings and the comparison never fires. Measured
    before the fix - 900 advertised, 500 returned, 400 invisible, and
    `collected >= MAX_COLLECTED` False.
    """
    oversize = usajobs.RESULTS_PER_PAGE * usajobs.MAX_PAGES_PER_QUERY + 400
    jobs = usajobs.collect(_cfg(), fetcher=_paged_fetcher(oversize))

    cut = usajobs.truncated_queries()
    assert cut, (
        f"read {len(jobs)} of {oversize} and reported nothing - this is the "
        f"silent truncation the ceiling note cannot catch")
    assert str(oversize) in cut[0], (
        f"the report should name what was available: {cut[0]!r}")
    # And it is genuinely below the cap the CLI compares against, so nothing
    # else would have caught it.
    assert len(jobs) < usajobs.MAX_COLLECTED


def test_a_complete_run_reports_no_truncation():
    """The positive control. A collector that always claimed truncation would
    satisfy the test above and make the note worthless."""
    usajobs.collect(_cfg(), fetcher=_paged_fetcher(usajobs.RESULTS_PER_PAGE + 5))
    assert usajobs.truncated_queries() == []


def test_the_report_is_cleared_between_runs():
    """Read AFTER collect returns, so a stale line from a previous run would
    report a truncation the person has already fixed by narrowing the search."""
    oversize = usajobs.RESULTS_PER_PAGE * usajobs.MAX_PAGES_PER_QUERY + 400
    usajobs.collect(_cfg(), fetcher=_paged_fetcher(oversize))
    assert usajobs.truncated_queries()

    usajobs.collect(_cfg(), fetcher=_paged_fetcher(10))
    assert usajobs.truncated_queries() == [], (
        "the previous run's truncation survived into a clean one")
