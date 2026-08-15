"""screen.py - The one deterministic relevance screen every posting passes
through, regardless of which source found it.

No model anywhere in this module. Every decision is a regex match, a
substring test or an integer comparison against `config.json` - the same
input always produces the same verdict, which is the whole reason screening
stays out of the agent's hands (constraint 3 in the project spec).

Order of checks, cheapest first: title include/exclude, seniority, then the
checks that need the full description - remote evidence, salary.
"""
from __future__ import annotations

import re
from functools import lru_cache
from typing import Any

from . import country as country_mod
from . import coverage as coverage_mod
from . import employment as employment_mod
from . import enrich
from . import location as location_mod
from . import requirements as requirements_mod

# No real job description approaches this length. A page that does is not a
# posting - it is a whole careers site, a search-result dump or an error
# page - and screening it costs unbounded CPU for a result that means
# nothing. Truncating is safe: whatever decides a verdict appears far
# earlier than this in any real posting.
MAX_JD_CHARS = 120_000

# Below this there is not enough text to judge a posting fairly, and a short
# description scoring badly means we failed to READ it, not that the job is
# wrong. Such a row is flagged for a person rather than discarded - dropping
# on a failure to fetch would quietly throw away real jobs.
MIN_JD_CHARS_TO_JUDGE = 200


def clamp(text: str) -> str:
    t = text or ""
    return t[:MAX_JD_CHARS] if len(t) > MAX_JD_CHARS else t


# Positive evidence a posting is remote. A posting that simply
# never mentions location was being treated as remote, which is silence
# being read as a yes. Silence is not evidence, so the gate below only
# passes on an actual match against one of these patterns - the caller
# always gets a reason string back, not just true/false, so a human can
# see what convinced it.
REMOTE_LOCATION = re.compile(
    r"\bremote\b|\btelecommute\b|\bwork from home\b|\banywhere\b", re.IGNORECASE)
REMOTE_DECLARED = re.compile(
    r"\b(fully remote|100%\s*remote|remote position|remote role|remote work|"
    r"remote opportunity|work from home|telecommut\w*|telework|home[- ]based|"
    r"virtual position|remote[- ]first)\b|"
    r"\bremote\b\s*[-\u2013(]|[-\u2013(]\s*remote\b", re.IGNORECASE)
ONSITE_DECLARED = re.compile(
    r"\b(on[- ]?site (?:daily|position|role|required)|must be on[- ]?site|"
    r"in[- ]office (?:daily|required)|hybrid (?:role|position|schedule)|"
    r"\d\s*days? (?:per week )?in (?:the )?office|report to the office|"
    r"onsite presence required)\b", re.IGNORECASE)


# A hybrid posting is neither remote nor onsite, and a person choosing
# between the three should not have hybrid roles arrive labelled as whichever
# word the posting happened to use first.
#
# Every branch requires "hybrid" NEXT TO a word about working, because
# "hybrid" on its own is a technical term: "hybrid cloud", "hybrid vehicles"
# and "hybrid ERP environment" all appear in job descriptions and none of
# them says anything about where the person sits.
HYBRID_DECLARED = re.compile(
    r"\bhybrid\s+(?:role|position|job|opportunity|schedule|model|"
    r"arrangement|setup|work(?:ing)?(?:\s+model|\s+week|\s+schedule)?|"
    r"remote|on[- ]?site|in[- ]office)\b|"
    r"\b(?:work|schedule|position|role|arrangement)\s+is\s+hybrid\b|"
    r"\bhybrid\s*[-:(]\s*\d\s*days?\b|"
    r"\b\d\s*days?\s*(?:per week\s*)?(?:in|at)\s*(?:the\s*)?office\b|"
    r"\b\d\s*days?\s*(?:per week\s*)?on[- ]?site\b", re.IGNORECASE)

WORK_MODES = ("remote", "hybrid", "onsite")


