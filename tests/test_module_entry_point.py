"""`python -m unlatched` has to work from a source checkout.

The desktop app's development fallback spawns exactly that command, so a
missing package entry point does not fail loudly in one place - it makes
every engine action in the app silently unavailable for anyone running
from source.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _run(args, home):
    # S603: the whole point of this test is spawning our own interpreter
    # against our own package; nothing here comes from user input.
    return subprocess.run(  # noqa: S603
        [sys.executable, "-m", "unlatched", "--home", str(home), *args],
        cwd=str(REPO_ROOT), capture_output=True, text=True, check=False)


def test_module_entry_point_reports_version(tmp_path):
    result = subprocess.run(  # noqa: S603 - same fixed invocation as above
        [sys.executable, "-m", "unlatched", "--version"],
        cwd=str(REPO_ROOT), capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    assert "unlatched" in result.stdout


def test_module_entry_point_runs_a_verb(tmp_path):
    result = _run(["init"], tmp_path / "home")
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "home" / "config.json").is_file()
    assert (tmp_path / "home" / "unlatched.db").is_file()


def test_module_entry_point_supports_json_output(tmp_path):
    home = tmp_path / "home"
    assert _run(["init"], home).returncode == 0
    result = _run(["jobs", "--json"], home)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().startswith("[")


def test_refresh_check_reports_a_decision_without_collecting(tmp_path):
    """The app spawns this on open. It must answer, cleanly, on a profile
    with nothing in it - and answering is all --check may do.
    """
    home = tmp_path / "home"
    assert _run(["init"], home).returncode == 0
    result = _run(["refresh", "--check", "--json"], home)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    # wake_in_seconds joined due/reason so the app can sleep until the next
    # anchor instead of polling to find out nothing changed. Asserted as an
    # EXACT set on purpose: this payload is a contract the desktop parses, and
    # a field appearing or vanishing unnoticed is how the two halves drift.
    assert set(payload) == {"due", "reason", "wake_in_seconds"}
    assert payload["reason"]
    assert payload["wake_in_seconds"] > 0


def test_refresh_is_switched_off_by_the_setting(tmp_path):
    home = tmp_path / "home"
    assert _run(["init"], home).returncode == 0
    assert _run(["config", "set", "refresh.daily", "false"], home).returncode == 0
    result = _run(["refresh", "--json"], home)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["due"] is False
    assert "switched off" in payload["reason"]
