"""The weekend run time, which used to be unconfigurable.

Found the hard way on 2026-08-09. A real instance had been set to 13:00 so it
would land safely behind another job that starts at 10:45 - and then Sunday came
and the app used a hardcoded 11:30, because refresh.at was only ever consulted
on weekdays. The Config screen said "Run at: 13:00, 16:30" the whole time.

A setting that displays one thing and does another, failing on exactly the two
days a week nobody is checking.
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from unlatched import refresh

# Same convention as test_refresh.py: the anchors are wall-clock times on the
# person's own day, so the tests state a zone rather than leaving it naive.
LOCAL = ZoneInfo("America/New_York")

SUNDAY = datetime(2026, 8, 9, 12, 0, tzinfo=LOCAL)
MONDAY = datetime(2026, 8, 10, 12, 0, tzinfo=LOCAL)


def test_the_weekend_time_can_be_set():
    anchors = refresh.weekend_settings({"refresh": {"weekend_at": ["14:45"]}})
    assert anchors == ((14, 45),)


def test_an_unset_weekend_time_keeps_the_documented_default():
    """An existing config must keep the behaviour it already had."""
    assert refresh.weekend_settings({}) == refresh.WEEKEND_ANCHORS
    assert refresh.weekend_settings({"refresh": {}}) == refresh.WEEKEND_ANCHORS


def test_a_typo_falls_back_to_the_weekend_default_not_the_weekday_one():
    """The bug this guards is subtle: _anchors used to fall back to
    DEFAULT_ANCHORS unconditionally, so a malformed weekend time would have
    given the weekend TWO weekday runs."""
    for bad in [[], ["nonsense"], ["25:00"], "not a list"]:
        assert refresh.weekend_settings({"refresh": {"weekend_at": bad}}) == \
            refresh.WEEKEND_ANCHORS, f"{bad!r} should fall back to the weekend default"


def test_a_sunday_uses_the_weekend_time_and_not_the_weekday_ones():
    """The exact failure. Collected at 11:00; at noon on a Sunday a 14:45
    weekend anchor must NOT be due, where the old hardcoded 11:30 would be."""
    due, why = refresh.due(
        "2026-08-09T11:00:00", SUNDAY,
        anchors=((13, 0), (16, 30)),
        weekend_anchors=((14, 45),))
    assert not due, why
    assert "14:45" in why


def test_the_same_moment_on_a_weekday_uses_the_weekday_times():
    """The weekend setting must not leak into the working week."""
    due, why = refresh.due(
        "2026-08-10T11:00:00", MONDAY,
        anchors=((13, 0), (16, 30)),
        weekend_anchors=((14, 45),))
    assert not due, why
    assert "13:00" in why


def test_past_the_weekend_time_it_is_due():
    due, why = refresh.due(
        "2026-08-09T11:00:00", datetime(2026, 8, 9, 15, 0, tzinfo=LOCAL),
        anchors=((13, 0), (16, 30)),
        weekend_anchors=((14, 45),))
    assert due, why
    assert "14:45" in why


def test_the_morning_batch_reason_is_not_given_for_an_afternoon_anchor():
    """A message that explains a decision with something untrue is worse than
    one that just states the time - the 8:00-10:30 batch has nothing to do
    with a weekend afternoon slot."""
    _due, why = refresh.due(
        "2026-08-09T11:00:00", SUNDAY,
        anchors=((13, 0), (16, 30)), weekend_anchors=((14, 45),))
    assert "8 and 10:30" not in why

    # Still given where it IS the reason.
    _due, weekday_why = refresh.due(
        "2026-08-10T08:00:00", datetime(2026, 8, 10, 9, 0, tzinfo=LOCAL),
        anchors=((10, 45), (16, 30)))
    assert "8 and 10:30" in weekday_why
