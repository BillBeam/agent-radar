"""时效性收口（2026-07-06）：B1 arXiv 分页防截尾 · B1b GitHub releases 深度 · B2 停机补课窗口。
全部无网络、无 LLM——网络行为用探针实测过（见 decisions.md），这里锁代码逻辑。"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

from radar.core.config import Paths, load_config
from radar.core.models import Item, RunContext, Source, SourceType, TimeWindow


def _ctx(window_h=48.0):
    from radar.obs import Logger, Tracer
    cfg = load_config()
    ctx = RunContext(run_id="test", mode="daily", config=cfg, window=TimeWindow(window_h))
    ctx.log = Logger("test", echo=False)
    ctx.trace = Tracer("test")
    return ctx


def _atom(entries: list[tuple[str, datetime]]) -> bytes:
    """Minimal Atom feed: [(title, published)]."""
    body = "".join(
        f"<entry><title>{t}</title><link href='http://arxiv.org/abs/{t}'/>"
        f"<published>{d.strftime('%Y-%m-%dT%H:%M:%SZ')}</published>"
        f"<summary>s</summary></entry>"
        for t, d in entries)
    return (b"<?xml version='1.0'?><feed xmlns='http://www.w3.org/2005/Atom'>"
            + body.encode() + b"</feed>")


# ---------- B1: arXiv window-aware pagination ----------

def _arxiv_source(max_results=9):
    return Source(id="arxiv-agents", name="arXiv", category="papers", type=SourceType.arxiv,
                  url="http://export.arxiv.org/api/query",
                  params={"max_results": max_results})


def _paged_arxiv(monkeypatch, pages: dict[int, list[tuple[str, datetime]]]):
    """ArxivSource whose get_bytes serves `pages[start]`, recording each start."""
    import radar.sources.arxiv as ax
    monkeypatch.setattr(ax, "PAGE_SIZE", 3)
    monkeypatch.setattr(ax, "PAGE_DELAY_S", 0.0)
    src = ax.ArxivSource()
    calls: list[int] = []

    def fake_get_bytes(url, **kw):
        start = int(parse_qs(urlparse(url).query)["start"][0])
        calls.append(start)
        return _atom(pages.get(start, []))

    monkeypatch.setattr(src, "get_bytes", fake_get_bytes)
    return src, calls


def test_arxiv_paginates_until_window_boundary(monkeypatch):
    """满页且仍在窗内 → 翻下一页；某页最老条目出窗 → 早停，不再多打一个请求。"""
    now = datetime.now(timezone.utc)
    fresh, stale = now - timedelta(hours=1), now - timedelta(hours=200)
    pages = {
        0: [("a1", fresh), ("a2", fresh), ("a3", fresh)],          # full page, all fresh
        3: [("a4", fresh), ("a5", fresh), ("a6", stale)],          # crosses the boundary
        6: [("a7", fresh)],                                        # must never be requested
    }
    src, calls = _paged_arxiv(monkeypatch, pages)
    items = src.fetch(_arxiv_source(max_results=9), TimeWindow(96))
    assert calls == [0, 3]                       # early stop — page 6 never fetched
    assert [it.title for it in items] == ["a1", "a2", "a3", "a4", "a5"]  # stale filtered
    assert all("paper" in it.tags for it in items)


def test_arxiv_short_page_stops(monkeypatch):
    now = datetime.now(timezone.utc)
    pages = {0: [("a1", now), ("a2", now)]}      # < PAGE_SIZE → feed exhausted
    src, calls = _paged_arxiv(monkeypatch, pages)
    items = src.fetch(_arxiv_source(max_results=9), TimeWindow(96))
    assert calls == [0] and len(items) == 2


def test_arxiv_ceiling_bounds_total(monkeypatch):
    """全部满页且全新鲜 → 翻到 max_results 硬顶为止（永不无限翻页）。"""
    now = datetime.now(timezone.utc)
    fresh_page = lambda s: [(f"p{s}-{i}", now) for i in range(3)]  # noqa: E731
    pages = {0: fresh_page(0), 3: fresh_page(3), 6: fresh_page(6), 9: fresh_page(9)}
    src, calls = _paged_arxiv(monkeypatch, pages)
    items = src.fetch(_arxiv_source(max_results=9), TimeWindow(96))
    assert calls == [0, 3, 6] and len(items) == 9


def test_arxiv_url_carries_start_and_per_page():
    from radar.sources.arxiv import ArxivSource
    u = ArxivSource()._url(SimpleNamespace(params={}), start=200, per_page=200)
    q = parse_qs(urlparse(u).query)
    assert q["start"] == ["200"] and q["max_results"] == ["200"]


def test_sources_yaml_arxiv_cap_not_truncating():
    """B1 验收：实测 07-03 的 96h 窗口匹配 >200 条 → cap 必须显著高于它。"""
    import yaml
    data = yaml.safe_load(Paths.sources_yaml.read_text(encoding="utf-8"))
    arx = next(s for s in data["sources"] if s["id"] == "arxiv-agents")
    assert int(arx["params"]["max_results"]) >= 400
    # A1 收紧零回退：keywords/categories 一个字不动
    assert "cs.LG" not in arx["params"]["categories"]


# ---------- B1b: GitHub releases — REST first, atom fallback ----------

def _gh_source():
    return Source(id="gh-x", name="X", category="harness", type=SourceType.github_releases,
                  url="https://github.com/o/r", params={"repo": "o/r"})


def _gh_adapter():
    from radar.sources.github_releases import GithubReleasesSource
    return GithubReleasesSource()


def test_gh_rest_api_first(monkeypatch):
    now = datetime.now(timezone.utc)
    api = [{"name": "v1.2.3", "tag_name": "v1.2.3",
            "html_url": "https://github.com/o/r/releases/tag/v1.2.3",
            "published_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "body": "## What's changed\nfix stuff"},
           {"name": "", "tag_name": "v1.2.2",   # falls back to tag_name
            "html_url": "https://github.com/o/r/releases/tag/v1.2.2",
            "published_at": (now - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "body": None}]
    ad = _gh_adapter()
    urls: list[str] = []
    monkeypatch.setattr(ad, "get_json", lambda url, **kw: (urls.append(url), api)[1])
    items = ad.fetch(_gh_source(), TimeWindow(48))
    assert "api.github.com/repos/o/r/releases" in urls[0] and "per_page=30" in urls[0]
    assert [it.title for it in items] == ["v1.2.3"]          # 30d-old one window-filtered
    assert "What's changed" in items[0].summary              # body kept, ws collapsed
    assert "release" in items[0].tags


def test_gh_falls_back_to_atom_on_api_failure(monkeypatch):
    now = datetime.now(timezone.utc)
    ad = _gh_adapter()

    def boom(url, **kw):
        raise RuntimeError("403 rate limited")

    monkeypatch.setattr(ad, "get_json", boom)
    monkeypatch.setattr(ad, "get_bytes", lambda url, **kw: _atom([("v9.9.9", now)]))
    items = ad.fetch(_gh_source(), TimeWindow(48))
    assert [it.title for it in items] == ["v9.9.9"]          # atom fallback kept the run alive


# ---------- B3b: html 光杆标题 → og:description 摘要增补 ----------

_INDEX_HTML = ('<a href="/news/introducing-foo">'
               '<h3>Introducing Foo agents for team workflows</h3></a>')
_PAGE_HTML = ('<html><head><meta property="og:description" '
              'content="Foo lets teams tag @Bot in channels to delegate tasks."/>'
              '</head><body>x</body></html>')


def _html_source():
    return Source(id="anthropic-news", name="AN", category="labs", type=SourceType.html,
                  url="https://x.example/news",
                  params={"url_contains": "/news/", "enrich_summary": True})


def _html_adapter(monkeypatch, tmp_path, page_html=_PAGE_HTML, page_raises=False):
    import radar.sources.html as H
    monkeypatch.setattr(H.Paths, "html_summary_cache", tmp_path / "html_summaries.json")
    ad = H.HtmlSource()
    calls: list[str] = []

    def fake_get_text(url, **kw):
        calls.append(url)
        if url.endswith("/news"):
            return _INDEX_HTML
        if page_raises:
            raise RuntimeError("boom")
        return page_html

    monkeypatch.setattr(ad, "get_text", fake_get_text)
    return ad, calls


def test_html_enrich_fills_summary_and_caches(monkeypatch, tmp_path):
    ad, calls = _html_adapter(monkeypatch, tmp_path)
    items = ad.fetch(_html_source(), TimeWindow(48))
    assert items[0].summary.startswith("Foo lets teams tag @Bot")
    # 第二跑走磁盘缓存：那篇文章页只被抓过一次
    items2 = ad.fetch(_html_source(), TimeWindow(48))
    assert items2[0].summary.startswith("Foo lets teams tag @Bot")
    assert sum("introducing-foo" in u for u in calls) == 1


def test_html_enrich_failure_leaves_bare_and_retries_next_run(monkeypatch, tmp_path):
    ad, calls = _html_adapter(monkeypatch, tmp_path, page_raises=True)
    items = ad.fetch(_html_source(), TimeWindow(48))
    assert items[0].summary == ""                       # 拉不到就保持光杆，绝不编
    ad.fetch(_html_source(), TimeWindow(48))            # 失败不落负缓存 → 下跑重试
    assert sum("introducing-foo" in u for u in calls) == 2


def test_html_enrich_opt_in_only(monkeypatch, tmp_path):
    ad, calls = _html_adapter(monkeypatch, tmp_path)
    src = _html_source()
    src.params.pop("enrich_summary")
    items = ad.fetch(src, TimeWindow(48))
    assert items[0].summary == ""
    assert sum("introducing-foo" in u for u in calls) == 0   # 未 opt-in 零文章页请求


def test_extract_description_both_attribute_orders():
    from radar.sources.html import extract_description
    a = '<meta property="og:description" content="AAA bbb"/>'
    b = '<meta content="CCC ddd" name="description"/>'
    assert extract_description(a) == "AAA bbb"
    assert extract_description(b) == "CCC ddd"
    assert extract_description("<html>no meta</html>") == ""


# ---------- B2: catch-up window ----------

def test_effective_window_no_state_uses_configured():
    from radar.stages.fetch import _effective_window
    ctx = _ctx()
    src = Source(id="s1", name="S", category="labs", type=SourceType.rss, url="http://x")
    w, configured = _effective_window(src, ctx, {})
    assert w.hours == 48.0 and configured == 48.0


def test_effective_window_normal_daily_cadence_does_not_widen():
    """正常连跑（gap ~24h + 余量12h < 48h 窗）→ 窗口不膨胀。"""
    from radar.stages.fetch import _effective_window
    ctx = _ctx()
    src = Source(id="s1", name="S", category="labs", type=SourceType.rss, url="http://x")
    stamp = (ctx.started_at - timedelta(hours=24)).isoformat(timespec="seconds")
    w, _ = _effective_window(src, ctx, {"s1": stamp})
    assert w.hours == 48.0


def test_effective_window_3day_outage_widens_and_caps():
    from radar.stages.fetch import _effective_window
    ctx = _ctx()
    src = Source(id="s1", name="S", category="labs", type=SourceType.rss, url="http://x")
    stamp3d = (ctx.started_at - timedelta(days=3)).isoformat(timespec="seconds")
    w, _ = _effective_window(src, ctx, {"s1": stamp3d})
    # approx: stamp 落盘只到秒，回读比 started_at 差 <1s
    assert w.hours == pytest.approx(72 + ctx.config.catchup_margin_hours, abs=0.01)
    stamp90d = (ctx.started_at - timedelta(days=90)).isoformat(timespec="seconds")
    w, _ = _effective_window(src, ctx, {"s1": stamp90d})
    assert w.hours == ctx.config.catchup_max_hours           # 14d ceiling


def test_effective_window_respects_wider_per_source_leash():
    """arXiv 96h 宽 leash：3 天停机(72+12=84h) 仍 < 96h → 用 96h，不缩不涨。"""
    from radar.stages.fetch import _effective_window
    ctx = _ctx()
    src = Source(id="arxiv-agents", name="A", category="papers", type=SourceType.arxiv,
                 url="http://x", params={"max_age_hours": 96})
    stamp = (ctx.started_at - timedelta(days=3)).isoformat(timespec="seconds")
    w, _ = _effective_window(src, ctx, {"arxiv-agents": stamp})
    assert w.hours == 96.0


def _wire_fetch(monkeypatch, tmp_path, adapter_cls, state: dict | None):
    """FetchStage.run() 全链路打桩：fake 源 + fake 适配器 + tmp state 文件。"""
    import radar.stages.fetch as F
    src = Source(id="s1", name="S", category="labs", type=SourceType.rss, url="http://x")
    monkeypatch.setattr(F, "load_sources", lambda: [src])
    monkeypatch.setattr(F.registry, "get", lambda kind, name: adapter_cls)
    monkeypatch.setattr(F, "SALVAGE_DELAY_S", 0.0)   # no settle wait in tests
    monkeypatch.setattr(F.Paths, "seen_json", tmp_path / "seen.json")
    monkeypatch.setattr(F.Paths, "first_seen_json", tmp_path / "first_seen.json")
    monkeypatch.setattr(F.Paths, "fetch_state_json", tmp_path / "fetch_state.json")
    monkeypatch.setattr(F.Paths, "candidates", tmp_path)
    if state is not None:
        (tmp_path / "fetch_state.json").write_text(json.dumps(state), encoding="utf-8")
    return F.FetchStage(), src


def test_fetch_stage_widens_after_outage_and_persists_state(monkeypatch, tmp_path):
    seen_windows: list[float] = []

    class FakeAdapter:
        def __init__(self, config=None, log=None): ...

        def fetch(self, source, window):
            seen_windows.append(window.hours)
            it = Item.create(source=source, title="t", url="http://x/t",
                             published_at=datetime.now(timezone.utc))
            return [it]

    ctx = _ctx()
    stamp = (ctx.started_at - timedelta(days=3)).isoformat(timespec="seconds")
    stage, src = _wire_fetch(monkeypatch, tmp_path, FakeAdapter,
                             {"last_success": {"s1": stamp}})
    stage.run(ctx)
    assert seen_windows[0] == pytest.approx(72 + ctx.config.catchup_margin_hours, abs=0.01)
    assert ctx.stats["catchup"]["s1"] == pytest.approx(84.0, abs=0.1)
    new_state = json.loads((tmp_path / "fetch_state.json").read_text())
    assert new_state["last_success"]["s1"] == ctx.started_at.isoformat(timespec="seconds")


def test_fetch_stage_failed_source_keeps_old_stamp(monkeypatch, tmp_path):
    """失败的源不更新 last_success → 下一跑自动为它放大窗口（今天 arxiv 超时的真实场景）。"""

    class DeadAdapter:
        def __init__(self, config=None, log=None): ...

        def fetch(self, source, window):
            raise RuntimeError("read timeout")

    ctx = _ctx()
    old = (ctx.started_at - timedelta(days=1)).isoformat(timespec="seconds")
    stage, src = _wire_fetch(monkeypatch, tmp_path, DeadAdapter,
                             {"last_success": {"s1": old}})
    stage.run(ctx)
    assert ctx.stats["per_source"]["s1"] == -1
    new_state = json.loads((tmp_path / "fetch_state.json").read_text())
    assert new_state["last_success"]["s1"] == old              # stamp preserved, not bumped


def test_fetch_stage_first_run_no_state_no_widening(monkeypatch, tmp_path):
    seen_windows: list[float] = []

    class FakeAdapter:
        def __init__(self, config=None, log=None): ...

        def fetch(self, source, window):
            seen_windows.append(window.hours)
            return []

    ctx = _ctx()
    stage, src = _wire_fetch(monkeypatch, tmp_path, FakeAdapter, None)
    stage.run(ctx)
    assert seen_windows == [48.0] and "catchup" not in ctx.stats
    assert json.loads((tmp_path / "fetch_state.json").read_text())["last_success"]["s1"]


# ---------- salvage re-pass (2026-07-07 postmortem: dark-wake killed 18/28 sources) ----------

def test_fetch_salvage_recovers_source_that_died_at_the_wrong_moment(monkeypatch, tmp_path):
    """第一轮失败（醒来瞬间代理没就绪）→ salvage 第二轮成功 → 当跑就有货，不用等明天补课。"""
    calls = {"n": 0}

    class FlakyAdapter:
        def __init__(self, config=None, log=None): ...

        def fetch(self, source, window):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("RemoteDisconnected — proxy tunnel not up yet")
            return [Item.create(source=source, title="t", url="http://x/t",
                                published_at=datetime.now(timezone.utc))]

    ctx = _ctx()
    stage, src = _wire_fetch(monkeypatch, tmp_path, FlakyAdapter, None)
    stage.run(ctx)
    assert calls["n"] == 2                                   # first pass + salvage
    assert ctx.stats["per_source"]["s1"] == 1                # recovered, item kept
    assert ctx.stats["salvaged_sources"] == ["s1"]
    assert ctx.stats["fetch_health"]["failed"] == []         # health reflects post-salvage truth
    assert len(ctx.candidates) == 1
    state = json.loads((tmp_path / "fetch_state.json").read_text())
    assert state["last_success"]["s1"]                       # success stamped by salvage pass


def test_fetch_salvage_still_dead_source_stays_failed(monkeypatch, tmp_path):
    """两轮都死 → 保持 -1、不写 last_success（B2 明天自动放大窗口），不虚报恢复。"""
    calls = {"n": 0}

    class DeadAdapter:
        def __init__(self, config=None, log=None): ...

        def fetch(self, source, window):
            calls["n"] += 1
            raise RuntimeError("still dead")

    ctx = _ctx()
    stage, src = _wire_fetch(monkeypatch, tmp_path, DeadAdapter, None)
    stage.run(ctx)
    assert calls["n"] == 2                                   # salvage did retry once
    assert ctx.stats["per_source"]["s1"] == -1
    assert "salvaged_sources" not in ctx.stats
    assert ctx.stats["fetch_health"]["failed"] == ["s1"]
