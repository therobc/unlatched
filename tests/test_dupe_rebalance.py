"""A group is decided once. The world does not stop afterwards.

LinkedIn is kept when the same job is on both sides, because applying there
records the application in LinkedIn's own tracker. But LinkedIn ads come down
well before the ATS requisition does - so the kept row closes, and the route the
person could still apply through is folded away underneath a dead posting.

find() cannot see this: it only considers rows with duplicate_of IS NULL. These
tests cover the pass that revisits groups already made.
"""
from __future__ import annotations

from unlatched import db, dupes, status

ATS = "https://job-boards.greenhouse.io/acme/jobs/1"


def pair(con, *, keeper_gone=None, hidden_gone=None):
    """One LinkedIn row and one board row, grouped WHILE BOTH ARE OPEN, and
    only then closed.

    The order is the point, and an earlier version of this fixture had it wrong.
    It created the pair with the closure already applied, and find() then quite
    correctly kept the OPEN row - so the fixture could not build the situation
    it existed to test. In life the group is made first and the collector author's collector
    pushes the closure the following day, which is what this now reproduces.
    """
    cid = db.upsert_company(con, "Acme")
    db.upsert_job(con, "greenhouse:1", {
        "company_id": cid, "title": "Analyst", "source": "greenhouse",
        "url": ATS, "fetched_at": "2026-08-01", "qualified": 1, "verdict": "keep",
    })
    db.upsert_job(con, "manual:li-1", {
        "company_id": cid, "title": "Analyst", "source": "imported",
        "url": "https://www.linkedin.com/jobs/view/1",
        "apply_url": dupes.links_mod.normalise_apply_url(ATS),
        "fetched_at": "2026-08-02", "qualified": 1, "verdict": "keep",
    })
    found = dupes.find(con)
    assert len(found) == 1
    assert found[0].duplicate_of == "manual:li-1", "LinkedIn should be kept"
    dupes.apply(con, found)

    for key, gone in (("manual:li-1", keeper_gone), ("greenhouse:1", hidden_gone)):
        if gone:
            con.execute("UPDATE jobs SET delisted_at = ? WHERE key = ?", (gone, key))
    con.commit()


def keeper_of(con, key):
    return db.get_job(con, key)["duplicate_of"]


def test_a_closed_keeper_hands_over_to_the_posting_still_open(con):
    """The whole point. The first user works this board by applying to things, so a group
    that shows him a closed ad and hides the live requisition costs an
    application."""
    pair(con, keeper_gone="2026-08-09T10:00:00")

    swapped = dupes.rebalance(con)

    assert swapped == [("greenhouse:1", "manual:li-1")]
    assert keeper_of(con, "greenhouse:1") is None, "the open row must be visible"
    assert keeper_of(con, "manual:li-1") == "greenhouse:1"


def test_the_reason_says_why_the_preferred_route_was_not_used(con):
    """Somebody looking at a board row where they expected the LinkedIn one
    deserves to find the answer on the row, not in a changelog."""
    pair(con, keeper_gone="2026-08-09T10:00:00")
    dupes.rebalance(con)

    assert "closed" in db.get_job(con, "manual:li-1")["duplicate_reason"]


def test_both_open_is_left_exactly_alone(con):
    """The ordinary case, and the one that must not churn: LinkedIn stays the
    preferred route for as long as it is actually available."""
    pair(con)

    assert dupes.rebalance(con) == []
    assert keeper_of(con, "greenhouse:1") == "manual:li-1"


def test_both_closed_is_left_alone(con):
    """Nothing to prefer. Swapping would move the group for no gain and lose
    the audit-trail row from the visible position."""
    pair(con, keeper_gone="2026-08-09T10:00:00", hidden_gone="2026-08-09T11:00:00")

    assert dupes.rebalance(con) == []
    assert keeper_of(con, "greenhouse:1") == "manual:li-1"


def test_an_application_on_the_closed_row_outranks_liveness(con):
    """The one case where showing the closed posting is right.

    If they applied through the LinkedIn ad, that record is what they need to
    see. Surfacing the open ATS route instead would hide the fact that they
    already went for this job - which is how somebody applies twice to one
    employer, and the exact failure the keeper rule exists to prevent.
    """
    pair(con, keeper_gone="2026-08-09T10:00:00")
    status.set_status(con, "manual:li-1", "applied")

    assert dupes.rebalance(con) == []
    assert keeper_of(con, "greenhouse:1") == "manual:li-1"


