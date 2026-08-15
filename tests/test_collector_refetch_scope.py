"""A collector may ask to be re-read. It may not widen what
this app is allowed to read.

Two different rules meet in `manual.recheck` and it matters that they are two:

  SCOPE      which rows are considered. `we_may_refetch` in the collector's
             own entry moves this, and the default is closed.
  PERMISSION which HOSTS this app requests. `may_fetch`, applied per row.
             The person's lists own this and no file can move it.

THE FAILURE THIS FILE EXISTS TO CATCH. Before the change, `recheck` passed a
flat `hand_added=True` for the whole run - correct only because the population
was hand-added rows and nothing else. The moment a collector could put rows in
that population, that flag would have handed the attended-only exception to a
third party's file: LinkedIn, fetched in bulk, unattended, because a JSON key
said so. `hand_added` is now read off each ROW.

EVERY REFUSAL TEST HERE IS PAIRED WITH A POSITIVE CONTROL. A suite asserting
that nothing was fetched passes just as happily when the whole path is broken,
the config was ignored, or the rows were never due - so each of those is proven
fetchable first, in the same run, by the same fetcher.
"""
from __future__ import annotations

from datetime import UTC, datetime

from unlatched import collectors, db, manual, status

STALE = "2026-08-01T00:00:00+00:00"
NOW = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)

GREENHOUSE = "https://boards.greenhouse.io/acme/jobs/{n}"
LINKEDIN = "https://www.linkedin.com/jobs/view/{n}"
INDEED = "https://www.indeed.com/viewjob?jk={n}"


def cfg_for(*entries, reading_on=True):
    """A profile config with these collector entries."""
    return {
        "collectors": [dict(e) for e in entries],
        "fetch": {"read_added_links": reading_on},
    }


def entry(ident, **overrides):
    return {"id": ident, "path": f"C:/nowhere/{ident}.json", **overrides}


def add(con, key, url, *, source, last_seen=STALE):
    cid = db.upsert_company(con, "Acme")
    db.upsert_job(con, key, {
        "company_id": cid, "title": "Analyst", "source": source,
        "url": url, "last_seen": last_seen, "delisted_at": None,
        "qualified": 1, "verdict": "keep",
    })


class Recorder:
    """A fetcher that says every posting is still live, and remembers who was
    asked. What it was NOT asked is the assertion in most of these tests."""

    def __init__(self):
        self.urls: list[str] = []

    def __call__(self, url, **_kwargs):
        self.urls.append(url)
        return (200, "<html>still listed</html>", url)


# ------------------------------------------------------- scope is opt-in ----

def test_a_collectors_rows_are_out_of_scope_until_its_entry_asks(con):
    add(con, "manual:1", GREENHOUSE.format(n=1), source="manual")
    add(con, "partner:1", GREENHOUSE.format(n=2), source="partner")
    cfg = cfg_for(entry("partner"))

    fetcher = Recorder()
    manual.recheck(con, cfg, fetcher=fetcher, now=NOW)

    assert fetcher.urls == [GREENHOUSE.format(n=1)]


def test_the_same_rows_are_read_once_the_collector_asks(con):
    """THE POSITIVE CONTROL for the test above.

    Identical rows, identical hosts, one key changed. If this did not fetch,
    the test above would be proving nothing more than that the plumbing is
    broken."""
    add(con, "manual:1", GREENHOUSE.format(n=1), source="manual")
    add(con, "partner:1", GREENHOUSE.format(n=2), source="partner")
    cfg = cfg_for(entry("partner", we_may_refetch=True))

    fetcher = Recorder()
    manual.recheck(con, cfg, fetcher=fetcher, now=NOW)

    assert sorted(fetcher.urls) == [GREENHOUSE.format(n=1), GREENHOUSE.format(n=2)]


def test_a_disabled_collector_is_out_of_scope_however_it_asks(con):
    """Turning a collector off has to stop the requests too, not only the
    imports - otherwise `enabled: false` leaves half of it running."""
    add(con, "partner:1", GREENHOUSE.format(n=2), source="partner")
    cfg = cfg_for(entry("partner", we_may_refetch=True, enabled=False))

    fetcher = Recorder()
    manual.recheck(con, cfg, fetcher=fetcher, now=NOW)

    assert fetcher.urls == []
    assert manual.recheckable_sources(cfg) == ("manual",)


# --------------------------------------- permission is not the file's to give ----

def test_asking_to_be_refetched_does_not_open_an_attended_only_host(con):
    """The collector is in scope AND its rows are due AND the host is one
    this app can technically read - just not unattended. It is refused."""
    add(con, "partner:1", LINKEDIN.format(n=101), source="partner")
    cfg = cfg_for(entry("partner", we_may_refetch=True))

    fetcher = Recorder()
    manual.recheck(con, cfg, fetcher=fetcher, now=NOW)

    assert fetcher.urls == []


