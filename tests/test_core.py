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
    # (href, title-ish text, full anchor text — kept for date parsing)
    assert p.links == [("/x", "Real Title", "Real Title some blurb that should not mash in")]
    p2 = _LinkExtractor()
    p2.feed('<a href="/y">Just Anchor Text</a>')
    assert p2.links == [("/y", "Just Anchor Text", "Just Anchor Text")]


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
    assert "🆕 今日新增" in md and "首次收录" in md and "今日新增 1" in md


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
    # 07-08: the failure line must also reassure — the gap backfills, nothing is lost.
    assert "补课" in _health_line(ctx)
    ctx.stats["fetch_health"] = {"live": 28, "total": 28, "failed": []}
    assert _health_line(ctx) == ""


def test_synthesize_thin_delivery_note(tmp_path, monkeypatch):
    """07-08「为什么只有9篇」: fewer items than the cap must self-explain in the header
    (宁缺毋滥 is by design, not a broken funnel) — and a full day must NOT show the note."""
    import radar.stages.synthesize as S
    monkeypatch.setattr(S.Paths, "digests", tmp_path)
    ctx = _ctx()
    ctx.llm = None
    ctx.sources = []
    ctx.items = [_item(title=f"t{i}", score=9) for i in range(2)]   # 2 < daily cap (10)
    S.SynthesizeStage().run(ctx)
    assert f"入选 2/{ctx.config.daily_max_items}" in ctx.digest.markdown
    assert "宁缺毋滥" in ctx.digest.markdown

    ctx2 = _ctx()
    ctx2.llm = None
    ctx2.sources = []
    ctx2.config.daily_max_items = 2                                  # full house → no note
    ctx2.items = [_item(title=f"t{i}", score=9) for i in range(2)]
    S.SynthesizeStage().run(ctx2)
    assert "宁缺毋滥" not in ctx2.digest.markdown


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


# ---- 7.3 复盘 fixes: display freshness / rerank degrade / html dates / llm failure trace ----
def test_synthesize_stale_dated_is_backfill(tmp_path, monkeypatch):
    """A months-old DATED post (html sources now parse card dates) must NOT be presented
    as 🆕今日新增 — it lands in 📚首次收录 under the honest new label."""
    import radar.stages.synthesize as S
    from datetime import datetime, timezone
    monkeypatch.setattr(S.Paths, "digests", tmp_path)
    ctx = _ctx()
    ctx.llm = None
    ctx.sources = []
    new = _item(title="new one", score=9)
    new.published_at = datetime.now(timezone.utc)
    old = _item(title="old blog post", score=8)
    old.published_at = datetime.now(timezone.utc) - timedelta(days=60)
    ctx.items = [old, new]                        # rerank happened to put the old post first
    S.SynthesizeStage().run(ctx)
    md = ctx.digest.markdown
    assert "首次收录（往期/无日期内容，非重复推送）" in md and "往期补课" not in md
    assert "[1] [new one]" in ctx.digest.markdown_brief     # fresh precedes stale in display
    assert "[2] [old blog post]" in ctx.digest.markdown_brief


def test_card_numbering_agrees_with_synthesize_on_stale_dated(tmp_path, monkeypatch):
    """The voting card derives [N]/🆕/📚 independently — it must use the SAME freshness
    predicate as synthesize, or votes/`mark N` map to the wrong item for stale-dated posts."""
    import radar.stages.synthesize as S
    from datetime import datetime, timezone
    from radar.channels.dingtalk_card import item_numbering
    monkeypatch.setattr(S.Paths, "digests", tmp_path)
    ctx = _ctx()
    ctx.llm = None
    ctx.sources = []
    old = _item(title="OLD", score=9)
    old.published_at = datetime.now(timezone.utc) - timedelta(days=60)
    new = _item(title="NEW", score=8)
    new.published_at = datetime.now(timezone.utc)
    ctx.items = [old, new]
    S.SynthesizeStage().run(ctx)
    numbering = item_numbering(ctx.digest.items)
    assert numbering[new.id] == (1, "🆕")
    assert numbering[old.id] == (2, "📚")


