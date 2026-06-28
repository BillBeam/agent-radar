"""Shared feedback writer — structure, last-write-wins, and mark↔callback contract parity."""
from __future__ import annotations

import json

from radar.core.feedback import record_feedback
from radar.core.io import atomic_write_json


def _item(id="abc", title="T", source="S", tags=None, url="http://u"):
    return {"id": id, "title": title, "source_name": source,
            "tags": ["x", "y"] if tags is None else tags, "url": url}


def test_record_feedback_shape(tmp_path, monkeypatch):
    import radar.core.feedback as F
    monkeypatch.setattr(F.Paths, "feedback", tmp_path)
    snap = record_feedback("2026-06-28", _item(), "up")
    assert set(snap) == {"vote", "ts", "title", "source", "tags", "url"}
    assert snap["vote"] == "up" and snap["title"] == "T" and snap["source"] == "S"
    assert snap["tags"] == ["x", "y"] and snap["url"] == "http://u"
    assert json.loads((tmp_path / "2026-06-28.json").read_text())["abc"] == snap


def test_record_feedback_last_write_wins(tmp_path, monkeypatch):
    import radar.core.feedback as F
    monkeypatch.setattr(F.Paths, "feedback", tmp_path)
    record_feedback("2026-06-28", _item(), "up")
    record_feedback("2026-06-28", _item(), "down")          # same id → flip vote
    data = json.loads((tmp_path / "2026-06-28.json").read_text())
    assert len(data) == 1 and data["abc"]["vote"] == "down"


def test_record_feedback_accumulates_distinct(tmp_path, monkeypatch):
    import radar.core.feedback as F
    monkeypatch.setattr(F.Paths, "feedback", tmp_path)
    record_feedback("2026-06-28", _item(id="a"), "up")
    record_feedback("2026-06-28", _item(id="b"), "down")
    assert set(json.loads((tmp_path / "2026-06-28.json").read_text())) == {"a", "b"}


def test_mark_and_callback_write_identical_shape(tmp_path, monkeypatch):
    """The whole point: `radar mark` and the DingTalk callback go through the SAME writer,
    so their feedback entries are identical in structure (keys + value types) — guaranteed by
    construction, this test just locks it against future drift."""
    from radar.core import config as Cfg
    from radar.cli import cmd_mark
    monkeypatch.setattr(Cfg.Paths, "feedback", tmp_path)   # Paths is one class → covers both paths
    monkeypatch.setattr(Cfg.Paths, "digests", tmp_path)
    item = _item(id="zzz", title="Z", source="S2", tags=["t"], url="http://z")
    atomic_write_json(tmp_path / "2026-06-28.items.json", [item])

    assert cmd_mark(["2026-06-28", "1", "--up"]) == 0       # path 1: terminal mark
    mark_entry = json.loads((tmp_path / "2026-06-28.json").read_text())["zzz"]

    (tmp_path / "2026-06-28.json").unlink()
    cb_entry = record_feedback("2026-06-28", item, "up")   # path 2: as the Stream handler calls it

    assert set(mark_entry) == set(cb_entry)                                       # same keys
    assert {k: type(v).__name__ for k, v in mark_entry.items()} == \
           {k: type(v).__name__ for k, v in cb_entry.items()}                     # same value types
    for k in ("vote", "title", "source", "tags", "url"):
        assert mark_entry[k] == cb_entry[k]                                       # same values (ts may differ)
