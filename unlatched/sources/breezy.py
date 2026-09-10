"""breezy.py - Breezy HR's public JSON feed."""
from __future__ import annotations

import re
from typing import TYPE_CHECKING

from unlatched.fetch import fetch as default_fetch
from unlatched.sources import JSON_API_MAX_BYTES, Job, decode_board_json

if TYPE_CHECKING:
    from collections.abc import Callable

SOURCE_NAME = "breezy"


def collect(
    ats_ref: str, *, fetcher: Callable[..., tuple[int, str, str]] = default_fetch,
) -> list[Job]:
    slug = ats_ref
    status, text, _ = fetcher(
        f"https://{slug}.breezy.hr/json", timeout=20, max_bytes=JSON_API_MAX_BYTES,
        respect_robots=False)
    if status != 200 or not text.lstrip().startswith("["):
        return []
    data = decode_board_json(text)
    if data is None:
        return []
    out = []
    for j in data:
        loc = j.get("location") or {}
        city = ""
        if isinstance(loc, dict):
            city = (loc.get("name") or
                    ((loc.get("city") or "") + " " + (loc.get("country") or "")).strip())
        url = str(j.get("url", ""))
        source_id = str(j.get("id", "")) or re.sub(r"[^a-z0-9]+", "-", url.lower())[-40:]
        out.append(Job(
            source=SOURCE_NAME,
            source_id=source_id,
            title=str(j.get("name", "")),
            location=str(city),
            url=url,
            posted=str(j.get("published_date", "")),
        ))
    return [j for j in out if j.source_id and j.title]
