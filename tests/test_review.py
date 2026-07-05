"""E1 review-mode unit tests — no network, no real LLM, no real DingTalk.
Covers: read-only gather with per-source degradation (incl. run-health from digest archives),
the HUMAN four-part summary（运行/质量/投票/拍板——零内部术语、每个数字带解释、绝不出现本地路径）,
humanized report rendering, the never-auto-apply marker, dry-run writing ONLY the review file,
push composition with the reading-page link (+ leak/deploy degradations), DingTalk push (fake
session) and the leak scanner."""
from __future__ import annotations

import json

from radar.core.config import Paths, RadarConfig
from radar.self_improve import review
from radar.self_improve.leak_scan import scan_text

# 用户拍板的禁用词清单：写给用户的输出（推送 + 周报模板文字）里绝不许出现的内部术语——防回归。
FORBIDDEN = ("MIN_PAIRS", "sidecar", "grounding", "D 阶", "可比天数", "support_rate")


def _assert_human(text: str) -> None:
    for w in FORBIDDEN:
        assert w not in text, f"内部术语泄进用户输出：{w}"


# ---- fixtures ----
def _wire_paths(tmp_path, monkeypatch):
    """Point every data source review reads at empty tmp dirs; return their roots."""
    for name in ("feedback", "digests", "critic", "eval"):
        d = tmp_path / name
        d.mkdir()
        monkeypatch.setattr(Paths, name, d)
    monkeypatch.setattr(review, "REVIEWS_DIR", tmp_path / "reviews")
    monkeypatch.setattr(review, "WATCHLIST_FILE", tmp_path / "WATCHLIST.md")
    return tmp_path


def _seed(tmp_path):
    """Realistic minimal fixtures: 2 feedback days, 1 digest day, 1 critic day."""
    (tmp_path / "feedback" / "2026-06-26.json").write_text(json.dumps({
        "a1": {"vote": "up"}, "a2": {"vote": "down"}, "a3": {"vote": "down"}}), encoding="utf-8")
    (tmp_path / "feedback" / "2026-06-30.json").write_text(json.dumps({
        "b1": {"vote": "down"}}), encoding="utf-8")
    (tmp_path / "digests" / "2026-07-05.items.json").write_text(json.dumps([
        {"id": "x1", "title": "T1", "source_name": "arXiv", "self_applicable": True,
         "target_component": "deepread"},
        {"id": "x2", "title": "T2", "source_name": "arXiv"},
        {"id": "x3", "title": "T3", "source_name": "HN"},
    ]), encoding="utf-8")
    (tmp_path / "critic" / "2026-07-05.json").write_text(json.dumps({
        "date": "2026-07-05", "items": [
            {"id": "x1", "title": "T1", "skip": False, "conf": "low", "why": ""},
            {"id": "x3", "title": "T3", "skip": True, "conf": "high", "why": "重复"}]}),
        encoding="utf-8")
    (tmp_path / "WATCHLIST.md").write_text("# WATCHLIST\n\n## 1. 源分布\n- 盯着\n", encoding="utf-8")


def _seed_archive(tmp_path, date, degraded=False):
    """A daily digest md archive (data/digests/YYYY/MM/date.md) — run-health raw material."""
    d = tmp_path / "digests" / date[:4] / date[5:7]
    d.mkdir(parents=True, exist_ok=True)
    body = "# 日报\n" + ("> ⚠️ 本日排序降级：rerank 未成功\n" if degraded else "")
    (d / f"{date}.md").write_text(body, encoding="utf-8")


def _g_with_trend(faith=1.0):
    """Hand-built gather dict with an eval trend — exercises the humanized quality lines."""
    return {
        "generated_at": "t", "min_pairs": 10,
        "eval_trend": [
            {"date": "2026-07-05", "faith": faith, "n_scored": 6, "n_total": 10,
             "grounding": "sidecar×6", "g_kinds": ["sidecar"], "fb_signal": False,
             "fb_acc": None, "fb_pairs": 0, "tau": -0.467, "judge_n": 10},
            {"date": "2026-06-26", "faith": 0.9, "n_scored": 6, "n_total": 10,
             "grounding": "full_text×6", "g_kinds": ["full_text"], "fb_signal": False,
             "fb_acc": None, "fb_pairs": 2, "tau": 0.2, "judge_n": 10},
        ],
        "votes": [{"date": "2026-06-26", "up": 2, "down": 4, "pairs": 8}],
        "digest_days": [{"date": "2026-07-05", "n": 10, "sources": {"arXiv": 5, "HN": 5}}],
        "self_applicable": [], "critic": [], "watchlist": None,
        "run_health": [{"date": "2026-07-03", "degraded": False},
                       {"date": "2026-07-05", "degraded": False}],
    }


