"""The two halves of the app must agree about what a status means.

The vocabulary is written twice - once in desktop/src/status.rs, which owns the
labels, colours and dependency rules because it draws them, and once in
unlatched/status.py, which needs only to know which statuses close a job out and
which prove an application was sent.

TWO COPIES IS THE POINT OF THESE TESTS. Rust cannot import Python constants and
the engine cannot link the desktop crate, so the choice is duplication with a
check or duplication without one. Before status.rs existed, five files on the
Rust side each carried their own copy of the list, and they had already drifted:
a status added for the funnel was never added to the "did the employer reply"
test next to it, so an offer scored as silence for as long as both existed.

These read status.rs as TEXT rather than reasoning about it, which is the only
way a Python test can hold a Rust file to account.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from unlatched import status

STATUS_RS = Path(__file__).resolve().parents[1] / "desktop" / "src" / "status.rs"


def _flow_entries() -> list[tuple[str, bool, int | None]]:
    """(value, settled, rung) for each entry of FLOW, in declaration order."""
    text = STATUS_RS.read_text(encoding="utf-8")
    marker = "pub const FLOW"
    assert marker in text, f"{STATUS_RS.name} no longer declares {marker}"
    body = text.split(marker, 1)[1].split("\n];", 1)[0]

    entries = []
    for chunk in body.split("Status {")[1:]:
        value = _field(chunk, r'value:\s*"([^"]+)"', "value")
        settled = _field(chunk, r"settled:\s*(true|false)", "settled")
        rung = _field(chunk, r"rung:\s*(?:Some\((\d+)\)|None)", "rung")
        entries.append((
            value,
            settled == "true",
            int(rung) if rung else None,
        ))
    return entries


def _field(chunk: str, pattern: str, name: str) -> str:
    """One field out of a FLOW entry, failing loudly if the shape changed.

    A silent miss here would make every comparison below pass against an empty
    or partial reading of the Rust file, which is the failure mode a
    cross-language check has to be most careful about - it looks like agreement.
    """
    found = re.search(pattern, chunk)
    if found is None:
        raise AssertionError(
            f"a FLOW entry has no {name}, or the field names changed:\n"
            f"{chunk[:200]}")
    # `rung: None` matches with an empty group, which is a real reading rather
    # than a miss - the caller turns it into None.
    return found.group(1) or ""


def test_the_rust_file_is_where_this_test_thinks_it_is():
    """A positive control for the parser itself.

    Every other test here passes trivially if _flow_entries returns nothing, so
    the parser has to prove it found something real before its silence can be
    read as agreement.
    """
    assert STATUS_RS.is_file(), f"{STATUS_RS} is gone - the checks below are vacuous"
    entries = _flow_entries()
    # Eleven since 2026-09-05, when "they said no" was split into No Offer
    # (after an interview), Rejection Email and No Response. The number is
    # written out rather than derived from status.FLOW on purpose: deriving it
    # would make this control agree with whatever the parser happened to find,
    # which is the one thing it exists not to do.
    assert len(entries) == 11, f"expected the eleven statuses, parsed {len(entries)}"


def test_both_halves_list_the_same_statuses_in_the_same_order():
    parsed = [value for value, _, _ in _flow_entries()]
    assert parsed == list(status.FLOW), (
        "unlatched/status.py FLOW and desktop/src/status.rs FLOW disagree.\n"
        f"  rust:   {parsed}\n"
        f"  python: {list(status.FLOW)}")


def test_settled_agrees_apart_from_the_retired_value():
    """SETTLED decides which rows drop out of the working list.

    The two sides derive it differently on purpose - Rust from a per-status
    flag, Python from a literal tuple - so this compares the RESULT rather than
    the spelling.
    """
    rust = {value for value, settled, _ in _flow_entries() if settled}
    python = set(status.SETTLED)
    assert python - rust == {"closed"}, (
        "python SETTLED carries something rust does not, beyond the retired "
        f"'closed': {python - rust - {'closed'}}")
    assert rust - python == set(), f"rust settles statuses python does not: {rust - python}"


def test_proves_applied_is_every_status_with_a_rung():
    """A status with a rung is one that could only be reached by applying.

    This is what the "you applied to N of these" warning counts before a bulk
    removal, so a status missing from it means somebody deletes an application
    record without being told.
    """
    rust = {value for value, _, rung in _flow_entries() if rung is not None}
    assert set(status.PROVES_APPLIED) == rust, (
        "python PROVES_APPLIED and the rust rungs disagree.\n"
        f"  only in rust:   {rust - set(status.PROVES_APPLIED)}\n"
        f"  only in python: {set(status.PROVES_APPLIED) - rust}")


def test_pass_is_the_one_status_that_is_settled_without_ever_applying():
    """The asymmetry worth stating: settled and applied are independent.

    Passing on a job closes it out having sent nothing, which is why Pass has no
    rung; every other settled status is the end of an application that was made.
    """
    settled_without_rung = {
        value for value, settled, rung in _flow_entries()
        if settled and rung is None}
    assert settled_without_rung == {"pass"}


@pytest.mark.parametrize("values", [status.SETTLED, status.PROVES_APPLIED, status.FLOW])
def test_a_status_list_becomes_bind_markers_not_text_in_the_statement(values):
    """The engine builds its IN clauses from markers and passes the values as
    parameters, so nothing about a status value can reach the SQL parser. The
    marker count has to match the tuple exactly or the query fails at bind time
    rather than returning wrong rows."""
    assert status.placeholders(values).count("?") == len(values)
    assert status.placeholders(("a", "b")) == "?, ?"


# The documents a reader is handed. cli.py needs no entry here: its status help
# is built from status.SETTLED at parse time, so it cannot say a retired value.
DOCS = ("README.md", "COLLECTORS.md", "BUILDING.md", "tests/README.md")

ROOT = Path(__file__).resolve().parents[1]

RETIRED = tuple(old for old, _ in status.RENAMES)


@pytest.mark.parametrize("name", DOCS)
def test_no_document_offers_a_status_the_app_renamed_away(name):
    """Prose has no migration to save it.

    A renamed status is rewritten in the database on open, so the old value
    disappears from the app - but a document naming it goes on offering a word
    the app no longer uses. COLLECTORS.md is the sharp case: it is the
    published contract, so a collector author writes what it says.

    Every one of these documents said `denied` for as long as `no_offer`
    existed, and all six gates passed the whole time. Nothing compared prose
    against the vocabulary until this test.
    """
    text = (ROOT / name).read_text(encoding="utf-8")
    renames = dict(status.RENAMES)
    for old in RETIRED:
        for line_no, line in enumerate(text.splitlines(), start=1):
            if not re.search(rf"\b{re.escape(old)}\b", line, re.IGNORECASE):
                continue
            # TELLING SOMEBODY TO STOP USING IT IS NOT OFFERING IT. A line
            # that marks the word legacy and names its replacement passes this
            # test's own message on to whoever is writing a collector.
            # Anything else is the lapse this guards.
            marks_it_legacy = (
                "legacy" in line.lower()
                and renames[old].lower() in line.lower())
            assert marks_it_legacy, (
                f"{name}:{line_no} names '{old}', which was renamed to "
                f"'{renames[old]}' and no longer exists in the app.\n"
                f"  {line.strip()}\n"
                "  A document may name it only on a line that calls it legacy "
                "and names what replaced it.")


def test_the_documents_are_there_and_the_search_would_find_a_lapse():
    """A positive control for the test above.

    A missing file or a pattern that matches nothing would make every check
    above pass in silence, which is the failure mode this whole file is written
    against.
    """
    assert RETIRED, "status.RENAMES is empty - the check above is vacuous"
    for name in DOCS:
        path = ROOT / name
        assert path.is_file(), f"{path} is gone - its check is vacuous"
        assert path.read_text(encoding="utf-8").strip(), f"{path} is empty"
    # The search finds the thing it is looking for when it IS present.
    sample = f"a status of {RETIRED[0].upper()} means something"
    assert re.search(rf"\b{re.escape(RETIRED[0])}\b", sample, re.IGNORECASE)


def test_both_halves_spell_a_cleared_status_the_same_way():
    """`job_status_log` is shared, and status.py's docstring states what a
    clear looks like in it: "A null 'to' means the status was cleared".

    The desktop wrote an empty string instead, so one table carried two
    spellings of one event depending on which half the person clicked in, and
    an exported history said `to: ""` for a desktop clear against `to: null`
    for a CLI one.

    Nothing observable differed - prune treats both as touched and a re-import
    clears the status either way - which is exactly why it needed a test
    rather than a bug report. A contract only one side keeps is discovered by
    the code that assumed it.

    Read off the Rust source, the same way the refresh anchors are compared.
    """
    import re
    from pathlib import Path

    rust = (Path(__file__).resolve().parent.parent
            / "desktop" / "src" / "db.rs").read_text(encoding="utf-8")

    # The column list gained set_by on 2026-09-05, so the pattern matches the
    # leading columns and allows more after them rather than pinning the exact
    # tuple - what this test is about is the NULL in the status position, not
    # how many columns the insert carries.
    inserts = re.findall(
        r"INSERT INTO job_status_log \(key, status, note, at[^)]*\)\s*\\?\s*"
        r"VALUES \(\?1,\s*([^,]+),", rust)
    assert inserts, "the desktop's clear_status insert was renamed or reshaped"
    for value in inserts:
        assert value.strip() == "NULL", (
            f"the desktop writes {value.strip()!r} for a cleared status; the "
            f"engine writes NULL and status.py documents null as the cleared "
            f"marker")


DATE_RS = Path(__file__).resolve().parents[1] / "desktop/src/date.rs"


def test_both_halves_stamp_time_in_the_same_shape():
    """One column, one meaning.

    job_status.updated and job_status_log.at are written by BOTH halves. The
    desktop moved to the local wall clock with its offset on 2026-09-05 and the
    engine was briefly left on UTC, which would have meant two kinds of stamp
    in one column - the same class of drift this file already guards for the
    status vocabulary.

    Checked by SHAPE rather than by value: the two are different languages and
    cannot be run against each other here, but "does it carry an offset, and is
    it local" is visible in both.
    """
    assert DATE_RS.is_file(), f"{DATE_RS} is gone - this check would be vacuous"
    rust = DATE_RS.read_text(encoding="utf-8")

    # The engine's clock, as a value.
    stamp = status.now_iso()
    assert stamp[10] == "T", stamp
    assert re.search(r"[+-]\d{2}:\d{2}$", stamp), (
        f"the engine wrote a stamp with no offset: {stamp}")
    assert not stamp.endswith("+00:00") or _really_utc_here(), (
        "the engine wrote UTC on a machine that is not on UTC - see now_iso")

    # The desktop's clock, as source: it applies the offset it was given and
    # formats a signed suffix. A bare format string would be the drift.
    body = rust.split("pub fn now_iso() -> String {")[1].split("\n}")[0]
    assert "local_offset()" in body, (
        "desktop now_iso no longer reads the local offset - it is writing UTC "
        "while the engine writes local")
    assert "+00:00" not in body, (
        "desktop now_iso hard-codes a UTC suffix again")


def _really_utc_here() -> bool:
    """True only if this machine genuinely sits on UTC.

    Without this the assertion above would fire on a UTC build agent, which is
    a correct stamp rather than the drift being looked for.
    """
    return datetime.now().astimezone().utcoffset() == timedelta(0)
