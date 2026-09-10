"""coverage.py - What share of a posting's requested skills does the resume
evidence?

THE METRIC THAT WAS WRONG
--------------------------
An earlier version scored coverage as the share of every distinct WORD in a
posting that also appeared in the resume. That is unstable and it is not
what "skill coverage" means to a reader: the number moved on its own when a
posting simply used richer prose, because a longer description has more
distinct words and the denominator grows with words the resume has no
reason to contain ("collaborate", "stakeholders", "passionate").

Scoring against a fixed SKILL VOCABULARY (config.skills, supplied by the
user) fixes both problems: only terms that are actual skills count, so the
number means "of the skills this employer asked for, how many does the
resume evidence" - and it does not move when a posting is merely wordier,
because the vocabulary the posting is scored against never changes.

INFLECTIONS
------------------
Exact word-boundary matching invents gaps: a resume that says
"Communications" does not literally contain "Communication", and one that
says "Diagnosing" does not contain "Diagnose". `present()` matches the
ordinary inflections of a term - plural/-s/-es, or the -ing/-ed/-es forms of
a verb - without ever treating a shorter term as a PREFIX of an unrelated
longer word. Suffixes are only ever appended to the whole term, which is
what keeps "soft" from matching inside "software" and "AI" from matching
inside "email".

CASE NORMALISATION
--------------------------
Both sides of every comparison are lowercased before matching, once, in this
module - not left to whichever caller remembers to do it. A mismatch here
(needle lowercased, haystack not) previously produced two impossible
numbers side by side on the same run: "100% coverage, 0 gaps" for one
document and a real skill scoring zero demand across the whole corpus.
"""
from __future__ import annotations

import re
from typing import Any


def present(term: str, text_lower: str) -> bool:
    """Does `term` (in any ordinary inflection) appear in `text_lower`?

    `text_lower` must already be lowercased by the caller - normalising it on
    every call here would repeat the same O(n) pass for every term in the
    vocabulary. `term` is lowercased inside this function so callers never
    have to remember which side needs it.
    """
    form = term.strip().lower()
    if not form:
        return False
    base = re.escape(form).replace(r"\ ", r"[\s\-/]+")
    stem = base[:-1] if form.endswith("e") else base
    pattern = r"\b(?:" + base + r"(?:s|es)?|" + stem + r"(?:ing|ed|es))\b"
    return re.search(pattern, text_lower) is not None


def coverage(description: str, skills: list[str], resume_text: str = "") -> dict[str, Any]:
    """{"asked": [...], "covered": [...], "missing": [...], "pct": float}

    "asked" is the subset of the vocabulary this specific posting mentions -
    the coverage percentage is covered/asked, not covered/len(vocabulary), so
    a posting that never mentions a skill is not counted as a gap against it.
    With no resume text supplied, everything asked is reported as missing:
    there is nothing yet to evidence it against.
    """
    text_low = (description or "").lower()
    resume_low = (resume_text or "").lower()
    asked = [s for s in skills if s and present(s, text_low)]
    if not asked:
        return {"asked": [], "covered": [], "missing": [], "pct": None}
    covered = [s for s in asked if resume_low and present(s, resume_low)]
    missing = [s for s in asked if s not in covered]
    pct = round(100.0 * len(covered) / len(asked), 1)
    return {"asked": asked, "covered": covered, "missing": missing, "pct": pct}
