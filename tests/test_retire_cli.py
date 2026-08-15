"""Removing rows from outside the app.

Retirement existed in the database and in the multi-select, and had NO command,
so the only way to remove a row was by hand in the app. The collector author asked whether
`forget-company` was the right instrument for 26 rows from blacklisted
reposters; it is not, and there was nothing else to point her at.

The tests that matter here are the ones asserting what retirement must NOT do.
A bulk removal driven by another program is one wrong argument away from hiding
a job somebody applied to.
"""
from __future__ import annotations

import argparse

from unlatched import cli, db, status


def add(con, key, company="RemoteHunter", title="Support"):
    cid = db.upsert_company(con, company)
    db.upsert_job(con, key, {"company_id": cid, "title": title,
                             "url": f"https://example.com/{key}",
                             "source": "imported", "qualified": 1,
                             "verdict": "keep"})


def run(home, **kw):
    args = argparse.Namespace(home=home, key=[], company=None, back=False,
                              dry_run=False, json=True)
    for k, v in kw.items():
        setattr(args, k, v)
    return cli.cmd_retire(args)


def test_a_whole_employer_can_be_retired_in_one_call(con, home):
    """What the collector author needs: 26 rows across eight poster names, without her having
    to send 26 keys or reach into the database."""
    add(con, "imported:1")
    add(con, "imported:2")
    add(con, "imported:3", company="Real Employer")
    con.commit()

    assert run(home, company="RemoteHunter") == 0

    fresh = db.connect(home)
    assert db.get_job(fresh, "imported:1")["retired_at"]
    assert db.get_job(fresh, "imported:2")["retired_at"]
    assert not db.get_job(fresh, "imported:3")["retired_at"], \
        "a different employer must not be swept up"


def test_nothing_is_deleted(con, home):
    """HIDES, never deletes - the rule retirement has always followed. The row
    keeps its status, its append-only log and its place in the record."""
    add(con, "imported:1")
    status.set_status(con, "imported:1", "applied")
    con.commit()

    run(home, company="RemoteHunter")

    fresh = db.connect(home)
    assert db.get_job(fresh, "imported:1") is not None
    assert fresh.execute("SELECT status FROM job_status WHERE key=?",
                         ("imported:1",)).fetchone()["status"] == "applied"
    assert fresh.execute("SELECT COUNT(*) FROM job_status_log WHERE key=?",
                         ("imported:1",)).fetchone()[0] == 1


def test_it_goes_both_ways(con, home):
    add(con, "imported:1")
    con.commit()
    run(home, company="RemoteHunter")
    assert db.get_job(db.connect(home), "imported:1")["retired_at"]

    run(home, company="RemoteHunter", back=True)
    assert not db.get_job(db.connect(home), "imported:1")["retired_at"], \
        "--back must restore, or a wrong bulk call is unrecoverable"


def test_a_dry_run_changes_nothing(con, home):
    """A bulk removal is worth seeing before it happens, and the count has to
    come from the same query that would do the work - a preview computed a
    different way is a preview of something else."""
    add(con, "imported:1")
    add(con, "imported:2")
    con.commit()

    assert run(home, company="RemoteHunter", dry_run=True) == 0

    fresh = db.connect(home)
    assert not db.get_job(fresh, "imported:1")["retired_at"]
    assert not db.get_job(fresh, "imported:2")["retired_at"]


def test_an_unknown_key_stops_the_whole_call(con, home):
    """Partial application of a bulk removal is worse than none: the caller
    cannot tell which half ran."""
    add(con, "imported:1")
    con.commit()

    assert run(home, key=["imported:1", "imported:nope"]) == 1
    assert not db.get_job(db.connect(home), "imported:1")["retired_at"]


def test_an_unknown_company_retires_nothing_and_does_not_error(con, home):
    """Running the eight-poster sweep on a profile that never held one of them
    is normal, not a failure."""
    add(con, "imported:1")
    con.commit()
    assert run(home, company="NotHere") == 0
    assert not db.get_job(db.connect(home), "imported:1")["retired_at"]


def test_retiring_twice_does_not_double_count(con, home):
    """The employer selection only picks up rows not already retired, so a
    re-run of the sweep reports 0 rather than re-stamping and re-reporting."""
    add(con, "imported:1")
    con.commit()
    run(home, company="RemoteHunter")
    first = db.get_job(db.connect(home), "imported:1")["retired_at"]

    run(home, company="RemoteHunter")
    assert db.get_job(db.connect(home), "imported:1")["retired_at"] == first
