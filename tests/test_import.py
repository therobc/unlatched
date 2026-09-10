"""Importing rows another collector already read.

The property that matters is NEGATIVE: no requests. A bulk import that quietly
re-reads what it was handed is a crawl wearing an import's name, and for a site
read only with a person present it would be a second automated reader of pages
that were already read once.

So these tests assert on whether the network was touched, not only on the rows
that come out.
"""
from __future__ import annotations

import json

import pytest

from unlatched import db, importer, links

ROW = {
    "url": "https://www.example.com/jobs/view/4012345",
    "title": "Technology Operations Support Analyst",
    "company": "Northwind",
    "location": "Remote - US",
    "description": "Support the operations team. Windows, Active Directory.",
    "posted": "2026-08-01",
    "apply_url": ("https://www.linkedin.com/safety/go/?url="
                  "https%3A%2F%2Fapply%2Eworkable%2Ecom%2Fnorthwind%2Fj%2FABC123%2F"
                  "&trk=public_jobs_apply-link-offsite"),
}


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """Any request at all is a test failure, wherever it comes from."""
    def explode(*_args, **_kwargs):
        raise AssertionError("import must not fetch anything")

    monkeypatch.setattr("unlatched.fetch.fetch", explode)
    monkeypatch.setattr("socket.getaddrinfo", explode)


def test_a_row_is_stored_without_any_request(con, cfg):
    result = importer.import_row(con, cfg, ROW)
    row = db.get_job(con, result["key"])
    assert row["title"] == "Technology Operations Support Analyst"
    assert row["location"] == "Remote - US"
    assert row["posted_at"] == "2026-08-01"
    assert row["description"].startswith("Support the operations team")


def test_the_apply_destination_is_unwrapped_and_normalised_on_the_way_in(con, cfg):
    """So an imported row can join a row collected directly from the ATS."""
    result = importer.import_row(con, cfg, ROW)
    assert result["apply_url"] == "https://apply.workable.com/northwind/j/ABC123"


def test_an_imported_row_takes_a_status_like_any_other(con, cfg):
    from unlatched import status
    key = importer.import_row(con, cfg, ROW)["key"]
    status.set_status(con, key, "applied")
    assert con.execute(
        "SELECT status FROM job_status WHERE key = ?", (key,)).fetchone()["status"] == "applied"


def test_importing_the_same_run_twice_updates_rather_than_duplicates(con, cfg):
    first = importer.import_row(con, cfg, ROW)["key"]
    second = importer.import_row(con, cfg, ROW)["key"]
    assert first == second
    assert con.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1


def test_a_senders_own_key_is_honoured_inside_our_namespace(con, cfg):
    """So the other app can re-send its rows and update them by ITS id, not by
    a URL that may have been rewritten between runs.

    PINNED TO ONE ANSWER. This asserted `imported:li:998877` OR `li:998877`,
    which is not an assertion about behaviour at all - it passes whichever of
    the two the importer does, so it could never catch the prefix being
    dropped. The prefix is imposed rather than taken from the file (see
    test_collector_namespace), and that is the half worth pinning: a sender's
    id landing unprefixed is a row in nobody's namespace.
    """
    result = importer.import_row(con, cfg, {**ROW, "key": "li:998877"})
    assert result["key"] == "imported:li:998877"
    assert db.get_job(con, result["key"]) is not None
    # And the sender's own id survives whole inside it, structure and all.
    assert result["key"].endswith("li:998877")


def test_a_row_with_no_title_is_reported_not_guessed(con, cfg):
    with pytest.raises(importer.ImportRowError):
        importer.import_row(con, cfg, {"url": ROW["url"]})


def test_one_bad_row_does_not_cost_the_others(con, cfg):
    """The sender is another program. A partial import that names its failures
    is recoverable; all-or-nothing is a stand-off."""
    rows = [ROW, {"url": "https://example.com/x"}, {**ROW, "url": "https://x.example/2",
                                                    "title": "Second"}]
    result = importer.import_all(con, cfg, rows)
    assert result["imported"] == 2
    assert len(result["failed"]) == 1
    assert result["failed"][0]["row"] == 1


def test_unknown_fields_are_ignored_rather_than_rejected(con, cfg):
    """The other collector has a richer schema - rank, fit_score, drop_reason,
    clearance. None of it is load-bearing here, and an import must not fail
    because the sender knows more than we store."""
    noisy = {**ROW, "rank": 3, "fit_score": 0.82, "drop_reason": None,
             "clearance": "none", "tech_signals": ["windows"]}
    assert importer.import_row(con, cfg, noisy)["key"]


def test_an_unsafe_url_is_not_stored_as_a_link(con, cfg):
    """Same store-boundary rule as everywhere else: what arrives from another
    program is not trusted more than what arrives from a job board."""
    result = importer.import_row(
        con, cfg, {**ROW, "url": "file:///C:/Windows/System32/calc.exe",
                   "key": "li:1"})
    assert db.get_job(con, result["key"])["url"] == ""


def test_rows_are_read_from_either_json_shape(tmp_path):
    plain = tmp_path / "a.json"
    plain.write_text(json.dumps([ROW]), encoding="utf-8")
    assert len(importer.read_rows(plain)) == 1

    wrapped = tmp_path / "b.json"
    wrapped.write_text(json.dumps({"jobs": [ROW, ROW]}), encoding="utf-8")
    assert len(importer.read_rows(wrapped)) == 2


def test_an_imported_row_and_a_collected_one_share_an_apply_key(con, cfg):
    """The whole point of carrying apply_url through the import: the MyBoard
    row and the Workable row it forwards to must be joinable."""
    imported = importer.import_row(con, cfg, ROW)
    direct = importer.import_row(con, cfg, {
        "url": "https://apply.workable.com/northwind/j/ABC123/",
        "title": "Technology Operations Support Analyst",
        "company": "Northwind",
        "apply_url": "https://apply.workable.com/northwind/j/ABC123/",
    })
    assert imported["key"] != direct["key"]
    assert imported["apply_url"] == direct["apply_url"]
    assert imported["apply_url"] == links.normalise_apply_url(
        "https://apply.workable.com/northwind/j/ABC123")
