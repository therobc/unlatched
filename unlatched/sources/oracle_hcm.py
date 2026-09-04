"""oracle_hcm.py - Oracle Fusion Cloud Recruiting (Oracle HCM).

Each tenant runs on its own pod host, e.g. "tenant.fa.us2.oraclecloud.com" -
there is no shared multi-tenant domain the way lever.co or ashbyhq.com work.
The public list endpoint is a GET (not a POST like Workday's CXS API):

  {host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions
    ?onlyData=true&expand=requisitionList.secondaryLocations
    &finder=findReqs;siteNumber={site},limit={n},offset={o}

and answers `{"items": [{"TotalJobsCount": N, "requisitionList": [...]}]}`.
Unlike Workday's CXS API, TotalJobsCount is reported accurately on every
page within the search window - there is no "real count on page one only"
trap here. There IS a different one: the search index behind `findReqs` has
a hard window (observed at an offset between 5,000 and 10,000 on a
15,000-job board) past which it reports both an empty requisitionList AND
TotalJobsCount 0, indistinguishable from "no more jobs". This collector's
own page ceiling (MAX_COLLECTED) stays well under that window, so it is
noted here rather than defended against.

Detail comes from a second endpoint, keyed by requisition Id and site:

  {host}/hcmRestApi/resources/latest/recruitingCEJobRequisitionDetails
    ?onlyData=true&expand=all&finder=ById;Id={id},siteNumber={site}

`ats_ref` is "host|site", e.g. "tenant.fa.us2.oraclecloud.com|CX_1". `site` is
an opaque identifier Oracle assigns per configured career site - it is not
always "CX_<number>": one tenant's main site is the bare slug "CX", and
another's is a custom name of the employer's own choosing. discover.py
captures whichever one a company's own careers page actually links to. If that capture is
missing or turns out to report zero postings, `collect` tries a short list
of identifiers seen in the wild before giving up on the tenant - some
tenants map an unrecognized siteNumber onto their own primary site rather
than erroring, so this is worth a few extra requests before concluding
there is nothing to collect.

TWO WINDOWS PER RUN, as in workday.py: the newest pages every run to keep a
search current, plus a slice of the BACKLOG from an offset that advances
between runs. The retail chains here are the largest boards the app reads,
and without the second window every one of them was truncated on every run.
"""
from __future__ import annotations

import urllib.parse
from typing import TYPE_CHECKING, Any

from unlatched.fetch import fetch as default_fetch
from unlatched.sources import JSON_API_MAX_BYTES, Job, decode_board_json, html_to_text

if TYPE_CHECKING:
    from collections.abc import Callable

SOURCE_NAME = "oracle_hcm"

# Observed in the wild: one tenant's primary site is CX_1; another's is the
# bare slug CX (CX_1001 is a secondary campus-recruiting site); a third, whose
# site carries a custom name, also answers to CX_1. None of these is a
# documented default - they are simply what has been seen to work when the
# site captured by discovery is missing or empty.
SITE_FALLBACKS = ("CX_1", "CX", "CX_1001")

# The list endpoint has been exercised up to a page size of 100 without
# error, but a big, polite page size that finishes a normal-sized tenant in
# a couple of requests is kinder to a real employer's system than pushing
# the observed ceiling. Paging stops at the server-reported total or this
# many pages, whichever comes first.
PAGE_SIZE = 50
MAX_PAGES = 10
# Pages of BACKLOG walked per run, from an offset that advances each time.
# The retail chains on this collector are the largest boards the app reads -
# measured, one first page each: Marriott 12,853 postings, Kroger 12,134,
# AutoZone 11,102, Albertsons 7,774. At 500 a run, every one of them was
# truncated on every run for ever, because paging always restarted at 0.
BACKFILL_PAGES = 20
BACKFILL_STRIDE = PAGE_SIZE * BACKFILL_PAGES
# What a full run of this collector can return: the new window plus the
# backfill window. The CLI reports when a board hits it, so a truncated
# board never reads as a small one - and with the backfill walking, hitting
# it now means "more to come next run" rather than "never".
MAX_COLLECTED = PAGE_SIZE * (MAX_PAGES + BACKFILL_PAGES)

