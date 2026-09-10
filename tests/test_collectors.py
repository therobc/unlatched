"""Several collectors, each in its own namespace, none of them trusted.

What this file covers directly:

  * two collectors run without corrupting each other
  * a collector cannot widen what the app may fetch
  * a handoff path configured before namespacing existed still works
"""
from __future__ import annotations

import json

import pytest

from unlatched import cli, collectors, importer

MYBOARD = {"id": "myboard", "label": "MyBoard collector",
            "pushes_closures": True}
OTHERTOOL = {"id": "othertool", "label": "Other tool collector"}


def write_handoff(path, jobs, closed=None):
    payload = {"version": 1, "generated_at": "2026-08-12T09:00:00+00:00",
               "jobs": jobs}
    if closed is not None:
        payload["closed"] = closed
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def job(n, url):
    return {"title": f"Support Analyst {n}", "company": "Acme", "url": url}


class Args:
    """The two attributes ingest_pending reads off an argparse namespace."""

    def __init__(self, home):
        self.home = str(home)
        self.json = True


# --------------------------------------------------------------- config ---

def test_an_old_config_keeps_working_without_a_collectors_list(home):
    """A real profile is live and pulls daily; an upgrade that
    silently stopped their handoff would not show until jobs went missing."""
    cfg = {"ingest": {"path": str(home / "handoff.json")}}

    found = collectors.enabled(cfg)

    assert len(found) == 1
    assert found[0].id == importer.SOURCE_NAME
    assert found[0].path == str(home / "handoff.json")
    # AND ITS MARKER IS THE OLD ONE. A new marker would read as "never taken
    # in" and re-import the current file - and re-importing is not free,
    # because relist() clears delisted_at and resurrects closed rows.
    assert found[0].marker == "ingest_last"


def test_a_named_collector_gets_its_own_marker(home):
    cfg = {"collectors": [dict(MYBOARD, path=str(home / "li.json"))]}

    found = collectors.enabled(cfg)

    assert [c.id for c in found] == ["myboard"]
    assert found[0].marker == "ingest_last:myboard"
    assert found[0].name == "MyBoard collector"


def test_the_minimum_entry_is_an_id_and_a_path(home):
    cfg = {"collectors": [{"id": "othertool", "path": str(home / "i.json")}]}

    entry = collectors.enabled(cfg)[0]

    assert entry.enabled is True
    assert entry.schedule == ()
    assert entry.we_may_refetch is False
    assert entry.pushes_closures is False
    assert entry.name == "othertool", "with no label it is called by its id"


@pytest.mark.parametrize(("entry", "expected"), [
    ({"path": "x.json"}, "collector 0"),
    ({"id": "myboard"}, "needs a path"),
    ({"id": "bad:id", "path": "x.json"}, "not usable"),
])
def test_an_unusable_entry_is_reported_and_the_others_still_run(entry, expected,
                                                                home):
    """One bad entry must not cost the good ones - the same reasoning as one
    malformed row inside a handoff."""
    cfg = {"collectors": [entry, {"id": "othertool", "path": str(home / "i.json")}]}

    found, problems = collectors.configured(cfg)

    assert [c.id for c in found] == ["othertool"]
    assert len(problems) == 1
    assert expected in problems[0]


def test_two_entries_cannot_share_one_namespace(home):
    cfg = {"collectors": [
        {"id": "myboard", "path": str(home / "a.json")},
        {"id": "myboard", "path": str(home / "b.json")},
    ]}

    found, problems = collectors.configured(cfg)

    assert len(found) == 1, "the second entry must not join the first's rows"
    assert "more than once" in problems[0]


# ---------------------------------------------------------- taking rows ---

def test_two_collectors_land_together_and_keep_their_own_provenance(home, cfg):
    """The test that would have caught the key collision.

    Both files carry the SAME posting url, which is the case that used to have
    the second collector overwrite the first.
    """
    same_url = "https://www.example.com/jobs/view/4400330022"
    write_handoff(home / "li.json", [job(1, same_url)])
    write_handoff(home / "in.json", [job(2, same_url)])
    cfg = dict(cfg, collectors=[
        dict(MYBOARD, path=str(home / "li.json")),
        dict(OTHERTOOL, path=str(home / "in.json")),
    ])
    (home / "config.json").write_text(json.dumps(cfg), encoding="utf-8")

    result = cli.ingest_pending(Args(home), cfg)

    assert result is not None
    assert result["imported"] == 2, "one collector overwrote the other"
    assert sorted(s["id"] for s in result["sources"]) == ["myboard", "othertool"]

    from unlatched import db as db_mod
    con = db_mod.connect(home)
    rows = con.execute("SELECT key, source FROM jobs ORDER BY source").fetchall()
    con.close()
    assert [r["source"] for r in rows] == ["myboard", "othertool"]
    assert all(r["key"].startswith(f"{r['source']}:") for r in rows)


