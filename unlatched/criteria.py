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


# How an incoming list meets one that is already there.
#
# TWO ANSWERS, BOTH RIGHT, WHICH IS WHY THE PERSON PICKS. Somebody keeping one
# search in two tools wants the union - a title added in the other app should
# arrive without deleting the ones typed here. Somebody handed a colleague's
# file wants exactly that file. This function did only the second,
# by construction: it merged at KEY level, so a list arrived whole and replaced
# what was there. The choice is what the ticket asks be made visible.
MODES = ("merge", "replace")


def _combine(current: Any, incoming: Any, mode: str) -> Any:
    """One key's new value.

    LISTS ARE THE ONLY PLACE THE MODE SHOWS. A floor, a currency or a tick box
    holds one answer, and two answers cannot both be kept - so under either
    mode the incoming one wins. A list holds several, and there the question
    "as well as, or instead of" is real.
    """
    if mode == "merge" and isinstance(current, list) and isinstance(incoming, list):
        # Existing entries first and in their own order: a merge must read as
        # an addition to what the person built, not as a reshuffle of it.
        out = list(current)
        out.extend(item for item in incoming if item not in out)
        return out
    return incoming


def apply(cfg: dict[str, Any], incoming: dict[str, Any],
          mode: str = "replace") -> list[str]:
    """Bring criteria into `cfg` in place. Returns which blocks changed.

    Block by block, and only blocks the file actually carries: a file with just
    `search` must not blank somebody's skills vocabulary. Within a block the
    incoming keys win, and keys the sender does not know about are left alone -
    so an older exporter cannot silently drop a setting a newer app added.

    `mode` decides what happens to a LIST. Defaults to "replace", which is what
    this function did before the choice existed, so a caller that does not pass
    one gets the behaviour it already had.
    """
    if mode not in MODES:
        msg = f"mode has to be one of {', '.join(MODES)}, not {mode!r}"
        raise CriteriaError(msg)
    changed = []
    for block in BLOCKS:
        if block not in incoming:
            continue
        current = cfg.get(block)
        if isinstance(current, dict) and isinstance(incoming[block], dict):
            merged = dict(current)
            for key, value in incoming[block].items():
                merged[key] = _combine(current.get(key), value, mode)
        else:
            merged = incoming[block]
        if merged != current:
            cfg[block] = merged
            changed.append(block)
    return changed


def preview(cfg: dict[str, Any], incoming: dict[str, Any],
            mode: str = "replace") -> list[dict[str, Any]]:
    """What an import would do to each key, without doing any of it.

    SHOWN BEFORE ANYTHING IS WRITTEN, because a criteria file is somebody
    else's idea of the search and the two are not the same document. "3 blocks
    changed" does not tell a person whether their salary floor is about to move
    - which is exactly the thing they would have wanted to know first.

    One entry per key that would actually change, carrying the old and new
    values so the caller can word it however suits its screen.
    """
    before = json.loads(json.dumps({b: cfg.get(b) for b in BLOCKS}))
    after = json.loads(json.dumps(before))
    apply(after, incoming, mode)
    rows: list[dict[str, Any]] = []
    for block in BLOCKS:
        old_block = before.get(block) or {}
        new_block = after.get(block) or {}
        if not isinstance(old_block, dict) or not isinstance(new_block, dict):
            if old_block != new_block:
                rows.append({"block": block, "key": "", "was": old_block,
                             "becomes": new_block, "added": 0, "removed": 0})
            continue
        for key in sorted(set(old_block) | set(new_block)):
            was = old_block.get(key)
            becomes = new_block.get(key)
            if was == becomes:
                continue
            added = removed = 0
            if isinstance(was, list) or isinstance(becomes, list):
                was_list = was if isinstance(was, list) else []
                new_list = becomes if isinstance(becomes, list) else []
                added = len([x for x in new_list if x not in was_list])
                removed = len([x for x in was_list if x not in new_list])
            rows.append({"block": block, "key": key, "was": was,
                         "becomes": becomes, "added": added, "removed": removed})
    return rows
