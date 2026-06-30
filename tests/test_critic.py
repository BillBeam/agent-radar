"""Phase C tests — critic verdicts + deepread gate/checkpoint + per-call trace.

Wiring + safety are asserted here (parse/store, gate predicate, conf normalization,
annotation render, checkpoint resume, trace roll-up). The critic's JUDGMENT (flag
garbage, never误标 hardcore) is proven by the real-LLM self-prove, not unit tests.
"""
from __future__ import annotations

import json

from radar.core.config import Paths, load_config
from radar.core.models import Item, RunContext, Source, SourceType, TimeWindow


# ---- helpers ----
def _ctx(mode="daily"):
    from radar.obs import Logger, Tracer
    ctx = RunContext(run_id="test", mode=mode, config=load_config(), window=TimeWindow(48))
    ctx.log = Logger("test", echo=False)
    ctx.trace = Tracer("test")
    return ctx


def _item(title="t", url=None, tags=None):
    s = Source(id="s", name="S", category="harness", type=SourceType.rss, url="http://x", weight=1.0)
    it = Item.create(source=s, title=title, url=url or f"http://x/{title}")
    it.tags = list(tags or [])
    return it


class _Res:
    def __init__(self, ok=True, text="", error=None, usage=None, model="sonnet"):
        self.ok, self.text, self.error, self.usage, self.model = ok, text, error, usage, model


class FakeLLM:
    """complete() → canned text; complete_json() → canned list. Records the tag per call."""
    def __init__(self, json_resp=None, text="这是一段中文详解。" * 20):
        self.json_resp, self.text, self.calls = json_resp, text, []

    def complete(self, prompt, **kw):
        self.calls.append(kw.get("tag"))
        return _Res(True, text=self.text)

    def complete_json(self, prompt, **kw):
        self.calls.append(kw.get("tag"))
        return self.json_resp, _Res(True)


# ---- critic stage: parse + store + sidecar + predicates ----
def test_critic_stage_stores_verdicts_and_sidecar(tmp_path, monkeypatch):
    from radar.stages.critic import CriticStage, high_conf_skip
    monkeypatch.setattr(Paths, "critic", tmp_path / "critic")
    ctx = _ctx()
    a, b = _item(title="PR", url="http://x/pr"), _item(title="hardcore", url="http://x/hard")
    ctx.items = [a, b]
    ctx.llm = FakeLLM(json_resp=[
        {"i": 0, "skip": True, "conf": "high", "why": "厂商发布稿"},
        {"i": 1, "skip": False, "conf": "low", "why": ""},
    ])
    CriticStage().run(ctx)
    assert ctx.stats["critic"][a.id] == {"skip": True, "conf": "high", "why": "厂商发布稿"}
    assert ctx.stats["critic"][b.id]["skip"] is False
    assert high_conf_skip(ctx, a) is True and high_conf_skip(ctx, b) is False
    assert ctx.llm.calls == ["critic"]                       # tagged for the trace
    date = ctx.started_at.astimezone().strftime("%Y-%m-%d")
    sc = json.loads((tmp_path / "critic" / f"{date}.json").read_text(encoding="utf-8"))
    assert sc["n"] == 2 and len(sc["items"]) == 2


def test_critic_invalid_conf_normalized_to_low(tmp_path, monkeypatch):
    """A malformed conf must degrade to 'low' so it can NEVER silently gate deepread (safe)."""
    from radar.stages.critic import CriticStage, high_conf_skip
    monkeypatch.setattr(Paths, "critic", tmp_path / "critic")
    ctx = _ctx()
    a = _item(title="x", url="http://x/x")
    ctx.items = [a]
    ctx.llm = FakeLLM(json_resp=[{"i": 0, "skip": True, "conf": "garbage", "why": "y"}])
    CriticStage().run(ctx)
    assert ctx.stats["critic"][a.id]["conf"] == "low"
    assert high_conf_skip(ctx, a) is False                   # malformed → never省 opus


def test_high_conf_skip_neutral_without_critic():
    from radar.stages.critic import high_conf_skip, critic_verdict
    ctx = _ctx()
    it = _item()
    assert high_conf_skip(ctx, it) is False                  # no critic run → neutral
    assert critic_verdict(ctx, it)["skip"] is False


