"""paths.py - Where Unlatched keeps its state.

Resolution order, highest priority first:
  1. an explicit override (the CLI's --home flag)
  2. the UNLATCHED_HOME environment variable
  3. a platform default: %APPDATA%/Unlatched on Windows, ~/.config/unlatched
     everywhere else

Nothing here ever hardcodes a path belonging to one particular machine or
person - that is the whole point of a data-dir resolver. The directory is
created on first use, not at import time, so importing this module never
touches the filesystem.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "Unlatched"
CONFIG_ENV_VAR = "UNLATCHED_HOME"


def resolve_home(override: str | os.PathLike[str] | None = None) -> Path:
    """Return the data directory, without creating it."""
    if override:
        return Path(override).expanduser()
    env = os.environ.get(CONFIG_ENV_VAR)
    if env:
        return Path(env).expanduser()
    if sys.platform.startswith("win"):
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / APP_NAME
    return Path.home() / ".config" / "unlatched"


def data_dir(override: str | os.PathLike[str] | None = None) -> Path:
    """Return the data directory, creating it (and its parents) if needed."""
    home = resolve_home(override)
    home.mkdir(parents=True, exist_ok=True)
    return home


def db_path(override: str | os.PathLike[str] | None = None) -> Path:
    return data_dir(override) / "unlatched.db"


def config_path(override: str | os.PathLike[str] | None = None) -> Path:
    return data_dir(override) / "config.json"
