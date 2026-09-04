"""One fetch-size choke point, MAX_FETCH_BYTES, enforced in a
single place every parser shares. Without it a single pathological page can
stall an entire collection run. Reads max_bytes + 1 so an oversize body is
DETECTED and truncated rather than silently accepted at exactly the cap.

Monkeypatches the opener rather than opening a real socket - fetch.py is
exercised for real, nothing about the network is.

`allow_private=True` on every call here is not about addresses: it short-
circuits the destination check before it can resolve a name, which keeps this
file's promise that nothing touches the network. What that check does is
tested in test_link_safety.py and test_redirect_policy.py.
"""
from __future__ import annotations

import urllib.request

from unlatched import fetch


class _FakeResponse:
    def __init__(self, body: bytes, status: int = 200, url: str = "https://example.com/x"):
        self._body = body
        self.status = status
        self._url = url

    def read(self, n=-1):
        if n is None or n < 0:
            chunk, self._body = self._body, b""
            return chunk
        chunk, self._body = self._body[:n], self._body[n:]
        return chunk

    def geturl(self):
        return self._url

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _serve(monkeypatch, body: bytes) -> None:
    """Answer every request with `body`.

    Patches OpenerDirector.open, not urllib.request.urlopen: fetch() builds a
    per-call opener so it can install a redirect guard that carries THIS
    call's policy, so urlopen is no longer on the path.
    """
    def fake_open(_self, _req, data=None, timeout=None):
        return _FakeResponse(body)

    monkeypatch.setattr(urllib.request.OpenerDirector, "open", fake_open)


def test_oversize_response_is_truncated_at_max_bytes(monkeypatch):
    _serve(monkeypatch, b"x" * 5000)
    status, text, _ = fetch.fetch("https://example.com/huge", max_bytes=1000,
                                   respect_robots=False, per_host_delay_s=0,
                                   allow_private=True)
    assert status == 200
    assert len(text) == 1000


def test_response_under_the_cap_is_not_truncated(monkeypatch):
    _serve(monkeypatch, b"y" * 500)
    status, text, _ = fetch.fetch("https://example.com/small", max_bytes=1000,
                                   respect_robots=False, per_host_delay_s=0,
                                   allow_private=True)
    assert status == 200
    assert len(text) == 500


def test_response_exactly_at_the_cap_is_not_marked_truncated(monkeypatch):
    _serve(monkeypatch, b"z" * 1000)
    status, text, _ = fetch.fetch("https://example.com/exact", max_bytes=1000,
                                   respect_robots=False, per_host_delay_s=0,
                                   allow_private=True)
    assert status == 200
    assert len(text) == 1000


def test_a_gzipped_body_is_decompressed_and_still_capped(monkeypatch):
    """Red-team finding M5. Gzip used to be attempted in sitemap.py against
    already-decoded text, where the magic bytes had become U+FFFD and the
    branch could never fire - every gzipped sitemap silently read as nothing.

    The cap has to survive the move: decompressing without one turns a small
    response into however many gigabytes the sender chose.
    """
    import gzip as gzip_mod

    _serve(monkeypatch, gzip_mod.compress(b"<loc>https://example.com/jobs/1</loc>"))
    status, text, _ = fetch.fetch("https://example.com/sitemap.xml.gz",
                                   respect_robots=False, per_host_delay_s=0,
                                   allow_private=True)
    assert status == 200
    assert "https://example.com/jobs/1" in text

    _serve(monkeypatch, gzip_mod.compress(b"A" * 5_000_000))
    _status, bomb, _ = fetch.fetch("https://example.com/bomb.gz", max_bytes=1000,
                                    respect_robots=False, per_host_delay_s=0,
                                    allow_private=True)
    assert len(bomb) == 1000, "a decompression bomb is capped like anything else"
