"""A renamed setting must carry the person's value across, not reset it.

A rename with no migration is a SILENT RESET: the old key stops being read,
the new one is absent from the file, `_deep_merge` fills in the default, and
the person is told nothing. Somebody who had deliberately turned a fetching
setting off would find it back on after an update - which is the worst
direction for that kind of setting to move on its own.

RENAMED_KEYS IS EMPTY TODAY, AND THESE TESTS DO NOT DEPEND ON THAT. Every
entry it held was a legacy spelling from before the first public release, so
no config in existence can carry one and migrating them would be theatre. The
MECHANISM is what has to keep working, because the first rename after
publication will need it and by then there will be real files to carry across.

So these inject their own renames and prove the machinery against those. That
is a better test than the historical entries were: it exercises the chain, the
precedence rule and the write-back without depending on which settings this
app happened to rename before anybody was using it.
"""
from __future__ import annotations

import json

import pytest

from unlatched import config

CURRENT = "read_added_links"

# A two-hop chain, so a config untouched since the original name has to be
# walked forward through BOTH renames rather than only the most recent.
CHAIN = (
    ("fetch.first_name", "fetch.second_name"),
    ("fetch.second_name", f"fetch.{CURRENT}"),
)


@pytest.fixture
def chain(monkeypatch):
    monkeypatch.setattr(config, "RENAMED_KEYS", CHAIN)


def write(home, raw: dict) -> None:
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.json").write_text(json.dumps(raw), encoding="utf-8")


def test_an_off_setting_survives_a_single_rename(home, chain):
    write(home, {"fetch": {"second_name": False}})
    cfg = config.load(home)
    assert cfg["fetch"][CURRENT] is False
    assert "second_name" not in cfg["fetch"]


def test_an_off_setting_survives_both_renames_in_sequence(home, chain):
    """The case a one-hop migration would miss."""
    write(home, {"fetch": {"first_name": False}})
    cfg = config.load(home)
    assert cfg["fetch"][CURRENT] is False
    assert "first_name" not in cfg["fetch"]
    assert "second_name" not in cfg["fetch"]


def test_the_newest_name_wins_when_several_are_present(home, chain):
    # A hand-edited file, or one written by a newer version and opened by an
    # older one. The newest key is the most recent statement of intent.
    write(home, {
        "fetch": {"first_name": True, "second_name": True, CURRENT: False},
    })
    assert config.load(home)["fetch"][CURRENT] is False


def test_a_value_that_was_on_stays_on(home, chain):
    """The silent reset in the other direction: an update must not turn off
    something somebody deliberately turned on."""
    write(home, {"fetch": {"first_name": True}})
    assert config.load(home)["fetch"][CURRENT] is True


def test_the_migration_is_written_back_on_the_next_save(home, chain):
    write(home, {"fetch": {"first_name": False}})
    config.save(config.load(home), home)
    on_disk = json.loads((home / "config.json").read_text(encoding="utf-8"))
    # Not just absent from the loaded dict - gone from the FILE, so the next
    # reader is not migrating it again forever.
    assert "first_name" not in on_disk["fetch"]
    assert "second_name" not in on_disk["fetch"]
    assert on_disk["fetch"][CURRENT] is False


def test_a_config_that_never_had_any_of_them_is_untouched(home):
    write(home, {"search": {"currency": "GBP"}})
    cfg = config.load(home)
    assert cfg["search"]["currency"] == "GBP"
    assert cfg["fetch"][CURRENT] is False, "the shipped default is off"


def test_nothing_is_migrated_when_there_is_nothing_to_migrate(home):
    """The real RENAMED_KEYS, not the injected chain.

    An empty tuple has to be a no-op rather than an error, and a config is
    handed back exactly as written.
    """
    assert config.RENAMED_KEYS == (), "a real rename needs its own test above"
    write(home, {"fetch": {CURRENT: True}, "search": {"currency": "USD"}})
    cfg = config.load(home)
    assert cfg["fetch"][CURRENT] is True
    assert cfg["search"]["currency"] == "USD"


def test_the_injected_chain_would_actually_fire():
    """A positive control for the fixture.

    Every migration test above passes trivially if monkeypatching failed and
    RENAMED_KEYS stayed empty - the old key would simply be left alone and the
    default would satisfy the assertion. This proves the chain is real.
    """
    assert len(CHAIN) == 2
    assert CHAIN[0][1] == CHAIN[1][0], "the two hops have to connect"
    assert CHAIN[-1][1].endswith(CURRENT)


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
