"""E1 review-mode unit tests — no network, no real LLM, no real DingTalk.
Covers: read-only gather with per-source degradation, honest empty sections, summary
lines (MIN_PAIRS gap / source mix / draft count), the never-auto-apply marker, dry-run
writing ONLY the review file, DingTalk push (fake session) and the leak scanner."""
from __future__ import annotations

import json

from radar.core.config import Paths
from radar.self_improve import review
from radar.self_improve.leak_scan import scan_text


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


# ---- gather ----
def test_gather_all_empty_degrades_honestly(tmp_path, monkeypatch):
    _wire_paths(tmp_path, monkeypatch)
    g = review.gather(now="t")
    assert g["eval_trend"] == [] and g["votes"] == [] and g["digest_days"] == []
    assert g["self_applicable"] == [] and g["critic"] == [] and g["watchlist"] is None
    md = review.render_markdown(g, None, "无 LLM 后端",
                                review.build_summary(g, None, "无 LLM 后端", "x.md"), "2026-07-05")
    assert "暂无 eval 报告" in md and "暂无投票数据" in md and "暂无 digest 数据" in md
    assert "本期无 self_applicable" in md and "暂无 critic" in md


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


def test_gather_bad_json_degrades(tmp_path, monkeypatch):
    _wire_paths(tmp_path, monkeypatch)
    (tmp_path / "feedback" / "2026-07-01.json").write_text("{broken", encoding="utf-8")
    (tmp_path / "digests" / "2026-07-01.items.json").write_text("[broken", encoding="utf-8")
    g = review.gather(now="t")           # must not raise
    assert g["votes"] == [] and g["digest_days"] == []


# ---- summary / render ----
def test_summary_lines(tmp_path, monkeypatch):
    _wire_paths(tmp_path, monkeypatch)
    _seed(tmp_path)
    g = review.gather(now="t")
    draft = "以下均为草案，等待拍板\n1. 改 A\n2. 改 B\n"
    s = review.build_summary(g, draft, None, "data/x.md")
    assert "👍1/👎3" in s                     # cumulative votes (1+0 up, 2+1 down)
    assert f"< {review.MIN_PAIRS}" in s and "还差" in s   # MIN_PAIRS gap, honest
    assert "arXiv×2" in s and "HN×1" in s     # source mix
    assert "草案建议 2 条" in s                # suggestion count
    assert "data/x.md" in s                   # full-report pointer


def test_render_never_autoapply_marker(tmp_path, monkeypatch):
    _wire_paths(tmp_path, monkeypatch)
    g = review.gather(now="t")
    md = review.render_markdown(g, "草案体", None,
                                review.build_summary(g, "草案体", None, "x.md"), "2026-07-05")
    assert "不会被自动应用" in md and "拍板" in md


def test_count_suggestions():
    assert review._count_suggestions("1. a\n2. b\n正文 3.5 不算\n 3、c\n") == 3
    assert review._count_suggestions(None) == 0
    # only the 草案建议 section counts — 观察/WATCHLIST numbering must not inflate it
    draft = ("**观察**\n1. x\n2. y\n\n**草案建议**（等待拍板）\n1. **改什么**：a\n"
             "\n**WATCHLIST 盘点**\n1. p\n2. q\n")
    assert review._count_suggestions(draft) == 1


# ---- run_review (dry) ----
def test_run_review_dry_writes_only_review_file(tmp_path, monkeypatch):
    _wire_paths(tmp_path, monkeypatch)
    _seed(tmp_path)
    rc = review.run_review(llm=None, dry_run=True)
    assert rc == 0
    files = list((tmp_path / "reviews").glob("*-review.md"))
    assert len(files) == 1
    body = files[0].read_text(encoding="utf-8")
    assert "dry-run" in body and "不会被自动应用" in body
    # nothing else appeared in the data dirs
    assert not list((tmp_path / "eval").iterdir())
    assert len(list((tmp_path / "feedback").iterdir())) == 2   # untouched fixtures


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
