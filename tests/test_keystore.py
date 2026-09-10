"""A stored API key must not be readable in config.json, and - the part that
actually bites - a key that cannot be unwrapped must read as "no credential"
rather than as a corrupt key sent to a live API.

These run on every platform. Where DPAPI is unavailable the round-trip tests
assert the documented fallback (store as-is, report unprotected) instead of
being skipped, because the fallback is a shipped behavior too.
"""
from __future__ import annotations

import json
from typing import Any

from unlatched import config
from unlatched import keystore as keystore_mod


def test_round_trip_returns_the_original_secret():
    assert keystore_mod.unprotect(keystore_mod.protect("KEY123")) == "KEY123"


def test_empty_stays_empty_and_is_never_tagged():
    assert keystore_mod.protect("") == ""
    assert keystore_mod.unprotect("") == ""
    assert keystore_mod.is_protected("") is False


def test_protecting_twice_does_not_double_wrap():
    once = keystore_mod.protect("KEY123")
    assert keystore_mod.protect(once) == once


def test_legacy_plaintext_passes_through_so_existing_installs_keep_working():
    assert keystore_mod.unprotect("KEY123") == "KEY123"


def test_a_blob_that_cannot_be_unwrapped_reads_as_no_credential():
    """The wrong-user / moved-machine case. Empty is the safe answer: the
    source's has_credentials() then reports False and it skips with its
    normal hint, instead of a mangled key reaching the API.
    """
    assert keystore_mod.unprotect("dpapi:not-valid-base64!!") == ""
    assert keystore_mod.unprotect("dpapi:QUJDREVG") == ""


# ------------------------------------------------------ config integration ---

def _saved_json(home: Any) -> dict[str, Any]:
    return json.loads((home / "config.json").read_text(encoding="utf-8"))


def test_the_key_is_not_readable_in_config_json(tmp_path):
    cfg = config.defaults()
    cfg["credentials"]["usajobs"]["api_key"] = "SUPERSECRET123"
    config.save(cfg, tmp_path)

    on_disk = _saved_json(tmp_path)["credentials"]["usajobs"]["api_key"]
    if keystore_mod.available():
        assert "SUPERSECRET123" not in json.dumps(_saved_json(tmp_path))
        assert keystore_mod.is_protected(on_disk)
    else:
        # Documented fallback: a secret that cannot be read back would be
        # worse than one stored plainly.
        assert on_disk == "SUPERSECRET123"


def test_load_returns_the_plain_secret_so_collectors_need_no_changes(tmp_path):
    cfg = config.defaults()
    cfg["credentials"]["usajobs"]["api_key"] = "SUPERSECRET123"
    cfg["credentials"]["usajobs"]["email"] = "me@example.com"
    config.save(cfg, tmp_path)

    loaded = config.load(tmp_path)
    assert loaded["credentials"]["usajobs"]["api_key"] == "SUPERSECRET123"
    assert loaded["credentials"]["usajobs"]["email"] == "me@example.com"


def test_save_does_not_mutate_the_config_the_caller_still_holds(tmp_path):
    """`config set` saves and then keeps using the same dict - swapping its
    live secret for a blob underneath it would hand the caller a key it
    cannot use.
    """
    cfg = config.defaults()
    cfg["credentials"]["usajobs"]["api_key"] = "SUPERSECRET123"
    config.save(cfg, tmp_path)
    assert cfg["credentials"]["usajobs"]["api_key"] == "SUPERSECRET123"


def test_an_existing_plaintext_config_is_upgraded_on_the_next_save(tmp_path):
    (tmp_path / "config.json").write_text(json.dumps(
        {"credentials": {"usajobs": {"email": "me@example.com",
                                      "api_key": "LEGACYKEY"}}}), encoding="utf-8")

    loaded = config.load(tmp_path)
    assert loaded["credentials"]["usajobs"]["api_key"] == "LEGACYKEY"

    config.save(loaded, tmp_path)
    on_disk = _saved_json(tmp_path)["credentials"]["usajobs"]["api_key"]
    if keystore_mod.available():
        assert keystore_mod.is_protected(on_disk)
    else:
        assert on_disk == "LEGACYKEY"


def test_non_secret_config_is_untouched(tmp_path):
    cfg = config.defaults()
    cfg["search"]["salary_floor"] = 70000
    cfg["search"]["title_include"] = ["HR Specialist"]
    config.save(cfg, tmp_path)

    saved = _saved_json(tmp_path)
    assert saved["search"]["salary_floor"] == 70000
    assert saved["search"]["title_include"] == ["HR Specialist"]
