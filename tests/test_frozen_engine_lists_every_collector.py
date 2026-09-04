"""The frozen engine must carry every collector the registry can return.

packaging/engine.spec names each collector module as a PyInstaller
hiddenimport. That list is a BACKSTOP: the registry imports its modules inside
a function body, and PyInstaller finds them by static analysis, so the list is
what stops a collector being dropped from a frozen build if that analysis ever
misses one.

A backstop nothing checks is not a backstop. This list had already drifted -
oracle_hcm and usajobs were in the registry and absent from the spec - and
every gate stayed green, because a frozen build still worked and nothing
compared the two.

The failure this guards is invisible by construction: the build succeeds, the
app runs, and one collector silently collects nothing in a release.
"""
from __future__ import annotations

import re
from pathlib import Path

from unlatched import sources

SPEC = Path(__file__).resolve().parent.parent / "packaging" / "engine.spec"


def _listed_in_spec() -> set[str]:
    """The module names inside engine.spec's source_modules list."""
    text = SPEC.read_text(encoding="utf-8")
    block = text.split("source_modules = [", 1)
    assert len(block) == 2, "engine.spec no longer defines source_modules"
    body = block[1].split("]", 1)[0]
    return set(re.findall(r'"([^"]+)"', body))


def test_the_spec_names_every_collector_the_registry_returns():
    expected = {
        f"unlatched.sources.{module.__name__.rsplit('.', 1)[-1]}"
        for module in sources.registry().values()
    }
    listed = _listed_in_spec()

    missing = expected - listed
    assert not missing, (
        "these collectors are in the registry but not in engine.spec's "
        f"hiddenimports, so a frozen build has no backstop for them: {sorted(missing)}"
    )


def test_the_spec_names_nothing_the_registry_does_not():
    """A stale entry is the other half of the same drift.

    It costs nothing at build time, but it says the list was maintained when
    it was not - and the next person reads it as authoritative.
    """
    expected = {
        f"unlatched.sources.{module.__name__.rsplit('.', 1)[-1]}"
        for module in sources.registry().values()
    }
    stale = _listed_in_spec() - expected
    assert not stale, (
        f"engine.spec names collectors the registry does not have: {sorted(stale)}"
    )
