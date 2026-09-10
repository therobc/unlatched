"""A key that says which collector wrote the row, with its pipeline attached.

Decided 2026-08-13: the stored data has to reflect what is actually true, and
moving the pipeline to achieve that is the acceptable cost.

The disagreement was real and measured on a live board: 410 rows keyed
`manual:` whose source said `imported`, carrying all 53 job_status rows and all
95 job_status_log rows between them. So the property under test is not "the key
changed" - it is "the key changed AND everything that pointed at it followed".
"""
from __future__ import annotations

import sqlite3

from unlatched import db, rekey


def a_job(con, key, source, title="Support Analyst"):
    con.execute(
        "INSERT INTO jobs (key, source, title, qualified) VALUES (?, ?, ?, 1)",
        (key, source, title))
    con.commit()


def test_a_key_that_disagrees_with_its_source_is_corrected(con, home):
    a_job(con, "manual:myboard-4400330022", "imported")

    result = rekey.apply(con, home)

    assert result["moved"] == 1
    keys = [r[0] for r in con.execute("SELECT key FROM jobs")]
    assert keys == ["imported:myboard-4400330022"]


def test_the_whole_pipeline_moves_with_it(con, home):
    """THE POINT OF THE WHOLE EXERCISE. A renamed job whose status stayed
    behind is worse than the disagreement it fixed: the person's record of
    having applied would be pointing at a row that no longer exists."""
    a_job(con, "manual:job-1", "imported")
    con.executescript("""
        INSERT INTO job_status (key, status, updated)
             VALUES ('manual:job-1', 'applied', '2026-08-01T10:00:00Z');
        INSERT INTO job_status_log (key, status, at)
             VALUES ('manual:job-1', 'applied', '2026-08-01T10:00:00Z');
        INSERT INTO job_note (key, note, at)
             VALUES ('manual:job-1', 'called them', '2026-08-02T10:00:00Z');
        INSERT INTO attachment (key, trust, kind, display_name, added_at)
             VALUES ('manual:job-1', 'mine', 'text', 'resume.txt',
                     '2026-08-01T10:00:00Z');
    """)
    con.commit()

    rekey.apply(con, home)

    for table in ("job_status", "job_status_log", "job_note", "attachment"):
        keys = [r[0] for r in con.execute(f"SELECT key FROM {table}")]  # noqa: S608
        assert keys == ["imported:job-1"], f"{table} was left behind"
    assert rekey.orphans(con) == {}


def test_a_group_still_points_at_the_row_it_was_folded_behind(con, home):
    """jobs.duplicate_of holds a key too. A move that skipped it would leave
    the group pointing at nothing, and the hidden row would come back out on
    the next dedupe pass."""
    a_job(con, "manual:original", "imported")
    a_job(con, "manual:copy", "imported", title="Support Analyst (copy)")
    con.execute("UPDATE jobs SET duplicate_of = 'manual:original' "
                "WHERE key = 'manual:copy'")
    con.commit()

    rekey.apply(con, home)

    folded = con.execute(
        "SELECT duplicate_of FROM jobs WHERE key = 'imported:copy'").fetchone()[0]
    assert folded == "imported:original"


def test_a_backup_exists_before_anything_moves(con, home):
    a_job(con, "manual:job-1", "imported")

    result = rekey.apply(con, home)

    backup = result["backup"]
    assert backup, "nothing was backed up"
    from pathlib import Path
    assert Path(backup).is_file()
    # AND IT HOLDS THE OLD KEYS, which is the only thing that makes it a
    # backup rather than a file. Read through sqlite rather than trusting the
    # size: a WAL-mode database copied as bytes can be missing the most recent
    # work, which is exactly the work somebody would be restoring.
    old = sqlite3.connect(backup)
    try:
        keys = [r[0] for r in old.execute("SELECT key FROM jobs")]
    finally:
        old.close()
    assert keys == ["manual:job-1"]


def test_two_rows_that_would_collide_are_reported_and_left_alone(con, home):
    """NEVER A MERGE. Correcting one key onto another row's key would join two
    postings' application histories, and no automatic rule gets to do that."""
    a_job(con, "manual:job-1", "imported")
    a_job(con, "imported:job-1", "imported", title="The row already there")

    result = rekey.apply(con, home)

    assert result["moved"] == 0
    assert result["conflicts"] == [
        {"key": "manual:job-1", "would_be": "imported:job-1", "source": "imported"}]
    keys = sorted(r[0] for r in con.execute("SELECT key FROM jobs"))
    assert keys == ["imported:job-1", "manual:job-1"], "both rows survive"


def test_rows_whose_prefix_already_agrees_are_not_touched(con, home):
    a_job(con, "greenhouse:acme-201", "greenhouse")

    result = rekey.apply(con, home)

    assert result["moved"] == 0
    assert result["backup"] is None, "nothing moved, so nothing needed backing up"


def test_running_it_twice_changes_nothing_the_second_time(con, home):
    a_job(con, "manual:job-1", "imported")

    first = rekey.apply(con, home)
    second = rekey.apply(con, home)

    assert first["moved"] == 1
    assert second["moved"] == 0


def test_opening_a_database_corrects_it_without_being_asked(home):
    """A real profile is opened by the engine every day; nobody should have to
    remember a command for the data to stop lying about itself."""
    con = db.connect(home)
    a_job(con, "manual:job-1", "imported")
    con.execute("INSERT INTO job_status (key, status, updated) "
                "VALUES ('manual:job-1', 'applied', '2026-08-01T10:00:00Z')")
    con.commit()
    con.close()

    con = db.connect(home)
    try:
        assert [r[0] for r in con.execute("SELECT key FROM jobs")] == ["imported:job-1"]
        assert [r[0] for r in con.execute("SELECT key FROM job_status")] == [
            "imported:job-1"]
        assert db.get_meta(con, rekey.MARKER)
    finally:
        con.close()


def test_no_table_holding_a_job_key_is_missing_from_the_list(con):
    """THE ONE THAT PROTECTS THE NEXT PERSON. A table added later with a `key`
    column would be silently orphaned by a rename, and nothing else in this
    suite would notice - so the list is checked against the database itself
    rather than against somebody's memory.
    """
    listed = {(table, column) for table, column in rekey.KEY_COLUMNS}
    found = set()
    for row in con.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"):
        table = row[0]
        for column in con.execute(f"PRAGMA table_info({table})"):
            name = column[1]
            if name in ("key", "duplicate_of") and table != "meta":
                found.add((table, name))

    assert found == listed, (
        "a table holding a job key is missing from rekey.KEY_COLUMNS (or the "
        "list names one that no longer exists)")
