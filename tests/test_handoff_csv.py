"""CSV is the documented handoff format. JSON stays for collector authors.

The first user, 2026-08-12, on why: the "Who is Jason?" argument - ordinary people do not
know what JSON is, and a format somebody cannot read is a format they cannot
produce. This app already EXPORTS csv, so accepting it inbound also gives
round-trip symmetry: export, edit in Excel, hand it back.

I ARGUED AGAINST CSV EARLIER AND WAS AIMING AT THE WRONG RISK. read_rows' old
docstring said a multi-paragraph description "is where CSV goes wrong quietly".
That is true of HAND-ROLLED writers and false of Excel and Python's csv module,
both of which quote embedded newlines and quotes correctly. The first test below
is that claim measured rather than repeated.
"""
from __future__ import annotations

import csv
import io
import json

import pytest

from unlatched import cli, config, db, importer

MESSY = ('First paragraph, with a comma.\n\n'
         'Second paragraph with "quoted" words and a\ttab.\n'
         'Third line; ends here.')


def write_csv(path, rows, columns=None):
    columns = columns or ["url", "title", "company", "description",
                          "closed", "generated_at"]
    path.parent.mkdir(parents=True, exist_ok=True)
    with io.StringIO() as out:
        writer = csv.DictWriter(out, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({c: row.get(c, "") for c in columns})
        path.write_text(out.getvalue(), encoding="utf-8")
    return path


def a_job(n, **extra):
    return {"url": f"https://boards.greenhouse.io/acme/jobs/{n}",
            "title": f"Support Analyst {n}", "company": "Acme", **extra}


# ------------------------------------------------------------- reading ----

def test_a_multi_paragraph_description_survives_the_round_trip(tmp_path):
    """THE OBJECTION, MEASURED. Commas, quotes, tabs and blank lines all go
    through csv untouched - which is the whole reason the earlier position
    against CSV does not hold."""
    path = write_csv(tmp_path / "h.csv", [a_job(1, description=MESSY)])

    rows = importer.read_rows(path)

    assert len(rows) == 1
    assert rows[0]["description"] == MESSY


def test_a_spreadsheets_capitals_and_spaces_are_the_same_columns(tmp_path):
    """The person producing this is typing into Excel. Refusing their file over
    a capital letter would be the format's first act."""
    path = write_csv(tmp_path / "h.csv", [{"URL": "https://x.example/1",
                                           "Job Title": "Analyst",
                                           "Apply URL": "https://x.example/a"}],
                     columns=["URL", "Job Title", "Apply URL"])

    row = importer.read_rows(path)[0]

    assert row["url"] == "https://x.example/1"
    assert row["apply_url"] == "https://x.example/a"
    # "Job Title" is not a column this app knows; it normalises to job_title
    # and is ignored, exactly like an unknown JSON field.
    assert "title" not in row


def test_excels_byte_order_mark_does_not_eat_the_first_column(tmp_path):
    """THE LIKELIEST WAY A CORRECT FILE READS AS EMPTY. Excel writes a BOM, it
    lands on the first header name, and `url` arrives as an invisible different
    string - so the column silently is not there."""
    path = tmp_path / "h.csv"
    path.write_text("﻿url,title\nhttps://x.example/1,Analyst\n",
                    encoding="utf-8")

    row = importer.read_rows(path)[0]

    assert row["url"] == "https://x.example/1"


def test_blank_cells_do_not_become_values(tmp_path):
    """A spreadsheet produces a cell for every column of every row. An empty
    `key` stored as "" would read as a key the sender chose, and _key_for would
    take it over the url."""
    path = write_csv(tmp_path / "h.csv", [a_job(1)], columns=["url", "title", "key"])

    row = importer.read_rows(path)[0]

    assert "key" not in row


def test_json_is_still_read(tmp_path):
    """The positive control for every CSV test here: the format collector
    authors already write must keep working, and it is chosen by CONTENT rather
    than by the file's name."""
    path = tmp_path / "h.txt"
    path.write_text(json.dumps({"version": 1, "jobs": [a_job(7)]}),
                    encoding="utf-8")

    rows = importer.read_rows(path)

    assert [r["title"] for r in rows] == ["Support Analyst 7"]


def test_a_handoff_bigger_than_the_ceiling_is_refused(tmp_path, monkeypatch):
    """A published interface means the file is whatever somebody else's program
    wrote, and both readers pull the whole thing into memory before anything
    here can object."""
    monkeypatch.setattr(importer, "MAX_HANDOFF_BYTES", 64)
    path = write_csv(tmp_path / "h.csv", [a_job(1, description="x" * 200)])

    with pytest.raises(importer.HandoffTooLargeError):
        importer.read_rows(path)


# ------------------------------------------------------------ closures ----

def test_a_closed_row_is_a_closure_and_not_a_job(tmp_path):
    """the first user's point: a `closed` column carries them in the same file, which is
    arguably tidier than JSON's separate list. Importing such a row as well
    would put a posting known to be dead on the board as a live one."""
    path = write_csv(tmp_path / "h.csv", [
        a_job(1),
        a_job(2, closed="TRUE"),
    ])

    assert [r["title"] for r in importer.read_rows(path)] == ["Support Analyst 1"]
    assert len(importer.read_closures(path)) == 1


@pytest.mark.parametrize("yes", ["TRUE", "true", "Yes", "y", "1"])
def test_the_ways_a_person_writes_yes(yes, tmp_path):
    path = write_csv(tmp_path / "h.csv", [a_job(1, closed=yes)])

    assert importer.read_closures(path)


@pytest.mark.parametrize("no", ["", "FALSE", "no", "0", "maybe"])
def test_and_the_ways_they_do_not(no, tmp_path):
    """The negative control. Without it, a version that treated every row as
    closed would pass all five cases above."""
    path = write_csv(tmp_path / "h.csv", [a_job(1, closed=no)])

    assert importer.read_closures(path) == []


def test_a_closure_can_be_identified_by_url_alone(tmp_path):
    """Somebody deleting rows in Excel and ticking `closed` will not have a key
    column. The key is derived the same way the import would derive it, so the
    two halves agree about what row this is."""
    from unlatched.manual import stable_id

    url = "https://boards.greenhouse.io/acme/jobs/9"
    path = write_csv(tmp_path / "h.csv", [{"url": url, "title": "Analyst",
                                           "closed": "TRUE"}])

    assert importer.read_closures(path) == [stable_id(url)]


def test_the_generated_at_column_is_read(tmp_path):
    """CSV has nowhere else to put it, and without it a dead collector's file
    still parses perfectly and reads as healthy."""
    path = write_csv(tmp_path / "h.csv",
                     [a_job(1, generated_at="2026-08-13T08:00:00+00:00")])

    assert importer.read_generated_at(path) == "2026-08-13T08:00:00+00:00"


# ------------------------------------------------- through the pipeline ----

class Args:
    def __init__(self, home):
        self.home = str(home)
        self.json = True


def test_a_csv_handoff_imports_and_closes_in_one_pass(home):
    """END TO END, through the same ingest path a JSON handoff uses: one live
    row lands, one closure is applied to a row already on the board."""
    cfg = {"collectors": [{"id": "partner", "path": str(home / "h.csv")}]}
    write_csv(home / "h.csv", [
        a_job(1),
        a_job(2, closed="TRUE"),
    ])
    # The row the closure is about has to already exist to be closed.
    first = write_csv(home / "seed.csv", [a_job(2)])
    seed_cfg = {"collectors": [{"id": "partner", "path": str(first)}]}
    cli.ingest_pending(Args(home), seed_cfg, on_demand=True)

    result = cli.ingest_pending(Args(home), cfg, on_demand=True)

    assert result["imported"] == 1
    assert result["closed"] == 1
    con = db.connect(home)
    try:
        # Found by URL rather than by rebuilding the key here: a test that
        # derives the key the same way the code does would agree with itself
        # about an identity that was wrong in both places.
        row = con.execute(
            "SELECT key, source, delisted_at FROM jobs WHERE url = ?",
            (a_job(2)["url"],)).fetchone()
        assert row is not None, "the closed posting should still be on the board"
        assert row["delisted_at"], "and marked taken down"
        assert row["source"] == "partner"
    finally:
        con.close()


# --------------------------------------------------- template and check ----

def test_the_template_this_app_emits_passes_its_own_checker(tmp_path):
    """A published format whose own example does not validate is one nobody can
    trust. This is that circularity closed."""
    path = tmp_path / "template.csv"
    path.write_text(importer.template_csv("2026-08-13T08:00:00+00:00"),
                    encoding="utf-8")

    report = importer.check_rows(path)

    assert report["format"] == "csv"
    assert report["jobs"] == 1
    assert report["problems"] == []


def test_check_names_the_row_a_spreadsheet_shows(tmp_path):
    """The header is row 1, so the first job is row 2 - the number in the
    message has to be the one on screen or it sends somebody to the wrong
    line."""
    path = write_csv(tmp_path / "h.csv", [
        a_job(1),
        {"url": "https://x.example/2", "title": ""},
    ])

    problems = importer.check_rows(path)["problems"]

    assert [p["row"] for p in problems] == [3]
    assert "no title" in problems[0]["problem"]


def test_a_closure_above_a_bad_row_does_not_shift_the_number(tmp_path):
    """THE OFF-BY-N THIS CATCHES. Closures are rows in a CSV too. Numbering the
    JOBS rather than the FILE would point one line too high the moment a closed
    row appeared above the problem."""
    path = write_csv(tmp_path / "h.csv", [
        a_job(1, closed="TRUE"),
        a_job(2, closed="TRUE"),
        {"url": "https://x.example/9", "title": ""},
    ])

    assert [p["row"] for p in importer.check_rows(path)["problems"]] == [4]


def test_check_reports_a_misspelled_column_without_calling_it_fatal(tmp_path):
    """Extra columns are ignored by design - the sender's schema is richer than
    ours. A MISSPELLED one looks exactly like an extra one, which is the whole
    reason this is reported rather than silent."""
    path = write_csv(tmp_path / "h.csv", [dict(a_job(1), compnay="Acme")],
                     columns=["url", "title", "compnay"])

    problems = importer.check_rows(path)["problems"]

    assert any("compnay" in p["problem"] for p in problems)
    # And the row still imports, because it is not an error.
    assert importer.read_rows(path)[0]["title"]


def test_check_catches_a_classification_in_the_destination(tmp_path):
    """the collector author asked to be told if 'easy-apply' ever appears in apply_url. This
    is where a collector author finds out before it reaches a board."""
    path = write_csv(tmp_path / "h.csv",
                     [{"url": "https://x.example/1", "title": "Analyst",
                       "apply_url": "easy-apply"}],
                     columns=["url", "title", "apply_url"])

    problems = importer.check_rows(path)["problems"]

    assert any("easy-apply" in p["problem"] for p in problems)


def test_check_writes_nothing(home):
    """A dry run that imported would be worse than useless: somebody validating
    a draft file would find its rows on their board."""
    import argparse

    path = write_csv(home / "h.csv", [a_job(1)])
    config.save(config.defaults(), home)
    args = argparse.Namespace(home=str(home), json=False, force=False,
                              collector=None, template=False, check=str(path))

    assert cli.cmd_ingest(args) == 0
    con = db.connect(home)
    try:
        assert con.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0
    finally:
        con.close()


def test_check_exits_nonzero_when_there_is_something_to_fix(home):
    """So a collector author can put it in their own build and have it mean
    something."""
    import argparse

    path = write_csv(home / "h.csv", [{"url": "https://x.example/1", "title": ""}])
    config.save(config.defaults(), home)
    args = argparse.Namespace(home=str(home), json=False, force=False,
                              collector=None, template=False, check=str(path))

    assert cli.cmd_ingest(args) == 1
