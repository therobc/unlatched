"""refresh.py - Is today a day worth collecting, and what is new since last time?

Anchored on measured behaviour rather than a timer. Postings arrive in daily
weekday batches: across 8,331 dated postings in our own corpus, 69% landed
Monday-Wednesday, Tuesday alone took 27.3%, and the weekend accounted for
1.7% combined. Published analyses agree on the shape and add the hour we
cannot see ourselves - roughly 8:00-10:30 a.m., because postings are staged
in the ATS and released ahead of the recruiters' late-morning review block.

So: fixed anchor times, and never before the morning batch has landed. Two
slots on weekdays - one after the morning release, one in the afternoon for
roles approved during the day that would otherwise wait until tomorrow - and
ONE at weekends, which is enough to pick up the small weekend tail without
polling for it. An hourly poll would spend network on a corpus that changes
in daily batches, and a 6 a.m. run would miss the very batch it exists to
catch.

A day that has already ended is not waited on: if the last collection was on
an earlier date, a refresh is due the moment the app is opened, whatever the
time. That is what makes the app usable after a weekend away.

Nothing here fetches. It answers WHETHER a fetch is worth making, and the
caller acts.

The split, set 2026-08-05: creating or changing a search and pressing
Search is deliberate - the app never collects because a setting was edited.
Once a search exists, the daily refresh is on by default and switchable off,
because the person has already said they want these jobs and a stale list is
what costs them interviews.
"""
from __future__ import annotations

from datetime import date, datetime, time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import sqlite3

# Times of day a refresh is worth making, as (hour, minute). The morning slot
# catches the main batch once it has landed. The afternoon slot exists
# because publishing is not a single event: roles approved during the day go
# live through the afternoon, and one source notes 46% of postings are
# approved Friday afternoon and HELD for release - so an afternoon check
# catches same-day arrivals a person would otherwise not see until tomorrow.
#
# Two, not more. Boards change in daily batches, so a third run mostly
# re-fetches what the first two already have.
#
# 11:00 rather than the 10:45 this shipped with (decided 2026-08-12). A round hour
# is what a person reads off the Config screen and remembers, and nothing in the
# posting data argued for the quarter-hour.
#
# LOCAL WALL CLOCK, NOT A TIMEZONE. due() compares this against datetime.now(),
# so a user in Denver gets 11:00 their time. That is the intent: the question is
# "has today's batch landed where this person is", not "is it 11:00 in New
# York". Anyone quoting these as EST is describing this device, not the default.
DEFAULT_ANCHORS = ((11, 0), (16, 30))

# Monday is 0.
WEEKDAYS = (0, 1, 2, 3, 4)

# Saturday and Sunday get ONE slot rather than none (decided 2026-08-05).
#
# The 1.7%-of-postings weekend figure argues against POLLING at weekends. It
# does not argue against a single catch-up run, and 1.7% of a real corpus is
# not nothing: it is the postings that go live on a Sunday evening and are
# already two days old by the time a weekdays-only search sees them. One run
# a day costs one request per board and closes that.
#
# Later in the day than the weekday morning slot, because the weekend arrivals
# it exists to catch are not staged to a business-hours release schedule.
WEEKEND_ANCHORS = ((11, 30),)


def last_collected(con: sqlite3.Connection) -> str | None:
    """When collection last wrote anything, as its stored ISO timestamp.

    Read from jobs.fetched_at rather than a separate marker: the marker
    could say a run happened when it collected nothing, and what the user
    actually wants to know is when jobs last arrived.
    """
    row = con.execute("SELECT MAX(fetched_at) FROM jobs").fetchone()
    return str(row[0]) if row and row[0] else None


def last_completed(con: sqlite3.Connection) -> str | None:
    """When a collect last RAN TO COMPLETION, for deciding whether one is owed.

    SEPARATE FROM last_collected, WHICH ANSWERS A DIFFERENT QUESTION.
    last_collected reads MAX(fetched_at) and is right for "when did jobs last
    arrive" - that is what the UI shows a person. It is WRONG for "did the last
    run finish", because fetched_at advances DURING a run: a collect that died
    10% in left a recent timestamp, due() called the anchor satisfied, and the
    other 90% of boards went uncollected until the next anchor.

    FALLS BACK TO last_collected when the marker is absent. Without that, every
    database that predates the marker would read as never-collected and fire a
    full backfill on first open - punishing existing installs for a change they
    had no part in.
    """
    from . import db as db_mod
    stamp = db_mod.get_meta(con, "collect_completed_at")
    return stamp if stamp else last_collected(con)


def new_since(con: sqlite3.Connection, cutoff: str) -> int:
    """How many postings arrived after `cutoff`. Drives the "14 new" badge."""
    row = con.execute(
        "SELECT COUNT(*) FROM jobs WHERE fetched_at > ?", (cutoff,)).fetchone()
    return int(row[0]) if row else 0


