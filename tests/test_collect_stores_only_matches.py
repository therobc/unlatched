"""A collect screens every posting on the board and writes down only the
matches.

WHY THIS IS SAFE, and it is the whole argument for the change: a collect
re-reads each board from the top every run. A posting that is still up is seen
and screened again tomorrow, so criteria that change tonight are applied to the
whole live board in the morning. The stored row was never what made a re-screen
work - which is why 93% of a real profile's rows were postings that had failed
the criteria every single day since they were first read.
"""
from __future__ import annotations

import pytest

from unlatched import cli, db, sources


def _board(*jobs):
    class _Collector:
        @staticmethod
        def collect(ats_ref, fetcher=None):
            return list(jobs)
    return _Collector


def _job(source_id, title):
    return sources.Job(source="greenhouse", source_id=source_id, title=title,
                       location="Remote - United States",
                       url=f"https://example.invalid/{source_id}",
                       description="A fully remote position. " * 5)


@pytest.fixture
def wanted(cfg):
    """Criteria that pass one title and fail the other, so a run has both."""
    cfg["search"]["title_include"] = ["Support Analyst"]
    return cfg


def _collect(cfg, monkeypatch, con, argv=("collect",)):
    monkeypatch.setattr(cli.config, "load", lambda home: cfg)
    con.close()
    args = cli.build_parser().parse_args(list(argv))
    cli._collect(args)  # noqa: SLF001 - the lock wrapper would fight the fixture


def _stored(home):
    con = db.connect(home)
    try:
        return sorted(r["key"] for r in con.execute("SELECT key FROM jobs"))
    finally:
        con.close()


def test_a_posting_that_does_not_match_is_never_written_down(
        con, home, wanted, monkeypatch):
    db.upsert_company(con, "Acme", ats="greenhouse", ats_ref="acme")
    con.commit()
    monkeypatch.setattr(cli.sources, "registry", lambda: {
        "greenhouse": _board(_job("1", "Support Analyst"),
                             _job("2", "Deep Sea Welder"))})
    _collect(wanted, monkeypatch, con)
    assert _stored(home) == ["greenhouse:1"]


def test_keep_unqualified_writes_them_down(con, home, wanted, monkeypatch):
    db.upsert_company(con, "Acme", ats="greenhouse", ats_ref="acme")
    con.commit()
    monkeypatch.setattr(cli.sources, "registry", lambda: {
        "greenhouse": _board(_job("1", "Support Analyst"),
                             _job("2", "Deep Sea Welder"))})
    _collect(wanted, monkeypatch, con, ("collect", "--keep-unqualified"))
    assert _stored(home) == ["greenhouse:1", "greenhouse:2"]


def test_a_row_already_here_goes_on_being_updated_after_it_stops_matching(
        con, home, wanted, monkeypatch):
    """The case that would silently break a person's pipeline. Criteria move,
    a job they applied to stops qualifying, and if the collector skipped it the
    row would stop advancing and then be delisted as though the employer had
    taken it down.
    """
    db.upsert_company(con, "Acme", ats="greenhouse", ats_ref="acme")
    db.upsert_job(con, "greenhouse:2", {"title": "Deep Sea Welder",
                                        "qualified": 1,
                                        "last_seen": "2020-01-01"})
    con.commit()
    monkeypatch.setattr(cli.sources, "registry", lambda: {
        "greenhouse": _board(_job("2", "Deep Sea Welder"))})
    _collect(wanted, monkeypatch, con)

    check = db.connect(home)
    try:
        row = check.execute("SELECT last_seen, delisted_at, qualified FROM jobs "
                            "WHERE key = 'greenhouse:2'").fetchone()
    finally:
        check.close()
    assert row is not None, "the row was deleted rather than left alone"
    assert row["last_seen"] > "2020-01-01", "it stopped advancing"
    assert row["delisted_at"] is None, "it was delisted while still on the board"
    assert row["qualified"] == 0, "the re-screen did not run"


def test_the_run_log_says_what_a_board_yielded_and_what_was_kept(
        con, home, wanted, monkeypatch):
    """The console line is in the app's in-memory panel and goes when it
    closes. A board that offers 900 postings and keeps 3 has to be readable
    from the file, or a run looks broken to whoever reads it next.
    """
    db.upsert_company(con, "Acme", ats="greenhouse", ats_ref="acme")
    con.commit()
    monkeypatch.setattr(cli.sources, "registry", lambda: {
        "greenhouse": _board(_job("1", "Support Analyst"),
                             _job("2", "Deep Sea Welder"),
                             _job("3", "Deep Sea Welder"))})
    _collect(wanted, monkeypatch, con)

    logs = sorted((home / "logs").glob("collect-*.log"))
    text = logs[-1].read_text(encoding="utf-8")
    assert "Acme" in text
    assert "3 collected" in text, text
    assert "1 qualified" in text, text
    assert "2 not kept" in text, text
