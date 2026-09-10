"""Hand back what this app knows is closed, so the sender can stop asking.

THE FLOW HAS ONLY EVER RUN ONE WAY. A collector hands over its rows and its
closures; nothing goes back. So a posting the PERSON discovers is closed - they
opened it, read "no longer accepting applications", and marked it taken down -
is known here and nowhere else. The collector keeps that lead on its own board,
and keeps spending sweep budget re-checking it.

That budget is the whole reason this matters. Measured 2026-08-26 on the live
pair: 1,003 open leads on the collector's side, 505 of them never checked once,
and the sweep manages roughly four or five rows a minute because it is rate
limited. A person marking a posting closed is the fastest and most reliable
signal in the system, and it was being thrown away.

A FILE, NOT A WRITE INTO THEIR DATABASE. Same contract as the incoming handoff,
for the same reasons its own author gave: the sender owns its store, the file is
inspectable, and neither side has to be running when the other one is.

WHAT TRAVELS IS "CLOSED", NOT "REMOVED". Retiring a row is the person taking it
off their own lists - a decision about their search, and none of the sender's
business. Being closed is a fact about the world and belongs to both.
"""
from __future__ import annotations

import json
import urllib.parse
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from . import collectors as collectors_mod
from . import importer

if TYPE_CHECKING:  # pragma: no cover - typing only
    import sqlite3
    from pathlib import Path

# The shape the file declares. The incoming handoff carries no version and its
# checker says so every time - "a contract without a version cannot change" -
# so this side does not repeat that mistake.
VERSION = 1


def pending(con: sqlite3.Connection,
            collector: collectors_mod.Collector) -> list[dict[str, Any]]:
    """Everything this app holds as closed that came from `collector`.

    NOT ONLY WHAT THE PERSON MARKED. Provenance is not recorded - a hand mark
    and a closure the sender itself pushed both set delisted_at - and it does
    not need to be: the useful signal is "this app believes this posting is
    closed", and the sender can compare that against its own record. Anything
    it already knew costs it one line to skip.
    """
    rows = con.execute(
        "SELECT key, url, title, source, delisted_at FROM jobs "
        "WHERE delisted_at IS NOT NULL "
        "ORDER BY delisted_at DESC, key").fetchall()
    theirs = _hosts(con, collector)
    rows = [r for r in rows
            if r["source"] == collector.id or _host(r["url"]) in theirs]
    # ONE ENTRY PER POSTING. A job added by hand and the same job handed over
    # by the collector are two rows here with two keys, and both spell back to
    # the same key in the sender's namespace. The URL is what identifies the
    # posting, so it is what deduplicates.
    seen: set[str] = set()
    unique = []
    for row in rows:
        url = (row["url"] or "").strip()
        if url and url in seen:
            continue
        seen.add(url)
        unique.append(row)
    rows = unique
    return [{
        # IN THE SENDER'S OWN NAMESPACE. Their keys say `manual:` and ours say
        # `imported:` for the same posting - the disagreement rekey.py fixed on
        # our side and cannot fix on theirs. Handing back a key they do not
        # recognise is how the closures went the other way in the first place:
        # 114 postings known closed sat flagged open because the prefix did not
        # match (measured 2026-08-22).
        "key": _their_key(collector, str(row["key"])),
        # THE URL IS THE RELIABLE JOIN and the key is the convenience. Their
        # store is keyed on the posting URL; ours is keyed on a slug of it.
        "url": row["url"] or "",
        "title": row["title"] or "",
        "closed_at": _stamp(row["delisted_at"]),
    } for row in rows]


def _host(url: str | None) -> str:
    """The host a posting lives on, lowercased, or "" if there is not one."""
    if not url:
        return ""
    return (urllib.parse.urlsplit(url.strip()).hostname or "").lower()


def _hosts(con: sqlite3.Connection,
           collector: collectors_mod.Collector) -> set[str]:
    """The hosts this collector's own rows live on.

    WHY NOT JUST FILTER ON THE SOURCE LABEL. A posting the person added by
    hand from the same board carries source "manual", not the collector's id -
    but it is the same board, the sender may well hold it, and its closure is
    exactly as useful. Deciding by host rather than by label catches it.

    READ FROM THE DATA rather than configured. Nothing here needs to know
    that a particular collector means any particular site; whatever its own
    rows point at is what belongs to it. A collector holding nothing yet
    claims no hosts, which is the right answer for one that has never handed
    anything over.
    """
    rows = con.execute(
        "SELECT DISTINCT url FROM jobs WHERE source = ? AND url IS NOT NULL",
        (collector.id,)).fetchall()
    return {h for h in (_host(r["url"]) for r in rows) if h}


def _stamp(value: str | None) -> str:
    """A closure time the reader cannot misread.

    delisted_at is stored as UTC with NO OFFSET on it. The program reading this
    keeps its own timestamps in naive LOCAL time, so handing over "01:39" bare
    would land four hours in ITS future - and a closure dated in the future is
    the kind of thing that silently sorts wrong rather than failing. So the
    offset is attached explicitly here.

    Anything that does not parse is passed through untouched: an older row
    carrying just a date is still a true closure, and guessing a clock time for
    it would be inventing precision.
    """
    if not value:
        return ""
    # A BARE DATE IS LEFT ALONE. fromisoformat parses "2026-08-11" perfectly
    # happily and returns midnight, so the sentence above about not inventing
    # precision was false until this line existed - caught by its own test.
    if "T" not in value:
        return value
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return value
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.isoformat(timespec="seconds")


def _their_key(collector: collectors_mod.Collector, key: str) -> str:
    """Our key, spelled the way this collector spells its own."""
    legacy = importer.LEGACY_PREFIXES.get(collector.id)
    if not legacy:
        return key
    prefix, _, rest = key.partition(":")
    if prefix == collector.id and rest:
        return f"{sorted(legacy)[0]}:{rest}"
    return key


def write(path: Path, collector: collectors_mod.Collector,
          rows: list[dict[str, Any]]) -> int:
    """Write the hand-back file. Returns how many closures it carries."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": VERSION,
        "source": "unlatched",
        "collector": collector.id,
        "generated_at": datetime.now(tz=UTC).astimezone().isoformat(
            timespec="seconds"),
        "closed": rows,
    }
    # WRITTEN WHOLE, THEN MOVED INTO PLACE. The reader on the other side may
    # look at any moment, and a half-written file is a parse error that reads
    # as a broken contract rather than as a race.
    staged = path.with_suffix(path.suffix + ".part")
    staged.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    staged.replace(path)
    return len(rows)


def default_path(collector: collectors_mod.Collector) -> Path:
    """Beside the file that collector hands us, which is the one directory both
    sides have already agreed on.
    """
    from pathlib import Path as _Path
    inbox = _Path(collector.path)
    return inbox.with_name(f"{inbox.stem}-closed-by-unlatched.json")


def hand_back(con: sqlite3.Connection, cfg: dict[str, Any]) -> dict[str, int]:
    """Write a hand-back file for every configured collector.

    Returns {collector id: closures written}. Never raises for a path that
    cannot be written: this runs at the end of a refresh, and a refresh that
    collected successfully must not report failure because a courtesy file
    could not be saved.
    """
    written: dict[str, int] = {}
    for collector in collectors_mod.enabled(cfg):
        try:
            rows = pending(con, collector)
            written[collector.id] = write(default_path(collector), collector, rows)
        except OSError:
            continue
    return written