# ---- gather ----
def test_gather_all_empty_degrades_honestly(tmp_path, monkeypatch):
    _wire_paths(tmp_path, monkeypatch)
    g = review.gather(now="t")
    assert g["eval_trend"] == [] and g["votes"] == [] and g["digest_days"] == []
    assert g["self_applicable"] == [] and g["critic"] == [] and g["watchlist"] is None
    assert g["run_health"] == []
    md = review.render_markdown(g, None, "无 LLM 后端",
                                review.build_summary(g, None, "无 LLM 后端"), "2026-07-05")
    assert "暂无质量抽查数据" in md and "还没有投票记录" in md and "暂无日报数据" in md
    assert "没有与雷达自身相关" in md and "暂无质检数据" in md
    _assert_human(md)


def test_gather_counts(tmp_path, monkeypatch):
    _wire_paths(tmp_path, monkeypatch)
    _seed(tmp_path)
    g = review.gather(now="t")
    assert g["votes"] == [
        {"date": "2026-06-26", "up": 1, "down": 2, "pairs": 2},
        {"date": "2026-06-30", "up": 0, "down": 1, "pairs": 0}]
    assert g["digest_days"][0]["sources"] == {"arXiv": 2, "HN": 1}
    assert g["self_applicable"] == [{"date": "2026-07-05", "id": "x1", "title": "T1",
                                     "target_component": "deepread"}]
    assert g["critic"][0]["n_skip"] == 1 and g["critic"][0]["skips"][0]["why"] == "重复"
    assert "WATCHLIST" in g["watchlist"]


def test_gather_run_health_from_archives(tmp_path, monkeypatch):
    _wire_paths(tmp_path, monkeypatch)
    _seed_archive(tmp_path, "2026-07-03", degraded=True)
    _seed_archive(tmp_path, "2026-07-05", degraded=False)
    g = review.gather(now="t")
    assert g["run_health"] == [{"date": "2026-07-03", "degraded": True},
                               {"date": "2026-07-05", "degraded": False}]


def test_gather_bad_json_degrades(tmp_path, monkeypatch):
    _wire_paths(tmp_path, monkeypatch)
    (tmp_path / "feedback" / "2026-07-01.json").write_text("{broken", encoding="utf-8")
    (tmp_path / "digests" / "2026-07-01.items.json").write_text("[broken", encoding="utf-8")
    g = review.gather(now="t")           # must not raise
    assert g["votes"] == [] and g["digest_days"] == []


# ---- summary（四段人话） ----
def test_summary_four_parts_human_and_no_paths(tmp_path, monkeypatch):
    _wire_paths(tmp_path, monkeypatch)
    _seed(tmp_path)
    _seed_archive(tmp_path, "2026-07-05")
    g = review.gather(now="t")
    draft = "**草案建议**\n以下均为草案，等待拍板\n1. 改 A\n2. 改 B\n"
    s = review.build_summary(g, draft, None)
    for icon in ("🩺", "🔍", "🗳", "📝"):
        assert icon in s                       # 四段式
    assert "👍1/👎3" in s                       # 累计票数
    assert "凑满 10 次" in s and "还差 8 次" in s  # 差距用人话（best=2 对比，凑满 10）
    assert "对比" in s                          # 「对」的含义有解释
    assert "2 条改进草案" in s
    _assert_human(s)
    assert "data/" not in s                     # 绝不出现本地路径


