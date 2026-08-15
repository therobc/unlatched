"""workday.py - Workday's CXS API. List is POST; detail is a second call.

The list endpoint (`/wday/cxs/{tenant}/{site}/jobs`) returns only title,
path, location and a relative posted-string - no description. The detail
endpoint carries the full description plus three fields worth reading
before recording a posting at all: `timeType` (full vs part time),
`startDate` (a real date), and `canApply` - a posting that has already
closed reports `canApply: false`, and there is no reason to keep a dead
link.

`ats_ref` is "tenant|wd_host|site", e.g. "acme|wd5|acme_careers".
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from unlatched.fetch import fetch as default_fetch
from unlatched.sources import JSON_API_MAX_BYTES, Job, decode_board_json, html_to_text

if TYPE_CHECKING:
    from collections.abc import Callable

SOURCE_NAME = "workday"
DEFAULT_SEARCH_TEXT = ""

# The CXS list endpoint serves at most 20 postings per request. Paging stops
# at the server-reported `total` or this many pages, whichever comes first -
# a ceiling so one enormous tenant cannot pin a collection run, but never a
# silent one: a truncated board is visible in the count the CLI prints
# against what the employer's site shows.
PAGE_SIZE = 20
MAX_PAGES = 10
# What a full run of this collector can return. The CLI reports when a
# board hits it, so a truncated board never reads as a small one.
MAX_COLLECTED = PAGE_SIZE * MAX_PAGES


def _list_page(base: str, tenant: str, site: str, offset: int,
               fetcher: Callable[..., tuple[int, str, str]]) -> dict[str, Any]:
    body = json.dumps({"appliedFacets": {}, "limit": PAGE_SIZE, "offset": offset,
                        "searchText": DEFAULT_SEARCH_TEXT}).encode("utf-8")
    status, text, _ = fetcher(f"{base}/wday/cxs/{tenant}/{site}/jobs",
                               data=body, content_type="application/json", timeout=25,
                               max_bytes=JSON_API_MAX_BYTES,
                               respect_robots=False)
    if status != 200 or not text:
        return {}
    data = decode_board_json(text)
    return data if isinstance(data, dict) else {}


def collect(ats_ref: str, *, fetcher: Callable[..., tuple[int, str, str]] = default_fetch,
            with_detail: bool = True) -> list[Job]:
    parts = (ats_ref or "").split("|")
    if len(parts) != 3:
        return []
    tenant, wd_host, site = parts
    base = f"https://{tenant}.{wd_host}.myworkdayjobs.com"

    postings: list[dict[str, Any]] = []
    # Only the FIRST page carries a real count: this API answers `total: 0`
    # on every subsequent page, so treating each page's value as authority
    # made the second page look like "that's everything" and a 246-posting
    # board silently collected 40.
    expected: int | None = None
    for page in range(MAX_PAGES):
        data = _list_page(base, tenant, site, page * PAGE_SIZE, fetcher)
        chunk = data.get("jobPostings") or []
        postings.extend(chunk)
        if expected is None:
            reported = data.get("total")
            if isinstance(reported, int) and reported > 0:
                expected = reported
        if not chunk:
            break
        if expected is not None and len(postings) >= expected:
            break

    out = []
    for j in postings:
        path = str(j.get("externalPath", ""))
        job = Job(
            source=SOURCE_NAME,
            source_id=path.strip("/") or str(j.get("bulletFields", [""])[0]),
            title=str(j.get("title", "")),
            location=str(j.get("locationsText", "")),
            url=f"{base}/{site}{path}",
            posted=str(j.get("postedOn", "")),
        )
        if with_detail and path:
            st2, text2, _ = fetcher(f"{base}/wday/cxs/{tenant}/{site}{path}",
                                     timeout=25, max_bytes=JSON_API_MAX_BYTES,
                                     respect_robots=False)
            if st2 == 200 and text2.lstrip().startswith("{"):
                try:
                    info = json.loads(text2).get("jobPostingInfo", {}) or {}
                except (ValueError, TypeError):
                    info = {}
                if info.get("canApply") is False:
                    continue  # closed - do not record a dead link
                job.description = html_to_text(info.get("jobDescription") or "")
                job.employment_type = str(info.get("timeType", "") or "")
                job.posted = str(info.get("startDate", "") or job.posted)
        out.append(job)
    return [j for j in out if j.source_id and j.title]
