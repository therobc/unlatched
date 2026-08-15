#!/usr/bin/env python3
"""Builds a Windows release of Unlatched: the desktop app, the frozen
engine, a portable zip, and (when Inno Setup is available) a Setup
wizard installer.

Stdlib only, no third-party imports beyond what the build steps
themselves invoke as subprocesses (cargo, PyInstaller). Works from a
fresh clone: paths are resolved relative to this script's own location,
never hardcoded to any one machine.

Usage (from anywhere, on Windows, with Rust and
`pip install ".[dev]" pyinstaller` already done):

    python packaging/build_release.py

Steps:
  1. cargo build --release for desktop/, with RUSTFLAGS set to remap
     this machine's absolute source paths out of the compiled binary.
  2. pyinstaller packaging/engine.spec, producing an onedir build of the
     Python CLI named unlatched-engine.
  3. Assemble dist/portable/Unlatched/ (Unlatched.exe, engine/, LICENSE,
     README.md) and zip it to
     dist/Unlatched-<version>-portable-win64.zip.
  4. If ISCC.exe (Inno Setup 6) can be found, compile
     packaging/installer.iss to dist/Unlatched-Setup-<version>.exe.
     Otherwise, print a note and finish with the portable zip only.

Everything lands under dist/, which is gitignored.
"""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

APP_NAME = "Unlatched"
APP_VERSION = "0.1.2"

REPO_ROOT = Path(__file__).resolve().parent.parent  # app/
PACKAGING_DIR = REPO_ROOT / "packaging"
DESKTOP_DIR = REPO_ROOT / "desktop"
DIST_DIR = REPO_ROOT / "dist"
PORTABLE_DIR = DIST_DIR / "portable" / APP_NAME

# True once use_sandbox has redirected the output. Read by build_installer,
# which must not run at all in that mode - see the note there.
SANDBOXED = False


def use_sandbox(dist: Path) -> None:
    """Send every output somewhere other than the shipping dist/.

    WHY THIS EXISTS. The GUI harness needs a real built app to drive, and it
    was driving the one in dist/ - the SHIPPING artifact. That makes two
    unrelated things share one directory: a QC run overwrites what a release
    was cut from, and cleaning up after QC deletes the deliverable.

    The first user, 2026-08-12, on finding sixteen Unlatched binaries on this machine:
    "Make sure that those harness exe are sandboxed so that cleanup doesn't
    effect finished product."

    So the harness builds into its own directory and nothing outside it is
    touched. A sandbox that can be deleted wholesale, at any moment, without
    looking at what is in it first.
    """
    global DIST_DIR, PORTABLE_DIR, SANDBOXED  # noqa: PLW0603 - the output root IS global state
    DIST_DIR = dist
    PORTABLE_DIR = DIST_DIR / "portable" / APP_NAME
    SANDBOXED = True


def run(cmd: list[str], cwd: Path | None = None,
        env: dict[str, str] | None = None) -> None:
    print("+", " ".join(str(c) for c in cmd))
    # S603: every command here is a fixed toolchain invocation built from
    # constants in this file; nothing user-supplied reaches argv.
    subprocess.run(cmd, cwd=str(cwd) if cwd else None, env=env, check=True)  # noqa: S603


def build_desktop() -> Path:
    """Cargo build --release for the desktop app.

    RUSTFLAGS carries --remap-path-prefix so the compiled binary never
    embeds this build machine's absolute paths (panic messages and
    debug info otherwise capture the literal source path via the
    file!() macro, cargo profile settings notwithstanding). Both this
    repo's own checkout path and the Cargo registry cache path are
    remapped, since dependency crates carry the same kind of literal
    path into the binary.
    """
    env = os.environ.copy()
    cargo_home = Path(env.get("CARGO_HOME", str(Path.home() / ".cargo")))
    remaps = [
        f"--remap-path-prefix={REPO_ROOT}=unlatched",
        f"--remap-path-prefix={cargo_home}=cargo-registry",
    ]
    existing = env.get("RUSTFLAGS", "")
    env["RUSTFLAGS"] = (existing + " " + " ".join(remaps)).strip()

    run(["cargo", "build", "--release"], cwd=DESKTOP_DIR, env=env)

    exe = DESKTOP_DIR / "target" / "release" / "unlatched-desktop.exe"
    if not exe.is_file():
        raise SystemExit(f"expected desktop exe not found: {exe}")
    return exe