def test_a_retired_row_is_not_promoted_into_view(con):
    """Somebody threw that one away. An expiring keeper is not a reason to
    bring it back."""
    pair(con, keeper_gone="2026-08-09T10:00:00")
    con.execute("UPDATE jobs SET retired_at = ? WHERE key = ?",
                ("2026-08-05T09:00:00", "greenhouse:1"))
    con.commit()

    assert dupes.rebalance(con) == []


def test_a_new_group_is_never_created_facing_the_wrong_way(con):
    """Liveness lives in _primary, so find() obeys it too.

    Before it did, a pair first seen with the LinkedIn ad ALREADY closed would
    be grouped LinkedIn-first and stay wrong until the next day's rebalance -
    because rebalance runs BEFORE find on any given run. One rule, applied at
    both moments, removes that window instead of narrowing it.
    """
    cid = db.upsert_company(con, "Acme")
    db.upsert_job(con, "greenhouse:1", {
        "company_id": cid, "title": "Analyst", "source": "greenhouse",
        "url": ATS, "fetched_at": "2026-08-01", "qualified": 1, "verdict": "keep",
    })
    db.upsert_job(con, "manual:li-1", {
        "company_id": cid, "title": "Analyst", "source": "imported",
        "url": "https://www.linkedin.com/jobs/view/1",
        "apply_url": dupes.links_mod.normalise_apply_url(ATS),
        "fetched_at": "2026-08-02", "qualified": 1, "verdict": "keep",
        "delisted_at": "2026-08-09T10:00:00",
    })

    found = dupes.find(con)
    assert len(found) == 1
    assert found[0].duplicate_of == "greenhouse:1", \
        "the open route should be kept when the LinkedIn ad is already closed"


def test_reopening_a_wrongly_closed_posting_restores_the_preference(con):
    """The case that made the one-directional version wrong.

    The collector author deliberately does not act on inconclusive closure signals, but a
    false positive is still possible and `delist --back` is how she reverses
    one. The first version of rebalance only ever tested for "keeper closed,
    hidden open", so once a group flipped it stayed flipped forever - the free
    LinkedIn audit trail silently lost, with nothing to indicate why.
    """
    pair(con, keeper_gone="2026-08-09T10:00:00")
    assert len(dupes.rebalance(con)) == 1
    assert keeper_of(con, "manual:li-1") == "greenhouse:1"

    # She reopens it.
    con.execute("UPDATE jobs SET delisted_at = NULL WHERE key = ?", ("manual:li-1",))
    con.commit()

    assert dupes.rebalance(con) == [("manual:li-1", "greenhouse:1")]
    assert keeper_of(con, "manual:li-1") is None, "LinkedIn should be preferred again"
    assert keeper_of(con, "greenhouse:1") == "manual:li-1"


def test_an_application_made_while_the_ad_was_down_is_not_undone_by_it_reopening(con):
    """History outranks liveness in BOTH directions.

    If the ad closed, we surfaced the ATS route, they applied through it, and
    the ad then came back - the row carrying the application stays visible.
    Restoring the LinkedIn preference here would hide the record of what they
    actually did.
    """
    pair(con, keeper_gone="2026-08-09T10:00:00")
    dupes.rebalance(con)
    status.set_status(con, "greenhouse:1", "applied")
    con.execute("UPDATE jobs SET delisted_at = NULL WHERE key = ?", ("manual:li-1",))
    con.commit()

    assert dupes.rebalance(con) == []
    assert keeper_of(con, "greenhouse:1") is None
    assert keeper_of(con, "manual:li-1") == "greenhouse:1"


def test_running_it_twice_changes_nothing_the_second_time(con):
    """Once swapped, the visible row is the open one, so there is no longer a
    closed keeper to act on. A pass that kept flipping would rewrite the board
    every single afternoon."""
    pair(con, keeper_gone="2026-08-09T10:00:00")

    assert len(dupes.rebalance(con)) == 1
    assert dupes.rebalance(con) == []
