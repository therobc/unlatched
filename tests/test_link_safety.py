"""The URL trust boundaries, from the red-team review of 2026-08-08.

Each test names the finding it pins. These are not hypotheticals: every one of
them describes something the app did before this file existed.
"""
from __future__ import annotations

import pytest

from unlatched import config, db, links, manual

# What a posting could nominate as its `url` before this was closed. Each one
# would have been stored verbatim and rendered as a link that eframe hands to
# the Windows shell.
HOSTILE = (
    "file://198.51.100.5/share/apply",
    "file:///C:/Windows/System32/calc.exe",
    "javascript:alert(1)",
    "ms-msdt:/id PCWDiagnostic",
    "data:text/html,<h1>hi",
    "ftp://198.51.100.5/x",
    "http://",
    "not a url",
    "",
)


@pytest.mark.parametrize("url", HOSTILE)
def test_only_http_counts_as_a_link(url):
    assert links.is_safe(url) is False
    assert links.safe_or_empty(url) == ""


@pytest.mark.parametrize("url", [
    "https://jobs.lever.co/softdocs/5eaba021",
    "http://careers.example.com:8443/jobs/1",
    "https://boards.greenhouse.io/brex?x=1#frag",
])
def test_real_postings_are_still_links(url):
    assert links.is_safe(url) is True
    assert links.safe_or_empty(url) == url


def test_a_hostile_url_loses_its_link_but_keeps_its_row(con):
    """Store boundary. The job is real even when the link is not - a
    posting is not discarded for having a URL we refuse to open."""
    cid = db.upsert_company(con, "Example")
    db.upsert_job(con, "sitemap:1", {
        "company_id": cid,
        "title": "Support Analyst",
        "url": "file://198.51.100.5/share/apply",
    })
    row = db.get_job(con, "sitemap:1")
    assert row is not None
    assert row["title"] == "Support Analyst", "the row survives"
    assert row["url"] == "", "the link does not"


def test_a_hostile_careers_url_is_refused_too(con):
    db.upsert_company(con, "Example", careers_url="file:///C:/Windows/x.exe")
    row = db.get_company(con, "Example")
    assert row is not None
    assert row["careers_url"] == ""


def test_adding_a_hostile_link_by_hand_is_refused_out_loud(con):
    """Hand-add path. Refused rather than silently blanked: the person
    typed it, so they are told. stable_id() alone did NOT catch this - the
    path survives even when the hostname is empty."""
    assert manual.stable_id("file:///C:/Windows/System32/calc.exe") != "", \
        "stable_id is not a scheme check and never was"
    with pytest.raises(ValueError, match="only http and https"):
        manual.add(con, config.defaults(), "file:///C:/Windows/System32/calc.exe",
                   title="Typed By Hand")


def test_userinfo_does_not_change_which_host_a_url_belongs_to():
    """The engine's half of a spoofing fix. The desktop had its own
    string-splitting version of this that could be fooled; both now agree."""
    assert links.host_of("https://boards.greenhouse.io@evil.com/jobs/1") == "evil.com"
    assert links.host_of("https://user:pa@ss@evil.com/x") == "evil.com"
    assert links.host_of("https://JOBS.Lever.CO/x") == "jobs.lever.co"


def test_an_aggregator_is_refused_however_it_is_spelled():
    for url in ("https://www.indeed.com/viewjob?jk=1",
                "https://uk.indeed.com/x",
                "https://glassdoor.com/x",
                "https://flexjobs.com/x"):
        assert manual.may_fetch(url) is False, url
    # Lookalikes are NOT the same host and must not be blocked by accident.
    assert manual.may_fetch("https://indeed.com.example.org/x") is True
    assert manual.may_fetch("https://notindeed.com/x") is True


def test_same_site_keeps_a_sitemap_walk_on_the_portal():
    """Every URL the sitemap walker follows came out of remote content."""
    assert links.same_site("https://careers.example.com/sitemap.xml", "careers.example.com")
    assert links.same_site("https://jobs.example.com/x", "careers.example.com"), \
        "a sibling subdomain of the same site is fine"
    assert not links.same_site("http://192.168.1.1/jobs/reboot", "careers.example.com")
    assert not links.same_site("https://evil.com/jobs/1", "careers.example.com")
    assert not links.same_site("not a url", "careers.example.com")


@pytest.mark.parametrize(("url", "expected"), [
    ("http://127.0.0.1:8787/jobs/", True),
    ("http://localhost/jobs/", True),
    ("http://192.168.1.1/jobs/reboot", True),
    ("http://10.0.0.5/x", True),
    ("http://169.254.169.254/latest/meta-data/", True),
    ("http://[::1]/x", True),
    ("https://a-name-that-does-not-resolve.invalid/x", False),
    ("", False),
])
def test_private_destinations_are_recognised(url, expected):
    """Literal addresses are the cheap half; the resolving half is what
    catches a hostile NAME pointed at 10.x, which is the likelier shape."""
    assert links.is_private_destination(url) is expected
