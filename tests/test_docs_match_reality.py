"""Prose held against the code it describes.

A hand-written list standing beside a generated one drifts, always, and it
drifts silently because nothing compares them. This package has been bitten by
that shape repeatedly: five Rust files each carrying their own copy of the
status vocabulary (see test_status_vocabulary), packaging/engine.spec's
collector backstop naming thirteen of fifteen (see
test_frozen_engine_lists_every_collector), and every one of the documents
checked here.

WHAT WAS WRONG WHEN THESE WERE WRITTEN, in the order the tests appear:

  README's source table named twelve of the fifteen collectors. The three it
  omitted were exactly the three SEARCH sources - the ones that read something
  other than a named employer's board - sitting under a sentence beginning
  "Every source Unlatched reads".

  tests/README named 80 of the 87 test files.

  README's "Command reference" named 31 of the 35 subcommands, and `prune` -
  the only command in the app that deletes anything - was one of the four it
  left out.

  Twelve comments across nine files pointed at paths that do not ship.

  BUILDING.md stated a Python floor two releases out of date, which is the
  difference between a refusal at install time and a traceback at first run.

None of it was caught by anything, because prose is not compiled and nobody
re-counts a list they wrote once.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from unlatched import sources

ROOT = Path(__file__).resolve().parents[1]
TESTS = ROOT / "tests"

# The name each collector goes by in README's source table.
#
# WRITTEN OUT RATHER THAN DERIVED. Three of the fifteen ids do not appear in
# the table at all in their registry spelling - README.md:211 says "Oracle HCM
# (Fusion Cloud Recruiting)" for `oracle_hcm`, :214 "schema.org JobPosting" for
# `schema_org`, and :215 "Sitemaps" for `sitemap` - so a check that looked for
# the id would fail on rows that are present and correct.
#
# The mapping also carries the test's weight: a collector added to the registry
# has no entry here, so `test_every_collector_has_a_documented_name` fails and
# names it.
README_NAMES = {
    "ashby": "Ashby",
    "bamboohr": "BambooHR",
    "breezy": "Breezy",
    "greenhouse": "Greenhouse",
    "lever": "Lever",
    "nodesk": "NoDesk",
    "oracle_hcm": "Oracle HCM",
    "recruitee": "Recruitee",
    "remoteok": "Remote OK",
    "schema_org": "schema.org JobPosting",
    "sitemap": "Sitemaps",
    "smartrecruiters": "SmartRecruiters",
    "usajobs": "USAJOBS",
    "workable": "Workable",
    "workday": "Workday",
}


def _source_table() -> str:
    """The rows of README's source table, as text."""
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    marker = "| Source | Access |"
    assert marker in readme, "README.md no longer has a source table"
    after = readme.split(marker, 1)[1]
    # The table runs until the first blank line after it.
    return after.split("\n\n", 1)[0]


def test_every_collector_has_a_documented_name():
    """A new collector must be given a public name, deliberately."""
    missing = sorted(set(sources.registry()) - set(README_NAMES))
    assert not missing, (
        "these collectors are in the registry but have no entry in "
        f"README_NAMES, so nothing checks that README mentions them: {missing}")

    stale = sorted(set(README_NAMES) - set(sources.registry()))
    assert not stale, f"README_NAMES names collectors that no longer exist: {stale}"


@pytest.mark.parametrize("collector", sorted(README_NAMES))
def test_the_readme_source_table_names_every_collector(collector):
    """"Every source Unlatched reads" has to mean every source."""
    table = _source_table()
    name = README_NAMES[collector]
    assert name in table, (
        f"README's source table does not list {name} ({collector}), but the "
        "registry returns it and the paragraph above the table says every "
        "source is listed.")


def test_the_test_index_names_every_test_file():
    """tests/README is an index; an index that skips files is a wrong index."""
    named = set(re.findall(r"test_[a-z0-9_]+", (TESTS / "README.md").read_text(encoding="utf-8")))
    on_disk = {p.stem for p in TESTS.glob("test_*.py")}

    missing = sorted(on_disk - named)
    assert not missing, f"tests/README.md does not mention these test files: {missing}"

    stale = sorted(named - on_disk)
    assert not stale, f"tests/README.md names test files that do not exist: {stale}"


def test_the_documents_are_real_and_the_parsers_found_something():
    """A positive control for everything above.

    Each check above passes trivially against an empty parse - an unfound table
    is an empty string that "contains" nothing, and a glob that matches no
    files makes the index look complete. So the parsers have to prove they read
    something real first.
    """
    table = _source_table()
    assert table.count("|") > 20, f"the source table parsed as {table!r}"
    assert "Greenhouse" in table, "the source table parse missed a known row"

    assert len({p.stem for p in TESTS.glob("test_*.py")}) > 50, \
        "the test glob found almost nothing - the index check would be vacuous"

    assert len(sources.registry()) > 10, \
        "the registry came back nearly empty - the table checks would be vacuous"


