"""A renamed setting must carry the person's value across, not reset it.

This one has been renamed twice, which is the interesting part:

    fetch.manual_fetch_linkedin          (original)
    fetch.added_links_include_linkedin   (2026-08-07)
    fetch.read_added_links               (2026-08-08)

The first rename dropped an awkward verb but kept a site in the name; the
second dropped the site too. Naming a setting after one site tells the reader
there is an integration with that site, and becomes wrong the moment a second
site joins the same category - LinkedIn is not the category, it is the current
sole member of one.

A rename with no migration is a SILENT RESET: the old key stops being read, the
new one is absent from the file, and `_deep_merge` fills in the default. That
default is ON, so somebody who had deliberately turned this OFF would have
found it back ON after an update, having been told nothing. Twice over, since
a config could still be carrying the original name.
"""
from __future__ import annotations

import json

from unlatched import config

CURRENT = "read_added_links"


def write(home, raw: dict) -> None:
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.json").write_text(json.dumps(raw), encoding="utf-8")


def test_an_off_setting_survives_a_single_rename(home):
    write(home, {"fetch": {"added_links_include_linkedin": False}})
    cfg = config.load(home)
    assert cfg["fetch"][CURRENT] is False
    assert "added_links_include_linkedin" not in cfg["fetch"]


def test_an_off_setting_survives_both_renames_in_sequence(home):
    """The case a one-hop migration would miss: a config untouched since the
    original name has to be walked forward through BOTH renames, not just the
    most recent one."""
    write(home, {"fetch": {"manual_fetch_linkedin": False}})
    cfg = config.load(home)
    assert cfg["fetch"][CURRENT] is False
    assert "manual_fetch_linkedin" not in cfg["fetch"]
    assert "added_links_include_linkedin" not in cfg["fetch"]


def test_the_newest_name_wins_when_several_are_present(home):
    # A hand-edited file, or one written by a newer version and opened by an
    # older one. The newest key is the most recent statement of intent.
    write(home, {
        "fetch": {
            "manual_fetch_linkedin": True,
            "added_links_include_linkedin": True,
            CURRENT: False,
        },
    })
    assert config.load(home)["fetch"][CURRENT] is False


def test_a_config_that_never_had_any_of_them_is_untouched(home):
    write(home, {"search": {"currency": "GBP"}})
    cfg = config.load(home)
    assert cfg["search"]["currency"] == "GBP"
    assert cfg["fetch"][CURRENT] is False, "the shipped default is off"


def test_an_existing_install_that_had_it_on_keeps_it_on(home):
    """The default flipped to off on 2026-08-08. Somebody who had turned it on
    deliberately must not have it turned back off by an update - that is the
    silent reset the whole rename machinery exists to prevent, in the other
    direction."""
    write(home, {"fetch": {"manual_fetch_linkedin": True}})
    assert config.load(home)["fetch"][CURRENT] is True


def test_the_migration_is_written_back_on_the_next_save(home):
    write(home, {"fetch": {"manual_fetch_linkedin": False}})
    config.save(config.load(home), home)
    on_disk = json.loads((home / "config.json").read_text(encoding="utf-8"))
    # Not just absent from the loaded dict - gone from the FILE, so the next
    # reader is not migrating it again forever.
    assert "manual_fetch_linkedin" not in on_disk["fetch"]
    assert "added_links_include_linkedin" not in on_disk["fetch"]
    assert on_disk["fetch"][CURRENT] is False


def test_the_four_dead_fetch_keys_are_gone(home):
    """max_bytes, timeout_s, per_host_delay_s and respect_robots sat in the
    defaults while nothing read any of them - every collector passes its own
    values. respect_robots was the worst: whether robots applies is decided per
    endpoint by what the endpoint IS, so a global switch could only mislead.

    An existing config that still carries them keeps them (the merge preserves
    unmodelled keys), but a fresh one no longer offers a control that does
    nothing."""
    defaults = config.defaults()["fetch"]
    for dead in ("max_bytes", "timeout_s", "per_host_delay_s", "respect_robots"):
        assert dead not in defaults, f"{dead} is not read by anything"
    assert defaults == {CURRENT: False}