def build_engine() -> Path:
    """PyInstaller onedir build of the Python CLI, from packaging/engine.spec.

    --distpath and --workpath are pointed at this script's own dist/ and
    build/ so PyInstaller's output lands next to everything else this
    script produces, instead of wherever the caller's working directory
    happens to be.
    """
    run(
        [
            sys.executable, "-m", "PyInstaller",
            "--noconfirm",
            "--distpath", str(DIST_DIR),
            "--workpath", str(REPO_ROOT / "build"),
            str(PACKAGING_DIR / "engine.spec"),
        ],
        cwd=REPO_ROOT,
    )

    onedir = DIST_DIR / "unlatched-engine"
    if not (onedir / "unlatched-engine.exe").is_file():
        raise SystemExit(f"expected engine exe not found under {onedir}")
    return onedir


def assemble_portable(desktop_exe: Path, engine_dir: Path) -> Path:
    if PORTABLE_DIR.exists():
        shutil.rmtree(PORTABLE_DIR)
    PORTABLE_DIR.mkdir(parents=True)

    shutil.copy2(desktop_exe, PORTABLE_DIR / "Unlatched.exe")
    shutil.copytree(engine_dir, PORTABLE_DIR / "engine")
    shutil.copy2(REPO_ROOT / "LICENSE", PORTABLE_DIR / "LICENSE")
    shutil.copy2(REPO_ROOT / "README.md", PORTABLE_DIR / "README.md")
    return PORTABLE_DIR


def zip_portable() -> Path:
    zip_path = DIST_DIR / f"{APP_NAME}-{APP_VERSION}-portable-win64.zip"
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(PORTABLE_DIR.rglob("*")):
            if path.is_file():
                arcname = Path(APP_NAME) / path.relative_to(PORTABLE_DIR)
                zf.write(path, arcname)
    return zip_path


def find_iscc() -> Path | None:
    """Looks for ISCC.exe (the Inno Setup 6 command-line compiler) in the
    usual places: an explicit ISCC env var, the two conventional install
    locations, or PATH. Returns None if it cannot be found anywhere.
    """
    env_path = os.environ.get("ISCC")
    if env_path and Path(env_path).is_file():
        return Path(env_path)

    candidates = []
    local_appdata = os.environ.get("LOCALAPPDATA")
    if local_appdata:
        candidates.append(Path(local_appdata) / "Programs" / "Inno Setup 6" / "ISCC.exe")
    for env_var in ("ProgramFiles", "ProgramFiles(x86)"):
        base = os.environ.get(env_var)
        if base:
            candidates.append(Path(base) / "Inno Setup 6" / "ISCC.exe")

    for candidate in candidates:
        if candidate.is_file():
            return candidate

    found = shutil.which("ISCC")
    return Path(found) if found else None


def build_installer() -> Path | None:
    # NEVER IN A SANDBOX, and this is what makes the sandbox real rather than
    # nominal. installer.iss carries its OWN hardcoded source and output paths
    # pointing at app/dist, so running it here would reach straight back out
    # and write the Setup exe into the shipping directory - the exact thing the
    # sandbox exists to prevent, and it would have done it silently.
    #
    # No loss: the harness drives the PORTABLE build. An installer is a release
    # artifact, and a release is not what QC is for.
    if SANDBOXED:
        print("sandboxed: skipping the installer, which writes to the real dist/")
        return None

    iscc = find_iscc()
    if iscc is None:
        print(
            "note: ISCC.exe (Inno Setup 6) was not found, so the Setup "
            "wizard installer was skipped. Install Inno Setup 6, or set "
            "the ISCC environment variable to its path, then re-run this "
            "script. The portable zip above is a complete release on its "
            "own."
        )
        return None

    # THE VERSION IS PASSED IN, because it was defined in two places and they
    # drifted at the first opportunity. installer.iss carried its own
    # `#define MyAppVersion`, so bumping APP_VERSION here to 0.1.2 built 0.1.2
    # content and named it Unlatched-Setup-0.1.1.exe - overwriting the previous
    # release's installer on disk with different bytes under its name.
    #
    # Caught by the missing-file check below, which is the only reason it was
    # noticed at all: the build otherwise reported success. -D overrides the
    # define in the script, so the .iss keeps a working default for anyone
    # compiling it by hand.
    run([str(iscc), f"-DMyAppVersion={APP_VERSION}",
         str(PACKAGING_DIR / "installer.iss")])

    installer = DIST_DIR / f"{APP_NAME}-Setup-{APP_VERSION}.exe"
    if not installer.is_file():
        raise SystemExit(f"ISCC ran but the expected installer is missing: {installer}")
    return installer


