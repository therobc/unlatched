"""Adding a job by link - including from sites this app will not read.

Decided 2026-08-06: somebody applying to a job they found on LinkedIn wants it
in their pipeline. The feature has to work for that WITHOUT the app going and
fetching LinkedIn, which is the thing these tests mostly pin down.
"""
from __future__ import annotations

import pytest
from conftest import make_fetcher

from unlatched import config, db, manual

PAGE = """<html><head><script type=application/ld+json>
{"@context":"https://schema.org/","@type":"JobPosting",
 "title":"Support Analyst","datePosted":"2026-08-01",
 "hiringOrganization":{"@type":"Organization","name":"Acme Corp"},
 "jobLocation":{"address":{"addressLocality":"Dayton","addressRegion":"OH"}},
 "description":"<p>Answer tickets and support end users. Ticketing, Windows.</p>"}
</script></head><body>x</body></html>"""

# Shaped like the real page, measured 2026-08-06: no schema.org markup at
# all, so the facts come out of the page's own containers.
LINKEDIN_PAGE = """<html><body>
<h1 class="topcard__title">Technical Support Engineer I</h1>
<a class="topcard__org-name-link" href="/company/x">Ashcombe</a>
<span class="topcard__flavor--bullet">Remote</span>
<div class="show-more-less-html__markup relative">
<p>Answer tickets and support end users.</p></div>
</body></html>"""


# ---- which hosts we are willing to look at -----------------------------

def test_the_sites_we_do_not_read_are_never_fetched():
    for url in (
        "https://www.indeed.com/viewjob?jk=abc",
        "https://www.glassdoor.com/job-listing/x",
        "https://www.flexjobs.com/publicjobs/x",
    ):
        assert manual.may_fetch(url) is False, url


def test_a_site_we_do_read_is_fetchable():
    assert manual.may_fetch("https://boards.greenhouse.io/acme/jobs/1") is True
    assert manual.may_fetch("https://nodesk.co/remote-jobs/x/") is True


def test_a_blocked_host_is_not_fetched_even_when_a_fetcher_is_handy():
    """The guard is on the HOST, not on whether a fetch would succeed."""
    called = []

    def spy(url, **kwargs):
        called.append(url)
        return 200, PAGE, url

    assert manual.read_posting("https://www.indeed.com/viewjob?jk=1", fetcher=spy) == {}
    assert called == []


# ---- attended-only hosts, which are a deliberate exception ------------------
#
# LinkedIn is the only member of ATTENDED_ONLY_HOSTS and is what this path is
# tested against because it is the most popular. Nothing here is a LinkedIn
# integration - the rule is about a category of site, and the tests are named
# for the rule rather than the site (decided 2026-08-08).

def test_an_attended_only_host_is_read_only_with_a_person_present():
    """Nothing is requested when the hand-add flag is absent - the
    distinction is not cosmetic, it is what keeps these hosts out of every
    collect and every scheduled refresh."""
    called = []

    def spy(url, **kwargs):
        called.append(url)
        return 200, LINKEDIN_PAGE, url

    url = "https://www.linkedin.com/jobs/view/4400440033/"
    assert manual.read_posting(url, fetcher=spy, hand_added=False) == {}
    assert called == []

    page = manual.read_posting(url, fetcher=spy, hand_added=True)
    assert called == [url]
    assert page["title"] == "Technical Support Engineer I"
    assert page["employer"] == "Ashcombe"
    assert "Answer tickets" in page["description"]


def test_the_robots_override_reaches_no_other_host():
    """An attended-only host's robots.txt disallows the job path for every
    agent, so reading it at all means overriding - which must not leak."""
    seen = {}

    def spy(url, **kwargs):
        seen[url] = kwargs.get("respect_robots", True)
        return 200, LINKEDIN_PAGE if "linkedin" in url else PAGE, url

    manual.read_posting("https://www.linkedin.com/jobs/view/1",
                        fetcher=spy, hand_added=True)
    manual.read_posting("https://nodesk.co/remote-jobs/x/", fetcher=spy)
    assert seen["https://www.linkedin.com/jobs/view/1"] is False
    assert seen["https://nodesk.co/remote-jobs/x/"] is True


def test_a_sign_in_wall_yields_nothing_rather_than_a_wrong_title():
    """The page has an <h1> whatever it is. Storing that would put "Sign in"
    in the list as a job title."""
    wall = "<html><body><h1>Sign in to view this job</h1></body></html>"
    page = manual.read_posting("https://www.linkedin.com/jobs/view/1",
                               fetcher=lambda url, **k: (200, wall, url),
                               hand_added=True)
    assert page == {}


