"""collectors.py - the configured list of programs that hand us rows.

Unlatched does not crawl Indeed, LinkedIn, Glassdoor or Jobs4TN, and should
not. This module is what makes that a POSITION rather than a limitation: the
app's own collectors read boards that publish access on purpose, and everything
else arrives as a file somebody else's program wrote.

WE PULL. THEY NEVER PUSH (Decided 2026-08-12: "I would like for our app to import
external apps and not have that app push"). One program writes to this database
and it is this one. A collector that exports a file needs no access to the
person's job data at all, and disabling it is deleting a line of config rather
than stopping somebody else's program.

EACH COLLECTOR IS A NAMESPACE. Its id becomes jobs.source AND the prefix on
every key it writes, so two collectors reporting the same posting stay two rows
with their own provenance instead of one silently overwriting the other. See
importer._key_for.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from . import importer, refresh

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

# What a collector entry may say, and what it means if it says nothing.
#
# EVERY FIELD EXCEPT id AND path HAS A DEFAULT, so the smallest usable entry is
# two lines. A contract that demands seven fields is one nobody implements.
DEFAULTS: dict[str, Any] = {
    "label": "",
    "enabled": True,
    # WHEN TO LOOK, not when the sender runs. Empty means "every time the app
    # refreshes", which is what the single pre-list handoff did and what the
    # live profile depends on - a default that quietly stopped a daily pull
    # would not be visible until jobs went missing.
    #
    # Times narrow that: ["13:00"] looks once, after 13:00, on days the app is
    # running. Looking is a stat() and an unchanged file is refused by
    # fingerprint, so this exists to give a person control over WHEN a
    # collector's rows appear, not to save work.
    "schedule": [],
    # DEFAULTS TO FALSE, and this is the safe direction. A collector saying
    # "you may re-fetch my rows" is asking this app to make requests to a site
    # somebody else already read - and for the sites this feature exists for,
    # that is the thing we promised not to do.
    "we_may_refetch": False,
    # Whether the collector reports its own closures. False by default because
    # assuming a collector sends closures it does not send would leave dead
    # postings on the board looking live.
    "pushes_closures": False,
}


class BadCollectorError(ValueError):
    """A collector entry that cannot be used, named so the caller can say which."""


@dataclass(frozen=True)
class Collector:
    """One configured source of handed-over rows."""

    id: str
    path: str
    label: str = ""
    enabled: bool = True
    schedule: tuple[str, ...] = ()
    we_may_refetch: bool = False
    pushes_closures: bool = False
    # True for the single unnamed handoff that predates this list. It keeps the
    # old key behaviour and the old marker, so upgrading does not re-import a
    # live board's file - see `migrated_from_ingest_path`.
    legacy: bool = False

    @property
    def name(self) -> str:
        """What to call it on screen."""
        return self.label or self.id

    @property
    def marker(self) -> str:
        """The meta key holding the fingerprint of the file last taken in.

        THE LEGACY ONE KEEPS THE OLD KEY. A real profile is live and
        pulls daily; a new marker would read as "never taken in" and re-import
        the current file, and re-importing is not free - relist() clears
        delisted_at, so a stale file resurrects rows that closed since it was
        written.
        """
        return "ingest_last" if self.legacy else f"ingest_last:{self.id}"

    @property
    def seen_marker(self) -> str:
        """The meta key holding when this collector was last LOOKED AT.

        SEPARATE FROM `marker`, WHICH ANSWERS A DIFFERENT QUESTION. `marker`
        advances only when a file with new content is taken in, so a collector
        whose sender has not run in a week still reads as "never taken" - and a
        schedule keyed on that would consider it due at every single refresh,
        which is not a schedule. This one advances on every look.
        """
        return f"ingest_seen:{self.id}"

    @property
    def anchors(self) -> tuple[tuple[int, int], ...]:
        """The schedule as (hour, minute) pairs, for refresh.due.

        Empty when nothing was configured, which `scheduled_now` reads as
        "every refresh" rather than "never".
        """
        return parse_times(self.schedule)[0]


_TIME = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


def parse_times(schedule: Sequence[str]) -> tuple[tuple[tuple[int, int], ...],
                                                  list[str]]:
    """Turn ["13:00"] into ((13, 0),), and name anything unreadable.

    UNREADABLE TIMES ARE RETURNED, NOT DROPPED. A typo in a schedule is
    invisible: the collector goes on working, it simply stops arriving when the
    person expected, and there is nothing to notice. `configured` turns these
    into a problem line, so the entry is refused with the offending text quoted.
    """
    times: list[tuple[int, int]] = []
    bad: list[str] = []
    for item in schedule:
        match = _TIME.match(str(item).strip())
        if match is None:
            bad.append(str(item))
            continue
        times.append((int(match.group(1)), int(match.group(2))))
    return tuple(sorted(set(times))), bad


def scheduled_now(collector: Collector, last_seen: str | None,
                  now: datetime) -> bool:
    """Should this collector be looked at during a refresh happening now?

    NO SCHEDULE MEANS EVERY REFRESH. See DEFAULTS["schedule"] for why that is
    the default rather than "on demand only".

    With times set, this is the same anchor rule the app's own refresh uses -
    `refresh.due` - rather than a second scheduler with its own edge cases. The
    weekday/weekend split is deliberately NOT applied: a person who wrote 13:00
    meant 13:00, and the weekend anchors exist for job boards' posting habits,
    which have nothing to do with when somebody else's program writes a file.
    """
    if not collector.enabled:
        return False
    anchors = collector.anchors
    if not anchors:
        return True
    return refresh.due(last_seen, now, anchors=anchors,
                       weekend_anchors=anchors)[0]


def _entry(raw: dict[str, Any], *, index: int) -> Collector:
    if not isinstance(raw, dict):
        msg = f"collector {index}: each entry has to be an object"
        raise BadCollectorError(msg)
    try:
        ident = importer.check_collector_id(str(raw.get("id") or ""))
    except importer.BadCollectorIdError as e:
        msg = f"collector {index}: {e}"
        raise BadCollectorError(msg) from e
    path = str(raw.get("path") or "").strip()
    if not path:
        msg = f"collector {ident!r}: needs a path to the file it writes"
        raise BadCollectorError(msg)
    schedule = raw.get("schedule") or DEFAULTS["schedule"]
    if isinstance(schedule, str):
        schedule = [schedule]
    _, unreadable = parse_times([str(s) for s in schedule])
    if unreadable:
        quoted = ", ".join(repr(s) for s in unreadable)
        msg = (f'collector {ident!r}: schedule needs times like "13:00" - '
               f"could not read {quoted}")
        raise BadCollectorError(msg)
    return Collector(
        id=ident,
        path=path,
        label=str(raw.get("label") or DEFAULTS["label"]),
        enabled=bool(raw.get("enabled", DEFAULTS["enabled"])),
        schedule=tuple(str(s) for s in schedule),
        we_may_refetch=bool(raw.get("we_may_refetch", DEFAULTS["we_may_refetch"])),
        pushes_closures=bool(raw.get("pushes_closures", DEFAULTS["pushes_closures"])),
    )


def migrated_from_ingest_path(cfg: dict[str, Any]) -> list[Collector]:
    """The single pre-list handoff, as a collector.

    MIGRATION, NOT REPLACEMENT. A real profile is live and pulling
    daily; an upgrade that silently stopped his handoff would not be visible
    until jobs went missing. So a config with the old `ingest.path` and no
    `collectors` list keeps working exactly as it did, under the id the rows it
    already wrote carry.
    """
    path = str((cfg.get("ingest") or {}).get("path") or "").strip()
    if not path:
        return []
    return [Collector(id=importer.SOURCE_NAME, path=path,
                      label="Handoff file", legacy=True,
                      pushes_closures=True)]


def configured(cfg: dict[str, Any]) -> tuple[list[Collector], list[str]]:
    """Every configured collector, and a line about each entry that was unusable.

    PROBLEMS ARE REPORTED, NOT RAISED. One bad entry in a list of four should
    not stop the other three from being read - the same reasoning as a
    malformed row inside a handoff.
    """
    raw = cfg.get("collectors")
    if not isinstance(raw, list) or not raw:
        return migrated_from_ingest_path(cfg), []

    found: list[Collector] = []
    problems: list[str] = []
    seen: set[str] = set()
    for index, item in enumerate(raw):
        try:
            entry = _entry(item, index=index)
        except BadCollectorError as e:
            problems.append(str(e))
            continue
        if entry.id in seen:
            # TWO ENTRIES CANNOT SHARE A NAMESPACE. Allowing it would put two
            # different files' rows in one id, which is the collision the
            # namespace exists to prevent, arriving through the front door.
            problems.append(f"collector {entry.id!r}: listed more than once, "
                            "so the second entry is ignored")
            continue
        seen.add(entry.id)
        found.append(entry)
    return found, problems


def enabled(cfg: dict[str, Any]) -> list[Collector]:
    """Just the ones that should run, in configured order."""
    found, _ = configured(cfg)
    return [c for c in found if c.enabled]


@dataclass
class Refetch:
    """What the app may re-read, per collector.

    THE USER'S RULES BEAT THE FILE'S CLAIMS, ALWAYS. A collector declaring
    `we_may_refetch: true` is a third party asking this app to make requests -
    and it must never widen what the app is allowed to fetch. The attended-only
    and blocked host lists are the person's, and they win.
    """

    allowed: set[str] = field(default_factory=set)

    @classmethod
    def from_config(cls, cfg: dict[str, Any]) -> Refetch:
        return cls({c.id for c in enabled(cfg) if c.we_may_refetch})

    def may_refetch(self, source: str) -> bool:
        return source in self.allowed