# Detail requests per employer, whether or not a title filter is set.
# title_may_pass returns True when there is no filter, so without this a
# profile that has not set search.title_include would make one request per
# posting - up to 500 before the deeper paging, and 1,500 after it.
MAX_DETAIL = 200

# This collector remembers where its backlog walk got to - see collect().
WANTS_BACKFILL = True


def _list_page(base: str, site: str, offset: int,
                fetcher: Callable[..., tuple[int, str, str]]) -> dict[str, Any]:
    quoted_site = urllib.parse.quote(site, safe="")
    finder = f"findReqs;siteNumber={quoted_site},limit={PAGE_SIZE},offset={offset}"
    url = (f"{base}/hcmRestApi/resources/latest/recruitingCEJobRequisitions"
           f"?onlyData=true&expand=requisitionList.secondaryLocations&finder={finder}")
    status, text, _ = fetcher(url, timeout=25, max_bytes=JSON_API_MAX_BYTES,
                               respect_robots=False)
    if status != 200 or not text:
        return {}
    data = decode_board_json(text)
    if not isinstance(data, dict):
        return {}
    items = data.get("items") or []
    return items[0] if items and isinstance(items[0], dict) else {}


def _fetch_detail(base: str, site: str, job_id: str,
                   fetcher: Callable[..., tuple[int, str, str]]) -> dict[str, Any]:
    quoted_id = urllib.parse.quote(job_id, safe="")
    quoted_site = urllib.parse.quote(site, safe="")
    finder = f"ById;Id={quoted_id},siteNumber={quoted_site}"
    url = (f"{base}/hcmRestApi/resources/latest/recruitingCEJobRequisitionDetails"
           f"?onlyData=true&expand=all&finder={finder}")
    status, text, _ = fetcher(url, timeout=25, max_bytes=JSON_API_MAX_BYTES,
                               respect_robots=False)
    if status != 200 or not text:
        return {}
    data = decode_board_json(text)
    if not isinstance(data, dict):
        return {}
    items = data.get("items") or []
    return items[0] if items and isinstance(items[0], dict) else {}


def _resolve_site(base: str, requested_site: str,
                   fetcher: Callable[..., tuple[int, str, str]]) -> tuple[str, dict[str, Any]]:
    """Pick a site that actually reports postings, trying the one discovery
    captured before falling back to conventional identifiers. Returns the
    chosen site and the first page already fetched for it, so the caller
    never pays for the same request twice.
    """
    candidates = list(dict.fromkeys(s for s in (requested_site, *SITE_FALLBACKS) if s))
    for site in candidates:
        page = _list_page(base, site, 0, fetcher)
        if page.get("requisitionList"):
            return site, page
    return requested_site, {}


# This collector takes search.title_include and uses it to decide which
# postings are worth a detail request. See the note above collect().
WANTS_TITLE_INCLUDE = True


def _title_may_pass(title: str, title_include: list[str] | None) -> bool:
    """Could this title survive search.title_include? See
    screen.title_may_pass, which sitemap.py uses too - this rule existed
    twice, differently, until it was moved there.

    Imported inside the function because sources/__init__.py builds its
    registry lazily to avoid dragging every submodule's dependencies in
    with the package.
    """
    from unlatched.screen import title_may_pass
    return title_may_pass(title, title_include)


