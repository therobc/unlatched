"""Moving what a person is looking for between tools.

One search, two apps. The failure this exists to prevent is silent: two tools
with criteria that have drifted apart, where neither is wrong on its own terms
and the person cannot tell which list is short because of a real absence and
which because of a setting they forgot to mirror.
"""
from __future__ import annotations

import contextlib
import io
import json
import re
from pathlib import Path

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


def test_replace_is_what_a_list_does_by_default(cfg):
    """The behaviour before the choice existed, unchanged by adding it.

    A caller that passes no mode has to get exactly what it got before, or
    every existing import silently changes meaning the day the option ships.
    """
    cfg["search"]["terms"] = ["fitter", "welder"]
    criteria.apply(cfg, {"search": {"terms": ["machinist"]}})
    assert cfg["search"]["terms"] == ["machinist"]


def test_merge_adds_to_a_list_instead_of_standing_in_for_it(cfg):
    """The case the feature is FOR: one search kept in two tools.

    A title added in the other app should arrive here. The four typed here
    should still be here afterwards - which is the whole difference between
    the two modes, and the reason the person is asked.
    """
    cfg["search"]["terms"] = ["fitter", "welder"]
    criteria.apply(cfg, {"search": {"terms": ["welder", "machinist"]}}, "merge")
    assert cfg["search"]["terms"] == ["fitter", "welder", "machinist"]


def test_a_merge_never_lists_the_same_thing_twice(cfg):
    """Two tools describing one search will overlap heavily - that is what
    makes them one search. A merge that appended blindly would double every
    shared term on the first import and again on the second."""
    cfg["search"]["terms"] = ["fitter", "welder"]
    # The overlap is PARTIAL on purpose. With both lists identical this test
    # cannot fail - a replace returns the same answer a merge does - which is
    # what a positive control run showed on 2026-09-04.
    criteria.apply(cfg, {"search": {"terms": ["welder"]}}, "merge")
    assert cfg["search"]["terms"] == ["fitter", "welder"]
    criteria.apply(cfg, {"search": {"terms": ["welder"]}}, "merge")
    assert cfg["search"]["terms"] == ["fitter", "welder"]


def test_a_merge_keeps_what_was_already_here_in_its_own_order(cfg):
    """An import must read as an addition to what somebody built, not as a
    reshuffle of it: a list that came back reordered would look like the
    import had rewritten a screen the person was not asking about."""
    cfg["search"]["terms"] = ["welder", "fitter", "rigger"]
    criteria.apply(cfg, {"search": {"terms": ["rigger", "machinist"]}}, "merge")
    assert cfg["search"]["terms"] == ["welder", "fitter", "rigger", "machinist"]


def test_a_single_value_ignores_the_mode(cfg):
    """A floor holds ONE answer and two cannot both be kept, so by construction
    there is nothing for a mode to decide. Merging into it has to mean the same
    thing as replacing it - anything else would be inventing a third number."""
    cfg["search"]["salary_floor"] = 60000
    for mode in criteria.MODES:
        cfg["search"]["salary_floor"] = 60000
        criteria.apply(cfg, {"search": {"salary_floor": 72000}}, mode)
        assert cfg["search"]["salary_floor"] == 72000


def test_an_unknown_mode_is_refused(cfg):
    """Not silently treated as one of the two. A typo'd mode that fell through
    to replace would delete lists the caller meant to add to."""
    with pytest.raises(criteria.CriteriaError):
        criteria.apply(cfg, {"search": {"terms": ["x"]}}, "combine")


def test_a_preview_changes_nothing(cfg):
    """The point of a preview: the person sees it BEFORE deciding. `apply`
    works in place, so a preview built the obvious way would apply the import
    it was asked to describe."""
    cfg["search"]["terms"] = ["fitter"]
    cfg["search"]["salary_floor"] = 60000
    criteria.preview(cfg, {"search": {"terms": ["machinist"], "salary_floor": 72000}})
    assert cfg["search"]["terms"] == ["fitter"]
    assert cfg["search"]["salary_floor"] == 60000


def test_a_preview_says_which_key_moves_and_by_how_much(cfg):
    """"3 blocks changed" does not tell somebody their salary floor is about
    to move, which is exactly what they would have wanted to know first."""
    cfg["search"]["terms"] = ["fitter", "welder"]
    cfg["search"]["salary_floor"] = 60000
    rows = criteria.preview(
        cfg, {"search": {"terms": ["welder", "machinist"], "salary_floor": 72000}},
        "merge")
    by_key = {r["key"]: r for r in rows}

    assert by_key["salary_floor"]["was"] == 60000
    assert by_key["salary_floor"]["becomes"] == 72000
    # One term arrives, none leave: that is what distinguishes this from the
    # same file imported the other way.
    assert by_key["terms"]["added"] == 1
    assert by_key["terms"]["removed"] == 0


def test_a_preview_of_a_replace_counts_what_would_be_lost(cfg):
    """The number the person needs to see before choosing replace. Under merge
    the same file removes nothing."""
    cfg["search"]["terms"] = ["fitter", "welder"]
    rows = criteria.preview(cfg, {"search": {"terms": ["machinist"]}}, "replace")
    terms = next(r for r in rows if r["key"] == "terms")
    assert terms["added"] == 1
    assert terms["removed"] == 2


