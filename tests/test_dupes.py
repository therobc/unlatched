"""The same job, reached from two different places.

The tests that matter here are the ones asserting what must NOT be merged. A
missed duplicate costs one wasted read; a false merge hides a job somebody
wanted and they never learn it existed.
"""
from __future__ import annotations

from unlatched import db, dupes


def add(con, key, title, description="", apply_url="", fetched="2026-08-01",
        company="Acme", url=None):
    cid = db.upsert_company(con, company)
    # The posting URL decides which side of a pair a row is on, so the fixture
    # has to set it: a key beginning "li:" alone is not how real rows arrive.
    if url is None:
        url = ("https://www.linkedin.com/jobs/view/1" if key.startswith("li:")
               else f"https://boards.example.com/{key}")
    db.upsert_job(con, key, {
        "company_id": cid, "title": title, "description": description,
        "url": url,
        "apply_url": dupes.links_mod.normalise_apply_url(apply_url),
        "fetched_at": fetched, "qualified": 1, "verdict": "keep",
    })


def test_two_boards_pointing_at_one_application_are_one_job(con):
    add(con, "li:1", "Support Analyst", fetched="2026-08-02", company="Facet",
        apply_url="https://apply.workable.com/facet/j/ABC123/")
    add(con, "workable:1", "Support Analyst", fetched="2026-08-01", company="Facet",
        apply_url="https://apply.workable.com/facet/j/ABC123")

    found = dupes.find(con)
    assert len(found) == 1
    # LINKEDIN IS KEPT even though the board row was collected first: applying
    # there records the application in LinkedIn's own tracker, which is an
    # audit trail for free. Two applications were once recovered only because
    # that badge was still visible.
    assert found[0].key == "workable:1"
    assert found[0].duplicate_of == "li:1"


def test_a_republisher_does_not_become_the_preferred_route(con):
    """the collector author's caveat. A republisher's listing is an intermediary, not a
    better route, and for a staffing agency it carries first-touch risk - an
    employer dropping a candidate rather than settle a fee dispute over who
    sourced them. Different employer named on the two rows is the signal."""
    add(con, "li:1", "Support Analyst", fetched="2026-08-02", company="Swooped",
        apply_url="https://apply.workable.com/facet/j/ABC123/")
    add(con, "workable:1", "Support Analyst", fetched="2026-08-01", company="Facet",
        apply_url="https://apply.workable.com/facet/j/ABC123")

    found = dupes.find(con)
    assert len(found) == 1
    assert found[0].duplicate_of == "workable:1", "the employer's own posting is kept"
    assert found[0].key == "li:1"


def test_a_hand_added_job_from_any_site_is_not_the_preferred_route(con):
    """The bug the grouped-view screenshot caught. Treating every hand-added
    job as "the LinkedIn side" made both rows in a pair look identical, so the
    rule silently did nothing and the LinkedIn rows were the ones folded away.
    Identity comes from the POSTING URL."""
    add(con, "manual:1", "Analyst", fetched="2026-08-01", company="Facet",
        url="https://apply.workable.com/facet/j/ABC123/",
        apply_url="https://apply.workable.com/facet/j/ABC123/")
    add(con, "manual:2", "Analyst", fetched="2026-08-05", company="Facet",
        url="https://www.linkedin.com/jobs/view/4012345",
        apply_url="https://apply.workable.com/facet/j/ABC123/")

    found = dupes.find(con)
    assert len(found) == 1
    assert found[0].duplicate_of == "manual:2", "the LinkedIn posting is kept"


def test_a_longer_employer_name_still_counts_as_the_same_employer(con):
    """One side writes "Facet", the other "Facet Wealth, Inc." - that is not a
    republisher, and treating it as one would cost the preferred route."""
    add(con, "li:1", "Support Analyst", fetched="2026-08-02",
        company="Facet Wealth, Inc.",
        apply_url="https://apply.workable.com/facet/j/ABC123/")
    add(con, "workable:1", "Support Analyst", fetched="2026-08-01", company="Facet",
        apply_url="https://apply.workable.com/facet/j/ABC123")

    assert dupes.find(con)[0].duplicate_of == "li:1"