def test_each_collector_is_marked_separately(home, cfg):
    """One collector's file arriving must not mark the other's as taken."""
    write_handoff(home / "li.json", [job(1, "https://example.invalid/1")])
    write_handoff(home / "in.json", [job(2, "https://example.invalid/2")])
    cfg = dict(cfg, collectors=[
        dict(MYBOARD, path=str(home / "li.json")),
        dict(OTHERTOOL, path=str(home / "in.json")),
    ])

    cli.ingest_pending(Args(home), cfg)

    from unlatched import db as db_mod
    con = db_mod.connect(home)
    marks = {row[0]: row[1] for row in con.execute(
        "SELECT key, value FROM meta WHERE key LIKE 'ingest_last%'")}
    con.close()
    assert set(marks) == {"ingest_last:myboard", "ingest_last:othertool"}
    # EACH MARKER HOLDS ITS OWN FILE'S FINGERPRINT, checked against the file
    # rather than against the other marker: two files written in the same
    # moment at the same size legitimately share a fingerprint, so "they
    # differ" would be a property of the fixture, not of the code.
    for ident, name in (("myboard", "li.json"), ("othertool", "in.json")):
        stat = (home / name).stat()
        assert marks[f"ingest_last:{ident}"] == f"{stat.st_mtime_ns}:{stat.st_size}"


def test_an_unchanged_file_is_not_taken_twice(home, cfg):
    write_handoff(home / "li.json", [job(1, "https://example.invalid/1")])
    cfg = dict(cfg, collectors=[dict(MYBOARD, path=str(home / "li.json"))])

    first = cli.ingest_pending(Args(home), cfg)
    second = cli.ingest_pending(Args(home), cfg)

    assert first is not None
    assert first["imported"] == 1
    assert second is None, (
        "re-taking an unchanged file resurrects rows closed since it was "
        "written, because import calls relist()")


def test_a_disabled_collector_is_not_read(home, cfg):
    write_handoff(home / "li.json", [job(1, "https://example.invalid/1")])
    cfg = dict(cfg, collectors=[
        dict(MYBOARD, path=str(home / "li.json"), enabled=False)])

    assert cli.ingest_pending(Args(home), cfg) is None


def test_one_broken_file_does_not_stop_the_other_collector(home, cfg, capsys):
    home.mkdir(parents=True, exist_ok=True)
    (home / "broken.json").write_text("{not json", encoding="utf-8")
    write_handoff(home / "in.json", [job(2, "https://example.invalid/2")])
    cfg = dict(cfg, collectors=[
        dict(MYBOARD, path=str(home / "broken.json")),
        dict(OTHERTOOL, path=str(home / "in.json")),
    ])

    result = cli.ingest_pending(Args(home), cfg)

    assert result is not None
    assert result["imported"] == 1
    assert [s["id"] for s in result["sources"]] == ["othertool"]
    assert "could not take in" in capsys.readouterr().err


def test_a_closure_written_with_the_old_prefix_still_closes_the_row(home, cfg):
    """THE QUIETEST WAY THIS COULD HAVE GONE WRONG.

    rekey.py corrected 410 rows from `manual:` to `imported:`
    on 2026-08-13. The collector on the other side still writes `manual:` keys,
    in its rows AND in its closures. A closure that does not match is not an
    error - the posting simply stays on the board looking live while the sender
    believes it reported it, which is the failure that is hardest to notice.
    """
    url = "https://www.example.com/jobs/view/4400330022"
    write_handoff(home / "li.json",
                  [dict(job(1, url), key="manual:myboard-4400330022")])
    cfg = dict(cfg, collectors=[dict(MYBOARD, path=str(home / "li.json"))])
    first = cli.ingest_pending(Args(home), cfg)
    assert first is not None
    assert first["imported"] == 1

    # The sender's next file: same posting, now reported closed, still using
    # the prefix it has always used.
    write_handoff(home / "li.json", [], closed=["manual:myboard-4400330022"])
    second = cli.ingest_pending(Args(home), cfg)

    assert second is not None
    assert second["closed"] == 1, "the closure did not match the row it named"

    # Looked up as THE row rather than by a key spelled out here: what matters
    # is that the closure reached the row the sender named, not that the key
    # came out in any particular shape.
    from unlatched import db as db_mod
    con = db_mod.connect(home)
    rows = con.execute("SELECT key, delisted_at FROM jobs").fetchall()
    con.close()
    assert len(rows) == 1
    assert rows[0]["delisted_at"], "the row is still on the board looking live"


