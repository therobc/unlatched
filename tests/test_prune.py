"""Pruning postings that never matched and that nobody ever looked at.

The rule these tests defend is not "unqualified rows go". It is "rows the
PERSON has no relationship with go", and the two disagree in a way that cost
real rows to discover: on a live profile, 2,458 unqualified rows carried a
status, which reads as somebody having judged them. Every one said `closed` -
the status the engine writes itself when a posting comes off its board
untouched. Treating that as a judgement would have spared 2,458 rows nobody
ever saw; treating every status as the engine's would delete work.
"""
from __future__ import annotations

from unlatched import db, prune
from unlatched import status as status_vocab


def _job(con, key, *, qualified=0, seat=None, title="Analyst"):
    db.upsert_job(con, key, {"title": title, "qualified": qualified,
                             "seat": seat, "last_seen": "2026-08-01"})
    con.commit()
    return key


def _keys(con):
    return sorted(r["key"] for r in con.execute("SELECT key FROM jobs"))


def test_an_unmatched_untouched_posting_goes(con, home):
    _job(con, "a:1", qualified=1)
    _job(con, "a:2", qualified=0)
    prune.apply(con, home)
    assert _keys(con) == ["a:1"]


def test_a_status_the_person_chose_keeps_the_row(con, home):
    _job(con, "a:1", qualified=0)
    status_vocab.set_status(con, "a:1", "pass")
    con.commit()
    prune.apply(con, home)
    assert _keys(con) == ["a:1"]


def test_the_engines_own_closed_status_does_not_count_as_touched(con, home):
    """The trap. `closed` is written by db.close_untouched_delisted onto rows
    nobody judged, and is deliberately outside the flow vocabulary so that a
    person cannot choose it - so a row carrying only that one was never seen.
    """
    _job(con, "a:1", qualified=0)
    db.close_untouched_delisted(con, ["a:1"], at="2026-08-02")
    con.commit()
    assert con.execute("SELECT status FROM job_status WHERE key = 'a:1'"
                       ).fetchone()["status"] == status_vocab.CLOSED
    prune.apply(con, home)
    assert _keys(con) == []


def test_a_cleared_status_keeps_the_row(con, home):
    """Somebody set a status and took it back. That is two decisions, not none.
    """
    _job(con, "a:1", qualified=0)
    status_vocab.set_status(con, "a:1", "pass")
    status_vocab.clear_status(con, "a:1")
    con.commit()
    prune.apply(con, home)
    assert _keys(con) == ["a:1"]


def test_a_note_keeps_the_row(con, home):
    _job(con, "a:1", qualified=0)
    status_vocab.add_note(con, "a:1", "recruiter emailed me about this")
    con.commit()
    prune.apply(con, home)
    assert _keys(con) == ["a:1"]


def test_an_attachment_keeps_the_row(con, home):
    _job(con, "a:1", qualified=0)
    con.execute("INSERT INTO attachment (key, trust, kind, display_name, "
                "added_at) VALUES ('a:1', 'mine', 'pdf', 'resume.pdf', "
                "'2026-08-02')")
    con.commit()
    prune.apply(con, home)
    assert _keys(con) == ["a:1"]


def test_a_row_thrown_away_by_hand_stays_thrown_away_not_deleted(con, home):
    """Retiring is reversible on purpose. A prune that deleted retired rows
    would quietly turn the undo into a lie.
    """
    _job(con, "a:1", qualified=0)
    db.retire(con, ["a:1"], at="2026-08-02")
    con.commit()
    prune.apply(con, home)
    assert _keys(con) == ["a:1"]


def test_the_earlier_rounds_of_a_surviving_seat_are_kept(con, home):
    """Repost detection reads every row sharing a seat. Deleting the earlier
    advertisements of a seat whose latest round survives would shorten its
    history without saying so.
    """
    seat = "acme|analyst|remote"
    _job(con, "a:old", qualified=0, seat=seat)
    _job(con, "a:new", qualified=1, seat=seat)
    _job(con, "b:other", qualified=0, seat="acme|driver|remote")
    prune.apply(con, home)
    assert _keys(con) == ["a:new", "a:old"]


def test_a_row_folded_behind_a_deleted_one_becomes_visible_again(con, home):
    """duplicate_of hides a row behind another. If the keeper is deleted and
    the pointer is left, the hidden row is hidden behind nothing - invisible
    in every list, for ever.
    """
    _job(con, "a:keeper", qualified=0)
    _job(con, "a:hidden", qualified=1)
    con.execute("UPDATE jobs SET duplicate_of = 'a:keeper', "
                "duplicate_reason = 'same posting' WHERE key = 'a:hidden'")
    con.commit()
    prune.apply(con, home)
    row = con.execute("SELECT duplicate_of, duplicate_reason FROM jobs "
                      "WHERE key = 'a:hidden'").fetchone()
    assert row["duplicate_of"] is None
    assert row["duplicate_reason"] is None


def test_the_engine_written_status_goes_with_its_row(con, home):
    """An orphaned status row would be counted by every breakdown that reads
    job_status without joining jobs.
    """
    _job(con, "a:1", qualified=0)
    db.close_untouched_delisted(con, ["a:1"], at="2026-08-02")
    con.commit()
    prune.apply(con, home)
    assert con.execute("SELECT COUNT(*) FROM job_status").fetchone()[0] == 0
    assert con.execute("SELECT COUNT(*) FROM job_status_log").fetchone()[0] == 0


def test_a_backup_is_written_before_anything_is_deleted(con, home):
    _job(con, "a:1", qualified=0)
    result = prune.apply(con, home)
    assert result["backup"], "nothing recorded a backup"
    saved = db.connect_at(result["backup"])
    try:
        assert [r["key"] for r in saved.execute("SELECT key FROM jobs")] == ["a:1"]
    finally:
        saved.close()


def test_planning_changes_nothing(con, home):
    _job(con, "a:1", qualified=0)
    _job(con, "a:2", qualified=1)
    intent = prune.plan(con)
    assert intent.rows == 2
    assert intent.doomed == 1
    assert intent.survivors == 1
    assert _keys(con) == ["a:1", "a:2"]


def test_the_report_separates_still_listed_from_taken_down(con, home):
    """The two are the same decision now that the collector stops storing what
    does not qualify, but they were not always, and the counts are what a
    person agrees to.
    """
    _job(con, "a:up", qualified=0)
    _job(con, "a:gone", qualified=0)
    con.execute("UPDATE jobs SET delisted_at = '2026-08-02' WHERE key = 'a:gone'")
    con.commit()
    intent = prune.plan(con)
    assert (intent.still_listed, intent.delisted) == (1, 1)
