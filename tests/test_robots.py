"""What robots.txt permits, evaluated the way every real crawler evaluates it.

THE DEFECT THESE WERE WRITTEN FOR, measured 2026-09-02. The app used
urllib.robotparser, which returns the FIRST rule that matches in file order.
Against a live site whose group reads

    User-agent: *
    Allow: /
    Disallow: /api/
    Disallow: /matching

both /api/jobs and /matching came back PERMITTED, because `Allow: /` matches
everything and is written first. The app says respecting robots.txt is the
point; it was reading the commonest layout on the web as "no rules at all".
"""
from __future__ import annotations

from unlatched import robots

# The shape that exposed it, reduced to what matters.
ALLOW_THEN_EXCEPTIONS = """User-agent: *
Allow: /
Allow: /jobs/*
Disallow: /api/
Disallow: /matching

User-agent: ClaudeBot
Disallow: /jobs/
"""

US = "unlatched/0.1.30"


def test_a_later_disallow_beats_an_earlier_allow_everything():
    """The regression itself. `Allow: /` first, `Disallow: /api/` after."""
    assert not robots.allows_url(ALLOW_THEN_EXCEPTIONS, US, "/api/jobs")
    assert not robots.allows_url(ALLOW_THEN_EXCEPTIONS, US, "/matching")


def test_what_the_site_did_open_stays_open():
    """The other half, verified rather than assumed: a parser that refused on
    any Disallow at all would stop the app reading the paths this same site
    opened, so both directions are asserted here."""
    assert robots.allows_url(ALLOW_THEN_EXCEPTIONS, US, "/jobs/12345")
    assert robots.allows_url(ALLOW_THEN_EXCEPTIONS, US, "/about")


def test_a_group_naming_another_crawler_does_not_apply_to_us():
    """/jobs/ is closed to ClaudeBot and open to everyone else. Reading the
    wrong group would have this app obeying a rule written for a different
    program - or, worse, ignoring one written for it."""
    assert robots.allows_url(ALLOW_THEN_EXCEPTIONS, US, "/jobs/1")
    assert not robots.allows_url(ALLOW_THEN_EXCEPTIONS, "ClaudeBot", "/jobs/1")


def test_a_group_naming_us_wins_over_the_star_group():
    text = """User-agent: *
Disallow: /

User-agent: unlatched
Allow: /
"""
    assert robots.allows_url(text, US, "/anything")
    assert not robots.allows_url(text, "SomeOtherBot", "/anything")


def test_consecutive_agent_lines_share_one_group():
    """Two agents, one set of rules - what the standard says and what files in
    the wild do. Treating the second line as a new empty group would leave the
    second crawler unrestricted."""
    text = """User-agent: GPTBot
User-agent: ClaudeBot
Disallow: /jobs/
"""
    assert not robots.allows_url(text, "GPTBot", "/jobs/1")
    assert not robots.allows_url(text, "ClaudeBot", "/jobs/1")
    assert robots.allows_url(text, US, "/jobs/1")


def test_the_longest_pattern_wins_not_the_first_one():
    text = """User-agent: *
Disallow: /files/
Allow: /files/public/
"""
    assert not robots.allows_url(text, US, "/files/secret.pdf")
    assert robots.allows_url(text, US, "/files/public/report.pdf")


def test_allow_wins_a_tie():
    """A site writing both for the same path has said something contradictory.
    Honouring the permission is the standard's rule and the likelier intent."""
    text = """User-agent: *
Disallow: /x/
Allow: /x/
"""
    assert robots.allows_url(text, US, "/x/y")


def test_an_empty_disallow_opens_the_site_rather_than_closing_it():
    """`Disallow:` with nothing after it is how a site says "no restrictions".
    Read as a pattern it matches every path and blocks everything - the exact
    inversion of what was written."""
    text = "User-agent: *\nDisallow:\n"
    assert robots.allows_url(text, US, "/anything")


def test_a_wildcard_matches_inside_a_pattern():
    text = "User-agent: *\nDisallow: /*/private\n"
    assert not robots.allows_url(text, US, "/a/private")
    assert robots.allows_url(text, US, "/a/public")


def test_a_dollar_anchors_the_end():
    text = "User-agent: *\nDisallow: /*.pdf$\n"
    assert not robots.allows_url(text, US, "/reports/annual.pdf")
    assert robots.allows_url(text, US, "/reports/annual.pdf.html")


def test_a_wildcard_does_not_buy_specificity():
    """`/*` and `/api/` both match /api/x. If the wildcard counted towards
    length, `Allow: /*` would outrank a real Disallow on two characters of
    punctuation and reopen the hole this module closes."""
    text = """User-agent: *
Allow: /*
Disallow: /api/
"""
    assert not robots.allows_url(text, US, "/api/x")


def test_a_file_with_no_rules_for_anyone_permits_everything():
    assert robots.allows_url("# nothing here\n", US, "/anything")


def test_rules_written_before_any_agent_line_belong_to_nobody():
    """A stray Disallow above the first User-agent applies to no group. Taking
    it as global would have one malformed line shut the app out of a site."""
    text = "Disallow: /\nUser-agent: *\nAllow: /\n"
    assert robots.allows_url(text, US, "/anything")


def test_comments_and_case_do_not_change_the_answer():
    text = """USER-AGENT: *
DISALLOW: /api/   # the api is closed
"""
    assert not robots.allows_url(text, US, "/api/x")
