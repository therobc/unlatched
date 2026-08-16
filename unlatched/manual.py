"""manual.py - add a job the person found themselves, by link.

Every other route into this app starts with a board we can read. This one
starts with a person who has already found the job: they are on the posting,
they are about to apply, and they want it in their pipeline so the follow-up
is tracked with everything else. Nothing else in the app can record that.

THE RULE THIS MODULE EXISTS TO KEEP
Adding a job by link does NOT mean fetching whatever is at the end of the
link. Some sites are ones this project will not read - the aggregators
whose terms forbid it - and a feature that quietly went and fetched them
would break that rule on the user's behalf while looking helpful. So the
host decides the behaviour:

  a site we read anyway  -> fetch it, fill in the title, employer and
                            description, and screen it like any other posting
  an attended-only site  -> read the PUBLIC page for this ONE job, because the
                            person running it asked for that one page. See
                            ATTENDED_ONLY_HOSTS for what that does and does
                            not mean, and for what was measured first
  the aggregators        -> fetch NOTHING. Keep the link, take what the
                            person typed, and let them paste the description
                            themselves if they want a Fit score

Both paths produce the same kind of row: it appears in the list, it takes a
status, and it is scored by the same screening code. The only difference is
who did the typing.

A manual job is never marked "taken down" by a board collect - see
db.mark_delisted. There is no board watching it, so absence is not evidence.
"""
from __future__ import annotations

import json
import re
import urllib.parse
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from . import collectors as collectors_mod
from . import db, screen
from . import db as db_mod
from . import links as links_mod
from . import status as status_mod
from .fetch import fetch as default_fetch
from .sources import html_to_text
from .sources.schema_org import parse_jsonld_jobs

if TYPE_CHECKING:
    import sqlite3
    from collections.abc import Callable, Sequence

SOURCE_NAME = "manual"

# Rows the manual refresh button covers. HAND-ADDED ONLY.
#
# IMPORTED ROWS WERE IN THIS LIST AND ARE NOT ANY MORE (decided 2026-08-12). The
# original reasoning was sound when it was written (decided 2026-08-09): "no board
# is watching either, so nothing else can tell the person the posting has gone."
#
# THAT PREMISE EXPIRED. The collector that sends imported rows now detects
# closures and pushes them in - 62 in one handoff on 2026-08-12, of which 53
# matched rows here and were applied, 0 left live. Something IS watching them,
# so re-reading them is a second automated reader for information this app is
# already being handed.
#
# IT WAS ALSO THE WRONG READER. Measured on a real profile before the change:
# every one of the 334 rows this button would have fetched was an imported
# LinkedIn URL and not one was hand-added. It fetched nothing only because
# fetch.read_added_links ships off - and that setting has to be turned ON for
# hand-added links to work at all, which would have armed it.
#
# Hand-added rows stay person-triggered. Never swept on a timer, in a collect,
# or in the scheduled refresh; the once-a-day-per-job and per-run limits apply.
#
# A COLLECTOR CAN ASK TO BE ADDED TO THIS, and asking is all it can do - see
# `recheckable_sources` below and `collectors.Refetch`. Being in scope decides
# which rows are CONSIDERED. It never decides which hosts are read: that is
# `may_fetch`, applied per row, and a collector cannot reach it.
ALWAYS_RECHECKABLE = (SOURCE_NAME,)

# Hosts this app does not read, ever. Aggregators whose terms forbid
# automated reading; FlexJobs additionally puts a paywall in front of the
# descriptions and refuses even robots.txt to a non-browser client
# (measured 2026-08-06).
#
# Being on this list does NOT stop somebody adding a job from there. It
# stops US going and looking. The link is kept, the person types what they
# know, and nothing is requested from the site.
NEVER_FETCH = (
    "indeed.com",
    "glassdoor.com",
    "jobs4tn.gov",
    "flexjobs.com",
)

