"""A collect keeps a durable record, and cannot run for ever.

THE INCIDENT. One scheduled collect ran for 11h47m. Nine and a half of those
hours were a single gap in which no employer completed, and afterwards there
was no way to say which employer had held it: the per-employer lines go to
stdout, the desktop keeps them in memory, and closing the app discards them.
The run had to be reconstructed from the timestamps on the rows it wrote, and
those can only show where a run GOT TO, never where it was stuck.

Two things are asserted here, and the second is the one with teeth:

  * the log exists, is timestamped, and names each employer BEFORE fetching it
  * the ceiling is enforced INSIDE the fetch layer, not only between employers

That second point is the whole design. A ceiling checked between employers
would not have fired once during the incident, because the loop never got a
turn - so a test that only proves "the loop stops when expired" would pass
against a fix that could not have prevented the thing it was written for.
"""
from __future__ import annotations

from typing import Any

import pytest

from unlatched import cli, db, fetch, runlog

CAREERS = "https://example.com/careers"
PAGE = """<html><head>
<script type="application/ld+json">
{"@type": "JobPosting", "title": "Support Analyst", "url":
"https://example.com/careers/a-1", "identifier": {"value": "1"},
"datePosted": "2026-08-01", "description": "Help desk."}
</script></head><body>hi</body></html>"""


@pytest.fixture(autouse=True)
def _clean_run_state():
    """Deadlines are module state, so a test that sets one must not leak it."""
    fetch.reset_rate_limits()
    yield
    fetch.reset_rate_limits()


def _fetch_ok(url: str, **_kw: Any) -> tuple[int, str, str]:
    return (200, PAGE, url) if url == CAREERS else (404, "", url)


def _seed(home, names=("Example Co",)):
    con = db.connect(home)
    for name in names:
        db.upsert_company(con, name, careers_url=CAREERS, probe_status="probed")
    con.close()


# --- the ceiling, where it actually has to work ---------------------------

def test_an_expired_run_stops_fetching_mid_employer():
    """The load-bearing one.

    An employer already in flight must stop being able to make requests, or a
    ceiling is only a promise about the gaps between employers - and the gaps
    are not where the eleven hours went.
    """
    fetch.set_run_deadline(0.0001)  # ~6ms
    import time
    time.sleep(0.05)

    assert fetch.run_expired()
    # Status 0 is this module's established "did not happen" - the same answer
    # a disallowed scheme or a host that keeps throwing 429s gets.
    status, body, _ = fetch.fetch("https://example.com/anything")
    assert status == 0
    assert body == ""


def test_no_ceiling_means_no_deadline():
    fetch.set_run_deadline(0)
    assert not fetch.run_expired()
    assert fetch.seconds_left() is None


def test_the_deadline_clears_with_the_rest_of_the_run_state():
    fetch.set_run_deadline(60)
    assert fetch.seconds_left() is not None
    fetch.reset_rate_limits()
    assert not fetch.run_expired()
    assert fetch.seconds_left() is None


def test_a_cut_short_run_is_not_marked_complete(tmp_path, monkeypatch, capsys):
    """The consequence that matters more than the stopping.

    collect_completed_at is what due() reads to decide an anchor was satisfied.
    A run stopped early that stamped it would tell the app the day's work was
    done and let every unreached employer wait until tomorrow, unmentioned -
    the exact failure the marker was introduced to prevent.
    """
    home = tmp_path / "home"
    _seed(home, ("A Co", "B Co", "C Co"))
    monkeypatch.setattr(cli.fetch_mod, "fetch", _fetch_ok)
    # Expire immediately, so the loop breaks before the first employer.
    monkeypatch.setattr(cli, "_run_ceiling_minutes", lambda _cfg: 0.0000001)

    rc = cli.main(["--home", str(home), "collect"])
    assert rc == 0

    con = db.connect(home)
    marker = db.get_meta(con, cli.COLLECT_COMPLETED_KEY)
    con.close()
    assert marker is None, "a run that stopped early must not read as complete"

    # And it must SAY so rather than looking like a quiet, successful run.
    err = capsys.readouterr().err
    assert "not reached" in err, err


