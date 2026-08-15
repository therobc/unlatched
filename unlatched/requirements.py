"""requirements.py - What a posting actually asks of a candidate, beyond the
title and the paycheck.

screen.py decides whether a posting matches a SEARCH (title, location,
salary, remote). This module answers a different question: even for a
posting that qualifies, what would screen the READER out - years of
experience, a degree level, a license they may not hold, a shift pattern, a
clearance. That information sits only in the description prose, so every
function here is a regex over text, in the same style as screen.py and
enrich.py: a fact plus the evidence string that produced it, never a bare
value with no way to check it.

NOT STATED VS NOT REQUIRED
---------------------------
A posting that never mentions education says nothing about education. A
posting that says "no degree required" says something specific and
different. Collapsing both into the same falsy value would make the second
case invisible, so every field distinguishes them: None/empty means the
posting never brought it up, while an explicit "not required" reading (only
education currently has one - EDUCATION_LEVELS includes "none" as a found
value, distinct from education_level itself being None) is returned as a
real value with its own evidence.

WHAT compare() CAN AND CANNOT JUDGE
------------------------------------
compare() only ever produces a verdict for a field the candidate profile
(config.py's "profile" block) can actually represent: years, education,
licenses, shift, travel, supervisory scope. Physical demands and clearance
are extracted and shown to the reader, but the profile has no field to hold
"can lift 50 lbs" or "holds a Secret clearance" against, so compare() never
touches them - inventing a verdict with nothing to compare against would be
exactly the guessed default this module exists to avoid.
"""
from __future__ import annotations

import re
from typing import Any, TypedDict

EDUCATION_LEVELS = ("none", "high_school", "associate", "bachelor", "master", "doctorate")
SHIFT_KINDS = ("nights", "weekends", "on_call", "rotating", "overtime")


class LicenseHit(TypedDict):
    name: str
    evidence: str


class ShiftHit(TypedDict):
    kind: str
    evidence: str


class PhysicalInfo(TypedDict):
    """`lifting` carries the matched phrase when the posting mentions
    lifting at all; `lifting_lbs` is only ever set when a weight was
    actually stated, not guessed from a generic "ability to lift" line.
    """

    lifting: str | None
    lifting_lbs: int | None
    lifting_evidence: str
    standing: bool
    standing_evidence: str
    climbing: bool
    climbing_evidence: str


class RequirementsInfo(TypedDict):
    """Every field is None/empty/False when the posting never raised the
    topic - see the module docstring for why that is not the same as a
    posting actively saying "not required".
    """

    years_required: int | None
    years_evidence: str
    education_level: str | None
    education_preferred: bool | None
    education_equivalent_ok: bool
    education_evidence: str
    licenses: list[LicenseHit]
    shift: list[ShiftHit]
    travel_pct: int | None
    travel_qualitative: str | None
    travel_evidence: str
    physical: PhysicalInfo
    supervises: bool | None
    supervises_evidence: str
    clearance: str | None
    clearance_evidence: str
    public_trust: str | None
    public_trust_evidence: str


class CompareResult(TypedDict):
    blockers: list[str]
    stretches: list[str]
    meets: list[str]


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


_CLAUSE_BREAK = re.compile(r"[\n;]")


def _line_window(text: str, start: int, end: int, pad: int = 60) -> str:
    """The text around [start, end), capped at `pad` chars each side and
    never crossing a line break or semicolon.

    Postings routinely bullet one requirement per line: a degree line,
    then a blank line, then a years-of-experience line. A flat character
    window ignores that structure and pulls words from the NEIGHBORING
    bullet into context checks - "in business" from a degree field
    ("Bachelor's degree in business...") bleeding backward into the very
    next bullet's years-of-experience figure is a real example that
    misread a stated experience requirement as small talk about the
    employer's history.

    The semicolon matters for the same reason within a single line: "...
    field required; Master's degree preferred" packs two distinct
    qualifier clauses about two different degree levels onto one line, and
    a newline-only boundary let the Bachelor's clause's "required" leak
    into the Master's clause's own check right next to it (and vice
    versa). Stopping at either character keeps a clause's context checks
    scoped to that clause.
    """
    # Both searches are bounded to the `pad` window, because the result is
    # clamped to it anyway - a break further away than `pad` could never
    # change the answer.
    #
    # This scanned from position 0 to `start` on EVERY call, and forward to
    # the end of the text on every call, to compute a window at most 60
    # characters wide. That made the whole extraction quadratic in the length
    # of the posting: one scan of the entire description per match, and a
    # long description has thousands of matches. MEASURED as a hang on a
    # 200KB description (red-team follow-up, 2026-08-08); the fetch cap allows
    # 2MB, so a hostile posting could stop a collection dead.
    #
    # Behaviour is unchanged. The old code took the last break in [0, start)
    # then raised it to `start - pad`; this takes the last break in
    # [start - pad, start) and defaults to the same floor.
    window_start = max(0, start - pad)
    left = window_start
    for m in _CLAUSE_BREAK.finditer(text, window_start, start):
        left = m.end()
    right_m = _CLAUSE_BREAK.search(text, end, end + pad)
    right = right_m.start() if right_m else min(len(text), end + pad)
    return text[left:right]


# ------------------------------------------------------------------ years ---

