"""Deep-read slot policy (thin grounding yields its slot) + smart grounding truncation.
Regressions for the 2026-07-03 复盘: [3] abstract-only grounding占了名额 while complete
[5]/[6] got one-liners; [2]/[8] were hard-cut mid-sentence at 28000. No network, no LLM."""
from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

import radar.stages.deepread as D
from radar.core.config import Paths, load_config
from radar.core.models import Item, RunContext, Source, SourceType, TimeWindow
from radar.obs import Logger, Tracer

ARX1 = "https://arxiv.org/abs/1111.11111"   # full paper
ARX2 = "https://arxiv.org/abs/2222.22222"   # abstract-only stub → thin
ARX3 = "https://arxiv.org/abs/3333.33333"   # full paper
BLOG = "https://blog.example.com/post"      # short blog — adequate (non-arXiv)


def _item(url, title=None):
    s = Source(id="s", name="S", category="papers", type=SourceType.rss, url="http://x")
    return Item.create(source=s, title=title or url.rsplit("/", 1)[-1], url=url,
                       summary="摘要 " * 80)   # basis comfortably ≥ MIN_BASIS_CHARS


class _LLM:
    def __init__(self):
        self.tags = []
        self.prompts = []
        self.kwargs = []

    def complete(self, prompt, **kw):
        self.tags.append(kw.get("tag"))
        self.prompts.append(prompt)
        self.kwargs.append(kw)
        return SimpleNamespace(ok=True, text="这是一段中文详解。" * 20, error=None)


def _ctx(tmp_path, monkeypatch, top_k):
    monkeypatch.setattr(Paths, "deepread_ckpt", tmp_path / "ckpt")
    monkeypatch.setattr(Paths, "deepread_sources", tmp_path / "src")
    cfg = load_config()
    cfg.deepread_top_k = top_k
    ctx = RunContext(run_id="t", mode="daily", config=cfg, window=TimeWindow(48))
    ctx.log = Logger("t", echo=False)
    ctx.trace = Tracer("t")
    ctx.llm = _LLM()
    return ctx


# ---- slot policy ----
def test_thin_arxiv_yields_slot_to_next_full(tmp_path, monkeypatch):
    texts = {ARX1: "F" * 20000, ARX2: "A" * 5000, ARX3: "F" * 15000, BLOG: "B" * 900}
    monkeypatch.setattr(D, "fetch_article_text", lambda url, config=None, max_chars=0: texts[url])
    ctx = _ctx(tmp_path, monkeypatch, top_k=2)
    items = [_item(ARX1), _item(ARX2), _item(ARX3), _item(BLOG)]   # rank order
    ctx.items = items
    D.DeepReadStage().run(ctx)
    deep = [it.url for it in items if it.explain_zh]
    assert deep == [ARX1, ARX3]                       # thin ARX2 yielded its slot to ARX3
    assert items[1].explain_zh is None                # thin item left as a one-liner
    assert ctx.stats["deepread.thin_skipped"] == [items[1].id]
    assert [it.url for it in ctx.items] == [ARX1, ARX2, ARX3, BLOG]   # display order untouched


def test_fill_with_thin_when_not_enough_full(tmp_path, monkeypatch):
    texts = {ARX1: "A" * 5000, ARX2: "A" * 4200, ARX3: "A" * 4100}
    monkeypatch.setattr(D, "fetch_article_text", lambda url, config=None, max_chars=0: texts[url])
    ctx = _ctx(tmp_path, monkeypatch, top_k=2)
    items = [_item(ARX1), _item(ARX2), _item(ARX3)]
    ctx.items = items
    D.DeepReadStage().run(ctx)
    deep = [it.url for it in items if it.explain_zh]
    assert deep == [ARX1, ARX2]     # honest degrade: thin fills in rank order, still top_k
    assert ctx.stats["deepread.thin_skipped"] == [items[2].id]