def work_mode(title: str, location: str, description: str) -> tuple[str, str]:
    """Remote, hybrid or onsite, with the text that decided it.

    Hybrid is tested FIRST because a hybrid posting almost always says
    "remote" somewhere too - that is what hybrid means - and reading it as
    remote is how a person who cannot commute ends up applying for a job
    that expects them in the office three days a week.
    """
    head = clamp(description or "")[:6000]
    for field, text in (("location", location or ""), ("title", title or ""),
                        ("description", head)):
        m = HYBRID_DECLARED.search(text)
        if m:
            snippet = re.sub(r"\s+", " ", m.group(0)).strip()
            return "hybrid", f"{field}: {snippet}"

    is_remote, evidence = remote_evidence(title, location, description)
    if is_remote:
        # Explicit onsite language outranks remote wording: "Remote - must be
        # on-site daily" is an onsite job with a careless location field.
        m = ONSITE_DECLARED.search(head)
        if m:
            return "onsite", f"description: {m.group(0)}"
        return "remote", evidence

    m = ONSITE_DECLARED.search(head)
    if m:
        return "onsite", f"description: {m.group(0)}"
    # The default. Most onsite postings never say so, because to the employer
    # writing it that is simply what a job is.
    return "onsite", "no remote or hybrid wording"


def wanted_modes(search: dict[str, Any]) -> list[str]:
    """Which ways of working the search accepts, from either setting.

    `work_modes` (Decided 2026-08-05: three tick boxes) is the answer when it is
    set. `remote_scope` is what the app asked before that, and is still read
    when work_modes is empty so an older config keeps the search it had.
    An empty result means no restriction.
    """
    modes = [str(m).strip().lower() for m in (search.get("work_modes") or [])]
    modes = [m for m in modes if m in WORK_MODES]
    if modes:
        return modes
    if (search.get("remote_scope") or "any") == "remote_only":
        return ["remote"]
    return []


def prefers_remote(search: dict[str, Any]) -> bool:
    """Should a remote posting rank above an equivalent onsite one?

    Only when the search leans that way: remote ticked and onsite not, or the
    older remote_scope saying so. Rewarding remote for everybody pushed
    remote listings up the ranking of a carpenter who cannot work remotely
    at all.
    """
    wanted = wanted_modes(search)
    if wanted:
        return "remote" in wanted and "onsite" not in wanted
    return (search.get("remote_scope") or "any") in ("prefer_remote", "remote_only")


def remote_evidence(title: str, location: str, description: str) -> tuple[bool, str]:
    """Does the POSTING say it is remote? Returns (bool, evidence). Silence
    is not evidence - every True carries the text that convinced it.
    """
    m = REMOTE_LOCATION.search(location or "")
    if m:
        return True, f"location: {m.group(0)}"
    m = REMOTE_DECLARED.search(title or "")
    if m:
        return True, f"title: {m.group(0)}"
    m = REMOTE_DECLARED.search((description or "")[:6000])
    if m:
        snippet = re.sub(r"\s+", " ", m.group(0)).strip()
        return True, f"description: {snippet}"
    return False, ""


# A dollar amount sitting in a benefits sentence is not pay - "401(k) up to
# $5,000" and "$250/year gym stipend" both contain money that is not
# compensation. The salary parser needs a guard on the
# surrounding context before it accepts a number as a salary at all.
BENEFIT_MONEY = re.compile(
    r"(reimburs\w*|gym|wellness|stipend|allowance|discount|tuition|"
    r"donation|match(?:ing)?\s+contribution|per\s+diem|referral bonus|"
    r"home office)[^.]{0,80}\$|"
    r"\$\s?[\d,.]+\s*(?:/|per\s+)?(?:year|yr|month|mo)?[^.]{0,60}"
    r"(gym|wellness|reimburs\w*|stipend|membership|tuition|equipment)", re.IGNORECASE)


def salary_is_credible(description: str, matched_display: str) -> bool:
    """Does the figure extract_salary matched actually read as pay, or does
    it just sit near a dollar sign in a benefits sentence?
    """
    if not matched_display:
        return True
    pattern = re.escape(matched_display.strip())
    for m in re.finditer(pattern, description or ""):
        window = (description or "")[max(0, m.start() - 120):m.end() + 120]
        if BENEFIT_MONEY.search(window):
            return False
    return True


