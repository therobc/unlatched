"""fetch.py - The ONE http choke point.

Every parser in this package - ATS collectors, careers-page discovery,
sitemap enumeration - reads its input through `fetch()`. Concentrating
network access in a single function is what makes three separate defects
fixable in one place instead of three:

  * an unbounded response can stall a whole run. `MAX_FETCH_BYTES` caps every
    read, here, once (this is the fix for the fetch-size defect: a single
    pathological page used to be able to consume unbounded memory and time).
  * a scraper that ignores robots.txt is a scraper that gets blocked, and
    deserves to be. `respect_robots` is on by default.
  * hammering one host is both rude and a fast way to get rate-limited.
    `per_host_delay_s` enforces a minimum gap between requests to the same
    host, tracked here so every caller gets it for free.

Every function that talks to the network takes an optional `fetcher`
parameter defaulting to `fetch`. Tests pass a fake one, so nothing in the
test suite ever touches a live socket.
"""
from __future__ import annotations

import gzip
import io
import random
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from typing import TYPE_CHECKING

from . import __version__
from .links import ALLOWED_SCHEMES, is_private_destination

if TYPE_CHECKING:
    from collections.abc import Callable

USER_AGENT = f"unlatched/{__version__}"

# One cap, shared by every parser that reads a fetched body. Without it, a
# single oversize page can stall an entire collection run.
MAX_FETCH_BYTES = 2_000_000
DEFAULT_TIMEOUT_S = 20.0
DEFAULT_PER_HOST_DELAY_S = 1.5

# How far above the delay a request may be pushed. Up to half again, never
# below - jitter that could shorten the gap would be a way of asking faster.
JITTER_FRACTION = 0.5
# A real pause every N requests to one host, so a long run is not continuous
# pressure. Sized against a normal collect rather than freehand: DETAIL_CAP is
# 60 detail fetches per run, so this lands two or three rests in a busy one.
REST_EVERY = 25
REST_SECONDS = 8.0
# Consecutive push-backs from one host before this run stops asking it at all.
# Two could be a coincidence of load; three is an answer.
THROTTLE_LIMIT = 3
# However long a host asks us to wait, we will not park a whole run on it.
MAX_RETRY_AFTER_S = 120.0
# Statuses that mean "you are asking too often", as opposed to a plain failure.
THROTTLE_STATUSES = frozenset({429, 503})

# Seeded on demand by reset_rate_limits() so a test can pin the jitter. Real
# runs get the default seeding, which is what makes the pattern unpredictable.
_rng = random.Random()  # noqa: S311 - request spacing, not a security decision
_host_requests: dict[str, int] = {}
_host_throttles: dict[str, int] = {}
_stopped_hosts: dict[str, str] = {}

# `timeout` is urllib's PER-SOCKET-OPERATION timeout, not a deadline. A server
# that dribbles one byte every nineteen seconds satisfies it forever, and a
# single hostile host could hold a whole collection run open until the 2MB cap
# was reached. The body is therefore read in chunks against a wall clock.
#
# Three times the socket timeout, because a legitimate slow page on a bad
# connection should still finish: the point is to bound the pathological case,
# not to fail the merely sluggish one.
DEADLINE_MULTIPLIER = 3.0
READ_CHUNK_BYTES = 65_536

_last_fetch_at: dict[str, float] = {}
_robots_cache: dict[str, urllib.robotparser.RobotFileParser | None] = {}


def _host_of(url: str) -> str:
    """The hostname, for rate limiting and robots.

    `.hostname`, NOT `.netloc`: netloc carries userinfo and port, so
    `http://a@example.com/` and `http://b@example.com/` keyed as two different
    hosts and each got its own rate-limit budget against one server. It also
    put credentials into the robots.txt URL built below.
    """
    try:
        return (urllib.parse.urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


def _scheme_allowed(url: str) -> bool:
    try:
        return urllib.parse.urlsplit(url).scheme in ALLOWED_SCHEMES
    except ValueError:
        return False


class _GuardedRedirect(urllib.request.HTTPRedirectHandler):
    """Re-apply the caller's policy on EVERY hop, not just the first.

    A red-team review found that every URL rule in this package was enforced against
    the URL handed in, and urllib then followed redirects wherever they led.
    Two things escaped through that gap:

      * NEVER_FETCH (Indeed, Glassdoor, Jobs4TN, FlexJobs) - a rule that
        exists for legal reasons, not performance - was defeated by any
        redirect. A shortened link, or a board that 302s, and this package
        fetched a site it promises never to touch.
      * ALLOWED_SCHEMES was defeated for `ftp:`, which CPython's redirect
        handler permits alongside http and https, and which the default opener
        has a handler for.

    Returning None means "do not follow": urlopen then hands back the 3xx
    response itself, which reads downstream as a page with no content rather
    than as an error. That is the right outcome - we did not fail, we declined.
    """

    def __init__(self, allowed: Callable[[str], bool]) -> None:
        super().__init__()
        self._allowed = allowed

    def redirect_request(
        self, req: urllib.request.Request, fp: object, code: int, msg: str,
        headers: object, newurl: str,
    ) -> urllib.request.Request | None:
        if not self._allowed(newurl):
            return None
        return super().redirect_request(req, fp, code, msg, headers, newurl)  # type: ignore[arg-type]


def _gunzip(raw: bytes, max_bytes: int) -> bytes:
    """Decompress a gzipped body, CAPPED.

    Sitemaps are very commonly served as .gz. This used to be attempted in
    sitemap.py against the already-DECODED text, where it could never work:
    0x8b is not valid UTF-8, so the magic bytes had been replaced with U+FFFD
    long before the check ran, and every gzipped sitemap silently yielded
    nothing (red-team finding M5).

    The cap is not optional. Decompressing without one turns a 2MB response
    into however many gigabytes the sender chose - the classic decompression
    bomb, and a hole this would otherwise have opened while closing another.
    """
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(raw)) as gz:
            return gz.read(max_bytes + 1)[:max_bytes]
    except (OSError, EOFError, ValueError):
        # Not actually gzip, or truncated mid-stream. The raw bytes are what
        # we have; the parsers will make of them what they can.
        return raw