def test_synthesize_rerank_degraded_banner(tmp_path, monkeypatch):
    import radar.stages.synthesize as S
    from datetime import datetime, timezone
    monkeypatch.setattr(S.Paths, "digests", tmp_path)
    ctx = _ctx()
    ctx.llm = None
    ctx.sources = []
    it = _item(title="t", score=9)
    it.published_at = datetime.now(timezone.utc)
    ctx.items = [it]
    ctx.stats["rerank_degraded"] = True
    S.SynthesizeStage().run(ctx)
    assert "排序降级" in ctx.digest.markdown and "排序降级" in ctx.digest.markdown_brief


def test_rerank_llm_failure_sets_degraded_and_uses_timeout():
    from types import SimpleNamespace
    from radar.stages.rerank import RerankStage
    ctx = _ctx()
    ctx.items = [_item(score=9, title="a"), _item(score=7, title="b")]
    seen_kw = {}

    class _FailLLM:
        def complete_json(self, prompt, **kw):
            seen_kw.update(kw)
            return None, SimpleNamespace(ok=False, error="timeout", text="")

    ctx.llm = _FailLLM()
    RerankStage().run(ctx)
    assert seen_kw.get("timeout") == 480              # 7.3: the 240s default timed out 3×
    assert ctx.stats.get("rerank_degraded") is True   # surfaced in the digest header
    assert [it.title for it in ctx.items] == ["a", "b"]   # triage-score fallback still selects


def test_html_source_parses_card_date(monkeypatch):
    from datetime import datetime, timezone
    from radar.core.models import Source, SourceType
    from radar.sources.html import HtmlSource
    fixture = (
        '<article><a href="/engineering/featured-post"><h2>Featured Post About Agents</h2>'
        '<p>blurb text long enough to matter here</p></a></article>'
        '<article><a href="/engineering/dated-post"><h3>A Dated Engineering Post</h3>'
        '<div>Apr 23, 2026</div></a></article>'
    )
    monkeypatch.setattr(HtmlSource, "get_text", lambda self, url, **kw: fixture)
    src = Source(id="x", name="X", category="harness", type=SourceType.html,
                 url="https://ex.com/engineering", params={"url_contains": "/engineering/"})
    items = HtmlSource().fetch(src, TimeWindow(48))
    by_slug = {it.url.rsplit("/", 1)[-1]: it for it in items}
    dated = by_slug["dated-post"]
    assert dated.published_at == datetime(2026, 4, 23, tzinfo=timezone.utc)
    assert dated.title == "A Dated Engineering Post"        # the date never leaks into the title
    assert by_slug["featured-post"].published_at is None    # dateless card → undated as before


def test_fetch_backfill_cap_covers_stale_dated():
    from datetime import datetime, timezone
    from radar.stages.fetch import _needs_backfill_cap
    w = TimeWindow(48)
    undated = _item(title="u")
    stale = _item(title="s")
    stale.published_at = datetime.now(timezone.utc) - timedelta(days=30)
    fresh = _item(title="f")
    fresh.published_at = datetime.now(timezone.utc)
    assert _needs_backfill_cap(undated, w) and _needs_backfill_cap(stale, w)
    assert not _needs_backfill_cap(fresh, w)


def test_llm_failed_attempts_recorded(monkeypatch):
    import radar.llm.claude_code as CC
    events = []

    class _Tr:
        def event(self, kind, **f):
            events.append((kind, f))

    llm = CC.ClaudeCodeLLM(config=None, log=None, trace=_Tr())
    monkeypatch.setattr(llm, "_run", lambda *a, **k: (False, "timeout", None))
    monkeypatch.setattr(CC.time, "sleep", lambda s: None)
    res = llm.complete("x", tag="rerank", retries=3)
    assert not res.ok
    assert llm.by_stage["rerank"]["failed"] == 3 and llm.by_stage["rerank"]["calls"] == 0
    assert len(events) == 3 and all(f.get("error") == "timeout" for _, f in events)