def _command_table() -> str:
    """The rows of README's command reference, as text."""
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    marker = "## Command reference"
    assert marker in readme, "README.md no longer has a command reference"
    return readme.split(marker, 1)[1].split("\n## ", 1)[0]


def test_the_command_reference_mentions_every_subcommand():
    """A "Command reference" that lists most of the commands is a trap.

    It listed 31 of 35. The four it left out were `prune`, `closures`,
    `attach-trust` and `forget-company` - and `prune` is the only command in
    the app that deletes anything, so the one command whose behaviour a person
    most needs stated was the one not stated.

    The table groups related commands into one row (`jobs` / `show`), so this
    asks whether the name appears at all rather than trying to match rows.

    WHAT THIS DOES NOT CHECK, and it matters: that the DESCRIPTION beside a
    command is true. `ats-audit` was listed as "Which employers resolved to
    which system, and which did not" while the parser calls it "deep
    parse-failure audit of a resume" - a different feature, and one that does
    not exist. This test passed the whole time, because the name was there.

    All 35 rows were then read against the parser's own help by hand, and that
    was the only one wrong. It is not automated because two correct
    descriptions of one command can share almost no words, so a similarity
    score would be noise wearing the costume of a check.
    """
    from unlatched import cli

    table = _command_table()
    parser = cli.build_parser()
    subs: list[str] = []
    for action in parser._subparsers._group_actions:  # noqa: SLF001
        subs.extend(action.choices)

    missing = sorted(s for s in subs if s not in table)
    assert not missing, (
        "README's command reference does not mention these subcommands, which "
        f"the parser accepts: {missing}")


def test_the_command_table_parsed_and_the_parser_has_commands():
    """A positive control for the check above: an unfound table is an empty
    string that "contains" nothing, so every command would look missing - and a
    parser with no subcommands would make the check pass against anything."""
    from unlatched import cli

    table = _command_table()
    assert table.count("|") > 40, f"the command table parsed as {table!r}"
    assert "`collect`" in table, "the command table parse missed a known row"

    parser = cli.build_parser()
    subs: list[str] = []
    for action in parser._subparsers._group_actions:  # noqa: SLF001
        subs.extend(action.choices)
    assert len(subs) > 20, f"only {len(subs)} subcommands found - check is vacuous"


# A pointer to SOURCE OR DOCUMENTATION: either dir/file.ext, or a bare .md
# filename - SPEC.md had no directory part and would otherwise slip through.
#
# Data extensions (.json, .txt, .csv) are deliberately NOT checked. Measured
# across the whole tree: every unresolved path with one of those extensions is
# a name a test INVENTED to pass as an argument ("nowhere/x.json",
# "nope/gone.txt"), which is what fixtures are made of rather than a claim
# about a file anybody should find.
PATH_CITATION = re.compile(
    r"\b([\w.-]+(?:/[\w.-]+)+\.(?:py|rs|md|toml)|[A-Z][\w.-]*\.md)\b")

CITATION_SUFFIXES = {".py", ".rs", ".md", ".toml", ".spec", ".iss", ".yml"}
SKIP_DIRS = {".git", "__pycache__", "target", "dist", "build",
             ".mypy_cache", ".pytest_cache", ".ruff_cache"}


def _shipped_files() -> list[Path]:
    out = []
    for p in ROOT.rglob("*"):
        if not p.is_file() or p.suffix not in CITATION_SUFFIXES:
            continue
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        # THIS FILE IS EXEMPT, and has to be: the test below explains itself by
        # naming the dead paths it was written for, and its positive control
        # asserts that two of them do not resolve. Scanning it would report
        # every example as a defect. It caught itself on the first run, which
        # is the most convincing evidence available that it catches anything.
        if p.resolve() == Path(__file__).resolve():
            continue
        out.append(p)
    return out


def _resolves(citation: str, from_file: Path) -> bool:
    """The way a reader resolves it: against the tree root, and against the
    citing file's own directory and every ancestor up to the root.

    places.rs writes "data/us_places.txt" and means desktop/data/...; Cargo.toml
    writes "src/main.rs". Both are correct from where they sit.
    """
    bases = [ROOT]
    here = from_file.parent
    while True:
        bases.append(here)
        if here == ROOT:
            break
        here = here.parent
    return any((b / citation).exists() for b in bases)


def _unresolved_citations() -> dict[str, list[str]]:
    bad: dict[str, list[str]] = {}
    for p in _shipped_files():
        try:
            text = p.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for line_no, line in enumerate(text.splitlines(), 1):
            for cite in PATH_CITATION.findall(line):
                if not _resolves(cite, p):
                    bad.setdefault(cite, []).append(
                        f"{p.relative_to(ROOT)}:{line_no}")
    return bad