_NUMBER_WORDS: dict[str, int] = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
    "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19, "twenty": 20,
}
# Longest-first so "seven" cannot win the alternation before "seventeen" gets
# a chance to be tried (Python's re backtracks either way, but this also
# keeps the pattern legible about intent).
_NUMBER_WORD_ALT = "|".join(sorted(_NUMBER_WORDS, key=len, reverse=True))
_NUM = rf"(?:\d{{1,2}}|{_NUMBER_WORD_ALT})"

YEARS_RE = re.compile(
    rf"(?:(?:a\s+)?minimum(?:\s+of)?\s+|at\s+least\s+)?"
    # Structured postings often bullet a number in parens, sometimes right
    # after spelling it out too - "Minimum one (1) year experience". The
    # parens have to be optional around the digit, not just around the
    # whole phrase. \b sits AFTER the optional paren rather than at the
    # very start of the pattern: "(" is a non-word character, so a
    # leading \b (needing a word/non-word transition) refuses to match at
    # the "(" itself when it is preceded by whitespace - which silently
    # dropped the paren from the matched evidence and, worse, could skip
    # straight to an unrelated number word standing after it.
    rf"\(?\b({_NUM})\)?(?:\s*(?:\+|or\s+more))?"
    rf"(?:\s*(?:-|\u2013|to)\s*\(?\b({_NUM})\)?)?"
    rf"\s*(?:\+)?\s*years?\b", re.IGNORECASE)

# A number followed by "years" that is really about the EMPLOYER's age, not
# a requirement on the candidate ("in business for 25 years"). Filtered by
# scanning the window around each YEARS_RE match rather than folding into
# the main pattern, which would have to repeat every prefix/suffix twice.
YEARS_NEGATIVE = re.compile(
    r"in\s+business|years?\s+(?:of\s+)?operation|years?\s+running|"
    r"anniversary|founded|established\s+in|company[\u2019']?s?\s+\d", re.IGNORECASE)

# The EMPLOYER's own age, from the boilerplate opening paragraph. Found in
# live results: "For over 20 years, Smartsheet has empowered teams..." was
# read as a 20-year experience requirement, and it reaches compare(), so it
# can flag a row against a candidate who was never short of anything.
#
# Checked against the characters IMMEDIATELY around the match rather than the
# wide YEARS_NEGATIVE window, for the reason YEARS_AGE_TRAILING documents: a
# window reaching 40 characters either way swallows the NEXT, unrelated years
# figure. "At least 2 years experience. For over 20 years, Acme has led the
# market." lost its real 2-year requirement to the company's boast about
# itself, which is a worse error than the one being fixed.
COMPANY_AGE_BEFORE = re.compile(
    r"for\s+(?:over|more\s+than|nearly|almost)\s*$", re.IGNORECASE)
# ", Smartsheet has ..." - a subject that is not the reader. Case-SENSITIVE
# on the company name: a sentence continuing in lower case is prose about the
# role, not a company introducing itself.
COMPANY_AGE_AFTER = re.compile(r"^\s*,\s+(?:[A-Z][\w.&'-]*\s+){1,3}(?:has|have)\b")

# The CANDIDATE's own minimum age ("must be at least 21 years of age") - a
# driver/security/alcohol-adjacent posting staple that reads identically to
# an experience figure ("at least N years") right up to this trailing
# phrase. Checked immediately after the match, not over the same wide
# window as YEARS_NEGATIVE: "years of age" sits far enough into a sentence
# that a wide backward-reaching window swallowed the NEXT, unrelated years
# figure too ("... 21 years of age. 5+ years of experience preferred"
# wrongly discarded the "5+ years" candidate along with the age one).
YEARS_AGE_TRAILING = re.compile(r"^\s*(?:of\s+age\b|old\b)", re.IGNORECASE)

YEARS_POSITIVE_CONTEXT = re.compile(
    r"experience|exp\.|background|track record|working\s+(?:in|as|with)", re.IGNORECASE)


def _num(token: str) -> int:
    t = token.strip().lower()
    if t.isdigit():
        return int(t)
    return _NUMBER_WORDS[t]


def years_required(text: str) -> tuple[int | None, str]:
    """The minimum years of experience stated, if any. A range ("5-7
    years") reports the low end, since that is the floor a candidate
    actually has to clear. Candidates near words like "experience" win over
    candidates that are not, since a bare "5 years" with no such word
    nearby is as likely to be describing the company as the candidate.
    """
    candidates: list[tuple[bool, int, int, str]] = []
    for m in YEARS_RE.finditer(text):
        window = _line_window(text, m.start(), m.end(), pad=40)
        if YEARS_NEGATIVE.search(window):
            continue
        if YEARS_AGE_TRAILING.search(text[m.end(): m.end() + 12]):
            continue
        if (COMPANY_AGE_BEFORE.search(text[max(0, m.start() - 24): m.start()])
                and COMPANY_AGE_AFTER.search(text[m.end(): m.end() + 60])):
            continue
        value = _num(m.group(1))
        if not (0 < value <= 40):
            continue
        ctx_window = _line_window(text, m.start(), m.end(), pad=90)
        has_context = bool(YEARS_POSITIVE_CONTEXT.search(ctx_window))
        candidates.append((has_context, m.start(), value, _clean(m.group(0))))
    if not candidates:
        return None, ""
    candidates.sort(key=lambda c: (not c[0], c[1]))
    _, _, value, evidence = candidates[0]
    return value, evidence


# -------------------------------------------------------------- education ---

