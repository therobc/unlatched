"""resumes.py - The person's resume, held BY THE APP rather than pointed at.

WHY A COPY AND NOT A PATH
-------------------------
config held one `resume_path`: a pointer to a file somewhere on disk. Three
things break under that, and all three are silent.

A stored Fit number stops meaning anything. coverage_pct and missing_skills
are written onto each job at screening time; if the file behind them is later
edited or moved, nothing on screen corresponds to a document we can still
produce. With a copy, "this posting said you were missing Customer Service"
always has the exact resume that was true of.

The BEFORE disappears. People optimise a resume by opening it and editing it
in place, which is the whole point of the exercise - so the moment they act on
our advice, a pointer model has lost the version we measured. Holding both is
the only way "here is what changed, and here is what it did to your coverage"
can ever be answered.

A moved or deleted file reads as an EMPTY resume, and an empty resume makes
every single skill report as missing. That is indistinguishable from a
genuinely thin resume, and it is the same silent-zero failure the .docx
handling already had.

THE SHAPE
---------
Copies live in `<profile>/resumes/`. Two roles:

    original   what the person started with, kept untouched
    optimized  the edited version, after the app's keyword and ATS advice

The optimized copy is the one screening reads when it exists, because it is
the resume they are actually sending. The original stays so the two can be
compared, and so a second search can start from the first search's optimized
version - which is the workflow this is for: search, optimise, search again
from the optimised copy, re-optimise for what THAT search is asking for.

Nothing here calls a model. The app reports the gaps; a person or an outside
assistant does the writing and hands the file back. See an earlier change.
"""
from __future__ import annotations

import re
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import paths

if TYPE_CHECKING:
    import os

ORIGINAL = "original"
OPTIMIZED = "optimized"
ROLES = (ORIGINAL, OPTIMIZED)

# Formats the engine can actually read. Anything else is accepted and stored -
# it is the person's document and refusing it helps nobody - but flagged, so
# they are not left wondering why their coverage reads zero.
READABLE_SUFFIXES = frozenset({".txt", ".md", ".docx"})


def resumes_dir(home: str | os.PathLike[str] | None = None) -> Path:
    return paths.data_dir(home) / "resumes"


def _slug(text: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", text).strip("-")
    return cleaned or "resume"


def attach(source: str | os.PathLike[str], role: str,
           home: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    """Copy a resume into the profile under `role`. Returns a record of it.

    The copy is timestamped rather than overwriting, so attaching a new
    optimised version never destroys the one a previous search was screened
    against. `current_path` then resolves to the newest for that role.
    """
    if role not in ROLES:
        raise ValueError(f"role must be one of {ROLES}, got {role!r}")
    src = Path(source)
    if not src.is_file():
        raise FileNotFoundError(f"no resume at {src}")

    target_dir = resumes_dir(home)
    target_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    dest = target_dir / f"{role}-{stamp}-{_slug(src.name)}"
    # Two attaches inside the same second produced the same name, and the
    # second silently overwrote the first - which defeats the one guarantee
    # this module exists to make. The suffix is only ever reached in that
    # case, so ordinary filenames stay readable.
    if dest.exists():
        for attempt in range(2, 100):
            candidate = target_dir / f"{role}-{stamp}-{attempt}-{_slug(src.name)}"
            if not candidate.exists():
                dest = candidate
                break
        else:
            raise RuntimeError(
                f"could not find an unused name for {src.name} - 99 copies "
                "already attached in the same second")
    shutil.copy2(src, dest)

    return {
        "role": role,
        "file": dest.name,
        "attached": datetime.now(UTC).isoformat(timespec="seconds"),
        "source": str(src),
        "readable": src.suffix.lower() in READABLE_SUFFIXES,
    }


def versions(home: str | os.PathLike[str] | None = None) -> list[dict[str, str]]:
    """Every stored copy, newest first, as {role, file, stamp}."""
    directory = resumes_dir(home)
    if not directory.is_dir():
        return []
    found: list[dict[str, str]] = []
    for path in directory.iterdir():
        if not path.is_file():
            continue
        role, _, rest = path.name.partition("-")
        if role not in ROLES:
            continue
        found.append({"role": role, "file": path.name, "stamp": rest[:15]})
    # Newest first WITHIN a role; the stamp sorts lexically because it is
    # written as YYYYMMDDTHHMMSS.
    found.sort(key=lambda v: v["stamp"], reverse=True)
    return found


def current(role: str, home: str | os.PathLike[str] | None = None) -> Path | None:
    """Newest copy for a role, or None."""
    for version in versions(home):
        if version["role"] == role:
            return resumes_dir(home) / version["file"]
    return None


def active_path(cfg: dict[str, Any],
                home: str | os.PathLike[str] | None = None) -> Path | None:
    """The resume screening should read.

    A PINNED COPY WINS. `resume_pinned` names one of the attached files, and it
    is honoured first so the app can offer a real choice - a marker saying "in
    use" over a document the engine was not reading would be worse than no
    choice at all.

    Otherwise the OPTIMISED copy when one exists, because that is the document
    being sent; otherwise the original. `resume_path` in config is still
    honoured last, so a profile set up before any of this existed keeps working
    unchanged rather than silently losing its resume.

    A pin naming a file that is no longer there is IGNORED, not obeyed: removing
    the pinned copy falls back to the automatic rule rather than leaving the
    profile with no resume.

    A PIN NAMES ONE OF THE ATTACHED COPIES AND NOTHING ELSE. It used to be
    joined straight onto the resumes directory, and pathlib does not normalise
    "..", so `resume_pinned: "../../something.txt"` resolved outside the
    profile and was read as the resume - verified, not theorised.

    That matters because of what this value becomes. Resume text is MINE-class
    in this app's trust model, the class attachments.py describes as "offered
    freely" to an assistant, so a pin pointing elsewhere puts some other file's
    contents in front of one. The threat model is not a stranger editing
    config.json; it is the one collectors.check_collector_id already states
    about a different config string - "this string arrives from config a third
    party may have written" - and a handed-over or assistant-written config is
    an ordinary thing here.

    Checking membership of `versions()` rather than sanitising the string does
    both jobs at once: nothing outside the directory can be named, and neither
    can a file inside it that the app did not attach.
    """
    pinned = str(cfg.get("resume_pinned") or "").strip()
    if pinned and any(v["file"] == pinned for v in versions(home)):
        candidate = resumes_dir(home) / pinned
        if candidate.is_file():
            return candidate
    for role in (OPTIMIZED, ORIGINAL):
        path = current(role, home)
        if path is not None:
            return path
    legacy = cfg.get("resume_path")
    if legacy:
        candidate = Path(str(legacy))
        if candidate.is_file():
            return candidate
    return None