# READ FROM THE ENVIRONMENT, NOT HARDCODED, and that is not a style choice.
#
# THIS FILE IS IN THE PUBLIC REPOSITORY, though not in the installer - the
# installer carries only the app, the engine, the licence, the readme and the
# icon. A repository is still published, so an absolute path like
# C:/<some company>/shared/tools/... baked in here leaks a directory layout in
# the very script whose job is to stop leaks.
#
# Set UNLATCHED_PUBLICATION_GATE to a command that stages the publishable
# tree, scans it, and exits non-zero if it is not clean. Unset, this script
# says so loudly and builds anyway: somebody who forked this repo has no such
# tool and should not be blocked by one. The enforcement it belongs to does
# not live here either - it is a pre-execution hook over every project, which
# is the right altitude for it.
#
# NAMED FOR THIS APP, deliberately. The variable was named after the private
# toolchain it was written against, which told anybody reading this repo the
# name of a thing they have no other reason to know - in the file whose whole
# job is to stop exactly that.
GATE_ENV = "UNLATCHED_PUBLICATION_GATE"


def leak_gate() -> None:
    """Refuse to produce a distributable while the source names internal things.

    WHY THIS IS A GATE IN THE BUILD AND NOT A STEP IN SOMEBODY'S CHECKLIST.
    On 2026-08-13 this script produced an installer, that installer was
    installed, and a copy was set aside for a thumb drive - all on the strength
    of a green test-and-lint run. Tests, ruff, mypy and clippy check that the
    code is correct; not one of them looks for a private name in a comment. So
    the engine shipped with two dozen of them baked in, retained as Python
    docstrings because engine.spec builds with optimize=0.

    A byte search of the finished build reported CLEAN, which is how it stayed
    invisible: the modules are zlib-compressed inside the exe, so the names are
    there and a naive grep cannot see them. Scanning the SOURCE is the check
    that works.

    So the rule is not "remember to scan". It is that a build which has not
    been scanned does not exist.

    ONLY FOR REAL RELEASES. A sandboxed build is QC scaffolding that is deleted
    afterwards and never leaves this machine, and gating it would make the test
    harness depend on the publication rules.
    """
    if SANDBOXED:
        return
    gate = os.environ.get(GATE_ENV, "").strip()
    if not gate:
        # SAID LOUDLY, then continued. A fork of this repo has no gate of ours
        # and must not be blocked by it; what must never happen is a release
        # leaving here with nobody having noticed there was no check.
        print(f"\n*** NO PUBLICATION GATE CONFIGURED ({GATE_ENV} is unset). ***")
        print("*** Nothing has scanned this tree for internal names, and     ***")
        print("*** anything in a docstring compiles into the frozen engine.  ***\n")
        return
    # THE GATE STAGES, THEN SCANS. Pointing a scanner at the repo root instead
    # takes ten minutes and measures the wrong thing: the root holds
    # desktop/target, gigabytes of build output that never ships. A staging
    # step already owns the list of what is published, so scanning its copy
    # checks EXACTLY the tree that would go out, in seconds.
    print("== publication gate (stage + leak scan) ==")
    result = subprocess.run(  # noqa: S603
        [sys.executable, gate, "--no-zip"],
        capture_output=True, text=True, check=False)
    if result.returncode == 0:
        print("leak scan: clean")
        return
    tail = (result.stdout or "").strip().splitlines()[-24:]
    print("\n".join(tail))
    raise SystemExit(
        "\nREFUSING TO BUILD. The source tree still names internal things, and "
        "they would be compiled into the artifacts - Python docstrings survive "
        "into the frozen engine. Fix the hits above in the SOURCE, then re-run."
        "\n\nTo build anyway for a local, never-distributed test, pass "
        "--dist <dir> to build into a sandbox instead.")


def main() -> int:
    # PARSED BY HAND, not argparse: this script is invoked by other tools that
    # pass flags it has historically ignored, and argparse would start
    # REFUSING those rather than skipping them - turning a stray flag into a
    # failed release build.
    for i, arg in enumerate(sys.argv[1:]):
        if arg == "--dist" and i + 2 <= len(sys.argv[1:]):
            use_sandbox(Path(sys.argv[i + 2]).resolve())
            print(f"sandboxed build: everything lands under {DIST_DIR}")
            break

    if platform.system() != "Windows":
        print(
            "warning: this release build targets Windows; the desktop "
            "and engine builds are expected to fail on other platforms."
        )

    # BEFORE ANY ARTIFACT EXISTS, so a refused build leaves nothing behind that
    # could be picked up and shipped by somebody who did not read the error.
    leak_gate()

    DIST_DIR.mkdir(exist_ok=True)

    print("== building desktop app (cargo build --release) ==")
    desktop_exe = build_desktop()

    print("== freezing the engine (pyinstaller) ==")
    engine_dir = build_engine()

    print("== assembling portable build ==")
    portable_dir = assemble_portable(desktop_exe, engine_dir)
    print(f"portable build: {portable_dir}")

    print("== zipping portable build ==")
    zip_path = zip_portable()
    print(f"portable zip: {zip_path}")

    print("== building Setup wizard installer ==")
    installer = build_installer()
    if installer is not None:
        print(f"installer: {installer}")

    print("done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