def reading_on() -> dict:
    cfg = config.defaults()
    cfg["fetch"]["read_added_links"] = True
    return cfg


def test_turning_it_on_fills_everything_in(con):
    """The whole point of the feature: paste a link, get the job. On, it reads
    the ordinary sites and the attended-only ones alike."""
    for url, page, title in (
        ("https://boards.greenhouse.io/x/jobs/1", PAGE, "Support Analyst"),
        ("https://www.linkedin.com/jobs/view/1", LINKEDIN_PAGE,
         "Technical Support Engineer I"),
    ):
        # `body=page` binds the loop variable at definition time. Closing over
        # it would make every iteration serve whatever the LAST one set.
        result = manual.add(con, reading_on(), url,
                            fetcher=lambda u, body=page, **k: (200, body, u))
        assert result["fetched"] is True, url
        assert result["title"] == title
        assert result["has_description"] is True


def test_a_link_that_names_a_board_registers_the_employer_for_good(con):
    """ONE PASTE, THAT EMPLOYER FROM THEN ON.

    Adding by hand used to record the employer with no board, so the next
    collect skipped them and the same person came back to paste their next
    posting by hand as well. The link they hold already says which board it
    is - an aggregator's "original job post" goes to /embed/job_app?for=<slug>
    - so reading the slug turns one paste into every posting that employer
    publishes, taken from their own board.

    Tracking parameters and all, because that is how the link arrives.
    """
    url = ("https://job-boards.greenhouse.io/embed/job_app?for=contosohealth"
           "&jr_id=6a7f3d93927c79391ad0745e&token=8499256002&utm_source=elsewhere")
    manual.add(con, reading_on(), url,
               fetcher=lambda u, **k: (200, PAGE, u))

    row = con.execute(
        "SELECT ats, ats_ref, probe_status FROM companies "
        "ORDER BY id DESC LIMIT 1").fetchone()
    assert row["ats"] == "greenhouse"
    assert row["ats_ref"] == "contosohealth"
    # What every other screen reads to say an employer can be collected from.
    # Left as "added by hand" it would report this one as unreadable while
    # holding a working board.
    assert row["probe_status"] == "yielding"


def test_a_link_that_names_no_board_records_none(con):
    """The other direction, and it matters more than it looks. A guess here
    would point the collector at somebody else's slug and file their postings
    under this employer - so no fingerprint means no board, and the behaviour
    is exactly what it was before."""
    manual.add(con, reading_on(), "https://careers.example.com/roles/17",
               fetcher=lambda u, **k: (200, PAGE, u))
    row = con.execute(
        "SELECT ats, ats_ref, probe_status FROM companies "
        "ORDER BY id DESC LIMIT 1").fetchone()
    assert not row["ats"]
    assert not row["ats_ref"]
    assert row["probe_status"] == "added by hand"


def test_it_ships_off_and_off_means_nothing_is_read(home):
    """Shipped default, decided 2026-08-08: it goes out off because of what an
    author publishes, not because the behaviour is indefensible.

    Off means nothing on this path is read - not "read the ordinary sites but
    not the restricted ones", which is what the OLD name implied while the
    switch only gated one host. The name says what it does now."""
    con = db.connect(home)
    cfg = config.defaults()
    assert cfg["fetch"]["read_added_links"] is False

    called = []

    def spy(url, **kwargs):
        called.append(url)
        return 200, LINKEDIN_PAGE, url

    for url in ("https://www.linkedin.com/jobs/view/1",
                "https://boards.greenhouse.io/x/jobs/1"):
        result = manual.add(con, cfg, url, title="Typed By Hand", fetcher=spy)
        assert result["fetched"] is False, url
        assert result["title"] == "Typed By Hand"
    assert called == [], "nothing was requested from anywhere"
    con.close()


# ---- the link is the identity ------------------------------------------

def test_tracking_parameters_do_not_create_a_second_copy():
    """A posting URL copied out of an email carries parameters that differ
    every time. Keying on them would fill the list with duplicates of one
    job."""
    a = manual.stable_id("https://nodesk.co/remote-jobs/acme-analyst/?utm_source=email")
    b = manual.stable_id("https://nodesk.co/remote-jobs/acme-analyst/")
    assert a == b


def test_a_trailing_slash_is_not_a_different_job():
    assert (manual.stable_id("https://x.co/jobs/1/")
            == manual.stable_id("https://x.co/jobs/1"))


