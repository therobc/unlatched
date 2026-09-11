"""remoteok.py - Remote OK's public jobs API.

A SEARCH source, like usajobs.py and unlike every board collector: it needs
no employer list at all, which is the whole point. Twelve of this package's
collectors sit idle until somebody names companies; this one returns
postings on install day with nothing configured and no key.

WHAT IT GIVES
One request to https://remoteok.com/api returns the latest ~100 postings as
JSON, complete with descriptions, company, location, tags, salary band and
the apply URL. Measured 2026-08-06.

NO PAGINATION. `?offset=100` returns the same records, so this is a "latest
100" feed rather than a searchable archive. Coverage therefore comes from
asking daily and keeping what arrives, which is exactly what the daily
refresh already does - each run adds whatever is new since the last.

ATTRIBUTION IS A CONDITION OF USE, NOT A COURTESY
Their API terms ask that the posting URL on Remote OK be linked back to and
that Remote OK be named as the source, on pain of access being withdrawn.
So `url` is always the Remote OK page (never a rewritten apply link), and
SOURCE_LABEL is what the app shows wherever provenance is displayed. Do not
"simplify" either of those away.

ROBOTS IS HONOURED HERE, unlike the board-API collectors. Those pass
respect_robots=False because a robots.txt is a crawler directive and a
documented API is not crawling. This host is different: its robots.txt
explicitly allows us (`User-agent: *` -> `Allow: /`, crawl-delay 1) while
naming and disallowing the large AI crawlers. When a site has gone to the
trouble of saying yes to us specifically, reading its file is the whole
point, not an obstacle.
"""
from __future__ import annotations

import json
from html import unescape as html_unescape
from typing import TYPE_CHECKING, Any

from unlatched.fetch import fetch as default_fetch
from unlatched.sources import JSON_API_MAX_BYTES, Job, html_to_text

if TYPE_CHECKING:
    from collections.abc import Callable

SOURCE_NAME = "remoteok"
IS_SEARCH_SOURCE = True

# Shown wherever the app names where a posting came from. Required by their
# terms; see the module docstring.
SOURCE_LABEL = "Remote OK"

# Shown in the Companies table for employers this source creates.
EMPLOYER_STATUS = "via Remote OK"

API_URL = "https://remoteok.com/api"

# Believed, not measured here: their robots.txt is read as asking for one
# second between requests. Verified by construction: `collect` below makes
# exactly one fetcher call per run, so the floor only matters if that ever
# changes.
CRAWL_DELAY_S = 1.0


def has_credentials(_cfg: dict[str, Any]) -> bool:
    """No key, no account. Verified by construction: cli.py's search-source
    loop calls `mod.has_credentials(cfg)` on every enabled entry from
    sources.search_sources() before collecting it, and this is that answer.
    """
    return True


CREDENTIALS_HINT = ""


def _job_from(record: dict[str, Any]) -> Job | None:
    job_id = str(record.get("id") or record.get("slug") or "").strip()
    title = str(record.get("position") or "").strip()
    if not job_id or not title:
        return None

    # Descriptions arrive as HTML. Screening reads prose, and requirements.py
    # counts on sentence structure surviving, which html_to_text preserves.
    description = html_to_text(str(record.get("description") or ""))

    # Every posting here is remote by definition - that is the entire board -
    # but the location field carries a country or region restriction often
    # enough to matter ("Windsor", "US only"). Kept as stated, with the
    # remote fact made explicit so screen.remote_evidence has something to
    # find rather than having to infer it from the source name.
    stated = str(record.get("location") or "").strip(" ,")
    location = f"Remote{f' - {stated}' if stated else ''}"

    tags = record.get("tags")
    tag_line = ""
    if isinstance(tags, list) and tags:
        tag_line = "\n\nTags: " + ", ".join(str(t) for t in tags if t)

    return Job(
        source=SOURCE_NAME,
        source_id=job_id,
        title=title,
        location=location,
        # Believed, not measured: the feed's "url" field is read as the Remote OK
        # page rather than the employer's own apply link, and linking back is a
        # condition of using this API - see the module docstring.
        url=str(record.get("url") or ""),
        posted=str(record.get("date") or ""),
        description=description + tag_line,
        # The feed carries company names HTML-encoded. Verified by construction:
        # `description` above is built through html_to_text, which unescapes
        # entities internally - `employer` below is not, so without this explicit
        # html_unescape call an ampersand in a company name would be stored and
        # shown as the literal "&amp;".
        employer=html_unescape(str(record.get("company") or "")).strip(),
    )


def collect(cfg: dict[str, Any], *,
            fetcher: Callable[..., tuple[int, str, str]] = default_fetch) -> list[Job]:
    """Every posting the feed is currently offering. Screening does the
    filtering, exactly as it does for a board - a search source that
    pre-filtered would hide postings from the person's own title list.
    """
    del cfg  # the feed takes no query; it returns what it has
    status, text, _final = fetcher(
        API_URL, timeout=30, max_bytes=JSON_API_MAX_BYTES,
        per_host_delay_s=CRAWL_DELAY_S)
    if status != 200 or not text:
        return []
    try:
        payload = json.loads(text)
    except (ValueError, TypeError):
        return []
    if not isinstance(payload, list):
        return []

    jobs: dict[str, Job] = {}
    for record in payload:
        if not isinstance(record, dict):
            continue
        # Believed, not measured: the feed's first element is read as a legal
        # notice rather than a posting - it carries "legal" and no id. Skipping by
        # SHAPE rather than by position - verified by construction: the check
        # below tests the record's keys, not its index - so a feed that stops
        # including it, or moves it, does not cost a posting or add a phantom one.
        if "legal" in record and not record.get("id"):
            continue
        job = _job_from(record)
        if job is not None:
            jobs[job.key()] = job
    return list(jobs.values())
