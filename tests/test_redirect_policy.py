"""A red-team review found that URL policy was enforced on the first URL only.

urllib follows redirects, and nothing re-applied the caller's rules to where
they led. Two things escaped through that gap, and both are tested here
against a real local HTTP server rather than a mock - the whole defect lived
in urllib's redirect handling, so a fake fetcher would have proved nothing.

  * NEVER_FETCH (Indeed, Glassdoor, Jobs4TN, FlexJobs) is a LEGAL rule, and
    any 302 defeated it.
  * ALLOWED_SCHEMES did not cover ftp:, which CPython's redirect handler
    permits alongside http and https.
"""
from __future__ import annotations

# If the blank line below the pytest import keeps vanishing: the PostToolUse
# formatter and the gate's ruff disagree about whether `unlatched` is
# first-party here, so editing this file strips what the gate then demands
# back. `python C:/tmp/ruff_like_gates.py --fix` settles it, because a Bash
# edit is not post-processed by the hook.
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from unlatched import fetch, manual

# Where each path sends the client. "" means answer normally.
ROUTES = {
    "/to-aggregator": "https://www.indeed.com/viewjob?jk=1",
    "/to-ftp": "ftp://198.51.100.5/x",
    "/to-file": "file:///C:/Windows/System32/calc.exe",
    # Stays on this server, so "an allowed redirect is still followed" can be
    # proved without the suite reaching the internet.
    "/to-ok": "/ok",
    "/ok": "",
}


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 - name fixed by BaseHTTPRequestHandler
        target = ROUTES.get(self.path)
        if target:
            self.send_response(302)
            self.send_header("Location", target)
            self.end_headers()
            return
        body = b"<html><body>arrived</body></html>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        """Silence: the suite's output is not a web server log."""


@pytest.fixture
def server():
    httpd = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


@pytest.fixture(autouse=True)
def _no_rate_limit():
    fetch.reset_rate_limits()


def _fetch(url, **kw):
    # allow_private: the test server is on loopback by necessity. That is the
    # one place this flag is turned on, and turning it on is what lets the
    # test exercise the redirect guard rather than the address check.
    return fetch.fetch(url, respect_robots=False, per_host_delay_s=0,
                           allow_private=True, **kw)


def test_a_redirect_cannot_smuggle_us_onto_an_aggregator(server):
    status, text, _final = _fetch(
        f"{server}/to-aggregator",
        url_ok=lambda u: manual.may_fetch(u, hand_added=False))
    # The redirect is declined, so what comes back is the 302 itself with no
    # body - never Indeed's page.
    assert status == 302
    assert text == ""


def test_an_allowed_redirect_is_still_followed(server):
    """The refusals above have to be POLICY, not a redirect handler I broke.

    Same server on both ends, so this proves the mechanism without the suite
    reaching the internet.
    """
    status, text, final = _fetch(f"{server}/to-ok")
    assert status == 200
    assert "arrived" in text
    assert final.endswith("/ok"), "followed through to the destination"


@pytest.mark.parametrize("path", ["/to-ftp", "/to-file"])
def test_a_redirect_cannot_escape_the_allowed_schemes(server, path):
    status, text, _final = _fetch(f"{server}{path}")
    assert status == 302, "not followed"
    assert text == ""


def test_an_ordinary_redirectless_page_still_reads(server):
    """The guard must not break the 99% case."""
    status, text, _final = _fetch(f"{server}/ok")
    assert status == 200
    assert "arrived" in text


def test_a_private_destination_is_refused_without_the_flag(server):
    """End to end: the same URL the tests above reach on purpose is
    refused by default, because the server is on loopback."""
    status, text, _final = fetch.fetch(f"{server}/ok", respect_robots=False,
                                           per_host_delay_s=0)
    assert (status, text) == (0, "")
