"""Shared fixtures. Nothing in this suite touches the network - every test
that would otherwise fetch a URL is given a fake fetcher with the same
(status, text, final_url) signature as unlatched.fetch.fetch.

That sentence was an ASPIRATION for most of this project's life, and it was
wrong. 2026-08-09: a new test called cmd_refresh, which calls cmd_collect, which
runs the search sources after the company loop with the REAL fetcher. It made
live outbound requests and took 87 seconds. Nothing in the suite objected,
because nothing in the suite was checking - the guarantee lived in a docstring
and in each author remembering to inject a fake.

_no_network below makes it enforced. Every test gets it, so the claim above is
now a property of the suite rather than a description of how it is usually
written.
"""
from __future__ import annotations

import ipaddress
import socket
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from unlatched import config as config_mod
from unlatched import db as db_mod

# TLDs the IETF reserved so they can never resolve to a real host (RFC 2606,
# RFC 6761). A lookup of one of these cannot reach anybody, which is exactly why
# fixtures use them - and why the guard below lets them through to fail
# naturally rather than turning "this name does not resolve" into a test error.
UNRESOLVABLE_TLDS = (".invalid", ".example", ".test", ".localhost")


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """Block name resolution for every test in the suite.

    getaddrinfo rather than unlatched.fetch.fetch: patching our own function
    only covers paths that go through it, and the failure that prompted this
    went through it and still escaped, because the test never patched anything.
    Nothing reaches a remote host without resolving a name first, so this
    catches a leak wherever it originates - including from a dependency.

    THREE THINGS PASS THROUGH, each because it cannot reach a stranger:

      literal IP addresses    getaddrinfo parses these without asking anybody.
                              links.is_private_destination resolves the host to
                              decide whether a URL points somewhere private -
                              the SSRF guard - so blocking this would break the
                              tests protecting us from 169.254.169.254, which is
                              the opposite of the intent here.
      reserved TLDs           see UNRESOLVABLE_TLDS.
      loopback                a local server is not the hazard, and a test that
                              binds one should not have to fight this fixture.

    Everything else raises, loudly and by name, rather than returning a failure
    that reads like an ordinary unreachable host - a silent guard would let the
    next author write the same test and never learn why it is slow.
    """
    real = socket.getaddrinfo

    def guarded(host, *args, **kwargs):
        name = str(host or "").strip("[]").lower()
        if name in ("localhost", "127.0.0.1", "::1", ""):
            return real(host, *args, **kwargs)
        if name.endswith(UNRESOLVABLE_TLDS):
            return real(host, *args, **kwargs)
        try:
            ipaddress.ip_address(name)
        except ValueError:
            msg = (f"test tried to resolve {host!r}. This suite does not use "
                   f"the network - inject a fake fetcher (see make_fetcher).")
            raise AssertionError(msg) from None
        return real(host, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", guarded)


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A throwaway UNLATCHED_HOME for one test."""
    h = tmp_path / "unlatched_home"
    monkeypatch.setenv("UNLATCHED_HOME", str(h))
    return h


@pytest.fixture
def con(home):
    """An open, migrated database in the throwaway home."""
    c = db_mod.connect(home)
    yield c
    c.close()


@pytest.fixture
def cfg():
    return config_mod.defaults()


def make_fetcher(routes: dict):
    """Build a fake fetcher from {url: (status, text)} or {url: text}.
    Any URL not in `routes` returns (0, "", url) - a clean failure, never a
    real network call.
    """
    def fetcher(url, **kwargs):
        if url in routes:
            v = routes[url]
            if isinstance(v, tuple):
                status, text = v
            else:
                status, text = 200, v
            return status, text, url
        return 0, "", url
    return fetcher
