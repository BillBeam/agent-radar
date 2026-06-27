"""Eval unit tests — no network, no real LLM (judge is stubbed)."""
from __future__ import annotations

import json

from radar.core.io import atomic_write_json
from radar.core.models import Item, RunContext, Source, SourceType, TimeWindow


# ---- local helpers (mirror test_core, kept independent) ----
def _ctx(mode="daily"):
    from radar.core.config import load_config
    from radar.obs import Logger, Tracer
    ctx = RunContext(run_id="test", mode=mode, config=load_config(), window=TimeWindow(48))
    ctx.log = Logger("test", echo=False)
    ctx.trace = Tracer("test")
    return ctx


def _item(title="t", url=None):
    s = Source(id="s", name="S", category="harness", type=SourceType.rss, url="http://x", weight=1.0)
    return Item.create(source=s, title=title, url=url or f"http://x/{title}")


class _Res:
    def __init__(self, ok, error=None, text=""):
        self.ok = ok
        self.error = error
        self.text = text


class FakeJudge:
    """Duck-typed LLM: complete_json pops the next canned response per call.
    Thread-safe (judges run concurrently). A response can be:
      dict → parsed JSON (success); str → a failure with that error string
      (e.g. "529 overloaded"); None / exhausted → a generic failure."""
    def __init__(self, *responses):
        import threading
        self.responses = list(responses)
        self.calls = 0
        self._lock = threading.Lock()

    def complete_json(self, prompt, **kw):
        with self._lock:
            self.calls += 1
            resp = self.responses.pop(0) if self.responses else None
        if isinstance(resp, dict):
            return resp, _Res(True)
        if isinstance(resp, str):
            return None, _Res(False, error=resp)
        return None, _Res(False, error=None)


def _claims(supported=0, unsupported=0, distorted=0, commentary=0):
    cs = []
    cs += [{"claim": "s", "type": "factual", "verdict": "supported", "evidence": "q"}
           for _ in range(supported)]
    cs += [{"claim": "u", "type": "factual", "verdict": "unsupported", "evidence": "no"}
           for _ in range(unsupported)]
    cs += [{"claim": "d", "type": "factual", "verdict": "distorted", "evidence": "twist"}
           for _ in range(distorted)]
    cs += [{"claim": "c", "type": "commentary", "verdict": "commentary"}
           for _ in range(commentary)]
    return {"claims": cs, "note": "n"}


# ---- _tally: code computes support_rate, not the LLM ----
def test_tally_support_rate():
    from radar.eval.faithfulness import _tally
    t = _tally(_claims(supported=3, unsupported=1)["claims"])
    assert t["n_factual"] == 4 and t["n_supported"] == 3
    assert t["support_rate"] == 0.75
    assert len(t["issues"]) == 1 and t["issues"][0]["verdict"] == "unsupported"


def test_tally_distorted_counts_as_issue():
    from radar.eval.faithfulness import _tally
    t = _tally(_claims(supported=1, distorted=1)["claims"])
    assert t["support_rate"] == 0.5 and len(t["issues"]) == 1


def test_tally_no_factual_is_none():
    from radar.eval.faithfulness import _tally
    t = _tally(_claims(commentary=2)["claims"])
    assert t["n_factual"] == 0 and t["support_rate"] is None and t["n_commentary"] == 2


# ---- grounding resolution: sidecar → full_text → none ----
def test_resolve_grounding_precedence(tmp_path, monkeypatch):
    import radar.eval.faithfulness as F
    monkeypatch.setattr(F.Paths, "deepread_sources", tmp_path)
    item = {"id": "abc", "full_text": "FULL"}

    text, label = F.resolve_grounding(item, "2026-06-26")
    assert label == "full_text" and text == "FULL"          # no sidecar → full_text

    atomic_write_json(tmp_path / "2026-06-26" / "abc.json", {"source_text": "SIDE"})
    text, label = F.resolve_grounding(item, "2026-06-26")
    assert label == "sidecar" and text == "SIDE"            # sidecar wins when present

    text, label = F.resolve_grounding({"id": "zzz"}, "2026-06-26")
    assert label == "none" and text is None                 # neither → none


def test_skip_reason():
    from radar.eval.faithfulness import _skip_reason
    assert _skip_reason({"explain_zh": ""}, "full_text") == "no_explain"
    assert _skip_reason({"explain_zh": "（原文正文未能获取，仅标题+链接可读）"}, "full_text") == "degraded"
    assert _skip_reason({"explain_zh": "真详解"}, "none") == "no_source"
    assert _skip_reason({"explain_zh": "真详解"}, "full_text") is None