def test_llm_subprocess_uses_neutral_cwd(monkeypatch):
    """claude -p loads CLAUDE.md from cwd + every ancestor into model context — pipeline
    calls must run from a neutral dir so the identity-laden (gitignored) project manual
    never reaches model context (it bled into outputs bound for the public reading page)."""
    from pathlib import Path

    import radar.llm.claude_code as CC
    from radar.core.config import Paths
    seen = {}

    def fake_run(cmd, **kw):
        seen.update(kw)

        class P:
            returncode = 0
            stdout = '{"result": "ok"}'
            stderr = ""
        return P()

    monkeypatch.setattr(CC.subprocess, "run", fake_run)
    llm = CC.ClaudeCodeLLM(config=None, log=None)
    assert llm.complete("x", tag="t").ok
    cwd = Path(seen["cwd"]).resolve()
    assert Paths.root.resolve() not in cwd.parents                    # never under the repo
    assert not (cwd / "CLAUDE.md").exists()
    assert not any((p / "CLAUDE.md").exists() for p in cwd.parents)   # no ancestor CLAUDE.md


def test_llm_subprocess_disables_all_tools(monkeypatch):
    """Pipeline calls are text-in-text-out. With tools available the model sporadically
    OPENS with a tool call (live 2026-07-04: rerank answered with ReportFindings), which
    burns the single --max-turns turn → CLI exit 1, empty stderr → silent stage degrade.
    `--tools \"\"` removes the whole failure class (and the tool-driven contamination
    channel: a pipeline call must never Read files or browse)."""
    import radar.llm.claude_code as CC
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"] = cmd

        class P:
            returncode = 0
            stdout = '{"result": "ok"}'
            stderr = ""
        return P()

    monkeypatch.setattr(CC.subprocess, "run", fake_run)
    llm = CC.ClaudeCodeLLM(config=None, log=None)
    assert llm.complete("x", tag="t").ok
    cmd = seen["cmd"]
    i = cmd.index("--tools")
    assert cmd[i + 1] == ""                       # all tools disabled, text-only completion
    assert "--max-turns" in cmd and cmd[cmd.index("--max-turns") + 1] == "1"


def test_llm_stream_watchdog_aligned_with_call_timeout(monkeypatch):
    """CLI ≥2.1.204's 300s stream-idle watchdog fires BEFORE first byte on heavy prompts
    (opus TTFT >5min on 80K grounding) — 'Connection closed mid-response' killed all 9
    fresh deepreads on 07-08. The wrapper must own the deadline: watchdog = call timeout."""
    import radar.llm.claude_code as CC
    seen = {}

    def fake_run(cmd, **kw):
        seen.update(kw)

        class P:
            returncode = 0
            stdout = '{"result": "ok"}'
            stderr = ""
        return P()

    monkeypatch.setattr(CC.subprocess, "run", fake_run)
    llm = CC.ClaudeCodeLLM(config=None, log=None)
    assert llm.complete("x", tag="t", timeout=1200).ok
    # real knob names verified via `strings` on the 2.1.204 binary (docs circulate a
    # wrong name without BYTE_ — that one is ignored and the 300s default still fires)
    assert seen["env"]["CLAUDE_BYTE_STREAM_IDLE_TIMEOUT_MS"] == "1200000"
    assert seen["env"]["API_TIMEOUT_MS"] == "1200000"
    assert seen["env"]["DISABLE_AUTOUPDATER"] == "1"


def test_llm_bin_pinnable_via_env(monkeypatch):
    """AGENT_RADAR_CLAUDE_BIN pins the pipeline CLI to a known-good build — brew bumped
    the CLI mid-day on 07-08 and the new build's request shape killed every big-payload
    opus call; production must not ride whatever binary PATH happens to serve today."""
    import radar.llm.claude_code as CC
    monkeypatch.setenv("AGENT_RADAR_CLAUDE_BIN", "/pinned/claude-2.1.201")
    llm = CC.ClaudeCodeLLM(config=None, log=None)
    assert llm.bin == "/pinned/claude-2.1.201"
    monkeypatch.delenv("AGENT_RADAR_CLAUDE_BIN")
    llm2 = CC.ClaudeCodeLLM(config=None, log=None)
    assert llm2.bin.endswith("claude")            # unset → normal PATH resolution


