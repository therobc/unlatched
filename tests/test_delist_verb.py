"""Recording a closure that something else detected.

A collector author owns closure detection for the rows their collector
gathers, because Unlatched re-detecting them would mean fetching the same site
again on a schedule - a second automated reader for no new information, which is the rule
an earlier change asked to be enforced. So this app is TOLD the answer.

The property that matters: a closure can never overwrite what the person
recorded. delisted_at is a column on the job, not a status value.
"""
from __future__ import annotations

from unlatched import db, manual, status


def add_job(con, cfg, url="https://boards.greenhouse.io/acme/jobs/1"):
    return manual.add(con, cfg, url, title="Support Analyst", no_fetch=True)["key"]


def delist(con, key, at="2026-08-08T12:00:00"):
    con.execute("UPDATE jobs SET delisted_at = ? WHERE key = ?", (at, key))
    con.commit()


def relist(con, key):
    con.execute("UPDATE jobs SET delisted_at = NULL WHERE key = ?", (key,))
    con.commit()


def test_a_closure_never_overwrites_what_the_person_recorded(con, cfg):
    """The one that matters. Somebody who applied and then sees the posting
    close must still show as having applied - that history is the whole value
    of the tracker, and it is what was lost when status lived in one place."""
    key = add_job(con, cfg)
    status.set_status(con, key, "applied")

    delist(con, key)

    assert con.execute(
        "SELECT status FROM job_status WHERE key = ?", (key,)).fetchone()["status"] == "applied"
    assert con.execute(
        "SELECT delisted_at FROM jobs WHERE key = ?", (key,)).fetchone()["delisted_at"]
    # And the append-only history is untouched by a fact about the employer.
    assert con.execute(
        "SELECT COUNT(*) FROM job_status_log WHERE key = ?", (key,)).fetchone()[0] == 1


def test_a_posting_that_comes_back_is_not_left_struck_through(con, cfg):
    """Postings reappear - boards go briefly empty during edits."""
    key = add_job(con, cfg)
    delist(con, key)
    relist(con, key)
    assert con.execute(
        "SELECT delisted_at FROM jobs WHERE key = ?", (key,)).fetchone()["delisted_at"] is None


def test_the_row_survives_a_closure(con, cfg):
    """Unlatched never deletes a job. A posting the employer pulled stays
    readable, which is the point of having applied to it."""
    key = add_job(con, cfg)
    delist(con, key)
    row = db.get_job(con, key)
    assert row is not None
    assert row["title"] == "Support Analyst"


def test_a_closure_and_a_removal_are_different_facts(con, cfg):
    """delisted_at is the employer's decision; retired_at is the reader's.
    Collapsing them would let a collect resurrect something thrown away, or a
    removal look like the job had closed."""
    key = add_job(con, cfg)
    delist(con, key)
    db.retire(con, [key], at="2026-08-08T13:00:00")

    row = db.get_job(con, key)
    assert row["delisted_at"] == "2026-08-08T12:00:00"
    assert row["retired_at"] == "2026-08-08T13:00:00"
    # Restoring is about the reader's decision only and leaves the fact alone.
    db.restore(con, [key])
    row = db.get_job(con, key)
    assert row["retired_at"] is None
    assert row["delisted_at"] == "2026-08-08T12:00:00"
