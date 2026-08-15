"""Sleeping until the next anchor, instead of polling to learn nothing.

The desktop woke on a fixed timer and asked due() the same question each time.
Between anchors the answer is always no, and exactly when it stops being no is
knowable in advance - so the app can sleep to that moment and be both prompt
and cheap, where a timer has to trade one against the other.

These are all against a FIXED `now`, never datetime.now(): a scheduling test
that reads the real clock passes or fails depending on the hour it is run.
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from unlatched import refresh

# Zone-aware, matching the rest of the suite: the anchors are wall-clock times
# on the person's own day, so a test that ran in another timezone and still
# passed would be proving the wrong thing.
LOCAL = ZoneInfo("America/New_York")


def at(year, month, day, hour, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=LOCAL)


# A Wednesday, so the weekday anchors apply and the next day is also a weekday.
WEDNESDAY = at(2026, 8, 12, 9)
FRIDAY = at(2026, 8, 14, 20)
SATURDAY = at(2026, 8, 15, 9)

ANCHORS = ((11, 0), (16, 30))


def test_before_the_first_anchor_it_waits_for_today():
    assert refresh.next_anchor(WEDNESDAY, anchors=ANCHORS) == at(2026, 8, 12, 11)


def test_between_anchors_it_waits_for_the_later_one():
    assert refresh.next_anchor(at(2026, 8, 12, 12), anchors=ANCHORS) == at(
        2026, 8, 12, 16, 30)


def test_after_the_last_anchor_it_rolls_to_the_next_day():
    evening = at(2026, 8, 12, 23, 59)
    assert refresh.next_anchor(evening, anchors=ANCHORS) == at(2026, 8, 13, 11)


def test_an_anchor_exactly_now_returns_the_next_one_not_this_one():
    """A caller handed "sleep zero seconds" would wake, find nothing due, and
    ask again immediately - a spin, at the one moment of the day the app is
    most likely to be busy actually collecting."""
    assert refresh.next_anchor(at(2026, 8, 12, 11), anchors=ANCHORS) == at(
        2026, 8, 12, 16, 30)


def test_friday_night_lands_on_the_weekend_slot_by_default():
    """The weekend catch-up run exists to pick up postings that go live on a
    Sunday evening. Skipping to Monday would let them sit two days unseen,
    which is the thing that slot was added for."""
    assert refresh.next_anchor(FRIDAY, anchors=ANCHORS) == at(2026, 8, 15, 11, 30)


def test_weekdays_only_skips_the_whole_weekend():
    assert refresh.next_anchor(FRIDAY, anchors=ANCHORS, weekdays_only=True) == at(
        2026, 8, 17, 11)


def test_weekdays_only_from_inside_the_weekend_still_finds_monday():
    """The awkward case: it is already Saturday and this day type is disabled,
    so the answer is not on the day being asked about at all."""
    assert refresh.next_anchor(SATURDAY, anchors=ANCHORS, weekdays_only=True) == at(
        2026, 8, 17, 11)


def test_a_custom_anchor_order_is_sorted_rather_than_trusted():
    """Config is a list a person edits. Handed them out of order, an
    unsorted walk would return the first FUTURE entry it happened to see
    rather than the soonest one, and the app would sleep past a run."""
    out_of_order = ((16, 30), (11, 0))
    assert refresh.next_anchor(WEDNESDAY, anchors=out_of_order) == at(2026, 8, 12, 11)


def test_the_sleep_is_never_zero_or_negative():
    exactly = at(2026, 8, 12, 11)
    assert refresh.seconds_until_next_anchor(exactly, anchors=ANCHORS) > 0

    before = at(2026, 8, 12, 10, 59).replace(second=30)
    assert refresh.seconds_until_next_anchor(before, anchors=ANCHORS) == pytest.approx(30.0)


def test_the_wake_time_agrees_with_what_due_says_at_that_moment():
    """THE PROPERTY THAT MATTERS, and the one a wrong answer here would break
    quietly: waking at the returned moment must actually find a refresh owed.

    Asserted against due() rather than against a hand-written expectation, so
    the two cannot drift apart - they are the same schedule read from two
    directions.
    """
    collected_this_morning = "2026-08-12T11:05:00"
    wake = refresh.next_anchor(at(2026, 8, 12, 12), anchors=ANCHORS)
    owed, why = refresh.due(collected_this_morning, wake, anchors=ANCHORS)
    assert owed, f"woke at {wake} and nothing was due: {why}"


def test_an_empty_anchor_tuple_raises_rather_than_sleeping_forever():
    """A None or a silently huge sleep here reads as the app having quietly
    stopped, which is the hardest failure of all to notice."""
    with pytest.raises(ValueError, match="no anchor"):
        refresh.next_anchor(WEDNESDAY, anchors=(), weekend_anchors=())