def _passed_anchor(now: datetime, anchors: tuple[tuple[int, int], ...]) -> time | None:
    """The most recent anchor time already passed today, if any."""
    passed = [time(h, m) for h, m in anchors if now.time() >= time(h, m)]
    return max(passed) if passed else None


def due(last: str | None, now: datetime, *,
        anchors: tuple[tuple[int, int], ...] = DEFAULT_ANCHORS,
        weekdays_only: bool = False,
        weekend_anchors: tuple[tuple[int, int], ...] = WEEKEND_ANCHORS) -> tuple[bool, str]:
    """Should a refresh be made right now? Returns (due, reason).

    Due when an anchor time has passed today and nothing has been collected
    SINCE that anchor. That is what makes two runs a day work without
    becoming a poll: the 16:30 slot fires only if the last collection
    predates it, so a manual run at 15:00 satisfies the morning slot and
    still leaves the afternoon one to catch what posts after it.

    BACKFILL (decided 2026-08-05): a gap of a whole day or more is due AT ONCE,
    without waiting for today's anchor. Somebody who closes the app on Friday
    and opens it on Tuesday morning was previously told "before 10:45" and
    collected nothing, with four days of postings unseen - the anchor exists
    to avoid fetching before today's batch has landed, which is no reason to
    keep ignoring days that have already ended.

    The reason is returned either way so the UI can say WHY it is not
    refreshing rather than silently doing nothing.
    """
    weekend = now.weekday() not in WEEKDAYS
    if weekend and weekdays_only:
        return False, "weekend - employers post on business days"
    today_anchors = weekend_anchors if weekend else anchors

    last_dt = _moment_of(last) if last is not None else None
    if last is not None and last_dt is None:
        return True, "last collection time is unreadable"

    if last_dt is not None and last_dt.date() < now.date():
        gap = (now.date() - last_dt.date()).days
        business = _business_days_between(last_dt.date(), now.date())
        if business <= 1:
            return True, f"last collected {gap} day{'s' if gap != 1 else ''} ago"
        return True, (f"last collected {gap} days ago - {business} business days "
                       "of postings not seen")

    anchor = _passed_anchor(now, today_anchors)
    if anchor is None:
        first = min(time(h, m) for h, m in today_anchors)
        # The morning-batch explanation belongs to the WEEKDAY anchor it was
        # chosen for. On a weekend, or against a time somebody has set for
        # their own reasons, it is simply not the reason - and a message that
        # explains a decision with something untrue is worse than one that
        # just states the time.
        because = ("" if weekend or first > time(12, 0)
                   else " - most postings go live between 8 and 10:30 a.m.")
        return False, f"before {first.strftime('%H:%M')}{because}"
    # Nothing ever collected still waits for the anchor: the FIRST collection
    # comes from pressing Search, which is deliberate, so there is no backlog
    # here to be late for.
    if last_dt is None:
        return True, "nothing collected yet"
    if last_dt.time() >= anchor:
        return False, f"already collected since {anchor.strftime('%H:%M')}"
    return True, f"collected earlier today, before the {anchor.strftime('%H:%M')} check"


def next_anchor(now: datetime, *,
                anchors: tuple[tuple[int, int], ...] = DEFAULT_ANCHORS,
                weekdays_only: bool = False,
                weekend_anchors: tuple[tuple[int, int], ...] = WEEKEND_ANCHORS,
                ) -> datetime:
    """The next moment a refresh could become due.

    SO THE APP CAN SLEEP UNTIL SOMETHING HAPPENS instead of polling to find out
    that nothing has. The desktop woke on a fixed timer and asked due() the
    same question every time; between anchors the answer is always no, and it
    is knowable in advance exactly when it stops being no.

    That is not only a tidiness argument. A fixed poll has to pick between
    waking often enough to be prompt and rarely enough to be cheap, and it gets
    both wrong: it can fire minutes after an anchor passes, and it spends every
    other wake-up learning nothing. Sleeping to the anchor is both immediate
    and free.

    ALWAYS STRICTLY IN THE FUTURE. An anchor exactly now returns the NEXT one,
    because a caller that slept zero seconds and asked again would spin.

    Answers only "when could one be due" - whether one IS due is due()'s
    question, and the caller still has to ask it on waking. A run that finishes
    after the anchor satisfies it, so waking is not the same as collecting.
    """
    day = now.date()
    # A fortnight is far more than enough - the only way to skip days is
    # weekdays_only, which can never skip more than two in a row - and it
    # terminates rather than looping forever on a config that somehow has no
    # usable anchor at all.
    for offset in range(15):
        when = date.fromordinal(day.toordinal() + offset)
        weekend = when.weekday() not in WEEKDAYS
        if weekend and weekdays_only:
            continue
        for hour, minute in sorted(weekend_anchors if weekend else anchors):
            # CARRIES `now`'s TZINFO, whatever it is. Callers pass both shapes:
            # the desktop hands us a naive local clock, the test suite uses
            # zone-aware datetimes so a run in another timezone still means
            # what it says. datetime.combine produces a NAIVE moment, and
            # comparing naive against aware raises TypeError rather than
            # returning a wrong answer - so this would have failed loudly the
            # first time it met the aware caller.
            moment = datetime.combine(when, time(hour, minute), tzinfo=now.tzinfo)
            if moment > now:
                return moment
    # Unreachable with any anchor tuple this module can produce - _anchors
    # falls back to a non-empty default - but a silent None here would become
    # a sleep of "forever" in the caller, which is the failure that looks like
    # the app having quietly stopped.
    msg = "no anchor found within a fortnight"
    raise ValueError(msg)