def test_summary_quality_perfect_and_imperfect():
    s = review.build_summary(_g_with_trend(1.0), None, "无 LLM 后端")
    assert "全部 6 篇" in s and "零幻觉" in s and "另外 4 条" in s   # 6/10 的 10 有交代
    _assert_human(s)
    s2 = review.build_summary(_g_with_trend(0.93), None, "无 LLM 后端")
    assert "93%" in s2 and "零幻觉" not in s2 and "点到具体位置" in s2
    _assert_human(s2)


def test_summary_votes_signal_reached():
    g = _g_with_trend()
    g["votes"] = [{"date": "2026-07-04", "up": 3, "down": 4, "pairs": 12}]
    s = review.build_summary(g, None, None)
    assert "已经够排序开始学你的口味" in s and "还差" not in s
    _assert_human(s)


def test_summary_health_degraded_named():
    g = _g_with_trend()
    g["run_health"] = [{"date": "2026-07-03", "degraded": True},
                       {"date": "2026-07-05", "degraded": False}]
    s = review.build_summary(g, None, None)
    assert "2026-07-03" in s and "退回了粗排" in s
    _assert_human(s)


def test_count_suggestions():
    assert review._count_suggestions("1. a\n2. b\n正文 3.5 不算\n 3、c\n") == 3
    assert review._count_suggestions(None) == 0
    # only the 草案建议 section counts — 观察/WATCHLIST numbering must not inflate it
    draft = ("**观察**\n1. x\n2. y\n\n**草案建议**（等待拍板）\n1. **改什么**：a\n"
             "\n**WATCHLIST 盘点**\n1. p\n2. q\n")
    assert review._count_suggestions(draft) == 1


# ---- render ----
def test_render_markdown_humanized_trend():
    g = _g_with_trend(0.93)
    s = review.build_summary(g, None, "x")
    md = review.render_markdown(g, None, "x", s, "2026-07-05")
    assert "深读原文×6" in md and "重取原文×6" in md     # 核对依据口径人话化
    assert "93%（6/10 篇）" in md
    assert "怎么读" in md and "仅诊断" in md              # τ 有人话解释且标注非考核
    assert "不会被自动应用" in md and "拍板" in md         # never-auto-apply marker
    _assert_human(md)


# ---- run_review (dry) ----
def test_run_review_dry_writes_only_review_file(tmp_path, monkeypatch):
    _wire_paths(tmp_path, monkeypatch)
    _seed(tmp_path)
    published = []
    monkeypatch.setattr(review, "publish_review",
                        lambda *a, **k: published.append(1) or (None, "x", "y"))
    rc = review.run_review(llm=None, dry_run=True)
    assert rc == 0
    files = list((tmp_path / "reviews").glob("*-review.md"))
    assert len(files) == 1
    body = files[0].read_text(encoding="utf-8")
    assert "dry-run" in body and "不会被自动应用" in body
    assert not published                       # dry-run 不发布、不联网
    # nothing else appeared in the data dirs
    assert not list((tmp_path / "eval").iterdir())
    assert len(list((tmp_path / "feedback").iterdir())) == 2   # untouched fixtures


# ---- run_review push composition（链接 / 各降级路径） ----
class _PushRec:
    def __init__(self):
        self.texts = []

    def __call__(self, text, **kw):
        self.texts.append(text)
        return True, "sent"


def _no_leak(text, **kw):
    return [], None


def _wire_push(tmp_path, monkeypatch, publish_result):
    _wire_paths(tmp_path, monkeypatch)
    _seed(tmp_path)
    monkeypatch.setattr(review, "publish_review", lambda md, *, date, config: publish_result)
    monkeypatch.setattr(review, "scan_text", _no_leak)
    rec = _PushRec()
    monkeypatch.setattr(review, "push_summary_dingtalk", rec)
    return rec


def test_run_review_push_has_page_link_never_local_path(tmp_path, monkeypatch):
    url = "https://x.pages.dev/" + "a" * 32 + "/"
    rec = _wire_push(tmp_path, monkeypatch, (url, "ok", "leak_scan 通过"))
    rc = review.run_review(llm=None, config=RadarConfig(), dry_run=False)
    assert rc == 0 and len(rec.texts) == 1
    t = rec.texts[0]
    assert t.startswith("📊 Agent Radar 周报")
    assert url in t and "完整周报" in t
    assert "data/self_improve" not in t and "data/" not in t
    _assert_human(t)


