"""links.py - what counts as a link this app will store, follow or show.

ONE definition, because a URL crosses three trust boundaries in this app and
they were previously guarded by one of them only:

  1. do we REQUEST it          - fetch.py
  2. do we STORE it            - db.upsert_job / upsert_company
  3. do we hand it to the OS   - the desktop's hyperlinks

Only (1) was ever checked. A job posting's `url` came out of remote JSON-LD,
was stored verbatim, and became a clickable link that eframe passes to the
Windows shell. A posting advertising `file://198.51.100.5/share/apply` would
have collected and screened like any other job, and one click on the title -
the app's most common action - hands the person's NTLM credentials to whoever
published it. Found by a red-team review, 2026-08-08.

So the rule lives here and all three boundaries import it. Anything that is
not plain http/https is not a link as far as this app is concerned.

MEASURED BEFORE ENFORCING, on 2026-08-08: 10,387 stored job URLs and 219
careers URLs across 216 distinct hosts, every one of them `https` and every
one resolving to a public address. Not a single row in
existing data is affected by this rule - it only closes the hole.
"""
from __future__ import annotations

import ipaddress
import socket
import urllib.parse

ALLOWED_SCHEMES = ("http", "https")


def host_of(url: str) -> str:
    """The hostname, with any userinfo and port already removed.

    `urlsplit().hostname` rather than `.netloc`, which is what makes
    `https://boards.greenhouse.io@evil.com/x` read as evil.com instead of as
    greenhouse.
    """
    try:
        return (urllib.parse.urlsplit(url).hostname or "").lower()
    except ValueError:
        # An unparseable authority - a bare "[" , a bad IPv6 literal. Not a
        # host, so not a link.
        return ""


def is_safe(url: str) -> bool:
    """Whether this is a link we will store, follow or open."""
    if not url or not url.strip():
        return False
    try:
        parts = urllib.parse.urlsplit(url.strip())
    except ValueError:
        return False
    return parts.scheme in ALLOWED_SCHEMES and bool(parts.hostname)


def safe_or_empty(url: str | None) -> str:
    """A link, or "" - for the store boundary.

    Empty rather than raising: a posting whose URL we refuse is still a real
    job worth keeping in the list. It loses its link, not its row.
    """
    text = (url or "").strip()
    return text if is_safe(text) else ""


def host_matches(host: str, domains: tuple[str, ...]) -> bool:
    """Subdomains count: jobs.example.com matches example.com."""
    return any(host == d or host.endswith("." + d) for d in domains)


def is_private_destination(url: str) -> bool:
    """Does this URL resolve to somewhere on the local machine or network?

    A red-team review found that the sitemap collector follows URLs it reads out of a
    remote robots.txt and remote sitemap XML, so a hostile careers site can
    choose what this app requests. Without this check that includes
    `http://192.168.1.1/jobs/reboot` - a GET issued from inside the person's
    own network, at the choosing of a stranger.

    Resolves the name rather than only rejecting literal addresses: a hostile
    host is far more likely to be a name pointing at 10.x than a bare IP.

    A name that will not resolve returns False - it is not evidence of a
    private address, and the request will fail on its own a moment later.

    HONEST LIMIT: this resolves, then urllib resolves again when it connects.
    A DNS entry that changes between the two (rebinding) defeats it. Closing
    that needs the connection itself to be pinned to the address we checked,
    which urllib does not expose. This raises the cost a long way and does not
    claim to be complete.
    """
    host = host_of(url)
    if not host:
        return False
    try:
        addrs = {info[4][0] for info in socket.getaddrinfo(host, None)}
    except (OSError, UnicodeError, ValueError):
        return False
    for text in addrs:
        try:
            ip = ipaddress.ip_address(text)
        except ValueError:
            continue
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
            return True
    return False


# Query parameters that identify WHERE A CLICK CAME FROM rather than WHICH JOB
# it leads to. Programmatic job advertising appends these for billing and
# attribution, so the same application page arrives with different ones from
# every source that referred it.
#
# Matched as exact names plus the `utm_` family. Deliberately a NAMED LIST and
# not a heuristic: a guess here silently merges two real jobs.
#
# `position`, `pagenum` and `jobsearchindex` ARE A SET, and they mean WHERE IN
# A RESULT LIST the person clicked - not which job. Said out loud because
# `position` on its own reads like the job itself, and removing it on that
# reading would put the click's rank back into the dedupe key, which is
# precisely the direction this list exists to prevent: two clicks on one job
# from different places in one result page would stop matching.
TRACKING_PARAMS = frozenset({
    "gh_src", "gh_jid_src", "lever-source", "lever-origin",
    "src", "source", "ref", "referer", "referrer", "refid",
    "trk", "trackingid", "trk_ref", "originalsubdomain",
    "li_fat_id", "recommendedflavor", "position", "pagenum",
    "jobposition", "jobsearchindex", "applyurl",
    "campaign", "medium", "content", "term",
    "fbclid", "gclid", "msclkid", "mc_cid", "mc_eid",
    "sessionid", "session_id", "sid",
})