# ---- brief annotation ----
def test_brief_annotation_renders():
    from radar.stages.synthesize import _critic_note, _render_brief
    assert _critic_note({"skip": True, "conf": "high", "why": "PR"}) == "⚠️ 可跳过 · PR"
    assert _critic_note({"skip": True, "conf": "low", "why": "?"}) == "⚠️ 疑似可跳过 · ?"
    assert _critic_note({"skip": False, "conf": "low", "why": ""}) == ""
    assert _critic_note(None) == ""
    it = _item(title="T", url="http://x/T")
    it.reason = "why"
    assert "⚠️ 可跳过 · PR" in _render_brief(it, 1, {"skip": True, "conf": "high", "why": "PR"})
    assert "⚠️" not in _render_brief(it, 1, {"skip": False, "conf": "low", "why": ""})


# ---- deepread gate: high-conf skip loses its slot; borderline + keep stay ----
def test_deepread_gate_skips_high_conf_only(tmp_path, monkeypatch):
    from radar.stages import deepread as dr
    monkeypatch.setattr(dr, "fetch_article_text", lambda url, **kw: "x" * 500)
    monkeypatch.setattr(Paths, "deepread_ckpt", tmp_path / "ckpt")
    monkeypatch.setattr(Paths, "deepread_sources", tmp_path / "src")
    ctx = _ctx()
    ctx.config.deepread_top_k = 5
    keep = _item(title="keep", url="http://x/keep")
    skip_hi = _item(title="skiphi", url="http://x/skiphi")
    skip_lo = _item(title="skiplo", url="http://x/skiplo")
    ctx.items = [keep, skip_hi, skip_lo]
    ctx.stats["critic"] = {
        skip_hi.id: {"skip": True, "conf": "high", "why": "PR"},
        skip_lo.id: {"skip": True, "conf": "low", "why": "?"},
    }
    ctx.llm = FakeLLM(text="这是中文详解。" * 30)
    dr.DeepReadStage().run(ctx)
    assert keep.explain_zh and keep.explain_zh != dr.NO_TEXT       # deep-read
    assert skip_lo.explain_zh and skip_lo.explain_zh != dr.NO_TEXT  # borderline STILL deep-read
    assert skip_hi.explain_zh is None                             # high-conf gated out, never touched


# ---- deepread checkpoint: a re-run reuses completed items (skips LLM) ----
def test_deepread_checkpoint_resumes(tmp_path, monkeypatch):
    from radar.stages import deepread as dr
    monkeypatch.setattr(dr, "fetch_article_text", lambda url, **kw: "x" * 500)
    monkeypatch.setattr(Paths, "deepread_ckpt", tmp_path / "ckpt")
    monkeypatch.setattr(Paths, "deepread_sources", tmp_path / "src")

    class CountingLLM(FakeLLM):
        def __init__(self):
            super().__init__(text="详解" * 30)
            self.n = 0
        def complete(self, prompt, **kw):
            self.n += 1
            return super().complete(prompt, **kw)

    ctx = _ctx()
    ctx.config.deepread_top_k = 2
    a, b = _item(title="a", url="http://x/a"), _item(title="b", url="http://x/b")
    ctx.items = [a, b]
    ctx.llm = CountingLLM()
    dr.DeepReadStage().run(ctx)
    assert ctx.llm.n == 2 and a.explain_zh and b.explain_zh

    ctx2 = _ctx()
    ctx2.started_at = ctx.started_at                 # same date → same checkpoint file
    ctx2.config.deepread_top_k = 2
    a2, b2 = _item(title="a", url="http://x/a"), _item(title="b", url="http://x/b")  # same ids
    ctx2.items = [a2, b2]
    ctx2.llm = CountingLLM()
    dr.DeepReadStage().run(ctx2)
    assert ctx2.llm.n == 0                            # all reused from checkpoint (no LLM)
    assert a2.explain_zh and b2.explain_zh


# ---- per-call trace + per-stage roll-up ----
def test_per_call_trace_records_event_and_rollup():
    from radar.llm.claude_code import ClaudeCodeLLM
    events = []

    class FakeTracer:
        def event(self, kind, **f):
            events.append((kind, f))

    llm = ClaudeCodeLLM(config=None, log=None, trace=FakeTracer())
    llm._record_call("sonnet", "deepread", 123.4, {"input_tokens": 10, "output_tokens": 5})
    assert events[0][0] == "llm_call"
    e = events[0][1]
    assert e["tag"] == "deepread" and e["ms"] == 123.4 and e["input"] == 10 and e["output"] == 5
    assert llm.by_stage["deepread"]["calls"] == 1 and llm.by_stage["deepread"]["input"] == 10
    llm._record_call("sonnet", "deepread", 100.0, {"input_tokens": 2, "output_tokens": 1})
    assert llm.by_stage["deepread"]["calls"] == 2 and llm.by_stage["deepread"]["input"] == 12


def test_tracer_is_thread_locked():
    from radar.obs import Tracer
    assert hasattr(Tracer("t"), "_lock")