def test_adequacy_predicate():
    assert not D._adequate(_item(ARX1), "x" * (D.THIN_ARXIV_CHARS - 1))
    assert D._adequate(_item(ARX1), "x" * D.THIN_ARXIV_CHARS)
    assert D._adequate(_item(BLOG), "x" * 300)    # non-arXiv: the page IS the article


def test_resume_skips_probe_fetch(tmp_path, monkeypatch):
    fetches = []

    def fake_fetch(url, config=None, max_chars=0):
        fetches.append(url)
        return "F" * 20000
    monkeypatch.setattr(D, "fetch_article_text", fake_fetch)
    ctx = _ctx(tmp_path, monkeypatch, top_k=1)
    it = _item(ARX1)
    ctx.items = [it]
    system = Paths.prompts.joinpath("deepread.md").read_text(encoding="utf-8")
    fp = hashlib.sha1(system.encode("utf-8")).hexdigest()[:12]
    date = ctx.started_at.astimezone().strftime("%Y-%m-%d")
    (tmp_path / "ckpt").mkdir(parents=True)
    (tmp_path / "ckpt" / f"{date}.json").write_text(json.dumps(
        {"date": date, "prompt_fp": fp,
         "items": {it.id: {"explain_zh": "缓存详解", "full_text": "F" * 20000}}}),
        encoding="utf-8")
    D.DeepReadStage().run(ctx)
    assert it.explain_zh == "缓存详解"
    assert fetches == []            # probe reused checkpointed full_text — zero re-fetch
    assert ctx.llm.tags == []       # zero LLM calls


# ---- V5: 全员深读 + 薄源标注 + 全文 grounding 常量 + 超时 ----
def test_v5_all_items_deep_read_thin_gets_note(tmp_path, monkeypatch):
    """top_k covers every item (V5 daily) → thin/critic-flagged items are still deep-read;
    the thin arXiv stub AND the very short page get the deterministic 源材料薄 note (07-06
    尺子实锤: short release pages without the note got padded from prior knowledge), a
    full-length page doesn't."""
    texts = {ARX1: "F" * 20000, ARX2: "A" * 5000, BLOG: "B" * 900, ARX3: "F" * 15000}
    monkeypatch.setattr(D, "fetch_article_text", lambda url, config=None, max_chars=0: texts[url])
    ctx = _ctx(tmp_path, monkeypatch, top_k=10)
    items = [_item(ARX1), _item(ARX2), _item(BLOG), _item(ARX3)]
    ctx.items = items
    ctx.stats["critic"] = {items[0].id: {"skip": True, "conf": "high", "why": "PR"}}
    D.DeepReadStage().run(ctx)
    assert all(it.explain_zh and it.explain_zh != D.NO_TEXT for it in items)
    assert "deepread.thin_skipped" not in ctx.stats           # nobody yielded a slot
    noted = [p for p in ctx.llm.prompts if "源材料提示" in p]
    assert len(noted) == 2                                    # arXiv stub + short page
    assert any(f"链接: {ARX2}" in p for p in noted)
    assert any(f"链接: {BLOG}" in p for p in noted)            # 900 chars < THIN_NOTE_CHARS
    assert all("绝不用你自己的背景知识" in p for p in noted)      # the prior-knowledge ban has teeth
    assert all(kw.get("timeout") == D.LLM_TIMEOUT for kw in ctx.llm.kwargs)


def test_v5_config_and_caps():
    """V5 contract pins: opus + 全部条目深读 + 全文 grounding（这些值回退=功能回退）."""
    from radar.core.config import ModelsConfig, RadarConfig
    assert ModelsConfig().deepread == "opus"
    assert RadarConfig().deepread_top_k == RadarConfig().daily_max_items == 10
    assert D.GROUNDING_CAP == 80000 and D.FETCH_CAP == 120000
    assert D.LLM_TIMEOUT >= 900


