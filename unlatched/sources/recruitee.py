"""recruitee.py - Recruitee's public offers JSON API."""
from __future__ import annotations

from typing import TYPE_CHECKING

from unlatched.fetch import fetch as default_fetch
from unlatched.sources import JSON_API_MAX_BYTES, Job, decode_board_json, html_to_text

if TYPE_CHECKING:
    from collections.abc import Callable

SOURCE_NAME = "recruitee"


def collect(
    ats_ref: str, *, fetcher: Callable[..., tuple[int, str, str]] = default_fetch,
) -> list[Job]:
    slug = ats_ref
    status, text, _ = fetcher(
        f"https://{slug}.recruitee.com/api/offers/",
        timeout=20, max_bytes=JSON_API_MAX_BYTES,
        respect_robots=False)
    if status != 200 or not text:
        return []
    data = decode_board_json(text)
    if data is None:
        return []
    raw = data.get("offers") if isinstance(data, dict) else None
    if raw is None:
        return []
    out = [
        Job(
            source=SOURCE_NAME,
            source_id=str(j.get("id", "")),
            title=str(j.get("title", "")),
            location=str(j.get("location", "")),
            url=str(j.get("careers_url", "") or j.get("careers_apply_url", "")),
            posted=str(j.get("published_at", "")),
            description=html_to_text(j.get("description") or ""),
            employment_type=str(j.get("employment_type", "") or ""),
        )
        for j in raw
    ]
    return [j for j in out if j.source_id and j.title]
