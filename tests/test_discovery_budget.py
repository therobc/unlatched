"""Discovery has to finish a real company list in minutes, not hours.

A resolving domain is not the company's domain: probing one freight company
resolved four candidates (its own, plus two unrelated registrations of the
short form), and since every accepted domain contributes eleven URLs, that
one company cost 44 requests. Two bounds keep it honest: only the first few
resolving domains are tried, and a company can never exceed a fixed request
ceiling however the domain math works out.
"""
from __future__ import annotations

from unlatched import discover


def _counting_fetcher(pages=None):
    """Returns (fetcher, calls). Every URL 404s unless listed in `pages`."""
    pages = pages or {}
    calls: list[str] = []

    def fetch(url, **_kw):
        calls.append(url)
        if url in pages:
            return 200, pages[url], url
        return 404, "", url

    return fetch, calls


def test_every_resolving_domain_is_not_probed(monkeypatch):
    monkeypatch.setattr(discover, "resolves", lambda *_a, **_k: True)
    fetch, calls = _counting_fetcher()

    discover.resolve("Lineage Logistics", fetcher=fetch)

    domains = {url.split("/")[2].removeprefix("careers.").removeprefix("jobs.")
                .removeprefix("www.") for url in calls}
    assert len(domains) <= discover.MAX_DOMAINS_TRIED


def test_request_ceiling_holds_when_nothing_confirms(monkeypatch):
    monkeypatch.setattr(discover, "resolves", lambda *_a, **_k: True)
    fetch, calls = _counting_fetcher()

    discover.resolve("Lineage Logistics", fetcher=fetch)

    assert len(calls) <= discover.MAX_URL_ATTEMPTS


def test_confirmation_stops_probing_other_domains(monkeypatch):
    monkeypatch.setattr(discover, "resolves", lambda *_a, **_k: True)
    confirming_page = (
        "<html><body>Careers at Lineage Logistics. " + "filler text " * 80
        + "</body></html>")
    fetch, calls = _counting_fetcher(
        {"https://www.lineagelogistics.com": confirming_page})

    result = discover.resolve("Lineage Logistics", fetcher=fetch)

    assert result["domain"] == "lineagelogistics.com"
    other_domain_calls = [c for c in calls if "lineagelogistics.com" not in c]
    assert other_domain_calls == []


def test_ats_fingerprint_still_wins_and_stops_early(monkeypatch):
    monkeypatch.setattr(discover, "resolves", lambda *_a, **_k: True)
    page = ("<html><body>Careers at Lineage Logistics "
             "<a href='https://boards.greenhouse.io/embed/job_board?for=lineage'>"
             "openings</a> " + "filler text " * 80 + "</body></html>")
    fetch, calls = _counting_fetcher({"https://careers.lineagelogistics.com": page})

    result = discover.resolve("Lineage Logistics", fetcher=fetch)

    assert result["ats"], result
    assert result["ats"][0]["provider"] == "greenhouse"
    # The very first URL answered; nothing after it should have been tried.
    assert len(calls) == 1


# ---- Workday career-site extraction ------------------------------------
#
# Measured 2026-08-06 across 34 Workday employers: 20 resolved to a board
# that could never return a posting. Two separate causes, both here.

def test_the_site_is_not_eaten_when_the_url_has_no_locale():
    r"""`(?:[\w-]+/)?` before the site was meant to skip "en-US/". With no
    locale in the URL it skipped the SITE instead and captured "job", and
    /wday/cxs/<tenant>/job/jobs returns nothing for anybody, ever.
    """
    from unlatched import discover
    no_locale = "https://cvshealth.wd1.myworkdayjobs.com/CVS_Health_Careers/job/RI/x_R1"
    with_locale = "https://cvshealth.wd1.myworkdayjobs.com/en-US/CVS_Health_Careers/job/RI/x_R1"
    for html in (no_locale, with_locale):
        found = discover.detect_ats(html)
        assert len(found) == 1, html
        assert found[0]["parts"] == ["cvshealth", "wd1", "CVS_Health_Careers"], html


def test_a_login_or_landing_link_is_not_a_career_site():
    """Recording one produces an employer that is permanently, silently
    empty - worse than no board, because the person concludes the employer
    is not hiring rather than that we cannot read them."""
    from unlatched import discover
    assert discover.detect_ats(
        "https://accenture.wd103.myworkdayjobs.com/en-US/userHome") == []
    assert discover.detect_ats(
        "https://dxctechnology.wd1.myworkdayjobs.com/en-US/login") == []


def test_the_real_site_wins_even_when_a_login_link_comes_first():
    """Taking only the first match meant whichever link the page happened to
    put first decided it."""
    from unlatched import discover
    html = ('<a href="https://cat.wd5.myworkdayjobs.com/en-US/login">Sign in</a>'
            '<a href="https://cat.wd5.myworkdayjobs.com/en-US/CAT_Careers">Jobs</a>')
    found = discover.detect_ats(html)
    assert found[0]["parts"] == ["cat", "wd5", "CAT_Careers"]
