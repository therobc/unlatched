"""Entry point PyInstaller freezes into unlatched-engine.exe.

This is not a public API: it just calls the same unlatched.cli:main that
the "unlatched" console script calls for a normal pip install. It exists
only because PyInstaller needs a script file to analyze, not an
importable console-script entry point.
"""
from __future__ import annotations

import sys

from unlatched.cli import main

if __name__ == "__main__":
    sys.exit(main())