# Checked highest-degree-first so a posting that names several levels (a
# common "Bachelor's required, Master's preferred" pattern) reports the one
# that actually gates the role rather than whichever appears first in text.
_EDU_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("doctorate", re.compile(r"\b(ph\.?d\.?|doctorate|doctoral degree)\b", re.IGNORECASE)),
    ("master", re.compile(
        r"\b(master[\u2019']?s\s+degree|master[\u2019']?s\s+in\s+\w|mba)\b", re.IGNORECASE)),
    ("bachelor", re.compile(
        r"\b(bachelor[\u2019']?s\s+degree|bachelor[\u2019']?s\s+in\s+\w|undergraduate degree|"
        r"4[\s-]year degree)\b", re.IGNORECASE)),
    ("associate", re.compile(
        r"\b(associate[\u2019']?s?\s+degree|2[\s-]year degree)\b", re.IGNORECASE)),
    ("high_school", re.compile(
        r"\b(high school diploma|high school (?:or\s+)?equivalent|ged)\b", re.IGNORECASE)),
]

EDU_NONE = re.compile(
    r"\bno (?:degree|formal education|education)(?:\s+is)?\s+required\b|"
    r"\bno degree necessary\b|\beducation:?\s*not required\b", re.IGNORECASE)

EDU_PREFERRED = re.compile(
    r"\bpreferred\b|\bnice to have\b|\ba plus\b|\bis a plus\b|\bdesired\b|\bideally\b",
    re.IGNORECASE)
# The lookbehind keeps "preferred but not required" from reading as a
# required-signal - without it, "not required" would satisfy EDU_REQUIRED
# on the word "required" alone and cancel out the preferred match right
# next to it.
# "minimum" is a required-signal in prose ("minimum of a bachelor's degree")
# but NOT when it is simply the name of a field: "Minimum Education:
# Bachelor's degree preferred" is a PREFERENCE, and reading the label as a
# qualifier inverted it - reporting a hard blocker on a degree the employer
# only preferred, which is exactly the kind of thing that stops someone
# applying for a job they would have got. Section HEADINGS like "Minimum
# Qualifications" still register as required via EDU_SECTION_REQUIRED.
# "or equivalent experience" / "or an equivalent combination of education
# and experience" is the employer explicitly saying the diploma is not a
# wall. Read as a hard requirement it hides the job from precisely the
# candidate the sentence was written to include: someone with the years and
# no matching degree.
EDU_EQUIVALENT = re.compile(
    r"\bor\s+(?:an?\s+)?equivalent\b|\bequivalent\s+(?:work\s+)?experience\b|"
    r"\bequivalent\s+combination\b|\bor\s+comparable\s+experience\b|"
    r"\bin\s+lieu\s+of\s+(?:a\s+)?degree\b",
    re.IGNORECASE)

EDU_REQUIRED = re.compile(
    r"(?<!not )(?<!not  )required\b|\bmust have\b|\bmandatory\b|"
    r"\bminimum\b(?!\s*(?:education|qualification|requirement|degree)s?\b\s*:?)",
    re.IGNORECASE)

# A bulleted degree line often carries no softening language of its own -
# the qualifier sits several lines up, on a section heading that governs
# every bullet under it ("Preferred Qualifications:\n- Bachelor's degree in
# business...\n- 5+ years..."). Used only when the bullet's own line is
# silent on the point.
EDU_SECTION_PREFERRED = re.compile(
    r"\bpreferred qualifications\b|\bnice[- ]to[- ]have\b|\bdesired qualifications\b",
    re.IGNORECASE)
EDU_SECTION_REQUIRED = re.compile(
    r"\brequired qualifications\b|\bminimum qualifications\b|\bbasic qualifications\b",
    re.IGNORECASE)


def _nearest_before(text: str, pos: int, pattern: re.Pattern[str]) -> int:
    """Start offset of the LAST match of `pattern` before `pos`, or -1."""
    last = -1
    for m in pattern.finditer(text, 0, pos):
        last = m.start()
    return last


def _education_signal(text: str, m: re.Match[str]) -> tuple[bool, bool]:
    """(local_preferred, local_required) for one degree match, read from
    its own clause only - see `_line_window`. A degree clause routinely
    names two or three acceptable fields of study before its qualifier
    ("...in Information Security, Computer Science, or related field
    required"), which runs well past `_line_window`'s 60-char default long
    before reaching either boundary character - the wider pad here is safe
    specifically because the semicolon/newline boundary, not the pad, is
    what stops it from crossing into a neighboring clause.
    """
    window = _line_window(text, m.start(), m.end(), pad=120)
    preferred = EDU_PREFERRED.search(window)
    required = EDU_REQUIRED.search(window)
    if preferred and required:
        # BOTH fired. A full stop is not one of `_line_window`'s boundaries -
        # and cannot be, because a naive period split would cut "B.S." and
        # "Ph.D." in half - so "Bachelor's degree preferred. 3 years of
        # experience required." arrives here as ONE window, and taking
        # `required` at face value told a candidate a degree was mandatory
        # when the posting had said the opposite about it.
        #
        # Decided by PROXIMITY to the degree itself instead. The qualifier
        # attached to a degree sits beside it; the one belonging to a
        # different demand in the same sentence sits further away. This needs
        # no sentence boundary and so cannot be defeated by an abbreviation.
        degree_at = m.start() - max(0, m.start() - 120)
        if abs(preferred.start() - degree_at) <= abs(required.start() - degree_at):
            return True, False
        return False, True
    return bool(preferred), bool(required)


