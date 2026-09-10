"""Lets the package run as `python -m unlatched`.

The installed console script (`unlatched`) and the frozen engine both call
cli.main directly, but a source checkout has neither: the desktop app's
development fallback invokes `python -m unlatched <verb>`, and so does
anyone who has cloned the repository without installing it. Without this
module that invocation fails outright.
"""
from __future__ import annotations

import sys

from unlatched.cli import main

if __name__ == "__main__":
    sys.exit(main())