def test_three_rows_on_one_application_all_fold_onto_the_same_keeper(con):
    """Not a chain. If A points at B and B is itself folded away, the person
    following the group lands on something that is not in any list."""
    add(con, "li:1", "Support Analyst", fetched="2026-08-03", company="Facet",
        apply_url="https://apply.workable.com/facet/j/ABC123/")
    add(con, "workable:1", "Support Analyst", fetched="2026-08-01", company="Facet",
        apply_url="https://apply.workable.com/facet/j/ABC123")
    add(con, "greenhouse:1", "Support Analyst", fetched="2026-08-02", company="Facet",
        apply_url="https://apply.workable.com/facet/j/ABC123?utm_source=x")

    found = dupes.find(con)
    assert len(found) == 2
    assert {d.duplicate_of for d in found} == {"li:1"}


def test_tracking_parameters_do_not_hide_a_match(con):
    add(con, "a:1", "Analyst", fetched="2026-08-01",
        apply_url="https://boards.greenhouse.io/x/jobs/9?gh_src=abc")
    add(con, "b:1", "Analyst", fetched="2026-08-02",
        apply_url="https://boards.greenhouse.io/x/jobs/9?utm_source=li")
    assert len(dupes.find(con)) == 1


def test_two_openings_at_one_employer_are_not_merged_by_their_apply_links(con):
    """Different jobs have different application pages, even at one employer."""
    add(con, "a:1", "Analyst", apply_url="https://jobs.example.com/apply?jobId=1")
    add(con, "b:1", "Analyst", apply_url="https://jobs.example.com/apply?jobId=2")
    assert dupes.find(con) == []


def test_easy_apply_rows_never_match_each_other(con):
    """An empty destination is a real answer - the application stays on the
    board - and two of them are not evidence of anything."""
    add(con, "li:1", "Analyst", description="One.")
    add(con, "li:2", "Analyst", description="Two.")
    assert dupes.find(con) == []


def sourced(con, key, title, source, url, *, apply_url="", fetched="2026-08-01",
            company="Acme"):
    """A row as a COLLECTOR writes it: a source and a posting url, no apply_url.
    That combination is what `add` never produces and what the board is made of.
    """
    cid = db.upsert_company(con, company)
    db.upsert_job(con, key, {
        "company_id": cid, "title": title, "source": source, "url": url,
        "apply_url": dupes.links_mod.normalise_apply_url(apply_url),
        "fetched_at": fetched, "qualified": 1, "verdict": "keep",
    })


def test_every_listed_source_exists():
    """A name here only does something if a collector emits it.

    The first version of APPLICATION_IS_THE_POSTING listed jobvite, personio and
    teamtailor. None of them is a collector in this app - they were plausible ATS
    names written instead of read off the registry, and they would have sat there
    looking like coverage while matching nothing, forever, silently.
    """
    from unlatched import sources

    known = set(sources.registry())
    listed = set(dupes.APPLICATION_IS_THE_POSTING)
    assert listed <= known, f"no collector emits: {sorted(listed - known)}"


def test_the_aggregating_sources_are_deliberately_absent():
    """Locks in the exclusion so a later 'completeness' pass cannot quietly add
    them. An aggregator's posting URL is not an application page - folding on it
    would join two unrelated employers through a reposter, which is the
    first-touch hazard the collector author flagged from the other direction.
    """
    for aggregator in ("nodesk", "remoteok", "usajobs"):
        assert aggregator not in dupes.APPLICATION_IS_THE_POSTING


