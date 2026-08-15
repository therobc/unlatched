"""The engine has to say what it is doing WHILE it does it.

The first user, watching a collect run with nothing on screen: "There should be
indication that it is collecting."

The lines existed. What did not exist was any guarantee they would arrive
before the run ended - Python block-buffers stdout whenever it is not a
terminal, and the desktop app runs the engine with its stdout on a pipe.
"""
from __future__ import annotations

import io

import pytest

from unlatched import cli


class _Recording(io.TextIOBase):
    """A stream that remembers whether anybody asked it to line-buffer."""

    def __init__(self):
        super().__init__()
        self.line_buffering_set = False

    def reconfigure(self, **kwargs):
        if kwargs.get("line_buffering"):
            self.line_buffering_set = True


class _Stubborn(io.TextIOBase):
    """A stream that refuses to be reconfigured, as a frozen build's can."""

    def reconfigure(self, **kwargs):
        msg = "cannot reconfigure this stream"
        raise ValueError(msg)


def test_output_is_line_buffered_so_progress_arrives_as_it_happens(monkeypatch):
    """Without this, a twenty-minute collect prints nothing for twenty minutes
    and the app faithfully displays the nothing it was sent."""
    out, err = _Recording(), _Recording()
    monkeypatch.setattr(cli.sys, "stdout", out)
    monkeypatch.setattr(cli.sys, "stderr", err)

    cli.line_buffer_output()

    assert out.line_buffering_set, "stdout must be line buffered"
    assert err.line_buffering_set, "stderr too - errors are progress as well"


def test_a_stream_that_cannot_be_reconfigured_does_not_stop_the_run(monkeypatch):
    """Being unable to set buffering is never a reason to refuse to work. A
    frozen build can hand us a stdout that raises here, and the collect still
    has to happen - it just scrolls late."""
    monkeypatch.setattr(cli.sys, "stdout", _Stubborn())
    monkeypatch.setattr(cli.sys, "stderr", _Stubborn())

    cli.line_buffer_output()  # must not raise


@pytest.mark.usefixtures("home")
def test_a_company_is_announced_before_it_is_read(con, capsys, monkeypatch):
    """The result line says what a company YIELDED, which is no help while
    that company is the one taking the time. A log whose last line names the
    previous employer looks like a run that has stalled.

    Driven through cli.main, the way the desktop invokes it, rather than by
    reaching into _collect - so this also proves the command still reaches the
    collect path at all.
    """
    from unlatched import db

    db.upsert_company(con, "Slowboard", ats="greenhouse", ats_ref="slowboard")
    con.commit()

    seen = []

    class _Collector:
        @staticmethod
        def collect(ats_ref, fetcher=None):
            # Whatever has been printed by the time the fetch STARTS is what a
            # person staring at the window can see WHILE it runs. Read after
            # the fact, this assertion would pass on the old behaviour too.
            seen.append(capsys.readouterr().out)
            return []

    monkeypatch.setattr(cli.sources, "registry", lambda: {"greenhouse": _Collector})

    # No --home: the `home` fixture sets UNLATCHED_HOME, which is the route a
    # real install takes anyway.
    cli.main(["collect"])

    assert seen, "the collector was never called - the test proves nothing"
    assert "Slowboard" in seen[0], (
        "the company must be named BEFORE its fetch begins, not after it ends")
    assert "reading" in seen[0]
