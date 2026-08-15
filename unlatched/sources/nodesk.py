"""nodesk.py - NoDesk's remote-job listings, read through its sitemap.

A SEARCH source: it needs no employer list, which is the point. NoDesk
publishes no API, but it does publish a sitemap of 14,891 job pages, and
every one of those pages carries schema.org JobPosting markup - so the
parsing this needs already exists in schema_org.py.

THE PROBLEM THIS MODULE IS MOSTLY ABOUT: 14,891 pages at a page a second is
four hours. Nobody should do that to somebody else's site, or to a person
waiting on their first search. The way out is in the URLs themselves. A
NoDesk job URL is /remote-jobs/<company>-<title-words>/, so the SLUG names
the job before anything is fetched. Matching slugs against the person's own
title list first turns a four-hour crawl into a few dozen requests, and the
requests that are made are the ones they actually wanted.

The sitemap is ordered NEWEST FIRST - measured 2026-08-06: the first entry
was posted that July, the middle one in 2023, the last in 2002. So walking
from the top and stopping at a cap takes the freshest matches rather than an
arbitrary slice, and tomorrow's run picks up whatever has appeared above
them since. That ordering is load-bearing; if NoDesk ever reverses it this
module quietly starts collecting antiques, which is what
`test_the_newest_are_taken_first` is there to catch.

robots.txt is honoured (it allows us: `User-agent: *` -> `Allow: /`).
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from unlatched.fetch import fetch as default_fetch
from unlatched.sources import Job
from unlatched.sources.schema_org import parse_jsonld_jobs

if TYPE_CHECKING:
    from collections.abc import Callable

SOURCE_NAME = "nodesk"
IS_SEARCH_SOURCE = True

SOURCE_LABEL = "NoDesk"

# Shown in the Companies table for employers this source creates.
EMPLOYER_STATUS = "via NoDesk"

SITEMAP_URL = "https://nodesk.co/sitemap-jobs.xml"

# Pages fetched per run. Deliberately small: this is a politeness budget and
# a patience budget at the same time. The sitemap is newest-first and the
# search refreshes daily, so a cap costs recency of the tail, not coverage
# of what is arriving.
MAX_PAGES_PER_RUN = 40

LOC = re.compile(r"<loc>\s*(.*?)\s*</loc>", re.IGNORECASE | re.DOTALL)


def has_credentials(_cfg: dict[str, Any]) -> bool:
    """No key, no account."""
    return True


CREDENTIALS_HINT = ""


def _slug_of(url: str) -> str:
    """"https://nodesk.co/remote-jobs/acme-support-analyst/" -> the last path
    segment, lowercased, punctuation flattened to spaces so word matching
    works on it.
    """
    tail = url.rstrip("/").rsplit("/", 1)[-1]
    return re.sub(r"[^a-z0-9]+", " ", tail.lower()).strip()


def slug_wants(slug: str, terms: list[str]) -> bool:
    """Does this URL look like a job the person asked for?

    Every word of ANY wanted term must appear in the slug, adjacent or not -
    deliberately the same rule screen.title_wants applies to real titles, so
    a posting is not thrown away here on a stricter test than the one the
    screening stage would have used. Written out rather than imported
    because sources/ does not depend on screen.py in either direction, and
    this runs on 14,891 strings before anything is fetched.

    No terms configured means everything matches: a brand new profile has no
    title list yet, and the cap above is what keeps that bounded.
    """
    if not terms:
        return True
    words_in_slug = set(slug.split())
    for term in terms:
        wanted = [w for w in re.sub(r"[^a-z0-9]+", " ", term.lower()).split() if w]
        if wanted and all(w in words_in_slug for w in wanted):
            return True
    return False


def candidate_urls(sitemap_xml: str, terms: list[str],
                   limit: int = MAX_PAGES_PER_RUN) -> list[str]:
    """The job pages worth fetching, newest first, capped."""
    out = []
    for url in LOC.findall(sitemap_xml or ""):
        if "/remote-jobs/" not in url:
            continue
        if slug_wants(_slug_of(url), terms):
            out.append(url)
            if len(out) >= limit:
                break
    return out


def collect(cfg: dict[str, Any], *,
            fetcher: Callable[..., tuple[int, str, str]] = default_fetch) -> list[Job]:
    search = cfg.get("search") or {}
    terms = [str(t) for t in (search.get("title_include") or []) if str(t).strip()]

    status, xml, _final = fetcher(SITEMAP_URL, timeout=30)
    if status != 200 or not xml:
        return []

    jobs: dict[str, Job] = {}
    for url in candidate_urls(xml, terms):
        page_status, html, final = fetcher(url, timeout=25)
        if page_status != 200 or not html:
            continue
        for node in parse_jsonld_jobs(html):
            title = node.get("title") or ""
            if not title:
                continue
            job = Job(
                source=SOURCE_NAME,
                # The slug, not the JSON-LD identifier: NoDesk's markup
                # carries no identifier, and the slug is what stays stable
                # across re-collections of the same posting.
                source_id=_slug_of(final or url),
                title=str(title),
                location=str(node.get("location") or ""),
                url=final or url,
                posted=str(node.get("posted") or ""),
                description=str(node.get("description") or ""),
                employment_type=str(node.get("employment_type") or ""),
                employer=str(node.get("employer") or ""),
            )
            if job.source_id:
                jobs[job.key()] = job
    return list(jobs.values())