def test_an_employers_own_careers_page_counts_as_its_application_page(con):
    """the collector author's third category: 31 rows across 24 single-tenant hosts, which no
    named HOST list could ever cover. Keying on the source does - however many
    employers there are, they arrive through schema_org or sitemap.
    """
    sourced(con, "schema_org:stripe-1", "Support Engineer", "schema_org",
            "https://stripe.com/jobs/listing/support-engineer/1", company="Stripe")
    sourced(con, "manual:li-9", "Support Engineer", "imported",
            "https://www.linkedin.com/jobs/view/9",
            apply_url="https://stripe.com/jobs/listing/support-engineer/1",
            fetched="2026-08-02", company="Stripe")

    found = dupes.find(con)
    assert len(found) == 1
    assert found[0].key == "schema_org:stripe-1"


def test_a_collected_ats_row_joins_the_linkedin_row_for_the_same_requisition(con):
    """The case an earlier change exists for, and could not actually do.

 apply_url is written only by `add` and `import`, so on a real board 166
    rows of 7,484 had one and every single one was on the LinkedIn side - the
    join had one side and found nothing. Measured 2026-08-09: 11 genuine pairs
    were sitting there invisible, this being their shape.
    """
    sourced(con, "greenhouse:6123566004", "Community Manager", "greenhouse",
            "https://job-boards.greenhouse.io/assetliving/jobs/6123566004",
            fetched="2026-08-01", company="Asset Living")
    sourced(con, "manual:li-4446954895", "Community Manager", "imported",
            "https://www.linkedin.com/jobs/view/4446954895",
            apply_url="https://job-boards.greenhouse.io/assetliving/jobs/6123566004",
            fetched="2026-08-02", company="Asset Living")

    found = dupes.find(con)
    assert len(found) == 1
    # LinkedIn is kept, because applying there records it in LinkedIn's own
    # tracker. The board row is the one folded away.
    assert found[0].key == "greenhouse:6123566004"
    assert found[0].duplicate_of == "manual:li-4446954895"


def test_two_different_postings_on_one_ats_are_never_merged(con):
    """The over-fire direction, which is the expensive one. Two requisitions at
    one employer have different posting URLs and must stay two jobs."""
    sourced(con, "greenhouse:1", "Analyst", "greenhouse",
            "https://job-boards.greenhouse.io/acme/jobs/1")
    sourced(con, "greenhouse:2", "Analyst", "greenhouse",
            "https://job-boards.greenhouse.io/acme/jobs/2")
    assert dupes.find(con) == []


def test_a_source_not_on_the_list_contributes_no_destination(con):
    """The list is a claim that the page carries the application form. An
    aggregator forwards elsewhere, so its posting URL is not a destination and
    inferring one would fold a real employer route into an intermediary.
    """
    sourced(con, "aggregator:1", "Analyst", "remotehunter",
            "https://www.remotehunter.com/apply-with/1")
    sourced(con, "aggregator:2", "Analyst", "remotehunter",
            "https://www.remotehunter.com/apply-with/1")
    assert dupes.find(con) == [], \
        "an unlisted source must not have its posting url read as an apply page"


def test_easy_apply_still_matches_nothing_now_that_urls_can_stand_in(con):
    """The guard that could have been broken by this change. An imported
    LinkedIn row with no apply_url stays on the board, and `imported` is
    deliberately absent from APPLICATION_IS_THE_POSTING - so its linkedin.com
    URL must not become a destination that something else can join.
    """
    sourced(con, "manual:li-1", "Analyst", "imported",
            "https://www.linkedin.com/jobs/view/1")
    sourced(con, "manual:li-2", "Analyst", "imported",
            "https://www.linkedin.com/jobs/view/2")
    assert dupes.find(con) == []


def test_a_stated_destination_beats_an_inferred_one(con):
    """A row that was TOLD where the application happens is not second-guessed
    from its own URL."""
    sourced(con, "greenhouse:1", "Analyst", "greenhouse",
            "https://job-boards.greenhouse.io/acme/jobs/1",
            apply_url="https://apply.workable.com/acme/j/REAL")
    sourced(con, "manual:li-1", "Analyst", "imported",
            "https://www.linkedin.com/jobs/view/1",
            apply_url="https://apply.workable.com/acme/j/REAL", fetched="2026-08-02")

    found = dupes.find(con)
    assert len(found) == 1, "the stated Workable destination should have joined them"


