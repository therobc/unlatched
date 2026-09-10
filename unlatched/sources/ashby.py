"""ashby.py - Ashby's public job-board JSON API."""
from __future__ import annotations

from typing import TYPE_CHECKING

from unlatched.fetch import fetch as default_fetch
from unlatched.sources import JSON_API_MAX_BYTES, Job, decode_board_json, html_to_text

if TYPE_CHECKING:
    from collections.abc import Callable

SOURCE_NAME = "ashby"


def collect(
    ats_ref: str, *, fetcher: Callable[..., tuple[int, str, str]] = default_fetch,
) -> list[Job]:
    slug = ats_ref
    status, text, _ = fetcher(
        f"https://api.ashbyhq.com/posting-api/job-board/{slug}",
        timeout=20, max_bytes=JSON_API_MAX_BYTES,
        respect_robots=False)
    if status != 200 or not text:
        return []
    data = decode_board_json(text)
    if data is None:
        return []
    raw = data.get("jobs") if isinstance(data, dict) else None
    if raw is None:
        return []
    out = []
    for j in raw:
        desc = j.get("descriptionPlain") or html_to_text(j.get("descriptionHtml") or "")
        out.append(Job(
            source=SOURCE_NAME,
            source_id=str(j.get("id", "")),
            title=str(j.get("title", "")),
            location=str(j.get("location", "")),
            url=str(j.get("jobUrl", "") or j.get("applyUrl", "")),
            posted=str(j.get("publishedAt", "")),
            description=desc,
            employment_type=str(j.get("employmentType", "") or ""),
        ))
    return [j for j in out if j.source_id and j.title]