# Sites this app reads ONLY when a person is adding one job by hand.
#
# A CATEGORY, not a site. LinkedIn is the only member today and is what this
# path is tested against, because it is the most popular - but nothing here is
# about LinkedIn, and naming the category after it would say there is an
# integration where there is only a rule (decided 2026-08-08).
#
# What puts a host in here: its robots.txt asks automated tools not to read the
# job path, and this app honours that everywhere EXCEPT on this one deliberate,
# person-present, one-page-at-a-time path. That is a decision by the first user
# (decided 2026-08-06): "I'm not using AI to scrape, I'm just logging a job in the
# app."
#
# WHAT WAS MEASURED BEFORE BUILDING IT, on 2026-08-06, against LinkedIn:
#   * A logged-out request with this app's own user agent returns 200 and the
#     full public job page. No account, no session, no login.
#   * That page carries NO schema.org markup - the structured route every
#     other collector here uses does not exist. Title, employer and
#     description have to be lifted out of the page's own HTML containers.
#   * robots.txt disallows /jobs/view/ for EVERY user agent, including "*".
#
# The exception is kept as narrow as it can be:
#   - only when a person is adding ONE job they are looking at, by hand
#   - one request for that one URL: no crawling, no related jobs, no paging
#   - this app's honest user agent. We do not pretend to be a browser, and if
#     a site ever blocks that agent, that is an answer and the fallback is
#     typing - not a better disguise
#   - no login, no cookies, no session: only what a signed-out visitor gets
#   - never in a collect, never in the scheduled refresh, never at scale
#
# `fetch.read_added_links` in config.json turns the whole hand-add read off.
ATTENDED_ONLY_HOSTS = ("linkedin.com",)


# Both moved to links.py, where fetch.py and db.py read the same definitions.
# Re-exported rather than re-implemented: two copies of "what is this URL's
# host" is exactly how the desktop ended up with a version that could be
# spoofed while this one could not.
host_of = links_mod.host_of
_host_matches = links_mod.host_matches


def is_attended_only(url: str) -> bool:
    """Is this a host we read only with a person present and asking?"""
    return _host_matches(host_of(url), ATTENDED_ONLY_HOSTS)


def may_fetch(url: str, *, hand_added: bool = False) -> bool:
    """Whether this app will request anything from the host of `url`.

    `hand_added` says a person is adding or re-checking this link right now,
    and that the setting permitting that is on. Nothing outside the hand-add
    path may pass it - that is what keeps ATTENDED_ONLY_HOSTS out of every
    collect and every scheduled refresh.
    """
    host = host_of(url)
    if not host:
        return False
    if _host_matches(host, NEVER_FETCH):
        return False
    if _host_matches(host, ATTENDED_ONLY_HOSTS):
        return hand_added
    return True


def stable_id(url: str) -> str:
    """The same link added twice is the same job, not two.

    Query strings are dropped: a posting URL copied out of an email or a
    search result carries tracking parameters that differ every time, and
    keying on them would fill the list with duplicates of one job.
    """
    parts = urllib.parse.urlsplit(url.strip())
    clean = f"{parts.hostname or ''}{parts.path}".rstrip("/").lower()
    return re.sub(r"[^a-z0-9]+", "-", clean).strip("-")[:120]


# LinkedIn's public job page carries no schema.org markup, so these lift the
# same three facts out of the page's own containers. CSS class names are a
# far weaker contract than markup: a restyle breaks them, and it breaks them
# SILENTLY, which is why read_linkedin returns {} and the person types
# instead of the app inventing anything.
# Every one of these is `marker ... > body </tag>`, and every one of them is a
# lazy run across a page written by somebody else. The body run is what bites:
# on markup carrying many near-matches that never close, each candidate start
# sends `(.*?)` scanning to the end of the document, so cost grows with
# near-matches TIMES page length. LI_DESCRIPTION did exactly that on 1MB of
# such markup (red-team follow-up, 2026-08-08) - the same defect as the old
# page-wide LD_BLOCK, on the one path that reads a site whose markup we neither
# control nor are welcome on.
#
# So the closing tag is found with str.find instead, exactly once, moving
# forward - the same structural fix schema_org.script_blocks uses. A length cap
# would only have made the multiplication smaller (measured: 7.4s at a 100,000
# cap); this removes it. Each pattern below matches only the OPENING tag, where
# `[^>]*` cannot escape the tag it belongs to because an attribute list cannot
# contain '>'.
LI_TITLE = re.compile(r"<h1[^>]*>", re.IGNORECASE)
LI_ORG = re.compile(r"topcard__org-name-link[^>]*>", re.IGNORECASE)
LI_LOCATION = re.compile(r"topcard__flavor--bullet[^>]*>", re.IGNORECASE)
LI_DESCRIPTION = re.compile(r"show-more-less-html__markup[^>]*>", re.IGNORECASE)