# ---- aggregate reports honest coverage (skipped/no-factual counted, mean over scored) ----
def test_eval_faithfulness_coverage(tmp_path, monkeypatch):
    import radar.eval.faithfulness as F
    monkeypatch.setattr(F.Paths, "deepread_sources", tmp_path)   # no sidecars on disk
    items = [
        {"id": "a", "title": "A", "explain_zh": "x", "full_text": "src"},          # scored 1.0
        {"id": "b", "title": "B", "explain_zh": "y", "full_text": "src"},          # scored 0.5
        {"id": "c", "title": "C", "explain_zh": "z"},                              # no_source
        {"id": "d", "title": "D", "explain_zh": "（原文未获取）", "full_text": "s"},  # degraded
        {"id": "e", "title": "E", "explain_zh": "w", "full_text": "src"},          # judge fails
    ]
    judge = FakeJudge(
        _claims(supported=4),                  # a
        _claims(supported=1, unsupported=1),   # b
        None,                                  # e (c, d skip before any judge call)
    )
    out = F.eval_faithfulness(judge, items, "2026-06-26", model="sonnet", system="sys")
    assert out["n_total"] == 5
    assert out["n_scored"] == 2
    assert out["n_skipped"] == 3               # c(no_source) + d(degraded) + e(judge_failed)
    assert out["mean_support_rate"] == 0.75    # (1.0 + 0.5) / 2 — only scored items
    assert out["n_issues"] == 1
    assert judge.calls == 3                     # c and d skipped before judging


def test_eval_faithfulness_all_commentary_is_no_factual(tmp_path, monkeypatch):
    import radar.eval.faithfulness as F
    monkeypatch.setattr(F.Paths, "deepread_sources", tmp_path)
    items = [{"id": "a", "title": "A", "explain_zh": "x", "full_text": "src"}]
    out = F.eval_faithfulness(FakeJudge(_claims(commentary=3)), items, "2026-06-26",
                              model="sonnet", system="sys")
    assert out["n_scored"] == 0 and out["n_no_factual"] == 1
    assert out["mean_support_rate"] is None


# ---- deepread sidecar write is correct AND defensive ----
def test_deepread_sidecar_write(tmp_path, monkeypatch):
    import radar.stages.deepread as D
    monkeypatch.setattr(D.Paths, "deepread_sources", tmp_path)
    ctx = _ctx()
    it = _item(title="X")
    D._write_source_sidecar(ctx, it, "GROUND TEXT")
    date = ctx.started_at.astimezone().strftime("%Y-%m-%d")
    data = json.loads((tmp_path / date / f"{it.id}.json").read_text(encoding="utf-8"))
    assert data["source_text"] == "GROUND TEXT" and data["item_id"] == it.id
    assert data["chars"] == len("GROUND TEXT")


def test_sidecar_write_never_breaks_deepread(monkeypatch):
    import radar.stages.deepread as D

    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(D, "atomic_write_json", boom)
    # must swallow the error (deepread is the daily critical path)
    D._write_source_sidecar(_ctx(), _item(), "text")


# ---- cli routing ----
def test_cmd_eval_requires_date():
    from radar.cli import cmd_eval
    assert cmd_eval(None) == 2
    assert cmd_eval("") == 2


def test_run_eval_missing_digest(tmp_path, monkeypatch):
    import radar.eval.run as R
    monkeypatch.setattr(R.Paths, "digests", tmp_path)
    assert R.run_eval("2099-01-01", llm=None) is None


# ---- robustness: classify failures, abort early on limit, resume on re-run ----
def test_classify_error():
    from radar.eval.faithfulness import _classify_error
    assert _classify_error("json parse: expecting value") == "parse_error"
    assert _classify_error("timeout") == "timeout"
    assert _classify_error("exit 1: 529 overloaded") == "rate_limit"
    assert _classify_error("usage limit reached") == "rate_limit"
    assert _classify_error("some weird error") == "llm_error"


