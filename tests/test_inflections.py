"""Exact word-boundary matching invents gaps. A resume that
says "Communications" does not literally contain "Communication", and one
that says "Diagnosing" does not contain "Diagnose" - both are the same
skill in an ordinary inflection, and present() must match them.
"""
from __future__ import annotations

from unlatched import coverage


def test_communications_matches_communication():
    assert coverage.present("Communication", "strong communications skills") is True


def test_diagnosing_matches_diagnose():
    assert coverage.present("Diagnose", "experience diagnosing network issues") is True


def test_troubleshooting_matches_troubleshoot():
    assert coverage.present("Troubleshoot", "skilled at troubleshooting complex systems") is True


def test_inflection_never_matches_as_a_prefix_of_an_unrelated_word():
    # "soft" must not match inside "software"; "AI" must not match inside
    # "email" or "maintain".
    assert coverage.present("soft", "we build enterprise software") is False
    assert coverage.present("AI", "please email the hiring manager") is False


def test_multi_word_term_matches_with_flexible_separators():
    # present() documents that the haystack arrives already lowercased -
    # callers normalise once, on their side, rather than every match paying
    # for it again.
    assert coverage.present("Active Directory", "manage active-directory group policy") is True