def _tag_body(html: str, opener: re.Pattern[str], closer: str) -> str:
    """What sits between an opening tag and its closer.

    One regex search for the opening tag, then one forward `find` for the
    close. Both linear, and neither can be made to rescan: markup that opens
    the tag a thousand times and never closes it costs one sweep, not a
    thousand.
    """
    m = opener.search(html)
    if not m:
        return ""
    end = html.lower().find(closer, m.end())
    return html[m.end():end] if end >= 0 else ""


def read_linkedin(html: str) -> dict[str, str]:
    """Title, employer, location and description from a public job page."""
    def first(rx: re.Pattern[str], closer: str) -> str:
        return html_to_text(_tag_body(html, rx, closer)).strip()

    description = first(LI_DESCRIPTION, "</div")
    title = first(LI_TITLE, "</h1")
    # A page that gave neither is a page we did not really read - a sign-in
    # wall, a redirect, or a restyle. Say nothing rather than store a
    # heading that happened to be the first <h1> on an error page.
    if not (title and description):
        return {}
    return {
        "title": title,
        "employer": first(LI_ORG, "</a"),
        "location": first(LI_LOCATION, "</span"),
        "description": description,
        "posted": "",
        "employment_type": "",
    }


def read_posting(url: str, *,
                 fetcher: Callable[..., tuple[int, str, str]] = default_fetch,
                 hand_added: bool = False,
                 ) -> dict[str, str]:
    """What the page itself says, when we are allowed to look and it carries
    something readable. Empty dict otherwise - a caller treats that as "the
    person will have to type it", never as an error.
    """
    if not may_fetch(url, hand_added=hand_added):
        return {}
    attended = is_attended_only(url)
    # An ATTENDED_ONLY host's robots.txt asks automated tools not to read the
    # job path, so reading one at all means overriding it. That is the
    # person's own decision, recorded at ATTENDED_ONLY_HOSTS above, and it is
    # confined to this one hand-add path - every other HTML fetch in this
    # package still honours robots.
    #
    # No try/except around this: fetch.fetch catches HTTPError, OSError and
    # ValueError itself and reports failure as status 0, which the next line
    # already handles. Wrapping it would be catching a case that cannot
    # happen, and every other collector here calls the fetcher bare.
    # `url_ok` carries the NEVER_FETCH rule into the fetcher so it is applied
    # to every redirect hop as well as to this URL. Checking it only here was
    # defeated by any 302: a shortened link, or a board that redirects, and
    # this package fetched an aggregator it promises never to touch - a legal
    # Rule, broken silently (found by a red-team review).
    def still_allowed(candidate: str) -> bool:
        return may_fetch(candidate, hand_added=hand_added)

    stat, html, _final = (
        fetcher(url, timeout=25, respect_robots=False, url_ok=still_allowed)
        if attended
        else fetcher(url, timeout=25, url_ok=still_allowed))
    if stat != 200 or not html:
        return {}
    if attended:
        return read_linkedin(html)
    nodes = parse_jsonld_jobs(html)
    if not nodes:
        return {}
    node = nodes[0]
    return {
        "title": str(node.get("title") or ""),
        "employer": str(node.get("employer") or ""),
        "location": str(node.get("location") or ""),
        "description": str(node.get("description") or ""),
        "posted": str(node.get("posted") or ""),
        "employment_type": str(node.get("employment_type") or ""),
    }


