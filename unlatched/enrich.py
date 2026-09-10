"""enrich.py - Salary range parsing and schedule / part-time detection.

Everything here reads a job description and returns FACTS about it -
numbers and flags - with no verdict attached. screen.py is what turns facts
into keep/drop; this module only extracts them, which is what lets both the
CLI and a future UI show "$85k-$110k, part-time: no" on a listing without
re-deriving it from raw text every time.
"""
from __future__ import annotations

import re
from typing import TypedDict


class SalaryInfo(TypedDict):
    """What `extract_salary` reports. `low`/`high` are None whenever no
    figure survived the sanity range (5000-900000) - that is a real "we
    found nothing usable" outcome, not a placeholder, so the type says so
    explicitly rather than pretending every field is always an int.
    """

    display: str
    low: int | None
    high: int | None
    hourly_rate: float | None

HOURLY = re.compile(r"\bper\s+hour|/\s*hr\b|hourly\b|an\s+hour\b", re.IGNORECASE)

# Scale units mark company financials ("$1.3 billion in revenue"), not pay.
BIG_UNIT = re.compile(r"^\s*(?:billion|million|trillion|bn\b|[bm]\b)", re.IGNORECASE)

# Language that marks a nearby figure as compensation rather than some other
# kind of number.
SALARY_CONTEXT = re.compile(
    r"\b(salary|salaries|compensation|pay|paid|rate|range|wage|earn\w*|"
    r"base|stipend|hourly|annually|per\s+(?:hour|year|annum)|"
    r"/\s*(?:yr|hr|hour|year))\b|\$\d[\d,]*\s*[kK]\b", re.IGNORECASE)

# Deliberately asymmetric: any full-time signal overrides a part-time one,
# because "full-time and part-time openings" and "part-time hours
# considered" describe flexibility, not a part-time-only role.
PART_TIME = re.compile(
    r"\b(part[\s-]?time|pt\s+position|20\s*-\s*29\s*hours|"
    r"less than 30 hours(?:\s+per\s+week)?)\b", re.IGNORECASE)
FULL_TIME = re.compile(
    r"\b(full[\s-]?time|ft\s+position|40\s*hours(?:\s+per\s+week)?|"
    r"37\.5\s*hours|salaried position|exempt position)\b", re.IGNORECASE)

WEEKEND = re.compile(
    r"\b(saturday\s+and\s+sunday|sunday\s+and\s+saturday|"
    r"sat(?:urday)?\s*[-" "\u2013" r"/]\s*sun(?:day)?|"
    r"weekends?\s+(?:are\s+)?(?:required|mandatory|expected)|"
    r"must\s+be\s+available\s+(?:on\s+)?weekends?|"
    r"work(?:ing)?\s+weekends?|weekend\s+shift)\b", re.IGNORECASE)

NIGHT_SHIFT = re.compile(
    r"\b(overnight|graveyard|night\s+shift|3rd\s+shift|third\s+shift|"
    r"midnight\s+shift)\b", re.IGNORECASE)


def is_part_time(text: str) -> bool:
    """True only when the posting is part-time AND says nothing full-time."""
    t = text or ""
    if not PART_TIME.search(t):
        return False
    return not FULL_TIME.search(t)


def schedule_flags(text: str) -> dict[str, bool]:
    """Informational only - not a gate. What a person considers acceptable
    is their own call, made in config or by reading the listing; this just
    surfaces what the posting says so they do not have to read it to find
    out.
    """
    t = text or ""
    return {
        "weekend": bool(WEEKEND.search(t)),
        "night_shift": bool(NIGHT_SHIFT.search(t)),
        "part_time": is_part_time(t),
    }


def extract_salary(text: str) -> SalaryInfo:
    """Best salary figure found in `text`.

    Returns a SalaryInfo. An hourly rate is annualised (rate * 2080) so
    `low`/`high` are always comparable to a yearly floor; `hourly_rate`
    keeps the original per-hour number for display. `low`/`high` are None
    when nothing in the sane salary range (5000-900000) was found - that is
    reported as "no salary found", never coerced into a number that was not
    actually there.
    """
    empty: SalaryInfo = {"display": "", "low": None, "high": None, "hourly_rate": None}
    if not text:
        return empty

    cands = []
    for m in re.finditer(
            r"\$\s?[\d,]+(?:\.\d+)?\s*(?:[kK])?"
            r"(?:\s*(?:-|\u2013|to)\s*\$?\s?[\d,]+(?:\.\d+)?\s*(?:[kK])?)?", text):
        frag = m.group(0).strip()
        if BIG_UNIT.match(text[m.end():m.end() + 14]):
            continue
        window = text[max(0, m.start() - 140): m.end() + 180]
        cands.append((bool(SALARY_CONTEXT.search(window)), len(frag), frag, m.start()))
    if not cands:
        return empty

    # Pay-context matches win; among equals, prefer the fuller expression (a
    # range beats a lone figure).
    cands.sort(key=lambda c: (c[0], c[1]), reverse=True)
    _, _, best, idx = cands[0]
    best = best[:80]
    window = text[max(0, idx - 140): idx + len(best) + 220]

    values = [float(v.replace(",", "")) for v in re.findall(r"\$\s?([\d,]+(?:\.\d+)?)", best)]
    values = [v for v in values if v > 0]
    if not values:
        return {**empty, "display": best}

    low_raw = min(values)
    high_raw = max(values)
    has_k = bool(re.search(r"\d\s*[kK]\b", best))

    # Hourly must be resolved before the thousands-shorthand rule below, or a
    # bare "$29.00" reads as $29,000 instead of an hourly rate. Detected two
    # ways: explicit wording nearby, or magnitude - no real annual salary is
    # $29, so a bare sub-300 figure with no "k" suffix is a rate.
    hourly = bool(HOURLY.search(window)) or (low_raw < 300 and not has_k)
    if hourly and 7 <= low_raw <= 300:
        return {
            "display": best,
            "low": int(round(low_raw * 2080)),
            "high": int(round(high_raw * 2080)),
            "hourly_rate": round(low_raw, 2),
        }

    # A bare figure under 1000 is shorthand for thousands ("$50k - $70" means
    # $70,000, not $70). Applied independently to each end of the range.
    low_val = int(low_raw * 1000) if (has_k or low_raw < 1000) else int(low_raw)
    high_val = int(high_raw * 1000) if (has_k or high_raw < 1000) else int(high_raw)
    # Outside the sane salary range, a figure is not compensation - drop it
    # rather than report a number that cannot be right. A bad high falls
    # back to the (possibly also-dropped) low, collapsing to a single point
    # or to "nothing usable" instead of keeping a clearly-wrong top of range.
    low: int | None = low_val if 5000 <= low_val <= 900_000 else None
    high: int | None = high_val if 5000 <= high_val <= 900_000 else low
    return {"display": best, "low": low, "high": high, "hourly_rate": None}
