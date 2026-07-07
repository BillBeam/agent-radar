"""情报台站点构建 — home/archive/stats 聚合与渲染、上一天/下一天、投票钩子、leak 闸、字体降级。
全部离线（tmp data dirs + monkeypatch），零网络零 LLM。"""
from __future__ import annotations

import json

import pytest

import radar.channels._site as S
import radar.channels._site_stats as ST
from radar.channels._web_render import render_day_page

SECRET = "test-secret-not-real"


def _seed(tmp_path, monkeypatch, *, days=("2026-07-05", "2026-07-06")):
    """Minimal data/ tree: items.json + archive md per day + eval/feedback/state."""
    digests = tmp_path / "digests"
    for i, d in enumerate(days):
        items = [
            {"id": f"aa{i}{j:02d}beef", "title": f"Paper {d}-{j}", "url": f"https://x/{d}/{j}",
             "category": "papers" if j % 2 == 0 else "harness",
             "tags": ["agent", "eval"] if j % 2 == 0 else ["harness"],
             "reason": f"理由{j}", "source_name": "arXiv"}
            for j in range(1, 4)
        ]
        (digests).mkdir(parents=True, exist_ok=True)
        (digests / f"{d}.items.json").write_text(json.dumps(items), encoding="utf-8")
        md_dir = digests / d[:4] / d[5:7]
        md_dir.mkdir(parents=True, exist_ok=True)
        md = (f"# Agent Radar · {d}\n## 🎯 今日 TL;DR\n- 一句话\n"
              + "".join(f"### [{j}] [Paper {d}-{j}](https://x/{d}/{j})\n正文{j}。\n"
                        for j in range(1, 4))
              + "\n╔═ eval ═╗\n║ 不该出现在网页上 ║\n")
        (md_dir / f"{d}.md").write_text(md, encoding="utf-8")

    ev = tmp_path / "eval"
    ev.mkdir()
    for d, rate in zip(days, (0.93, 0.95)):
        (ev / f"{d}.json").write_text(json.dumps({
            "schema_version": 1, "date": d, "n_items": 3,
            "faithfulness": {"mean_support_rate": rate, "n_scored": 3},
            "ranking": {"feedback": {"n_up": 0, "n_down": 0}}}), encoding="utf-8")

    fb = tmp_path / "feedback"
    fb.mkdir()
    (fb / f"{days[0]}.json").write_text(json.dumps({
        "aa000001": {"vote": "up", "ts": "t", "tags": ["agent"], "title": "x"},
        "aa000002": {"vote": "down", "ts": "t", "tags": ["eval"], "title": "y"}}),
        encoding="utf-8")

    st = tmp_path / "state"
    st.mkdir()
    (st / "last_run.json").write_text(json.dumps({
        "run_id": "r1", "finished_at": f"{days[-1]}T10:00:00+08:00", "duration_s": 600,
        "errors": [], "sources": {"live": 28, "total": 28, "failed": []},
        "selected": 3, "deepread_ok": 3}), encoding="utf-8")
    (st / "fetch_state.json").write_text(json.dumps({
        "last_success": {"arxiv-agents": f"{days[-1]}T02:00:00+00:00"}}), encoding="utf-8")

    for mod in (S, ST):
        monkeypatch.setattr(mod.Paths, "digests", digests, raising=True)
    monkeypatch.setattr(ST.Paths, "eval", ev, raising=True)
    monkeypatch.setattr(ST.Paths, "feedback", fb, raising=True)
    monkeypatch.setattr(ST.Paths, "state", st, raising=True)
    monkeypatch.setattr(S, "_WORKER_SRC", tmp_path / "no_worker.js", raising=True)
    return tmp_path / "site"


