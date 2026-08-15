"""A status has to attach to a stable `source:id` key, never a
list position. The original dashboard keyed status by array index; a newly
collected posting shifts every row below it, so a status saved against an
index silently attaches to the wrong job the next time the list is
rebuilt. Import (and every lookup) must go through the key alone.
"""
from __future__ import annotations

from unlatched import db, status


def test_import_attaches_by_key_regardless_of_dict_order(con):
    for key in ("greenhouse:300", "lever:9", "ashby:41"):
        db.upsert_job(con, key, {"title": "Support Analyst", "qualified": 1})

    export = {
        "status": {
            "ashby:41": {"status": "denied", "at": "2026-01-03T00:00:00+00:00"},
            "greenhouse:300": {"status": "applied", "at": "2026-01-01T00:00:00+00:00"},
            "lever:9": {"status": "interviewed", "at": "2026-01-02T00:00:00+00:00"},
        },
        "log": [
            {"key": "lever:9", "from": None, "to": "interviewed",
             "at": "2026-01-02T00:00:00+00:00"},
            {"key": "greenhouse:300", "from": None, "to": "applied",
             "at": "2026-01-01T00:00:00+00:00"},
            {"key": "ashby:41", "from": None, "to": "denied",
             "at": "2026-01-03T00:00:00+00:00"},
        ],
        "exported": "2026-01-04T00:00:00+00:00",
    }
    status.import_status(con, export)

    assert status.get_status(con, "greenhouse:300")["status"] == "applied"
    assert status.get_status(con, "lever:9")["status"] == "interviewed"
    assert status.get_status(con, "ashby:41")["status"] == "denied"


def test_status_survives_new_jobs_shifting_list_order(con):
    db.upsert_job(con, "greenhouse:1", {"title": "Support Analyst A", "qualified": 1,
                                         "score": 50.0})
    status.set_status(con, "greenhouse:1", "applied")

    before = db.list_jobs(con, qualified_only=False, include_closed=True)
    before_position = [r["key"] for r in before].index("greenhouse:1")

    # Insert new jobs that outrank the original by score, forcing it to move
    # in any position-ordered listing.
    for i in range(5):
        db.upsert_job(con, f"lever:{i}", {"title": f"New Role {i}", "qualified": 1,
                                           "score": 90.0 + i})

    after = db.list_jobs(con, qualified_only=False, include_closed=True)
    after_position = [r["key"] for r in after].index("greenhouse:1")
    assert after_position != before_position, "test setup should have moved the row"

    # The status must still be readable by the SAME key, unaffected by the
    # row having moved.
    row = status.get_status(con, "greenhouse:1")
    assert row["status"] == "applied"


def test_import_tolerates_bare_string_and_legacy_prefixed_keys(con):
    db.upsert_job(con, "li:82", {"title": "Legacy Row", "qualified": 1})
    export = {
        "status": {"li:82": "applied"},
        "log": [{"key": "li:82", "from": None, "to": "applied", "at": "2026-01-01T00:00:00+00:00"}],
    }
    result = status.import_status(con, export)
    assert result["status_rows"] == 1
    row = status.get_status(con, "li:82")
    assert row["status"] == "applied"
