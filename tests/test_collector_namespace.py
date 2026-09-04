"""Rows from different collectors stay apart, and say where they came from.

An earlier change. Two things were true before this and both are traps
under more than one collector:

  * every imported row carried the constant source "imported", so a second
    collector's rows were indistinguishable from the first's
  * keys were whatever the sender supplied, so two collectors reading the same
    posting derive the same id and the second silently OVERWRITES the first
"""
from __future__ import annotations

import pytest

from unlatched import importer

# The same posting, as two different collectors would report it. Same URL,
# because that is exactly the case that collided: both derive their key from it.
POSTING = {"title": "Support Analyst", "company": "Acme",
           "url": "https://www.example.com/jobs/view/4400330022"}


def test_two_collectors_reporting_the_same_posting_do_not_overwrite_each_other(
        con, cfg):
    first = importer.import_row(con, cfg, dict(POSTING), collector="myboard")
    second = importer.import_row(con, cfg, dict(POSTING), collector="othertool")

    assert first["key"] != second["key"], (
        "both collectors derived the same key from the same URL, so one "
        "overwrote the other - the exact collision the namespace exists for")
    rows = con.execute(
        "SELECT key, source FROM jobs ORDER BY key").fetchall()
    assert len(rows) == 2
    assert {r["source"] for r in rows} == {"myboard", "othertool"}
    assert all(r["key"].startswith(f"{r['source']}:") for r in rows)


def test_a_collector_cannot_claim_another_collectors_namespace(con, cfg):
    """The prefix is imposed here, not taken from the file.

    A file that supplies "othertool:123" while being imported as the myboard
    collector is trying, deliberately or not, to write into othertool's rows.
    """
    row = dict(POSTING, key="othertool:123")
    stored = importer.import_row(con, cfg, row, collector="myboard")

    # KEPT WHOLE INSIDE myboard's NAMESPACE rather than rewritten to
    # "myboard:123": the sender's id is theirs and may have structure, and
    # what matters here is only that it cannot land in othertool's rows.
    assert stored["key"] == "myboard:othertool:123"
    assert not stored["key"].startswith("othertool:")
    source = con.execute("SELECT source FROM jobs WHERE key = ?",
                         (stored["key"],)).fetchone()[0]
    assert source == "myboard"


def test_two_ids_that_differ_only_by_the_senders_own_prefix_stay_two_rows(
        con, cfg):
    """A sender whose ids look like "li:998877" and "in:998877" means two
    different postings. Stripping at the first colon would fold them into one
    row and lose whichever arrived first - which is the same silent overwrite
    the namespace exists to prevent, arriving from the other direction."""
    first = importer.import_row(
        con, cfg, dict(POSTING, key="li:998877"), collector="othertool")
    second = importer.import_row(
        con, cfg, dict(POSTING, key="in:998877", title="A different job"),
        collector="othertool")

    assert first["key"] != second["key"]
    assert con.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 2


@pytest.mark.parametrize("bad", ["with:colon", "has space", "", "../escape",
                                 "x" * 33, "myboard/../indeed"])
def test_an_unusable_collector_id_is_refused_rather_than_sanitised(con, cfg, bad):
    """Refused, not quietly cleaned up: a silently rewritten id would put rows
    in a namespace nobody configured, which is worse than an error.

    The colon case is the one that matters most - an id containing one could
    build a prefix that reads as somebody else's namespace.
    """
    with pytest.raises(importer.BadCollectorIdError):
        importer.import_row(con, cfg, dict(POSTING), collector=bad)


def test_case_is_normalised_because_config_is_written_by_hand(con, cfg):
    """"MyBoard" is what a person types. It is accepted and lowercased, and
    the normalised id is what reaches the row - so the source and the key
    prefix agree with each other whatever the config looked like."""
    stored = importer.import_row(con, cfg, dict(POSTING), collector="MyBoard")

    assert stored["key"].startswith("myboard:")
    source = con.execute("SELECT source FROM jobs WHERE key = ?",
                         (stored["key"],)).fetchone()[0]
    assert source == "myboard"


def test_a_sender_still_using_the_old_prefix_updates_the_row_it_already_wrote(
        con, cfg):
    """THE FAILURE THIS PREVENTS IS A SILENT DOUBLING OF THE BOARD.

    rekey.py corrected 410 rows from `manual:` to `imported:` on
    2026-08-13. The collector on the other side still writes `manual:` keys and
    will keep doing so until it is updated - which is not something this app
    controls, and is exactly the situation a published contract has to survive.

    Honouring the sender's prefix would create a SECOND copy of every row
    beside the corrected one. Normalising means the same posting keeps being
    the same row.
    """
    already_here = importer.import_row(
        con, cfg, dict(POSTING, key="imported:www-example-com-jobs-view-4400330022"))

    from_the_sender = importer.import_row(
        con, cfg, dict(POSTING, key="manual:www-example-com-jobs-view-4400330022",
                       title="Support Analyst II"))

    assert from_the_sender["key"] == already_here["key"]
    rows = con.execute("SELECT key, title FROM jobs").fetchall()
    assert len(rows) == 1, "the old prefix created a duplicate of the whole row"
    assert rows[0]["title"] == "Support Analyst II"


def test_a_batch_carries_its_collector_to_every_row(con, cfg):
    rows = [dict(POSTING, url=f"https://example.invalid/{n}", title=f"Role {n}")
            for n in range(3)]
    result = importer.import_all(con, cfg, rows, collector="othertool")

    assert result["imported"] == 3
    sources = {r[0] for r in con.execute("SELECT DISTINCT source FROM jobs")}
    assert sources == {"othertool"}


def test_re_importing_the_same_collectors_run_updates_rather_than_duplicates(
        con, cfg):
    """The namespace must not break the property it sits on top of: the same
    row from the same collector is the same job."""
    importer.import_row(con, cfg, dict(POSTING), collector="myboard")
    importer.import_row(con, cfg, dict(POSTING, title="Support Analyst II"),
                        collector="myboard")

    rows = con.execute("SELECT key, title FROM jobs").fetchall()
    assert len(rows) == 1
    assert rows[0]["title"] == "Support Analyst II"
