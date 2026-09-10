"""attachments.py - files and links a person keeps beside a job.

WHO WROTE THE BYTES IS THE WHOLE DESIGN. An attachment is either something the
person made - a resume, a cover letter, their own notes - or something that
came from the employer's side of the conversation: the posting, a description
PDF, a recruiter's email, a screenshot of an ad.

Those two are treated differently, and only in one direction (decided
2026-08-12): employer-written material is the ONLY thing whose access is
restricted, and everything else stays reachable by an assistant - resumes above
all. An assistant on this machine helping tailor a resume is the point of the app
having an agent surface at all; the same assistant reading text a stranger
wrote is a prompt-injection vector. So POSTING-class content never reaches
`brief`, and MINE-class content is offered freely.

THIS MODULE PARSES NOTHING. It classifies by file extension, copies bytes, and
records rows. No OCR, no PDF text layer, no docx unzip - nothing extracted is
nothing to inject, and it also means the app carries no document parser for a
malicious file to attack. The desktop renders images and plain text, which are
the two things that can be shown without interpreting a format; everything else
is handed back to the person to open in whatever they normally use.

MIRRORED IN desktop/src/attachments.rs, which is what the UI writes through.
The two lists are checked against each other by a test on the Rust side, so a
suffix added to one and not the other fails a build rather than quietly letting
a refused file through the other door.
"""
from __future__ import annotations

import re
import secrets
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import links as links_mod

if TYPE_CHECKING:
    import sqlite3

# The two trust classes. Values, not booleans, because "is_untrusted" reads
# backwards half the time it is used and a third class is easy to imagine.
POSTING = "posting"
MINE = "mine"
CLASSES = (POSTING, MINE)

# REFUSED AT ATTACH TIME rather than stored and guarded afterwards. A file the
# app will not store cannot be double-clicked out of a folder six months later
# by somebody who has forgotten where it came from, and the guard cannot be
# forgotten by a future code path that opens attachments a new way.
#
# Windows treats several of these as executable without the extension being
# visible in Explorer, which is exactly how they get run by accident.
REFUSED_SUFFIXES = frozenset({
    ".exe", ".com", ".bat", ".cmd", ".msi", ".msp", ".scr", ".pif", ".cpl",
    ".hta", ".js", ".jse", ".vbs", ".vbe", ".wsf", ".wsh", ".ps1", ".psm1",
    ".reg", ".lnk", ".inf", ".sct", ".jar",
})

# EVERY FILE IS DOWNLOAD-ONLY (decided 2026-08-13). The kinds below decide
# an icon and a hover, never a renderer: the app opens nothing, so it
# carries no decoder or parser for any format at all.
# That is the strongest form of the read-only rule rather than a weaker one -
# the attack surface is not reduced, it is absent.
IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"})
TEXT_SUFFIXES = frozenset({".txt", ".csv", ".md", ".log", ".eml"})
OFFICE_SUFFIXES = frozenset({".doc", ".docx", ".rtf", ".xls", ".xlsx", ".odt"})
PDF_SUFFIXES = frozenset({".pdf"})

KIND_IMAGE, KIND_TEXT, KIND_PDF, KIND_OFFICE, KIND_OTHER, KIND_LINK = (
    "image", "text", "pdf", "office", "other", "link")


class Refused(Exception):  # noqa: N818 - a refusal, not an error condition
    """The app will not store this file, and says why."""


def suffix_of(name: str) -> str:
    return Path(name).suffix.lower()


def kind_of(name: str) -> str:
    """What the app can do with a file of this name. Extension only.

    Deliberately not content sniffing: sniffing means reading the file to
    decide how to read the file, and the decision this feeds is whether to
    render it at all.
    """
    suffix = suffix_of(name)
    if suffix in IMAGE_SUFFIXES:
        return KIND_IMAGE
    if suffix in TEXT_SUFFIXES:
        return KIND_TEXT
    if suffix in OFFICE_SUFFIXES:
        return KIND_OFFICE
    if suffix in PDF_SUFFIXES:
        return KIND_PDF
    return KIND_OTHER


def check_allowed(name: str) -> None:
    """Raise Refused if this is a file the app will not take."""
    suffix = suffix_of(name)
    if suffix in REFUSED_SUFFIXES:
        msg = (f"{suffix} files are not accepted as attachments - Windows can "
               "run them, and nothing here needs to. Keep it somewhere you "
               "choose deliberately.")
        raise Refused(msg)
    if not suffix:
        msg = ("a file with no extension cannot be classified, so the app "
               "cannot tell whether it is safe to show. Rename it and try "
               "again.")
        raise Refused(msg)