def test_a_normal_run_is_still_marked_complete(tmp_path, monkeypatch):
    """The positive control. Without this, a fix that simply never wrote the
    marker would pass the test above and break the schedule for everyone."""
    home = tmp_path / "home"
    _seed(home)
    monkeypatch.setattr(cli.fetch_mod, "fetch", _fetch_ok)

    assert cli.main(["--home", str(home), "collect"]) == 0

    con = db.connect(home)
    marker = db.get_meta(con, cli.COLLECT_COMPLETED_KEY)
    con.close()
    assert marker, "a completed run must still stamp the marker"


def test_the_ceiling_default_is_four_hours_and_survives_a_typo():
    assert cli._run_ceiling_minutes({}) == 240.0  # noqa: SLF001 - the test of a private helper must reach it
    assert cli._run_ceiling_minutes({"fetch": {}}) == 240.0  # noqa: SLF001 - the test of a private helper must reach it
    # A typo must not silently remove a safety limit.
    assert cli._run_ceiling_minutes({"fetch": {"max_run_minutes": "soon"}}) == 240.0  # noqa: SLF001 - the test of a private helper must reach it
    assert cli._run_ceiling_minutes({"fetch": {"max_run_minutes": 30}}) == 30.0  # noqa: SLF001 - the test of a private helper must reach it
    # Explicitly off is a decision somebody is allowed to make.
    assert cli._run_ceiling_minutes({"fetch": {"max_run_minutes": 0}}) == 0.0  # noqa: SLF001 - the test of a private helper must reach it


def test_an_expired_employer_does_not_end_the_run():
    """THE ONE THAT MATTERS. run_expired must stay false when only the
    employer's own budget has gone, because the collect loop breaks on
    run_expired and merely moves on when an employer runs out.

    Getting this backwards would be worse than the original fault: the employer
    that hangs here is third of 1,119 alphabetically, so ending the run on it
    means the other 1,116 are never collected, every day.
    """
    import time

    fetch.set_run_deadline(60)          # plenty of run left
    fetch.set_employer_deadline(0.0001)  # this employer is done
    time.sleep(0.05)

    assert fetch.employer_expired()
    assert not fetch.run_expired(), "an out-of-time employer must not end the run"
    # But it must stop being able to fetch.
    status, _, _ = fetch.fetch("https://example.com/anything")
    assert status == 0


def test_the_next_employer_gets_a_fresh_budget():
    import time

    fetch.set_employer_deadline(0.0001)
    time.sleep(0.05)
    assert fetch.employer_expired()

    fetch.set_employer_deadline(10)  # the loop does this before each employer
    assert not fetch.employer_expired()


def test_both_budgets_clear_with_the_run_state():
    fetch.set_run_deadline(60)
    fetch.set_employer_deadline(10)
    fetch.reset_rate_limits()
    assert not fetch.run_expired()
    assert not fetch.employer_expired()


def test_the_employer_budget_default_is_ten_minutes_and_survives_a_typo():
    assert cli._employer_budget_minutes({}) == 10.0  # noqa: SLF001 - the test of a private helper must reach it
    assert cli._employer_budget_minutes({"fetch": {}}) == 10.0  # noqa: SLF001 - the test of a private helper must reach it
    assert cli._employer_budget_minutes({"fetch": {"max_employer_minutes": "soon"}}) == 10.0  # noqa: SLF001 - the test of a private helper must reach it
    assert cli._employer_budget_minutes({"fetch": {"max_employer_minutes": 2}}) == 2.0  # noqa: SLF001 - the test of a private helper must reach it
    assert cli._employer_budget_minutes({"fetch": {"max_employer_minutes": 0}}) == 0.0  # noqa: SLF001 - the test of a private helper must reach it


# --- the durable record ----------------------------------------------------

