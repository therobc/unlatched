"""criteria.py - what this person is looking for, in a form another tool can use.

One search, two tools. A person should describe what they want ONCE - the
titles, the floor, the places, what they will not take - and have every
collector work from it. Retyping it into a second app is how the two quietly
drift apart, and the day they disagree neither is wrong on its own terms and
the person cannot tell which list is short because of a real absence and which
because of a setting they forgot to mirror.

WHAT TRAVELS, and why these three blocks:

  search   the criteria proper - titles wanted and refused, floor, places,
           work modes, employment types, seniority markers
  skills   the vocabulary Fit is measured against
  profile  what they can accept - travel, shifts, clearance, education - which
           is a fact about the person and therefore identical in every tool
           that screens for them

WHAT DOES NOT TRAVEL, deliberately:

  credentials, resume paths, refresh times, source toggles, agent API keys

Those are about a particular INSTALL, not about the search. An API key crossing
a tool boundary in a criteria file is a credential leak by another name, and a
refresh schedule copied from another machine is how two collectors end up
running at the same minute.

The file is versioned, because the receiving side is a different program on a
different release cadence and needs to be able to say "I do not understand
this" rather than half-apply it.
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

FORMAT = "unlatched.criteria"
VERSION = 1

# Blocks carried across, in the order a person would read them.
BLOCKS = ("search", "skills", "profile")


def export(cfg: dict[str, Any]) -> dict[str, Any]:
    """The criteria, ready to write out."""
    return {
        "format": FORMAT,
        "version": VERSION,
        **{block: cfg.get(block) for block in BLOCKS if cfg.get(block) is not None},
    }


def write(cfg: dict[str, Any], path: Path) -> dict[str, Any]:
    payload = export(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return payload


class CriteriaError(ValueError):
    """A file that cannot be read as criteria, named so the caller can say why."""


def read(path: Path) -> dict[str, Any]:
    """Load a criteria file, refusing anything it does not recognise.

    A wrong or newer format is REFUSED rather than partially applied. Half a
    set of criteria is worse than none: the search still runs, still returns
    results, and the person has no way to see that the floor never arrived.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        msg = f"not valid JSON: {e}"
        raise CriteriaError(msg) from e
    if not isinstance(data, dict):
        msg = "expected an object with format, version and the criteria blocks"
        raise CriteriaError(msg)
    if data.get("format") != FORMAT:
        msg = f"not an {FORMAT} file (format={data.get('format')!r})"
        raise CriteriaError(msg)
    version = data.get("version")
    if not isinstance(version, int) or version > VERSION:
        msg = (f"version {version!r} is newer than this app understands "
               f"(supports up to {VERSION})")
        raise CriteriaError(msg)
    if not any(block in data for block in BLOCKS):
        msg = f"carries none of {', '.join(BLOCKS)}"
        raise CriteriaError(msg)
    return data


def apply(cfg: dict[str, Any], incoming: dict[str, Any]) -> list[str]:
    """Merge criteria into `cfg` in place. Returns which blocks changed.

    Block by block, and only blocks the file actually carries: a file with just
    `search` must not blank somebody's skills vocabulary. Within a block the
    incoming keys win, and keys the sender does not know about are left alone -
    so an older exporter cannot silently drop a setting a newer app added.
    """
    changed = []
    for block in BLOCKS:
        if block not in incoming:
            continue
        current = cfg.get(block)
        if isinstance(current, dict) and isinstance(incoming[block], dict):
            merged = {**current, **incoming[block]}
        else:
            merged = incoming[block]
        if merged != current:
            cfg[block] = merged
            changed.append(block)
    return changed
