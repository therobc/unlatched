"""A company with a confirmed careers page but no ATS fingerprint must still
be collectable: first through the page's embedded schema.org JobPosting
markup, then through the portal sitemap. Both routes exist as collectors, so
the collect command has to actually dispatch to them - a registered source
nothing can reach is a dead end, not a feature.
"""
from __future__ import annotations

import json
from typing import Any

from unlatched import cli, db

CAREERS_URL = "https://example.com/careers"

JSONLD_PAGE = """<html><head>
<script type="application/ld+json">
{"@type": "JobPosting", "title": "Support Analyst", "url":
"https://example.com/careers/support-analyst-42", "identifier":
{"value": "42"}, "datePosted": "2026-08-01", "description": "Help desk role.",
"jobLocationType": "TELECOMMUTE"}
</script></head><body>Careers at Example</body></html>"""


def _fake_fetch_factory(pages: dict[str, str]) -> Any:
    def fake_fetch(url: str, **_kw: Any) -> tuple[int, str, str]:
        if url in pages:
            return 200, pages[url], url
        return 404, "", url
    return fake_fetch


def test_no_ats_company_collects_via_schema_org(tmp_path, monkeypatch):
    home = tmp_path / "home"
    con = db.connect(home)
    db.upsert_company(con, "Example Co", domain="example.com",
                       careers_url=CAREERS_URL, probe_status="probed")
    con.close()

    monkeypatch.setattr(cli.fetch_mod, "fetch",
                         _fake_fetch_factory({CAREERS_URL: JSONLD_PAGE}))
    rc = cli.main(["--home", str(home), "collect", "--json"])
    assert rc == 0

    con = db.connect(home)
    rows = con.execute("SELECT * FROM jobs").fetchall()
    con.close()
    assert len(rows) == 1
    row = rows[0]
    assert row["key"] == "schema_org:42"
    assert row["title"] == "Support Analyst"
    assert "Remote" in (row["location"] or "")


def test_company_with_nothing_still_skipped(tmp_path, monkeypatch):
    home = tmp_path / "home"
    con = db.connect(home)
    db.upsert_company(con, "Ghost Co", probe_status="dead")
    con.close()

    monkeypatch.setattr(cli.fetch_mod, "fetch", _fake_fetch_factory({}))
    rc = cli.main(["--home", str(home), "collect", "--json"])
    assert rc == 0

    con = db.connect(home)
    count = con.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    con.close()
    assert count == 0


def test_schema_org_dispatch_survives_json_roundtrip(tmp_path, monkeypatch, capsys):
    home = tmp_path / "home"
    con = db.connect(home)
    db.upsert_company(con, "Example Co", careers_url=CAREERS_URL)
    con.close()

    monkeypatch.setattr(cli.fetch_mod, "fetch",
                         _fake_fetch_factory({CAREERS_URL: JSONLD_PAGE}))
    rc = cli.main(["--home", str(home), "collect", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload
    assert payload[0]["source"] == "schema_org"
    assert payload[0]["collected"] == 1
