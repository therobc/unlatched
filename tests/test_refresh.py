"""When is a daily refresh worth making?

Anchored on measured posting behaviour, not a timer. Our own corpus of
8,331 dated postings: 69% Monday-Wednesday, Tuesday 27.3%, weekend 1.7%
combined. Outside analyses agree and add the hour we cannot see ourselves -
postings land roughly 8:00-10:30 a.m. See research/posting_time_of_day.md.

Decided 2026-08-05: pressing Search is deliberate; the daily refresh after that
is ON by default and switchable off.
"""
from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from unlatched import config, refresh

# "Is it a weekday, is it past 11" is a question about the person's own
# clock, so these are local-time datetimes with an explicit zone rather than
# naive ones.
LOCAL = ZoneInfo("America/New_York")

# 2026-08-04 is a Tuesday; 2026-08-08 a Saturday.
TUE_LATE = datetime(2026, 8, 4, 11, 30, tzinfo=LOCAL)
TUE_EARLY = datetime(2026, 8, 4, 9, 0, tzinfo=LOCAL)
SAT_LATE = datetime(2026, 8, 8, 11, 30, tzinfo=LOCAL)


# Derived, not typed. These assertions are about WHICH anchor the reason names,
# not about a particular hour, and hardcoding the time meant moving the morning
# slot broke four tests that were not testing the time at all (2026-08-12).
FIRST = "{:02d}:{:02d}".format(*refresh.DEFAULT_ANCHORS[0])


def test_nothing_collected_yet_is_due():
    assert refresh.due(None, TUE_LATE)[0] is True


def test_not_before_the_first_anchor():
    """A 9 a.m. check predates the batch it exists to catch."""
    due, why = refresh.due(None, TUE_EARLY)
    assert due is False
    assert FIRST in why


def test_weekends_get_one_catch_up_run():
    """Decided 2026-08-05: the weekend tail is small, not empty, and a Sunday
    posting is already two days old before a weekdays-only search sees it.
    """
    assert refresh.due(None, SAT_LATE)[0] is True
    # One slot, not two: a 09:00 Saturday check is still before it.
    sat_early = datetime(2026, 8, 8, 9, 0, tzinfo=LOCAL)
    due, why = refresh.due(None, sat_early)
    assert due is False
    assert "11:30" in why
    # And having run once, the weekend does not run again that day.
    assert refresh.due("2026-08-08T11:35:00", SAT_LATE)[0] is False


def test_weekends_can_still_be_switched_off():
    due, why = refresh.due(None, SAT_LATE, weekdays_only=True)
    assert due is False
    assert "business days" in why


def test_a_gap_of_days_does_not_wait_for_todays_anchor():
    """The backfill case: closed Friday, opened Tuesday at 09:00. The anchor
    exists so a fetch is not made before TODAY's batch lands - which is no
    reason to keep ignoring four days that have already ended.
    """
    due, why = refresh.due("2026-07-31T16:40:00", TUE_EARLY)
    assert due is True
    assert "business days of postings not seen" in why


def test_a_collection_before_an_anchor_does_not_satisfy_it():
    """Two anchors a day means the TIME matters, not just the date. A 09:00
    run predates the morning check, so that batch is still unseen.
    """
    due, why = refresh.due("2026-08-04T09:00:00", TUE_LATE)
    assert due is True
    assert FIRST in why


def test_a_collection_after_an_anchor_satisfies_it():
    due, why = refresh.due("2026-08-04T11:05:00", TUE_LATE)
    assert due is False
    assert f"already collected since {FIRST}" in why


def test_the_afternoon_anchor_is_owed_even_after_a_morning_run():
    """The reason two slots exist: roles approved during the day would
    otherwise not be seen until tomorrow.
    """
    afternoon = datetime(2026, 8, 4, 16, 35, tzinfo=LOCAL)
    due, why = refresh.due("2026-08-04T10:50:00", afternoon)
    assert due is True
    assert "16:30" in why
    assert refresh.due("2026-08-04T16:31:00", afternoon)[0] is False


def test_anchor_times_are_configurable_and_a_typo_falls_back():
    assert refresh.settings({"refresh": {"at": ["09:00", "13:15"]}})[1] == ((9, 0), (13, 15))
    assert refresh.settings({"refresh": {"at": ["nine am"]}})[1] == refresh.DEFAULT_ANCHORS


def test_yesterday_is_due():
    assert refresh.due("2026-08-03T09:00:00", TUE_LATE)[0] is True


def test_a_gap_is_counted_in_business_days_not_calendar_days():
    """A Monday is one posting day behind a Friday, not three - counting
    calendar days would overstate what was missed every single weekend.
    """
    mon = datetime(2026, 8, 10, 11, 30, tzinfo=LOCAL)
    due, why = refresh.due("2026-08-07T09:00:00", mon)
    assert due is True
    assert "3 days ago" in why
    assert "business days of postings" not in why, "one posting day is not a backlog"

    thu = datetime(2026, 8, 13, 11, 30, tzinfo=LOCAL)
    due, why = refresh.due("2026-08-06T09:00:00", thu)
    assert due is True
    assert "5 business days" in why


def test_a_utc_stamp_is_converted_to_local_not_truncated():
    """jobs.fetched_at is written in UTC with an offset; the anchors are
    wall-clock times on the person's own day. Truncating the offset made a
    12:50 collection in Tennessee read as 16:50 and satisfy the 16:30 slot
    that had not happened yet - four hours of postings missed, silently.
    """
    def as_utc_stamp(hour: int, minute: int) -> str:
        """That wall-clock moment on THIS machine, written the way
        jobs.fetched_at writes it. Derived rather than hard-coded, so the
        test asserts the conversion rather than the tester's own zone."""
        local = datetime(2026, 8, 4, hour, minute).astimezone()
        return local.astimezone(UTC).isoformat()

    # 17:00 on this machine's own clock, whatever zone that is.
    afternoon = datetime(2026, 8, 4, 17, 0).astimezone()
    due, why = refresh.due(as_utc_stamp(12, 50), afternoon)
    assert due is True
    assert "16:30" in why
    # And a collection that really was after the anchor still satisfies it.
    assert refresh.due(as_utc_stamp(16, 45), afternoon)[0] is False


def test_an_unreadable_timestamp_errs_toward_refreshing():
    """Better a redundant fetch than silently never refreshing again."""
    assert refresh.due("not-a-timestamp", TUE_LATE)[0] is True


def test_daily_refresh_is_on_by_default():
    enabled, anchors, weekdays = refresh.settings(config.defaults())
    assert enabled is True
    # Also catches the two copies drifting apart: config.defaults() writes its
    # own "at" list, refresh.DEFAULT_ANCHORS is the fallback when there is none,
    # and changing one without the other left them disagreeing (2026-08-12).
    assert anchors == refresh.DEFAULT_ANCHORS
    assert weekdays is False, "the weekend catch-up run is on unless switched off"


def test_a_config_written_before_the_weekend_run_existed_gains_it():
    assert refresh.settings({"refresh": {"daily": True}})[2] is False
    assert refresh.settings({"refresh": {"weekdays_only": True}})[2] is True


def test_an_older_config_without_the_block_still_refreshes():
    assert refresh.settings({})[0] is True


def test_it_stays_switchable_off():
    assert refresh.settings({"refresh": {"daily": False}})[0] is False


def test_an_out_of_range_time_falls_back_to_the_documented_default():
    assert refresh.settings({"refresh": {"at": ["99:99"]}})[1] == refresh.DEFAULT_ANCHORS
