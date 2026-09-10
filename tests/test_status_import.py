"""status import/export round-trip, exercised against the exact shape the
importer accepts:

  {"status": {key: {"status": s, "at": iso}},
   "log": [{"key": k, "from": prev_or_null, "to": new_or_null, "at": iso}],
   "exported": iso}

A log entry's "to" is what lands in job_status_log.status; a null "to"
means the status was cleared - the transition is still appended to the
log, and the job_status row is removed. Bare-string status values and the
older {"status", "note", "updated"} shape are accepted as fallbacks.
"""
from __future__ import annotations

import pytest

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
    # The whole dict, not a few keys: this is the return CONTRACT, and a
    # field appearing or vanishing is what a caller building a summary line
    # off it would find out about the hard way. `log_duplicates` and
    # `note_duplicates` joined it when re-importing stopped doubling a
    # person's application history.
    assert result == {"status_rows": 1, "log_rows": 2, "note_rows": 0,
                      "log_duplicates": 0, "note_duplicates": 0}

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


@pytest.fixture
def conn():
    """A throwaway migrated database.

    The tests above each build their own; these need several statements
    against one connection, so the setup is shared rather than repeated six
    times.
    """
    c = db.connect_at(":memory:")
    yield c
    c.close()


def _applied_count(conn) -> int:
    """Applications as the dashboard funnel counts them - from the LOG."""
    row = conn.execute(
        "SELECT COUNT(*) FROM job_status_log WHERE status = 'applied'").fetchone()
    return int(row[0])


def test_importing_the_same_export_twice_changes_nothing(conn):
    """THE DEFECT: a second import appended every log row again.

    That is not untidiness. The funnel, the Applied column, the response rate
    and applied_among's bulk-retire warning are ALL counted from this log, so
    a re-import doubled the number of applications the person appears to have
    made - and there is nothing on screen that would look wrong.
    """
    status.set_status(conn, "greenhouse:1", "applied", note="sent CV",
                      at="2026-08-01T09:00:00+00:00")
    status.set_status(conn, "greenhouse:1", "interviewed",
                      at="2026-08-09T14:00:00+00:00")
    export = status.export_status(conn)
    before = _applied_count(conn)

    first = status.import_status(conn, export)
    second = status.import_status(conn, export)

    assert _applied_count(conn) == before, (
        f"re-importing doubled the application count: {before} -> "
        f"{_applied_count(conn)}")
    assert second["log_rows"] == 0, "a re-import wrote new log rows"
    assert second["log_duplicates"] == first["log_duplicates"] + first["log_rows"], (
        "every entry should have been recognised as already recorded")


def test_a_genuine_second_application_is_still_recorded(conn):
    """The positive control, and the reason identity is (key, status, at)
    rather than (key, status). Somebody who applies, is passed over, and
    applies again months later has made TWO applications and the funnel has to
    say two."""
    status.set_status(conn, "greenhouse:1", "applied",
                      at="2026-03-01T09:00:00+00:00")
    status.set_status(conn, "greenhouse:1", "pass",
                      at="2026-03-20T09:00:00+00:00")
    status.set_status(conn, "greenhouse:1", "applied",
                      at="2026-08-01T09:00:00+00:00")

    assert _applied_count(conn) == 2
    status.import_status(conn, status.export_status(conn))
    assert _applied_count(conn) == 2, (
        "the two applications were merged into one, or a third was invented")


def test_a_cleared_status_does_not_re_import_every_time(conn):
    """A clear is stored with a NULL status, and `status = NULL` is never true
    in SQL - so a check written with `=` would miss these rows entirely and
    append another clear on every single import."""
    status.set_status(conn, "greenhouse:1", "applied",
                      at="2026-08-01T09:00:00+00:00")
    status.clear_status(conn, "greenhouse:1", at="2026-08-02T09:00:00+00:00")
    export = status.export_status(conn)

    status.import_status(conn, export)
    status.import_status(conn, export)

    cleared = conn.execute(
        "SELECT COUNT(*) FROM job_status_log WHERE status IS NULL").fetchone()[0]
    assert cleared == 1, f"the clear was recorded {cleared} times"


def test_a_re_export_carrying_more_detail_fills_in_what_is_missing(conn):
    """Skipping a known transition must not throw away detail it arrives with.
    Same COALESCE rule set_status already uses: more wins over nothing,
    nothing never erases something."""
    status.set_status(conn, "greenhouse:1", "offer",
                      at="2026-08-01T09:00:00+00:00")
    export = status.export_status(conn)
    export["log"][0]["pay"] = "$70,000"
    export["log"][0]["note"] = "verbal, written to follow"

    status.import_status(conn, export)

    row = conn.execute(
        "SELECT pay, note FROM job_status_log WHERE key = 'greenhouse:1'").fetchone()
    assert row[0] == "$70,000"
    assert row[1] == "verbal, written to follow"


def test_notes_are_not_duplicated_either(conn):
    """job_note is its own table and was appended just as blindly."""
    status.add_note(conn, "greenhouse:1", "recruiter called",
                    at="2026-08-05T11:00:00+00:00")
    export = status.export_status(conn)

    status.import_status(conn, export)
    status.import_status(conn, export)

    count = conn.execute(
        "SELECT COUNT(*) FROM job_note WHERE key = 'greenhouse:1'").fetchone()[0]
    assert count == 1, f"the note was recorded {count} times"


def test_the_same_words_on_a_different_day_are_two_notes(conn):
    """The positive control for the note rule. "called again" written twice in
    a week is two events, and collapsing them would lose one."""
    status.add_note(conn, "greenhouse:1", "called again",
                    at="2026-08-05T11:00:00+00:00")
    status.add_note(conn, "greenhouse:1", "called again",
                    at="2026-08-12T11:00:00+00:00")
    export = status.export_status(conn)

    status.import_status(conn, export)

    count = conn.execute(
        "SELECT COUNT(*) FROM job_note WHERE key = 'greenhouse:1'").fetchone()[0]
    assert count == 2