def test_the_description_path_is_off_unless_asked_for(con):
    """THE FINDING behind this default. Measured on a real 7,189-row corpus:
    hundreds of pairs score 1.000 while being separate jobs, because one
    employer writes one description and posts it at every branch."""
    shared = "Serve clients at our branch. Handle transactions. " * 20
    add(con, "wd:wyomissing", "Wealth Management Client Associate", shared)
    add(con, "wd:york", "Wealth Management Client Associate", shared)

    assert dupes.find(con) == [], "identical branch postings are separate jobs"
    # Available deliberately, for a corpus where it is safe.
    assert len(dupes.find(con, use_descriptions=True)) == 1


def test_a_seniority_difference_disqualifies_a_text_match(con):
    """Level I and level II share most of their text and are separate reqs -
    12 such pairs were correctly NOT linked in the validation corpus."""
    shared = "Do the work described here in detail. " * 20
    add(con, "a:1", "Claims Specialist I", shared)
    add(con, "a:2", "Claims Specialist II", shared)
    assert dupes.find(con, use_descriptions=True) == []


def test_grouping_hides_but_never_deletes(con):
    add(con, "li:1", "Analyst", fetched="2026-08-02",
        apply_url="https://apply.example.com/j/1")
    add(con, "ats:1", "Analyst", fetched="2026-08-01",
        apply_url="https://apply.example.com/j/1")

    dupes.apply(con, dupes.find(con))
    row = db.get_job(con, "ats:1")
    assert row is not None, "the row still exists"
    assert row["duplicate_of"] == "li:1"
    assert row["duplicate_reason"]

    # And one call puts it back, which is what makes an over-fire recoverable.
    assert dupes.clear(con) == 1
    assert db.get_job(con, "ats:1")["duplicate_of"] is None


def test_a_job_already_applied_to_is_never_the_one_folded_away(con):
    """the first user, 2026-08-09. Apply through the board row, then the LinkedIn row
    arrives - if LinkedIn won here, the visible row would read "not set" while
    the record of the application sat in the grouped view. That is exactly how
    the Facet and Carlisle applications were lost.

    The source preference decides which ROUTE to apply through. It stops
    mattering the moment an application exists.
    """
    from unlatched import status

    add(con, "workable:1", "Support Analyst", fetched="2026-08-01", company="Facet",
        apply_url="https://apply.workable.com/facet/j/ABC123")
    status.set_status(con, "workable:1", "applied")
    add(con, "li:1", "Support Analyst", fetched="2026-08-05", company="Facet",
        apply_url="https://apply.workable.com/facet/j/ABC123/")

    found = dupes.find(con)
    assert len(found) == 1
    assert found[0].duplicate_of == "workable:1", "the row carrying the application is kept"
    assert found[0].key == "li:1"

    # And after grouping, the visible row still shows the application.
    dupes.apply(con, found)
    visible = con.execute(
        "SELECT j.key, s.status FROM jobs j "
        "LEFT JOIN job_status s ON s.key = j.key "
        "WHERE j.duplicate_of IS NULL AND j.key IN ('li:1','workable:1')").fetchall()
    assert [(r["key"], r["status"]) for r in visible] == [("workable:1", "applied")]


def test_a_denied_job_still_counts_as_acted_on(con):
    """A job applied to and later denied still represents an application, so
    its row is still the one worth keeping visible."""
    from unlatched import status

    add(con, "workable:1", "Analyst", fetched="2026-08-01", company="Facet",
        apply_url="https://apply.workable.com/facet/j/ABC")
    status.set_status(con, "workable:1", "applied")
    status.set_status(con, "workable:1", "denied")
    add(con, "li:1", "Analyst", fetched="2026-08-05", company="Facet",
        apply_url="https://apply.workable.com/facet/j/ABC")

    assert dupes.find(con)[0].duplicate_of == "workable:1"


