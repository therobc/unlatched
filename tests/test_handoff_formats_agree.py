"""CSV and JSON produce identical results from equivalent input.

"If the two formats disagree, one of them is wrong and users will find out the
hard way."

THE COMPARISON IS BETWEEN TWO REAL IMPORTS, not between two parser outputs.
Reading the same fields out of both files would only prove the readers agree
about the file; what matters is whether the ROWS ON THE BOARD end up the same,
which runs everything after the reader too - key derivation, screening, the
apply_kind inference, closures applied in the right order.

The two go into SEPARATE HOMES under the SAME collector id. Same id because the
key namespace is part of what is being compared; separate homes because two
imports of the same postings into one board would be one import and one no-op.
"""
from __future__ import annotations

import csv
import io
import json
import sqlite3

from unlatched import cli, db

# One of each interesting shape, and the awkward ones on purpose: a
# multi-paragraph description, an easy-apply row with no destination, a row
# carrying a status the person already set, and a closure.
JOBS = [
    {"key": "a1", "url": "https://boards.greenhouse.io/acme/jobs/1",
     "title": "Support Analyst", "company": "Acme", "location": "Remote, US",
     "posted": "2026-08-01",
     "description": "First paragraph, with a comma.\n\nSecond one with "
                    '"quotes" in it.',
     "apply_url": "https://boards.greenhouse.io/acme/jobs/1/apply",
     "apply_kind": "external"},
    {"key": "a2", "url": "https://www.linkedin.com/jobs/view/222",
     "title": "Operations Analyst", "company": "Nimbus", "location": "Austin, TX",
     "posted": "2026-08-03", "description": "Short one.",
     "apply_kind": "easy-apply"},
    {"key": "a3", "url": "https://boards.greenhouse.io/acme/jobs/3",
     "title": "Data Analyst", "company": "Acme", "location": "Remote, US",
     "posted": "2026-08-05", "description": "Another posting.",
     "status": "applied", "applied_at": "2026-08-06T09:00:00+00:00"},
]
CLOSED = ["a1"]
STAMP = "2026-08-13T08:00:00+00:00"

COLUMNS = ["key", "url", "title", "company", "location", "posted",
           "description", "apply_url", "apply_kind", "status", "applied_at",
           "closed", "generated_at"]

# What an imported row is, as far as anybody using the board is concerned.
COMPARED = ("key", "source", "title", "location", "url", "apply_url",
            "apply_kind", "posted_at", "description", "delisted_at",
            "qualified", "verdict", "score")


class Args:
    def __init__(self, home):
        self.home = str(home)
        self.json = True


def write_json(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"version": 1, "generated_at": STAMP,
                                "jobs": JOBS, "closed": CLOSED}),
                    encoding="utf-8")
    return path


def write_csv(path):
    """The same payload as a spreadsheet: closures are rows with closed=TRUE."""
    path.parent.mkdir(parents=True, exist_ok=True)
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=COLUMNS, lineterminator="\n")
    writer.writeheader()
    for job in JOBS:
        row = {c: job.get(c, "") for c in COLUMNS}
        row["closed"] = "FALSE"
        row["generated_at"] = STAMP
        writer.writerow(row)
    for key in CLOSED:
        blank = dict.fromkeys(COLUMNS, "")
        blank["key"] = key
        blank["closed"] = "TRUE"
        writer.writerow(blank)
    path.write_text(out.getvalue(), encoding="utf-8")
    return path


def landed(home, path):
    cfg = {"collectors": [{"id": "partner", "path": str(path)}]}
    result = cli.ingest_pending(Args(home), cfg, on_demand=True)
    con = db.connect(home)
    try:
        con.row_factory = sqlite3.Row
        # SELECT *, then narrow in Python. Composing the column list into the
        # SQL reads as an injection whether or not COMPARED is a constant, and
        # the row is small enough that filtering here costs nothing.
        rows = [{k: dict(r)[k] for k in COMPARED} for r in con.execute(
            "SELECT * FROM jobs ORDER BY key")]
        statuses = [dict(r) for r in con.execute(
            "SELECT key, status FROM job_status ORDER BY key")]
    finally:
        con.close()
    return result, rows, statuses


