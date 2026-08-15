"""The scheduled run collects AND groups, not just collects.

The first user called it "the daily refresh and dedup" (2026-08-09), which is what it
should be and was not: `refresh` ran `collect` and stopped, so duplicates were
grouped only when somebody typed `dedupe --apply` or an import passed --dedupe.
A board that regroups only when asked shows the same job twice every morning.

The tests here assert the two halves that would be SILENT if wrong: that the
grouping happens at all, and that it stays on the exact path. A scheduled run
that quietly started fuzzy-matching would fold separate jobs together and
nobody would see the ones that disappeared.
"""
from __future__ import annotations

import argparse

from unlatched import cli, db, dupes


def add(con, key, title, *, apply_url="", description="", company="Acme",
        fetched="2026-08-01", url=None):
    cid = db.upsert_company(con, company)
    db.upsert_job(con, key, {
        "company_id": cid, "title": title, "description": description,
        "url": url or f"https://boards.example.com/{key}",
        "apply_url": dupes.links_mod.normalise_apply_url(apply_url),
        "fetched_at": fetched, "qualified": 1, "verdict": "keep",
    })


def args_for(home):
    return argparse.Namespace(home=home, json=False)


def test_two_boards_on_one_application_are_grouped_without_anybody_asking(con, home):
    add(con, "li:1", "Support Analyst", company="Facet", fetched="2026-08-02",
        apply_url="https://apply.workable.com/facet/j/ABC123/",
        url="https://www.linkedin.com/jobs/view/1")
    add(con, "workable:1", "Support Analyst", company="Facet",
        fetched="2026-08-01", apply_url="https://apply.workable.com/facet/j/ABC123")
    con.commit()

    cli.group_new_duplicates(args_for(home))

    row = db.get_job(db.connect(home), "workable:1")
    assert row["duplicate_of"] == "li:1", \
        "the scheduled run collected the pair and left them ungrouped"


def test_the_scheduled_run_never_fuzzy_matches(con, home):
    """The measured reason, not a preference: on 7,189 real rows the description
    path produced hundreds of pairs scoring 1.000 that are SEPARATE jobs, because
    one employer writes one description and posts it at every branch. Grouping
    those unattended would hide real openings.

    Two postings with IDENTICAL text and no apply destination must survive.
    """
    shared = "We are looking for a motivated professional to join our team. " * 12
    add(con, "board:branch-a", "Relationship Banker", description=shared,
        company="Westbank")
    add(con, "board:branch-b", "Relationship Banker", description=shared,
        company="Westbank")
    con.commit()

    cli.group_new_duplicates(args_for(home))

    fresh = db.connect(home)
    assert db.get_job(fresh, "board:branch-a")["duplicate_of"] is None
    assert db.get_job(fresh, "board:branch-b")["duplicate_of"] is None


def test_a_job_already_applied_to_is_never_the_row_that_disappears(con, home):
    """The keeper rule has to hold on the UNATTENDED path too. Nobody is
    watching a scheduled run, so this is precisely where folding away a job
    somebody applied to would go unnoticed.
    """
    from unlatched import status

    add(con, "workable:1", "Support Analyst", company="Facet",
        fetched="2026-08-01", apply_url="https://apply.workable.com/facet/j/ABC123")
    status.set_status(con, "workable:1", "applied")
    add(con, "li:1", "Support Analyst", company="Facet", fetched="2026-08-02",
        apply_url="https://apply.workable.com/facet/j/ABC123/",
        url="https://www.linkedin.com/jobs/view/1")
    con.commit()

    cli.group_new_duplicates(args_for(home))

    fresh = db.connect(home)
    assert db.get_job(fresh, "workable:1")["duplicate_of"] is None, \
        "the applied-to row was folded away by an unattended run"
    assert db.get_job(fresh, "li:1")["duplicate_of"] == "workable:1"


def test_grouping_hides_but_never_deletes(con, home):
    add(con, "li:1", "Support Analyst", company="Facet", fetched="2026-08-02",
        apply_url="https://apply.workable.com/facet/j/ABC123/",
        url="https://www.linkedin.com/jobs/view/1")
    add(con, "workable:1", "Support Analyst", company="Facet",
        fetched="2026-08-01", apply_url="https://apply.workable.com/facet/j/ABC123")
    con.commit()

    cli.group_new_duplicates(args_for(home))

    fresh = db.connect(home)
    assert db.get_job(fresh, "workable:1") is not None
    assert db.get_job(fresh, "li:1") is not None


def test_the_refresh_command_is_what_triggers_the_grouping(con, home):
    """The load-bearing test, and the reason the others are not enough.

    Every test above calls group_new_duplicates directly, so all of them would
    still pass if the call were removed from cmd_refresh - the function would
    work perfectly and never run. That is the regression this whole change
    exists to prevent, so it is asserted through the COMMAND.
    """
    add(con, "li:1", "Support Analyst", company="Facet", fetched="2026-08-02",
        apply_url="https://apply.workable.com/facet/j/ABC123/",
        url="https://www.linkedin.com/jobs/view/1")
    add(con, "workable:1", "Support Analyst", company="Facet",
        fetched="2026-08-01", apply_url="https://apply.workable.com/facet/j/ABC123")
    con.commit()

    # --force so the anchor times play no part: this is about what refresh DOES
    # once it has decided to run, not about when it decides to.
    code = cli.cmd_refresh(argparse.Namespace(
        home=home, json=True, force=True, check=False))

    assert code == 0
    assert db.get_job(db.connect(home), "workable:1")["duplicate_of"] == "li:1"


def test_the_refresh_command_also_rebalances_a_group_whose_keeper_closed(con, home):
    """Same control, for the other half. rebalance() has its own tests, and all
    of them would pass with the call missing from cmd_refresh.

    This is the pass that matters most during a week of real applying: the
    collect itself is what marks a posting delisted, so the run that closes a
    kept row is the run that must hand over to the one still open.
    """
    add(con, "li:1", "Support Analyst", company="Facet", fetched="2026-08-02",
        apply_url="https://apply.workable.com/facet/j/ABC123/",
        url="https://www.linkedin.com/jobs/view/1")
    add(con, "workable:1", "Support Analyst", company="Facet",
        fetched="2026-08-01", apply_url="https://apply.workable.com/facet/j/ABC123")
    con.commit()
    cli.group_new_duplicates(args_for(home))

    # The kept LinkedIn ad comes down; the ATS requisition stays live.
    con.execute("UPDATE jobs SET delisted_at = ? WHERE key = ?",
                ("2026-08-09T10:00:00", "li:1"))
    con.commit()

    cli.cmd_refresh(argparse.Namespace(home=home, json=True, force=True,
                                       check=False))

    fresh = db.connect(home)
    assert db.get_job(fresh, "workable:1")["duplicate_of"] is None, \
        "the still-open route stayed hidden behind a closed posting"
    assert db.get_job(fresh, "li:1")["duplicate_of"] == "workable:1"


def test_a_failure_to_group_never_fails_the_collection(home):
    """A refresh that collected has done the thing the person is waiting for.
    Reporting that as a failure because the grouping step broke would send
    somebody looking for a collection problem that does not exist.
    """
    broken = argparse.Namespace(home=home / "does-not-exist" / "nested", json=True)
    cli.group_new_duplicates(broken)  # must not raise
