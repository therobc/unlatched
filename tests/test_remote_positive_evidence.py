"""A posting that never mentions location was being treated as
remote - silence is not evidence. remote_evidence() must require a positive
match and return the evidence string that convinced it, so the reasoning is
visible rather than a bare true/false.
"""
from __future__ import annotations

from unlatched import screen


def test_silent_posting_is_not_remote():
    title = "Support Analyst"
    location = ""
    description = ("Join our team to support customers with their accounts. "
                    "We offer great benefits and a collaborative culture. " * 10)
    is_remote, evidence = screen.remote_evidence(title, location, description)
    assert is_remote is False
    assert evidence == ""


def test_location_field_stating_remote_is_evidence():
    is_remote, evidence = screen.remote_evidence(
        "Support Analyst", "Remote - United States", "")
    assert is_remote is True
    assert "remote" in evidence.lower()


def test_description_declaring_fully_remote_is_evidence():
    is_remote, evidence = screen.remote_evidence(
        "Support Analyst", "", "This is a fully remote position based anywhere in the US.")
    assert is_remote is True
    assert evidence


def test_city_with_no_remote_language_is_not_remote():
    is_remote, evidence = screen.remote_evidence(
        "Support Analyst", "Columbus, Ohio",
        "Provide desktop support and manage tickets in our office.")
    assert is_remote is False