def seconds_until_next_anchor(now: datetime, **kwargs: Any) -> float:
    """How long to sleep, as seconds. Never negative, never zero."""
    return max(1.0, (next_anchor(now, **kwargs) - now).total_seconds())


def _moment_of(stamp: str) -> datetime | None:
    """Full timestamp, not just the day - two anchors a day means the TIME of
    the last collection decides whether the next slot is owed.
    """
    text = (stamp or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            return datetime.combine(date.fromisoformat(text[:10]), time(0, 0))
        except ValueError:
            return None
    # CONVERTED to local, not truncated. jobs.fetched_at is written in UTC
    # with an offset, while the anchors are wall-clock times on this
    # person's own day - so dropping the offset made a collection that ran
    # at 12:50 in a UTC-4 zone read as 16:50, which then satisfied the 16:30
    # afternoon slot that had not yet happened. Four hours of the day's
    # postings, missed, every day, and silently.
    return parsed.astimezone().replace(tzinfo=None) if parsed.tzinfo else parsed


def _business_days_between(start: date, end: date) -> int:
    """Weekdays strictly after `start` up to and including `end`. Counting
    calendar days would call a Monday "3 days" behind a Friday when only one
    posting day was missed.
    """
    days = 0
    step = start
    while step < end:
        step = date.fromordinal(step.toordinal() + 1)
        if step.weekday() in WEEKDAYS:
            days += 1
    return days


def weekend_settings(cfg: dict[str, Any]) -> tuple[tuple[int, int], ...]:
    """The weekend anchor time(s) from config.

    Configurable because it was NOT, and that made the Config screen lie: it
    showed "Run at: 13:00, 16:30" while Saturday and Sunday quietly used a
    hardcoded 11:30. On an instance sequenced behind another job that is the
    difference between running after it and colliding with it, and it fails on
    exactly the two days a week nobody is checking (decided 2026-08-09).

    Defaults to the documented 11:30, so an existing config keeps the behaviour
    it already had.
    """
    block = cfg.get("refresh") or {}
    raw = block.get("weekend_at")
    if raw is None:
        return WEEKEND_ANCHORS
    return _anchors(raw, default=WEEKEND_ANCHORS)


def settings(cfg: dict[str, Any]) -> tuple[bool, tuple[tuple[int, int], ...], bool]:
    """(enabled, anchor times, weekdays_only) from config, with the defaults
    this module documents.

    Daily refresh defaults ON. Creating or changing a search and pressing
    Search is the deliberate act; from then on the person has already said
    they want these jobs, and a stale list is the failure that costs them
    interviews. A MISSING key therefore reads as enabled, so an older
    config.json written before this block existed still refreshes.
    """
    block = cfg.get("refresh") or {}
    daily = block.get("daily")
    enabled = True if daily is None else bool(daily)
    # Weekdays-only defaults OFF, so the weekend catch-up run happens unless
    # somebody deliberately turns it off. A config written before the weekend
    # slot existed therefore GAINS it, which is the behaviour it would have
    # been given had it been written today.
    weekdays = block.get("weekdays_only")
    return enabled, _anchors(block.get("at")), False if weekdays is None else bool(weekdays)


def _anchors(raw: Any, default: tuple[tuple[int, int], ...] = DEFAULT_ANCHORS
             ) -> tuple[tuple[int, int], ...]:
    """["10:45", "16:30"] -> ((10, 45), (16, 30)).

    An unparseable or empty list falls back to the documented default rather
    than leaving a search that never refreshes - a typo in a time should not
    silently disable the feature.
    """
    if not isinstance(raw, list) or not raw:
        return default
    out: list[tuple[int, int]] = []
    for item in raw:
        parts = str(item).split(":")
        if len(parts) != 2:
            continue
        try:
            hour, minute = int(parts[0]), int(parts[1])
        except ValueError:
            continue
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            out.append((hour, minute))
    return tuple(sorted(set(out))) or default