def test_run_review_deploy_failure_push_still_sent(tmp_path, monkeypatch):
    rec = _wire_push(tmp_path, monkeypatch, (None, "deploy_failed", "code=1"))
    review.run_review(llm=None, config=RadarConfig(), dry_run=False)
    t = rec.texts[0]
    assert "没部署成功" in t and "https://" not in t
    for icon in ("🩺", "🔍", "🗳", "📝"):
        assert icon in t                       # 推送照发、内容完整
    assert "data/" not in t


def test_run_review_page_leak_says_so_honestly(tmp_path, monkeypatch):
    rec = _wire_push(tmp_path, monkeypatch, (None, "leak", "泄漏自检命中 2 处"))
    review.run_review(llm=None, config=RadarConfig(), dry_run=False)
    t = rec.texts[0]
    assert "敏感词" in t and "https://" not in t
    assert t.startswith("📊")                  # 正文照发，只是没链接
    assert "data/" not in t


def test_run_review_push_gate_degrades_whole_text(tmp_path, monkeypatch):
    url = "https://x.pages.dev/seg/"
    rec = _wire_push(tmp_path, monkeypatch, (url, "ok", "ok"))
    monkeypatch.setattr(review, "scan_text",
                        lambda text, **kw: ([{"line": 1, "label": "local:x"}], None))
    review.run_review(llm=None, config=RadarConfig(), dry_run=False)
    t = rec.texts[0]
    assert "不推正文" in t and url not in t     # 命中 → 整条降级为通用指针


# ---- DingTalk push ----
class _FakeResp:
    def __init__(self, data):
        self._d = data
        self.status_code = 200
        self.content = b"x"

    def json(self):
        return self._d

    def raise_for_status(self):
        return None


class _FakeSession:
    def __init__(self):
        self.posts = []
        self.trust_env = True

    def post(self, url, **kw):
        self.posts.append((url, kw))
        return _FakeResp({"accessToken": "tok"} if "oauth2" in url else {"processQueryKey": "q"})


def test_push_summary_ok(monkeypatch):
    for k in ("CLIENT_ID", "CLIENT_SECRET", "ROBOT_CODE", "USER_ID"):
        monkeypatch.setenv(f"DINGTALK_{k}", "v")
    s = _FakeSession()
    ok, detail = review.push_summary_dingtalk("hello", session=s)
    assert ok and detail == "sent"
    assert s.trust_env is False                       # domestic — proxy stripped
    url, kw = s.posts[1]
    assert url == review._OTO_URL
    assert kw["json"]["msgKey"] == "sampleMarkdown"
    assert "hello" in kw["json"]["msgParam"]


def test_push_summary_missing_env(monkeypatch):
    for k in ("CLIENT_ID", "CLIENT_SECRET", "ROBOT_CODE", "USER_ID"):
        monkeypatch.delenv(f"DINGTALK_{k}", raising=False)
    ok, detail = review.push_summary_dingtalk("x")
    assert not ok and "缺环境变量" in detail


# ---- leak scan ----
def test_leak_scan_builtin_hits(tmp_path):
    word = "简" + "历"          # built by concat so THIS file never contains the word itself
    hits, warning = scan_text(f"这是一份{word}，请查收", terms_file=tmp_path / "none.txt")
    assert warning is not None                        # vocabulary missing → loud
    assert any(h["label"] == f"builtin:{word}" for h in hits)


def test_leak_scan_local_terms(tmp_path):
    tf = tmp_path / "terms.txt"
    tf.write_text("# c\nACME\n/foo\\d+/\n", encoding="utf-8")
    hits, warning = scan_text("acme 与 foo42 都出现了", terms_file=tf)
    assert warning is None
    assert {h["label"] for h in hits} == {"local:ACME", "local:regex"}


def test_leak_scan_clean(tmp_path):
    tf = tmp_path / "terms.txt"
    tf.write_text("ACME\n", encoding="utf-8")
    hits, _ = scan_text("纯技术内容：agent harness eval", terms_file=tf)
    assert hits == []
