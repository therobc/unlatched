"""The starter pack repairs its own board references instead of rotting.

starter.py ships 50 employers with an ATS reference each, measured on one day.
An employer that moves to a different system does not announce it: `collect`
keeps asking the stored reference, gets nothing back, and that employer goes
silent for ever with nothing on screen to distinguish it from one that simply
is not hiring.

WHAT IS ASSERTED HERE IS THE TRIGGER, not the discovery. rediscover.plan and
discover.resolve have their own tests; what could not go wrong quietly there
and can here is WHEN a re-probe happens - too eagerly and a collect becomes a
crawl of fifty careers sites, too rarely and the pack still rots.

So every test below is about the conditions: how many empty runs, whose
employers, how many per run, and what a failed probe is allowed to overwrite
(nothing).
"""
from __future__ import annotations

import pytest

from unlatched import db, rediscover


def _company(con, name: str, *, ats: str = "greenhouse", ref: str = "acme",
             origin: str = db.SEEDED) -> int:
    return db.upsert_company(con, name, ats=ats, ats_ref=ref,
                             probe_status="yielding", origin=origin)


def _row(con, name: str):
    return db.get_company(con, name)


# ---- counting quiet runs ----------------------------------------------------

def test_a_board_that_answers_clears_the_quiet_count(con):
    """Reset on ANY posting, not a qualified one. The question is whether the
    reference still reaches a board; a board full of jobs the person does not
    want is a board answering perfectly."""
    cid = _company(con, "Northwind Systems")

    assert rediscover.note_collect_result(con, cid, 0) == 1
    assert rediscover.note_collect_result(con, cid, 0) == 2
    assert rediscover.note_collect_result(con, cid, 7) == 0
    assert rediscover.note_collect_result(con, cid, 0) == 1


def test_the_count_survives_between_runs(con):
    """It lives in `meta`, so it has to outlast the process that wrote it -
    three empty collects on three different days is the case this exists for,
    not three inside one run."""
    cid = _company(con, "Fabrikam Retail")
    rediscover.note_collect_result(con, cid, 0)
    stored = db.get_meta(con, rediscover.quiet_key(cid))
    assert stored == "1", "the quiet count is not being persisted"


# ---- who is due, and when ---------------------------------------------------

def test_nothing_is_re_probed_before_three_empty_collects(con):
    """A board legitimately returns nothing all the time. Re-probing on the
    first empty result would turn every quiet week into a crawl."""
    cid = _company(con, "Contoso Health")
    row = _row(con, "Contoso Health")
    for runs in (1, 2):
        rediscover.note_collect_result(con, cid, 0)
        assert not rediscover.due_for_healing(row, runs), (
            f"re-probed after only {runs} empty collection(s)")
    assert rediscover.due_for_healing(row, rediscover.QUIET_RUNS)


def test_an_employer_that_never_had_a_board_is_never_re_probed(con):
    """Its silence says nothing about a reference having gone stale - there is
    no reference. Probing it would be discovery, which is a different verb the
    person runs deliberately."""
    _company(con, "Tailspin Toys", ats="", ref="")
    row = _row(con, "Tailspin Toys")
    assert not rediscover.due_for_healing(row, rediscover.QUIET_RUNS + 5)


@pytest.mark.parametrize("origin", [db.MANUAL, db.IMPORTED])
def test_only_our_own_employers_are_repaired(con, origin):
    """A hand-added or imported employer's reference came from somebody else.
    Rewriting it would be answering a question this app was not asked."""
    _company(con, f"Adventure Works {origin}", origin=origin)
    row = _row(con, f"Adventure Works {origin}")
    assert not rediscover.due_for_healing(row, rediscover.QUIET_RUNS + 5)


@pytest.mark.parametrize("origin", [db.SEEDED, db.DISCOVERED])
def test_seeded_and_discovered_employers_are_repaired(con, origin):
    """The positive control for the test above. Without it, a rule that
    refused EVERY origin would pass that test perfectly."""
    _company(con, f"Litware {origin}", origin=origin)
    row = _row(con, f"Litware {origin}")
    assert rediscover.due_for_healing(row, rediscover.QUIET_RUNS)


# ---- what a probe is allowed to do ------------------------------------------