def test_no_shipped_file_points_at_something_that_does_not_ship():
    """A comment naming a file the reader cannot open is a dead end.

    app/ is the tree that publishes; SPEC.md and research/ live one level ABOVE
    it. Eleven comments across eight files pointed into them - a specification
    the reader has no copy of, and three research write-ups explaining where
    measured constants came from. A twelfth pointed at `unlatched/secrets.py`,
    which has never existed under that name: keystore.py's own docstring
    explains it was deliberately not called `secrets` because that would shadow
    the standard library module.

    None of this was reachable by the person the comment was written for.
    """
    bad = _unresolved_citations()
    assert not bad, (
        "these paths are named in shipped files but do not exist:\n" +
        "\n".join(f"  {cite}\n      {', '.join(where[:4])}"
                  for cite, where in sorted(bad.items())))


def test_the_citation_scan_found_something_and_would_catch_a_dead_one():
    """A positive control.

    The check above asserts an ABSENCE, so it passes just as happily against a
    regex that matches nothing or a file list that is empty.
    """
    files = _shipped_files()
    assert len(files) > 100, f"only {len(files)} files scanned - check is vacuous"

    found = sum(len(PATH_CITATION.findall(p.read_text(encoding="utf-8")))
                for p in files)
    assert found > 40, f"only {found} citations found - check is vacuous"

    # The two real shapes this was written for, neither of which exists.
    here = ROOT / "tests" / "test_docs_match_reality.py"
    assert not _resolves("research/posting_time_of_day.md", here)
    assert not _resolves("SPEC.md", here)
    # ...and one that does, so it is not simply refusing everything.
    assert _resolves("COLLECTORS.md", here)
    assert _resolves("unlatched/keystore.py", here)


# Where each document states the minimum Python version, in prose.
PY_FLOOR = re.compile(r"Python (\d+\.\d+)\+")


def test_every_document_states_the_python_floor_pyproject_enforces():
    """Three copies of one number, and one of them was two releases behind.

    pyproject says `requires-python = ">=3.11"`, and it has to: the engine
    calls `datetime.UTC`, which does not exist before 3.11. While pyproject
    still said 3.9, pip installed happily on 3.9 and the engine died on import
    - so the floor is not cosmetic, it is the difference between a clear
    refusal at install time and a traceback at first run.

    pyproject was corrected and README with it. BUILDING.md went on saying 3.9,
    because nothing compared the three.
    """
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    stated = re.search(r'requires-python\s*=\s*">=(\d+\.\d+)"', pyproject)
    assert stated, "pyproject.toml no longer states requires-python"
    floor = stated.group(1)

    for name in ("README.md", "BUILDING.md"):
        text = (ROOT / name).read_text(encoding="utf-8")
        found = PY_FLOOR.findall(text)
        assert found, f"{name} does not state a Python version at all"
        wrong = [v for v in found if v != floor]
        assert not wrong, (
            f"{name} says Python {wrong} while pyproject requires >={floor}")


def test_both_halves_create_the_same_tables():
    """The schema is a shared contract and db.rs says so in its first line:
    "both open the same database file and must create identical tables".

    It was not true. The desktop's SCHEMA_SQL created seven tables and the
    engine's eight - `meta` was engine-only - while db.rs::collector_taken_in
    queries `SELECT value FROM meta`. A profile created by the DESKTOP and
    opened before anything had been collected therefore had no such table, the
    query failed, and the call site's unwrap_or_default turned that into "no
    collector has ever been read in".

    WHY A TABLE RATHER THAN A COLUMN. Columns already have a mechanism -
    ensure_columns on either side adds what its half is missing. Tables have
    none: whichever half creates the file decides which exist, so a table only
    one side declares is only there when that side got there first.

    The Rust is READ AS TEXT and executed against sqlite3 directly, which is
    the only way to compare the two without a build step inside a Python test.
    """
    import re
    import sqlite3
    from pathlib import Path

    from unlatched import db as engine_db

    rust = (Path(__file__).resolve().parent.parent
            / "desktop" / "src" / "db.rs").read_text(encoding="utf-8")
    block = re.search(r'pub const SCHEMA_SQL: &str = "(.*?)";', rust, re.DOTALL)
    assert block, "SCHEMA_SQL was renamed or is no longer a plain string"

    def tables_of(schema: str) -> set[str]:
        con = sqlite3.connect(":memory:")
        con.executescript(schema)
        found = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'")}
        con.close()
        return found

    desktop = tables_of(block.group(1))
    engine = tables_of(engine_db.SCHEMA)

    assert desktop == engine, (
        f"the two halves do not create the same tables - "
        f"engine only: {sorted(engine - desktop)}, "
        f"desktop only: {sorted(desktop - engine)}. Whichever half creates a "
        f"profile first decides what exists, so a table only one side declares "
        f"is missing whenever the other got there first.")
