"""The version is written in more than one file, so they have to agree.

WHY THIS TEST EXISTS. The version was defined in three places and drifted at
the first opportunity. packaging/installer.iss carried its own
`#define MyAppVersion "0.1.1"`, so bumping the build to 0.1.2 produced 0.1.2
content named `Unlatched-Setup-0.1.1.exe` - overwriting the previous release's
installer on disk with different bytes under its name. The build reported
success; only a missing-file check noticed. pyproject.toml was still saying
0.1.1 at the same time.

installer.iss no longer holds a version at all - build_release.py passes it in
with -D - so two definitions are left, and this is what keeps them equal.
build_release.APP_VERSION is the source of truth because it is what names the
artifacts.
"""
from __future__ import annotations

import re
from pathlib import Path

APP = Path(__file__).resolve().parent.parent
BUILD_RELEASE = APP / "packaging" / "build_release.py"
PYPROJECT = APP / "pyproject.toml"
INSTALLER = APP / "packaging" / "installer.iss"
# The two this test did NOT cover for its first month, and one of them drifted
# 28 releases while it watched the other two. __init__ is the worse of the two:
# fetch.py builds the User-Agent from it, so a stale value is not a display
# problem, it is what this app tells every employer's server it is.
PACKAGE = APP / "unlatched" / "__init__.py"
CARGO = APP / "desktop" / "Cargo.toml"

# The documents a reader is handed. These were never in this test's scope, and
# README.md carried `Unlatched-Setup-0.1.1.exe` - the exact literal from the
# incident above - for twenty-eight releases after it.
DOCS = ("README.md", "BUILDING.md", "COLLECTORS.md", "tests/README.md")

# An artifact filename with a version baked into it. Deliberately NOT "any
# version-shaped digits": a sentence about what 0.1.28 changed stays true for
# ever, while a filename is wrong the next time anybody builds.
VERSIONED_ARTIFACT = re.compile(r"Unlatched[-\w]*-\d+\.\d+\.\d+")


def _one(pattern: str, path: Path) -> str:
    """The single captured value, or a failure naming the file.

    Asserts there is EXACTLY one match rather than taking the first: a second
    definition appearing in one of these files is the whole failure mode here,
    and `.group(1)` on the first match would sail straight past it.
    """
    # MULTILINE, because every pattern here is anchored at the start of a LINE
    # and without it `^` only matches the start of the file - so the first
    # version of this test reported "found 0" against a file that plainly
    # states its version.
    found = re.findall(pattern, path.read_text(encoding="utf-8"),
                       flags=re.MULTILINE)
    assert len(found) == 1, (
        f"{path.name}: expected exactly one version statement, found "
        f"{len(found)}: {found}")
    return found[0]


def test_every_file_that_states_the_version_states_the_same_one():
    """ALL FOUR, not the two this used to compare.

    build_release.APP_VERSION is the source of truth because it names the
    artifacts. The others have to agree with it:
      pyproject.toml   what pip reports for an installed package
      __init__.py      what `--version` prints AND what fetch.py puts in the
                       User-Agent - the one that goes out on the wire
      Cargo.toml       what the desktop exe's PE metadata carries
    """
    build = _one(r'^APP_VERSION\s*=\s*"([^"]+)"', BUILD_RELEASE)
    stated = {
        "packaging/build_release.py": build,
        "pyproject.toml": _one(r'^version\s*=\s*"([^"]+)"', PYPROJECT),
        "unlatched/__init__.py": _one(r'^__version__\s*=\s*"([^"]+)"', PACKAGE),
        "desktop/Cargo.toml": _one(r'^version\s*=\s*"([^"]+)"', CARGO),
    }
    disagree = {name: v for name, v in stated.items() if v != build}
    assert not disagree, (
        f"these disagree with build_release.py ({build}): {disagree}. The "
        f"artifacts are named from build_release.py, and __init__.py is what "
        f"the engine reports to every server it contacts.")


def test_the_installer_script_no_longer_pins_a_version():
    """The fix for the drift, asserted rather than trusted.

    A bare `#define MyAppVersion "1.2.3"` here would silently become a third
    source of truth again - and it would only be noticed the next time somebody
    bumped the version and looked closely at the output filename.
    """
    text = INSTALLER.read_text(encoding="utf-8")
    assert "#ifndef MyAppVersion" in text, (
        "installer.iss should keep an #ifndef fallback so a hand-compile still "
        "works")

    # WALKS THE #ifndef BLOCK rather than pattern-matching indentation. The
    # fallback define is legitimate and lives inside the guard; only a define
    # OUTSIDE it is a second source of truth. Keying on leading whitespace
    # would have failed the guarded one - which is exactly what it did.
    outside: list[str] = []
    depth = 0
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(("#ifndef", "#ifdef")):
            depth += 1
            continue
        if stripped.startswith("#endif"):
            depth = max(0, depth - 1)
            continue
        found = re.match(r'#define\s+MyAppVersion\s+"([^"]+)"', stripped)
        if found and depth == 0:
            outside.append(found.group(1))
    assert not outside, (
        f"installer.iss defines MyAppVersion outside an #ifndef, as {outside}. "
        f"That is a second source of truth: it is what produced 0.1.2 content "
        f"named Unlatched-Setup-0.1.1.exe.")


def test_no_document_names_an_artifact_with_a_version_in_it():
    """A filename in prose cannot be bumped by the build that renames the file.

    README.md's hash check is the sharp case. It is the one command this
    project offers a reader who has been told, correctly, not to trust the
    download - and it named the 0.1.1 installer while the release was 0.1.29.
    Somebody following it gets an error about a missing file from the step that
    was supposed to reassure them.

    `<version>` reads as a placeholder to a human and stays right for ever,
    which is why the fix is a placeholder rather than the current number.
    """
    for name in DOCS:
        text = (APP / name).read_text(encoding="utf-8")
        found = VERSIONED_ARTIFACT.findall(text)
        assert not found, (
            f"{name} names {found}, which is wrong the next time the version "
            "moves. Use a `<version>` placeholder instead.")


def test_that_search_would_find_a_versioned_filename():
    """A positive control: the check above is a search for something ABSENT, so
    it passes just as happily against a pattern that matches nothing."""
    assert VERSIONED_ARTIFACT.findall("see dist/Unlatched-Setup-0.1.1.msi here")
    assert VERSIONED_ARTIFACT.findall("Unlatched-0.1.29-portable-win64.zip")
    # ...and a sentence about a release is deliberately not a match.
    assert not VERSIONED_ARTIFACT.findall("0.1.28 added the closures hand-back")
    for name in DOCS:
        assert (APP / name).is_file(), f"{name} is gone - its check is vacuous"
