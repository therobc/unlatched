"""ats_audit.py - Deep parse-failure audit of a resume .docx.

Layout problems are the obvious thing to check and rarely the actual cause.
The checks that matter come from quieter places a parser silently mangles
or discards content:

  * document PROPERTIES - author, last-modified-by, company, template. Word
    stamps whoever's install produced the file; a mismatched author is the
    kind of detail a human reviewer notices even when a parser does not.
  * non-ASCII characters - smart quotes, en/em dashes and Unicode bullets
    are the classic source of mojibake once a resume is re-parsed elsewhere.
  * section headings - a parser only recognises a fixed vocabulary. A
    heading outside it is invisible to the parser even though a person
    reads it fine.
  * date formats - a consistent, recognisable pattern is what lets a parser
    compute tenure; mixed formats produce gaps or zero-length roles.
  * contact block - must be readable in the body text, not only in a header
    or footer a parser may not read at all.
  * fonts - an unusual or missing font is a common cause of a bad fallback
    render in whatever system ingests the file next.

Reads a .docx directly from its OOXML package (zipfile + a little XML regex)
so this needs no dependency beyond the standard library. Read-only: this
module reports, it never edits a resume.
"""
from __future__ import annotations

import contextlib
import re
import zipfile
from pathlib import Path

OK, WARN, FAIL = "ok", "WARN", "FAIL"

KNOWN_HEADINGS = {
    "professional summary", "summary", "profile", "objective",
    "technical skills", "skills", "core competencies",
    "work experience", "experience", "professional experience",
    "employment history", "education", "certifications", "licenses",
    "projects", "publications", "awards", "volunteer",
}

SAFE_FONTS = {
    "Calibri", "Arial", "Helvetica", "Times New Roman", "Georgia",
    "Garamond", "Verdana", "Tahoma", "Cambria",
}

MAX_SAFE_SIZE_BYTES = 1_000_000

PROPERTY_TAGS = ("dc:creator", "cp:lastModifiedBy", "dc:title", "dc:subject",
                  "cp:keywords", "dc:description", "cp:category", "cp:revision")
APP_TAGS = ("Company", "Manager", "Template", "Application")


def read_properties(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
        if "docProps/core.xml" in names:
            core = z.read("docProps/core.xml").decode("utf-8", "replace")
            for tag in PROPERTY_TAGS:
                m = re.search(rf"<{tag}[^>]*>(.*?)</{tag}>", core, re.DOTALL)
                out[tag] = m.group(1).strip() if m else ""
        if "docProps/app.xml" in names:
            app = z.read("docProps/app.xml").decode("utf-8", "replace")
            for tag in APP_TAGS:
                m = re.search(rf"<{tag}[^>]*>(.*?)</{tag}>", app, re.DOTALL)
                out[tag] = m.group(1).strip() if m else ""
    return out


def body_text(path: Path) -> str:
    with zipfile.ZipFile(path) as z:
        xml = z.read("word/document.xml").decode("utf-8", "replace")
    xml = re.sub(r"</w:p>", "\n", xml)
    return re.sub(r"<[^>]+>", "", xml)


def fonts_used(path: Path) -> set[str]:
    with zipfile.ZipFile(path) as z:
        xml = z.read("word/document.xml").decode("utf-8", "replace")
        with contextlib.suppress(KeyError):
            xml += z.read("word/styles.xml").decode("utf-8", "replace")
    return set(re.findall(r'w:ascii="([^"]+)"', xml))


def audit_text(md_or_plain_text: str) -> list[tuple[str, str, str]]:
    """Heading and date checks over a plain-text (or markdown) rendering of
    the resume, independent of the .docx package.
    """
    rows = []
    heads = [h.strip().lower() for h in re.findall(r"^##\s+(.+)$", md_or_plain_text, re.MULTILINE)]
    if heads:
        unknown = [h for h in heads if h not in KNOWN_HEADINGS]
        rows.append((OK if not unknown else WARN, "headings",
                     f"{len(heads)} sections; "
                     + ("all recognised by parsers" if not unknown
                        else f"unrecognised: {unknown}")))
    ranges = re.findall(
        r"([A-Z][a-z]{2}\s+\d{4})\s*-\s*([A-Z][a-z]{2}\s+\d{4}|Present)",
        md_or_plain_text)
    other = re.findall(r"\b(\d{1,2}/\d{4}|\d{4}\s*-\s*\d{4})\b", md_or_plain_text)
    if ranges or other:
        rows.append((OK if ranges and not other else WARN, "dates",
                     f"{len(ranges)} ranges in 'Mon YYYY - Mon YYYY' form"
                     + (f"; {len(other)} in another format: {other[:3]}" if other else "")))
    return rows


def audit(docx_path: str, plain_text: str = "") -> list[tuple[str, str, str]]:
    """Run every check. Returns a list of (level, category, message).
    `plain_text` is an optional markdown/plain rendering used for the
    heading and date checks; without it those two checks are skipped.
    """
    path = Path(docx_path)
    if not path.exists():
        return [(FAIL, "file", f"missing: {docx_path}")]

    rows: list[tuple[str, str, str]] = []
    props = read_properties(path)

    author = props.get("dc:creator", "")
    modby = props.get("cp:lastModifiedBy", "")
    rows.append((OK if author else WARN, "author",
                 f"dc:creator = {author!r}" + ("" if author else " - not set")))
    rows.append((OK if not modby or modby == author else WARN, "author",
                 f"lastModifiedBy = {modby!r}"
                 + ("" if not modby or modby == author
                    else " - different from the document author")))
    for tag, label in (("Company", "company"), ("Manager", "manager"),
                        ("cp:keywords", "keywords"), ("dc:subject", "subject"),
                        ("dc:description", "description")):
        v = props.get(tag, "")
        rows.append((OK if not v else WARN, "properties",
                     f"{label} = {v!r}" + ("" if not v else " - consider clearing it")))
    rows.append((OK, "properties", f"generator = {props.get('Application', '')!r}"))

    text = body_text(path)
    bad: dict[str, int] = {}
    for ch in text:
        if ord(ch) > 127:
            bad[ch] = bad.get(ch, 0) + 1
    if bad:
        shown = ", ".join(f"{c!r} x{n}" for c, n in
                           sorted(bad.items(), key=lambda x: -x[1])[:6])
        rows.append((FAIL, "encoding", f"{sum(bad.values())} non-ASCII characters: {shown}"))
    else:
        rows.append((OK, "encoding", "ASCII only - nothing to mangle"))

    rows += audit_text(plain_text or text)

    email = re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", text)
    rows.append((OK if email else FAIL, "contact",
                 f"email in body: {email.group(0) if email else 'NOT FOUND'}"))

    used = fonts_used(path)
    risky = {x for x in used if x not in SAFE_FONTS}
    rows.append((OK if not risky else WARN, "fonts",
                 f"{sorted(used)}" + ("" if not risky else f" - unusual: {sorted(risky)}")))

    size = path.stat().st_size
    rows.append((OK if size < MAX_SAFE_SIZE_BYTES else WARN, "file",
                 f"{size / 1024:.0f} KB, .docx"))
    return rows
