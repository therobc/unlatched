"""A board API response is one JSON document for the whole board, so big
employers legitimately exceed the page-sized default fetch cap. Found live:
a 219-posting board truncated at the default cap parsed as invalid JSON and
collected ZERO, silently. Two guarantees pin the fix:

  1. every JSON collector asks the fetcher for the board-sized cap, and
  2. a body that fails to parse AT the cap raises (so collect reports an
     error for that company) instead of returning an empty board.
"""
from __future__ import annotations

import pytest

from unlatched.sources import (
    JSON_API_MAX_BYTES,
    ashby,
    bamboohr,
    breezy,
    decode_board_json,
    greenhouse,
    lever,
    oracle_hcm,
    recruitee,
    smartrecruiters,
    workable,
    workday,
)

JSON_COLLECTORS = [
    (greenhouse, "acme"),
    (lever, "acme"),
    (ashby, "acme"),
    (smartrecruiters, "acme"),
    (workable, "acme"),
    (recruitee, "acme"),
    (bamboohr, "acme"),
    (breezy, "acme"),
    (workday, "acme|wd1|acme_careers"),
    (oracle_hcm, "acme.fa.us2.oraclecloud.com|CX_1"),
]


@pytest.mark.parametrize(("module", "ats_ref"), JSON_COLLECTORS,
                          ids=[m.SOURCE_NAME for m, _ in JSON_COLLECTORS])
def test_json_collector_requests_the_board_sized_cap(module, ats_ref):
    seen: list[int | None] = []

    robots_seen: list[bool | None] = []

    def recording_fetch(url, **kw):
        seen.append(kw.get("max_bytes"))
        robots_seen.append(kw.get("respect_robots"))
        return 200, "", url

    module.collect(ats_ref, fetcher=recording_fetch)
    assert seen, "collector never called its fetcher"
    assert all(mb == JSON_API_MAX_BYTES for mb in seen), (
        f"{module.SOURCE_NAME} fetched with caps {seen}, "
        f"expected {JSON_API_MAX_BYTES} on every call")
    assert all(rr is False for rr in robots_seen), (
        f"{module.SOURCE_NAME} left the robots gate on for a documented "
        "board API - an API host that robots-disallows crawlers would "
        "silently collect zero")


def test_truncated_board_raises_instead_of_returning_empty():
    truncated = '{"jobs": [' + '{"id": 1, "title": "x"}, ' * 4000
    truncated = truncated.ljust(JSON_API_MAX_BYTES, " ")
    with pytest.raises(RuntimeError, match="truncated at the fetch cap"):
        decode_board_json(truncated)


def test_short_malformed_body_is_still_just_no_data():
    assert decode_board_json("<html>Not Found</html>") is None
    assert decode_board_json("") is None


def test_collect_surfaces_truncation_as_error_not_zero(monkeypatch):
    huge_invalid = '{"jobs": ['.ljust(JSON_API_MAX_BYTES, "x")

    def fake_fetch(url, **kw):
        return 200, huge_invalid, url

    with pytest.raises(RuntimeError):
        greenhouse.collect("acme", fetcher=fake_fetch)
