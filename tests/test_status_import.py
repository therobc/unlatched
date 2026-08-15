"""status import/export round-trip, exercised against the exact shape the
browser dashboard this package replaces actually exports:

  {"status": {key: {"status": s, "at": iso}},
   "log": [{"key": k, "from": prev_or_null, "to": new_or_null, "at": iso}],
   "exported": iso}

A log entry's "to" is what lands in job_status_log.status; a null "to"
means the status was cleared - the transition is still appended to the
log, and the job_status row is removed. Bare-string status values and the
older {"status", "note", "updated"} shape are accepted as fallbacks.
"""
from __future__ import annotations

from unlatched import db, status


def test_primary_shape_imports_status_and_log():
    export = {
        "status": {
            "greenhouse:1": {"status": "applied", "at": "2026-01-01T00:00:00+00:00"},
        },
        "log": [
            {"key": "greenhouse:1", "from": None, "to": "interviewed",
             "at": "2026-01-01T00:00:00+00:00"},
            {"key": "greenhouse:1", "from": "interviewed", "to": "applied",
             "at": "2026-01-02T00:00:00+00:00"},
        ],
        "exported": "2026-01-03T00:00:00+00:00",
    }
    conn = db.connect_at(":memory:")
    result = status.import_status(conn, export)
    # note_rows is 0 rather than absent: an export written before notes existed
    # has no "notes" key, which is not an error and not a different outcome
    # from having none.
    assert result == {"status_rows": 1, "log_rows": 2, "note_rows": 0}

    row = status.get_status(conn, "greenhouse:1")
    assert row["status"] == "applied"

    log_rows = status.log_for(conn, "greenhouse:1")
    assert [r["status"] for r in log_rows] == ["interviewed", "applied"]
    conn.close()


def test_null_to_clears_the_status_and_still_appends_to_the_log():
    conn = db.connect_at(":memory:")
    status.set_status(conn, "greenhouse:1", "applied")
    assert status.get_status(conn, "greenhouse:1") is not None

    export = {
        "status": {},  # the key is absent - it was cleared, this is current
        "log": [
            {"key": "greenhouse:1", "from": "applied", "to": None,
             "at": "2026-01-05T00:00:00+00:00"},
        ],
    }
    result = status.import_status(conn, export)
    assert result["log_rows"] == 1
    assert status.get_status(conn, "greenhouse:1") is None

    log_rows = status.log_for(conn, "greenhouse:1")
    assert log_rows[-1]["status"] is None
    conn.close()


def test_status_dict_is_authoritative_over_a_stale_log_tail():
    """The log is replayed first, the status dict applied on top - so even
    if the log's last entry for a key says one thing, the dict (the current
    snapshot) decides the final row."""
    conn = db.connect_at(":memory:")
    export = {
        "status": {"greenhouse:1": {"status": "applied", "at": "2026-01-02T00:00:00+00:00"}},
        "log": [
            {"key": "greenhouse:1", "from": None, "to": "applied",
             "at": "2026-01-01T00:00:00+00:00"},
            {"key": "greenhouse:1", "from": "applied", "to": None,
             "at": "2026-01-02T00:00:00+00:00"},
        ],
    }
    status.import_status(conn, export)
    row = status.get_status(conn, "greenhouse:1")
    assert row is not None
    assert row["status"] == "applied"
    conn.close()


def test_export_reconstructs_from_transitions_and_round_trips():
    conn = db.connect_at(":memory:")
    db.upsert_job(conn, "greenhouse:1", {"title": "Support Analyst", "qualified": 1})
    status.set_status(conn, "greenhouse:1", "applied")
    status.set_status(conn, "greenhouse:1", "interviewed")

    exported = status.export_status(conn)
    assert exported["status"]["greenhouse:1"]["status"] == "interviewed"
    transitions = [e for e in exported["log"] if e["key"] == "greenhouse:1"]
    assert transitions[0]["from"] is None
    assert transitions[0]["to"] == "applied"
    assert transitions[1]["from"] == "applied"
    assert transitions[1]["to"] == "interviewed"

    fresh = db.connect_at(":memory:")
    db.upsert_job(fresh, "greenhouse:1", {"title": "Support Analyst", "qualified": 1})
    status.import_status(fresh, exported)
    assert status.get_status(fresh, "greenhouse:1")["status"] == "interviewed"
    conn.close()
    fresh.close()


def test_legacy_status_note_updated_shape_is_still_accepted():
    conn = db.connect_at(":memory:")
    export = {
        "status": {"ex:31": {"status": "pass", "note": "not a fit",
                              "updated": "2026-01-01T00:00:00+00:00"}},
        "log": [],
    }
    status.import_status(conn, export)
    row = status.get_status(conn, "ex:31")
    assert row["status"] == "pass"
    assert row["note"] == "not a fit"
    conn.close()