def test_a_closure_for_a_posting_nobody_holds_is_still_reported(home, cfg):
    """The normalisation must not turn "we disagree about identity" into
    silence: an unknown key is a real problem wearing the costume of a no-op."""
    write_handoff(home / "li.json", [job(1, "https://example.invalid/1")])
    cfg = dict(cfg, collectors=[dict(MYBOARD, path=str(home / "li.json"))])
    cli.ingest_pending(Args(home), cfg)

    write_handoff(home / "li.json", [], closed=["manual:never-heard-of-it"])
    result = cli.ingest_pending(Args(home), cfg)

    assert result is not None
    assert result["closed"] == 0
    assert result["closed_unknown"] == ["manual:never-heard-of-it"]


# ------------------------------------------------------------- refetch ----

def test_a_collector_that_says_nothing_may_not_be_refetched(home):
    """First half. The default is the safe direction: a collector has to
    ask before this app makes requests on its rows' behalf."""
    cfg = {"collectors": [{"id": "othertool", "path": str(home / "i.json")}]}

    rule = collectors.Refetch.from_config(cfg)

    assert rule.may_refetch("othertool") is False


def test_only_the_collectors_that_asked_are_refetchable(home):
    cfg = {"collectors": [
        {"id": "myboard", "path": str(home / "a.json"), "we_may_refetch": True},
        {"id": "othertool", "path": str(home / "b.json")},
    ]}

    rule = collectors.Refetch.from_config(cfg)

    assert rule.may_refetch("myboard") is True
    assert rule.may_refetch("othertool") is False
    assert rule.may_refetch("greenhouse") is False, (
        "a source that is not a configured collector is not refetchable "
        "through this rule at all")


@pytest.mark.parametrize("wrong", [
    {"myboard": {"path": "x.json"}},   # an object - the obvious slip, since
                                       # every other block in config.json is one
    "myboard.json",                    # a bare path
    3,
])
def test_a_collectors_key_that_is_not_a_list_is_reported(home, wrong):
    """Silently discarding it was the same failure this file refuses everywhere
    else. A typo'd schedule is quoted back rather than dropped; a `collectors`
    value of the wrong shape used to be dropped whole, with no problem line, so
    the collectors simply never ran and nothing said why.

    The legacy handoff still applies - the mistake is in the list, and taking
    the person's other working route away as well would make one error into
    two.
    """
    cfg = {"collectors": wrong, "ingest": {"path": str(home / "handoff.json")}}

    found, problems = collectors.configured(cfg)

    assert problems, f"{wrong!r} was discarded without a word"
    assert "list" in problems[0], problems
    assert [c.id for c in found] == [importer.SOURCE_NAME], (
        "the legacy handoff should survive a mistake in the collectors list")


@pytest.mark.parametrize("empty", [None, []])
def test_no_collectors_configured_is_not_a_problem(home, empty):
    """The positive control. Absence is not a mistake, and reporting it would
    put a problem line in front of every person who has never configured one."""
    cfg = {"collectors": empty} if empty is not None else {}
    cfg["ingest"] = {"path": str(home / "handoff.json")}

    found, problems = collectors.configured(cfg)

    assert problems == []
    assert [c.id for c in found] == [importer.SOURCE_NAME]


def test_a_collector_that_caps_itself_declares_the_ceiling():
    """A collector that stops short and says nothing is the silent-truncation
    failure this package keeps meeting: the board fetch cap, robots.txt on
    board APIs, a rejected USAJOBS key, and a query stream that read 500 of
    900. The person sees a small number and no reason to think there were
    more.

    cli.py already reports a ceiling - it compares what came back against the
    module's MAX_COLLECTED. This asserts every collector that bounds itself
    has told it what that bound is. Three of them had not: bamboohr sliced its
    list to 150, nodesk stopped walking at 40 pages, and sitemap fetched the
    first 60 URLs.

    Read off the SOURCE rather than a hand-kept list, so a new collector with
    a new cap is covered the day it lands.
    """
    import re
    from pathlib import Path

    from unlatched import sources

    # Constants that bound how much comes back. Deliberately broad - a cap
    # this misses is a collector that can truncate unnoticed.
    cap_shaped = re.compile(
        r"^(MAX_[A-Z_]*|[A-Z_]*_CAP|DEFAULT_MAX[A-Z_]*|[A-Z_]*PAGES[A-Z_]*)\s*=",
        re.MULTILINE)

    silent = []
    for name, mod in sources.registry().items():
        text = Path(mod.__file__).read_text(encoding="utf-8")
        caps = {m.rstrip(" =") for m in cap_shaped.findall(text)}
        # A collector may report truncation itself instead - usajobs does,
        # because de-duplicating across query streams makes the count
        # comparison unable to fire.
        if not caps:
            continue
        if getattr(mod, "MAX_COLLECTED", None) is None and not callable(
                getattr(mod, "truncated_queries", None)):
            silent.append(f"{name} ({', '.join(sorted(caps))})")

    assert not silent, (
        "these collectors bound what they return and declare no ceiling, so a "
        "truncated run is indistinguishable from a quiet board: "
        + "; ".join(silent))