def test_a_collect_leaves_a_timestamped_log(tmp_path, monkeypatch):
    home = tmp_path / "home"
    _seed(home)
    monkeypatch.setattr(cli.fetch_mod, "fetch", _fetch_ok)

    assert cli.main(["--home", str(home), "collect"]) == 0

    logs = sorted((home / "logs").glob("collect-*.log"))
    assert len(logs) == 1, f"expected one run log, got {logs}"
    text = logs[0].read_text(encoding="utf-8")

    assert "run started" in text
    assert "run ended: completed" in text
    assert "Example Co" in text
    # Named BEFORE the fetch, which is what lets a stalled run be diagnosed
    # while it is still stalled rather than only afterwards.
    assert "reading" in text
    # Every line carries a wall clock and an elapsed figure.
    first = text.splitlines()[0]
    assert first[2] == ":", first
    assert first[5] == ":", first


def test_whole_board_sources_are_logged_too(tmp_path, monkeypatch):
    """The gap the first real log exposed.

    USAJOBS, Remote OK and NoDesk belong to no employer and run in a second
    loop after the company one. The first version of this logging covered only
    the first loop: the header read "0 employer(s)" and 139 postings then
    arrived from two sources the log never named. A source that pages through
    one host can hang exactly as an employer can, so leaving them out would
    have reproduced the silent, unattributable hole this was built to end.
    """
    from unlatched import sources

    class FakeSource:
        IS_SEARCH_SOURCE = True
        CREDENTIALS_HINT = "needs a key"

        @staticmethod
        def has_credentials(_cfg):
            return True

        @staticmethod
        def collect(_cfg, fetcher=None):
            return []

    home = tmp_path / "home"
    db.connect(home).close()  # a profile with no employers at all
    monkeypatch.setattr(sources, "search_sources", lambda _reg: {"fakeboard": FakeSource})

    assert cli.main(["--home", str(home), "collect"]) == 0

    text = sorted((home / "logs").glob("collect-*.log"))[0].read_text(encoding="utf-8")
    assert "whole-board source" in text, text
    assert "fakeboard" in text, text


def test_a_source_without_credentials_says_so_in_the_log(tmp_path, monkeypatch):
    """A skipped source must be visible. Silence here is indistinguishable
    from a source that ran and found nothing."""
    from unlatched import sources

    class NoKey:
        IS_SEARCH_SOURCE = True
        CREDENTIALS_HINT = "usajobs skipped - add credentials"

        @staticmethod
        def has_credentials(_cfg):
            return False

    home = tmp_path / "home"
    db.connect(home).close()
    monkeypatch.setattr(sources, "search_sources", lambda _reg: {"usajobs": NoKey})

    assert cli.main(["--home", str(home), "collect"]) == 0

    text = sorted((home / "logs").glob("collect-*.log"))[0].read_text(encoding="utf-8")
    assert "usajobs" in text, text
    assert "no credentials" in text, text


def test_the_log_names_the_employer_before_it_is_fetched(tmp_path):
    """Reading order, asserted directly: the 'reading' line for an employer
    must appear before anything that could only be written after its fetch."""
    log = runlog.RunLog(tmp_path, "collect")
    log.start(1, 0, 240)
    log.employer_start("Slow Co", "workday")
    log.employer_done("Slow Co", 3, 1)
    log.finish("completed")

    lines = log.path.read_text(encoding="utf-8").splitlines()
    reading = next(i for i, x in enumerate(lines) if "reading" in x)
    result = next(i for i, x in enumerate(lines) if "collected" in x)
    assert reading < result


def test_logging_never_takes_the_run_down(tmp_path):
    """A diagnostic that kills the thing it was added to diagnose is worse
    than the blindness it was fixing."""
    log = runlog.RunLog(tmp_path / "nope" / "deeper", "collect")
    log._handle = None  # noqa: SLF001 - simulating a disk that filled mid-run
    log.start(1, 0, 240)
    log.employer_start("A", "b")
    log.employer_done("A", 1, 1)
    log.finish("completed")  # must not raise


def test_old_run_logs_are_pruned(tmp_path):
    folder = tmp_path / "logs"
    folder.mkdir()
    for i in range(runlog.KEEP_RUNS + 5):
        (folder / f"collect-2026010{i // 10}-0000{i % 10:02d}.log").write_text("x")

    runlog.RunLog(tmp_path, "collect").finish("completed")

    kept = sorted(folder.glob("collect-*.log"))
    assert len(kept) <= runlog.KEEP_RUNS + 1, f"{len(kept)} left"
