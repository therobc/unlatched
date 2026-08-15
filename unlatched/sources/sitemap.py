"""sitemap.py - Read a corporate careers portal through its sitemap.xml.

Big careers portals frequently render their search UI client-side, so
neither an ATS fingerprint nor a fetch of the search page itself yields
anything. Their sitemaps do not have that problem: Google for Jobs requires
schema.org/JobPosting markup on individual job pages, so those pages are
server-rendered even when the search UI in front of them is not.

The job title is in the URL slug, so a cheap pre-filter runs on the slug
BEFORE any detail page is fetched - a portal can list thousands of roles and
a search is after a handful, so fetching only the URLs whose slug plausibly
matches keeps this to a few dozen requests instead of several thousand.
`ats_ref` here is the portal host, e.g. "careers.example.com".
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING

from unlatched import links
from unlatched.fetch import fetch as default_fetch
from unlatched.sources import Job
from unlatched.sources.schema_org import parse_jsonld_jobs

if TYPE_CHECKING:
    from collections.abc import Callable

SOURCE_NAME = "sitemap"

JOB_URL = re.compile(r"/(job|jobs|career|careers|opening|position)s?/", re.IGNORECASE)
LOC_RE = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>", re.IGNORECASE)
INDEX_RE = re.compile(r"<sitemapindex", re.IGNORECASE)

DEFAULT_MAX_FETCH = 60
DEFAULT_MAX_SITEMAPS = 12


def _get(url: str, fetcher: Callable[..., tuple[int, str, str]]) -> tuple[int, str]:
    """Gzip is handled in fetch(), on the BYTES, where it can actually work.

    It was attempted here for a while, against the already-decoded text: 0x8b
    is not valid UTF-8, so the magic bytes had become U+FFFD before this ran
    and the branch could never fire. Every gzipped sitemap - and .gz is the
    common shape on a large portal - silently yielded nothing.
    """
    status, text, _ = fetcher(url, timeout=25)
    return status, text


def discover_sitemaps(host: str, fetcher: Callable[..., tuple[int, str, str]]) -> list[str]:
    """robots.txt first - the declared, intended entry point."""
    out = []
    status, text = _get(f"https://{host}/robots.txt", fetcher)
    if status == 200:
        out += re.findall(r"(?im)^\s*sitemap:\s*(\S+)", text or "")
    for guess in (f"https://{host}/sitemap.xml", f"https://{host}/sitemap_index.xml"):
        if guess not in out:
            out.append(guess)
    return out


def collect_urls(host: str, fetcher: Callable[..., tuple[int, str, str]],
                  max_maps: int = DEFAULT_MAX_SITEMAPS) -> list[str]:
    """Walk sitemaps, following one level of sitemap-index nesting.

    EVERY URL followed here came out of remote content - a `Sitemap:` line in
    the target's robots.txt, or a `<loc>` in its XML - so the site being
    collected chooses what this app requests. Unconstrained, that is an SSRF
    primitive: `<loc>http://192.168.1.1/jobs/reboot</loc>` and the app issues
    that GET from inside the person's own network (found by a red-team review).

    Two things now bound it. `same_site` keeps the walk on the portal we were
    pointed at, and fetch() refuses private and loopback destinations outright.
    Both, because they fail differently: same_site is a cheap string check that
    a clever hostname could argue with, and the address check is the one that
    actually decides.
    """
    seen_maps: set[str] = set()
    urls: list[str] = []
    queue = [u for u in discover_sitemaps(host, fetcher) if links.same_site(u, host)]
    while queue and len(seen_maps) < max_maps:
        sm = queue.pop(0)
        if sm in seen_maps:
            continue
        seen_maps.add(sm)
        status, xml = _get(sm, fetcher)
        if status != 200 or not xml:
            continue
        locs = [u for u in LOC_RE.findall(xml) if links.same_site(u, host)]
        if INDEX_RE.search(xml):
            children = sorted(locs, key=lambda u: 0 if JOB_URL.search(u) else 1)
            queue += [c for c in children if c not in seen_maps][:max_maps]
        else:
            urls += locs
    return list(dict.fromkeys(urls))


def slug_title(url: str) -> str:
    """Recover a readable title from the URL slug for pre-filtering.

    Strips EVERY trailing numeric segment, not just one - some portals shape
    URLs as /job/{city}/{title}/{tenant-id}/{job-id}, two numeric tails deep,
    so stepping back a single segment leaves a bare id and the title screen
    matches nothing at all.
    """
    path = re.sub(r"https?://[^/]+", "", url).rstrip("/")
    parts = [p for p in path.split("/") if p]
    while parts and re.fullmatch(r"\d+", parts[-1]):
        parts.pop()
    seg = parts[-1] if parts else ""
    return re.sub(r"[-_]+", " ", seg).strip()


def collect(ats_ref: str, *, fetcher: Callable[..., tuple[int, str, str]] = default_fetch,
            max_fetch: int = DEFAULT_MAX_FETCH,
            title_include: list[str] | None = None) -> list[Job]:
    host = ats_ref
    urls = collect_urls(host, fetcher)
    jobish = [u for u in urls if JOB_URL.search(u)]

    if title_include:
        needles = [t.lower() for t in title_include if t]
        passing = [u for u in jobish
                   if any(n in slug_title(u).lower() for n in needles)]
    else:
        passing = jobish

    if len(passing) > max_fetch:
        passing = passing[:max_fetch]

    out = []
    for u in passing:
        status, html, _ = fetcher(u, timeout=25)
        if status != 200 or not html:
            continue
        for node in parse_jsonld_jobs(html):
            source_id = re.sub(r"[^A-Za-z0-9]+", "-", (node["url"] or u)).strip("-")[-80:]
            out.append(Job(
                source=SOURCE_NAME,
                source_id=source_id,
                title=node["title"],
                location=node["location"],
                url=node["url"] or u,
                posted=node["posted"],
                description=node["description"],
                employment_type=node["employment_type"],
            ))
    return [j for j in out if j.source_id and j.title]
