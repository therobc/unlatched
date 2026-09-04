"""How the collector asks, not how much it asks for.

The volume limits - page caps, DETAIL_CAP, the recheck ceiling, the wall-clock
deadline - were already in place and look complete until somebody asks about
rhythm. These cover the three things they said nothing about: a metronomic
request pattern, continuous pressure across a long run, and what happens when a
host says stop.

Sleeps are captured rather than performed. A test that actually waited eight
seconds for the rest interval would be a test nobody runs.
"""
from __future__ import annotations

import urllib.error

import pytest

from unlatched import fetch


@pytest.fixture(autouse=True)
def _clean_pacing():
    # Seeded, so jitter is reproducible - a gate that depends on real
    # randomness is a flaky gate.
    fetch.reset_rate_limits(seed=1)
    yield
    fetch.reset_rate_limits()


@pytest.fixture
def slept(monkeypatch):
    """Capture every sleep instead of taking it."""
    calls: list[float] = []
    monkeypatch.setattr(fetch.time, "sleep", calls.append)
    return calls


def test_the_gap_between_requests_is_never_the_same_twice(slept, monkeypatch):
    """A fixed delay is a metronome: the easiest pattern to classify as
    automated, and a steady floor of load rather than a yield."""
    clock = iter([0.0] * 200)
    monkeypatch.setattr(fetch.time, "monotonic", lambda: next(clock))

    for _ in range(6):
        fetch._respect_rate_limit("example.com", 1.5)  # noqa: SLF001

    waits = [w for w in slept if w > 0]
    assert len(set(waits)) > 1, f"every wait identical: {waits}"


def test_jitter_only_ever_lengthens_the_gap(slept, monkeypatch):
    """Randomising must not become a way of asking FASTER than the delay."""
    clock = iter([0.0] * 400)
    monkeypatch.setattr(fetch.time, "monotonic", lambda: next(clock))

    for _ in range(30):
        fetch._respect_rate_limit("example.com", 1.5)  # noqa: SLF001

    # Strictly below the rest interval, so the periodic long pause is not
    # mistaken for a per-request wait.
    waits = [w for w in slept if 0 < w < fetch.REST_SECONDS]
    assert waits, "expected some per-request waits"
    assert min(waits) >= 1.5, f"a wait came in under the delay: {min(waits)}"
    assert max(waits) <= 1.5 * (1 + fetch.JITTER_FRACTION) + 1e-9


def test_a_long_run_takes_a_real_break(slept, monkeypatch):
    """The per-request delay never adds up to a pause, and continuous pressure
    is the part a small employer's careers page actually feels."""
    clock = iter([0.0] * 400)
    monkeypatch.setattr(fetch.time, "monotonic", lambda: next(clock))

    for _ in range(fetch.REST_EVERY):
        fetch._respect_rate_limit("example.com", 1.5)  # noqa: SLF001

    assert fetch.REST_SECONDS in slept, \
        f"no rest after {fetch.REST_EVERY} requests: {slept}"


def test_the_rest_is_per_host_not_global(slept, monkeypatch):
    """Two hosts sharing one counter would rest one of them for the other's
    traffic, and never rest either at the right time."""
    clock = iter([0.0] * 400)
    monkeypatch.setattr(fetch.time, "monotonic", lambda: next(clock))

    for _ in range(fetch.REST_EVERY - 1):
        fetch._respect_rate_limit("a.example", 1.5)  # noqa: SLF001
    for _ in range(fetch.REST_EVERY - 1):
        fetch._respect_rate_limit("b.example", 1.5)  # noqa: SLF001

    assert fetch.REST_SECONDS not in slept


def test_repeated_push_back_stops_the_host_for_the_run(slept):
    """The one that matters. A 429 used to be a plain failed fetch, and the
    loop moved straight on to the next URL on the SAME host - knocking harder
    after being asked to stop."""
    for _ in range(fetch.THROTTLE_LIMIT):
        fetch._note_throttled("busy.example", 429, None)  # noqa: SLF001

    assert "busy.example" in fetch.stopped_hosts()
    # And it is reported, because a silent back-off is indistinguishable from
    # a collector that found nothing.
    assert "429" in fetch.stopped_hosts()["busy.example"]


