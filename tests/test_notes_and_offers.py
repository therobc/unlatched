"""Notes, offer terms, and getting all of it back out.

The first user's rule: nothing a person writes is ever replaced by the next thing they
write. That makes these tests about ACCUMULATION rather than about storage - the
interesting failures are the ones where a note is silently overwritten or
silently dropped on the way out, both of which look like nothing at all until
somebody goes looking for what they wrote months earlier.
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


# --------------------------------------------------------------- the rename ---

def written_by_an_older_version(path, rows):
    """A database carrying statuses this version no longer writes.

    Seeded and closed, so the assertions below run against a REOPEN - which is
    where the migration actually happens. Calling the migration directly would
    prove the function works without proving it is ever reached.
    """
    con = db.connect_at(path)
    for key, status_value in rows:
        con.execute(
            "INSERT INTO job_status (key, status, updated) VALUES (?, ?, ?)",
            (key, status_value, "2026-08-01T09:00:00"))
        con.execute(
            "INSERT INTO job_status_log (key, status, at) VALUES (?, ?, ?)",
            (key, status_value, "2026-08-01T09:00:00"))
    con.commit()
    con.close()


def test_the_old_word_for_a_rejection_is_carried_forward_in_both_tables(tmp_path):
    """Denied became No Offer. A database written before that has to come
    forward on open, and its HISTORY has to come with it - the log is what the
    funnel and the export read, so leaving it behind would show one word on the
    row and another in its own timeline."""
    path = tmp_path / "old.db"
    written_by_an_older_version(path, [("gh:1", "denied")])

    con = db.connect_at(path)

    current = con.execute(
        "SELECT status FROM job_status WHERE key = 'gh:1'").fetchone()[0]
    assert current == "no_offer"
    history = [r[0] for r in con.execute(
        "SELECT status FROM job_status_log WHERE key = 'gh:1' ORDER BY id")]
    assert "denied" not in history
    assert "no_offer" in history


def test_the_retired_closed_value_is_left_exactly_where_it_is(tmp_path):
    """'closed' meant "the opening went away", which the app now derives from
    jobs.delisted_at. Rewriting those rows into some other status would be
    inventing a decision the person never made, so they keep their value and
    keep reading as a word."""
    path = tmp_path / "closed.db"
    written_by_an_older_version(path, [("gh:2", "closed")])

    con = db.connect_at(path)

    current = con.execute(
        "SELECT status FROM job_status WHERE key = 'gh:2'").fetchone()[0]
    assert current == "closed"


# -------------------------------------------------------------- the CSV out ---

def test_a_note_survives_the_export_with_its_newline_and_its_quote(con, cfg,
                                                                   tmp_path):
    """The acceptance the first user named. A note is free text a person typed, so it
    contains commas, quote marks and line breaks - and the export is the file
    that has to outlive the app. Quoting is csv.DictWriter's job; this proves
    the round trip rather than assuming it."""
    key = add(con, cfg, "https://boards.greenhouse.io/acme/jobs/3")
    awkward = 'said "we\'ll be in touch",\nthen a second line'
    status.set_status(con, key, "applied", note=awkward)

    out = tmp_path / "pipeline.csv"
    export.write_csv(con, out)

    row = read_back(out)[0]
    assert awkward in row["history"], (
        "the note must reach the file intact, newline and quote included")


def test_the_history_column_carries_what_was_written_about_each_step(con, cfg,
                                                                     tmp_path):
    """The defect this replaced: the export selected key/status/at only, so
    every word a person had written about their own applications was absent
    from the one file that exists to recover them."""
    key = add(con, cfg, "https://boards.greenhouse.io/acme/jobs/4")
    status.set_status(con, key, "applied", note="through the portal")
    status.set_status(con, key, "interviewed", note="panel of three")

    out = tmp_path / "pipeline.csv"
    export.write_csv(con, out)

    history = read_back(out)[0]["history"]
    assert "applied (through the portal)" in history
    assert "interviewed (panel of three)" in history
    assert history.index("applied") < history.index("interviewed"), (
        "oldest first, so the cell reads as a sequence")


def test_a_step_with_nothing_written_about_it_carries_no_empty_brackets(con, cfg,
                                                                        tmp_path):
    key = add(con, cfg, "https://boards.greenhouse.io/acme/jobs/5")
    status.set_status(con, key, "applied")

    out = tmp_path / "pipeline.csv"
    export.write_csv(con, out)

    assert "()" not in read_back(out)[0]["history"]


def test_standalone_notes_get_their_own_column(con, cfg, tmp_path):
    """Notes that are not about a status change are a separate table, so they
    would be invisible in an export that only walked the status log."""
    key = add(con, cfg, "https://boards.greenhouse.io/acme/jobs/6")
    con.execute(
        "INSERT INTO job_note (key, note, at) VALUES (?, ?, ?)",
        (key, "same recruiter as the last one", "2026-08-01T09:00:00"))
    con.commit()

    out = tmp_path / "pipeline.csv"
    export.write_csv(con, out)

    assert "same recruiter as the last one" in read_back(out)[0]["notes"]


def test_what_an_offer_was_reaches_the_export_as_its_own_columns(con, cfg,
                                                                 tmp_path):
    """Pay and the offer date are the two facts a person is asked for months
    later. Buried in a note they are unsortable and unfindable; as columns they
    survive into a spreadsheet as data."""
    key = add(con, cfg, "https://boards.greenhouse.io/acme/jobs/7")
    status.set_status(con, key, "offer", note="verbal",
                      pay="$120,000", offer_date="2026-09-01")

    out = tmp_path / "pipeline.csv"
    export.write_csv(con, out)

    row = read_back(out)[0]
    assert row["pay_offered"] == "$120,000"
    assert row["offer_date"] == "2026-09-01"


def test_a_renegotiated_offer_reports_the_one_that_stands(con, cfg, tmp_path):
    key = add(con, cfg, "https://boards.greenhouse.io/acme/jobs/8")
    status.set_status(con, key, "offer", pay="$110,000")
    status.set_status(con, key, "offer", pay="$120,000")

    out = tmp_path / "pipeline.csv"
    export.write_csv(con, out)

    assert read_back(out)[0]["pay_offered"] == "$120,000"
    # And the first is not erased - the log keeps both.
    pays = [r[0] for r in con.execute(
        "SELECT pay FROM job_status_log WHERE pay IS NOT NULL ORDER BY id")]
    assert pays == ["$110,000", "$120,000"]


# ------------------------------------------------------ the JSON round trip ---

def test_notes_and_offer_terms_survive_an_export_and_reimport(con, cfg, tmp_path):
    """The status export is what a person restores from. Anything it does not
    carry is lost on the restore that was supposed to save them - which is the
    exact failure the export exists because of."""
    key = add(con, cfg, "https://boards.greenhouse.io/acme/jobs/9")
    status.set_status(con, key, "offer", note="verbal, written to follow",
                      pay="$120,000", offer_date="2026-09-01")
    con.execute(
        "INSERT INTO job_note (key, note, at) VALUES (?, ?, ?)",
        (key, "chased them on the 5th", "2026-08-05T09:00:00"))
    con.commit()

    payload = status.export_status(con)

    fresh = db.connect_at(tmp_path / "restored.db")
    result = status.import_status(fresh, payload)
    assert result["note_rows"] == 1

    row = fresh.execute(
        "SELECT status, note, pay, offer_date FROM job_status_log "
        "WHERE key = ? ORDER BY id DESC LIMIT 1", (key,)).fetchone()
    assert row["status"] == "offer"
    assert row["note"] == "verbal, written to follow"
    assert row["pay"] == "$120,000"
    assert row["offer_date"] == "2026-09-01"

    note = fresh.execute(
        "SELECT note FROM job_note WHERE key = ?", (key,)).fetchone()[0]
    assert note == "chased them on the 5th"


def test_an_export_written_before_notes_existed_still_imports(con, tmp_path):
    """Having no "notes" key is not the same as having no notes, and neither is
    an error. An older export must not fail on a field it predates."""
    payload = {
        "status": {"gh:1": {"status": "applied", "at": "2026-08-01T09:00:00"}},
        "log": [{"key": "gh:1", "from": None, "to": "applied",
                 "at": "2026-08-01T09:00:00"}],
    }
    fresh = db.connect_at(tmp_path / "old.db")
    result = status.import_status(fresh, payload)

    assert result["status_rows"] == 1
    assert result["log_rows"] == 1
    assert result["note_rows"] == 0


def test_a_note_never_arrives_as_a_cleared_status(con, cfg, tmp_path):
    """WHY job_note is its own table. A null status in the log means "cleared",
    which import_status acts on by deleting the job_status row - so a note
    carried as a log entry would import having erased the thing it was written
    about. This proves the two travel separately."""
    key = add(con, cfg, "https://boards.greenhouse.io/acme/jobs/10")
    status.set_status(con, key, "applied")
    con.execute(
        "INSERT INTO job_note (key, note, at) VALUES (?, ?, ?)",
        (key, "left a voicemail", "2026-08-02T09:00:00"))
    con.commit()

    fresh = db.connect_at(tmp_path / "roundtrip.db")
    status.import_status(fresh, status.export_status(con))

    surviving = fresh.execute(
        "SELECT status FROM job_status WHERE key = ?", (key,)).fetchone()
    assert surviving is not None, "the note must not have erased the status"
    assert surviving[0] == "applied"