def test_a_person_adding_that_same_link_by_hand_still_gets_it_read(con):
    """THE POSITIVE CONTROL, and the whole point of the per-row flag.

    Same host, same run, same fetcher: the hand-added row IS read and the
    collector's is not. A version that simply blocked LinkedIn everywhere would
    fail here, and a version that passed one flag for the whole run would fetch
    both - so this pins the distinction to the row rather than to the button.
    """
    add(con, "manual:mine", LINKEDIN.format(n=100), source="manual")
    add(con, "partner:theirs", LINKEDIN.format(n=101), source="partner")
    cfg = cfg_for(entry("partner", we_may_refetch=True))

    fetcher = Recorder()
    manual.recheck(con, cfg, fetcher=fetcher, now=NOW)

    assert fetcher.urls == [LINKEDIN.format(n=100)]


def test_asking_to_be_refetched_does_not_open_a_blocked_aggregator(con):
    """The stronger half: NEVER_FETCH is refused for the collector AND for the
    person. Nobody can consent to this one on the app's behalf."""
    add(con, "manual:mine", INDEED.format(n=100), source="manual")
    add(con, "partner:theirs", INDEED.format(n=101), source="partner")
    cfg = cfg_for(entry("partner", we_may_refetch=True))

    fetcher = Recorder()
    manual.recheck(con, cfg, fetcher=fetcher, now=NOW)

    assert fetcher.urls == []


def test_a_collector_named_after_a_blocked_host_gains_nothing(con):
    """The obvious attempt: name the collector `indeed` and declare it
    refetchable. The id is a NAMESPACE, never a permission - the host decides,
    and the host is still Indeed."""
    add(con, "indeed:1", INDEED.format(n=1), source="indeed")
    cfg = cfg_for(entry("indeed", we_may_refetch=True))

    fetcher = Recorder()
    manual.recheck(con, cfg, fetcher=fetcher, now=NOW)

    assert fetcher.urls == []
    # In scope, and refused anyway. If it were out of scope this test would
    # pass for the wrong reason.
    assert "indeed" in manual.recheckable_sources(cfg)


def test_robots_is_still_honoured_for_a_collectors_rows(con):
    """The attended-only exception also turns robots.txt off for that one
    request. A collector's row must never carry that with it, even on a host
    where the fetch is allowed."""
    add(con, "partner:1", GREENHOUSE.format(n=2), source="partner")
    cfg = cfg_for(entry("partner", we_may_refetch=True))
    seen = {}

    def record(url, **kwargs):
        seen.update(kwargs)
        return (200, "<html>still listed</html>", url)

    manual.recheck(con, cfg, fetcher=record, now=NOW)

    assert seen["respect_robots"] is True
    assert seen["url_ok"](LINKEDIN.format(n=1)) is False, (
        "a redirect from a collector's row must not land on an attended-only "
        "host either")


# ------------------------------------------------ the rest of the rules hold ----

def test_a_collectors_row_with_a_status_is_still_left_alone(con):
    """Being refetchable does not exempt a collector from the exclusions the
    population already has - touched rows are decided rows."""
    add(con, "partner:1", GREENHOUSE.format(n=2), source="partner")
    status.set_status(con, "partner:1", "applied")
    cfg = cfg_for(entry("partner", we_may_refetch=True))

    fetcher = Recorder()
    manual.recheck(con, cfg, fetcher=fetcher, now=NOW)

    assert fetcher.urls == []


def test_the_reading_setting_still_turns_the_whole_path_off(con):
    add(con, "partner:1", GREENHOUSE.format(n=2), source="partner")
    cfg = cfg_for(entry("partner", we_may_refetch=True), reading_on=False)

    def explode(*_args, **_kwargs):
        raise AssertionError("nothing should be requested")

    assert manual.recheck(con, cfg, fetcher=explode, now=NOW)["checked"] == 0


def test_the_count_beside_the_button_counts_the_same_population(con):
    """A number describing a wider set than the button reads is a number a
    person learns to disbelieve - and the scope is now per profile, so the two
    can drift in a way they could not when both said 'manual'."""
    add(con, "manual:1", GREENHOUSE.format(n=1), source="manual")
    add(con, "partner:1", GREENHOUSE.format(n=2), source="partner")
    cfg = cfg_for(entry("partner", we_may_refetch=True))
    sources = manual.recheckable_sources(cfg)

    state = manual.recheck_status(con, NOW, sources=sources)
    due = manual.due_rows(con, NOW, sources=sources)

    assert state["total"] == 2
    assert state["due"] == len(due) == 2


def test_the_scope_and_the_refetch_rule_are_the_same_answer(con):
    """`recheckable_sources` is what the app runs; `Refetch` is what the
    contract document describes. They read the same config, so a collector the
    document says is refetchable has to be one the button actually covers."""
    cfg = cfg_for(entry("partner", we_may_refetch=True), entry("quiet"))
    rule = collectors.Refetch.from_config(cfg)

    sources = manual.recheckable_sources(cfg)

    assert sources == ("manual", "partner")
    assert rule.may_refetch("partner") is True
    assert rule.may_refetch("quiet") is False