def test_one_push_back_is_not_treated_as_an_answer(slept):
    """Two could be a coincidence of load. Stopping on the first would abandon
    a host that was briefly busy."""
    fetch._note_throttled("busy.example", 503, None)  # noqa: SLF001
    assert fetch.stopped_hosts() == {}


def test_a_stopped_host_is_not_asked_again(slept, monkeypatch):
    """Declined before robots.txt: the cheapest request is the one never made,
    and re-reading robots would itself be another request to a host that just
    asked for fewer."""
    for _ in range(fetch.THROTTLE_LIMIT):
        fetch._note_throttled("busy.example", 429, None)  # noqa: SLF001

    def explode(*_args, **_kwargs):
        raise AssertionError("a stopped host must not be contacted")

    monkeypatch.setattr(fetch.urllib.request, "build_opener", explode)
    monkeypatch.setattr(fetch, "_robots_allows", explode)

    assert fetch.fetch("https://busy.example/jobs") == (0, "", "https://busy.example/jobs")


def test_retry_after_is_honoured_when_the_host_states_its_terms(slept):
    fetch._note_throttled("busy.example", 429, "30")  # noqa: SLF001
    assert 30.0 in slept


def test_a_host_cannot_park_the_whole_run(slept):
    """Honouring Retry-After is right; obeying an absurd one is not."""
    fetch._note_throttled("busy.example", 429, "86400")  # noqa: SLF001
    assert max(slept) == fetch.MAX_RETRY_AFTER_S


def test_an_unparseable_retry_after_falls_back_rather_than_guessing(slept):
    """The HTTP-date form is legal and rare. Guessing at a date parse to sleep
    on is worse than our own backoff."""
    fetch._note_throttled("busy.example", 429, "Wed, 21 Oct 2026 07:28:00 GMT")  # noqa: SLF001
    assert slept == []


def test_a_throttled_response_is_recorded_through_the_real_fetch_path(monkeypatch, slept):
    """The counter has to be wired to the error branch, not just callable."""
    def raise_429(*_args, **_kwargs):
        raise urllib.error.HTTPError(
            "https://busy.example/jobs", 429, "Too Many Requests",
            {"Retry-After": "5"}, None)

    class Opener:
        open = staticmethod(raise_429)

    monkeypatch.setattr(fetch.urllib.request, "build_opener", lambda *_: Opener())
    monkeypatch.setattr(fetch, "_robots_allows", lambda *_a, **_k: True)
    monkeypatch.setattr(fetch, "is_private_destination", lambda _u: False)

    for _ in range(fetch.THROTTLE_LIMIT):
        status, _, _ = fetch.fetch("https://busy.example/jobs")
        assert status == 429

    assert "busy.example" in fetch.stopped_hosts()
    assert 5.0 in slept, "Retry-After should have been honoured on the way"


def test_an_ordinary_failure_is_not_mistaken_for_push_back(monkeypatch, slept):
    """A 500 or a timeout is a broken host, not a host asking for less. Backing
    off on those would abandon boards over transient faults."""
    def raise_500(*_args, **_kwargs):
        raise urllib.error.HTTPError(
            "https://broken.example/jobs", 500, "Server Error", {}, None)

    class Opener:
        open = staticmethod(raise_500)

    monkeypatch.setattr(fetch.urllib.request, "build_opener", lambda *_: Opener())
    monkeypatch.setattr(fetch, "_robots_allows", lambda *_a, **_k: True)
    monkeypatch.setattr(fetch, "is_private_destination", lambda _u: False)

    for _ in range(fetch.THROTTLE_LIMIT + 2):
        fetch.fetch("https://broken.example/jobs")

    assert fetch.stopped_hosts() == {}