def _education_accepts_equivalent(text: str) -> bool:
    """Does the degree clause itself offer an experience alternative?

    Scoped to the clause the degree sits in, for the same reason
    `_education_signal` is: an "equivalent experience" phrase belonging to a
    DIFFERENT bullet says nothing about this degree.
    """
    for _level, pattern in _EDU_PATTERNS:
        m = pattern.search(text)
        if m and EDU_EQUIVALENT.search(_line_window(text, m.start(), m.end(), pad=120)):
            return True
    return False


def education_required(text: str) -> tuple[str | None, bool | None, str]:
    """(level, preferred, evidence). `preferred` is only ever True/False
    once a level is found - it is never guessed when nothing says either
    way, in which case it defaults to False (required): a degree listed
    under a Requirements heading with no softening language is exactly
    what "required" reads as in job-posting prose.

    A posting that names TWO levels on one line ("Bachelor's degree
    required; Master's degree preferred") is common, and the level that
    actually gates the role is the lower, REQUIRED one - reporting the
    higher "preferred" level instead (which highest-first matching alone
    would do, since it is checked before Bachelor's) told a candidate they
    needed a Master's when the posting's own floor was a Bachelor's. Every
    level mentioned is checked for its own local required/required-implied
    signal first; only when none of them settles it does the single
    highest-level-found fallback below apply.
    """
    found: list[tuple[str, re.Match[str], bool, bool]] = []
    for level, pattern in _EDU_PATTERNS:
        m = pattern.search(text)
        if m:
            local_preferred, local_required = _education_signal(text, m)
            found.append((level, m, local_preferred, local_required))

    if len(found) > 1:
        required_only = [f for f in found if f[3] and not f[2]]
        if required_only:
            # EDUCATION_LEVELS is lowest-to-highest; _EDU_PATTERNS above is
            # highest-to-lowest, so the LAST required match here is the
            # lowest (most permissive) genuine floor.
            level, m, _pref, _req = required_only[-1]
            return level, False, _clean(m.group(0))

    for level, m, local_preferred, local_required in found:
        if local_preferred or local_required:
            preferred = local_preferred and not local_required
        else:
            pref_at = _nearest_before(text, m.start(), EDU_SECTION_PREFERRED)
            req_at = _nearest_before(text, m.start(), EDU_SECTION_REQUIRED)
            preferred = pref_at > req_at
        return level, preferred, _clean(m.group(0))

    m = EDU_NONE.search(text)
    if m:
        return "none", False, _clean(m.group(0))
    return None, None, ""


# --------------------------------------------------------------- licenses ---

_LICENSE_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("Driver's License", re.compile(
        r"\b(?:valid\s+)?driver[\u2019']?s?\s+licen[cs]e\b", re.IGNORECASE)),
    ("CDL", re.compile(
        r"\bcdl\b|\bcommercial driver[\u2019']?s\s+licen[cs]e\b", re.IGNORECASE)),
    ("RN", re.compile(
        r"\bregistered nurse\b|\bactive rn\b|\bcurrent rn\b|\brn\s+licen[cs]e\b|"
        r"\brn\b(?=\s*,?\s*(?:license|licensure|certification))", re.IGNORECASE)),
    ("LPN", re.compile(r"\blpn\b|\blicensed practical nurse\b", re.IGNORECASE)),
    ("BLS", re.compile(r"\bbls\b|\bbasic life support\b", re.IGNORECASE)),
    ("ACLS", re.compile(
        r"\bacls\b|\badvanced cardi(?:ac|ovascular) life support\b", re.IGNORECASE)),
    ("CPR", re.compile(r"\bcpr\b|\bcardiopulmonary resuscitation\b", re.IGNORECASE)),
    ("OSHA 10", re.compile(r"\bosha[\s-]?10\b", re.IGNORECASE)),
    ("OSHA 30", re.compile(r"\bosha[\s-]?30\b", re.IGNORECASE)),
    ("Forklift", re.compile(
        r"\bforklift\s+(?:certif\w*|licens\w*|operator certification)\b", re.IGNORECASE)),
    ("PMP", re.compile(r"\bpmp\b|\bproject management professional\b", re.IGNORECASE)),
    ("SHRM-CP", re.compile(r"\bshrm[\s-]cp\b", re.IGNORECASE)),
    ("SHRM-SCP", re.compile(r"\bshrm[\s-]scp\b", re.IGNORECASE)),
    ("PHR", re.compile(r"\bphr\b|\bprofessional in human resources\b", re.IGNORECASE)),
    ("CPA", re.compile(r"\bcpa\b|\bcertified public accountant\b", re.IGNORECASE)),
    ("Security+", re.compile(r"\bsecurity\+|\bcomptia security\+", re.IGNORECASE)),
    ("Journeyman", re.compile(r"\bjourneyman\b", re.IGNORECASE)),
    ("Apprentice", re.compile(r"\bapprentice(?:ship)?\b", re.IGNORECASE)),
    ("EPA 608", re.compile(r"\bepa\s*(?:section\s*)?608\b", re.IGNORECASE)),
    ("Notary", re.compile(r"\bnotary(?:\s+public)?\b", re.IGNORECASE)),
]


def licenses(text: str) -> list[LicenseHit]:
    hits: list[LicenseHit] = []
    for name, pattern in _LICENSE_PATTERNS:
        m = pattern.search(text)
        if m:
            hits.append({"name": name, "evidence": _clean(m.group(0))})
    return hits


