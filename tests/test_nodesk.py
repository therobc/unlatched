"""NoDesk: a search source read through its sitemap, with a slug pre-filter.

An earlier change. The sitemap lists 14,891 job pages. Fetching them all is four
hours, so the URLs are filtered by their slugs BEFORE anything is fetched -
which is only possible because a NoDesk job URL names the company and the
title. These tests exist mostly to keep that pre-filter honest: too strict
and it hides jobs, too loose and it re-creates the four-hour crawl.
"""
from __future__ import annotations

from conftest import make_fetcher

from unlatched.sources import nodesk

# Newest first, which is how NoDesk really orders it - measured 2026-08-06.
SITEMAP = """<?xml version="1.0" encoding="utf-8"?>
<urlset>
  <url><loc>https://nodesk.co/remote-jobs/acme-support-analyst/</loc></url>
  <url><loc>https://nodesk.co/remote-jobs/globex-senior-carpenter/</loc></url>
  <url><loc>https://nodesk.co/remote-jobs/initech-support-analyst-ii/</loc></url>
  <url><loc>https://nodesk.co/remote-companies/acme/</loc></url>
</urlset>"""

# The minified, UNQUOTED attribute NoDesk actually serves. A quoted-only
# pattern found nothing here - see test_schema_org_unquoted_attribute.
PAGE = """<html><head><script type=application/ld+json>
{"@context":"https://schema.org/","@type":"JobPosting",
 "title":"Support Analyst","datePosted":"2026-07-30",
 "employmentType":["FULL_TIME","PART_TIME"],
 "jobLocationType":"TELECOMMUTE",
 "hiringOrganization":{"@type":"Organization","name":"Acme Corp"},
 "description":"<p>Answer tickets and support end users.</p>"}
</script></head><body>x</body></html>"""


def routes(**extra) -> dict:
    base = {
        nodesk.SITEMAP_URL: SITEMAP,
        "https://nodesk.co/remote-jobs/acme-support-analyst/": PAGE,
        "https://nodesk.co/remote-jobs/globex-senior-carpenter/": PAGE,
        "https://nodesk.co/remote-jobs/initech-support-analyst-ii/": PAGE,
    }
    base.update(extra)
    return base


# ---- the slug pre-filter, which is the whole design ---------------------

def test_every_word_of_a_wanted_term_must_appear_in_the_slug():
    assert nodesk.slug_wants("acme support analyst", ["Support Analyst"]) is True
    assert nodesk.slug_wants("acme support analyst", ["Carpenter"]) is False


def test_the_words_do_not_have_to_be_adjacent():
    """Deliberately the same rule screen.title_wants applies to real titles:
    somebody who wants "Support Analyst" will take "Support Operations
    Analyst". A stricter test here would throw the posting away before
    screening ever saw it."""
    assert nodesk.slug_wants("acme support operations analyst", ["Support Analyst"]) is True


def test_any_one_term_matching_is_enough():
    assert nodesk.slug_wants("globex senior carpenter", ["Support Analyst", "Carpenter"]) is True


def test_no_terms_configured_matches_everything():
    """A brand new profile has no title list yet. The per-run cap is what
    keeps that bounded, not this."""
    assert nodesk.slug_wants("anything at all", []) is True


def test_only_job_pages_are_considered():
    urls = nodesk.candidate_urls(SITEMAP, [])
    assert all("/remote-jobs/" in u for u in urls)
    assert not any("remote-companies" in u for u in urls)


def test_the_newest_are_taken_first():
    """The sitemap is newest-first, so a cap must take from the TOP. If
    NoDesk ever reverses that order this test still passes but the module's
    docstring stops being true - which is why the ordering is written down
    there as an assumption rather than a fact."""
    urls = nodesk.candidate_urls(SITEMAP, [], limit=2)
    assert urls == [
        "https://nodesk.co/remote-jobs/acme-support-analyst/",
        "https://nodesk.co/remote-jobs/globex-senior-carpenter/",
    ]


def test_the_cap_is_a_politeness_budget_and_is_respected():
    assert len(nodesk.candidate_urls(SITEMAP, [], limit=1)) == 1


# ---- collecting ---------------------------------------------------------

def test_only_matching_pages_are_fetched():
    """The point of the pre-filter: a search for carpenters must not fetch
    two analyst pages to find that out."""
    fetched = []

    def counting_fetcher(url, **kwargs):
        fetched.append(url)
        return make_fetcher(routes())(url, **kwargs)

    cfg = {"search": {"title_include": ["Carpenter"]}}
    nodesk.collect(cfg, fetcher=counting_fetcher)
    job_pages = [u for u in fetched if "/remote-jobs/" in u]
    assert job_pages == ["https://nodesk.co/remote-jobs/globex-senior-carpenter/"]


def test_a_collected_job_carries_the_employer_from_the_markup():
    """A board collector knows whose board it is reading. This one does not -
    the employer is only named inside the page."""
    cfg = {"search": {"title_include": ["Support Analyst"]}}
    jobs = nodesk.collect(cfg, fetcher=make_fetcher(routes()))
    assert len(jobs) == 2, "both analyst slugs match; the carpenter must not"
    job = jobs[0]
    assert job.source == "nodesk"
    assert job.employer == "Acme Corp"
    assert job.title == "Support Analyst"
    assert "Answer tickets" in job.description
    assert "Remote" in job.location


def test_the_employment_type_list_is_joined_not_stringified():
    """schema.org says employmentType is singular; the wild says otherwise.
    str(["FULL_TIME","PART_TIME"]) would store brackets and quotes for
    employment.py to read through."""
    cfg = {"search": {"title_include": ["Support Analyst"]}}
    job = nodesk.collect(cfg, fetcher=make_fetcher(routes()))[0]
    assert "[" not in job.employment_type
    assert "FULL_TIME" in job.employment_type


def test_the_source_id_is_stable_across_runs():
    cfg = {"search": {"title_include": ["Support Analyst"]}}
    first = nodesk.collect(cfg, fetcher=make_fetcher(routes()))
    second = nodesk.collect(cfg, fetcher=make_fetcher(routes()))
    assert [j.key() for j in first] == [j.key() for j in second]


def test_an_unreachable_sitemap_is_empty_not_an_exception():
    assert nodesk.collect({}, fetcher=make_fetcher({})) == []
    assert nodesk.collect({}, fetcher=make_fetcher({nodesk.SITEMAP_URL: (500, "")})) == []


def test_a_page_that_fails_does_not_lose_the_others():
    cfg = {"search": {"title_include": ["Support Analyst"]}}
    broken = routes(**{"https://nodesk.co/remote-jobs/acme-support-analyst/": (503, "")})
    jobs = nodesk.collect(cfg, fetcher=make_fetcher(broken))
    assert len(jobs) == 1
    assert "initech" in jobs[0].url


def test_it_is_registered_as_a_search_source():
    from unlatched import sources
    assert "nodesk" in sources.search_sources(sources.registry())
