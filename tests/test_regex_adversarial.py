"""An earlier CAREERS_HOST_RE used overlapping character classes
and could backtrack catastrophically - 1.5s at 20KB of input, 109s at 160KB.
Every regex that runs over a fetched page needs an adversarial length test,
timed against real-shaped input, not a small fixture.
"""
from __future__ import annotations

import random
import time

from unlatched import discover, manual, requirements, screen
from unlatched.sources import schema_org

TIME_BUDGET_S = 1.0
MIN_SIZE_BYTES = 200_000


def _adversarial_html(target_bytes: int) -> str:
    """Messy HTML built to stress a backtracking-prone host/path regex: long
    runs of the prefix words a naive pattern would key on, interleaved with
    dots and hyphens that never resolve into a real match. A safe (disjoint
    character class) regex is linear in this; a catastrophic one is not.
    """
    # A seeded, non-cryptographic generator is exactly right here: this only
    # needs a reproducible pile of messy test bytes, never secrecy.
    rng = random.Random(1234)  # noqa: S311
    prefixes = ("careers", "jobs", "apply", "talent", "workwith", "joinus")
    chunks = ["<html><body>"]
    size = 0
    while size < target_bytes:
        prefix = rng.choice(prefixes)
        # A run of hyphenated / dotted segments that dead-ends without a
        # trailing "/" - the shape that made the old pattern backtrack.
        junk = "-".join(f"seg{n}" for n in range(rng.randint(3, 9)))
        dots = ".".join(f"part{n}" for n in range(rng.randint(3, 9)))
        piece = (f'<a href="https://{prefix}{junk}.{dots}z">link</a> '
                  'the quick brown fox jumps over the lazy dog '
                  '<span>salary $85,000 gym stipend $50/month remote work</span> ')
        chunks.append(piece)
        size += len(piece)
    chunks.append("</body></html>")
    return "".join(chunks)


def test_careers_host_regex_is_linear_time():
    html = _adversarial_html(MIN_SIZE_BYTES)
    assert len(html) >= MIN_SIZE_BYTES

    start = time.perf_counter()
    discover.CAREERS_HOST_RE.findall(html)
    elapsed = time.perf_counter() - start
    assert elapsed < TIME_BUDGET_S, f"CAREERS_HOST_RE took {elapsed:.3f}s on {len(html)} bytes"


def test_screen_regexes_are_linear_time():
    html = _adversarial_html(MIN_SIZE_BYTES)
    description = html * 1  # already 200KB+, used as-is as posting text

    for pattern, name in (
        (screen.REMOTE_DECLARED, "REMOTE_DECLARED"),
        (screen.ONSITE_DECLARED, "ONSITE_DECLARED"),
        (screen.BENEFIT_MONEY, "BENEFIT_MONEY"),
        (screen.REMOTE_LOCATION, "REMOTE_LOCATION"),
    ):
        start = time.perf_counter()
        pattern.search(description)
        elapsed = time.perf_counter() - start
        assert elapsed < TIME_BUDGET_S, f"{name} took {elapsed:.3f}s on {len(description)} bytes"


def test_page_confirms_company_is_linear_time():
    html = _adversarial_html(MIN_SIZE_BYTES)
    start = time.perf_counter()
    discover.page_confirms_company(html, "Example Widgets Incorporated")
    elapsed = time.perf_counter() - start
    assert elapsed < TIME_BUDGET_S, f"page_confirms_company took {elapsed:.3f}s"


def _adversarial_posting(target_bytes: int) -> str:
    """Prose shaped like a job description, built to stress the requirement
    patterns: long runs of the words they key on - years, degrees, clearances,
    travel percentages - that never complete into a real statement.
    """
    rng = random.Random(4321)  # noqa: S311
    leads = ("must have", "requires", "preferred", "ideally", "we need")
    nouns = ("years of", "year's", "degree in", "clearance", "travel", "lifting")
    chunks = []
    size = 0
    while size < target_bytes:
        piece = (f"{rng.choice(leads)} {rng.randint(2, 15)}+ {rng.choice(nouns)} "
                  f"{'experience ' * rng.randint(1, 4)}in a related field, "
                  "bachelors masters phd or equivalent, up to 25% travel, "
                  "active secret clearance preferred but not required; ")
        chunks.append(piece)
        size += len(piece)
    return "".join(chunks)


