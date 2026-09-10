"""schema_org.py - schema.org/JobPosting JSON-LD embedded in a careers page.

Google for Jobs requires this markup, so a large share of career sites carry
it even when they run no ATS this package can fingerprint by URL - and it is
present even on pages whose search UI is a client-rendered SPA, because the
markup exists for a crawler rather than a visitor. That makes it the
fallback route for whatever `discover.py` could not otherwise identify.

`ats_ref` here is the confirmed careers page URL itself, not a slug - there
is no separate provider API to address.
"""
from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any

from unlatched.fetch import fetch as default_fetch
from unlatched.sources import Job, html_to_text

if TYPE_CHECKING:
    from collections.abc import Callable

SOURCE_NAME = "schema_org"

# The quotes around the attribute value are OPTIONAL in HTML5, and every
# minifier strips them: `<script type=application/ld+json>` is what a
# Hugo/Next-built careers page actually serves. Requiring quotes made this
# collector silently return zero from any minified page - not an error, not
# a warning, just a site that appeared to have no postings. Found on
# nodesk.co, 2026-08-06, but nothing about it is specific to that site.
#
# Applied to ONE tag's attributes at a time, never to a whole page. This was a
# single page-wide pattern -
#
#   <script[^>]*\btype\s*=\s*["\']?application/ld\+json["\']?[^>]*>(.*?)</script>
#
# - and it backtracked catastrophically: MEASURED at 88.7 SECONDS on 1MB of
# markup carrying many `<script type=application/ld+json` openings that never
# close (red-team follow-up, 2026-08-08). Every unclosed opening sent `(.*?)`
# scanning to the end of the document again, so cost grew with openings TIMES
# page length. A careers page can be written to do that on purpose, and this
# collector reads pages written by strangers - so it was a hang, at the
# choosing of whoever served the page.
LD_TYPE = re.compile(r'\btype\s*=\s*["\']?application/ld\+json', re.IGNORECASE)

SCRIPT_OPEN = "<script"
SCRIPT_CLOSE = "</script"


def script_blocks(html: str) -> list[tuple[str, str]]:
    """Every <script> tag as (attributes, body), in one forward pass.

    Plain string scanning rather than a regex, because the guarantee wanted
    here is not "a simpler pattern" but "no backtracking at all": `pos` only
    ever moves forward, so this is linear in the length of the page no matter
    what the page contains. An unclosed <script> ends the scan rather than
    restarting it.
    """
    out: list[tuple[str, str]] = []
    low = html.lower()
    pos = 0
    while True:
        start = low.find(SCRIPT_OPEN, pos)
        if start < 0:
            return out
        tag_end = low.find(">", start)
        if tag_end < 0:
            return out
        close = low.find(SCRIPT_CLOSE, tag_end)
        if close < 0:
            return out
        out.append((html[start + len(SCRIPT_OPEN):tag_end], html[tag_end + 1:close]))
        pos = close + len(SCRIPT_CLOSE)


def _stable_id(node: dict[str, Any], url: str) -> str:
    ident = node.get("identifier")
    if isinstance(ident, dict):
        value = ident.get("value")
        if value:
            return str(value)
    if isinstance(ident, str) and ident:
        return ident
    if url:
        return re.sub(r"[^A-Za-z0-9]+", "-", url).strip("-")[-80:]
    title = str(node.get("title", ""))
    return re.sub(r"[^A-Za-z0-9]+", "-", title).strip("-")[:60]


def parse_jsonld_jobs(html: str) -> list[dict[str, Any]]:
    """Every JobPosting node found in embedded JSON-LD, as plain dicts."""
    out = []
    for attrs, block in script_blocks(html or ""):
        if not LD_TYPE.search(attrs):
            continue
        try:
            data = json.loads(block.strip())
        except (ValueError, TypeError):
            continue
        stack = [data]
        while stack:
            node = stack.pop()
            if isinstance(node, list):
                stack.extend(node)
                continue
            if not isinstance(node, dict):
                continue
            if "@graph" in node:
                stack.append(node["@graph"])
            t = node.get("@type")
            types = t if isinstance(t, list) else [t]
            if "JobPosting" not in [str(x) for x in types]:
                continue
            loc = node.get("jobLocation")
            if isinstance(loc, list):
                loc = loc[0] if loc else {}
            addr = (loc or {}).get("address") if isinstance(loc, dict) else {}
            where = ""
            if isinstance(addr, dict):
                where = ", ".join(
                    str(addr.get(k)) for k in
                    ("addressLocality", "addressRegion", "addressCountry")
                    if addr.get(k))
            if str(node.get("jobLocationType", "")).upper() == "TELECOMMUTE":
                where = (where + " (Remote)").strip()
            # employmentType is singular in the spec but routinely a LIST in
            # the wild ("FULL_TIME", "PART_TIME" on the same posting).
            # str() on a list yields "['FULL_TIME', 'PART_TIME']", brackets
            # and quotes included, which employment.py then has to read
            # through. Joined instead, so what is stored is what was meant.
            kind = node.get("employmentType") or ""
            if isinstance(kind, list):
                kind = ", ".join(str(k) for k in kind if k)

            desc = node.get("description") or ""
            if isinstance(desc, str) and desc:
                desc = html_to_text(desc)
            # Only a SEARCH source needs this - a board collector already
            # knows whose board it is reading. nodesk.py does not: one page
            # per employer, and the employer is only named in the markup.
            org = node.get("hiringOrganization")
            employer = str(org.get("name") or "") if isinstance(org, dict) else ""

            out.append({
                "title": str(node.get("title", "")),
                "employer": employer,
                "location": where,
                "url": str(node.get("url", "") or ""),
                "posted": str(node.get("datePosted", "")),
                "employment_type": str(kind),
                "description": desc if isinstance(desc, str) else "",
                "identifier": node.get("identifier"),
            })
    return out


def collect(
    ats_ref: str, *, fetcher: Callable[..., tuple[int, str, str]] = default_fetch,
) -> list[Job]:
    url = ats_ref
    status, html, final = fetcher(url, timeout=25)
    if status != 200 or not html:
        return []
    out = []
    for node in parse_jsonld_jobs(html):
        job_url = node["url"] or final
        out.append(Job(
            source=SOURCE_NAME,
            source_id=_stable_id(node, job_url),
            title=node["title"],
            location=node["location"],
            url=job_url,
            posted=node["posted"],
            description=node["description"],
            employment_type=node["employment_type"],
        ))
    return [j for j in out if j.source_id and j.title]
