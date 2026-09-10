"""A rescore pass once overwrote job_status rows - applied,
denied, closed decisions a person had already made - because machine
output and human judgement lived in the same row. A re-screen must be able
to run as many times as it likes and never move a single job_status or
job_status_log row.
"""
from __future__ import annotations

from types import SimpleNamespace

from unlatched import config, db, screen, status


def _seed_job(con, key="greenhouse:123", title="Support Analyst",
              description="This is a fully remote position. " * 5):
    db.upsert_job(con, key, {
        "title": title, "location": "Remote - United States",
        "description": description, "url": "https://example.com/job/123",
        "posted_at": "2026-01-01", "fetched_at": "2026-01-01T00:00:00",
        "qualified": 1, "score": 80.0, "screen_reasons": "",
    })


def test_rescreen_leaves_job_status_byte_identical(con):
    _seed_job(con)
    status.set_status(con, "greenhouse:123", "applied", note="sent via referral")

    before = db.snapshot_job_status(con)
    before_log = con.execute(
        "SELECT key, status, note, at FROM job_status_log ORDER BY id").fetchall()

    cfg = config.defaults()
    # A title_include that matches nothing deliberately fails every row on
    # rescreen - the point of this test is that the JOBS row changes while
    # job_status does not, not the specific reason it changed.
    cfg["search"]["title_include"] = ["Nonexistent Role Title Filter"]
    for row in con.execute("SELECT * FROM jobs").fetchall():
        pseudo = SimpleNamespace(title=row["title"], location=row["location"],
                                  description=row["description"])
        fields = screen.screen_job(pseudo, cfg)
        db.upsert_job(con, row["key"], fields)

    after = db.snapshot_job_status(con)
    after_log = con.execute(
        "SELECT key, status, note, at FROM job_status_log ORDER BY id").fetchall()

    assert [tuple(r) for r in before] == [tuple(r) for r in after]
    assert [tuple(r) for r in before_log] == [tuple(r) for r in after_log]

    # The rescreen DID change the machine columns - the point is that it
    # changed only those.
    row = db.get_job(con, "greenhouse:123")
    assert row["qualified"] == 0


def test_rescreen_runs_many_times_without_drift(con):
    _seed_job(con)
    status.set_status(con, "greenhouse:123", "interviewed")
    baseline = [tuple(r) for r in db.snapshot_job_status(con)]

    cfg = config.defaults()
    for _ in range(5):
        for row in con.execute("SELECT * FROM jobs").fetchall():
            pseudo = SimpleNamespace(title=row["title"], location=row["location"],
                                      description=row["description"])
            fields = screen.screen_job(pseudo, cfg)
            db.upsert_job(con, row["key"], fields)

    assert [tuple(r) for r in db.snapshot_job_status(con)] == baseline
