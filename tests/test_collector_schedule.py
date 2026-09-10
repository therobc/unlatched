"""When the app looks, and pulling on command.

TWO SEPARATE QUESTIONS, and the bugs live in mistaking one for the other:

  WHEN DO WE LOOK      the collector's `schedule`. No schedule means every
                       refresh, which is what the single pre-list handoff did
                       and what the live profile depends on.
  DID ANYTHING ARRIVE  the file fingerprint, which is unchanged and is not
                       what this file is about.

Keying the schedule on "was anything imported" instead of "did we look" is the
mistake that turns a schedule into a poll: a collector whose sender has not run
yet never imports, so it stays due and is looked at again every single refresh.
test_a_quiet_collector_is_not_looked_at_again_the_same_day is the control for
that, and it fails against exactly that version.
"""
from __future__ import annotations

import json
from datetime import datetime

import pytest

from unlatched import cli, collectors, db


# LOCAL WALL CLOCK, aware, exactly as ingest_pending builds it. A schedule of
# "13:00" is the person's own thirteen hundred, and refresh._moment_of converts
# the stored stamp to local before comparing - so a test written in UTC would
# compare two different clocks and pass or fail by the machine's timezone.
def local(hour, day=13):
    return datetime(2026, 8, day, hour, 0).astimezone()


MORNING = local(9)
AFTERNOON = local(14)
LATER = local(17)
TOMORROW = local(14, day=14)


class Args:
    def __init__(self, home):
        self.home = str(home)
        self.json = True


def write_handoff(path, n=1):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "version": 1, "generated_at": "2026-08-13T08:00:00+00:00",
        "jobs": [{"title": f"Support Analyst {n}", "company": "Acme",
                  "url": f"https://boards.greenhouse.io/acme/jobs/{n}"}],
    }), encoding="utf-8")
    return path


def cfg_for(home, **overrides):
    return {"collectors": [
        {"id": "partner", "path": str(home / "partner.json"), **overrides}]}


# ------------------------------------------------------------- parsing ----

def test_times_are_read_and_sorted():
    assert collectors.parse_times(["18:30", "09:05"]) == (((9, 5), (18, 30)), [])


@pytest.mark.parametrize("bad", ["1300", "25:00", "13:60", "noon", "13:0"])
def test_an_unreadable_time_is_named_rather_than_dropped(bad, home):
    """A typo in a schedule is otherwise invisible: the collector goes on
    working, it simply stops arriving when the person expected, and there is
    nothing to notice."""
    found, problems = collectors.configured(cfg_for(home, schedule=[bad]))

    assert found == []
    assert len(problems) == 1
    assert repr(bad) in problems[0]


def test_a_readable_time_is_accepted_alongside(home):
    """The positive control for the parametrised refusal above - without it,
    a version that refused EVERY schedule would pass all five."""
    found, problems = collectors.configured(cfg_for(home, schedule=["13:00"]))

    assert problems == []
    assert found[0].anchors == ((13, 0),)


# ------------------------------------------------------------ the look ----

def test_no_schedule_means_every_refresh(home):
    """THE LIVE PROFILE'S CASE. A migrated handoff has no schedule, and it is
    pulled on every refresh today. A default of "on demand only" would stop
    that silently - jobs would simply stop arriving."""
    write_handoff(home / "partner.json")

    result = cli.ingest_pending(Args(home), cfg_for(home), now=MORNING)

    assert result is not None
    assert result["imported"] == 1


def test_a_time_that_has_not_come_round_yet_is_not_looked_at(home):
    write_handoff(home / "partner.json")

    result = cli.ingest_pending(Args(home), cfg_for(home, schedule=["13:00"]),
                                now=MORNING)

    assert result is None


def test_the_same_collector_is_looked_at_once_the_time_passes(home):
    """The positive control for the test above: same collector, same file,
    later clock. Without it, a version that never looked at a scheduled
    collector at all would pass."""
    write_handoff(home / "partner.json")

    result = cli.ingest_pending(Args(home), cfg_for(home, schedule=["13:00"]),
                                now=AFTERNOON)

    assert result is not None
    assert result["imported"] == 1