# ------------------------------------------------------------------ shift ---

# Every pattern requires language that reads as an EXPECTATION placed on the
# candidate ("required", "must", a named recurring pattern), not a passing
# mention - "we occasionally work late" is not the same claim as "overtime
# required".
_SHIFT_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("nights", re.compile(
        r"\b(night shift|overnight shift|graveyard shift|3rd shift|third shift|"
        r"must (?:be able to )?work nights|available (?:for |to work )?nights)\b",
        re.IGNORECASE)),
    ("weekends", re.compile(
        r"\b(weekends? required|must be available[^.\n]{0,20}weekends|"
        r"must work[^.\n]{0,20}weekends|working weekends|"
        r"weekend availability required|weekend shift)\b",
        re.IGNORECASE)),
    ("on_call", re.compile(
        r"\bon[\s-]?call\b(?:\s+(?:rotation|schedule|required))?", re.IGNORECASE)),
    ("rotating", re.compile(r"\brotating\s+(?:shifts?|schedule)\b", re.IGNORECASE)),
    ("overtime", re.compile(
        r"\bmandatory overtime\b|\bovertime (?:required|as needed|may be required|"
        r"is required)\b|\brequired to work overtime\b", re.IGNORECASE)),
]


def shift(text: str) -> list[ShiftHit]:
    hits: list[ShiftHit] = []
    for kind, pattern in _SHIFT_PATTERNS:
        m = pattern.search(text)
        if m:
            hits.append({"kind": kind, "evidence": _clean(m.group(0))})
    return hits


# ----------------------------------------------------------------- travel ---

TRAVEL_PCT = re.compile(
    r"(?:up to\s*)?(\d{1,3})\s*%[^.\n]{0,25}?travel|"
    r"travel[^.\n]{0,25}?(?:up to\s*)?(\d{1,3})\s*%", re.IGNORECASE)
TRAVEL_QUALITATIVE = re.compile(
    r"\b(frequent travel|extensive travel|occasional travel|minimal travel|"
    r"significant travel|some travel (?:is )?required|travel(?:ing)? (?:is )?required|"
    r"willing(?:ness)? to travel|ability to travel)\b", re.IGNORECASE)


def travel(text: str) -> tuple[int | None, str | None, str]:
    m = TRAVEL_PCT.search(text)
    if m:
        pct = int(m.group(1) or m.group(2))
        return pct, None, _clean(m.group(0))
    m2 = TRAVEL_QUALITATIVE.search(text)
    if m2:
        evidence = _clean(m2.group(0))
        return None, evidence, evidence
    return None, None, ""


# --------------------------------------------------------------- physical ---

LIFTING = re.compile(
    r"\b(?:lift(?:ing)?|carry(?:ing)?)\s+(?:up to\s+|at least\s+)?"
    r"(\d{1,3})\s*(?:lbs?\.?|pounds?)\b", re.IGNORECASE)
LIFTING_GENERIC = re.compile(
    r"\b(?:ability to lift|must be able to lift|frequent lifting|heavy lifting|"
    r"repetitive lifting)\b", re.IGNORECASE)
STANDING = re.compile(
    r"\b(stand(?:ing)?(?:\s+(?:or|and)\s+walk\w*)?\s+for\s+"
    r"(?:extended|long|prolonged)\s+periods|prolonged standing|"
    r"extended periods of standing)\b", re.IGNORECASE)
CLIMBING = re.compile(r"\b(climb(?:ing)?\s+(?:stairs|ladders)|ability to climb)\b",
                       re.IGNORECASE)


def physical(text: str) -> PhysicalInfo:
    lifting_desc: str | None = None
    lifting_lbs: int | None = None
    lifting_evidence = ""
    m = LIFTING.search(text)
    if m:
        lifting_lbs = int(m.group(1))
        lifting_evidence = _clean(m.group(0))
        lifting_desc = lifting_evidence
    else:
        m = LIFTING_GENERIC.search(text)
        if m:
            lifting_evidence = _clean(m.group(0))
            lifting_desc = lifting_evidence
    standing_m = STANDING.search(text)
    climbing_m = CLIMBING.search(text)
    return {
        "lifting": lifting_desc,
        "lifting_lbs": lifting_lbs,
        "lifting_evidence": lifting_evidence,
        "standing": bool(standing_m),
        "standing_evidence": _clean(standing_m.group(0)) if standing_m else "",
        "climbing": bool(climbing_m),
        "climbing_evidence": _clean(climbing_m.group(0)) if climbing_m else "",
    }


# -------------------------------------------------------------- supervise ---

SUPERVISES = re.compile(
    r"\bsupervis\w*\s+(?:staff|employees|team|others|a team)\b|"
    r"\bmanag\w*\s+(?:a\s+)?team\s+of\b|"
    r"\b\d+\s*\+?\s*direct[\s-]reports?\b|"
    r"\bpeople[\s-]manager\b|"
    r"\bsupervisory (?:role|responsibilit\w*|experience)\b|"
    r"\boversee(?:s|ing)?\s+(?:a\s+)?(?:team|staff|department)\b|"
    r"\blead(?:s|ing)?\s+a\s+team\s+of\b", re.IGNORECASE)

