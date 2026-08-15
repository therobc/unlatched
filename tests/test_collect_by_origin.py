"""Collecting from one KIND of employer, which is what the manual menu needs.

The first user asked for a manual collect menu offering "all boards" and "seeded
companies" as separate actions, and for the seeded list to be refreshable on
its own. That cannot be expressed without knowing which employers were seeded,
which is what companies.origin records.
"""
from __future__ import annotations

import pytest

from unlatched import cli, db


def _collected_names(cfg, monkeypatch, argv):
    """Run a collect and report which employers its collector was asked for.

    THE CONNECTION IS NOT HANDED IN. _collect opens its own and CLOSES it when
    it finishes, so a test that passed one in could only ever run a single
    collect - the second died on "cannot operate on a closed database". Letting
    it open its own is also closer to what really happens.
    """
    asked = []

    class _Collector:
        @staticmethod
        def collect(ats_ref, fetcher=None):
            asked.append(ats_ref)
            return []

    monkeypatch.setattr(cli.sources, "registry", lambda: {"greenhouse": _Collector})
    monkeypatch.setattr(cli.config, "load", lambda home: cfg)

    args = cli.build_parser().parse_args(argv)
    cli._collect(args)  # noqa: SLF001 - the lock wrapper would fight the fixture
    return asked


def _three_employers(con):
    db.upsert_company(con, "Shipped", ats="greenhouse", ats_ref="shipped",
                      origin=db.SEEDED)
    db.upsert_company(con, "Found", ats="greenhouse", ats_ref="found",
                      origin=db.DISCOVERED)
    db.upsert_company(con, "Old one", ats="greenhouse", ats_ref="old")
    con.commit()


def test_no_origin_collects_from_every_employer(con, cfg, monkeypatch):
    _three_employers(con)
    con.close()
    asked = _collected_names(cfg, monkeypatch, ["collect"])
    assert sorted(asked) == ["found", "old", "shipped"]


def test_seeded_collects_only_the_shipped_ones(con, cfg, monkeypatch):
    _three_employers(con)
    con.close()
    asked = _collected_names(cfg, monkeypatch, ["collect", "--origin", db.SEEDED])
    assert asked == ["shipped"]


def test_discovered_collects_only_the_ones_the_app_found(con, cfg, monkeypatch):
    _three_employers(con)
    con.close()
    asked = _collected_names(cfg, monkeypatch,
                             ["collect", "--origin", db.DISCOVERED])
    assert asked == ["found"]


@pytest.mark.parametrize("origin", ["seeded", "discovered", "manual", "imported"])
def test_a_row_predating_the_column_is_not_swept_into_any_set(origin, con, cfg,
                                                               monkeypatch):
    """Honestly unknown, so it matches nothing.

    The alternative - defaulting old rows to 'seeded' or 'discovered' - would
    put employers nobody chose into a set the app is about to fetch on their
    behalf, which is exactly the kind of quiet over-reach the collect rules
    exist to prevent.

    PARAMETRISED rather than looped: a loop inside one test shares a single
    collect's closed connection, and it also reports one failure for four
    cases instead of naming the one that leaked.
    """
    _three_employers(con)
    con.close()
    asked = _collected_names(cfg, monkeypatch, ["collect", "--origin", origin])
    assert "old" not in asked, f"the unlabelled row leaked into {origin}"


def test_an_unknown_origin_is_refused_at_the_command_line():
    """argparse choices, so a typo fails loudly instead of silently
    collecting from nothing and reporting success."""
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["collect", "--origin", "sedeed"])
