"""lever.py - Lever's public postings JSON API.

`createdAt` arrives as epoch MILLISECONDS, not a date string. Stored raw it
reached the UI as "1541085065881" in the Posted column, and sorted as text
rather than chronologically - 1,199 postings across the test profiles. Every
other collector yields an ISO date, so this one converts.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from unlatched.fetch import fetch as default_fetch
from unlatched.sources import (
    JSON_API_MAX_BYTES,
    Job,
    decode_board_json,
    html_to_text,
)

if TYPE_CHECKING:
    from collections.abc import Callable

SOURCE_NAME = "lever"

# Lever has existed since 2012; anything below this is not a posting date but
# a value that was already seconds, or garbage.
_MIN_PLAUSIBLE_MS = 1_000_000_000_000


def posted_date(created_at: object) -> str:
    """Epoch milliseconds -> ISO date. Anything unrecognised passes through
    as text rather than being discarded - an unparseable date is still what
    the board said, and losing it entirely would be worse than showing it.
    """
    raw = str(created_at or "").strip()
    if not raw.isdigit():
        return raw
    value = int(raw)
    if value < _MIN_PLAUSIBLE_MS:
        return raw
    try:
        return datetime.fromtimestamp(value / 1000, tz=UTC).date().isoformat()
    except (OverflowError, OSError, ValueError):
        return raw


def full_description(posting: dict[str, Any]) -> str:
    """The WHOLE posting, not just its opening paragraphs.

    Lever splits a posting across five fields, and only the first is the
    intro. Measured on softdocs/Client Support Technician, 2026-08-07:

        descriptionPlain   874 chars   <- all this collector used to store
        lists              5 sections, 2,683 chars
                           "What You'll Do", "Technical Skills",
                           "Soft Skills", "Education & Experience",
                           "What We Offer"
        additionalPlain    154 chars

    So 76% of every Lever posting was being dropped, and specifically the
    part that names the requirements. That is not just a display problem:
    coverage.py scores the resume against this text, requirements.py mines
    years/education/licences out of it, and keywords.py counts demand across
    it. A Lever row showing "-" in the Asks column was not a posting that
    asked for nothing; it was a posting whose requirements we never read.

    Headings are kept above their bullets. "5+ years" under "Education &
    Experience" means something different from the same words under "What We
    Offer", and the extractors read surrounding context.
    """
    parts: list[str] = []
    intro = posting.get("descriptionPlain") or html_to_text(posting.get("description") or "")
    if intro:
        parts.append(intro.strip())

    lists = posting.get("lists")
    if isinstance(lists, list):
        for section in lists:
            if not isinstance(section, dict):
                continue
            heading = html_to_text(str(section.get("text") or "")).strip()
            body = html_to_text(str(section.get("content") or "")).strip()
            if not body:
                continue
            parts.append(f"{heading}\n{body}" if heading else body)

    extra = posting.get("additionalPlain") or html_to_text(posting.get("additional") or "")
    if extra:
        parts.append(extra.strip())
    return "\n\n".join(parts)


def collect(
    ats_ref: str, *, fetcher: Callable[..., tuple[int, str, str]] = default_fetch,
) -> list[Job]:
    slug = ats_ref
    status, text, _ = fetcher(
        f"https://api.lever.co/v0/postings/{slug}?mode=json",
        timeout=20, max_bytes=JSON_API_MAX_BYTES,
        respect_robots=False)
    if status != 200 or not text:
        return []
    data = decode_board_json(text)
    if data is None:
        return []
    if not isinstance(data, list):
        return []
    out = []
    for j in data:
        cats = j.get("categories") or {}
        desc = full_description(j)
        out.append(Job(
            source=SOURCE_NAME,
            source_id=str(j.get("id", "")),
            title=str(j.get("text", "")),
            location=str(cats.get("location", "") if isinstance(cats, dict) else ""),
            url=str(j.get("hostedUrl", "")),
            posted=posted_date(j.get("createdAt")),
            description=desc,
            employment_type=str(cats.get("commitment", "") if isinstance(cats, dict) else ""),
        ))
    return [j for j in out if j.source_id and j.title]