def _respect_rate_limit(host: str, delay_s: float) -> None:
    """Wait our turn on this host before asking it for anything.

    Three things beyond the flat delay, all of them about RHYTHM rather than
    volume - the caps elsewhere in this module already bound how much we ask
    for, and said nothing about how we ask for it.
    """
    if delay_s <= 0 or not host:
        return
    last = _last_fetch_at.get(host)
    now = time.monotonic()
    if last is not None:
        # JITTER. A fixed delay is a metronome: it holds a steady floor of load
        # rather than yielding, and it is the easiest possible pattern for a
        # rate limiter to classify as automated. Randomising around the delay
        # is both more considerate and less conspicuous, at no cost to us.
        wait = delay_s * _rng.uniform(1.0, 1.0 + JITTER_FRACTION) - (now - last)
        if wait > 0:
            time.sleep(wait)

    # A LONGER REST every so often. Without this a long run holds continuous
    # pressure on one host for its whole duration, which is the part a small
    # employer's careers page actually feels - the per-request delay never adds
    # up to a pause.
    count = _host_requests.get(host, 0) + 1
    _host_requests[host] = count
    if count % REST_EVERY == 0:
        time.sleep(REST_SECONDS)

    _last_fetch_at[host] = time.monotonic()


def _note_throttled(host: str, status: int, retry_after: str | None) -> None:
    """Record that a host pushed back, and stop asking if it keeps doing so.

    Before this, a 429 was simply a failed fetch and the loop moved on to the
    next URL ON THE SAME HOST. That is knocking harder after being asked to
    stop, and no amount of politeness elsewhere makes up for it.
    """
    if not host:
        return
    hits = _host_throttles.get(host, 0) + 1
    _host_throttles[host] = hits

    # Retry-After is the host stating its own terms. Honouring it is both the
    # courteous reading and the one that gets us collected data, since every
    # rate limiter is built expecting it.
    pause = _retry_after_seconds(retry_after)
    if pause is not None:
        time.sleep(min(pause, MAX_RETRY_AFTER_S))

    if hits >= THROTTLE_LIMIT:
        _stopped_hosts[host] = (
            f"stopped after {hits} throttled responses (last was {status})")


def _retry_after_seconds(value: str | None) -> float | None:
    """Seconds from a Retry-After header, or None if it says nothing usable.

    Only the delta-seconds form is honoured. The HTTP-date form is legal and
    rare, and guessing at a date parse to sleep on is worse than falling back
    to our own backoff.
    """
    if not value:
        return None
    try:
        seconds = float(value.strip())
    except (TypeError, ValueError):
        return None
    return seconds if seconds >= 0 else None


