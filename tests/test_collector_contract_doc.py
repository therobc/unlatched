"""COLLECTORS.md is a published interface. This is what stops it drifting.

A CONTRACT DOCUMENT IS A MECHANISM THAT PRODUCES CONFIDENT OUTPUT, and the
worst thing it can do is go on producing it about the wrong thing. Nothing
about a number in prose fails when the constant behind it moves - a stranger
implements against a limit that has not existed for months, and the first sign
is their file being refused.

So every quantity the document states out loud is asserted here against the
code it describes. These are not tests of the document's prose; they are tests
that a specific claim in it is still true.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from unlatched import cli, importer

DOC = Path(__file__).resolve().parent.parent / "COLLECTORS.md"


@pytest.fixture(scope="module")
def text():
    return DOC.read_text(encoding="utf-8")


def test_the_document_ships_with_the_app():
    """It is in app/, which is the tree that is published. A contract living
    only in a ticket is one no adopter can read."""
    assert DOC.exists(), f"{DOC} is the published collector contract"


def test_the_size_ceiling_it_states_is_the_one_enforced(text):
    assert f"({importer.MAX_HANDOFF_BYTES // 1048576} MB)" in text


def test_the_staleness_threshold_it_states_is_the_one_used(text):
    assert f"{cli.STALE_HANDOFF_HOURS} hours old" in text


def test_the_contract_version_it_documents_is_the_one_read(text):
    assert f'"version": {importer.CONTRACT_VERSION}' in text


def test_the_id_rule_it_states_is_the_one_checked(text):
    """1-32 characters of a-z, 0-9, underscore or hyphen. The document says so
    in words; check_collector_id is what decides."""
    assert "1-32 characters" in text
    assert importer.check_collector_id("a" * 32) == "a" * 32
    with pytest.raises(importer.BadCollectorIdError):
        importer.check_collector_id("a" * 33)
    with pytest.raises(importer.BadCollectorIdError):
        importer.check_collector_id("has:colon")


def test_every_apply_kind_it_names_is_one_the_importer_accepts(text):
    for kind in importer.APPLY_KINDS:
        assert f"`{kind}`" in text, f"the document should explain {kind}"


def test_every_column_in_its_table_is_one_the_template_emits(text):
    """The table and the template are two descriptions of the same list, and
    the template is the one somebody actually fills in."""
    for column in importer.TEMPLATE_COLUMNS:
        assert f"| `{column}` |" in text, f"{column} is missing from the table"


def test_every_command_it_tells_somebody_to_run_exists(text):
    """The commands in the examples, checked against the parser rather than
    against my memory of it. A document whose first instruction fails teaches
    an adopter to distrust the rest."""
    parser = cli.build_parser()
    for argv in (["ingest", "--template"],
                 ["ingest", "--check", "jobs.csv"],
                 ["ingest", "--collector", "linkedin"]):
        # Both halves matter: the parser has to accept it, AND the document has
        # to be telling somebody to run that exact thing.
        parser.parse_args(argv)
        assert f"unlatched {argv[0]} {argv[1]}" in text
    parser.parse_args(["ingest"])
    assert "unlatched ingest  " in text, "the pull-all form is documented too"


def test_the_two_defaults_it_documents_are_the_real_defaults(text):
    """`we_may_refetch` and `pushes_closures` both default to false, and the
    document's table says so. Either one wrong in the other direction is a
    collector author assuming a protection they do not have."""
    from unlatched import collectors

    assert collectors.DEFAULTS["we_may_refetch"] is False
    assert collectors.DEFAULTS["pushes_closures"] is False
    assert "| `we_may_refetch` | `false` |" in text
    assert "| `pushes_closures` | `false` |" in text


def test_an_empty_schedule_still_means_every_refresh(text):
    """The one place this document departs from the ticket that specified it.
    If the default is ever flipped, this fails and the document is corrected
    with it rather than after somebody's daily pull stops."""
    from datetime import UTC, datetime

    from unlatched import collectors

    entry = collectors.enabled({"collectors": [
        {"id": "x", "path": "C:/nowhere/x.json"}]})[0]

    assert entry.schedule == ()
    # Asked at a moment BEFORE any plausible anchor: a version that had quietly
    # become "on demand only", or one that waited for a time of day, would both
    # answer False here.
    dawn = datetime(2026, 8, 13, 3, 0, tzinfo=UTC)
    assert collectors.scheduled_now(entry, None, dawn) is True
    assert "Empty means every refresh" in text