def _resolving_to(ats: str, ref: str):
    """A fake discover.resolve result carrying one fingerprint."""
    parts = ref.split("|") if ats in discover_compound() else [ref]
    return {"company": "", "domain": "example.invalid",
            "careers_url": "https://careers.example.invalid/",
            "ats": [{"provider": ats, "parts": parts}], "portals": [], "note": ""}


def discover_compound():
    from unlatched import discover
    return discover.COMPOUND_REF


def test_a_move_is_written(con, monkeypatch):
    """The point of the whole feature: the employer moved from Greenhouse to
    Lever, and the next collect asks Lever."""
    from unlatched import discover

    _company(con, "Woodgrove Bank", ats="greenhouse", ref="woodgrove")
    monkeypatch.setattr(discover, "resolve",
                        lambda *_a, **_k: _resolving_to("lever", "woodgrove"))

    finding = rediscover.heal_one(con, "Woodgrove Bank", fetcher=lambda *a, **k: None)

    assert finding is not None
    assert finding.outcome == rediscover.MOVED
    row = _row(con, "Woodgrove Bank")
    assert row["ats"] == "lever"
    assert row["ats_ref"] == "woodgrove"


def test_a_probe_that_finds_nothing_leaves_the_reference_alone(con, monkeypatch):
    """THE ONE THAT MATTERS MOST. A careers site down for an afternoon must
    never cost an employer its board reference - that would take them out of
    every future collect over a bad connection, which is worse than the rot
    this feature exists to fix."""
    from unlatched import discover

    _company(con, "Proseware Inc", ats="greenhouse", ref="proseware")
    monkeypatch.setattr(discover, "resolve", lambda *_a, **_k: {
        "company": "", "domain": "", "careers_url": "", "ats": [],
        "portals": [], "note": "no domain resolved"})

    finding = rediscover.heal_one(con, "Proseware Inc", fetcher=lambda *a, **k: None)

    assert finding is not None
    assert finding.outcome == rediscover.UNREADABLE
    row = _row(con, "Proseware Inc")
    assert row["ats"] == "greenhouse", "a failed probe blanked a working board"
    assert row["ats_ref"] == "proseware"


def test_unreadable_is_not_among_the_outcomes_that_write():
    """Stated as a property of the module rather than only exercised above, so
    adding a fifth outcome cannot quietly make blanking possible."""
    assert rediscover.UNREADABLE not in rediscover.WRITES
    assert rediscover.UNCHANGED not in rediscover.WRITES
    assert set(rediscover.WRITES) == {rediscover.MOVED, rediscover.NOW_READABLE}


# ---- the bound --------------------------------------------------------------

def test_a_run_repairs_at_most_a_few_employers():
    """A network outage makes EVERY board look quiet at once, which is exactly
    when re-probing fifty careers sites would be both useless and rude. The
    cap is what keeps a bad afternoon from turning a collect into a sweep."""
    assert rediscover.MAX_HEALS_PER_RUN <= 5, (
        "a collect that re-probes more than a handful of employers is the "
        "scheduled crawl rediscover.py refuses to be")
    assert rediscover.QUIET_RUNS >= 2, (
        "one empty collection is an ordinary quiet week, not evidence")


def test_the_counter_is_cleared_after_a_probe(con, monkeypatch):
    """Whether or not the probe found anything. Leaving the count at the
    threshold would re-probe this employer on every collect from then on -
    which is the scheduled crawl, arrived at by accident."""
    from unlatched import discover

    cid = _company(con, "Fourth Coffee", ats="greenhouse", ref="fourth")
    for _ in range(rediscover.QUIET_RUNS):
        rediscover.note_collect_result(con, cid, 0)
    assert db.get_meta(con, rediscover.quiet_key(cid)) == str(rediscover.QUIET_RUNS)

    monkeypatch.setattr(discover, "resolve", lambda *_a, **_k: {
        "company": "", "domain": "", "careers_url": "", "ats": [],
        "portals": [], "note": ""})
    rediscover.heal_one(con, "Fourth Coffee", fetcher=lambda *a, **k: None)
    # cli.cmd_collect clears it; assert the value it writes is what stops the
    # re-probe rather than trusting the call site to remember.
    db.set_meta(con, rediscover.quiet_key(cid), "0")
    row = _row(con, "Fourth Coffee")
    assert not rediscover.due_for_healing(row, 0)


