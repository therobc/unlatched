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


def test_the_build_and_the_package_state_the_same_version():
    build = _one(r'^APP_VERSION\s*=\s*"([^"]+)"', BUILD_RELEASE)
    pyproject = _one(r'^version\s*=\s*"([^"]+)"', PYPROJECT)
    assert build == pyproject, (
        f"packaging/build_release.py says {build} and pyproject.toml says "
        f"{pyproject}. The artifacts are named from build_release.py, so a "
        f"mismatch ships a file whose name disagrees with the package it "
        f"contains.")


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