# ---- adding ------------------------------------------------------------

def test_a_readable_posting_fills_itself_in(home):
    con = db.connect(home)
    result = manual.add(con, reading_on(),
                        "https://nodesk.co/remote-jobs/acme-analyst/",
                        fetcher=make_fetcher({"https://nodesk.co/remote-jobs/acme-analyst/": PAGE}))
    assert result["fetched"] is True
    assert result["title"] == "Support Analyst"
    assert result["company"] == "Acme Corp"
    assert result["has_description"] is True
    con.close()


def test_a_linkedin_link_is_kept_with_what_the_person_typed(home):
    """The whole point: the job is tracked, and nothing was requested from
    LinkedIn to do it."""
    con = db.connect(home)
    url = "https://www.linkedin.com/jobs/view/4271234567"
    result = manual.add(con, config.defaults(), url,
                        title="Application Support Analyst", company="Some Employer",
                        fetcher=make_fetcher({}))
    assert result["fetched"] is False
    assert result["title"] == "Application Support Analyst"
    row = con.execute("SELECT url, source FROM jobs WHERE key = ?",
                      (result["key"],)).fetchone()
    assert row["url"] == url
    assert row["source"] == "manual"
    con.close()


def test_what_the_person_typed_beats_what_the_page_said(home):
    """They are looking at the posting; we are guessing from markup."""
    con = db.connect(home)
    result = manual.add(con, reading_on(),
                        "https://nodesk.co/remote-jobs/acme-analyst/",
                        title="Service Desk Technician", company="Acme Subsidiary",
                        fetcher=make_fetcher({"https://nodesk.co/remote-jobs/acme-analyst/": PAGE}))
    assert result["title"] == "Service Desk Technician"
    assert result["company"] == "Acme Subsidiary"
    con.close()


def test_a_title_is_required_when_the_page_cannot_be_read(home):
    con = db.connect(home)
    with pytest.raises(ValueError, match="title"):
        manual.add(con, config.defaults(),
                   "https://www.linkedin.com/jobs/view/1", fetcher=make_fetcher({}))
    con.close()


def test_a_hand_added_job_is_never_dropped_by_screening(home):
    """Pasting a link is a person saying they are interested. It is not a
    screening decision, so the title filter does not get to remove it - the
    reasons are still recorded and still shown."""
    cfg = config.defaults()
    cfg["search"]["title_include"] = ["Carpenter"]
    con = db.connect(home)
    result = manual.add(con, cfg, "https://www.linkedin.com/jobs/view/1",
                        title="Support Analyst", company="Acme",
                        fetcher=make_fetcher({}))
    row = con.execute("SELECT qualified, screen_reasons FROM jobs WHERE key = ?",
                      (result["key"],)).fetchone()
    assert row["qualified"] == 1
    assert "title matches none" in row["screen_reasons"]
    con.close()


def test_adding_the_same_link_twice_updates_one_row(home):
    con = db.connect(home)
    url = "https://www.linkedin.com/jobs/view/1"
    manual.add(con, config.defaults(), url, title="Support Analyst",
               fetcher=make_fetcher({}))
    manual.add(con, config.defaults(), url, title="Support Analyst II",
               fetcher=make_fetcher({}))
    rows = con.execute("SELECT title FROM jobs WHERE source = 'manual'").fetchall()
    assert len(rows) == 1
    assert rows[0]["title"] == "Support Analyst II"
    con.close()


def test_a_blank_link_is_refused(home):
    con = db.connect(home)
    for bad in ("", "   "):
        with pytest.raises(ValueError, match="link"):
            manual.add(con, config.defaults(), bad, title="x",
                       fetcher=make_fetcher({}))
    con.close()


def test_a_collect_never_marks_a_hand_added_job_taken_down(home):
    """If they added a job at an employer whose board we also read, it
    shares that company_id - and would otherwise be struck through the first
    time that board was collected."""
    con = db.connect(home)
    result = manual.add(con, config.defaults(), "https://www.linkedin.com/jobs/view/1",
                        title="Support Analyst", company="Acme Corp",
                        fetcher=make_fetcher({}))
    company_id = con.execute("SELECT id FROM companies WHERE name = 'Acme Corp'").fetchone()["id"]
    db.mark_delisted(con, company_id, "2099-01-01T00:00:00")
    row = con.execute("SELECT delisted_at FROM jobs WHERE key = ?",
                      (result["key"],)).fetchone()
    assert row["delisted_at"] is None
    con.close()
