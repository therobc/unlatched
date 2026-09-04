"""runlog.py - a durable, timestamped record of what a collect did.

WHY THIS EXISTS. A collect announced each employer to stdout and kept nothing.
The desktop reads those lines into memory and drops them when it closes, so
after the fact there was no record at all: a run that took eleven hours and
forty-seven minutes had to be reconstructed from the timestamps on the rows it
wrote, which can only show where the run GOT TO, never where it was stuck. Nine
of those hours had no rows at all, and there is still no way to say which
employer held it.

WHAT IT RECORDS, and the choice is the whole point: the moment an employer is
STARTED, not only what it yielded. A log whose last line is the previous
employer's result looks identical whether the next one is slow or the run has
died. With a start line, the stuck employer is named in the file while it is
still stuck.

PLAIN TEXT, FIXED COLUMNS. A person reading this is asking "what happened and
when", usually in a hurry and often in Notepad. JSON would be better for a
program and worse for that.

NEVER FATAL. Logging is not the job; collecting is. Every write is guarded, and
a log that cannot be opened degrades to doing nothing rather than taking the
run down with it - the failure mode of a diagnostic that kills the thing it was
added to diagnose is worse than the blindness it was fixing.
"""
from __future__ import annotations

import contextlib
import time
from datetime import datetime
from typing import TYPE_CHECKING

from . import paths

if TYPE_CHECKING:
    import os
    from pathlib import Path

# Runs to keep. Enough to cover a fortnight of daily collects and any manual
# ones alongside them, without becoming a directory nobody dares open.
KEEP_RUNS = 30


class RunLog:
    """One file per collect. Opened lazily, closed by `finish`."""

    def __init__(self, home: str | os.PathLike[str] | None = None,
                 kind: str = "collect") -> None:
        self._started = time.monotonic()
        self._handle = None
        self.path: Path | None = None
        try:
            # THE PACKAGE'S OWN RULE, not this module's. --home is optional, so
            # callers routinely pass None; db.connect and config.load both go
            # through resolve_home for that reason, and a second answer here
            # would put the log somewhere the database is not.
            folder = paths.resolve_home(home) / "logs"
            folder.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")  # noqa: DTZ005 - filename
            self.path = folder / f"{kind}-{stamp}.log"
            # Line buffered, so a run that is killed still leaves everything it
            # had written. A crashed run's last line is the interesting one and
            # a full buffer would take exactly that away.
            self._handle = self.path.open("w", encoding="utf-8", buffering=1)
            _prune(folder, kind)
        except OSError:
            self._handle = None

    # -- writing ----------------------------------------------------------
    def line(self, text: str) -> None:
        """One record: local time, elapsed since the run began, and the text."""
        if self._handle is None:
            return
        elapsed = time.monotonic() - self._started
        stamp = datetime.now().strftime("%H:%M:%S")  # noqa: DTZ005 - local wall clock
        try:
            self._handle.write(f"{stamp}  {_hms(elapsed)}  {text}\n")
        except OSError:
            # A disk that filled or a file that vanished under us. The run
            # continues blind rather than dying of its own logging.
            self._handle = None

    def start(self, employers: int, boards: int, ceiling_minutes: float) -> None:
        """BOTH KINDS OF WORK IN ONE LINE. A run does employers with their own
        boards and then the whole-board sources that belong to no employer;
        a header counting only the first read "0 employer(s)" over a run that
        went on to collect 139 postings.
        """
        when = datetime.now().strftime("%Y-%m-%d %H:%M:%S")  # noqa: DTZ005
        limit = f"{ceiling_minutes:g} min" if ceiling_minutes > 0 else "none"
        self.line(f"run started {when} local - {employers} employer(s), "
                  f"{boards} whole-board source(s), ceiling {limit}")

    def employer_start(self, name: str, ats: str) -> None:
        """Written BEFORE the fetch. See the module docstring - this is the
        line that names the employer a stalled run is stuck on.
        """
        self._employer_at = time.monotonic()
        self.line(f"  -> {name}  [{ats}] reading")

    def employer_done(self, name: str, collected: int, qualified: int,
                      stored: int | None = None) -> None:
        """What this board yielded. `collected` is what it OFFERED.

        `stored` is what was written down, and it is named only when it differs
        - a board that offers 907 postings and stores 3 is working exactly as
        intended, and a line reporting only the 907 sends the next reader
        looking for 904 rows that were never meant to exist.
        """
        dropped = "" if stored is None or stored >= collected else \
            f", {collected - stored} not kept"
        self.line(f"     {name}  {collected} collected, {qualified} qualified"
                  f"{dropped}  ({_hms(self._since_employer())})")

    def employer_error(self, name: str, error: str) -> None:
        self.line(f"     {name}  ERROR {error}  ({_hms(self._since_employer())})")

    def finish(self, reason: str) -> None:
        self.line(f"run ended: {reason}  (total {_hms(time.monotonic() - self._started)})")
        if self._handle is not None:
            # A file that has already gone (a full disk, a deleted folder) is
            # not worth a second failure on the way out.
            with contextlib.suppress(OSError):
                self._handle.close()
            self._handle = None

    # -- internals --------------------------------------------------------
    _employer_at: float = 0.0

    def _since_employer(self) -> float:
        return time.monotonic() - (self._employer_at or self._started)


def _hms(seconds: float) -> str:
    """Elapsed as h:mm:ss. The unit that made the incident legible - "11:46:56"
    says something "42413.2s" does not.
    """
    total = int(seconds)
    return f"{total // 3600}:{(total % 3600) // 60:02d}:{total % 60:02d}"


def _prune(folder: Path, kind: str) -> None:
    """Keep the newest KEEP_RUNS files of this kind.

    BY NAME, NOT BY MTIME. The names are timestamps, so sorting them sorts by
    run time; mtime would reorder a file that something else touched and could
    delete the run somebody was in the middle of reading.
    """
    try:
        files = sorted(folder.glob(f"{kind}-*.log"))
        for old in files[:-KEEP_RUNS]:
            old.unlink(missing_ok=True)
    except OSError:
        pass