# Structured postings carry a "Supervisory Responsibilities:" heading for
# EVERY posting, answered with anything from a bare "N/A" to a full
# sentence ("This role is an individual contributor with no direct reports
# or supervisory authority") for individual-contributor roles - the
# heading alone is not evidence the role supervises anyone, so the
# paragraph that follows it has to be checked for a negation before the
# match is trusted.
SUPERVISES_NEGATIVE = re.compile(
    r"\bn/?a\b|\bnone\b|\bnot applicable\b|\bindividual contributor\b|"
    r"\bno direct reports\b|\bno supervisory\b|\bnot a supervisory\b|"
    r"\bnon-supervisory\b|\bwithout supervisory\b", re.IGNORECASE)


def _forward_paragraph(text: str, end: int, max_pad: int = 220) -> str:
    """Text from `end` up to the next blank line (a section break in every
    posting format seen so far) or `max_pad` chars, whichever comes first.
    """
    span = text[end: end + max_pad + 50]
    blank = re.search(r"\n\s*\n", span)
    right = blank.start() if blank else max_pad
    return span[:right]


def supervises_required(text: str) -> tuple[bool | None, str]:
    """True when the posting states supervisory scope; None (never False)
    when it does not - a posting silent on the point has not said the role
    is an individual contributor, it has simply not said anything.
    """
    for m in SUPERVISES.finditer(text):
        if SUPERVISES_NEGATIVE.search(_forward_paragraph(text, m.end())):
            continue
        return True, _clean(m.group(0))
    return None, ""


# -------------------------------------------------------------- clearance ---

CLEARANCE = re.compile(
    r"\b(top secret(?:/sci| sci)?\s*(?:clearance)?|ts/sci\s*(?:clearance)?|"
    r"secret clearance|security clearance|"
    # Public trust is a suitability designation rather than a clearance, so
    # it is worded without the word "clearance" far more often than not
    # ("Moderate Risk Public Trust", "Public Trust - Background
    # Investigation"). Matching only "public trust clearance" missed the
    # common forms entirely.
    r"public trust(?:\s*(?:clearance|position))?|"
    # DOE's own ladder, which is what Oak Ridge and Y-12 post against - a
    # federal search run from Knoxville hits these constantly and they look
    # nothing like the DoD wording.
    # Agencies sometimes paste an en dash where a hyphen belongs
    # ("Q - Nonsensitive"), so accept either. Written as an escape so this
    # file stays ASCII and the character cannot be mistaken on sight.
    "[ql]\\s*[-\\u2013]?\\s*(?:nonsensitive|sensitive)|[ql] clearance|"
    r"\bsci\b|dod (?:security )?clearance|"
    # The federal sensitivity ladder's own names. Spelled out rather than
    # matched as "*-sensitive" because "Non-sensitive" sits in the same
    # field and means the OPPOSITE - a suffix pattern would invert it.
    r"(?:noncritical|critical|special)[- ]sensitive|"
    r"active (?:secret|top secret) clearance|"
    r"national security position|sensitive position|"
    r"clearance eligib\w*)\b", re.IGNORECASE)

# "Security Clearance: Not Required" is the single most common clearance
# STATEMENT in the federal corpus, and reading it as "requires a clearance"
# would invert the filter - throwing away exactly the postings a
# clearance-free candidate wants. Checked in a window around the match, not
# over the whole posting, so an unrelated "not required" elsewhere in a long
# description cannot cancel a real requirement.
NO_CLEARANCE = re.compile(
    r"(not required|no clearance|none required|\bnot applicable\b|\bn/?a\b)",
    re.IGNORECASE)
CLEARANCE_NEGATION_WINDOW = 40

# "Security Clearance: Secret" matches on the label, so the evidence would
# read "Security Clearance" and tell the reader nothing they did not already
# know. When the match is a label followed by its value, the value is what
# belongs in the evidence.
LABELLED_VALUE = re.compile(r"\s*:\s*([A-Za-z][\w /()-]{2,44})")


def _with_value(text: str, end: int, matched: str) -> str:
    m = LABELLED_VALUE.match(text, end)
    return f"{matched}: {_clean(m.group(1))}" if m else matched


# Public trust is a SUITABILITY tier, not a clearance, and the federal
# vocabulary for it is risk-based: "Moderate Risk", "High Risk". The word
# "Sensitive" is what separates the national-security ladder from the public
# trust one - "Noncritical-Sensitive (NCS)/Moderate Risk" is a clearance
# position that merely shares the words "Moderate Risk", so this must not
# fire on it. Measured against the live vocabulary, which is why the
# alternatives are anchored the way they are.
PUBLIC_TRUST = re.compile(
    r"\b(public trust|moderate risk(?:\s*\(mr\))?|high risk(?:\s*\(hr\))?)\b",
    re.IGNORECASE)
SENSITIVE_TIER = re.compile(r"\bsensitive\b", re.IGNORECASE)
PUBLIC_TRUST_CONTEXT = 60


def clearance(text: str) -> tuple[str | None, str]:
    """Returns (requirement, evidence). None means this posting states no
    clearance requirement - which is NOT the same as it stating one and the
    candidate lacking it; that judgement belongs to compare().
    """
    for m in CLEARANCE.finditer(text):
        window = text[m.end():m.end() + CLEARANCE_NEGATION_WINDOW]
        if NO_CLEARANCE.search(window):
            continue
        evidence = _with_value(text, m.end(), _clean(m.group(0)))
        return evidence, evidence
    return None, ""


