"""The suite's network guard, tested like anything else.

_no_network is autouse, so it applies to every test in this project including
these. That is exactly why it needs its own: a guard nobody exercises is a guard
that can stop working without a single test turning red - the whole suite would
go on passing, and the next accidental live request would take 87 seconds and be
blamed on a slow machine.

That number is not hypothetical. It is what a cmd_refresh test cost on
2026-08-09, before this existed.
"""
from __future__ import annotations

import socket

import pytest


def test_a_real_hostname_is_refused():
    with pytest.raises(AssertionError, match="does not use the network"):
        socket.getaddrinfo("www.linkedin.com", 443)


def test_the_message_names_the_host_so_the_author_knows_which_call_it_was():
    """A guard that says only "no network" sends somebody hunting through a
    stack. The one thing they need is which host, and it is the one thing the
    fixture knows."""
    with pytest.raises(AssertionError, match="boards.greenhouse.io"):
        socket.getaddrinfo("boards.greenhouse.io", 443)


@pytest.mark.parametrize("host", [
    "192.168.1.1",      # links.is_private_destination resolves literals to
    "10.0.0.5",         # decide if a URL points somewhere private. Blocking
    "169.254.169.254",  # these would break the SSRF tests themselves.
    "::1",
])
def test_literal_addresses_still_resolve(host):
    assert socket.getaddrinfo(host, None)


@pytest.mark.parametrize("host", [
    "a-name-that-does-not-resolve.invalid",
    "busy.example",
])
def test_reserved_names_are_left_to_fail_on_their_own(host):
    """RFC 2606 / 6761 names cannot reach anybody, so the guard has no reason to
    intercept them - and fixtures use them precisely because they always fail.
    Turning that natural failure into an AssertionError would make the guard
    the thing under test in somebody else's test."""
    with pytest.raises(socket.gaierror):
        socket.getaddrinfo(host, None)


def test_loopback_is_allowed():
    assert socket.getaddrinfo("localhost", None)