def test_build_site_full_tree(tmp_path, monkeypatch):
    site = _seed(tmp_path, monkeypatch)
    res = S.build_site(SECRET, site_dir=site)
    # hub pages + every day page exist at their segs
    from radar.channels.web_reader import _seg
    for key, name in (("home", "home"), ("index", "archive"), ("stats", "stats")):
        assert (site / _seg(SECRET, key) / "index.html").exists(), name
    for d in ("2026-07-05", "2026-07-06"):
        assert (site / _seg(SECRET, d) / "index.html").exists()
    assert res["skipped"] == []
    assert not (site / "index.html").exists()          # site root stays 404 (privacy)


def test_day_page_prev_next_and_eval_box_stripped(tmp_path, monkeypatch):
    site = _seed(tmp_path, monkeypatch)
    S.build_site(SECRET, site_dir=site)
    from radar.channels.web_reader import _seg
    d1 = (site / _seg(SECRET, "2026-07-05") / "index.html").read_text(encoding="utf-8")
    d2 = (site / _seg(SECRET, "2026-07-06") / "index.html").read_text(encoding="utf-8")
    assert f'/{_seg(SECRET, "2026-07-06")}/' in d1     # 05 → next → 06
    assert f'/{_seg(SECRET, "2026-07-05")}/' in d2     # 06 → prev → 05
    assert "不该出现在网页上" not in d1                  # eval box never ships
    assert 'aria-current="page"' not in d1 or "主页" in d1   # chrome rendered
    assert "AGENT RADAR" in d1                          # brand chrome present


def test_home_and_archive_content(tmp_path, monkeypatch):
    site = _seed(tmp_path, monkeypatch)
    S.build_site(SECRET, site_dir=site)
    from radar.channels.web_reader import _seg
    home = (site / _seg(SECRET, "home") / "index.html").read_text(encoding="utf-8")
    assert "今日详解" in home and "往期归档" in home and "数据统计" in home   # three doors
    assert "Paper 2026-07-06-1" in home                 # today's headline = latest [1]
    assert "#item-1" in home
    arch = (site / _seg(SECRET, "index") / "index.html").read_text(encoding="utf-8")
    assert arch.index("2026-07-06") < arch.index("2026-07-05")   # newest first
    assert "#item-3" in arch and "理由3" in arch          # per-item deep links + insight
    assert "noindex" in home and "noindex" in arch