def test_running_it_twice_does_not_report_the_same_pair_again(con):
    """A caller sweeping after every import needs "1 found" to mean one NEW
    one. Re-reporting settled pairs makes the number meaningless."""
    add(con, "li:1", "Analyst", fetched="2026-08-02",
        apply_url="https://apply.example.com/j/1")
    add(con, "ats:1", "Analyst", fetched="2026-08-01",
        apply_url="https://apply.example.com/j/1")

    dupes.apply(con, dupes.find(con))
    assert dupes.find(con) == [], "already-grouped rows are settled"

    # Ungrouping puts it back in scope, so a mistake can be re-examined.
    dupes.clear(con)
    assert len(dupes.find(con)) == 1


def test_a_retired_row_is_not_dragged_into_a_group(con):
    """Somebody threw it away; it should not reappear as a duplicate of
    something else."""
    add(con, "li:1", "Analyst", apply_url="https://apply.example.com/j/1")
    add(con, "ats:1", "Analyst", apply_url="https://apply.example.com/j/1")
    db.retire(con, ["li:1"], at="2026-08-08T10:00:00")
    assert dupes.find(con) == []


def test_boilerplate_alone_is_not_similarity(con):
    """EEO and benefits blocks are identical across every posting at an
    employer, so leaving them in would inflate every score toward every other."""
    boiler = ("We are an equal opportunity employer. Reasonable accommodation "
              "is available. Benefits include medical and 401(k). ")
    add(con, "a:1", "Analyst", boiler + "Manage the ledger and close the books.")
    add(con, "a:2", "Analyst", boiler + "Drive the forklift and load pallets.")
    assert dupes.find(con, use_descriptions=True) == []


# ------------------------------------------- a repost is not a duplicate ----
#
# Decided 2026-08-12: "If a job gets reposted more than 4 weeks later, treat it as
# a new entry and link the original job or group that it originated from."
#
# Grouping here is exact and was date-blind, so an employer who re-advertises a
# seat and reuses the apply URL would have had the NEW opening folded behind
# the old round - and the hidden one is the live one.

def add_dated(con, key, posted, apply_url, **kwargs):
    add(con, key, "Support Analyst", apply_url=apply_url, company="Facet",
        **kwargs)
    con.execute("UPDATE jobs SET posted_at = ? WHERE key = ?", (posted, key))
    con.commit()


APPLY = "https://apply.workable.com/facet/j/ABC123/"


def test_a_seat_advertised_again_months_later_stays_its_own_entry(con):
    add_dated(con, "workable:1", "2026-01-10", APPLY, fetched="2026-01-10")
    add_dated(con, "workable:2", "2026-06-01", APPLY, fetched="2026-06-01")

    assert dupes.find(con) == []


def test_but_the_same_posting_seen_twice_in_a_week_is_still_one_job(con):
    """THE POSITIVE CONTROL. Same fixture, same apply page, dates close
    together - if this did not group, the test above would be passing because
    duplicate detection was broken rather than because the rule fired."""
    add_dated(con, "workable:1", "2026-06-01", APPLY, fetched="2026-06-01")
    add_dated(con, "workable:2", "2026-06-04", APPLY, fetched="2026-06-04")

    assert len(dupes.find(con)) == 1


def test_rows_with_no_posting_date_are_grouped_as_before(con):
    """"No date" says nothing about when a posting ran. Reading it as "long
    ago" would stop grouping the ordinary same-day duplicates this module
    exists for - and most boards do supply a date, so the ones that do not are
    exactly where an unguarded assumption would do the most damage."""
    add(con, "workable:1", "Support Analyst", apply_url=APPLY, company="Facet")
    add(con, "workable:2", "Support Analyst", apply_url=APPLY, company="Facet")

    assert len(dupes.find(con)) == 1
