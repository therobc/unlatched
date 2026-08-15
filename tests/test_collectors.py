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

LINKEDIN = {"id": "linkedin", "label": "LinkedIn collector",
            "pushes_closures": True}
INDEED = {"id": "indeed", "label": "Indeed collector"}


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
    silently stopped his handoff would not show until jobs went missing."""
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
    cfg = {"collectors": [dict(LINKEDIN, path=str(home / "li.json"))]}

    found = collectors.enabled(cfg)

    assert [c.id for c in found] == ["linkedin"]
    assert found[0].marker == "ingest_last:linkedin"
    assert found[0].name == "LinkedIn collector"


def test_the_minimum_entry_is_an_id_and_a_path(home):
    cfg = {"collectors": [{"id": "indeed", "path": str(home / "i.json")}]}

    entry = collectors.enabled(cfg)[0]

    assert entry.enabled is True
    assert entry.schedule == ()
    assert entry.we_may_refetch is False
    assert entry.pushes_closures is False
    assert entry.name == "indeed", "with no label it is called by its id"


@pytest.mark.parametrize(("entry", "expected"), [
    ({"path": "x.json"}, "collector 0"),
    ({"id": "linkedin"}, "needs a path"),
    ({"id": "bad:id", "path": "x.json"}, "not usable"),
])
def test_an_unusable_entry_is_reported_and_the_others_still_run(entry, expected,
                                                                home):
    """One bad entry must not cost the good ones - the same reasoning as one
    malformed row inside a handoff."""
    cfg = {"collectors": [entry, {"id": "indeed", "path": str(home / "i.json")}]}

    found, problems = collectors.configured(cfg)

    assert [c.id for c in found] == ["indeed"]
    assert len(problems) == 1
    assert expected in problems[0]


def test_two_entries_cannot_share_one_namespace(home):
    cfg = {"collectors": [
        {"id": "linkedin", "path": str(home / "a.json")},
        {"id": "linkedin", "path": str(home / "b.json")},
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
    same_url = "https://www.linkedin.com/jobs/view/4435183193"
    write_handoff(home / "li.json", [job(1, same_url)])
    write_handoff(home / "in.json", [job(2, same_url)])
    cfg = dict(cfg, collectors=[
        dict(LINKEDIN, path=str(home / "li.json")),
        dict(INDEED, path=str(home / "in.json")),
    ])
    (home / "config.json").write_text(json.dumps(cfg), encoding="utf-8")

    result = cli.ingest_pending(Args(home), cfg)

    assert result is not None
    assert result["imported"] == 2, "one collector overwrote the other"
    assert sorted(s["id"] for s in result["sources"]) == ["indeed", "linkedin"]

    from unlatched import db as db_mod
    con = db_mod.connect(home)
    rows = con.execute("SELECT key, source FROM jobs ORDER BY source").fetchall()
    con.close()
    assert [r["source"] for r in rows] == ["indeed", "linkedin"]
    assert all(r["key"].startswith(f"{r['source']}:") for r in rows)


def test_each_collector_is_marked_separately(home, cfg):
    """One collector's file arriving must not mark the other's as taken."""
    write_handoff(home / "li.json", [job(1, "https://example.invalid/1")])
    write_handoff(home / "in.json", [job(2, "https://example.invalid/2")])
    cfg = dict(cfg, collectors=[
        dict(LINKEDIN, path=str(home / "li.json")),
        dict(INDEED, path=str(home / "in.json")),
    ])

    cli.ingest_pending(Args(home), cfg)

    from unlatched import db as db_mod
    con = db_mod.connect(home)
    marks = {row[0]: row[1] for row in con.execute(
        "SELECT key, value FROM meta WHERE key LIKE 'ingest_last%'")}
    con.close()
    assert set(marks) == {"ingest_last:linkedin", "ingest_last:indeed"}
    # EACH MARKER HOLDS ITS OWN FILE'S FINGERPRINT, checked against the file
    # rather than against the other marker: two files written in the same
    # moment at the same size legitimately share a fingerprint, so "they
    # differ" would be a property of the fixture, not of the code.
    for ident, name in (("linkedin", "li.json"), ("indeed", "in.json")):
        stat = (home / name).stat()
        assert marks[f"ingest_last:{ident}"] == f"{stat.st_mtime_ns}:{stat.st_size}"


def test_an_unchanged_file_is_not_taken_twice(home, cfg):
    write_handoff(home / "li.json", [job(1, "https://example.invalid/1")])
    cfg = dict(cfg, collectors=[dict(LINKEDIN, path=str(home / "li.json"))])

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
        dict(LINKEDIN, path=str(home / "li.json"), enabled=False)])

    assert cli.ingest_pending(Args(home), cfg) is None


def test_one_broken_file_does_not_stop_the_other_collector(home, cfg, capsys):
    home.mkdir(parents=True, exist_ok=True)
    (home / "broken.json").write_text("{not json", encoding="utf-8")
    write_handoff(home / "in.json", [job(2, "https://example.invalid/2")])
    cfg = dict(cfg, collectors=[
        dict(LINKEDIN, path=str(home / "broken.json")),
        dict(INDEED, path=str(home / "in.json")),
    ])

    result = cli.ingest_pending(Args(home), cfg)

    assert result is not None
    assert result["imported"] == 1
    assert [s["id"] for s in result["sources"]] == ["indeed"]
    assert "could not take in" in capsys.readouterr().err


def test_a_closure_written_with_the_old_prefix_still_closes_the_row(home, cfg):
    """THE QUIETEST WAY THIS COULD HAVE GONE WRONG.

 rekey.py corrected 410 rows from `manual:` to `imported:`
    on 2026-08-13. The collector on the other side still writes `manual:` keys,
    in its rows AND in its closures. A closure that does not match is not an
    error - the posting simply stays on the board looking live while the sender
    believes it reported it, which is the failure that is hardest to notice.
    """
    url = "https://www.linkedin.com/jobs/view/4435183193"
    write_handoff(home / "li.json",
                  [dict(job(1, url), key="manual:linkedin-4435183193")])
    cfg = dict(cfg, collectors=[dict(LINKEDIN, path=str(home / "li.json"))])
    first = cli.ingest_pending(Args(home), cfg)
    assert first is not None
    assert first["imported"] == 1

    # The sender's next file: same posting, now reported closed, still using
    # the prefix it has always used.
    write_handoff(home / "li.json", [], closed=["manual:linkedin-4435183193"])
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
    cfg = dict(cfg, collectors=[dict(LINKEDIN, path=str(home / "li.json"))])
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
    cfg = {"collectors": [{"id": "indeed", "path": str(home / "i.json")}]}

    rule = collectors.Refetch.from_config(cfg)

    assert rule.may_refetch("indeed") is False


def test_only_the_collectors_that_asked_are_refetchable(home):
    cfg = {"collectors": [
        {"id": "linkedin", "path": str(home / "a.json"), "we_may_refetch": True},
        {"id": "indeed", "path": str(home / "b.json")},
    ]}

    rule = collectors.Refetch.from_config(cfg)

    assert rule.may_refetch("linkedin") is True
    assert rule.may_refetch("indeed") is False
    assert rule.may_refetch("greenhouse") is False, (
        "a source that is not a configured collector is not refetchable "
        "through this rule at all")
