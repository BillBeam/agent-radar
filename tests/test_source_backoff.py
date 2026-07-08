"""HTTP rate-limit backoff (2026-07-08 regression).

arxiv returned 429 and the old 0.8s/1.6s linear backoff hammered it and gave up in
2.4s → the source contributed 0 → the candidate pool starved to 76 → only 9 items
cleared the 6.0 floor → a 9-item digest. These tests lock in: 429/5xx back off hard
(honoring Retry-After) so a transient limit can clear, while a dead-proxy transport
error still fails fast on the cheap linear backoff.
"""
from __future__ import annotations

import requests

from radar.sources import _base as B


class _Resp:
    def __init__(self, status=200, headers=None, content=b"ok"):
        self.status_code = status
        self.headers = headers or {}
        self.content = content
        self.text = content.decode() if isinstance(content, bytes) else content

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}", response=self)


class _Session:
    """Yields queued responses / raises queued exceptions in order."""
    def __init__(self, seq):
        self.seq = list(seq)
        self.trust_env = False
        self.calls = 0

    def get(self, *a, **k):
        self.calls += 1
        item = self.seq.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class _Src(B.BaseSource):                    # BaseSource.fetch is abstract — stub it
    def fetch(self, source, window):
        return []


def _mk(monkeypatch, seq):
    src = _Src(config=None)                   # config=None → no proxy, real Session…
    src._session = _Session(seq)             # …which we swap for the scripted one
    slept: list[float] = []
    monkeypatch.setattr(B.time, "sleep", lambda s: slept.append(s))
    return src, slept


def test_429_backs_off_hard_and_recovers(monkeypatch):
    src, slept = _mk(monkeypatch, [_Resp(429), _Resp(429), _Resp(200)])
    assert src.get_bytes("http://x") == b"ok"          # succeeds on the 3rd try
    assert slept == [5.0, 15.0]                         # escalating, NOT 0.8/1.6


def test_retry_after_header_is_honored(monkeypatch):
    src, slept = _mk(monkeypatch, [_Resp(429, headers={"Retry-After": "42"}), _Resp(200)])
    assert src.get_bytes("http://x") == b"ok"
    assert slept == [42.0]                              # server's number wins over the floor


def test_429_exhausted_raises_after_backoff(monkeypatch):
    src, slept = _mk(monkeypatch, [_Resp(429), _Resp(429), _Resp(429)])
    try:
        src.get_bytes("http://x")
        assert False, "expected SourceError"
    except B.SourceError:
        pass
    assert slept == [5.0, 15.0]                         # backed off twice, then gave up


def test_503_uses_transient_backoff(monkeypatch):
    src, slept = _mk(monkeypatch, [_Resp(503), _Resp(200)])
    assert src.get_bytes("http://x") == b"ok"
    assert slept == [5.0]


def test_transport_error_still_fails_fast(monkeypatch):
    # Dead proxy → ConnectionError has no .response → cheap linear backoff, fast fail.
    err = requests.ConnectionError("proxy dead")
    src, slept = _mk(monkeypatch, [err, err, err])
    try:
        src.get_bytes("http://x")
        assert False, "expected SourceError"
    except B.SourceError:
        pass
    assert slept == [0.8, 1.6]                          # unchanged: network-down path stays fast


def test_retry_after_parsing():
    assert B._retry_after_seconds(_Resp(429, headers={"Retry-After": "10"})) == 10.0
    assert B._retry_after_seconds(_Resp(429)) is None                 # absent
    assert B._retry_after_seconds(_Resp(429, headers={"Retry-After": "99999"})) == 120.0  # capped
    assert B._retry_after_seconds(_Resp(429, headers={"Retry-After": "junk"})) is None     # unparseable
