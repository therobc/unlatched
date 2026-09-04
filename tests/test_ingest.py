"""The handoff path: taking in rows another tool left in a file.

Two properties carry this feature, and both are about NOT doing something.

FIRST, it must not take the same file twice. The sender rewrites one path every
run, so "have I already taken this?" is the whole question. Getting it wrong is
not a harmless repeat: relist() clears delisted_at, so re-importing a stale file
resurrects rows that were closed after it was written.

SECOND, it must never break the refresh. The file is written by a different
process on a different schedule. If a malformed one could raise, somebody else's
bug becomes this app not collecting - which is a far worse outcome than a
handoff that did not land.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from unlatched import cli, config, db

ROW = {
    "url": "https://boards.greenhouse.io/northwind/jobs/7788",
    "title": "Technical Support Analyst",
    "company": "Northwind",
    "location": "Remote - US",
    "description": "Support the platform team. Windows, Active Directory, SLAs.",
    "posted": "2026-08-12",
}


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """A handoff is rows somebody already read. Reading them again is the bug."""
    def explode(*_args, **_kwargs):
        raise AssertionError("ingest must not fetch anything")

    monkeypatch.setattr("unlatched.fetch.fetch", explode)


def _args(home, **kw):
    base = {"home": home, "json": False, "force": False}
    base.update(kw)
    return argparse.Namespace(**base)


def _cfg(path=None):
    cfg = config.defaults()
    cfg["ingest"]["path"] = None if path is None else str(path)
    return cfg


def _drop(tmp_path, rows, name="handoff.json"):
    p = tmp_path / name
    p.write_text(json.dumps({"jobs": rows}), encoding="utf-8")
    return p


def test_no_path_configured_does_nothing(home, tmp_path):
    """The shipped default. Null path means the feature is not there at all."""
    assert cli.ingest_pending(_args(home), _cfg(None)) is None


def test_missing_file_is_not_an_error(home, tmp_path):
    """The sender has not run yet. That is a normal morning, not a failure."""
    assert cli.ingest_pending(_args(home), _cfg(tmp_path / "nope.json")) is None


def test_takes_in_rows(home, tmp_path):
    p = _drop(tmp_path, [ROW])
    result = cli.ingest_pending(_args(home), _cfg(p))
    assert result is not None
    assert result["imported"] == 1
    con = db.connect(home)
    assert con.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1
    con.close()


def test_same_file_is_not_taken_twice(home, tmp_path):
    """The guard against resurrecting closed rows from a stale file."""
    p = _drop(tmp_path, [ROW])
    assert cli.ingest_pending(_args(home), _cfg(p))["imported"] == 1
    assert cli.ingest_pending(_args(home), _cfg(p)) is None


def test_rewritten_file_is_taken_again(home, tmp_path):
    """A POSITIVE control for the guard above.

    Without this, a version that simply never ingested twice would pass the
    not-taken-twice test while being completely broken.
    """
    p = _drop(tmp_path, [ROW])
    assert cli.ingest_pending(_args(home), _cfg(p))["imported"] == 1

    second = dict(ROW, url="https://boards.greenhouse.io/northwind/jobs/9900",
                  title="Application Support Analyst")
    _drop(tmp_path, [ROW, second])
    again = cli.ingest_pending(_args(home), _cfg(p))
    assert again is not None, "a rewritten file must be taken again"
    con = db.connect(home)
    assert con.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 2
    con.close()


def test_force_retakes_an_unchanged_file(home, tmp_path):
    p = _drop(tmp_path, [ROW])
    assert cli.ingest_pending(_args(home), _cfg(p))["imported"] == 1
    # Checks the COUNT, not just that something came back. `is not None` would
    # pass on a run that took the file and imported nothing.
    assert cli.ingest_pending(_args(home), _cfg(p), force=True)["imported"] == 1


def test_a_file_vanishing_mid_read_does_not_raise(home, tmp_path, monkeypatch):
    """The sender rewrites this path on its own schedule.

    So the file CAN be replaced between exists() and stat(), and that OSError
    used to propagate out of the refresh - the docstring promised "never raises"
    while the two calls that open the work sat outside the try (2026-08-12).
    """
    p = _drop(tmp_path, [ROW])
    real = Path.stat

    # Scoped to THIS path. Raising for every Path breaks pytest's own traceback
    # machinery, which stats files while reporting - the failure looks like a
    # bug in the test framework rather than in the patch.
    def vanish(self, **kw):
        if str(self) == str(p):
            raise OSError("file went away")
        return real(self, **kw)

    monkeypatch.setattr(Path, "stat", vanish)
    assert cli.ingest_pending(_args(home), _cfg(p)) is None


def test_an_unopenable_database_does_not_raise(home, tmp_path, monkeypatch):
    """Same promise, the other call that was outside the try."""
    p = _drop(tmp_path, [ROW])

    def refuse(*_a, **_kw):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(db, "connect", refuse)
    assert cli.ingest_pending(_args(home), _cfg(p)) is None


def test_malformed_file_does_not_raise(home, tmp_path, capsys):
    """Somebody else's half-written file must not stop this app collecting."""
    p = tmp_path / "handoff.json"
    p.write_text('{"jobs": [{"title": "trunc', encoding="utf-8")
    assert cli.ingest_pending(_args(home), _cfg(p)) is None
    assert "could not take in" in capsys.readouterr().err