def public_trust(text: str) -> tuple[str | None, str]:
    """Public trust / risk-tier vetting, reported separately from clearance
    because they are different systems - a candidate can be barred from one
    and fine with the other, and someone screening for "no vetting at all"
    needs both.
    """
    for m in PUBLIC_TRUST.finditer(text):
        start = max(0, m.start() - PUBLIC_TRUST_CONTEXT)
        around = text[start:m.end() + CLEARANCE_NEGATION_WINDOW]
        # "Noncritical-Sensitive (NCS)/Moderate Risk" is a national-security
        # position; clearance() owns it, and reporting it here as well would
        # double-count one requirement as two.
        if SENSITIVE_TIER.search(around):
            continue
        if NO_CLEARANCE.search(text[m.end():m.end() + CLEARANCE_NEGATION_WINDOW]):
            continue
        evidence = _clean(m.group(0))
        return evidence, evidence
    return None, ""


# ---------------------------------------------------------------- extract ---

def extract(description: str) -> RequirementsInfo:
    """Everything this module can find in one posting's description."""
    text = description or ""
    years_val, years_ev = years_required(text)
    edu_level, edu_preferred, edu_ev = education_required(text)
    edu_equivalent = bool(edu_level) and _education_accepts_equivalent(text)
    pct, qualitative, travel_ev = travel(text)
    supervises_val, supervises_ev = supervises_required(text)
    clearance_val, clearance_ev = clearance(text)
    trust_val, trust_ev = public_trust(text)
    return {
        "years_required": years_val,
        "years_evidence": years_ev,
        "education_level": edu_level,
        "education_preferred": edu_preferred,
        "education_equivalent_ok": edu_equivalent,
        "education_evidence": edu_ev,
        "licenses": licenses(text),
        "shift": shift(text),
        "travel_pct": pct,
        "travel_qualitative": qualitative,
        "travel_evidence": travel_ev,
        "physical": physical(text),
        "supervises": supervises_val,
        "supervises_evidence": supervises_ev,
        "clearance": clearance_val,
        "clearance_evidence": clearance_ev,
        "public_trust": trust_val,
        "public_trust_evidence": trust_ev,
    }


def summary(reqs: RequirementsInfo) -> str:
    """The demands of a posting compressed to a few words for a table row.

    "5+ yrs, BS, CDL" tells a reader whether to open a posting at all, which
    is the whole point - the alternative is opening every row to find the one
    line that rules them out. Everything here is already extracted; this only
    chooses what fits in a column.

    Order is by how often a requirement is the thing that disqualifies
    someone: years first, then education, then licenses, then the practical
    constraints. A requirement the posting never states produces nothing -
    silence is not "none required", the same rule the rest of this module
    follows.
    """
    parts: list[str] = []
    years = reqs.get("years_required")
    if years is not None:
        parts.append(f"{years}+ yrs")

    level = reqs.get("education_level")
    if level:
        # The extractor names levels with underscores ("high_school"), which
        # leaked into the column as a raw enum. Normalised on the way in so a
        # new level added upstream reads as words rather than code.
        key = str(level).lower().replace("_", " ")
        label = _EDUCATION_SHORT.get(key, key)
        if reqs.get("education_preferred"):
            label += " pref"
        elif reqs.get("education_equivalent_ok"):
            # A degree "or equivalent experience" is not a wall, and showing
            # it as a bare "BS" would read as one.
            label += " or exp"
        parts.append(label)

    # licenses() and shift() return dicts ({"name", "evidence"} and
    # {"kind", "evidence"}), not strings - rendering them whole put a Python
    # repr in the column. Capped at two: a row has finite width, and years is
    # a more common disqualifier than a fourth certification.
    parts.extend(str(item.get("name", "")) for item in (reqs.get("licenses") or [])[:2])

    if reqs.get("clearance"):
        parts.append("clearance")
    elif reqs.get("public_trust"):
        parts.append("public trust")

    travel = reqs.get("travel_pct")
    if travel is not None:
        parts.append(f"travel {travel}%")
    elif reqs.get("travel_qualitative"):
        parts.append("travel")

    if reqs.get("supervises"):
        parts.append("supervises")
    shifts = reqs.get("shift") or []
    if shifts:
        parts.append(str(shifts[0].get("kind", "")).replace("_", " "))

    return ", ".join(part for part in parts if part)


# Degree levels shortened to what fits a narrow column. Anything not listed
# is shown as extracted rather than guessed at.
_EDUCATION_SHORT = {
    "high school": "HS",
    "associate": "AA",
    "bachelor": "BS",
    "bachelors": "BS",
    "master": "MS",
    "masters": "MS",
    "doctorate": "PhD",
    "phd": "PhD",
}


# ---------------------------------------------------------------- compare ---

def profile_is_configured(profile: dict[str, Any]) -> bool:
    """False for the untouched config.py default, where every field is
    null/empty and compare() would have nothing to say about any of them.
    """
    p = profile or {}
    return bool(
        p.get("years_experience") is not None
        or p.get("education") is not None
        or p.get("licenses")
        or p.get("can_travel") is not None
        or p.get("willing_shifts")
        or p.get("supervises_ok") is not None)


def _known_list(value: Any) -> list[str] | None:
    """An empty list from config.py's "profile" block cannot be told apart
    from a field the user never touched - both default to []. Treating an
    empty list as "unknown" (rather than "confirmed holds none") is what
    keeps a freshly-initialised profile from generating a license blocker
    on every posting before the user has entered a single fact about
    themselves.
    """
    return list(value) if value else None


