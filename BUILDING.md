# Building Unlatched

Unlatched ships as two pieces that talk to the same SQLite database and
`config.json`: a Python engine (the `unlatched` command-line tool) and a
Rust desktop app (`desktop/`, built with egui). This document covers
both the everyday developer setup and how the Windows release packaging
works.

## Developer setup

You need Python 3.11+ and a stable Rust toolchain.

```
pip install -e ".[dev]"
pytest
```

That installs the `unlatched` console script in editable mode, plus
pytest for the regression suite in `tests/`.

For the desktop app:

```
cd desktop
cargo build
cargo run
```

The desktop app spawns the engine as a child process to run long-running
commands (discover, collect, etc.) and streams the output into a log
pane. In a source checkout it runs the engine the same way you would
from a terminal: a configured Python interpreter with `unlatched`'s
`-m` invocation, editable there in the Companies view. There is no
PyInstaller step in this loop; you are always running the plain Python
package.

`cargo run` picks up the desktop app's profile registry (`profiles.json`)
from the platform-default home, not from any `UNLATCHED_HOME` you may have
exported in the same shell for CLI testing: that env var, if set, instead
locks the whole run to that folder as a read-only "(env)" profile, which is
also how the GUI QC harness gets an isolated app instance. Unset it if you
want `cargo run` to behave like a normal end-user launch with the profile
switcher enabled.

## Release packaging (Windows)

End users do not have Python installed and should never need to know
they are running one. The release build freezes the engine into a
standalone executable with PyInstaller and wraps everything in a
Setup wizard with Inno Setup, so installing Unlatched is: download,
double-click, Next, Next, Finish.

Prerequisites beyond the developer setup above:

```
pip install pyinstaller
```

and, for the installer step, [Inno Setup 6](https://jrsoftware.org/isinfo.php)
(the free `ISCC.exe` command-line compiler it installs).

Then, from the repository root:

```
python packaging/build_release.py
```

This runs, in order:

1. `cargo build --release` for `desktop/`, with `RUSTFLAGS` set to remap
   the build machine's absolute paths out of the compiled binary.
2. `pyinstaller packaging/engine.spec`, an onedir build of the CLI
   (entry point `unlatched.cli:main`) named `unlatched-engine`.
3. Assembly of `dist/portable/Unlatched/`: the desktop exe renamed to
   `Unlatched.exe`, the frozen `engine/` folder, `LICENSE`, and
   `README.md`. That folder is a complete, self-contained install; it is
   also zipped to `dist/Unlatched-<version>-portable-win64.zip` for anyone
   who would rather not run an installer at all.
4. If `ISCC.exe` can be found (a standard install location, or the
   `ISCC` environment variable), compiling `packaging/installer.iss`
   into `dist/Unlatched-Setup-<version>.exe`. If it cannot be found, the
   script prints a note and finishes with the portable zip only; that
   zip is still a complete release on its own.

All build output lands under `dist/`, which is gitignored.

To build somewhere else - a scratch copy for testing, which never gets
distributed - pass a destination:

```
python packaging/build_release.py --dist C:/some/scratch/dir
```

### The publication-gate warning

On a plain checkout the build prints, in capitals, that no publication gate
is configured. Nothing is wrong and the build continues.

It is there because this project ships as a public repository written partly
against a private toolchain, and a private name left in a docstring survives
into the frozen engine - PyInstaller keeps docstrings, and a text search of
the finished binary reads clean regardless, because the modules are
compressed inside it. So the build asks whether anything has checked the
source before producing something other people will hold.

Set `UNLATCHED_PUBLICATION_GATE` to any command that examines the tree and
exits non-zero when it does not like what it finds, and the build will run it
first and refuse if it fails. Leave it unset and you get the warning and a
normal build, which is the right default for a fork: the rule is ours, and
you should not inherit an obstacle you never asked for.

## Why the engine is frozen for end users but stays plain Python for developers

The two audiences want opposite things from the same code. An end user
wants to double-click an installer and never see a terminal, a `pip`
command, or a version-mismatch error; a PyInstaller onedir build gives
them a self-contained `engine/` folder the desktop app can invoke
directly, no interpreter required. A developer wants to edit `unlatched/`
and immediately see the change, set breakpoints, and run `pytest`
against a package installed with `-e`; freezing that loop through
PyInstaller on every change would make development slower for no
benefit, since the developer already has Python.

The desktop app bridges both worlds itself: it looks for
`engine/unlatched-engine.exe` next to its own executable first (what the
installer lays out) and only falls back to a configured Python
interpreter if that is not there (what a source checkout looks like).
Nothing about the engine's own code differs between the two paths; only
how the desktop app launches it does.
