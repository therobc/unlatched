"""Removing rows from your lists, and getting them back.

The first user asked for delete with a multi-select, 2026-08-08. It is implemented as
retire-and-restore: the row leaves every list immediately, which is what was
asked for, but nothing is destroyed. A bulk action over a multi-select is one
misclick from erasing months of application history, and that history is the
entire value of a job tracker.
"""
from __future__ import annotations

from unlatched import db, status


def add_job(con, key: str, title: str = "Support Analyst") -> None:
    cid = db.upsert_company(con, "Example")
    db.upsert_job(con, key, {"company_id": cid, "title": title, "qualified": 1,
                              "verdict": "keep"})


def test_a_retired_row_leaves_the_lists_but_keeps_everything(con):
    add_job(con, "gh:1")
    status.set_status(con, "gh:1", "applied")

    assert db.retire(con, ["gh:1"], at="2026-08-08T10:00:00") == 1

    row = db.get_job(con, "gh:1")
    assert row is not None, "the row still exists"
    assert row["retired_at"] == "2026-08-08T10:00:00"
    assert row["title"] == "Support Analyst", "nothing about the job was lost"
    # The half that matters: their own record survives untouched.
    current = con.execute(
        "SELECT status FROM job_status WHERE key = 'gh:1'").fetchone()
    assert current["status"] == "applied"
    logged = con.execute(
        "SELECT COUNT(*) FROM job_status_log WHERE key = 'gh:1'").fetchone()[0]
    assert logged == 1


def test_restoring_puts_it_back(con):
    add_job(con, "gh:1")
    db.retire(con, ["gh:1"], at="2026-08-08T10:00:00")
    assert db.restore(con, ["gh:1"]) == 1
    assert db.get_job(con, "gh:1")["retired_at"] is None


def test_retiring_is_sticky_against_the_next_collection(con):
    """The defect this guards: a job still live on its board is re-read by
    every collect. If a collect could clear retired_at, a row somebody threw
    away would be back the next morning - and they would have to throw it away
    again, daily, forever."""
    add_job(con, "gh:1")
    db.retire(con, ["gh:1"], at="2026-08-08T10:00:00")

    cid = db.upsert_company(con, "Example")
    db.upsert_job(con, "gh:1", {
        "company_id": cid,
        "title": "Support Analyst",
        "description": "re-read from the board",
        "qualified": 1,
    })

    row = db.get_job(con, "gh:1")
    assert row["retired_at"] == "2026-08-08T10:00:00", "still hidden"
    assert row["description"] == "re-read from the board", "still updated"


def test_retiring_twice_does_not_move_the_date(con):
    """The first removal is when they decided. A second pass over a selection
    that still contains it must not rewrite that."""
    add_job(con, "gh:1")
    db.retire(con, ["gh:1"], at="2026-08-08T10:00:00")
    assert db.retire(con, ["gh:1"], at="2026-08-09T10:00:00") == 0
    assert db.get_job(con, "gh:1")["retired_at"] == "2026-08-08T10:00:00"


def test_bulk_retire_reports_what_it_moved(con):
    for n in range(5):
        add_job(con, f"gh:{n}")
    assert db.retire(con, [f"gh:{n}" for n in range(5)], at="2026-08-08") == 5
    assert db.retired_count(con) == 5


def test_an_empty_selection_is_a_no_op_not_a_wipe(con):
    """`WHERE key IN ()` is a syntax error in sqlite, and the shape of code
    that gets that wrong is the shape that updates every row instead."""
    add_job(con, "gh:1")
    assert db.retire(con, [], at="2026-08-08") == 0
    assert db.restore(con, []) == 0
    assert db.get_job(con, "gh:1")["retired_at"] is None


def test_the_confirmation_can_say_how_many_were_applied_to(con):
    for n in range(4):
        add_job(con, f"gh:{n}")
    status.set_status(con, "gh:0", "applied")
    status.set_status(con, "gh:1", "interviewed")
    status.set_status(con, "gh:2", "pass")

    applied = db.applied_among(con, [f"gh:{n}" for n in range(4)])
    assert sorted(applied) == ["gh:0", "gh:1"], "pass is not an application"


def test_a_job_denied_after_applying_still_counts_as_applied_to(con):
    """Reads the append-only log, not the current status. Somebody clearing
    out rejections is removing exactly the rows worth warning about."""
    add_job(con, "gh:1")
    status.set_status(con, "gh:1", "applied")
    status.set_status(con, "gh:1", "denied")
    assert db.applied_among(con, ["gh:1"]) == ["gh:1"]


def test_a_key_that_does_not_exist_is_ignored(con):
    add_job(con, "gh:1")
    assert db.retire(con, ["gh:1", "gh:missing"], at="2026-08-08") == 1
    assert db.applied_among(con, ["gh:missing"]) == []