def add(con: sqlite3.Connection, cfg: dict[str, Any], url: str, *,
        title: str = "", company: str = "", description: str = "",
        location: str = "", posted: str = "", apply_url: str = "",
        resume_text: str = "", no_fetch: bool = False,
        fetcher: Callable[..., tuple[int, str, str]] = default_fetch,
        ) -> dict[str, Any]:
    """Record one job from a link. Returns what was stored, plus `fetched`
    so a caller can tell the person whether anything was filled in for them.

    What the PERSON typed always wins over what the page said. They are
    looking at the posting; we are guessing from markup.
    """
    url = url.strip()
    if not url:
        msg = "a link is required"
        raise ValueError(msg)
    # Refused rather than blanked, unlike the collectors: the person typed
    # this, so they get told. stable_id alone did not catch it - for
    # "file:///C:/Windows/System32/calc.exe" the hostname is empty but the
    # path survives, producing a perfectly good id for a link that must never
    # Have become clickable (found by a red-team review).
    if not links_mod.is_safe(url):
        msg = (f"only http and https links can be added, got {url!r}. "
               "Copy the address out of your browser's address bar.")
        raise ValueError(msg)
    key_id = stable_id(url)
    if not key_id:
        msg = f"that does not look like a link: {url!r}"
        raise ValueError(msg)

    # `hand_added=True` unconditionally: it states a FACT about this call site
    # - a person is adding this link right now - not a preference. The
    # preference is `read_added_links`, and it decides whether the page is read
    # at all. Keeping those two separate is what stops the setting's name from
    # lying: it says "read added links", so turning it off has to stop the
    # reading, not just narrow which sites it applies to.
    #
    # `no_fetch` is the CALLER saying it already has the data, which is a
    # different statement from the person's preference and overrides it in the
    # safe direction only - it can suppress a request, never cause one. A
    # collector that has already read the posting must not make this app read it
    # again: for a host in ATTENDED_ONLY_HOSTS that would be a SECOND automated
    # reader of a site that asked for none, which is the rule an earlier change asked
    # to be enforced rather than observed (the collector author, 2026-08-08).
    wants_page = bool((cfg.get("fetch") or {}).get("read_added_links", True))
    if wants_page and not no_fetch:
        page = read_posting(url, fetcher=fetcher, hand_added=True)
    else:
        page = {}
    title = title.strip() or page.get("title", "")
    company = company.strip() or page.get("employer", "")
    description = description.strip() or page.get("description", "")
    location = location.strip() or page.get("location", "")
    posted = posted.strip() or page.get("posted", "")

    if not title:
        msg = ("no title found on the page, so one has to be given: "
               'add --title "Support Analyst"')
        raise ValueError(msg)

    company_id = db.upsert_company(
        con, company or "Added by hand", probe_status="added by hand",
        origin=db.MANUAL)

    posting = type("Posting", (), {
        "title": title, "location": location, "description": description,
        "employment_type": page.get("employment_type", ""),
    })()
    fields = screen.screen_job(posting, cfg, resume_text)

    now = status_mod.now_iso()
    key = f"{SOURCE_NAME}:{key_id}"
    fields.update({
        "company_id": company_id,
        "source": SOURCE_NAME,
        "title": title,
        "location": location,
        "url": url,
        "posted_at": posted,
        # Stored NORMALISED, so the cross-board join is a plain equality test at
        # query time rather than a function every comparison has to remember to
        # call. Empty when there is none - Easy Apply is a real answer, not a
        # gap: the application stays on the board and there is no ATS row it
        # could collide with.
        "apply_url": links_mod.normalise_apply_url(apply_url),
        "fetched_at": now,
        "last_seen": now,
        "description": description,
        # Adding a job by hand is not a screening decision. Somebody pasting
        # a link is telling us they are interested, so it is never dropped
        # for failing the title filter or the salary floor - the reasons are
        # still recorded and still shown, but the row stays.
        "qualified": 1,
        "verdict": "keep" if fields.get("verdict") == "keep" else "alt",
    })
    db.upsert_job(con, key, fields)
    db.relist(con, key)
    return {
        "key": key,
        "url": url,
        "title": title,
        "company": company,
        "apply_url": fields["apply_url"],
        "fetched": bool(page),
        "has_description": bool(description),
        "verdict": fields["verdict"],
        "coverage_pct": fields.get("coverage_pct"),
    }