# A display name is TEXT ON A SCREEN AND NOTHING ELSE. It never reaches the
# filesystem (see stored_name) and, for posting-class rows, it is also read by
# an agent surface - so a crafted name is a place to hide an instruction as
# much as a path traversal.
_UNSAFE_NAME = re.compile(r"[\x00-\x1f\x7f]")
MAX_DISPLAY_NAME = 120


def safe_display_name(name: str) -> str:
    cleaned = _UNSAFE_NAME.sub(" ", Path(name).name).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    if len(cleaned) > MAX_DISPLAY_NAME:
        head = cleaned[: MAX_DISPLAY_NAME - 3]
        cleaned = f"{head}..."
    return cleaned or "attachment"


def stored_name(original: str) -> str:
    """The name the bytes are actually written under.

    GENERATED, never the original. A name chosen by whoever wrote the file
    cannot then traverse a path, collide with another attachment, or dress
    itself up as a different type. The extension is kept because the person
    downloads these back out and their own machine needs it - and it is
    rebuilt from allowed characters rather than copied.
    """
    suffix = suffix_of(original)
    safe_suffix = "." + re.sub(r"[^a-z0-9]", "", suffix)[:8] if suffix else ""
    return f"{secrets.token_hex(8)}{safe_suffix}"


def directory(home: Path | str, trust: str) -> Path:
    """Where a class of attachment lives.

    SEPARATE DIRECTORIES PER CLASS, so "keep the untrusted ones away from an
    agent" is one path to deny rather than a per-file decision somebody has to
    get right every time.
    """
    if trust not in CLASSES:
        msg = f"unknown attachment class {trust!r}"
        raise ValueError(msg)
    return Path(home) / "attachments" / trust


def add_file(con: sqlite3.Connection, home: Path | str, key: str,
             source: Path | str, trust: str, at: str) -> dict[str, Any]:
    """Copy a file in and record it. Returns the stored row."""
    source = Path(source)
    check_allowed(source.name)
    if trust not in CLASSES:
        msg = f"unknown attachment class {trust!r}"
        raise ValueError(msg)
    dest_dir = directory(home, trust)
    dest_dir.mkdir(parents=True, exist_ok=True)
    stored = stored_name(source.name)
    # copy, not move: the person's own file stays where they left it.
    shutil.copyfile(source, dest_dir / stored)
    size = (dest_dir / stored).stat().st_size
    display = safe_display_name(source.name)
    cur = con.execute(
        "INSERT INTO attachment (key, trust, kind, stored_name, display_name, "
        "                         url, bytes, added_at) "
        "VALUES (?, ?, ?, ?, ?, NULL, ?, ?)",
        (key, trust, kind_of(source.name), stored, display, size, at))
    con.commit()
    return {"id": cur.lastrowid, "key": key, "trust": trust,
            "kind": kind_of(source.name), "stored_name": stored,
            "display_name": display, "bytes": size, "added_at": at}


def add_link(con: sqlite3.Connection, key: str, url: str, label: str,
             trust: str, at: str) -> dict[str, Any]:
    """Record a URL beside a job.

    Free in every sense that matters: no bytes stored, no format to parse, no
    malicious-file risk at all. The recruiter's profile, the take-home repo,
    the scheduling link.

    THE SCHEME IS CHECKED HERE, at attach time, for the same reason a file's
    extension is: this row becomes something the desktop hands to the
    operating system to open, and db.py calls that the store boundary for
    links - "anything that is not plain http/https loses its link at the one
    point every collector and the hand-add path both pass through". An
    attachment was the one route that did not pass through it.

    REFUSED RATHER THAN BLANKED, unlike the collectors. A collector handling a
    thousand postings drops a bad link and keeps the job; a person typing one
    link typed it on purpose and is owed an answer. Same reasoning as
    manual.add, which refuses for the same reason.

    The desktop re-checks before opening anything (browse::open, via
    fmt::safe_link), so this is a second line rather than the only one - which
    is the arrangement worth having, since the next surface to open an
    attachment link might not go through browse.rs at all.
    """
    if trust not in CLASSES:
        msg = f"unknown attachment class {trust!r}"
        raise ValueError(msg)
    if not links_mod.is_safe(url):
        msg = (f"only http and https links can be attached, got {url!r}. "
               "Copy the address out of your browser's address bar.")
        raise Refused(msg)
    display = safe_display_name(label or url)
    cur = con.execute(
        "INSERT INTO attachment (key, trust, kind, stored_name, display_name, "
        "                         url, bytes, added_at) "
        "VALUES (?, ?, ?, NULL, ?, ?, NULL, ?)",
        (key, trust, KIND_LINK, display, url, at))
    con.commit()
    return {"id": cur.lastrowid, "key": key, "trust": trust, "kind": KIND_LINK,
            "display_name": display, "url": url, "added_at": at}


