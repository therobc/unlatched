"""workable.py - Workable's public widget JSON API."""
from __future__ import annotations

from typing import TYPE_CHECKING

from unlatched.fetch import fetch as default_fetch
from unlatched.sources import JSON_API_MAX_BYTES, Job, decode_board_json, html_to_text

if TYPE_CHECKING:
    from collections.abc import Callable

SOURCE_NAME = "workable"


def collect(
    ats_ref: str, *, fetcher: Callable[..., tuple[int, str, str]] = default_fetch,
) -> list[Job]:
    slug = ats_ref
    status, text, _ = fetcher(
        f"https://apply.workable.com/api/v1/widget/accounts/{slug}?details=true",
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
        loc = j.get("location") or {}
        loc_s = ""
        if isinstance(loc, dict):
            loc_s = ", ".join(b for b in (loc.get("city", ""), loc.get("region", ""),
                                           loc.get("country", "")) if b)
        else:
            loc_s = str(loc)
        out.append(Job(
            source=SOURCE_NAME,
            source_id=str(j.get("shortcode", "") or j.get("id", "")),
            title=str(j.get("title", "")),
            location=loc_s,
            url=str(j.get("url", "") or j.get("application_url", "")),
            posted=str(j.get("published_on", "") or j.get("created_at", "")),
            description=html_to_text(j.get("description") or ""),
            employment_type=str(j.get("employment_type", "") or ""),
        ))
    return [j for j in out if j.source_id and j.title]
