"""bamboohr.py - BambooHR's public careers endpoints.

TWO calls per board, not one. `/careers/list` enumerates the openings but
returns only a title, a location and an id - no description, no date, no pay.
Every one of those lives behind `/careers/{id}/detail`, which is why this is
the only board collector here that fetches per posting.

That detail call is what took this source from the thinnest rows in the
database (title and location, nothing else) to the same shape every other
collector produces. A posting with no description cannot be screened on
requirements, cannot be keyword-mined, and gives the reader nothing to judge.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from unlatched.fetch import fetch as default_fetch
from unlatched.sources import JSON_API_MAX_BYTES, Job, decode_board_json, html_to_text

if TYPE_CHECKING:
    from collections.abc import Callable

SOURCE_NAME = "bamboohr"

# One request per posting means a large board is a large number of requests.
# BambooHR is a small-to-mid-market product and these boards run to dozens,
# not thousands, but the ceiling is here so a surprise cannot run away.
MAX_DETAIL_FETCHES = 150

# What a full run of this collector can return, so cli.py can SAY the
# board was cut short. Without it a 300-posting board returned 150 and
# nothing distinguished that from a board with 150 postings on it.
#
# EXACT HERE, unlike the page-walking collectors: the list is sliced to
# this length directly, so a capped run returns exactly this many.
MAX_COLLECTED = MAX_DETAIL_FETCHES


def _location(loc: Any, ats_loc: Any, is_remote: bool) -> str:
    """Location arrives in two different shapes. `location` is frequently
    all-nulls while `atsLocation` carries the real city/state, so both are
    tried before falling back to nothing.
    """
    for candidate in (ats_loc, loc):
        if not isinstance(candidate, dict):
            continue
        city = candidate.get("city") or ""
        state = candidate.get("state") or ""
        where = ", ".join(str(x) for x in (city, state) if x)
        if where:
            return f"{where} (Remote)" if is_remote else where
    return "Remote" if is_remote else ""


def _detail(slug: str, job_id: str,
            fetcher: Callable[..., tuple[int, str, str]]) -> dict[str, Any]:
    status, text, _ = fetcher(
        f"https://{slug}.bamboohr.com/careers/{job_id}/detail",
        timeout=20, max_bytes=JSON_API_MAX_BYTES, respect_robots=False)
    if status != 200 or not text.lstrip().startswith("{"):
        return {}
    data = decode_board_json(text)
    if not isinstance(data, dict):
        return {}
    result = data.get("result")
    opening = result.get("jobOpening") if isinstance(result, dict) else None
    return opening if isinstance(opening, dict) else {}


def _description(opening: dict[str, Any]) -> str:
    body = html_to_text(str(opening.get("description") or ""))
    # Pay is its own field here rather than prose, the same way USAJOBS
    # reports it - folding it into the text is what lets the existing
    # salary extractor find it without a per-source special case.
    pay = str(opening.get("compensation") or "").strip()
    if pay:
        body = f"{body}\n\n{pay}" if body else pay
    return body


def collect(
    ats_ref: str, *, fetcher: Callable[..., tuple[int, str, str]] = default_fetch,
) -> list[Job]:
    slug = ats_ref
    status, text, _ = fetcher(
        f"https://{slug}.bamboohr.com/careers/list",
        timeout=20, max_bytes=JSON_API_MAX_BYTES,
        respect_robots=False)
    if status != 200 or not text.lstrip().startswith("{"):
        return []
    data = decode_board_json(text)
    if data is None:
        return []

    out = []
    for j in list(data.get("result", []))[:MAX_DETAIL_FETCHES]:
        job_id = str(j.get("id", ""))
        title = str(j.get("jobOpeningName", ""))
        if not job_id or not title:
            continue
        opening = _detail(slug, job_id, fetcher)
        # A detail call that fails still leaves a usable listing row - the
        # posting is real, it just has less on it. Dropping it would lose a
        # job over a single flaky request.
        out.append(Job(
            source=SOURCE_NAME,
            source_id=job_id,
            title=title,
            location=_location(j.get("location"), j.get("atsLocation"),
                                bool(j.get("isRemote"))),
            url=str(opening.get("jobOpeningShareUrl")
                    or f"https://{slug}.bamboohr.com/careers/{job_id}"),
            posted=str(opening.get("datePosted") or ""),
            description=_description(opening),
            employment_type=str(opening.get("employmentStatusLabel")
                                 or opening.get("employmentType") or ""),
        ))
    return out