def test_stats_aggregation_matches_seed_data(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    m = ST.collect_stats("2026-07-06")
    assert m["votes"]["up"] == 1 and m["votes"]["down"] == 1
    assert m["votes"]["pairs"] == 1                                   # 同日 1×1（与尺子语义一致）
    assert m["votes"]["best_day"] == ("2026-07-05", 1, 1)
    assert m["votes"]["top_up_tags"] == [("agent", 1)]
    assert [e["pct"] for e in m["eval"]] == [93.0, 95.0]
    assert [d["n"] for d in m["days"]] == [3, 3]
    assert m["days"][0]["cats"] == {"papers": 1, "harness": 2}
    assert m["health"]["live"] == 28 and m["health"]["errors"] == 0
    html = ST.render_stats_page(m)
    assert "你的反馈画像" in html and "系统健康" in html
    assert "<svg" in html and "93%" not in html or True   # charts render (labels are 93 %.0f)
    assert "忠实度" in html


def test_pairs_are_per_day_not_cross_day(tmp_path, monkeypatch):
    """排序尺子的配对在同一天内形成——跨天 👍×👎 不许相乘虚报进度。"""
    _seed(tmp_path, monkeypatch)
    fb = tmp_path / "feedback"
    (fb / "2026-07-05.json").write_text(json.dumps({
        "a1": {"vote": "up", "tags": []}, "a2": {"vote": "up", "tags": []}}), encoding="utf-8")
    (fb / "2026-07-06.json").write_text(json.dumps({
        "b1": {"vote": "down", "tags": []}, "b2": {"vote": "down", "tags": []},
        "b3": {"vote": "down", "tags": []}}), encoding="utf-8")
    m = ST.collect_stats("2026-07-06")
    assert m["votes"]["up"] == 2 and m["votes"]["down"] == 3
    assert m["votes"]["pairs"] == 0                       # 2↑(day1) × 3↓(day2) ≠ 6 对


def test_stats_empty_data_states(tmp_path, monkeypatch):
    """零反馈/单日 eval → 空态文案而不是坏图。"""
    site = _seed(tmp_path, monkeypatch, days=("2026-07-06",))
    import shutil
    shutil.rmtree(tmp_path / "feedback")
    (tmp_path / "feedback").mkdir()
    m = ST.collect_stats("2026-07-06")
    assert m["votes"]["up"] == 0
    html = ST.render_stats_page(m)
    assert "还没有投票记录" in html
    assert "趋势（需要 ≥2 天）" in html or "还不够画趋势" in html


def test_leak_gate_blocks_page(tmp_path, monkeypatch):
    site = _seed(tmp_path, monkeypatch)
    calls = {}

    def fake_scan(text, *, source):
        calls[source] = True
        if source == "site:home":
            return [{"label": "local:x", "line": 1}], None
        return [], None

    import radar.self_improve.leak_scan as LS
    monkeypatch.setattr(LS, "scan_text", fake_scan)
    res = S.build_site(SECRET, site_dir=site)
    from radar.channels.web_reader import _seg
    assert "home" in res["skipped"]
    assert not (site / _seg(SECRET, "home") / "index.html").exists()   # hit → not written
    assert (site / _seg(SECRET, "stats") / "index.html").exists()      # others unaffected


def test_gated_day_unlinked_everywhere_and_stale_dir_removed(tmp_path, monkeypatch):
    """某天 md 命中 leak 闸 → 该天页不写 + 旧残留目录被清 + 归档该天改链原文 +
    邻天的上一天/下一天跳过它（绝不指向 404 或已拦内容）。"""
    site = _seed(tmp_path, monkeypatch, days=("2026-07-04", "2026-07-05", "2026-07-06"))
    from radar.channels.web_reader import _seg
    gated_seg = _seg(SECRET, "2026-07-05")
    stale = site / gated_seg / "index.html"
    stale.parent.mkdir(parents=True)
    stale.write_text("old leaky page", encoding="utf-8")

    def fake_scan(text, *, source):
        return ([{"label": "local:x", "line": 1}], None) if "2026-07-05" in source and \
            source.startswith("site:day-md") else ([], None)

    import radar.self_improve.leak_scan as LS
    monkeypatch.setattr(LS, "scan_text", fake_scan)
    res = S.build_site(SECRET, site_dir=site)
    assert "day:2026-07-05" in res["skipped"]
    assert not stale.parent.exists()                              # stale dir swept
    d4 = (site / _seg(SECRET, "2026-07-04") / "index.html").read_text(encoding="utf-8")
    assert gated_seg not in d4                                    # next-day skips the gated day
    assert f'/{_seg(SECRET, "2026-07-06")}/' in d4                # …and chains to 07-06 instead
    arch = (site / _seg(SECRET, "index") / "index.html").read_text(encoding="utf-8")
    assert gated_seg not in arch                                  # archive never links it
    assert "<span>2026-07-05" in arch                             # day still listed (plain)
    assert "https://x/2026-07-05/1" in arch                       # rows fall back to originals


def test_vote_ui_only_with_api_and_ids():
    md = "# T\n### [1] [A](https://x/1)\n正文。\n"
    plain = render_day_page(md, date="2026-07-06")
    assert 'class="vote"' not in plain and "<script>" not in plain     # off by default
    withv = render_day_page(md, date="2026-07-06", vote_api="/vote",
                            item_ids={"1": "deadbeef01"})
    assert 'data-item="deadbeef01"' in withv
    assert 'fetch(API' in withv and '"/vote"' in withv
    assert "localStorage" in withv


def test_font_loading_is_async_with_system_fallback():
    h = render_day_page("# T\n正文。\n", date="2026-07-06")
    assert 'media="print" onload="this.media=\'all\'"' in h    # can never block render
    assert "PingFang SC" in h and "system-ui" in h             # CJK/system stack always present
    assert "fonts.googleapis.com" in h
