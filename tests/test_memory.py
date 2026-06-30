"""Phase B memory tests — no network, no real LLM.

Wiring is asserted here (store round-trip, tag-overlap signal, rerank injection +
toggle + degrade). The down-weight *behavior* is proven by the real-LLM A/B run
(scripts/prove_rerank_personalization.py), not by these unit tests.
"""
from __future__ import annotations

import re

from radar.core.config import Paths, load_config
from radar.core.models import Item, RunContext, Source, SourceType, TimeWindow
from radar.memory.store import MemoryStore
from radar.stages.rerank import RerankStage, load_known_topics


# ---- helpers (self-contained, mirror test_core/test_eval) ----
def _ctx(mode="daily"):
    from radar.obs import Logger, Tracer
    ctx = RunContext(run_id="test", mode=mode, config=load_config(), window=TimeWindow(48))
    ctx.log = Logger("test", echo=False)
    ctx.trace = Tracer("test")
    return ctx


def _item(title="t", url=None, tags=None, score=None):
    s = Source(id="s", name="S", category="harness", type=SourceType.rss, url="http://x", weight=1.0)
    it = Item.create(source=s, title=title, url=url or f"http://x/{title}")
    it.tags = list(tags or [])
    it.score = score
    return it


class _Res:
    def __init__(self, ok=True, error=None, text=""):
        self.ok, self.error, self.text = ok, error, text


class CapturingLLM:
    """Duck-typed LLM: records the rerank user prompt + system, returns identity order."""
    def __init__(self):
        self.prompt = None
        self.system = None

    def complete_json(self, prompt, **kw):
        self.prompt = prompt
        self.system = kw.get("system")
        idxs = [int(m) for m in re.findall(r"^\[(\d+)\]", prompt, flags=re.M)]
        return [{"i": i, "why": "x"} for i in idxs], _Res(True)


# ---- store: round-trip + idempotency ----
def test_store_roundtrip_and_idempotent(tmp_path):
    store = MemoryStore(tmp_path / "m.db")
    it = _item(title="A", url="http://x/A", tags=["rag", "eval"])
    assert store.remember_digest("2026-06-30", [it]) == 1
    cand = _item(title="B", url="http://x/B", tags=["rag"])
    h = store.topic_history(cand, recent_days=365, today="2026-06-30")
    assert h["count"] == 1 and h["last_date"] == "2026-06-30"
    store.remember_digest("2026-06-30", [it])               # re-remember same id
    assert store.topic_history(cand, recent_days=365, today="2026-06-30")["count"] == 1  # not 2


# ---- store: tag-overlap signal + recency window ----
def test_topic_history_overlap_and_window(tmp_path):
    store = MemoryStore(tmp_path / "m.db")
    store.remember_digest("2026-06-20", [_item(title="R", url="http://x/R", tags=["rag"])])   # in 30d
    store.remember_digest("2026-01-01", [_item(title="O", url="http://x/O", tags=["rag"])])   # out
    cand = _item(title="C", url="http://x/C", tags=["rag"])
    assert store.topic_history(cand, recent_days=30, today="2026-06-30")["count"] == 1        # recent only
    disjoint = _item(title="D", url="http://x/D", tags=["sandbox"])
    assert store.topic_history(disjoint, recent_days=365, today="2026-06-30")["count"] == 0


def test_load_known_topics_section_extraction(tmp_path):
    p = tmp_path / "USER.md"
    p.write_text("# U\n## 背景\n资深后端\n## 已会清单\n- rag\n- ctx-eng\n## 反馈史\nFEEDBACK_SENTINEL\n",
                 encoding="utf-8")
    body = load_known_topics(p)
    assert "rag" in body and "ctx-eng" in body
    assert "资深后端" not in body                       # 背景 (before) not captured
    assert "反馈史" not in body and "FEEDBACK_SENTINEL" not in body   # 反馈史 (after) stops capture
    assert load_known_topics(tmp_path / "absent.md") == ""    # missing → ""


# ---- rerank injection (the key wiring test) ----
def test_rerank_injects_known_topics_tags_and_marker(tmp_path, monkeypatch):
    user_md = tmp_path / "USER.md"
    user_md.write_text("## 已会清单\n- rag 混合检索\n- context-engineering\n", encoding="utf-8")
    monkeypatch.setattr(Paths, "user_md", user_md)
    store = MemoryStore(tmp_path / "m.db")
    today = __import__("datetime").datetime.now().astimezone().strftime("%Y-%m-%d")
    store.remember_digest(today, [_item(title="old", url="http://x/old", tags=["rag"])])

    a = _item(title="A", url="http://x/A", tags=["rag"], score=5)     # → same-topic marker
    b = _item(title="B", url="http://x/B", tags=["eval"], score=4)
    ctx = _ctx()
    ctx.config.memory.personalize_rerank = True
    ctx.memory = store
    fake = CapturingLLM()
    ctx.llm = fake
    ctx.items = [a, b]
    RerankStage().run(ctx)

    assert "已会主题" in fake.prompt                       # preamble injected
    assert "rag 混合检索" in fake.prompt                   # USER.md section body injected
    assert "〔标签" in fake.prompt                          # candidate tags shown to the model
    assert "⟨近30天同主题×1⟩" in fake.prompt               # memory marker on the rag candidate
    assert "对他新" in (fake.system or "")                  # refined rerank.md rubric is the system


# ---- toggle off → byte-identical baseline ----
def test_rerank_toggle_off_is_baseline(tmp_path, monkeypatch):
    user_md = tmp_path / "USER.md"
    user_md.write_text("## 已会清单\n- rag\n", encoding="utf-8")
    monkeypatch.setattr(Paths, "user_md", user_md)
    ctx = _ctx()
    ctx.config.memory.personalize_rerank = False
    ctx.memory = None
    fake = CapturingLLM()
    ctx.llm = fake
    ctx.items = [_item(title="A", url="http://x/A", tags=["rag"], score=5),
                 _item(title="B", url="http://x/B", tags=["eval"], score=4)]
    RerankStage().run(ctx)
    assert "已会主题" not in fake.prompt
    assert "〔标签" not in fake.prompt and "⟨近" not in fake.prompt
    assert fake.prompt.startswith("Rank these candidates best-first")


# ---- degrade: personalize on but no USER.md and no memory → still ranks, no crash ----
def test_rerank_degrades_without_user_md_or_memory(tmp_path, monkeypatch):
    monkeypatch.setattr(Paths, "user_md", tmp_path / "absent-USER.md")
    ctx = _ctx()
    ctx.config.memory.personalize_rerank = True
    ctx.memory = None
    fake = CapturingLLM()
    ctx.llm = fake
    ctx.items = [_item(title="A", url="http://x/A", score=5),
                 _item(title="B", url="http://x/B", score=4)]
    RerankStage().run(ctx)                  # must not raise
    assert "已会主题" not in fake.prompt     # known empty → baseline
    assert len(ctx.items) == 2
