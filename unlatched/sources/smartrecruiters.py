"""smartrecruiters.py - SmartRecruiters' public postings JSON API.

The list endpoint paginates and reports the true size in `totalFound`;
reading only the first page silently truncates any board bigger than the
page size. `collect` keeps paging until it has seen `totalFound` postings
or a sane page-count ceiling, whichever comes first.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from unlatched.fetch import fetch as default_fetch
from unlatched.sources import (
    JSON_API_MAX_BYTES,
    Job,
    decode_board_json,
    html_to_text,
)

if TYPE_CHECKING:
    from collections.abc import Callable

SOURCE_NAME = "smartrecruiters"
PAGE_SIZE = 100
MAX_PAGES = 20

# The list endpoint does NOT carry the posting text. `jobAd` is null on every
# row it returns; the text lives on the per-posting detail endpoint, split
# across four sections. Measured on Sodexo, 2026-08-07: 100 of 100 postings
# collected with an EMPTY description, which meant no Fit score, no
# requirements extracted, and every one of them screened as "description too
# short to judge".
#
# So a detail fetch per posting is not an optimisation, it is the only way
# this source returns anything usable. It costs one request per job, which is
# why it is capped: the first DETAIL_CAP postings of a board get their full
# text, and anything past that is still recorded (title, location, link)
# rather than dropped. A board bigger than the cap therefore degrades to what
# this collector used to return for ALL of it.
DETAIL_CAP = 60

# In the order they read on the posting page. companyDescription first is
# deliberate: it is what the employer leads with, and requirements.py reads
# surrounding context when it decides what a "5+ years" belongs to.
AD_SECTIONS = ("companyDescription", "jobDescription", "qualifications",
                "additionalInformation")
# What a full run of this collector can return. The CLI reports when a
# board hits it, so a truncated board never reads as a small one.
MAX_COLLECTED = PAGE_SIZE * MAX_PAGES


def _detail_text(slug: str, job_id: str,
                 fetcher: Callable[..., tuple[int, str, str]]) -> str:
    """Every section of one posting's ad, joined with its headings.

    Returns "" for anything unreadable rather than raising: one posting that
    will not load must not cost the other ninety-nine.
    """
    if not job_id:
        return ""
    status, text, _ = fetcher(
        f"https://api.smartrecruiters.com/v1/companies/{slug}/postings/{job_id}",
        timeout=20, max_bytes=JSON_API_MAX_BYTES, respect_robots=False)
    if status != 200 or not text:
        return ""
    data = decode_board_json(text)
    if not isinstance(data, dict):
        return ""
    sections = (data.get("jobAd") or {}).get("sections") or {}
    if not isinstance(sections, dict):
        return ""
    parts = []
    for name in AD_SECTIONS:
        block = sections.get(name)
        if not isinstance(block, dict):
            continue
        body = html_to_text(str(block.get("text") or "")).strip()
        if body:
            title = html_to_text(str(block.get("title") or "")).strip()
            parts.append(f"{title}\n{body}" if title else body)
    return "\n\n".join(parts)


def collect(
    ats_ref: str, *, fetcher: Callable[..., tuple[int, str, str]] = default_fetch,
) -> list[Job]:
    slug = ats_ref
    out: list[Job] = []
    offset = 0
    for _ in range(MAX_PAGES):
        status, text, _ = fetcher(
            f"https://api.smartrecruiters.com/v1/companies/{slug}/postings"
            f"?limit={PAGE_SIZE}&offset={offset}", timeout=20, max_bytes=JSON_API_MAX_BYTES,
            respect_robots=False)
        if status != 200 or not text:
            break
        data = decode_board_json(text)
        if data is None:
            break
        if not isinstance(data, dict):
            break
        page = data.get("content") or []
        for j in page:
            loc = j.get("location") or {}
            loc_s = ""
            if isinstance(loc, dict):
                loc_s = ", ".join(b for b in (loc.get("city", ""), loc.get("region", ""),
                                               loc.get("country", "")) if b)
                if loc.get("remote"):
                    loc_s = f"Remote {loc_s}".strip()
            out.append(Job(
                source=SOURCE_NAME,
                source_id=str(j.get("id", "")),
                title=str(j.get("name", "")),
                location=loc_s,
                url=str(j.get("applyUrl", "") or ""),
                posted=str(j.get("releasedDate", "") or ""),
                description=_detail_text(slug, str(j.get("id", "")), fetcher)
                if len(out) < DETAIL_CAP else "",
            ))
        total = data.get("totalFound")
        offset += PAGE_SIZE
        if not isinstance(total, int) or offset >= total or not page:
            break
    return [j for j in out if j.source_id and j.title]