# ---------------------------------------------------------------- recheck ---
#
# the first user's design, 2026-08-06: the scheduled refresh keeps handling everything
# the app ships with - the employer boards and the search sources, all of
# which publish access deliberately. Hand-added links are NOT swept on a
# timer. They are re-read when the person presses Refresh, and until they do,
# the app SAYS so next to the last-refreshed line rather than leaving them
# quietly stale.
#
# That is what keeps a link the site asked us not to crawl on the right side
# of the distinction: every request for one has a person present, looking at
# the list it belongs to.

# How many are read in one press.
#
# Was 25, chosen in 2026-08-06 when this path had NO PACING of its own and the
# count was the only brake there was. It is not the only brake now: fetch.py
# jitters the gap between requests, rests 8 seconds every 25 to one host, and
# STOPS asking a host entirely after three throttled responses, honouring
# Retry-After. The protection moved to where it belongs - rhythm and backoff -
# so the count no longer has to stand in for it (decided 2026-08-09).
#
# 50 costs about 1.8 minutes of wall clock, in a subprocess that does not block
# the window. Measured, not guessed: 1.5s per-host delay plus up to 50% jitter
# averages 1.88s, and two rests fall inside a run that size.
#
# This is a per-PRESS ceiling, not a daily one. Rows read in a run have their
# last_seen updated and drop out of "due", so pressing again picks up the NEXT
# oldest batch - somebody working through a backlog is not capped at 50 a day.
# The once-per-20-hours rule is what stops the same job being read twice.
RECHECK_MAX_PER_RUN = 50

# Once a day per job. Pressing Refresh five times in an afternoon re-reads
# the boards; it does not re-read the same LinkedIn page five times.
RECHECK_MIN_HOURS = 20

# Statuses that mean the posting is GONE, as opposed to "we could not read
# it". Everything else - a timeout, a 429, a 500, a redirect to a wall -
# leaves the row exactly as it was.
GONE_STATUSES = frozenset({404, 410})

GONE_TEXT = re.compile(
    r"no longer accepting applications|"
    r"this job is no longer available|"
    r"no longer available|"
    r"position (?:has been )?filled|"
    r"posting (?:has )?(?:been )?(?:closed|expired|removed)",
    re.IGNORECASE)


def _looks_gone(status: int, html: str) -> bool:
    if status in GONE_STATUSES:
        return True
    if status != 200 or not html:
        # NOT gone: unknown. A network blip quietly marking a dozen live
        # jobs as dead is the same silent-failure class as a board that
        # reads as zero postings, and it costs a person real applications.
        return False
    # Only the top of the page. The phrase appears in "similar jobs" blocks
    # further down, which are about OTHER postings entirely.
    return bool(GONE_TEXT.search(html[:20000]))


def _hours_since(stamp: str | None, now: datetime) -> float:
    """Hours since an ISO stamp. A missing or unreadable one reads as
    "never checked", which makes it due rather than skipped.
    """
    if not stamp:
        return float("inf")
    try:
        when = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except ValueError:
        return float("inf")
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return (now - when).total_seconds() / 3600.0


