"""A re-screen must not take back what a person put here.

A job added by hand, or imported from somebody else's collector, is stored
qualified on purpose: somebody is telling the app they are interested, and the
title filter and salary floor are not the app's to overrule. The re-screen used
to write its own verdict over every row, so criteria that later failed one of
these turned it unqualified - and prune deletes unqualified rows nobody touched.
Measured 2026-09-10 on a throwaway home before the fix: qualified went 1 -> 0
and prune.plan listed the row.

Both directions are tested. A row a built-in collector wrote must still go
unqualified, or the fix would simply have switched re-screening off.
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING

from unlatched import cli, config, db, importer, manual, prune

if TYPE_CHECKING:
    from pathlib import Path


def _filter_that_fails_everything(home: Path) -> dict:
    assert cli.main(["--home", str(home), "init"]) == 0
    path = home / "config.json"
    cfg = json.loads(path.read_text(encoding="utf-8"))
    cfg.setdefault("search", {})["title_include"] = ["Nonexistent Role Title Filter"]
    path.write_text(json.dumps(cfg), encoding="utf-8")
    return config.load(home)


def _qualified(home: Path) -> dict[str, int]:
    con = db.connect(home)
    try:
        return {r["source"]: r["qualified"]
                for r in con.execute("SELECT source, qualified FROM jobs")}
    finally:
        con.close()


def test_a_rescreen_keeps_what_a_person_added(home):
    cfg = _filter_that_fails_everything(home)
    con = db.connect(home)
    manual.add(con, cfg, "https://example.com/jobs/1",
               title="Warehouse Associate", company="Acme", no_fetch=True)
    importer.import_row(con, cfg, {"url": "https://example.com/jobs/2",
                                   "title": "Warehouse Lead", "company": "Acme"})
    con.close()

    assert cli.main(["--home", str(home), "screen"]) == 0

    assert _qualified(home) == {manual.SOURCE_NAME: 1, importer.SOURCE_NAME: 1}
    con = db.connect(home)
    try:
        assert prune.plan(con).doomed == 0, "prune would delete a row a person added"
    finally:
        con.close()


def test_a_rescreen_still_screens_what_a_collector_wrote(home):
    _filter_that_fails_everything(home)
    con = db.connect(home)
    db.upsert_job(con, "greenhouse:123", {
        "title": "Support Analyst", "source": "greenhouse",
        "url": "https://example.com/job/123", "qualified": 1})
    con.close()

    assert cli.main(["--home", str(home), "screen"]) == 0

    assert _qualified(home) == {"greenhouse": 0}
