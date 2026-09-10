"""Why an apply destination is missing is a different question from whether it is.

An empty apply_url meant two OPPOSITE things and nothing could tell them apart:

    "this job applies on the board itself, so no external destination exists"
    "we failed to capture one"

The second is a defect that looks exactly like the first. A sending collector
hit it - a silent empty write - and three runs of rows reached this database
unjoinable with nothing to say so. A row arriving with neither a destination
nor a classification then has to be reportable, which could not be done here
at all: there was nowhere for a classification to live.
"""
from __future__ import annotations

from unlatched import db, importer

BASE = {"url": "https://www.example.com/jobs/view/1", "title": "Analyst",
        "company": "Acme"}


def test_a_stated_easy_apply_is_recorded_as_one(con, cfg):
    importer.import_row(con, cfg, dict(BASE, apply_kind="easy-apply"))
    row = db.get_job(con, "imported:www-example-com-jobs-view-1")
    assert row["apply_kind"] == "easy-apply"
    assert not row["apply_url"]


def test_an_unclassified_row_with_no_destination_stays_unknown(con, cfg):
    """The distinction that matters. This must NOT quietly become 'easy-apply'
    - guessing here would recreate the exact ambiguity the column exists to
    remove, and would mark a capture failure as a job with no external route."""
    importer.import_row(con, cfg, dict(BASE))
    row = db.get_job(con, "imported:www-example-com-jobs-view-1")
    assert row["apply_kind"] == "", "an unknown must stay unknown"


def test_a_real_destination_is_external_even_when_unstated(con, cfg):
    """Inference is safe in this direction only: the row demonstrably HAS an
    external route, whatever the sender declared."""
    importer.import_row(con, cfg, dict(
        BASE, apply_url="https://job-boards.greenhouse.io/acme/jobs/1"))
    row = db.get_job(con, "imported:www-example-com-jobs-view-1")
    assert row["apply_kind"] == "external"


def test_an_invented_classification_is_not_believed(con, cfg):
    """A sender inventing a fourth state should not fail the import, but it must
    not be recorded as fact either."""
    importer.import_row(con, cfg, dict(BASE, apply_kind="maybe-later"))
    row = db.get_job(con, "imported:www-example-com-jobs-view-1")
    assert row["apply_kind"] == ""


def test_a_marker_in_the_destination_is_caught_and_reported(con, cfg):
    """The contract: 'easy-apply' and 'closed' are classifications, NEVER URLs.

    The failure is silent in the worst way. A literal marker is a NON-EMPTY
    value, so it passes every "does this row have a destination" test - and then
    every easy-apply row in the batch carries the SAME string, which is an exact
    dedupe key, and they all fold into one job.
    """
    result = importer.import_all(con, cfg, [
        dict(BASE, url="https://www.example.com/jobs/view/1", apply_url="easy-apply"),
        dict(BASE, url="https://www.example.com/jobs/view/2", apply_url="easy-apply"),
    ])

    assert len(result["markers_in_apply_url"]) == 2
    assert result["imported"] == 2, "the jobs are still imported - one bad field"
    for n in (1, 2):
        row = db.get_job(con, f"imported:www-example-com-jobs-view-{n}")
        assert row["apply_url"] == "", "the marker must not be stored as a URL"
        assert row["apply_kind"] == "easy-apply"


def test_markered_rows_do_not_collapse_into_one_job(con, cfg):
    """The consequence the check exists to prevent, asserted end to end."""
    from unlatched import dupes

    importer.import_all(con, cfg, [
        dict(BASE, url="https://www.example.com/jobs/view/1", apply_url="easy-apply"),
        dict(BASE, url="https://www.example.com/jobs/view/2", apply_url="easy-apply"),
    ])
    assert dupes.find(con) == [], "two separate easy-apply jobs were folded into one"


def test_rows_with_neither_a_destination_nor_a_classification_are_counted(con, cfg):
    """The number they asked for, which nothing could produce before."""
    result = importer.import_all(con, cfg, [
        dict(BASE, url="https://www.example.com/jobs/view/1"),
        dict(BASE, url="https://www.example.com/jobs/view/2", apply_kind="easy-apply"),
        dict(BASE, url="https://www.example.com/jobs/view/3",
             apply_url="https://job-boards.greenhouse.io/acme/jobs/1"),
    ])
    assert result["no_destination_and_unclassified"] == 1


def test_a_clean_batch_reports_no_defects_at_all(con, cfg):
    """A key that only ever appears on failure. If it turned up on every import
    it would be read as noise and stop being looked at."""
    result = importer.import_all(con, cfg, [
        dict(BASE, url="https://www.example.com/jobs/view/2", apply_kind="easy-apply"),
    ])
    assert "markers_in_apply_url" not in result
    assert "no_destination_and_unclassified" not in result


def test_easy_apply_rows_still_never_match_each_other(con, cfg):
    """Unchanged and worth re-asserting here: the application never leaves the
    board, so no ATS row can duplicate it and two of them are not evidence of
    anything."""
    from unlatched import dupes

    importer.import_all(con, cfg, [
        dict(BASE, url="https://www.example.com/jobs/view/1", apply_kind="easy-apply"),
        dict(BASE, url="https://www.example.com/jobs/view/2", apply_kind="easy-apply"),
    ])
    assert dupes.find(con) == []