# Boards that wrap an outbound apply link in their own interstitial rather than
# linking straight out. The real destination sits percent-encoded in a query
# parameter, so the href's own host says nothing about where the application
# lives - the wrapper stays on the wrapping site no matter which ATS it leads
# to.
#
# Reported 2026-08-08 from a career-url backfill, where a naive off-site test
# rejected every wrapped row for exactly this reason:
#   <wrapper-host>/safety/go/?url=https%3A%2F%2Fapply%2Eworkable%2Ecom%2F...
# Note the dots are encoded too (%2E), so the target has to be unquoted before
# anything reads its host.
REDIRECT_WRAPPERS = {
    "linkedin.com": ("/safety/go",),
}
REDIRECT_PARAMS = ("url", "target", "redirect", "dest", "u")
# One wrapper around another is real; a chain longer than this is a loop or an
# attempt to bury the destination, and neither deserves the benefit of the doubt.
MAX_UNWRAPS = 3


def unwrap_redirect(url: str) -> str:
    """Follow an interstitial to the destination it names, without fetching it.

    Reads the target out of the query string only. Nothing is requested, so this
    is safe to run over stored rows in bulk and costs nothing.

    Scoped to a NAMED list of wrappers rather than "any link with a url
    parameter", because plenty of genuine application links carry one of their
    own and unwrapping those would throw away the actual destination.
    """
    for _ in range(MAX_UNWRAPS):
        if not is_safe(url):
            return url
        parts = urllib.parse.urlsplit(url)
        host = (parts.hostname or "").lower()
        paths = next(
            (p for domain, p in REDIRECT_WRAPPERS.items() if host_matches(host, (domain,))),
            None,
        )
        if not paths or not parts.path.rstrip("/").lower().endswith(
            tuple(p.rstrip("/") for p in paths)
        ):
            return url
        params = dict(urllib.parse.parse_qsl(parts.query, keep_blank_values=False))
        target = next(
            (params[name] for name in REDIRECT_PARAMS
             if name in params and is_safe(urllib.parse.unquote(params[name]))),
            None,
        )
        if target is None:
            return url
        url = urllib.parse.unquote(target)
    return url


def normalise_apply_url(url: str | None) -> str:
    """A form of an apply link that two sources can be compared on.

    Case and trailing slashes are made consistent, tracking parameters are
    dropped, and what survives is sorted so parameter ORDER cannot make one job
    look like two.

    THE QUERY STRING IS NOT DROPPED WHOLESALE, and that is the important
    decision here. Plenty of application links carry the job's identity IN the
    query - `?gh_jid=4012345`, `?jobId=88213` - so stripping everything would
    collapse every opening at one employer into a single key. This module's
    standing rule is that over-firing is worse than under-firing: a missed
    duplicate costs one wasted read, a false merge HIDES A JOB somebody wanted
    and they never learn it existed.

    Returns "" for anything that is not a usable http/https link, so an absent
    or unusable destination never matches another absent one.
    """
    if not url:
        return ""
    text = url.strip()
    if not is_safe(text):
        return ""
    # Before anything reads the host: a wrapped link's own host is the board
    # that wrapped it, not where the application lives.
    text = unwrap_redirect(text)
    if not is_safe(text):
        return ""
    parts = urllib.parse.urlsplit(text)
    host = (parts.hostname or "").lower()
    if not host:
        return ""
    # Default ports carry no meaning; a non-default one does.
    port = ""
    if parts.port and parts.port not in (80, 443):
        port = f":{parts.port}"
    path = parts.path.rstrip("/") or "/"

    kept = [
        (name, value)
        for name, value in urllib.parse.parse_qsl(parts.query, keep_blank_values=False)
        if name.lower() not in TRACKING_PARAMS and not name.lower().startswith("utm_")
    ]
    query = urllib.parse.urlencode(sorted(kept))
    return urllib.parse.urlunsplit(("https", f"{host}{port}", path, query, ""))


def same_site(url: str, host: str) -> bool:
    """Is `url` on the same site as `host`?

    Used to keep the sitemap walk inside the portal it was pointed at. Compares
    the last two labels, which is wrong for multi-part public suffixes
    (`example.co.uk` vs `other.co.uk` both reduce to `co.uk`) - deliberately,
    because the alternative is shipping and maintaining a public-suffix list
    for a check whose job is to stop a sitemap sending us to an unrelated
    machine, not to make a security decision about cousin domains. The
    private-address check above is what carries that weight.
    """
    def registrable(name: str) -> str:
        labels = name.lower().strip(".").split(".")
        return ".".join(labels[-2:]) if len(labels) >= 2 else name.lower()

    target = host_of(url)
    return bool(target) and registrable(target) == registrable(host)