def test_an_employer_with_no_fingerprint_becoming_readable_is_written(con, monkeypatch):
    """NOW_READABLE is the other half of the repair: an employer we could not
    fingerprint when the pack was built, who has since put a board up."""
    from unlatched import discover

    _company(con, "Humongous Insurance", ats="", ref="")
    monkeypatch.setattr(discover, "resolve",
                        lambda *_a, **_k: _resolving_to("ashby", "humongous"))

    finding = rediscover.heal_one(con, "Humongous Insurance",
                                   fetcher=lambda *a, **k: None)

    assert finding.outcome == rediscover.NOW_READABLE
    assert _row(con, "Humongous Insurance")["ats"] == "ashby"


# ---- the wiring -------------------------------------------------------------

EMPTY_BOARD = '{"jobs": []}'


def _greenhouse_url(slug: str) -> str:
    return f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"


def test_three_empty_collects_repair_the_reference(home, monkeypatch):
    """End to end through cmd_collect, because the wiring is where this would
    silently stop working - every piece can be correct while nothing calls
    them.

    The board answers, politely, with no jobs. That is indistinguishable from
    an employer who is not hiring until it happens three times, which is
    exactly what the counter is for.
    """
    from unlatched import cli, db, discover

    con = db.connect(home)
    db.upsert_company(con, "Northwind Systems", ats="greenhouse",
                      ats_ref="northwind", probe_status="yielding",
                      origin=db.SEEDED)
    con.close()

    def board(url, **_kwargs):
        if url == _greenhouse_url("northwind"):
            return 200, EMPTY_BOARD, url
        return 0, "", url

    monkeypatch.setattr(cli.fetch_mod, "fetch", board)
    monkeypatch.setattr(discover, "resolve", lambda *_a, **_k: {
        "company": "Northwind Systems", "domain": "northwind.invalid",
        "careers_url": "https://careers.northwind.invalid/",
        "ats": [{"provider": "lever", "parts": ["northwind"]}],
        "portals": [], "note": ""})

    def stored_ats() -> tuple[str, str]:
        c = db.connect(home)
        row = db.get_company(c, "Northwind Systems")
        answer = (str(row["ats"]), str(row["ats_ref"]))
        c.close()
        return answer

    # Two quiet collections are not yet evidence.
    for run in (1, 2):
        assert cli.main(["--home", str(home), "collect"]) == 0
        assert stored_ats() == ("greenhouse", "northwind"), (
            f"the reference was rewritten after only {run} empty collection(s)")

    # The third is.
    assert cli.main(["--home", str(home), "collect"]) == 0
    assert stored_ats() == ("lever", "northwind"), (
        "three empty collections did not trigger a repair - the counter, the "
        "due check and the probe can each be right while nothing joins them")


def test_a_board_that_answers_is_never_re_probed(home, monkeypatch):
    """The negative control. A collector returning postings must never cause a
    careers-site probe, however many times it runs - that would be the
    scheduled crawl arrived at from the other direction."""
    from unlatched import cli, db, discover

    con = db.connect(home)
    db.upsert_company(con, "Fabrikam Retail", ats="greenhouse",
                      ats_ref="fabrikam", probe_status="yielding",
                      origin=db.SEEDED)
    con.close()

    one_job = ('{"jobs": [{"id": 1, "title": "Support Analyst", '
               '"location": {"name": "Remote"}, '
               '"absolute_url": "https://example.invalid/j/1", '
               '"updated_at": "2026-08-01", "content": "A real posting."}]}')

    def board(url, **_kwargs):
        if url == _greenhouse_url("fabrikam"):
            return 200, one_job, url
        return 0, "", url

    def explode(*_args, **_kwargs):
        raise AssertionError("a board that answered must not be re-probed")

    monkeypatch.setattr(cli.fetch_mod, "fetch", board)
    monkeypatch.setattr(discover, "resolve", explode)

    for _ in range(rediscover.QUIET_RUNS + 2):
        assert cli.main(["--home", str(home), "collect"]) == 0

    # THE PREMISE, SAID OUT LOUD. `explode` is what actually fails this test,
    # and it would also fire if the board URL were wrong and every collection
    # came back empty - the right failure for the wrong reason. Asserting the
    # posting landed says the board really did answer, so a pass means what it
    # claims: answered every time, probed none of them.
    con = db.connect(home)
    kept = con.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    quiet = db.get_meta(con, rediscover.quiet_key(1))
    con.close()
    assert kept == 1, "the board never answered, so nothing was actually tested"
    assert quiet in (None, "0"), f"an answering board still counted quiet: {quiet}"