def list_for(con: sqlite3.Connection, key: str) -> list[dict[str, Any]]:
    rows = con.execute(
        "SELECT id, key, trust, kind, stored_name, display_name, url, bytes, "
        "       added_at FROM attachment WHERE key = ? ORDER BY id ASC",
        (key,)).fetchall()
    return [dict(row) for row in rows]


def set_trust(con: sqlite3.Connection, home: Path | str, attachment_id: int,
              trust: str, at: str) -> bool:
    """Move one attachment between classes, keeping the history.

    LOGGED, because the question people ask later is not "what class is this"
    but "why did the assistant see this one", and only a history answers that.

    THE BYTES MOVE TOO. The class is a directory, not just a column - that is
    what makes "keep the untrusted ones away from an agent" one path to deny -
    so a row whose class changed while its file stayed put would leave the
    column and the disk disagreeing. Caught by a positive control: a flipped
    attachment's path pointed at a file that was not there, which would also
    have left an orphan behind on delete.
    """
    if trust not in CLASSES:
        msg = f"unknown attachment class {trust!r}"
        raise ValueError(msg)
    row = con.execute("SELECT trust, stored_name FROM attachment WHERE id = ?",
                      (attachment_id,)).fetchone()
    if row is None:
        return False
    was, stored = row[0], row[1]
    if stored and was != trust:
        dest_dir = directory(home, trust)
        dest_dir.mkdir(parents=True, exist_ok=True)
        shutil.move(str(directory(home, was) / stored), str(dest_dir / stored))
    con.execute("UPDATE attachment SET trust = ? WHERE id = ?",
                (trust, attachment_id))
    con.execute(
        "INSERT INTO attachment_trust_log (attachment_id, was, now, at) "
        "VALUES (?, ?, ?, ?)", (attachment_id, row[0], trust, at))
    con.commit()
    return True


def remove(con: sqlite3.Connection, home: Path | str,
           attachment_id: int) -> bool:
    """Delete one attachment, bytes and all. The person's own call."""
    row = con.execute(
        "SELECT trust, stored_name FROM attachment WHERE id = ?",
        (attachment_id,)).fetchone()
    if row is None:
        return False
    trust, stored = row[0], row[1]
    if stored:
        path = directory(home, trust) / stored
        path.unlink(missing_ok=True)
    con.execute("DELETE FROM attachment WHERE id = ?", (attachment_id,))
    con.commit()
    return True


def for_agent(row: dict[str, Any]) -> dict[str, Any]:
    """What an agent surface may be told about one attachment.

    THE WHOLE PROTECTION, IN ONE FUNCTION, so there is a single place to read
    and a single place to test. A posting-class row yields metadata and a
    sanitised name - never bytes, never extracted text, and never a path that
    resolves to the file. A row the person made yields its path, which is the
    point: an assistant asked to tailor a resume has to be able to open it.
    """
    common = {
        "id": row["id"],
        "kind": row["kind"],
        "added_at": row["added_at"],
        "bytes": row["bytes"],
        "trust": row["trust"],
    }
    if row["trust"] == POSTING:
        # The name is shown because "there is a PDF here" is useful and true;
        # it is re-sanitised on the way out because the same string is being
        # handed to a model.
        common["display_name"] = safe_display_name(row["display_name"] or "")
        common["readable"] = False
        common["withheld"] = (
            "came from the employer's side, so its contents are not offered "
            "to an assistant")
        return common
    common["display_name"] = row["display_name"]
    common["readable"] = True
    if row["kind"] == KIND_LINK:
        common["url"] = row["url"]
    return common


def path_for_agent(home: Path | str, row: dict[str, Any]) -> str | None:
    """The file path an agent may open, or None if it may not.

    Separate from for_agent because a path is the one field whose presence is
    the permission: a caller that forgets to check `readable` still cannot get
    a posting-class path out of this.
    """
    if row["trust"] == POSTING or not row.get("stored_name"):
        return None
    return str(directory(home, row["trust"]) / row["stored_name"])
