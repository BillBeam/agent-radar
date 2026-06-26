"""Fast unit tests — no network, no LLM. Guards the core invariants."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from radar.core.config import load_config
from radar.core.models import Item, RunContext, Source, SourceType, TimeWindow
from radar.llm._json import extract_json


def _ctx(mode="daily"):
    from radar.obs import Logger, Tracer
    cfg = load_config()
    ctx = RunContext(run_id="test", mode=mode, config=cfg, window=TimeWindow(48))
    ctx.log = Logger("test", echo=False)   # no path → no-op, but stages can call .info/.warn
    ctx.trace = Tracer("test")
    return ctx


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
    ctx.config.finalist_pool = 2   # CapRule now caps to the finalist pool (rerank picks final)
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


# ---- A: presentation (titles / truncation / dingtalk-safe / headings) ----
def test_smart_truncate():
    from radar.core.text import smart_truncate
    assert smart_truncate("short", 80) == "short"
    assert smart_truncate("hello world foobar", 13) == "hello world…"   # English word boundary
    assert smart_truncate("一二三四五六七八九十", 5) == "一二三四五…"      # CJK hard cut, no over-trim


def test_strip_trailing_date():
    from radar.core.text import strip_trailing_date
    assert strip_trailing_date("How we contain Claude Apr 08, 2026") == "How we contain Claude"
    assert strip_trailing_date("Effective harnesses Nov 26 2025") == "Effective harnesses"
    assert strip_trailing_date("Some post 2026-06-21") == "Some post"
    assert strip_trailing_date("No date here") == "No date here"


def test_demote_headings():
    from radar.core.text import demote_headings
    assert demote_headings("## 背景\n正文\n### 机制\nx") == "**背景**\n正文\n**机制**\nx"


def test_link_extractor_prefers_heading():
    from radar.sources.html import _LinkExtractor
    p = _LinkExtractor()
    p.feed('<a href="/x"><h3>Real Title</h3><p>some blurb that should not mash in</p></a>')
    assert p.links == [("/x", "Real Title")]
    p2 = _LinkExtractor()
    p2.feed('<a href="/y">Just Anchor Text</a>')
    assert p2.links == [("/y", "Just Anchor Text")]


def test_clean_title():
    from radar.sources.html import _clean_title
    assert _clean_title("Featured Some Title") == "Some Title"
    assert _clean_title("How we contain Claude Apr 08, 2026") == "How we contain Claude"


def test_render_brief_dingtalk_safe():
    from radar.stages.synthesize import _render_brief
    it = _item(title="My Title", score=9)
    it.reason = "为何值得看：具体洞见"
    it.self_applicable = True
    it.target_component = "x"
    it.tags = ["a", "b"]
    b = _render_brief(it)
    assert "`" not in b and "★" not in b and "相关度" not in b   # no noise/backticks
    assert "为何值得看" in b and "[My Title](" in b and "---" in b


# ---- B: full-pool triage / rerank diversity / recency split ----
def test_triage_scores_full_pool_no_weight_cut():
    from radar.stages.triage import TriageStage
    ctx = _ctx()
    ctx.llm = None  # → fallback path, but pool must still be the FULL candidate set
    ctx.candidates = [_item(weight=0.5, title="low"), _item(weight=1.5, title="high"),
                      _item(weight=1.0, title="mid")]
    TriageStage().run(ctx)
    assert {it.title for it in ctx.items} == {"low", "high", "mid"}  # low-weight survives


def test_rerank_diversity_quota():
    from collections import Counter
    from radar.stages.rerank import RerankStage
    ctx = _ctx()
    ctx.llm = None  # → score-order, deterministic
    ctx.config.daily_max_items = 4
    ctx.config.max_per_source = 2
    items = []
    for i in range(6):
        it = _item(score=9 - i, title=f"t{i}")
        it.source_id = "A" if i < 4 else "B"
        items.append(it)
    ctx.items = items
    RerankStage().run(ctx)
    counts = Counter(it.source_id for it in ctx.items)
    assert len(ctx.items) == 4 and counts["A"] <= 2  # per-source cap enforced


def test_synthesize_recency_split(tmp_path, monkeypatch):
    import radar.stages.synthesize as S
    monkeypatch.setattr(S.Paths, "digests", tmp_path)
    from datetime import datetime, timezone
    ctx = _ctx()
    ctx.llm = None
    ctx.sources = []
    dated = _item(title="fresh one", score=9)
    dated.published_at = datetime.now(timezone.utc)
    undated = _item(title="old one", score=8)
    undated.published_at = None
    ctx.items = [dated, undated]
    S.SynthesizeStage().run(ctx)
    md = ctx.digest.markdown
    assert "🆕 今日新增" in md and "往期补课" in md and "今日新增 1" in md


# ---- E: canonical display order + mark feedback ----
def test_synthesize_canonical_order(tmp_path, monkeypatch):
    """items.json + [N] + digest.items must all follow display order (fresh→backfill),
    even when rerank interleaves them — else `mark N` maps to the wrong item."""
    import json
    from datetime import datetime, timezone
    import radar.stages.synthesize as S
    monkeypatch.setattr(S.Paths, "digests", tmp_path)
    ctx = _ctx()
    ctx.llm = None
    ctx.sources = []
    f1 = _item(title="F1", score=9)
    f1.published_at = datetime.now(timezone.utc)
    b1 = _item(title="B1", score=8)
    b1.published_at = None
    f2 = _item(title="F2", score=7)
    f2.published_at = datetime.now(timezone.utc)
    ctx.items = [f1, b1, f2]  # rerank interleaves fresh/backfill
    S.SynthesizeStage().run(ctx)
    persisted = json.loads((tmp_path / f"{ctx.digest.date}.items.json").read_text(encoding="utf-8"))
    assert [p["title"] for p in persisted] == ["F1", "F2", "B1"]          # display order
    assert [it.title for it in ctx.digest.items] == ["F1", "F2", "B1"]
    assert "[1] [F1]" in ctx.digest.markdown_brief
    assert "[3] [B1]" in ctx.digest.markdown_brief


def test_mark_maps_and_snapshots(tmp_path, monkeypatch):
    import json
    from radar.core import config as Cfg
    from radar.core.io import atomic_write_json
    from radar.cli import cmd_mark
    monkeypatch.setattr(Cfg.Paths, "digests", tmp_path)
    monkeypatch.setattr(Cfg.Paths, "feedback", tmp_path)
    atomic_write_json(tmp_path / "2026-06-26.items.json", [
        {"id": "aaa", "title": "First", "source_name": "S1", "tags": ["x"], "url": "http://a"},
        {"id": "bbb", "title": "Second", "source_name": "S2", "tags": ["y"], "url": "http://b"},
    ])
    assert cmd_mark(["2026-06-26", "2", "--up"]) == 0
    fb = json.loads((tmp_path / "2026-06-26.json").read_text(encoding="utf-8"))
    assert "bbb" in fb and "aaa" not in fb                     # #2 → second item (id bbb)
    assert fb["bbb"]["vote"] == "up" and fb["bbb"]["title"] == "Second"
    assert fb["bbb"]["source"] == "S2" and fb["bbb"]["url"] == "http://b"   # content snapshot
    assert cmd_mark(["2026-06-26", "99"]) == 1                 # out of range, no crash
    assert cmd_mark(["2099-01-01", "1"]) == 1                  # missing date file, graceful


# ---- C: salvage / lock / health / last_run ----
def test_salvage_objects():
    from radar.llm._json import salvage_objects
    bad = '[{"i":0,"score":9}, GARBAGE!!, {"i":2,"score":3}]'
    got = salvage_objects(bad)
    assert [o["i"] for o in got] == [0, 2]


def test_run_lock(tmp_path):
    from radar.core.lock import RunLock
    p = tmp_path / "run.lock"
    a = RunLock(p)
    assert a.acquire() is True
    assert RunLock(p).acquire() is False     # held by a live process (us)
    a.release()
    c = RunLock(p)
    assert c.acquire() is True               # released → free
    c.release()


def test_run_lock_reclaims_stale(tmp_path):
    from radar.core.io import atomic_write_json
    from radar.core.lock import RunLock
    p = tmp_path / "run.lock"
    atomic_write_json(p, {"pid": 999999, "ts": "2000-01-01T00:00:00+00:00"})  # dead + old
    assert RunLock(p).acquire() is True       # stale → reclaimed


def test_health_line():
    from radar.stages.synthesize import _health_line
    ctx = _ctx()
    ctx.stats["fetch_health"] = {"live": 0, "total": 28, "failed": ["a", "b"]}
    assert "大面积失败" in _health_line(ctx)
    ctx.stats["fetch_health"] = {"live": 26, "total": 28, "failed": ["a", "b"]}
    assert "26/28" in _health_line(ctx)
    ctx.stats["fetch_health"] = {"live": 28, "total": 28, "failed": []}
    assert _health_line(ctx) == ""


def test_write_last_run(tmp_path, monkeypatch):
    import radar.core.runner as R
    monkeypatch.setattr(R.Paths, "state", tmp_path)
    ctx = _ctx()
    ctx.stats.update(candidates=10, fetch_health={"live": 5, "total": 6, "failed": ["x"]})
    R._write_last_run(ctx)
    import json
    d = json.loads((tmp_path / "last_run.json").read_text(encoding="utf-8"))
    assert d["candidates"] == 10 and d["sources"]["live"] == 5 and d["sources"]["total"] == 6


# ---- proxy resolution (D) ----
def test_proxy_settings():
    from radar.core.config import RadarConfig
    # explicit proxy wins and disables env
    proxies, trust = RadarConfig(http_proxy="http://p:1").proxy_settings()
    assert proxies == {"http": "http://p:1", "https": "http://p:1"} and trust is False
    # default: no explicit proxy → honor env proxies (trust_env True)
    proxies, trust = RadarConfig().proxy_settings()
    assert proxies is None and trust is True
    # env disabled → force direct
    proxies, trust = RadarConfig(use_env_proxy=False).proxy_settings()
    assert proxies is None and trust is False