def test_failed_item_not_checkpointed_and_retried_next_run(tmp_path, monkeypatch):
    """A checkpointed NO_TEXT turns a one-off LLM hiccup into a permanent hole for the day
    (live 2026-07-06: one opus `exit 1` killed [2] for every resume). Failures must stay
    OUT of the checkpoint so the next run retries them."""
    monkeypatch.setattr(D, "fetch_article_text", lambda url, config=None, max_chars=0: "F" * 20000)

    class FlakyLLM(_LLM):
        def __init__(self, ok):
            super().__init__()
            self.ok = ok

        def complete(self, prompt, **kw):
            if not self.ok:
                self.prompts.append(prompt)
                return SimpleNamespace(ok=False, text="", error="exit 1: ")
            return super().complete(prompt, **kw)

    ctx = _ctx(tmp_path, monkeypatch, top_k=1)
    it = _item(ARX1)
    ctx.items = [it]
    ctx.llm = FlakyLLM(ok=False)
    D.DeepReadStage().run(ctx)
    assert it.explain_zh == D.NO_TEXT
    date = ctx.started_at.astimezone().strftime("%Y-%m-%d")
    ck_file = tmp_path / "ckpt" / f"{date}.json"          # sole item failed → nothing to persist
    ck = json.loads(ck_file.read_text(encoding="utf-8")) if ck_file.exists() else {"items": {}}
    assert it.id not in ck["items"]                       # failure NOT checkpointed

    ctx2 = _ctx(tmp_path, monkeypatch, top_k=1)
    ctx2.started_at = ctx.started_at
    it2 = _item(ARX1)
    ctx2.items = [it2]
    ctx2.llm = FlakyLLM(ok=True)
    D.DeepReadStage().run(ctx2)
    assert it2.explain_zh and it2.explain_zh != D.NO_TEXT  # retried, not "resumed" into failure


def test_undiagnosed_cli_exit_is_retried(monkeypatch):
    """`exit N:` with empty stderr carries no diagnostic — treat as transient (one lost
    详解 costs more than a retry)."""
    from radar.llm.claude_code import ClaudeCodeLLM
    llm = ClaudeCodeLLM(config=None, log=None, trace=None)
    calls = {"n": 0}

    def fake_run(prompt, system, model, timeout):
        calls["n"] += 1
        if calls["n"] < 3:
            return False, "exit 1: ", None
        return True, "成功", {"usage": None}
    monkeypatch.setattr(llm, "_run", fake_run)
    monkeypatch.setattr("time.sleep", lambda s: None)
    res = llm.complete("p", model="opus", retries=3)
    assert res.ok and res.text == "成功" and calls["n"] == 3

    calls["n"] = 0

    def hard_fail(prompt, system, model, timeout):
        calls["n"] += 1
        return False, "exit 1: real diagnostic message", None
    monkeypatch.setattr(llm, "_run", hard_fail)
    res = llm.complete("p", model="opus", retries=3)
    assert not res.ok and calls["n"] == 1                 # diagnosed failure: still no retry


# ---- smart grounding truncation ----
def test_smart_grounding_short_unchanged():
    assert D.smart_grounding("short text", cap=100) == "short text"


def test_smart_grounding_cuts_references():
    body = ("Intro paragraph. " * 1200).strip()                 # ~20K of real body
    refs = "\nReferences\n" + ("[1] Some Citation. " * 800)     # ~15K low-info tail
    out = D.smart_grounding(body + refs, cap=28000)
    assert "Some Citation" not in out                           # tail section dropped…
    assert out.endswith("Intro paragraph.")                     # …body kept whole, no elision
    assert D._ELISION not in out


def test_smart_grounding_keeps_head_and_tail():
    paras = [f"para{i} " + "x" * 90 for i in range(600)]        # ~58K, clear boundaries
    text = "\n\n".join(paras)
    out = D.smart_grounding(text, cap=28000)
    assert len(out) <= 28000
    assert D._ELISION in out
    head, tail = out.split(D._ELISION)
    assert head.startswith("para0 ")
    assert "para599" in tail                                    # the ENDING is preserved
    assert head.split("\n\n")[-1] in paras                      # boundary snap: whole paragraphs only


def test_smart_grounding_no_boundary_falls_back_to_hard_cut():
    out = D.smart_grounding("y" * 60000, cap=28000)
    assert len(out) <= 28000
