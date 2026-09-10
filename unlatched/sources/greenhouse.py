"""greenhouse.py - Greenhouse's public job-board JSON API.

The plain board endpoint returns title/location/url only - no description.
Screening a posting with no description fails it for lack of any evidence at
all, so every Greenhouse find would look empty. `?content=true` returns the
full body in the same request, which is far cheaper than fetching each
posting's own page afterward.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from unlatched.fetch import fetch as default_fetch
from unlatched.sources import JSON_API_MAX_BYTES, Job, decode_board_json, html_to_text

if TYPE_CHECKING:
    from collections.abc import Callable

SOURCE_NAME = "greenhouse"


def collect(
    ats_ref: str, *, fetcher: Callable[..., tuple[int, str, str]] = default_fetch,
) -> list[Job]:
    slug = ats_ref
    status, text, _ = fetcher(
        f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true",
        timeout=30, max_bytes=JSON_API_MAX_BYTES,
        respect_robots=False)
    if status != 200 or not text:
        return []
    data = decode_board_json(text)
    if data is None:
        return []
    out = []
    for j in data.get("jobs", []):
        loc = j.get("location") or {}
        out.append(Job(
            source=SOURCE_NAME,
            source_id=str(j.get("id", "")),
            title=str(j.get("title", "")),
            location=str(loc.get("name", "") if isinstance(loc, dict) else loc),
            url=str(j.get("absolute_url", "")),
            posted=str(j.get("updated_at", ""))[:10],
            description=html_to_text(j.get("content") or ""),
        ))
    return [j for j in out if j.source_id and j.title]
