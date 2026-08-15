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
    assert len(entries) == 9, f"expected the nine statuses, parsed {len(entries)}"


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