def term_in_title(term: str, title: str) -> bool:
    """Whole-word (or whole-phrase) match, never a bare substring.

    A substring test lets a short term hide inside an unrelated word: the
    term "NOC" matched "Nocturnist" and qualified a physician posting for
    an IT support search. Word boundaries are what every one of these
    lists means, so every list uses them - include, exclude and seniority
    alike, rather than only the last.
    """
    return re.search(r"\b" + re.escape(term.strip()) + r"\b", title,
                      re.IGNORECASE) is not None


def title_wants(term: str, title: str) -> bool:
    """A looser match for what someone is LOOKING for: every word of the
    term appears in the title, adjacent or not.

    Somebody who types "HR Specialist" means it: they will take "Human
    Resources Onboarding Specialist" and "HR Operations Specialist" too.
    Requiring the words to sit next to each other rejected exactly those
    postings and left a real candidate with nothing, which no user would
    read as anything but the tool being broken.

    Deliberately NOT used for exclusions. "What I want" should be generous,
    because the cost of a loose match is one row a person glances past;
    "what I refuse" should be exact, because the cost of a loose match
    there is a job they never see. So "account executive" keeps rejecting
    only that phrase, not any title that happens to contain both words.
    """
    words = [w for w in re.split(r"\s+", term.strip()) if w]
    if not words:
        return False
    if all(_word_or_plural(word, title) for word in words):
        return True
    # Compound spelling. "help desk" and "Helpdesk" are the same job, and so
    # are "health care"/"healthcare" and "on-boarding"/"onboarding", but a
    # word-boundary test sees nothing in common: there is no boundary after
    # "help" inside "Helpdesk". A real "IT HelpDesk Analyst" sat uncollected
    # in the corpus for exactly this reason.
    return _spelling_variant(term).search(title) is not None


def _word_or_plural(word: str, title: str) -> bool:
    """One word of a wanted term, matched against a title in either number.

    "Human Resources Generalist" and "Human Resource Generalist" are the same
    job, and Dana's search asked for the plural while the posting used the
    singular - so the exact role she was hunting scored "title matches none
    of search.title_include".

    Deliberately NOT the inflection matcher in coverage.py, which also
    accepts -ing/-ed forms. That is right for finding a skill in prose but
    wrong for titles: it would let "support" match "Supporting Actor". Number
    is the only inflection a job title varies by.
    """
    return any(term_in_title(form, title) for form in _number_forms(word))


@lru_cache(maxsize=1024)
def _number_forms(word: str) -> tuple[str, ...]:
    w = word.strip().lower()
    # Too short to pluralise meaningfully, and stripping a letter off a
    # 3-character token invents matches ("was" -> "wa").
    if len(w) <= 3:
        return (w,)
    if w.endswith("ies"):
        return (w, w[:-3] + "y")
    if w.endswith("es"):
        return (w, w[:-2], w[:-1])
    if w.endswith("s"):
        return (w, w[:-1])
    return (w, w + "s", w + "es")


# Separators a compound word gets written with, in either the term or the
# title. Anything a person might type between two halves of one word.
_SEPARATORS = r"[\s\-_/.]*"
# Below this length the pattern stops being evidence: two- and three-letter
# terms interleaved with optional separators start matching acronyms inside
# unrelated titles. Short terms keep the plain whole-word rule above.
_MIN_VARIANT_LEN = 5


@lru_cache(maxsize=512)
def _spelling_variant(term: str) -> re.Pattern[str]:
    r"""A pattern matching `term` however its separators are placed.

    Built by squashing the term to bare characters and allowing optional
    separators BETWEEN each one, anchored with word boundaries at both
    ends. That handles both directions with one mechanism - the term
    "help desk" matches "Helpdesk", and the term "helpdesk" matches
    "Help Desk" - without the false positives a substring test invites:
    the trailing \b is what stops "support" from matching "Supportive".
    """
    squashed = re.sub(r"[^A-Za-z0-9]", "", term)
    if len(squashed) < _MIN_VARIANT_LEN:
        # Never matches - the caller has already tried the whole-word rule,
        # and a short term gets nothing further.
        return re.compile(r"(?!x)x")
    body = _SEPARATORS.join(re.escape(c) for c in squashed)
    return re.compile(r"\b" + body + r"\b", re.IGNORECASE)


