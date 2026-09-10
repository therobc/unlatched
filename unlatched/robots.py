"""robots.py - what a site's robots.txt actually permits.

WHY THIS IS NOT `urllib.robotparser`. The standard library evaluates rules in
FILE ORDER and returns the first one that matches. Every real robots.txt of
this shape then reads as "everything is allowed":

    User-agent: *
    Allow: /
    Disallow: /api/

`Allow: /` matches every path and comes first, so `/api/` is never blocked.
That is not a corner case - "Allow: / then a list of exceptions" is one of the
commonest layouts on the web, and a site using it got no protection at all
from a tool that says respecting robots.txt is the point (measured 2026-09-02
against a live site whose /api/ and /matching are both disallowed and both
came back permitted).

THE RULE EVERY CRAWLER ACTUALLY USES is longest-match: of the rules that match
the path, the one with the LONGEST pattern wins, and Allow wins a tie. That is
what Google's parser does and what site owners write their files expecting.

WHAT IS SUPPORTED, and it is the whole of the de facto standard: `*` as a
wildcard inside a pattern, `$` anchoring the end, group selection by the most
specific matching user-agent token, and `*` as the fallback group. Crawl-delay
and Sitemap lines are ignored here - fetch.py reads Crawl-delay itself.

BEING UNABLE TO READ robots.txt IS NOT A BLOCK. Most career sites publish none
at all, and treating absence as refusal would stop the app doing the one thing
it exists to do. That decision lives in fetch.py, which only calls this when it
has a document.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Rules:
    """The Allow and Disallow patterns that apply to one user-agent."""

    allow: tuple[str, ...] = ()
    disallow: tuple[str, ...] = ()


def _agent_name(line: str) -> str:
    return line.split(":", 1)[1].strip().lower()


def _value(line: str) -> str:
    return line.split(":", 1)[1].strip()


def parse(text: str, agent: str) -> Rules:
    """The rules that apply to `agent`, from the groups in `text`.

    GROUPS ARE CHOSEN BY SPECIFICITY, not by position: a file may name several
    agents and the most specific token that matches ours is the one that
    applies. `*` is the fallback and is used only when nothing else matches -
    a site that blocks one named crawler must not thereby lose its general
    rules, and a site that names US must not have them diluted by the general
    ones.

    CONSECUTIVE User-agent LINES SHARE ONE GROUP, which is what the standard
    says and what files in the wild do:

        User-agent: GPTBot
        User-agent: ClaudeBot
        Disallow: /jobs/
    """
    agent_low = agent.lower()
    groups: list[tuple[list[str], list[tuple[str, str]]]] = []
    naming = False
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        field = line.split(":", 1)[0].strip().lower()
        if field == "user-agent":
            if not naming or not groups:
                groups.append(([], []))
                naming = True
            groups[-1][0].append(_agent_name(line))
        elif field in {"allow", "disallow"}:
            if not groups:
                # Rules before any User-agent line belong to nobody. Ignored
                # rather than guessed at.
                continue
            naming = False
            groups[-1][1].append((field, _value(line)))

    best: tuple[int, list[tuple[str, str]]] | None = None
    for named, rules in groups:
        for ua in named:
            if ua == "*":
                score = 0
            elif agent_low.startswith(ua):
                # A robots token matches the START of a product token, so
                # "unlatched" matches "unlatched/0.1.30". Longer token, more
                # specific group. Verified by
                # test_robots::test_a_group_naming_us_wins_over_the_star_group.
                score = len(ua)
            else:
                continue
            if best is None or score > best[0]:
                best = (score, rules)

    if best is None:
        return Rules()
    allow = tuple(v for k, v in best[1] if k == "allow" and v)
    # AN EMPTY `Disallow:` MEANS "nothing is disallowed" and is how a site
    # says everything is open. Dropping the empty value is right; treating it
    # as a pattern would block the whole site, which is the opposite.
    disallow = tuple(v for k, v in best[1] if k == "disallow" and v)
    return Rules(allow=allow, disallow=disallow)


def _matches(pattern: str, path: str) -> bool:
    """Does `pattern` match `path`, honouring `*` and a trailing `$`?"""
    anchored = pattern.endswith("$")
    body = pattern[:-1] if anchored else pattern
    expr = "".join(".*" if ch == "*" else re.escape(ch) for ch in body)
    return re.match(expr + ("$" if anchored else ""), path) is not None


def _specificity(pattern: str) -> int:
    """How specific a rule is: the length of its pattern.

    The wildcard and the anchor are not path characters, so they do not count
    towards length - otherwise `/*` would outrank `/api/` on a two-character
    technicality and reopen exactly the hole this module exists to close.
    """
    return len(pattern.replace("*", "").replace("$", ""))


def allows(rules: Rules, path: str) -> bool:
    """Is `path` permitted under `rules`?

    LONGEST MATCH WINS, AND ALLOW WINS A TIE. The tie rule is the standard's,
    and it is the safe direction for the SITE as well as for us: a site that
    writes `Allow: /public/` beside `Disallow: /public/` has said something
    contradictory, and honouring the permission is what it most likely meant.
    """
    if not path.startswith("/"):
        path = "/" + path
    best_allow = max((_specificity(p) for p in rules.allow if _matches(p, path)),
                     default=-1)
    best_deny = max((_specificity(p) for p in rules.disallow if _matches(p, path)),
                    default=-1)
    if best_deny < 0:
        return True
    return best_allow >= best_deny


def allows_url(text: str, agent: str, path: str) -> bool:
    """Convenience for one question against one document."""
    return allows(parse(text, agent), path)
