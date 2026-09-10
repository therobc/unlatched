"""Lever reports `createdAt` as epoch MILLISECONDS.

Stored raw it reached the Posted column as "1541085065881" and sorted as
text rather than chronologically - 1,199 postings across the test profiles,
and the only source in the package not yielding an ISO date.
"""
from __future__ import annotations

import json
from typing import Any

import pytest

from unlatched.sources import lever


@pytest.mark.parametrize(("raw", "expected"), [
    (1541085065881, "2018-11-01"),
    ("1755000000000", "2025-08-12"),
])
def test_epoch_milliseconds_become_an_iso_date(raw: Any, expected: str):
    assert lever.posted_date(raw) == expected


@pytest.mark.parametrize("raw", ["2026-07-20", "not-a-date", "12345", "", None])
def test_anything_unrecognised_passes_through_rather_than_being_lost(raw: Any):
    """An unparseable date is still what the board said. Discarding it would
    be worse than showing it.
    """
    assert lever.posted_date(raw) == str(raw or "").strip()


def test_collect_yields_a_date_not_a_number():
    payload = [{
        "id": "abc", "text": "Support Analyst",
        "createdAt": 1541085065881,
        "categories": {"location": "Remote", "commitment": "Full-time"},
        "descriptionPlain": "Help customers.",
        "hostedUrl": "https://jobs.lever.co/acme/abc",
    }]

    def fetch(url: str, **_kw: Any) -> tuple[int, str, str]:
        return 200, json.dumps(payload), url

    jobs = lever.collect("acme", fetcher=fetch)
    assert jobs[0].posted == "2018-11-01"