def compare(reqs: RequirementsInfo, profile: dict[str, Any]) -> CompareResult:
    """Judge extracted requirements against one candidate profile.

    A BLOCKER is a plain factual mismatch the posting states as mandatory:
    a license not on the profile, a degree level stated as required and
    above the profile's, travel or supervision the profile marks as
    unworkable. A STRETCH is softer: years somewhat short, a degree stated
    as merely preferred, or a shift pattern the profile has not listed as
    accepted. Nothing is ever produced for a field either side leaves
    unknown - see `_known_list` and the None checks below.
    """
    blockers: list[str] = []
    stretches: list[str] = []
    meets: list[str] = []

    years_req = reqs["years_required"]
    years_have = profile.get("years_experience")
    if years_req is not None and years_have is not None:
        if years_have >= years_req:
            meets.append(
                f"meets the {years_req}-year experience requirement "
                f'("{reqs["years_evidence"]}")')
        else:
            gap = years_req - years_have
            stretches.append(
                f"asks for {years_req} years of experience, {gap} more than the "
                f'{years_have} on the profile ("{reqs["years_evidence"]}")')

    level = reqs["education_level"]
    have_level = profile.get("education")
    if (level is not None and have_level is not None
            and level in EDUCATION_LEVELS and have_level in EDUCATION_LEVELS):
        req_rank = EDUCATION_LEVELS.index(level)
        have_rank = EDUCATION_LEVELS.index(have_level)
        if have_rank >= req_rank:
            meets.append(
                f'meets the {level} education requirement ("{reqs["education_evidence"]}")')
        elif reqs["education_preferred"]:
            stretches.append(
                f"lists a {level} degree as preferred, not required, above the "
                f'profile\'s {have_level} ("{reqs["education_evidence"]}")')
        elif reqs["education_equivalent_ok"]:
            # "Bachelor's degree or equivalent experience" is not a wall. The
            # employer has said so themselves, and treating it as one hides
            # jobs from exactly the candidate it was written to include -
            # someone with years of the work and no matching diploma.
            stretches.append(
                f"asks for a {level} degree but accepts equivalent experience "
                f'("{reqs["education_evidence"]}")')
        else:
            blockers.append(
                f"requires a {level} degree, above the profile's {have_level} "
                f'("{reqs["education_evidence"]}")')

    have_licenses = _known_list(profile.get("licenses"))
    if have_licenses is not None:
        held = {lic.lower() for lic in have_licenses}
        for lic_hit in reqs["licenses"]:
            if lic_hit["name"].lower() in held:
                meets.append(
                    f'holds the required {lic_hit["name"]} ("{lic_hit["evidence"]}")')
            else:
                blockers.append(
                    f'requires {lic_hit["name"]}, which the profile does not list '
                    f'("{lic_hit["evidence"]}")')

    have_shifts = _known_list(profile.get("willing_shifts"))
    if have_shifts is not None:
        accepted = {s.lower() for s in have_shifts}
        for shift_hit in reqs["shift"]:
            if shift_hit["kind"].lower() in accepted:
                meets.append(
                    f'the profile accepts {shift_hit["kind"]} work ("{shift_hit["evidence"]}")')
            else:
                stretches.append(
                    f'expects {shift_hit["kind"]} work, which the profile does not list as '
                    f'accepted ("{shift_hit["evidence"]}")')

    can_travel = profile.get("can_travel")
    travel_stated = reqs["travel_pct"] is not None or reqs["travel_qualitative"] is not None
    if travel_stated and can_travel is not None:
        if can_travel:
            meets.append(
                f'the profile can travel, matching this posting\'s travel expectation '
                f'("{reqs["travel_evidence"]}")')
        else:
            blockers.append(
                f'expects travel, which the profile marks as not workable '
                f'("{reqs["travel_evidence"]}")')

    supervises_ok = profile.get("supervises_ok")
    if reqs["supervises"] and supervises_ok is not None:
        if supervises_ok:
            meets.append(
                f'the profile is open to supervising others, matching this role '
                f'("{reqs["supervises_evidence"]}")')
        else:
            blockers.append(
                f'this role supervises others, which the profile marks as not wanted '
                f'("{reqs["supervises_evidence"]}")')

    return {"blockers": blockers, "stretches": stretches, "meets": meets}


def summarize(reqs: RequirementsInfo) -> str:
    """One compact line for a listing view - `unlatched show` uses this so
    a full requirements breakdown does not have to be printed just to tell
    a reader whether there is anything worth a closer look.
    """
    parts: list[str] = []
    if reqs["years_required"] is not None:
        parts.append(f"{reqs['years_required']}+ yrs")
    if reqs["education_level"]:
        tag = "preferred" if reqs["education_preferred"] else "required"
        parts.append(f"{reqs['education_level']} ({tag})")
    if reqs["licenses"]:
        parts.append("licenses: " + ", ".join(h["name"] for h in reqs["licenses"]))
    if reqs["shift"]:
        parts.append("shift: " + ", ".join(h["kind"] for h in reqs["shift"]))
    if reqs["travel_pct"] is not None:
        parts.append(f"travel {reqs['travel_pct']}%")
    elif reqs["travel_qualitative"]:
        parts.append(f"travel: {reqs['travel_qualitative']}")
    if reqs["supervises"]:
        parts.append("supervises")
    if reqs["clearance"]:
        parts.append(f"clearance: {reqs['clearance']}")
    return "; ".join(parts) if parts else "no requirements detected"