def test_a_bad_file_is_not_marked_as_taken(home, tmp_path):
    """A failed read must not consume the handoff.

    Marking on failure would mean a file that was mid-write when the refresh
    landed is skipped forever once the sender finishes writing it, because the
    bytes changed but the app already recorded it as done.
    """
    p = tmp_path / "handoff.json"
    p.write_text('{"jobs": [{"title": "trunc', encoding="utf-8")
    assert cli.ingest_pending(_args(home), _cfg(p)) is None
    _drop(tmp_path, [ROW])
    assert cli.ingest_pending(_args(home), _cfg(p))["imported"] == 1


def test_closures_in_the_handoff_are_applied(home, tmp_path):
    """The 62 that would have vanished.

    read_rows takes `jobs` and ignores every other key, so closures bundled into
    the same file were accepted with a clean exit and no effect. A dropped
    closure is worse than a failed one: the row stays on the board looking live.
    """
    p = _drop(tmp_path, [ROW])
    cli.ingest_pending(_args(home), _cfg(p))

    p.write_text(json.dumps({"jobs": [], "closed": [_key_of(home)]}),
                 encoding="utf-8")
    result = cli.ingest_pending(_args(home), _cfg(p))
    assert result["closed"] == 1

    con = db.connect(home)
    assert con.execute(
        "SELECT delisted_at FROM jobs").fetchone()["delisted_at"] is not None
    con.close()


def test_closure_for_an_unknown_key_is_reported_not_swallowed(home, tmp_path):
    """Disagreement about identity is a real problem dressed as a no-op."""
    p = _drop(tmp_path, [ROW])
    p.write_text(json.dumps({"jobs": [ROW], "closed": ["manual:not-here"]}),
                 encoding="utf-8")
    result = cli.ingest_pending(_args(home), _cfg(p))
    assert result["closed"] == 0
    assert result["closed_unknown"] == ["manual:not-here"]


def test_a_row_present_in_both_lists_ends_closed(home, tmp_path):
    """Ordering. import_row calls db.relist() on every row it stores, which
    clears delisted_at, so closures applied first would be wiped by the import
    that follows. This test is the assertion behind that claim.
    """
    p = _drop(tmp_path, [ROW])
    cli.ingest_pending(_args(home), _cfg(p))
    key = _key_of(home)

    p.write_text(json.dumps({"jobs": [ROW], "closed": [key]}), encoding="utf-8")
    assert cli.ingest_pending(_args(home), _cfg(p))["closed"] == 1
    con = db.connect(home)
    assert con.execute(
        "SELECT delisted_at FROM jobs").fetchone()["delisted_at"] is not None
    con.close()


def test_a_file_with_no_closed_key_is_fine(home, tmp_path):
    """Every sender that predates closures keeps working."""
    p = _drop(tmp_path, [ROW])
    assert cli.ingest_pending(_args(home), _cfg(p))["closed"] == 0


