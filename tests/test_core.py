"""Fast unit tests — no network, no LLM. Guards the core invariants."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from radar.core.config import load_config
from radar.core.models import Item, RunContext, Source, SourceType, TimeWindow
from radar.llm._json import extract_json


def _ctx(mode="daily"):
    cfg = load_config()
    return RunContext(run_id="test", mode=mode, config=cfg, window=TimeWindow(48))


def _item(score=None, title="t", weight=1.0, cat="harness", url=None):
    s = Source(id="s", name="S", category=cat, type=SourceType.rss, url="http://x", weight=weight)
    it = Item.create(source=s, title=title, url=url or f"http://x/{title}")
    it.score = score
    return it


# ---- extract_json ----
@pytest.mark.parametrize("text,expected", [
    ('[{"i":0,"score":9}]', [{"i": 0, "score": 9}]),
    ('```json\n[{"i":1}]\n```', [{"i": 1}]),
    ('here you go: [{"i":2}] done', [{"i": 2}]),
    ('{"a": 1}', {"a": 1}),
])
def test_extract_json(text, expected):
    assert extract_json(text) == expected


def test_extract_json_raises_on_garbage():
    with pytest.raises(ValueError):
        extract_json("no json here")


# ---- freshness window ----
def test_time_window():
    w = TimeWindow(24)
    now = datetime.now(timezone.utc)
    assert w.is_fresh(now)
    assert w.is_fresh(now - timedelta(hours=12))
    assert not w.is_fresh(now - timedelta(hours=48))
    assert w.is_fresh(None)  # undated passes, judged later by triage


# ---- item id stability / dedup ----
def test_item_id_stable():
    assert _item(url="http://a").id == _item(url="http://a").id
    assert _item(url="http://a").id != _item(url="http://b").id


# ---- quality rules ----
def test_threshold_rule():
    from radar.quality.rules import ThresholdRule
    ctx = _ctx()
    items = [_item(score=9), _item(score=3), _item(score=6), _item(score=None)]
    kept = ThresholdRule().apply(items, ctx)
    assert {round(i.score or 0) for i in kept} == {9, 6}  # threshold 6.0


def test_cap_rule_sorts_and_caps():
    from radar.quality.rules import CapRule
    ctx = _ctx()
    ctx.config.daily_max_items = 2
    items = [_item(score=5), _item(score=9), _item(score=7)]
    kept = CapRule().apply(items, ctx)
    assert [i.score for i in kept] == [9, 7]


# ---- dingtalk chunker ----
def test_dingtalk_chunk_short_passthrough():
    from radar.channels.dingtalk import _chunk
    assert _chunk("short") == ["short"]


def test_dingtalk_chunk_bytes():
    from radar.channels.dingtalk import _bytes, _chunk
    md = "# h\n" + "\n".join(f"## sec {i}\n" + "x" * 5000 for i in range(6))
    parts = _chunk(md, limit=12000)
    assert len(parts) > 1
    assert all(_bytes(p) <= 12000 for p in parts)


def test_dingtalk_chunk_cjk_hard_split():
    from radar.channels.dingtalk import _bytes, _chunk
    md = "## 详解\n" + "测" * 10000  # one ~30KB CJK block must hard-split
    parts = _chunk(md, limit=9000)
    assert len(parts) >= 4
    assert all(_bytes(p) <= 9000 for p in parts)


# ---- html title cleanup ----
def test_clean_title():
    from radar.sources.html import _clean_title
    assert _clean_title("Featured How we contain Claude") == "How we contain Claude"
    long = "word " * 40
    assert _clean_title(long).endswith("…")
