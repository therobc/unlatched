"""Moving what a person is looking for between tools.

One search, two apps. The failure this exists to prevent is silent: two tools
with criteria that have drifted apart, where neither is wrong on its own terms
and the person cannot tell which list is short because of a real absence and
which because of a setting they forgot to mirror.
"""
from __future__ import annotations

import json

import pytest

from unlatched import config, criteria


def test_the_criteria_blocks_travel(cfg, tmp_path):
    out = tmp_path / "criteria.json"
    criteria.write(cfg, out)
    data = json.loads(out.read_text(encoding="utf-8"))

    assert data["format"] == criteria.FORMAT
    assert data["version"] == criteria.VERSION
    for block in ("search", "skills", "profile"):
        assert block in data, f"{block} should travel"


def test_credentials_and_install_settings_never_travel(cfg, tmp_path):
    """An API key crossing a tool boundary in a criteria file is a credential
    leak by another name, and a refresh schedule copied from another machine is
    how two collectors end up running at the same minute."""
    cfg["credentials"]["usajobs"]["api_key"] = "SECRET-KEY-VALUE"
    out = tmp_path / "criteria.json"
    criteria.write(cfg, out)
    text = out.read_text(encoding="utf-8")

    assert "SECRET-KEY-VALUE" not in text
    for absent in ("credentials", "refresh", "sources", "agent_api", "resume_path"):
        assert absent not in json.loads(text), f"{absent} must not travel"


def test_criteria_round_trip_unchanged(cfg, tmp_path):
    out = tmp_path / "criteria.json"
    criteria.write(cfg, out)
    fresh = config.defaults()
    fresh["search"]["title_include"] = ["something else entirely"]

    changed = criteria.apply(fresh, criteria.read(out))
    assert "search" in changed
    assert fresh["search"]["title_include"] == cfg["search"]["title_include"]


def test_a_file_carrying_one_block_does_not_blank_the_others(cfg, tmp_path):
    """A sender that only knows about titles must not wipe a skills vocabulary
    it never had an opinion about."""
    out = tmp_path / "partial.json"
    out.write_text(json.dumps({
        "format": criteria.FORMAT, "version": 1,
        "search": {"salary_floor": 91000},
    }), encoding="utf-8")

    # The shipped default has no titles, so give it some - the point being
    # tested is that a key the sender did not mention survives.
    cfg["search"]["title_include"] = ["Support Analyst"]
    before_skills = list(cfg["skills"])
    changed = criteria.apply(cfg, criteria.read(out))

    assert changed == ["search"]
    assert cfg["search"]["salary_floor"] == 91000
    assert cfg["skills"] == before_skills
    # And keys the sender did not mention survive inside the block it did send.
    assert cfg["search"]["title_include"]


def test_a_foreign_or_newer_file_is_refused_rather_than_half_applied(tmp_path):
    """Half a set of criteria is worse than none: the search still runs, still
    returns results, and nothing shows that the floor never arrived."""
    for payload in (
        {"format": "something.else", "version": 1, "search": {}},
        {"format": criteria.FORMAT, "version": 99, "search": {}},
        {"format": criteria.FORMAT, "version": 1},
        ["not", "an", "object"],
    ):
        path = tmp_path / "bad.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        with pytest.raises(criteria.CriteriaError):
            criteria.read(path)


def test_unreadable_json_says_so(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(criteria.CriteriaError, match="not valid JSON"):
        criteria.read(path)


def test_applying_identical_criteria_reports_no_change(cfg, tmp_path):
    """So a caller can sync repeatedly without it looking like something moved
    every time."""
    out = tmp_path / "criteria.json"
    criteria.write(cfg, out)
    assert criteria.apply(cfg, criteria.read(out)) == []


def test_an_older_exporter_cannot_drop_a_newer_setting(cfg, tmp_path):
    """Within a block the incoming keys win, but keys the sender does not know
    about are left alone - otherwise upgrading one tool would silently reset
    settings the other has not learned about yet."""
    cfg["search"]["a_setting_added_later"] = "keep me"
    out = tmp_path / "old.json"
    out.write_text(json.dumps({
        "format": criteria.FORMAT, "version": 1,
        "search": {"salary_floor": 60000},
    }), encoding="utf-8")

    criteria.apply(cfg, criteria.read(out))
    assert cfg["search"]["a_setting_added_later"] == "keep me"
    assert cfg["search"]["salary_floor"] == 60000
