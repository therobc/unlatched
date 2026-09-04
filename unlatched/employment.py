"""employment.py - Full time, part time, contract: one vocabulary from many.

Every ATS names this differently, and a sample of real collected values shows
the spread: "Full time", "Full-Time", "Fulltime-Regular", "['FULL_TIME']",
"Part time", "Independent Contractor", "Independent Contractor T2". Filtering
on the raw strings would need a rule per vendor and would still miss the next
one, so everything is normalised to a small closed set first.

Postings that state nothing are NOT guessed at. A missing employment type
means the posting never said, which is different from it being full time -
the same not-stated-vs-not-required rule requirements.py follows. A search
that filters on type keeps unstated postings rather than discarding them,
because dropping on silence would hide most of the corpus.
"""
from __future__ import annotations

import re

# The closed set. Order matters only for reporting; membership is what the
# config validates against.
KINDS = ("full_time", "part_time", "contract", "temporary", "internship")

# Checked in order: the first match wins, so the more specific patterns come
# first. "Contract to hire" is a contract, not a hire; an internship is an
# internship even when it is also full time for the summer.
_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("internship", re.compile(r"\bintern(ship)?\b|\bco[- ]?op\b", re.IGNORECASE)),
    ("contract", re.compile(
        r"\bcontract\b|\bcontractor\b|\b1099\b|\bc2c\b|\bcorp[- ]to[- ]corp\b|"
        r"\bstatement of work\b|\bfreelance\b|\bconsultanc(y|ies)\b", re.IGNORECASE)),
    ("temporary", re.compile(
        r"\btemp(orary)?\b|\bseasonal\b|\bfixed[- ]term\b|\binterim\b", re.IGNORECASE)),
    ("part_time", re.compile(r"\bpart[\s\-_]?time\b|\bpt\b", re.IGNORECASE)),
    ("full_time", re.compile(
        r"\bfull[\s\-_]?time\b|\bfulltime\b|\bpermanent\b|\bregular\b", re.IGNORECASE)),
)


def normalize(raw: str) -> str | None:
    """A vendor's employment-type string -> one of KINDS, or None when the
    value says nothing usable ("", "OTHER", an unrecognised label).
    """
    text = (raw or "").strip()
    if not text:
        return None
    # Some collectors hand back a list and it reaches the column as its repr
    # ("['FULL_TIME']"). Strip the brackets and quotes rather than teaching
    # every caller to parse it.
    text = re.sub(r"[\[\]'\"]", " ", text)
    for kind, pattern in _PATTERNS:
        if pattern.search(text):
            return kind
    return None


def detect(employment_type: str, title: str = "", description: str = "") -> str | None:
    """Best available reading of a posting's employment type.

    The structured field is trusted first because it is the employer's own
    answer. Only when it is absent or unrecognised does this fall back to the
    title and then to the top of the description - a posting that says
    "12+ months contract" in its first lines is stating a contract as surely
    as a field would.
    """
    from_field = normalize(employment_type)
    if from_field:
        return from_field
    from_title = normalize(title)
    if from_title:
        return from_title
    # Only the opening of the description: a benefits section far below often
    # mentions "full-time employees are eligible for...", which describes the
    # benefit population, not this role.
    return normalize((description or "")[:600])


def accepted(kind: str | None, wanted: list[str] | None) -> tuple[bool, str]:
    """Does a posting's type match what the searcher will accept?

    Returns (ok, reason). An empty `wanted` accepts everything, and an
    UNKNOWN kind is always accepted: the posting never said, and silence is
    not a reason to hide a job.

    A False here FLAGS the posting rather than discarding it (2026-08-05):
    somebody open only to full time will still read the contract and part-time
    postings that surface, and mark them passed themselves. A person
    dismissing a job in two seconds is cheap; a job they never saw is not
    recoverable. Screening turns this into an "alt" verdict, never a drop.
    """
    if not wanted:
        return True, ""
    if kind is None:
        return True, ""
    if kind in wanted:
        return True, ""
    readable = kind.replace("_", " ")
    return False, f"{readable} role, not among the accepted employment types"