def test_rate_limit_aborts_remaining(tmp_path, monkeypatch):
    import radar.eval.faithfulness as F
    monkeypatch.setattr(F.Paths, "deepread_sources", tmp_path)
    items = [{"id": i, "title": i, "explain_zh": "x", "full_text": "src"} for i in ("a", "b", "c")]
    judge = FakeJudge("529 overloaded")            # first call hits the limit; rest must not run
    out = F.eval_faithfulness(judge, items, "2026-06-26", model="sonnet", system="sys",
                              max_workers=1)
    assert judge.calls == 1                          # did NOT grind through the doomed batch
    assert out["rate_limited"] is True
    assert out["n_skipped"] == 3
    assert out["skip_breakdown"].get("rate_limit") == 1
    assert out["skip_breakdown"].get("aborted_rate_limit") == 2


def test_resume_reuses_prior_scored(tmp_path, monkeypatch):
    import radar.eval.faithfulness as F
    monkeypatch.setattr(F.Paths, "deepread_sources", tmp_path)
    items = [{"id": "a", "title": "A", "explain_zh": "x", "full_text": "src"}]
    first = F.eval_faithfulness(FakeJudge(_claims(supported=2)), items, "2026-06-26",
                                model="sonnet", system="sys")
    assert first["n_scored"] == 1

    judge2 = FakeJudge()                             # would fail if called
    second = F.eval_faithfulness(judge2, items, "2026-06-26", model="sonnet", system="sys",
                                 prior=first)
    assert judge2.calls == 0                          # unchanged item reused, no token spent
    assert second["n_reused"] == 1 and second["n_scored"] == 1
    assert second["mean_support_rate"] == first["mean_support_rate"]


def test_judge_item_salvages_truncated_output():
    from radar.eval.faithfulness import judge_item
    # model opened a ```json fence and got cut off mid-claim → whole-parse fails,
    # but the two complete flat claim objects must still be recovered.
    truncated = (
        '```json\n{\n  "claims": [\n'
        '    {"claim": "a", "type": "factual", "verdict": "supported", "evidence": "x"},\n'
        '    {"claim": "b", "type": "factual", "verdict": "unsupported", "evidence": "y"},\n'
        '    {"claim": "c", "type": "fac'      # truncated, no closing brace/fence
    )

    class _Trunc:
        def complete_json(self, prompt, **kw):
            return None, _Res(False, error="json parse: no JSON found", text=truncated)

    out = judge_item(_Trunc(), {"explain_zh": "x"}, "src", model="sonnet", system="sys")
    assert out["ok"] is True and out["salvaged"] is True
    assert out["n_factual"] == 2 and out["n_supported"] == 1     # a,b recovered; c dropped
    assert out["support_rate"] == 0.5


def test_judge_item_real_failure_not_salvaged():
    from radar.eval.faithfulness import judge_item

    class _Timeout:
        def complete_json(self, prompt, **kw):
            return None, _Res(False, error="timeout", text="")

    out = judge_item(_Timeout(), {"explain_zh": "x"}, "src", model="sonnet", system="sys")
    assert out["ok"] is False and out["error_kind"] == "timeout"


def test_resume_rejudges_changed_content(tmp_path, monkeypatch):
    import radar.eval.faithfulness as F
    monkeypatch.setattr(F.Paths, "deepread_sources", tmp_path)
    items = [{"id": "a", "title": "A", "explain_zh": "x", "full_text": "src"}]
    first = F.eval_faithfulness(FakeJudge(_claims(supported=2)), items, "2026-06-26",
                                model="sonnet", system="sys")
    items[0]["explain_zh"] = "DIFFERENT 详解"          # content changed → must re-judge
    judge2 = FakeJudge(_claims(supported=1, unsupported=1))
    second = F.eval_faithfulness(judge2, items, "2026-06-26", model="sonnet", system="sys",
                                 prior=first)
    assert judge2.calls == 1 and second["n_reused"] == 0
    assert second["mean_support_rate"] == 0.5


def test_checkpoint_persists_after_each_item(tmp_path, monkeypatch):
    import radar.eval.faithfulness as F
    monkeypatch.setattr(F.Paths, "deepread_sources", tmp_path)
    items = [{"id": i, "title": i, "explain_zh": "x", "full_text": "src"} for i in ("a", "b")]
    seen = []
    F.eval_faithfulness(FakeJudge(_claims(supported=1), _claims(supported=1)),
                        items, "2026-06-26", model="sonnet", system="sys",
                        checkpoint=lambda agg: seen.append(agg["n_scored"]))
    assert len(seen) == 2            # one checkpoint per item — progress is never lost
    assert seen[-1] == 2             # final checkpoint sees both scored