def test_llm_failure_diag_from_stdout_and_transient_class(monkeypatch):
    """rc≠0 with EMPTY stderr must surface the CLI's stdout diagnostic (json envelope) —
    dropping it left 07-08 as an undiagnosable `exit 1: ` for 90 minutes. And genuine
    mid-stream FINs ('Connection closed mid-response') must classify as transient/retry."""
    import radar.llm.claude_code as CC
    calls = {"n": 0}

    def fake_run(cmd, **kw):
        calls["n"] += 1

        class P:
            returncode = 1
            stdout = '{"is_error":true,"result":"API Error: Connection closed mid-response."}'
            stderr = ""
        return P()

    monkeypatch.setattr(CC.subprocess, "run", fake_run)
    monkeypatch.setattr(CC.time, "sleep", lambda s: None)
    llm = CC.ClaudeCodeLLM(config=None, log=None)
    res = llm.complete("x", tag="t", retries=2)
    assert not res.ok
    assert "Connection closed mid-response" in (res.error or "")   # stdout diag surfaced
    assert calls["n"] == 2                                          # transient → retried


# ---- chunked triage (07-07: one 219-item haiku call timed out ×3 → whole-pool degrade) ----
def test_triage_chunks_with_global_indices(monkeypatch):
    import radar.stages.triage as T
    from radar.stages.triage import TriageStage
    monkeypatch.setattr(T, "CHUNK_SIZE", 2)
    ctx = _ctx()
    ctx.candidates = [_item(title=f"t{i}") for i in range(5)]
    calls = []

    class LLM:
        def complete_json(self, prompt, **kw):
            import re as _re
            idx = [int(m) for m in _re.findall(r"^\[(\d+)\]", prompt, _re.M)]
            calls.append(idx)
            seen_kw.update(kw)
            from types import SimpleNamespace
            return ([{"i": i, "score": 9 - i, "reason": f"r{i}", "tags": []} for i in idx],
                    SimpleNamespace(text="[]", error=None))

    seen_kw = {}
    ctx.llm = LLM()
    TriageStage().run(ctx)
    assert calls == [[0, 1], [2, 3], [4]]                    # 3 calls, GLOBAL indices
    assert seen_kw.get("timeout") == 480   # 07-08: 块常态 156–217s，240 默认穿顶（同 rerank 7.3 先例）
    assert [it.score for it in ctx.items] == [9, 8, 7, 6, 5]  # every item scored by its chunk
    assert ctx.stats["triage_coverage"] == 1.0
    assert "triage_degraded" not in ctx.stats


def test_triage_single_failed_chunk_degrades_only_itself(monkeypatch):
    import radar.stages.triage as T
    from radar.stages.triage import TriageStage
    monkeypatch.setattr(T, "CHUNK_SIZE", 2)
    ctx = _ctx()
    ctx.candidates = [_item(title=f"t{i}", weight=1.0) for i in range(4)]
    n = {"call": 0}

    class LLM:
        def complete_json(self, prompt, **kw):
            import re as _re
            from types import SimpleNamespace
            n["call"] += 1
            if n["call"] == 2:                               # second chunk times out
                return None, SimpleNamespace(text="", error="timeout")
            idx = [int(m) for m in _re.findall(r"^\[(\d+)\]", prompt, _re.M)]
            return ([{"i": i, "score": 8, "reason": "ok", "tags": []} for i in idx],
                    SimpleNamespace(text="[]", error=None))

    ctx.llm = LLM()
    TriageStage().run(ctx)
    assert [it.score for it in ctx.items[:2]] == [8, 8]       # chunk 1 scored by LLM
    assert all("启发式兜底" in (it.reason or "") for it in ctx.items[2:])   # chunk 2 heuristic
    assert ctx.stats["triage_chunks_failed"] == 1
    assert "triage_degraded" not in ctx.stats                 # NOT a whole-pool degrade
    assert ctx.stats["triage_coverage"] == 0.5


def test_triage_all_chunks_failed_falls_back_whole_pool(monkeypatch):
    import radar.stages.triage as T
    from radar.stages.triage import TriageStage
    monkeypatch.setattr(T, "CHUNK_SIZE", 2)
    ctx = _ctx()
    ctx.candidates = [_item(title=f"t{i}") for i in range(3)]

    class LLM:
        def complete_json(self, prompt, **kw):
            from types import SimpleNamespace
            return None, SimpleNamespace(text="", error="timeout")

    ctx.llm = LLM()
    TriageStage().run(ctx)
    assert ctx.stats["triage_degraded"] is True               # honest whole-run flag preserved
    assert all(it.score is not None for it in ctx.items)