def _key_of(home):
    con = db.connect(home)
    key = con.execute("SELECT key FROM jobs").fetchone()["key"]
    con.close()
    return key


def _drop_stamped(tmp_path, rows, generated_at, name="handoff.json"):
    p = tmp_path / name
    p.write_text(json.dumps({"generated_at": generated_at, "jobs": rows}),
                 encoding="utf-8")
    return p


def test_age_is_reported_on_a_good_run(home, tmp_path):
    stamp = (datetime.now().astimezone() - timedelta(hours=3)).isoformat()
    p = _drop_stamped(tmp_path, [ROW], stamp)
    result = cli.ingest_pending(_args(home), _cfg(p))
    assert 2.5 < result["age_hours"] < 3.5


def test_an_unstamped_file_reports_none_not_zero(home, tmp_path):
    """age_hours is None for an unstamped file, and this asserts that literally.

    A sender that stamps nothing would otherwise have to be reported as some
    number, and any number reads as "recently written". None is the only value
    that says "cannot tell", which is a different answer from "fresh".
    """
    p = _drop(tmp_path, [ROW])
    assert cli.ingest_pending(_args(home), _cfg(p))["age_hours"] is None


def test_an_unchanged_stale_file_says_the_sender_may_have_stopped(
        home, tmp_path, capsys):
    """A stale unchanged file warns; this asserts the warning text reaches stderr.

    The case it covers: a stopped sender leaves a file that still exists, still
    parses, and still holds the same rows, so every other check here passes. The
    stamp is the only field that differs, which is why it is read at all. Paired
    with test_an_unchanged_recent_file_stays_quiet as the negative control.
    """
    old = (datetime.now().astimezone()
           - timedelta(hours=cli.STALE_HANDOFF_HOURS + 5)).isoformat()
    p = _drop_stamped(tmp_path, [ROW], old)
    cli.ingest_pending(_args(home), _cfg(p))
    capsys.readouterr()

    assert cli.ingest_pending(_args(home), _cfg(p)) is None
    assert "may have stopped" in capsys.readouterr().err


def test_an_unchanged_recent_file_stays_quiet(home, tmp_path, capsys):
    """Positive control for the warning above.

    Without this, a version that warned on EVERY unchanged file would pass the
    staleness test while making the warning meaningless.
    """
    recent = (datetime.now().astimezone() - timedelta(hours=2)).isoformat()
    p = _drop_stamped(tmp_path, [ROW], recent)
    cli.ingest_pending(_args(home), _cfg(p))
    capsys.readouterr()

    assert cli.ingest_pending(_args(home), _cfg(p)) is None
    assert capsys.readouterr().err == ""


def test_collect_groups_duplicates_too(home, tmp_path, monkeypatch):
    """A hand-pressed Collect must group, not only the scheduled refresh.

    Grouping used to live inside cmd_refresh alone, so a Collect left every new
    duplicate showing twice until a refresh happened to run. Measured on
    2026-08-12: grouped stayed at 15 across a full collect while a report-only
    dedupe found a real ungrouped pair.
    """
    grouped = []
    monkeypatch.setattr(cli, "group_new_duplicates",
                        lambda _a: grouped.append(True))
    monkeypatch.setattr(cli.config, "load", lambda _h: config.defaults())
    monkeypatch.setattr(cli, "_load_resume_text", lambda _c, _h: "")

    args = argparse.Namespace(home=home, company=None, source=None, json=True)
    cli.cmd_collect(args)
    assert grouped == [True], "a collect that does not group leaves duplicates"


def test_refresh_takes_the_handoff_before_grouping(home, tmp_path, monkeypatch):
    """ORDER IS THE POINT.

    Handed-over rows must be in the database before the dedupe runs, or they sit
    ungrouped until the next day and the board shows the same job twice every
    morning - the failure an earlier change exists to stop.
    """
    p = _drop(tmp_path, [ROW])
    cfg = _cfg(p)
    monkeypatch.setattr(config, "load", lambda _home: cfg)
    monkeypatch.setattr(cli, "cmd_collect", lambda _a: 0)

    order = []
    real_ingest = cli.ingest_pending

    def spy_ingest(a, c, **kw):
        order.append("ingest")
        return real_ingest(a, c, **kw)

    monkeypatch.setattr(cli, "ingest_pending", spy_ingest)
    monkeypatch.setattr(cli, "group_new_duplicates",
                        lambda _a: order.append("group"))

    args = _args(home, force=True, check=False)
    cli.cmd_refresh(args)
    assert order == ["ingest", "group"]