def _title_screen(title: str, search_cfg: dict[str, Any]) -> tuple[bool, str]:
    t = title or ""
    include = [s for s in (search_cfg.get("title_include") or []) if s]
    exclude = [s for s in (search_cfg.get("title_exclude") or []) if s]
    seniority = [s for s in (search_cfg.get("seniority") or []) if s]

    if include and not any(title_wants(term, t) for term in include):
        return False, "title matches none of search.title_include"
    for term in exclude:
        if term_in_title(term, t):
            return False, f"title excluded by search.title_exclude: {term!r}"
    for term in seniority:
        if term_in_title(term, t):
            return False, f"title excluded by search.seniority: {term!r}"
    return True, ""


def _salary_gate(salary_max: int | None, floor: float | None) -> tuple[bool, str]:
    """Judge the floor against the range TOP, never the bottom.
    Comparing against the bottom drops roles that pay well above the floor -
    "$67,953-$95,000" against a $70k floor is a false drop if the low end is
    what gets compared.
    """
    if floor is None or salary_max is None:
        return True, ""
    if salary_max < floor:
        return False, f"salary top ${salary_max:,} is under the ${floor:,} floor"
    return True, ""


def screen_job(job: Any, cfg: dict[str, Any], resume_text: str = "") -> dict[str, Any]:
    """Job is a sources.Job (or anything with the same attributes). Returns a
    dict ready to merge into the jobs.* row: qualified, score, reasons,
    remote, remote_evidence, salary_min, salary_max, currency.

    Only ever reads jobs.* shaped data and config - never touches
    job_status or job_status_log. That split is what keeps a re-screen from
    undoing a person's own tracked decisions.
    """
    search = cfg.get("search") or {}
    title = getattr(job, "title", "") or ""
    location = getattr(job, "location", "") or ""
    description = clamp(getattr(job, "description", "") or "")

    reasons: list[str] = []
    qualified = True
    # A posting that matches the search but falls short on a soft dimension -
    # pay under the floor but above the fallback tier, a stated requirement
    # the profile does not meet, or a description too thin to judge. Recorded
    # and shown, distinguished from a clean match so it does not flatter the
    # count.
    alt = False

    ok, why = _title_screen(title, search)
    if not ok:
        qualified = False
        reasons.append(why)

    mode, evidence = work_mode(title, location, description)
    is_remote = mode == "remote"
    wanted = wanted_modes(search)
    if wanted and mode not in wanted:
        qualified = False
        # Names what the posting IS, not what is missing: "no positive
        # evidence the role is remote" left a person guessing whether the
        # posting was onsite or just badly written.
        reasons.append(
            f"{mode} role ({evidence}); this search wants "
            f"{' or '.join(wanted)}")

    # A job you cannot get to is not a job you can take. Remote roles skip
    # this entirely; there is no commute to judge - hybrid roles do NOT,
    # because hybrid means going in.
    if not is_remote:
        ok, why = location_mod.is_commutable(
            location, description, search.get("locations") or [],
            travel_ok=bool(search.get("travel_ok")))
        if not ok:
            qualified = False
            reasons.append(why)

    # Vetting the candidate has ruled out. This is the first thing in this
    # function to consult requirements.py - screening judged the SEARCH
    # (title, place, pay) and left what a posting demands of the reader as
    # display-only information. A clearance the candidate cannot get is a
    # hard disqualifier, not a footnote, so it belongs here.
    profile = cfg.get("profile") or {}
    if profile.get("clearance_ok") is False:
        found, why_vetting = requirements_mod.clearance(description)
        if found:
            qualified = False
            reasons.append(f"requires a security clearance: {why_vetting}")
    if profile.get("public_trust_ok") is False:
        found, why_vetting = requirements_mod.public_trust(description)
        if found:
            qualified = False
            reasons.append(f"requires public trust vetting: {why_vetting}")

    salary = enrich.extract_salary(description)
    if salary["display"] and not salary_is_credible(description, salary["display"]):
        salary = {"display": "", "low": None, "high": None, "hourly_rate": None}
        reasons.append("a dollar amount was found but rejected: benefits context, not pay")

    floor = search.get("salary_floor")
    alt_floor = search.get("salary_alt_floor")
    ok, why = _salary_gate(salary["high"], floor)
    if not ok:
        # Under the floor but above the ALT floor is a fallback tier, not a
        # rejection: worth seeing in a thin market, worth keeping out of the
        # main count. Below both is a genuine drop.
        if alt_floor and salary["high"] is not None and salary["high"] >= alt_floor:
            alt = True
            reasons.append(f"{why} (above the ${alt_floor:,.0f} fallback floor)")
        else:
            qualified = False
            reasons.append(why)

    # Jurisdiction. A hard drop, not an alt: a role in Warsaw is not a job a
    # US-based person can take without work authorisation, and there is
    # nothing for them to judge. Only fires on POSITIVE evidence of a foreign
    # location - silence stays domestic, so a posting naming no place at all
    # is never discarded on suspicion.
    ok, why = country_mod.accepted(
        location, title, bool(search.get("us_only", True)))
    if not ok:
        qualified = False
        reasons.append(why)

    # Employment type. A mismatch flags rather than drops - see
    # employment.accepted for why a person dismissing a job beats never
    # seeing it.
    kind = employment_mod.detect(
        getattr(job, "employment_type", "") or "", title, description)
    ok, why = employment_mod.accepted(kind, search.get("employment_types") or [])
    if not ok:
        alt = True
        reasons.append(why)

    # --- the description gets a say --------------------------------------
    # Everything above judged the SEARCH. These judge the posting itself, and
    # they are the difference between "matches what I asked for" and "I could
    # actually take this job". Nothing here drops a posting outright: the rule
    # is never to drop on suspicion, so a disqualifier flags the row as alt
    # with its evidence attached and leaves the person to decide.
    asks = ""
    if qualified:
        if len(description.strip()) < MIN_JD_CHARS_TO_JUDGE:
            alt = True
            reasons.append("description too short to judge")
        else:
            reqs = requirements_mod.extract(description)
            asks = requirements_mod.summary(reqs)
            verdicts = requirements_mod.compare(reqs, cfg.get("profile") or {})
            for blocker in verdicts["blockers"]:
                alt = True
                reasons.append(blocker)

    score = 60.0
    if title and search.get("title_include") and any(
            term.lower() in title.lower() for term in search["title_include"]):
        score += 20.0
    # Remote is a preference, not a virtue - see prefers_remote.
    if is_remote and prefers_remote(search):
        score += 10.0
    if salary["high"] and floor and salary["high"] >= floor:
        score += 10.0
    if not qualified:
        score = min(score, 20.0)

    # Which of the skills this posting asks for the resume does not evidence.
    # Computed here, at screening time, and stored - the alternative is
    # recomputing it for every visible row on every repaint, and the person
    # who most needs this list is the one without a model to write it for
    # them. Not part of the verdict: a gap is something to go fix, not a
    # reason to hide the job.
    gaps = coverage_mod.coverage(description, cfg.get("skills") or [], resume_text)

    return {
        "qualified": 1 if qualified else 0,
        "verdict": ("drop" if not qualified else ("alt" if alt else "keep")),
        "coverage_pct": gaps["pct"],
        "missing_skills": ", ".join(gaps["missing"]),
        # Always written, including as "" - a row that stops qualifying must
        # not keep the summary from when it did.
        "requirements_summary": asks,
        "score": round(score, 1),
        "screen_reasons": " | ".join(reasons) if reasons else "",
        "remote": "yes" if is_remote else "no",
        "remote_evidence": evidence,
        "salary_min": salary["low"],
        "salary_max": salary["high"],
        "hourly_rate": salary.get("hourly_rate"),
        "currency": search.get("currency") or "USD",
    }