def test_a_quiet_collector_is_not_looked_at_again_the_same_day(home):
    """THE ONE THAT SEPARATES A SCHEDULE FROM A POLL.

    Nothing is written at the path, so nothing is ever imported. A schedule
    keyed on the import would find this collector due at every refresh for the
    rest of the day; keyed on the LOOK, one o'clock happens once.
    """
    args, cfg = Args(home), cfg_for(home, schedule=["13:00"])

    assert cli.ingest_pending(args, cfg, now=AFTERNOON) is None
    con = db.connect(home)
    try:
        looked = db.get_meta(con, collectors.enabled(cfg)[0].seen_marker)
    finally:
        con.close()

    assert looked is not None, "the look itself has to be recorded"
    # And a file appearing later that day is NOT taken until tomorrow's slot.
    write_handoff(home / "partner.json")
    assert cli.ingest_pending(args, cfg, now=LATER) is None
    assert cli.ingest_pending(args, cfg, now=TOMORROW) is not None


def test_a_second_slot_the_same_day_is_its_own_look(home):
    """Two times mean two looks, which is the whole reason the field is a list
    rather than a single time."""
    args, cfg = Args(home), cfg_for(home, schedule=["13:00", "16:00"])

    write_handoff(home / "partner.json", n=1)
    assert cli.ingest_pending(args, cfg, now=AFTERNOON)["imported"] == 1
    write_handoff(home / "partner.json", n=2)
    assert cli.ingest_pending(args, cfg, now=LATER)["imported"] == 1


def test_a_disabled_collector_is_never_looked_at(home):
    write_handoff(home / "partner.json")

    result = cli.ingest_pending(Args(home), cfg_for(home, enabled=False),
                                now=AFTERNOON)

    assert result is None


# ---------------------------------------------------------- on command ----

def test_asking_for_a_pull_ignores_the_schedule(home):
    """Pulling from a fresh write, on command. A schedule says when the app
    looks by ITSELF. Refusing a person who pressed the button
    would be the app arguing with them about their own data."""
    write_handoff(home / "partner.json")

    result = cli.ingest_pending(Args(home), cfg_for(home, schedule=["13:00"]),
                                on_demand=True, now=MORNING)

    assert result is not None
    assert result["imported"] == 1


def test_a_pull_can_name_one_collector(home):
    cfg = {"collectors": [
        {"id": "partner", "path": str(home / "partner.json")},
        {"id": "other", "path": str(home / "other.json")},
    ]}
    write_handoff(home / "partner.json", n=1)
    write_handoff(home / "other.json", n=2)

    result = cli.ingest_pending(Args(home), cfg, only="partner",
                                on_demand=True, now=MORNING)

    assert [s["id"] for s in result["sources"]] == ["partner"]
    assert result["imported"] == 1


def test_pull_all_takes_every_enabled_collector(home):
    """The positive control for the one above: the same two files, no filter."""
    cfg = {"collectors": [
        {"id": "partner", "path": str(home / "partner.json")},
        {"id": "other", "path": str(home / "other.json")},
    ]}
    write_handoff(home / "partner.json", n=1)
    write_handoff(home / "other.json", n=2)

    result = cli.ingest_pending(Args(home), cfg, on_demand=True, now=MORNING)

    assert sorted(s["id"] for s in result["sources"]) == ["other", "partner"]
    assert result["imported"] == 2


def test_naming_a_collector_that_is_not_configured_says_so(home, capsys):
    """Silently importing nothing is the wrong answer to a typo: it looks
    exactly like a collector that had nothing new."""
    import argparse

    from unlatched import config as config_mod

    config_mod.save(dict(config_mod.defaults(), **cfg_for(home)), home)
    args = argparse.Namespace(home=str(home), json=False, force=False,
                              collector="pratner")

    code = cli.cmd_ingest(args)

    assert code == 1
    assert "pratner" in capsys.readouterr().err
