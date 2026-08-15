"""Getting the pipeline out.

Two applications were lost because status lived in exactly one place with no
export path. The tests that matter here are about what the file must NOT omit:
a filter is not a backup, and the rows a person can no longer reconstruct from
the web are precisely the removed and taken-down ones.
"""
from __future__ import annotations

import csv

from unlatched import db, export, manual, status


def add(con, cfg, key_url, title="Support Analyst", company="Acme"):
    return manual.add(con, cfg, key_url, title=title, company=company,
                      no_fetch=True)["key"]


def read_back(path):
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def test_a_job_and_what_the_person_did_about_it_survive(con, cfg, tmp_path):
    key = add(con, cfg, "https://boards.greenhouse.io/acme/jobs/1")
    status.set_status(con, key, "applied")

    out = tmp_path / "pipeline.csv"
    assert export.write_csv(con, out) == 1

    row = read_back(out)[0]
    assert row["company"] == "Acme"
    assert row["title"] == "Support Analyst"
    assert row["status"] == "applied"
    assert row["url"] == "https://boards.greenhouse.io/acme/jobs/1"
    assert row["applied_on"], "the date they applied is the one an employer asks for"


def test_the_history_survives_not_just_the_current_status(con, cfg, tmp_path):
    """The append-only log is what the funnel and response rate are read from.
    A snapshot of the current status would lose that a job was applied to
    before it was denied - which is the whole record of the effort."""
    key = add(con, cfg, "https://boards.greenhouse.io/acme/jobs/2")
    status.set_status(con, key, "applied")
    status.set_status(con, key, "interviewed")
    status.set_status(con, key, "denied")

    out = tmp_path / "pipeline.csv"
    export.write_csv(con, out)
    row = read_back(out)[0]

    assert row["status"] == "denied"
    for step in ("applied", "interviewed", "denied"):
        assert step in row["history"], f"{step} missing from {row['history']!r}"
    assert row["history"].index("applied") < row["history"].index("denied"), \
        "the order is the point - it is a history, not a set"


def test_a_job_denied_after_applying_still_reports_when_it_was_applied_to(con, cfg, tmp_path):
    key = add(con, cfg, "https://boards.greenhouse.io/acme/jobs/3")
    status.set_status(con, key, "applied")
    status.set_status(con, key, "denied")

    out = tmp_path / "pipeline.csv"
    export.write_csv(con, out)
    assert read_back(out)[0]["applied_on"], \
        "read from the LOG, not the current status"


def test_removed_and_taken_down_rows_are_in_the_backup(con, cfg, tmp_path):
    """The rows that cannot be reconstructed from the web are exactly the ones
    a filter would drop. A backup that honours the current view is not one."""
    add(con, cfg, "https://boards.greenhouse.io/acme/jobs/4", title="Kept")
    removed = add(con, cfg, "https://boards.greenhouse.io/acme/jobs/5", title="Removed")
    gone = add(con, cfg, "https://boards.greenhouse.io/acme/jobs/6", title="Taken down")

    status.set_status(con, removed, "applied")
    db.retire(con, [removed], at="2026-08-08T10:00:00")
    con.execute("UPDATE jobs SET delisted_at = ? WHERE key = ?",
                ("2026-08-08T11:00:00", gone))
    con.commit()

    out = tmp_path / "pipeline.csv"
    assert export.write_csv(con, out) == 3

    by_title = {row["title"]: row for row in read_back(out)}
    assert set(by_title) == {"Kept", "Removed", "Taken down"}
    assert by_title["Removed"]["removed_on"] == "2026-08-08T10:00:00"
    assert by_title["Removed"]["status"] == "applied", \
        "removing a row from the lists never removed what they did about it"
    assert by_title["Taken down"]["taken_down_on"] == "2026-08-08T11:00:00"


def test_a_job_with_no_status_reads_as_not_set_rather_than_blank(con, cfg, tmp_path):
    add(con, cfg, "https://boards.greenhouse.io/acme/jobs/7")
    out = tmp_path / "pipeline.csv"
    export.write_csv(con, out)
    assert read_back(out)[0]["status"] == "not set"


def test_an_empty_search_still_writes_a_usable_file(con, cfg, tmp_path):
    """A backup taken before anything was collected must not be a zero-byte
    file that reads as a failed export."""
    out = tmp_path / "pipeline.csv"
    assert export.write_csv(con, out) == 0
    with out.open(newline="", encoding="utf-8-sig") as handle:
        header = next(csv.reader(handle))
    assert header == list(export.COLUMNS)


def test_the_file_opens_with_accented_names_intact(con, cfg, tmp_path):
    """utf-8-sig so a spreadsheet reads it correctly. Mojibake would land on
    the person least equipped to work out why."""
    add(con, cfg, "https://boards.greenhouse.io/acme/jobs/8", company="Sociéte Générale")
    out = tmp_path / "pipeline.csv"
    export.write_csv(con, out)
    assert read_back(out)[0]["company"] == "Sociéte Générale"