def recheckable_sources(cfg: dict[str, Any]) -> tuple[str, ...]:
    """Which `jobs.source` values the refresh button covers, for this profile.

    Hand-added rows always. A collector's rows only when ITS entry says
    `we_may_refetch` - off unless somebody writes it down, because a collector
    that already read the posting asking us to read it again is a second
    automated reader for information this app has already been handed.

    WHAT THIS DOES NOT DO. Being in scope means the row is CONSIDERED. Every
    row is still put to `may_fetch` one at a time, and only hand-added rows
    carry `hand_added=True` - so a collector cannot reach the attended-only
    exception no matter what its file claims, and nothing reaches the blocked
    aggregators at all. See `recheck`, and test_collector_refetch_scope.
    """
    extra = collectors_mod.Refetch.from_config(cfg).allowed - {SOURCE_NAME}
    return (*ALWAYS_RECHECKABLE, *sorted(extra))


def due_rows(con: sqlite3.Connection, now: datetime | None = None,
             *, limit: int = RECHECK_MAX_PER_RUN,
             sources: Sequence[str] = ALWAYS_RECHECKABLE) -> list[sqlite3.Row]:
    """Jobs worth re-reading: the UNTOUCHED, still-listed ones.

    "Is this still open" is only worth a request when the answer changes what
    the person does - and it only does for a job they have not decided about
    yet. Two exclusions, both the first user's, 2026-08-09:

    ALREADY MARKED CLOSED. Re-reading these was the old behaviour and it is
    backwards: the app had already recorded the posting was gone, and it went
    back to ask again anyway, every day, forever. Worse, it could UNDO the
    record - a 200 from a sign-in wall or a rebuilt page took the row out of
    delisted and presented a dead posting as live again.

    ANYTHING WITH A STATUS. Applied, passed, denied, interviewed, offer, hired -
    if they have touched it, the posting's liveness no longer drives an action.
    The previous version did the opposite and put applied-to jobs FIRST, on the
    reasoning that a closure matters while you wait to hear back. It does not
    matter enough to spend a request on: it does not tell you whether you were
    rejected, and for these rows another collector already detects closures and
    pushes them in.

    Interviewed, offer and hired are excluded for consistency rather than by
    instruction - "touched" is the rule, and a job you have interviewed for is
    more decided than one you merely applied to, not less.
    """
    now = now or datetime.now(UTC)
    names = tuple(dict.fromkeys(sources))
    if not names:
        return []
    # A FIXED QUERY, even though the list is now variable. The obvious way to
    # take N sources is to build "IN (?, ?, ?)" - and that is a query assembled
    # from a string, which reads identically to an injection whether or not the
    # interpolated part can only ever be punctuation. json_each turns the list
    # into ONE bound parameter, so this string is a constant like every other
    # query in the file, and there is nothing for a reader or a linter to
    # decide about.
    rows = con.execute(
        "SELECT j.key, j.url, j.source, j.last_seen, j.delisted_at, "
        "       (SELECT s.status FROM job_status s WHERE s.key = j.key) AS status "
        "FROM jobs j "
        "WHERE j.source IN (SELECT value FROM json_each(?)) "
        "  AND j.delisted_at IS NULL "
        "ORDER BY j.last_seen IS NULL DESC, j.last_seen",
        (json.dumps(names),)).fetchall()
    untouched = [r for r in rows if not (r["status"] or "").strip()]
    return [r for r in untouched
            if _hours_since(r["last_seen"], now) >= RECHECK_MIN_HOURS][:limit]