def collect(ats_ref: str, *, fetcher: Callable[..., tuple[int, str, str]] = default_fetch,
            with_detail: bool = True,
            title_include: list[str] | None = None,
            backfill_from: int = 0) -> list[Job]:
    """Postings from an Oracle recruiting tenant.

    `title_include` is the profile's own title filter, and it is used HERE
    rather than only in screening because the description costs a request per
    posting. Measured on a real tenant: 907 postings, 7 of which pass that
    filter - so 500 detail requests were being made to keep 7, and that one
    employer took ten minutes of every run. The title is already in the list
    page, so the decision can be made before spending anything.

    A posting that fails the title filter is still RETURNED, from the list-page
    fields, so nothing downstream loses sight of it. Only its description goes
    unfetched, and screening would have rejected it on the title regardless.

    `backfill_from` is where the caller's backlog walk had got to. It only ever
    increases and is taken modulo the board's real size here, because the
    caller never learns that size - only this side sees TotalJobsCount. Zero,
    or a board small enough for the new window, simply means no backlog walk
    happens.
    """
    # A bare host is valid input. Plenty of careers pages reference the
    # Oracle host without ever linking the candidate-experience path that
    # carries the site id, and _resolve_site can find a working site from
    # the host alone.
    parts = (ats_ref or "").split("|", 1)
    if not parts or not parts[0].strip():
        return []
    host = parts[0].strip()
    requested_site = parts[1].strip() if len(parts) == 2 else ""
    base = f"https://{host}"

    site, first_page = _resolve_site(base, requested_site, fetcher)
    if not first_page.get("requisitionList"):
        return []

    postings: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    def keep(chunk: list[dict[str, Any]]) -> int:
        """Add a page, ignoring anything already taken.

        Deduplicated by requisition id because the two windows can overlap
        on a small board, and a posting arriving twice would be collected
        twice.
        """
        for row in chunk:
            ident = str(row.get("Id", ""))
            if ident and ident in seen_ids:
                continue
            if ident:
                seen_ids.add(ident)
            postings.append(row)
        return len(chunk)

    keep(first_page.get("requisitionList") or [])
    expected = first_page.get("TotalJobsCount")
    expected = expected if isinstance(expected, int) and expected > 0 else None

    # THE NEW WINDOW. The newest postings, every run, exactly as before.
    for page_num in range(1, MAX_PAGES):
        if expected is not None and page_num * PAGE_SIZE >= expected:
            break
        page = _list_page(base, site, page_num * PAGE_SIZE, fetcher)
        if not keep(page.get("requisitionList") or []):
            break

    # THE BACKFILL WINDOW, on boards bigger than the new window covers. The
    # offset only ever increases in the caller and is taken modulo the real
    # size here, because only this side sees the reported total.
    if expected is not None and expected > MAX_PAGES * PAGE_SIZE:
        start = MAX_PAGES * PAGE_SIZE
        span = expected - start
        base_offset = start + (max(backfill_from, 0) % max(span, 1))
        for page_num in range(BACKFILL_PAGES):
            offset = base_offset + page_num * PAGE_SIZE
            if offset >= expected:
                break
            page = _list_page(base, site, offset, fetcher)
            if not keep(page.get("requisitionList") or []):
                break

    out = []
    detail_calls = 0
    for req in postings[:MAX_COLLECTED]:
        job_id = str(req.get("Id", ""))
        job = Job(
            source=SOURCE_NAME,
            source_id=job_id,
            title=str(req.get("Title", "")),
            location=str(req.get("PrimaryLocation", "")),
            url=f"{base}/hcmUI/CandidateExperience/en/sites/{site}/job/{job_id}",
            posted=str(req.get("PostedDate", "") or ""),
        )
        # The request is skipped, not the posting. See the docstring.
        worth_detail = (with_detail and job_id
                        and detail_calls < MAX_DETAIL
                        and _title_may_pass(job.title, title_include))
        if worth_detail:
            detail_calls += 1
            detail = _fetch_detail(base, site, job_id, fetcher)
            if detail:
                sections = [str(detail.get(k, "") or "") for k in
                            ("ExternalDescriptionStr", "ExternalResponsibilitiesStr",
                             "ExternalQualificationsStr")]
                job.description = html_to_text("\n".join(s for s in sections if s))
                job.employment_type = str(detail.get("JobSchedule", "") or "")
                posted = str(detail.get("ExternalPostedStartDate", "") or "")
                if posted:
                    job.posted = posted.split("T", 1)[0]
        out.append(job)
    return [j for j in out if j.source_id and j.title]