def test_requirement_regexes_are_linear_time():
    """The Asks column runs these over the FULL description.

    Sized against what descriptions actually became: the Lever and
    SmartRecruiters fixes on 2026-08-07 took the average stored description
    from 874 to 5,653 characters, and nothing re-timed these against the
    larger input they now see. 200KB is far beyond the real worst case, which
    is the point - a pattern that is linear here cannot be surprised by a long
    posting.
    """
    text = _adversarial_posting(MIN_SIZE_BYTES)
    for pattern, name in (
        (requirements.YEARS_RE, "YEARS_RE"),
        (requirements.YEARS_NEGATIVE, "YEARS_NEGATIVE"),
        (requirements.YEARS_POSITIVE_CONTEXT, "YEARS_POSITIVE_CONTEXT"),
        (requirements.COMPANY_AGE_BEFORE, "COMPANY_AGE_BEFORE"),
        (requirements.EDU_REQUIRED, "EDU_REQUIRED"),
        (requirements.EDU_PREFERRED, "EDU_PREFERRED"),
        (requirements.EDU_EQUIVALENT, "EDU_EQUIVALENT"),
        (requirements.TRAVEL_PCT, "TRAVEL_PCT"),
        (requirements.TRAVEL_QUALITATIVE, "TRAVEL_QUALITATIVE"),
        (requirements.CLEARANCE, "CLEARANCE"),
        (requirements.NO_CLEARANCE, "NO_CLEARANCE"),
        (requirements.SUPERVISES, "SUPERVISES"),
        (requirements.LIFTING, "LIFTING"),
    ):
        start = time.perf_counter()
        pattern.search(text)
        elapsed = time.perf_counter() - start
        assert elapsed < TIME_BUDGET_S, f"{name} took {elapsed:.3f}s on {len(text)} bytes"


def test_requirements_summary_is_linear_time():
    """The whole extraction, not just one pattern at a time.

    This is where the quadratic scan hid: every individual pattern here is
    fast, and the cost was in _line_window rescanning the entire description
    from position 0 once per match to build a 60-character window. Timing the
    patterns one by one would never have found it.
    """
    text = _adversarial_posting(MIN_SIZE_BYTES)
    start = time.perf_counter()
    requirements.summarize(requirements.extract(text))
    elapsed = time.perf_counter() - start
    assert elapsed < TIME_BUDGET_S * 3, f"extract took {elapsed:.3f}s on {len(text)} bytes"


def _unclosed_markup() -> str:
    """Openings that look like the target and never close.

    This is the input that made the old page-wide LD_BLOCK pattern take 88.7
    SECONDS: every unclosed `<script type=application/ld+json` opening sent its
    `(.*?)` scanning to the end of the document again, so the cost was
    openings TIMES page length. A careers page can be written this way on
    purpose, and these collectors read pages written by strangers.
    """
    html = ("<script type=application/ld+jsonX " + ("data-x=1 " * 20) + ">") * 4000
    return html + '<div class="show-more-less-html__markupX">' * 4000


def test_json_ld_extraction_is_linear_time():
    """The regression test for the 88-second hang. parse_jsonld_jobs now scans
    forward with str.find and never backtracks, so the guarantee is structural
    rather than a pattern that happens to be well behaved."""
    html = _unclosed_markup()
    start = time.perf_counter()
    found = schema_org.parse_jsonld_jobs(html)
    elapsed = time.perf_counter() - start
    assert found == [], "nothing in this page is a real posting"
    assert elapsed < TIME_BUDGET_S, f"parse_jsonld_jobs took {elapsed:.3f}s on {len(html)} bytes"


def test_json_ld_is_still_found_when_the_markup_is_real():
    """A linear scanner that finds nothing would also pass the test above."""
    html = ('<html><head>'
            '<script type=application/ld+json>'
            '{"@type": "JobPosting", "title": "Support Analyst", '
            '"description": "Answer tickets.", "url": "https://example.com/j/1"}'
            '</script></head><body>x</body></html>')
    found = schema_org.parse_jsonld_jobs(html)
    assert len(found) == 1
    assert found[0]["title"] == "Support Analyst"


def test_linkedin_extraction_is_linear_time():
    """The one path that reads a site whose markup we neither control nor are
    welcome on. These were `.*?` runs across a whole page and took 7.4s on
    markup that opens the tag repeatedly and never closes it."""
    html = _unclosed_markup()
    start = time.perf_counter()
    assert manual.read_linkedin(html) == {}, "nothing here is a real posting"
    elapsed = time.perf_counter() - start
    assert elapsed < TIME_BUDGET_S, f"_read_linkedin took {elapsed:.3f}s on {len(html)} bytes"


def test_linkedin_extraction_still_reads_a_real_page():
    """A linear scanner that finds nothing would also pass the test above."""
    html = ('<html><body><h1 class="top-card-layout__title">Support Analyst</h1>'
            '<a class="topcard__org-name-link" href="/x">Example Ltd</a>'
            '<span class="topcard__flavor--bullet">Knoxville, TN</span>'
            '<div class="show-more-less-html__markup">Answer tickets.</div>'
            '</body></html>')
    page = manual.read_linkedin(html)
    assert page["title"] == "Support Analyst"
    assert page["employer"] == "Example Ltd"
    assert page["location"] == "Knoxville, TN"
    assert page["description"] == "Answer tickets."
