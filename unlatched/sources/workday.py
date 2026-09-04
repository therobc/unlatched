"""workday.py - Workday's CXS API. List is POST; detail is a second call.

The list endpoint (`/wday/cxs/{tenant}/{site}/jobs`) returns only title,
path, location and a relative posted-string - no description. The detail
endpoint carries the full description plus three fields worth reading
before recording a posting at all: `timeType` (full vs part time),
`startDate` (a real date), and `canApply` - a posting that has already
closed reports `canApply: false`, and there is no reason to keep a dead
link.

`ats_ref` is "tenant|wd_host|site", e.g. "acme|wd5|acme_careers".

TWO WINDOWS PER RUN. The board is returned newest first, so a run reads the
newest pages every time to keep a search current, and separately walks a
slice of the BACKLOG from an offset that advances between runs. Without the
second window a board bigger than one run was truncated on every run for
ever, because paging always restarted at offset 0.
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

# The CXS list endpoint serves at most 20 postings per request.
#
# MAX_PAGES is THE NEW WINDOW: the newest 200 postings, read every run so a
# search stays current. It stops at the server-reported total when the board
# is smaller than that.
#
# BACKFILL_PAGES is the BACKLOG WINDOW, walked from an offset that advances
# each run. It exists because the new window alone was the whole collector,
# and paging always restarted at offset 0 - so anything past it was invisible
# on EVERY run, not merely the first. Measured across the fifty starter
# employers, one first page each: 41 of 48 boards held more than a run could
# take and 100,202 postings were never read at all. CVS Health kept 200 of
# 19,277; Kroger 500 of 12,134.
#
# Boards come back newest first (measured: offset 0 "Posted Today", offset
# 200 "Posted 4 Days Ago"), which is why the new window is the right slice to
# have if you can only have one - and why walking backwards from it reaches
# the rest in age order rather than at random.
PAGE_SIZE = 20
MAX_PAGES = 10
BACKFILL_PAGES = 20
BACKFILL_STRIDE = PAGE_SIZE * BACKFILL_PAGES
# What a full run of this collector can return: the new window plus the
# backfill window. The CLI reports when a board hits it, so a truncated
# board never reads as a small one - and with the backfill walking, hitting
# it now means "more to come next run" rather than "never".
MAX_COLLECTED = PAGE_SIZE * (MAX_PAGES + BACKFILL_PAGES)

# Detail requests per employer, whether or not a title filter is set.
#
# THE PRE-FILTER IS NOT ENOUGH ON ITS OWN. title_may_pass returns True when
# there is no filter, so without this a profile that has not set
# search.title_include - which is what a new one looks like, since the Quick
# start sets salary and work mode and not titles - would make one detail
# request per posting and pay five times the old cost for the deeper paging.
#
# 200 is deliberately the OLD ceiling: the newest 200 postings still get
# their descriptions exactly as before, and everything behind them arrives
# from the list page without one. A visible partial, not an invisible
# absence.
MAX_DETAIL = 200

# This collector takes search.title_include and uses it to decide which
# postings are worth a detail request - see the note above collect().
WANTS_TITLE_INCLUDE = True

# ...and remembers where its backlog walk got to, so cli.py hands the offset
# back on the next run. See collect().
WANTS_BACKFILL = True


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
            with_detail: bool = True,
            title_include: list[str] | None = None,
            backfill_from: int = 0) -> list[Job]:
    """Postings from a Workday tenant: the newest, plus a slice of the backlog.

    `title_include` is the profile's own title filter, applied HERE rather
    than only in screening because the description costs a request per
    posting - the same reasoning, and the same mechanism, as oracle_hcm.
    A posting that fails it is still returned from the list-page fields;
    only its description goes unfetched.

    `backfill_from` is where the caller's backlog walk had got to. It only
    ever increases and is taken modulo the board's real size here, because
    the caller never learns that size - only this side sees the reported
    total. Zero, or a board small enough for the new window, simply means no
    backlog walk happens.
    """
    parts = (ats_ref or "").split("|")
    if len(parts) != 3:
        return []
    tenant, wd_host, site = parts
    base = f"https://{tenant}.{wd_host}.myworkdayjobs.com"

    postings: list[dict[str, Any]] = []
    seen_paths: set[str] = set()

    def take(offset: int) -> tuple[int, int | None]:
        """One page in. Returns (how many arrived, the reported total).

        Deduplicated by path because the two windows can overlap on a small
        board, and a posting arriving twice would be collected twice.
        """
        data = _list_page(base, tenant, site, offset, fetcher)
        chunk = data.get("jobPostings") or []
        for row in chunk:
            path = str(row.get("externalPath", ""))
            if path and path in seen_paths:
                continue
            if path:
                seen_paths.add(path)
            postings.append(row)
        reported = data.get("total")
        return len(chunk), (reported if isinstance(reported, int)
                            and reported > 0 else None)

    # THE NEW WINDOW. The newest postings, every run, exactly as before.
    #
    # Only the FIRST page carries a real count: this API answers `total: 0`
    # on every subsequent page, so treating each page's value as authority
    # made the second page look like "that's everything" and a 246-posting
    # board silently collected 40.
    expected: int | None = None
    for page in range(MAX_PAGES):
        got, reported = take(page * PAGE_SIZE)
        if expected is None and reported is not None:
            expected = reported
        if not got:
            break
        if expected is not None and (page + 1) * PAGE_SIZE >= expected:
            break

    # THE BACKFILL WINDOW. Only worth walking on a board bigger than the new
    # window already covers - below that, the first loop has read all of it.
    #
    # The offset the caller hands over only ever increases; it is taken
    # modulo the board's real size here, because the caller cannot know that
    # size. A walk that reaches the end simply starts round again.
    if expected is not None and expected > MAX_PAGES * PAGE_SIZE:
        start = MAX_PAGES * PAGE_SIZE
        span = expected - start
        base_offset = start + (max(backfill_from, 0) % max(span, 1))
        for page in range(BACKFILL_PAGES):
            offset = base_offset + page * PAGE_SIZE
            if offset >= expected:
                break
            if not take(offset)[0]:
                break

    from unlatched.screen import title_may_pass

    out = []
    detail_calls = 0
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
        # The request is skipped, not the posting. See the docstring.
        worth_detail = (with_detail and path
                        and detail_calls < MAX_DETAIL
                        and title_may_pass(job.title, title_include))
        if worth_detail:
            detail_calls += 1
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
