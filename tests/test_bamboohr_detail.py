"""BambooHR needs two calls per board, and the second one is where all the
data is.

`/careers/list` returns a title, a location and an id. Description, date
posted, pay and the shareable URL only exist behind `/careers/{id}/detail`.
Collecting the list alone produced the thinnest rows in the database - no
description meant no requirements screening, no keyword mining, and nothing
for a person to read.

The field names below were read off a live BambooHR board rather than
guessed at: `datePosted`, `compensation`, `employmentStatusLabel`,
`jobOpeningShareUrl`, and an `atsLocation` that carries the city and state
that `location` leaves null. The board itself is not named here - the shapes
are what this file is about, and quoting a real employer's posting into a
fixture tells a reader nothing the shapes do not.
"""
from __future__ import annotations

import json
from typing import Any

from unlatched.sources import bamboohr

LIST_PAYLOAD = {"result": [
    {"id": 645, "jobOpeningName": "Sales Executive", "isRemote": False,
     "location": {"city": None, "state": None},
     "atsLocation": {"city": "Dayton", "state": "Ohio"}},
]}

DETAIL_PAYLOAD = {"result": {"jobOpening": {
    "jobOpeningName": "Sales Executive",
    "datePosted": "2025-10-10",
    "description": "<p>Support the <b>platform</b> team.</p><li>Travel 25%</li>",
    "compensation": "Base salary range $95k-$130k",
    "employmentStatusLabel": "Fulltime-Regular",
    "jobOpeningShareUrl": "https://northwind.bamboohr.com/careers/645",
}}}


def _fetcher(list_payload: Any = None, detail_payload: Any = None,
             detail_status: int = 200):
    def fetch(url: str, **_kw: Any) -> tuple[int, str, str]:
        if url.endswith("/detail"):
            if detail_status != 200:
                return detail_status, "", url
            return 200, json.dumps(
                detail_payload if detail_payload is not None else DETAIL_PAYLOAD), url
        return 200, json.dumps(
            list_payload if list_payload is not None else LIST_PAYLOAD), url
    return fetch


def test_detail_call_supplies_date_description_and_pay():
    jobs = bamboohr.collect("northwind", fetcher=_fetcher())
    assert len(jobs) == 1
    job = jobs[0]
    assert job.posted == "2025-10-10"
    assert "Support the platform team." in job.description
    assert job.employment_type == "Fulltime-Regular"
    assert job.url == "https://northwind.bamboohr.com/careers/645"


def test_compensation_is_folded_into_the_description_for_the_salary_parser():
    """Pay is a separate field here, exactly like USAJOBS - folding it into
    the text is what lets the shared extractor find it with no per-source
    special case.
    """
    from unlatched import enrich

    jobs = bamboohr.collect("northwind", fetcher=_fetcher())
    assert "Base salary range" in jobs[0].description
    assert enrich.extract_salary(jobs[0].description)["display"]


def test_ats_location_wins_when_location_is_all_nulls():
    jobs = bamboohr.collect("northwind", fetcher=_fetcher())
    assert jobs[0].location == "Dayton, Ohio"


def test_remote_flag_is_reflected_even_with_no_city():
    payload = {"result": [{"id": 1, "jobOpeningName": "Engineer", "isRemote": True,
                            "location": {}, "atsLocation": {}}]}
    jobs = bamboohr.collect("x", fetcher=_fetcher(list_payload=payload))
    assert jobs[0].location == "Remote"


def test_a_failed_detail_call_still_yields_the_posting():
    """The job is real; a flaky second request should cost its description,
    not the row itself.
    """
    jobs = bamboohr.collect("x", fetcher=_fetcher(detail_status=500))
    assert len(jobs) == 1
    assert jobs[0].title == "Sales Executive"
    assert jobs[0].posted == ""
    # Falls back to the constructed URL rather than an empty link.
    assert jobs[0].url.endswith("/careers/645")


def test_rows_without_an_id_or_title_are_dropped_before_any_detail_fetch():
    calls: list[str] = []

    def counting(url: str, **_kw: Any) -> tuple[int, str, str]:
        calls.append(url)
        if url.endswith("/detail"):
            return 200, json.dumps(DETAIL_PAYLOAD), url
        return 200, json.dumps({"result": [
            {"id": "", "jobOpeningName": "No Id"},
            {"id": 7, "jobOpeningName": ""},
        ]}), url

    assert bamboohr.collect("x", fetcher=counting) == []
    assert not [c for c in calls if c.endswith("/detail")]


def test_detail_fetches_are_bounded():
    big = {"result": [{"id": i, "jobOpeningName": f"Role {i}", "location": {},
                        "atsLocation": {"city": "Dayton", "state": "OH"}}
                       for i in range(bamboohr.MAX_DETAIL_FETCHES + 25)]}
    jobs = bamboohr.collect("x", fetcher=_fetcher(list_payload=big))
    assert len(jobs) == bamboohr.MAX_DETAIL_FETCHES
