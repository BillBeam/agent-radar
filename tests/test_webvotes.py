"""Web 投票轮询器 — /votes → record_feedback 与 `radar mark` 逐键一致；游标推进；坏票隔离。"""
from __future__ import annotations

import json
from types import SimpleNamespace

import radar.serve.webvotes as WV
from radar.core.feedback import record_feedback


def _wire(tmp_path, monkeypatch, items):
    date = "2026-07-07"
    digests = tmp_path / "digests"
    digests.mkdir()
    (digests / f"{date}.items.json").write_text(json.dumps(items), encoding="utf-8")
    state = tmp_path / "state"
    state.mkdir()
    fb = tmp_path / "feedback"
    fb.mkdir()
    import radar.core.feedback as F
    import radar.serve.listener as L
    monkeypatch.setattr(F.Paths, "feedback", fb, raising=True)
    monkeypatch.setattr(L.Paths, "digests", digests, raising=True)
    monkeypatch.setattr(WV, "_CURSOR", state / "web_votes_cursor.json", raising=True)
    return date, fb, state


class _FakeSession:
    def __init__(self, votes):
        self.votes = votes
        self.calls = []

    def get(self, url, **kw):
        self.calls.append((url, kw.get("params")))
        return SimpleNamespace(status_code=200, raise_for_status=lambda: None,
                               json=lambda: {"votes": self.votes})


def test_poll_once_writes_feedback_identical_to_mark(tmp_path, monkeypatch):
    item = {"id": "deadbeef01", "title": "T", "source_name": "arXiv",
            "tags": ["agent"], "url": "https://x/1"}
    date, fb, state = _wire(tmp_path, monkeypatch, [item])

    sess = _FakeSession([{"date": date, "item_id": "deadbeef01", "vote": "up", "ts": 1111}])
    n = WV.poll_once("https://site.example", "tok", session=sess)
    assert n == 1
    web_snap = json.loads((fb / f"{date}.json").read_text())["deadbeef01"]

    # same item voted via `radar mark` path → byte-identical structure (ts differs by clock)
    record_feedback(date, item, "up")
    mark_snap = json.loads((fb / f"{date}.json").read_text())["deadbeef01"]
    assert set(web_snap) == set(mark_snap)
    for k in ("vote", "title", "source", "tags", "url"):
        assert web_snap[k] == mark_snap[k]

    # cursor advanced → next poll asks since=1111 and records nothing new
    assert json.loads((state / "web_votes_cursor.json").read_text()) == {"ts": 1111}
    sess2 = _FakeSession([])
    assert WV.poll_once("https://site.example", "tok", session=sess2) == 0
    assert sess2.calls[0][1] == {"since": 1111}


def test_poll_once_isolates_malformed_and_survives_network_error(tmp_path, monkeypatch):
    item = {"id": "deadbeef01", "title": "T", "source_name": "s", "tags": [], "url": "u"}
    date, fb, state = _wire(tmp_path, monkeypatch, [item])
    sess = _FakeSession([
        {"date": date, "item_id": "deadbeef01", "vote": "sideways", "ts": 5},   # bad vote → skip
        {"date": date, "item_id": "deadbeef01", "vote": "down", "ts": 7},
    ])
    assert WV.poll_once("https://site.example", "tok", session=sess) == 1
    assert json.loads((fb / f"{date}.json").read_text())["deadbeef01"]["vote"] == "down"

    class _Boom:
        def get(self, *a, **kw):
            raise OSError("net down")

    # never raises; -1 tells the loop to back off (503-before-KV / net down)
    assert WV.poll_once("https://site.example", "tok", session=_Boom()) == -1


def test_read_token_is_derived_not_secret():
    tok = WV.read_token("s3cret")
    assert tok != "s3cret" and len(tok) == 32
    import hashlib
    import hmac as _hmac
    assert tok == _hmac.new(b"s3cret", b"vote-read", hashlib.sha256).hexdigest()[:32]