def test_the_same_payload_in_either_format_lands_the_same_board(tmp_path,
                                                                monkeypatch):
    json_home = tmp_path / "as_json"
    csv_home = tmp_path / "as_csv"

    monkeypatch.setenv("UNLATCHED_HOME", str(json_home))
    from_json = landed(json_home, write_json(tmp_path / "h.json"))
    monkeypatch.setenv("UNLATCHED_HOME", str(csv_home))
    from_csv = landed(csv_home, write_csv(tmp_path / "h.csv"))

    result_json, rows_json, status_json = from_json
    result_csv, rows_csv, status_csv = from_csv

    # THE COUNTS FIRST, because a difference here says which half disagrees.
    assert result_json["imported"] == result_csv["imported"] == 3
    assert result_json["closed"] == result_csv["closed"] == 1

    # THEN THE ROWS, field by field. Score and verdict are in the comparison on
    # purpose: they come out of screening, which runs after the reader, so this
    # catches a format difference that only shows up downstream.
    assert rows_json == rows_csv

    # AND THE STATUS THE SENDER CARRIED, which is the one thing an import
    # exists to preserve.
    #
    # a1 IS THE CLOSURE, and it carries a status now. A collector's closure used
    # to set delisted_at and stop, which left the row reading "not set" for
    # ever; it now takes the same rule as every other way a posting is found
    # gone - the app's own `closed` where the person never decided anything.
    # Asserted HERE rather than in a test of its own because the point of this
    # file is that the two formats land the same board, and a closure that
    # wrote a status in JSON and not in CSV would be exactly that kind of drift.
    assert status_json == status_csv == [{"key": "partner:a1",
                                          "status": "closed"},
                                         {"key": "partner:a3",
                                          "status": "applied"}]


def test_the_closure_reaches_the_same_row_either_way(tmp_path, monkeypatch):
    """A POSITIVE CONTROL for the comparison above. Two boards that were both
    empty, or both missed the closure, would compare equal and prove nothing -
    so this asserts the closure actually landed on a1 and only on a1."""
    for name, writer in (("as_json", write_json), ("as_csv", write_csv)):
        home = tmp_path / name
        monkeypatch.setenv("UNLATCHED_HOME", str(home))
        suffix = "json" if name == "as_json" else "csv"
        _, rows, _ = landed(home, writer(tmp_path / f"c.{suffix}"))

        closed = [r["key"] for r in rows if r["delisted_at"]]
        assert closed == ["partner:a1"], f"{name} closed {closed}"
        assert len(rows) == 3, f"{name} landed {len(rows)} rows"


def test_a_broken_file_is_reported_row_by_row_in_both_formats(tmp_path):
    """It says EVERY bad row rather than the first.

    A checker that stops at the first problem makes fixing a file an
    n-round-trip exercise, which is how a collector author gives up.
    """
    from unlatched import importer

    bad_json = tmp_path / "bad.json"
    bad_json.write_text(json.dumps({"version": 1, "jobs": [
        {"url": "https://x.example/1", "title": ""},
        {"title": "Fine", "url": "https://x.example/2"},
        {"title": "Marker", "url": "https://x.example/3",
         "apply_url": "easy-apply"},
        {"title": "No identity"},
    ]}), encoding="utf-8")

    report = importer.check_rows(bad_json)
    rows = [p["row"] for p in report["problems"]]

    # Three problems, on three DIFFERENT rows, and the good one is not named.
    assert sorted(set(rows)) == [1, 3, 4]
    assert report["jobs"] == 4, "checking must not drop the rows it complains about"
