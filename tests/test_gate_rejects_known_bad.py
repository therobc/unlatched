"""A pattern built through an inline shell command had its
`\\b` collapse first to `\b` and then to a literal backspace character, so
the gate could never match anything and passed every input silently -
failing OPEN. Two defenses:

  1. every gate regex is written and read from a .py file with the editor,
     never assembled through a shell - which is simply true of this whole
     package, but is worth a machine-checkable form too.
  2. every gate that can fail open needs a self-test proving it REJECTS a
     known-bad input, so a collapsed escape (or any other silent breakage)
     shows up as a failing assertion instead of an all-pass gate.
"""
from __future__ import annotations

import re

from unlatched import coverage, discover, screen

# The literal control character an escape collapse produces. No gate pattern
# in this package may contain it - if one does, the pattern cannot possibly
# match real text and the gate has failed open.
BACKSPACE = "\x08"

GATE_PATTERNS = [
    ("screen.REMOTE_LOCATION", screen.REMOTE_LOCATION),
    ("screen.REMOTE_DECLARED", screen.REMOTE_DECLARED),
    ("screen.ONSITE_DECLARED", screen.ONSITE_DECLARED),
    ("screen.BENEFIT_MONEY", screen.BENEFIT_MONEY),
    ("discover.CAREERS_HOST_RE", discover.CAREERS_HOST_RE),
]


def test_no_gate_pattern_contains_a_collapsed_escape():
    for name, pattern in GATE_PATTERNS:
        assert BACKSPACE not in pattern.pattern, (
            f"{name} contains a literal backspace - an escape has collapsed "
            f"and the gate can never match real text")


def test_remote_gate_rejects_a_known_bad_onsite_posting():
    is_remote, _ = screen.remote_evidence(
        "Support Analyst", "Columbus, Ohio",
        "This role requires you to be onsite daily at our Columbus office.")
    assert is_remote is False


def test_careers_host_regex_rejects_a_non_careers_host():
    html = '<a href="https://www.example.com/about/">About</a>'
    assert discover.CAREERS_HOST_RE.findall(html) == []


def test_careers_host_regex_accepts_a_known_good_host():
    html = '<a href="https://careers.example.com/">Careers</a>'
    assert discover.CAREERS_HOST_RE.findall(html) == ["careers.example.com"]


def test_page_confirms_company_rejects_an_unrelated_page():
    html = "<html><body>Welcome to Totally Different Corp</body></html>"
    assert discover.page_confirms_company(html, "Example Widgets") is False


def test_page_confirms_company_accepts_a_page_naming_the_company():
    html = "<html><body>Careers at Example Widgets Incorporated</body></html>"
    assert discover.page_confirms_company(html, "Example Widgets") is True


def test_coverage_present_rejects_a_known_bad_prefix_collision():
    # "soft" must never match inside "software" - a prefix collision here
    # would silently inflate coverage on every posting that says "software".
    assert coverage.present("soft", "we build enterprise software") is False


def test_benefit_money_gate_rejects_a_known_bad_real_salary_line():
    # The guard must not fire on ordinary compensation language - that would
    # be failing CLOSED on everything, the opposite defect but just as bad.
    text = "The salary for this role is $80,000 per year."
    assert screen.salary_is_credible(text, "$80,000") is True


def test_every_gate_pattern_compiles_and_is_not_trivially_empty():
    for name, pattern in GATE_PATTERNS:
        assert isinstance(pattern, re.Pattern), name
        assert pattern.pattern.strip(), f"{name} is an empty pattern"


# ---- JSON-LD in the wild ------------------------------------------------

def test_schema_org_reads_an_unquoted_ld_json_attribute():
    """HTML5 makes the quotes optional and every minifier drops them:
    `<script type=application/ld+json>` is what a Hugo- or Next-built
    careers page actually serves. Requiring quotes made this collector
    return ZERO from any minified page - no error, no warning, just a site
    that looked like it had no openings. Found on a live site 2026-08-06.
    """
    from unlatched.sources import schema_org
    minified = ('<script type=application/ld+json>'
                '{"@type":"JobPosting","title":"Support Analyst"}</script>')
    quoted = ('<script type="application/ld+json">'
              '{"@type":"JobPosting","title":"Support Analyst"}</script>')
    for html in (minified, quoted):
        nodes = schema_org.parse_jsonld_jobs(html)
        assert len(nodes) == 1, html
        assert nodes[0]["title"] == "Support Analyst"


def test_schema_org_keeps_the_employer_named_in_the_markup():
    """A board collector already knows whose board it is reading; a search
    source does not."""
    from unlatched.sources import schema_org
    html = ('<script type=application/ld+json>{"@type":"JobPosting",'
            '"title":"Support Analyst","hiringOrganization":'
            '{"@type":"Organization","name":"Acme Corp"}}</script>')
    assert schema_org.parse_jsonld_jobs(html)[0]["employer"] == "Acme Corp"