def test_a_preview_of_a_file_that_changes_nothing_is_empty(cfg):
    """An import that would do nothing has to LOOK like it would do nothing.
    A preview listing every key it touched, changed or not, would read as a
    long list of edits about to happen."""
    cfg["search"]["terms"] = ["fitter"]
    assert criteria.preview(cfg, {"search": {"terms": ["fitter"]}}) == []


def test_the_app_round_trips_a_search_through_the_engine(cfg, tmp_path, monkeypatch):
    """EXPORT HERE, IMPORT THERE, through the SAME argument lists the desktop
    builds.

    Not a second implementation of the round trip: the strings below are what
    app.rs::criteria_args produces (criteria, --import, <path>, --mode, <mode>,
    --json, and --dry-run for a preview), and desktop tests assert that
    function produces them. What this checks is the half those cannot - that
    the engine, handed exactly that, moves a search from one profile to
    another and leaves the install-specific settings behind.
    """
    from unlatched import cli

    sender = tmp_path / "sender"
    receiver = tmp_path / "receiver"
    for home in (sender, receiver):
        home.mkdir()
    cfg["search"]["terms"] = ["fitter", "welder"]
    cfg["search"]["salary_floor"] = 72000
    cfg["credentials"]["usajobs"]["api_key"] = "SECRET-KEY-VALUE"
    config.save(cfg, sender)
    config.save(config.load(receiver), receiver)

    handover = tmp_path / "criteria.json"
    assert cli.main(["--home", str(sender), "criteria",
                     "--export", str(handover), "--json"]) == 0

    # The desktop's preview: the same call with --dry-run on the end.
    preview_args = ["--home", str(receiver), "criteria", "--import", str(handover),
                    "--mode", "replace", "--json", "--dry-run"]
    assert cli.main(preview_args) == 0
    # Nothing yet. A preview that changed the receiving profile would apply
    # the import the person is still being asked about.
    assert config.load(receiver)["search"]["terms"] != ["fitter", "welder"]

    assert cli.main(preview_args[:-1]) == 0
    arrived = config.load(receiver)
    assert arrived["search"]["terms"] == ["fitter", "welder"]
    assert arrived["search"]["salary_floor"] == 72000
    # The key stayed on the sending machine, which is the whole reason the
    # format carries three blocks and not the config.
    assert arrived["credentials"]["usajobs"]["api_key"] != "SECRET-KEY-VALUE"


def test_the_app_can_add_to_a_search_without_replacing_it(cfg, tmp_path):
    """The other mode, end to end. Verified before it was written: `apply` had
    no mode at all, so a list arriving always stood in for the one here."""
    from unlatched import cli

    sender = tmp_path / "sender"
    receiver = tmp_path / "receiver"
    for home in (sender, receiver):
        home.mkdir()
    cfg["search"]["terms"] = ["machinist"]
    config.save(cfg, sender)

    theirs = config.load(receiver)
    theirs["search"]["terms"] = ["fitter", "welder"]
    config.save(theirs, receiver)

    handover = tmp_path / "criteria.json"
    assert cli.main(["--home", str(sender), "criteria",
                     "--export", str(handover), "--json"]) == 0
    assert cli.main(["--home", str(receiver), "criteria", "--import", str(handover),
                     "--mode", "merge", "--json"]) == 0

    assert config.load(receiver)["search"]["terms"] == [
        "fitter", "welder", "machinist"]


def _rust_fields(struct: str) -> set[str]:
    """The field names the desktop reads off one of its structs."""
    source = (Path(__file__).resolve().parents[1]
              / "desktop/src/criteria.rs").read_text(encoding="utf-8")
    body = source.split(f"pub struct {struct} {{")[1].split("\n}")[0]
    return set(re.findall(r"^\s*pub (\w+):", body, re.MULTILINE))


def test_the_desktop_reads_the_keys_the_engine_actually_emits(cfg, tmp_path):
    """A CONTRACT THAT FAILS SILENTLY - verified 2026-09-05 by reading
    criteria.rs, where Report and Change carry serde(default) on every field.
    A key renamed on this side therefore would not fail to parse over there:
    it would deserialise to an empty report, and the preview dialog would read
    "Nothing would change" while offering to import a file that changes plenty.

    The Rust unit test cannot catch that. It feeds a payload somebody typed
    after reading this file, which proves the struct parses THAT rather than
    what the engine sends. Same reasoning as ADDED_JOB_COLUMNS being compared
    against its copy in db.rs.
    """
    from unlatched import cli

    sender = tmp_path / "sender"
    receiver = tmp_path / "receiver"
    for home in (sender, receiver):
        home.mkdir()
    cfg["search"]["terms"] = ["fitter", "welder"]
    cfg["search"]["salary_floor"] = 72000
    config.save(cfg, sender)

    handover = tmp_path / "criteria.json"
    assert cli.main(["--home", str(sender), "criteria",
                     "--export", str(handover), "--json"]) == 0

    emitted = json.loads(_capture(cli, [
        "--home", str(receiver), "criteria", "--import", str(handover),
        "--mode", "merge", "--dry-run", "--json"]))

    missing = _rust_fields("Report") - set(emitted)
    assert not missing, f"the desktop reads {sorted(missing)}, the engine sends nothing"

    rows = emitted["preview"]
    assert rows, "a file that changes things previewed as changing nothing"
    row_missing = _rust_fields("Change") - set(rows[0])
    assert not row_missing, f"the desktop reads {sorted(row_missing)} off each row"


def _capture(cli, args: list[str]) -> str:
    """Run the CLI and hand back what it printed."""
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        assert cli.main(args) == 0
    return buffer.getvalue()
