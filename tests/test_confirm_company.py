"""Resolving a plausible domain proves it exists, not that it
is the company's. `page_confirms_company` has to see the page actually name
the company before anything on it is trusted; a domain that resolves but
serves someone else's site must not be adopted just because it was the
first candidate that answered.
"""
from __future__ import annotations

from unlatched import discover


def test_resolving_but_wrong_page_does_not_confirm():
    html = ("<html><head><title>Welcome</title></head><body>"
            "Totally Unrelated Corp is a leading provider of widgets."
            "</body></html>")
    assert discover.page_confirms_company(html, "Example Fintech Corp") is False


def test_page_naming_the_company_confirms():
    html = "<html><body>Careers at Example Fintech Corp - join our team</body></html>"
    assert discover.page_confirms_company(html, "Example Fintech Corp") is True


def test_resolve_does_not_adopt_a_domain_whose_page_never_confirms(monkeypatch):
    # Every candidate domain "resolves" (DNS is not exercised in a test), but
    # every fetched page belongs to somebody else. resolve() must come back
    # with no careers_url and no ATS rather than trusting the first hit.
    monkeypatch.setattr(discover, "resolves", lambda domain, timeout=5.0: True)

    # Padded past the 500-byte floor `resolve()` applies before it even asks
    # whether the page confirms anything - this has to fail on CONTENT, not
    # on being too short to bother reading.
    filler = "This page belongs to somebody else entirely. " * 20
    html = (f"<html><body>Unrelated Company Inc - not who you are looking for. "
            f"{filler}</body></html>")

    def fetcher(url, **kwargs):
        return 200, html, url

    result = discover.resolve("Example Fintech Corp", fetcher=fetcher)
    assert result["careers_url"] == ""
    assert result["ats"] == []
    assert "no page identified itself" in result["note"]


def test_resolve_stops_at_the_first_confirming_page(monkeypatch):
    monkeypatch.setattr(discover, "resolves", lambda domain, timeout=5.0: True)

    filler = "Join our growing team and help us build the future. " * 12

    def fetcher(url, **kwargs):
        if "careers.examplefintech.com" in url:
            html = (f'<html><body>Careers at Example Fintech Corp. {filler}'
                     '<a href="https://jobs.lever.co/examplefintech/">jobs</a>'
                     '</body></html>')
            return 200, html, url
        return 200, f"<html><body>Unrelated Company Inc. {filler}</body></html>", url

    result = discover.resolve("Example Fintech Corp", fetcher=fetcher)
    assert result["ats"], result
    assert result["ats"][0]["provider"] == "lever"


# ---- initialism names whose other words are all generic ------------------
#
# "PNC Financial Services" has no non-generic 4+ token: "financial" and
# "services" are both in GENERIC_TOKEN and "pnc" is too short for the 4+
# scan. The whole-name fallback then looked for "pncfinancialservices",
# which appears on no page in existence - so PNC's real 157KB careers page,
# saying "PNC" throughout, was rejected and the employer recorded as dead.

def test_initialism_confirms_when_every_other_word_is_generic():
    page = "<html><body>Welcome to PNC. Careers at PNC Bank.</body></html>"
    assert discover.page_confirms_company(page, "PNC Financial Services")
    assert discover.page_confirms_company(
        "<html>GXO is hiring</html>", "GXO Logistics")


def test_initialism_must_match_as_a_whole_word():
    """"pnc" inside "pncbank" is not evidence the page is PNC's - the same
    strictness that keeps "abb" from matching "abbey".
    """
    assert not discover.page_confirms_company(
        "<html>pncbank-lookalike.example</html>", "PNC Financial Services")


def test_two_letter_initialisms_do_not_confirm_on_their_own():
    """"at" appears on essentially every page ever written, so a 2-character
    token is not evidence. AT&T is still reachable through the squashed
    whole-name form ("att").
    """
    assert not discover.page_confirms_company(
        "<html>look at this unrelated page</html>", "AT&T")
    assert discover.page_confirms_company("<html>att careers</html>", "AT&T")


def test_a_wrong_company_page_still_fails():
    assert not discover.page_confirms_company(
        "<html>welcome to acme corp</html>", "GXO Logistics")


# ---- careers links on the confirmed page --------------------------------
#
# A census of 104 unreadable employers found 83 recorded as nothing but
# their homepage: every guessed path missed, and the "Careers" link in the
# employer's own navigation was never followed.

def test_careers_links_finds_same_site_careers_pages():
    html = ('<a href="/about-us/careers/">Careers</a>'
             '<a href="/products">Products</a>'
             '<a href="https://example.com/en-us/join-our-team">Work with us</a>')
    links = discover.careers_links(html, "https://example.com/")
    assert "https://example.com/about-us/careers/" in links
    assert "https://example.com/en-us/join-our-team" in links
    assert not [x for x in links if "products" in x]


def test_careers_links_matches_on_anchor_text_not_only_the_href():
    """Employers routinely label the link "Careers" while the href is
    something no path guess would produce.
    """
    html = '<a href="/x7/portal-2">Careers</a>'
    assert discover.careers_links(html, "https://example.com/") == [
        "https://example.com/x7/portal-2"]


def test_careers_links_stays_on_the_same_origin():
    """Offsite links are external_careers_hosts' job; following arbitrary
    ones is how a crawler wanders.
    """
    html = '<a href="https://elsewhere.example/careers">Careers</a>'
    assert discover.careers_links(html, "https://example.com/") == []


def test_careers_links_is_bounded():
    html = "".join(f'<a href="/careers-{i}">Careers</a>' for i in range(30))
    assert len(discover.careers_links(html, "https://example.com/")) <= (
        discover.MAX_CAREERS_LINK_HOPS)


# ---- greenhouse publishes its board four different ways ------------------

def test_every_live_greenhouse_embed_form_is_fingerprinted():
    """The original pattern covered two of four. Elation Health publishes the
    "/js" embed variant and PerfectServe the bare "boards.greenhouse.io/slug";
    both were recorded as having no board while being fully collectable.
    """
    forms = {
        "//boards.greenhouse.io/embed/job_board/js?for=elationhealth": "elationhealth",
        "//boards.greenhouse.io/perfectserve": "perfectserve",
        "https://boards.greenhouse.io/embed/job_board?for=acme": "acme",
        "https://job-boards.greenhouse.io/acmecorp": "acmecorp",
    }
    for html, slug in forms.items():
        hits = discover.detect_ats(html)
        assert hits, html
        assert hits[0]["provider"] == "greenhouse"
        assert hits[0]["parts"] == [slug], html


def test_embed_is_never_captured_as_a_company_slug():
    hits = discover.detect_ats("boards.greenhouse.io/embed/job_board?for=realslug")
    assert hits[0]["parts"] == ["realslug"]