def _robots_allows(url: str, timeout: float) -> bool:
    host = _host_of(url)
    if not host:
        return True
    parts = urllib.parse.urlsplit(url)
    # Host AND port, rebuilt from the parsed pieces rather than reusing
    # netloc: netloc would carry any userinfo straight into the robots.txt
    # URL, which both leaks credentials and asks the wrong server. The port
    # still belongs here - robots.txt for :8443 is its own document - even
    # though the cache and rate limiter key on the bare host.
    authority = f"{host}:{parts.port}" if parts.port else host
    rp = _robots_cache.get(host)
    if rp is None:
        rp = urllib.robotparser.RobotFileParser()
        robots_url = f"{parts.scheme}://{authority}/robots.txt"
        rp.set_url(robots_url)
        try:
            # robots_url inherits its scheme from `url`, which fetch() has
            # already restricted to http/https before calling here.
            req = urllib.request.Request(  # noqa: S310
                robots_url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310
                raw = r.read(MAX_FETCH_BYTES).decode("utf-8", "replace")
            rp.parse(raw.splitlines())
        except (OSError, ValueError):
            # No reachable robots.txt is not a block - most career sites do
            # not publish one at all. OSError covers DNS/connection/timeout
            # failures (URLError, HTTPError and TimeoutError all subclass
            # it); ValueError covers a malformed robots_url.
            rp = None
        _robots_cache[host] = rp
    if rp is None:
        return True
    return rp.can_fetch(USER_AGENT, url)


def _read_capped(response: object, max_bytes: int, deadline: float) -> bytes:
    """The body, bounded by BOTH size and wall clock.

    Chunked rather than one read(max_bytes + 1) so the deadline is checked
    while the transfer is in progress. One read of the whole body can only be
    interrupted by the socket timeout, which a slow drip never trips.
    """
    parts: list[bytes] = []
    total = 0
    while total <= max_bytes:
        if time.monotonic() > deadline:
            break
        chunk = response.read(min(READ_CHUNK_BYTES, max_bytes + 1 - total))  # type: ignore[attr-defined]
        if not chunk:
            break
        parts.append(chunk)
        total += len(chunk)
    return b"".join(parts)[:max_bytes]


def fetch(url: str, *, timeout: float = DEFAULT_TIMEOUT_S,
          data: bytes | None = None, content_type: str = "",
          max_bytes: int = MAX_FETCH_BYTES,
          per_host_delay_s: float = DEFAULT_PER_HOST_DELAY_S,
          respect_robots: bool = True,
          allow_private: bool = False,
          url_ok: Callable[[str], bool] | None = None,
          headers: dict[str, str] | None = None) -> tuple[int, str, str]:
    """GET (or POST when `data` is given). Returns (status, text, final_url).

    status 0 means the request never completed (DNS failure, timeout,
    connection refused, or a destination this function declined to open).
    The body is always truncated at `max_bytes`, and bounded by a wall-clock
    deadline as well as the per-socket `timeout`.

    `url_ok` is the CALLER's own policy - manual.py passes the rule that keeps
    this package off the aggregators. It is applied here, and again on every
    redirect hop, which is the whole point of it living in this signature
    rather than at the call site.

    `allow_private` opens up loopback and LAN addresses. Off by default and
    off everywhere in this package: measured across all five profiles on
    2026-08-08, not one of 216 collected hosts resolved to a private address,
    so refusing them costs nothing and closes the SSRF the sitemap walker
    would otherwise hand to any careers site that asked.

    `headers` overrides/extends the defaults below - it exists for the one
    API that needs it (USAJOBS requires `Authorization-Key`, `Host`, and a
    `User-Agent` set to the caller's registered email rather than this
    package's own UA string). Every other caller leaves it unset.
    """
    def allowed(candidate: str) -> bool:
        if not _scheme_allowed(candidate):
            return False
        if url_ok is not None and not url_ok(candidate):
            return False
        return allow_private or not is_private_destination(candidate)

    if not allowed(url):
        return 0, "", url
    host = _host_of(url)
    # A host that has already pushed back repeatedly is not asked again for the
    # rest of this run - checked BEFORE robots, because the cheapest request is
    # the one never made.
    if host in _stopped_hosts:
        return 0, "", url
    if respect_robots and not data and not _robots_allows(url, timeout):
        return 0, "", url
    _respect_rate_limit(host, per_host_delay_s)

    req = urllib.request.Request(url, data=data)  # noqa: S310 - scheme checked above
    req.add_header("User-Agent", USER_AGENT)
    req.add_header("Accept", "application/json, text/html;q=0.9, */*;q=0.5")
    if content_type:
        req.add_header("Content-Type", content_type)
    for key, value in (headers or {}).items():
        req.add_header(key, value)

    # A per-call opener, not the module-level default one: `allowed` closes
    # over this call's url_ok and allow_private, so the redirect guard cannot
    # be shared between callers with different policies.
    opener = urllib.request.build_opener(_GuardedRedirect(allowed))
    deadline = time.monotonic() + timeout * DEADLINE_MULTIPLIER
    try:
        with opener.open(req, timeout=timeout) as r:
            raw = _read_capped(r, max_bytes, deadline)
            if raw.startswith(b"\x1f\x8b"):
                raw = _gunzip(raw, max_bytes)
            text = raw.decode("utf-8", "replace")
            return r.status, text, r.geturl()
    except urllib.error.HTTPError as e:
        if e.code in THROTTLE_STATUSES:
            _note_throttled(host, e.code, e.headers.get("Retry-After"))
        return e.code, "", url
    except (OSError, ValueError):
        # OSError covers URLError/ConnectionError/TimeoutError/SSLError (all
        # subclass it) for DNS failure, refused connection or a stalled
        # socket; ValueError covers a malformed url reaching Request().
        return 0, "", url


def reset_rate_limits(seed: int | None = None) -> None:
    """Test helper: clear the per-host clock and counters between cases.

    `seed` pins the jitter so a test can assert on timing. A gate that depends
    on real randomness is a flaky gate.
    """
    _last_fetch_at.clear()
    _robots_cache.clear()
    _host_requests.clear()
    _host_throttles.clear()
    _stopped_hosts.clear()
    if seed is not None:
        _rng.seed(seed)


def stopped_hosts() -> dict[str, str]:
    """Hosts this run gave up on, and why.

    Surfaced so a run summary can SAY it backed off. A silent back-off looks
    identical to a collector that found nothing, and "no new jobs" is a
    conclusion this app produces legitimately - so the two must not be
    indistinguishable.
    """
    return dict(_stopped_hosts)