def recheck(con: sqlite3.Connection, cfg: dict[str, Any], *,
            fetcher: Callable[..., tuple[int, str, str]] = default_fetch,
            now: datetime | None = None,
            limit: int = RECHECK_MAX_PER_RUN) -> dict[str, Any]:
    """Re-read hand-added links. Returns what changed.

    Only ever called with a person present - see the note above recheck's
    constants. Never from a collect, never from the scheduled refresh.
    """
    now = now or datetime.now(UTC)
    # The same setting as `add`, for the same reason: this is the other half of
    # the hand-driven path, and somebody who turned the reading off did not
    # mean "except when I press the button".
    if not bool((cfg.get("fetch") or {}).get("read_added_links", True)):
        return {"checked": 0, "gone": [], "unreadable": []}
    stamp = status_mod.now_iso()
    gone, unreadable = [], []

    for row in due_rows(con, now, limit=limit,
                        sources=recheckable_sources(cfg)):
        url = row["url"] or ""
        # PER ROW, NEVER PER RUN. This used to be a flat `hand_added=True`,
        # which was true only because the population was hand-added rows and
        # nothing else. Now a collector can put its rows in that population,
        # and `hand_added` is the ONE exception that reads a host whose
        # robots.txt says not to - so it has to be a fact about the row rather
        # than a property of the button that started the run.
        by_hand = (row["source"] or "") == SOURCE_NAME
        if not may_fetch(url, hand_added=by_hand):
            continue
        # Bare, for the same reason as read_posting: the fetcher reports
        # failure as status 0 rather than raising, and status 0 lands in the
        # unreadable branch below where it belongs.
        status, html, _final = fetcher(
            url, timeout=25,
            respect_robots=not (by_hand and is_attended_only(url)),
            url_ok=lambda candidate, hand=by_hand: may_fetch(
                candidate, hand_added=hand))

        if _looks_gone(status, html):
            con.execute("UPDATE jobs SET delisted_at = ? WHERE key = ?",
                        (stamp, row["key"]))
            gone.append(row["key"])
        elif status == 200 and html:
            # Still listed, nothing to record. There is no un-delisting branch
            # here any more: due_rows never returns a row already marked
            # closed, so the "postings reappear" case it used to handle cannot
            # be reached - and a 200 from a sign-in wall taking a dead posting
            # OUT of delisted was the failure mode that made it worse than
            # useless. A posting that genuinely comes back is re-added by hand,
            # which is how it got here in the first place.
            pass
        else:
            unreadable.append(row["key"])
            continue
        # Only a row we actually READ gets its clock reset. One that could
        # not be reached stays due, so a site that is down for an hour is
        # retried rather than quietly parked for a day.
        con.execute("UPDATE jobs SET last_seen = ? WHERE key = ?",
                    (stamp, row["key"]))
    con.commit()
    # A posting this path closed was confirmed gone by reading the site, so it
    # takes the same status rule as every other way a closure is noticed.
    db_mod.close_untouched_delisted(con, list(gone), at=stamp)
    return {"checked": len(gone), "gone": gone,
            "unreadable": unreadable}


def recheck_status(con: sqlite3.Connection,
                   now: datetime | None = None,
                   *, sources: Sequence[str] = ALWAYS_RECHECKABLE,
                   ) -> dict[str, Any]:
    """What the app shows next to the last-refreshed line, without fetching
    anything: how many links are in scope, how many are due, and when the next
    check becomes available.

    `sources` has to be the same list `due_rows` will be given, or the number
    beside the button describes a different population from the one the button
    reads. cli.cmd_recheck passes `recheckable_sources(cfg)` to both.
    """
    now = now or datetime.now(UTC)
    names = tuple(dict.fromkeys(sources))
    if not names:
        return {"total": 0, "due": 0, "hours_until_due": 0.0}
    # The SAME population due_rows will actually read: still listed, and not
    # touched. Counting a wider set would promise checks that never happen -
    # "291 added links not re-checked" beside a button that then reads eleven
    # of them is a number a person learns to disbelieve.
    rows = con.execute(
        "SELECT j.last_seen FROM jobs j "
        "WHERE j.source IN (SELECT value FROM json_each(?)) "
        "  AND j.delisted_at IS NULL "
        "  AND NOT EXISTS (SELECT 1 FROM job_status s "
        "                  WHERE s.key = j.key AND TRIM(s.status) != '')",
        (json.dumps(names),)).fetchall()
    if not rows:
        return {"total": 0, "due": 0, "hours_until_due": 0.0}
    ages = [_hours_since(r["last_seen"], now) for r in rows]
    due = sum(1 for a in ages if a >= RECHECK_MIN_HOURS)
    soonest = min((RECHECK_MIN_HOURS - a for a in ages if a < RECHECK_MIN_HOURS),
                  default=0.0)
    return {"total": len(rows), "due": due,
            "hours_until_due": max(0.0, round(soonest, 1))}
