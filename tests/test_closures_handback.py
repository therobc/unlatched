"""Handing closures back to the collector that sent them.

The flow had only ever run one way, so a posting the person discovered was
closed - opened it, read "no longer accepting applications", marked it taken
down - was known here and nowhere else. Measured on the live pair 2026-08-26:
the sender re-checks a lead every five days, so its own sweep would not look at
that posting again until 30 August.
"""
from __future__ import annotations

import json

import pytest

from unlatched import closures, collectors, db


@pytest.fixture
def collector(cfg, tmp_path):
    cfg["ingest"] = {"path": str(tmp_path / "handoff" / "board.json")}
    return collectors.enabled(cfg)[0]


def _job(con, key, *, delisted=None, url="https://example.invalid/1"):
    db.upsert_job(con, key, {"title": "Analyst", "source": "imported",
                             "url": url, "qualified": 1})
    if delisted:
        con.execute("UPDATE jobs SET delisted_at = ? WHERE key = ?", (delisted, key))
    con.commit()


def test_a_posting_marked_taken_down_is_handed_back(con, collector):
    _job(con, "imported:abc", delisted="2026-08-26T18:00:00")
    rows = closures.pending(con, collector)
    assert [r["url"] for r in rows] == ["https://example.invalid/1"]


def test_a_posting_still_open_is_not(con, collector):
    _job(con, "imported:abc")
    assert closures.pending(con, collector) == []


def test_the_key_goes_back_in_the_senders_own_spelling(con, collector):
    """Their keys say `manual:` and ours say `imported:` for the same posting.
    Handing back a key they do not recognise is how 114 closures matched
    nothing going the other way (2026-08-22).
    """
    _job(con, "imported:abc", delisted="2026-08-26T18:00:00")
    assert closures.pending(con, collector)[0]["key"] == "manual:abc"


def test_a_row_from_a_board_is_not_handed_to_the_collector(con, collector):
    """Greenhouse delisting a posting is not news for the MyBoard sender, and
    the key would mean nothing to it.
    """
    db.upsert_job(con, "greenhouse:9", {"title": "Analyst", "source": "greenhouse",
                                        "qualified": 1})
    con.execute("UPDATE jobs SET delisted_at = '2026-08-26' WHERE key = 'greenhouse:9'")
    con.commit()
    assert closures.pending(con, collector) == []


def test_the_file_declares_a_version(tmp_path, con, collector):
    """The incoming handoff carries none and its own checker says so on every
    run - "a contract without a version cannot change".
    """
    _job(con, "imported:abc", delisted="2026-08-26T18:00:00")
    path = tmp_path / "out" / "closed.json"
    closures.write(path, collector, closures.pending(con, collector))
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert doc["version"] == closures.VERSION
    assert doc["source"] == "unlatched"
    assert len(doc["closed"]) == 1


def test_nothing_is_left_half_written(tmp_path, con, collector):
    """The reader may look at any moment. A partial file is a parse error that
    reads as a broken contract rather than as a race.
    """
    _job(con, "imported:abc", delisted="2026-08-26T18:00:00")
    path = tmp_path / "out" / "closed.json"
    closures.write(path, collector, closures.pending(con, collector))
    assert not list(path.parent.glob("*.part")), "a staging file was left behind"
    json.loads(path.read_text(encoding="utf-8"))


def test_it_lands_beside_the_file_the_collector_hands_us(collector, tmp_path):
    where = closures.default_path(collector)
    assert where.parent == (tmp_path / "handoff")
    assert where.name == "board-closed-by-unlatched.json"


def test_the_closure_time_says_which_clock_it_is_on(con, collector):
    """delisted_at is UTC with no offset written on it, and the program reading
    this file keeps its own timestamps in naive local time. Handed over bare,
    an evening closure lands hours in ITS future - which sorts wrong quietly
    rather than failing.
    """
    _job(con, "imported:abc", delisted="2026-08-27T01:39:12")
    stamp = closures.pending(con, collector)[0]["closed_at"]
    assert stamp == "2026-08-27T01:39:12+00:00"


def test_a_date_only_closure_is_not_given_invented_precision(con, collector):
    _job(con, "imported:abc", delisted="2026-08-11")
    assert closures.pending(con, collector)[0]["closed_at"] == "2026-08-11"


def test_a_posting_added_by_hand_from_the_same_board_travels_too(con, collector):
    """It carries source "manual", not the collector's id - but it is the same
    board, the sender may well hold it, and its closure is as useful. Filtering
    on the source label alone would silently drop it.
    """
    _job(con, "imported:a", url="https://www.example.com/jobs/view/1/",
         delisted="2026-08-26T18:00:00")
    db.upsert_job(con, "manual:b", {
        "title": "Analyst", "source": "manual", "qualified": 1,
        "url": "https://www.example.com/jobs/view/2/"})
    con.execute("UPDATE jobs SET delisted_at = '2026-08-26T19:00:00' "
                "WHERE key = 'manual:b'")
    con.commit()
    urls = {r["url"] for r in closures.pending(con, collector)}
    assert urls == {"https://www.example.com/jobs/view/1/",
                    "https://www.example.com/jobs/view/2/"}


def test_a_hand_added_job_from_somewhere_else_does_not(con, collector):
    """The widening is by HOST, not "anything hand-added". A posting the sender
    has never seen is not its business and its key would mean nothing there.
    """
    _job(con, "imported:a", url="https://www.example.com/jobs/view/1/",
         delisted="2026-08-26T18:00:00")
    db.upsert_job(con, "manual:c", {
        "title": "Analyst", "source": "manual", "qualified": 1,
        "url": "https://careers.example.invalid/job/9"})
    con.execute("UPDATE jobs SET delisted_at = '2026-08-26' WHERE key = 'manual:c'")
    con.commit()
    urls = {r["url"] for r in closures.pending(con, collector)}
    assert urls == {"https://www.example.com/jobs/view/1/"}


def test_one_posting_held_twice_is_handed_back_once(con, collector):
    """The same posting added by hand AND handed over is two rows with two
    keys that spell back to the same key. The URL identifies the posting.
    """
    _job(con, "imported:a", url="https://www.example.com/jobs/view/1/",
         delisted="2026-08-26T18:00:00")
    db.upsert_job(con, "manual:a", {
        "title": "Analyst", "source": "manual", "qualified": 1,
        "url": "https://www.example.com/jobs/view/1/"})
    con.execute("UPDATE jobs SET delisted_at = '2026-08-26T19:00:00' "
                "WHERE key = 'manual:a'")
    con.commit()
    assert len(closures.pending(con, collector)) == 1