def test_refresh_groups_even_when_the_collect_fails(home, tmp_path, monkeypatch):
    """cmd_refresh groups on `code == 0 or taken`; this drives the `or taken`.

    cmd_collect is forced to a non-zero return with a handoff present, so the
    second half of that condition is the only thing that can trigger grouping
    here. That makes it load-bearing rather than decorative, which is the claim.

    Gating the grouping on the collect alone would leave handed-over rows
    ungrouped exactly when a collect is failing, which is when the board is most
    likely to be showing duplicates already.
    """
    p = _drop(tmp_path, [ROW])
    cfg = _cfg(p)
    monkeypatch.setattr(config, "load", lambda _home: cfg)
    monkeypatch.setattr(cli, "cmd_collect", lambda _a: 1)
    grouped = []
    monkeypatch.setattr(cli, "group_new_duplicates",
                        lambda _a: grouped.append(True))

    cli.cmd_refresh(_args(home, force=True, check=False))
    assert grouped == [True]


def test_a_handoff_records_when_it_was_taken_in(tmp_path, home):
    """WHEN, recorded, not inferred later from the rows.

    The dashboard used to answer "has this file been read" from
    MAX(fetched_at) for that source. That is a different question, and gives
    the same answer only while every handoff carries jobs. Measured on the
    live profile 2026-08-27: a file of 0 jobs and 384 closures imported
    correctly, all 333 matching rows were closed - and the screen still read
    "not taken in yet", because no row's timestamp had moved.
    """
    con = db.connect(home)
    db.upsert_job(con, "imported:gone", {"title": "Analyst", "source": "imported",
                                          "url": ROW["url"], "qualified": 1})
    con.commit()
    con.close()

    p = tmp_path / "closures-only.json"
    p.write_text(json.dumps({
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "jobs": [],
        "closed": ["manual:gone"],
    }), encoding="utf-8")

    cfg = _cfg(p)
    collector = cli.collectors_mod.enabled(cfg)[0]
    cli.ingest_pending(_args(home), cfg)

    con = db.connect(home)
    try:
        assert db.get_meta(con, collector.taken_marker), (
            "nothing recorded when the handoff was taken in")
        # The closure did land - which is what makes the silence a display bug
        # rather than an import one.
        assert con.execute(
            "SELECT delisted_at FROM jobs WHERE key = 'imported:gone'"
        ).fetchone()[0]
        # And no row's arrival time moved, which is exactly why inferring it
        # from the rows could never have worked.
        assert con.execute(
            "SELECT COUNT(*) FROM jobs WHERE source = 'imported' "
            "AND fetched_at IS NOT NULL").fetchone()[0] == 0
    finally:
        con.close()


def test_a_file_already_held_records_that_it_is_held(tmp_path, home):
    """A profile that took a handoff in before the stamp existed has no record
    of when, and the file never changes again - so the "nothing new" branch is
    the only one it will ever reach. Without a backfill there it reads as
    never taken in, permanently.
    """
    p = tmp_path / "h.json"
    p.write_text(json.dumps({
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "jobs": [ROW],
    }), encoding="utf-8")
    cfg = _cfg(p)
    collector = cli.collectors_mod.enabled(cfg)[0]

    assert cli.ingest_pending(_args(home), cfg)["imported"] == 1
    con = db.connect(home)
    try:
        db.set_meta(con, collector.taken_marker, "")
        con.execute("DELETE FROM meta WHERE key = ?", (collector.taken_marker,))
        con.commit()
        assert db.get_meta(con, collector.taken_marker) is None
    finally:
        con.close()

    # Same file, nothing new - the branch a settled profile always lands in.
    assert cli.ingest_pending(_args(home), cfg) is None

    con = db.connect(home)
    try:
        assert db.get_meta(con, collector.taken_marker), (
            "holding the file was never recorded")
    finally:
        con.close()
