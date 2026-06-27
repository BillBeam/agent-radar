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
def test_cmd_eval_no_date_runs_trend(tmp_path, monkeypatch):
    # no date → cross-day trend (no LLM); empty dir → graceful "no reports", exit 0
    import radar.eval.report as R
    from radar.cli import cmd_eval
    monkeypatch.setattr(R.Paths, "eval", tmp_path)
    assert cmd_eval(None) == 0
    assert cmd_eval("") == 0


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


# ================= Block ② ranking eval =================
def _disp(*ids):
    """Items in display order (index = rank)."""
    return [{"id": i, "title": i.upper()} for i in ids]


def test_feedback_pairwise_math_and_thin_guard():
    from radar.eval.ranking import feedback_pairwise
    items = _disp("a", "b", "c", "d")                  # ranks a0 b1 c2 d3
    fb = {"a": {"vote": "up"}, "b": {"vote": "up"}, "d": {"vote": "down"}}
    r = feedback_pairwise(items, fb)
    assert r["n_up"] == 2 and r["n_down"] == 1 and r["n_pairs"] == 2
    assert r["correct_pairs"] == 2 and r["pairwise_accuracy"] == 1.0   # both 👍 above 👎
    assert r["is_signal"] is False and "样本太少" in r["note"]          # but n=2 → NOT a signal


def test_feedback_pairwise_incorrect_pair():
    from radar.eval.ranking import feedback_pairwise
    items = _disp("a", "b", "c")                        # a0 b1 c2
    fb = {"c": {"vote": "up"}, "a": {"vote": "down"}}   # 👍 c(rank2) is BELOW 👎 a(rank0)
    r = feedback_pairwise(items, fb)
    assert r["n_pairs"] == 1 and r["correct_pairs"] == 0 and r["pairwise_accuracy"] == 0.0


def test_feedback_zero_pairs():
    from radar.eval.ranking import feedback_pairwise
    r = feedback_pairwise(_disp("a", "b"), {"a": {"vote": "up"}})   # no 👎
    assert r["n_pairs"] == 0 and r["pairwise_accuracy"] is None
    assert r["is_signal"] is False and "暂无" in r["note"]


def test_kendall_tau():
    from radar.eval.ranking import _kendall_tau
    assert _kendall_tau(["a", "b", "c"], ["a", "b", "c"]) == (1.0, 1.0)     # identical
    assert _kendall_tau(["a", "b", "c"], ["c", "b", "a"]) == (-1.0, 0.0)    # reversed
    tau, agree = _kendall_tau(["a", "b", "c", "d"], ["a", "c", "b", "d"])   # one swap of 6 pairs
    assert tau == round(4 / 6, 3) and agree == round(5 / 6, 3)
    assert _kendall_tau(["a"], ["a"]) == (None, None)                       # <2 ids


def test_parse_order_robustness():
    from radar.eval.ranking import _parse_order
    assert _parse_order({"order": [2, 0, 1]}, "", 3) == [2, 0, 1]
    assert _parse_order([1, 0], "", 3) == [1, 0, 2]                 # dropped index appended
    assert _parse_order({"order": [{"i": 1}, {"i": 0}]}, "", 2) == [1, 0]   # dict items
    assert _parse_order({"order": [0, 0, 1]}, "", 2) == [0, 1]      # dedup
    assert _parse_order("garbage", "no json here", 2) is None


def test_independent_judge_tau(monkeypatch):
    from radar.eval.ranking import independent_judge
    items = [{"id": "a", "title": "A", "explain_zh": "x"},
             {"id": "b", "title": "B", "explain_zh": "y"},
             {"id": "c", "title": "C", "explain_zh": "z"}]
    # neutral order (sorted by id) = a,b,c. Judge agrees → tau 1.0
    r = independent_judge(items, llm=FakeJudge({"order": [0, 1, 2]}), model="m", system="s")
    assert r["kendall_tau"] == 1.0 and r["n"] == 3 and "稳定性" in r["note"]
    # judge reverses → tau -1.0
    r2 = independent_judge(items, llm=FakeJudge({"order": [2, 1, 0]}), model="m", system="s")
    assert r2["kendall_tau"] == -1.0


def test_independent_judge_bad_output_and_too_few():
    from radar.eval.ranking import independent_judge
    items = [{"id": "a", "title": "A"}, {"id": "b", "title": "B"}]
    assert "error" in independent_judge(items, llm=FakeJudge("timeout"), model="m", system="s")
    r = independent_judge([{"id": "a", "title": "A"}], llm=FakeJudge(), model="m", system="s")
    assert r["n"] == 1 and "条目太少" in r["note"]


def test_eval_ranking_no_llm_skips_judge():
    from radar.eval.ranking import eval_ranking
    r = eval_ranking(_disp("a", "b"), {"a": {"vote": "up"}}, llm=None)
    assert r["independent_judge"] is None and r["feedback"]["n_up"] == 1


# ================= Block ③ report + top-line + trend =================
def _eval_report(mean=0.9, n_scored=6, n_total=10, n_skip=4, n_issues=9,
                 fb_signal=False, fb_acc=1.0, fb_pairs=2, tau=-0.2, agree=0.4, jn=10):
    return {
        "schema_version": 1, "date": "2026-06-26", "n_items": n_total,
        "faithfulness": {
            "mean_support_rate": mean, "n_scored": n_scored, "n_total": n_total,
            "n_skipped": n_skip, "n_no_factual": 0, "n_issues": n_issues, "n_reused": 0,
            "skip_breakdown": {"no_explain": n_skip} if n_skip else {}, "rate_limited": False,
            "items": [
                {"status": "scored", "grounding_source": "full_text", "support_rate": 0.73,
                 "title": "Low one", "issues": [{"verdict": "unsupported",
                                                 "claim": "X 脑补的论断", "why": "原文没有"}]},
                {"status": "scored", "grounding_source": "full_text", "support_rate": 1.0,
                 "title": "High one", "issues": []},
                {"status": "skipped", "skip_reason": "no_explain", "title": "Skipped one"},
            ],
        },
        "ranking": {
            "feedback": {"is_signal": fb_signal, "pairwise_accuracy": fb_acc, "n_pairs": fb_pairs,
                         "correct_pairs": fb_pairs, "n_up": 2, "n_down": 1, "note": "n"},
            "independent_judge": ({"kendall_tau": tau, "pairwise_agreement": agree, "n": jn,
                                   "note": "诊断", "low_n_caveat": False}
                                  if tau is not None else None),
        },
    }


def test_top_line_holds_three_red_lines():
    from radar.eval.report import top_line
    tl = top_line(_eval_report())                       # mean .9, 6/10, skip 4, 9 issues, fb 2 pairs, tau -0.2
    # red line 1: coverage is mandatory
    assert "忠实度 90%" in tl and "基于 6/10 篇" in tl and "跳过 4 篇" in tl and "标记幻觉/失真 9 处" in tl
    # red line 2: thin feedback → NOT a bare percentage
    assert "样本太少不构成信号（2 对）" in tl and "100%" not in tl
    # red line 3: judge labelled a diagnostic
    assert "〔诊断〕" in tl and "τ=-0.2" in tl


def test_top_line_feedback_signal_branch():
    from radar.eval.report import top_line
    tl = top_line(_eval_report(fb_signal=True, fb_acc=0.83, fb_pairs=12))
    assert "排序-反馈 83%（12 对）" in tl and "样本太少" not in tl


def test_top_line_no_scored_items():
    from radar.eval.report import top_line
    tl = top_line(_eval_report(mean=None, n_scored=0, n_total=4, n_skip=4))
    assert "忠实度 —（无可评篇" in tl


def test_markdown_report_scannable_and_specific():
    from radar.eval.report import markdown, top_line
    rep = _eval_report()
    md = markdown("2026-06-26", rep)
    assert md.startswith("# Agent Radar eval — 2026-06-26")
    assert top_line(rep) in md                          # top-line embedded verbatim
    assert "## 忠实度" in md and "## 排序合理性" in md
    assert "X 脑补的论断" in md                          # the specific low-score claim is pointed out
    assert "schema v1" in md


def test_trend_skips_bad_and_old_schema(tmp_path, monkeypatch):
    import radar.eval.report as R
    from radar.core.io import atomic_write_json
    monkeypatch.setattr(R.Paths, "eval", tmp_path)
    atomic_write_json(tmp_path / "2026-06-26.json", _eval_report())                    # good
    atomic_write_json(tmp_path / "2026-06-20.json", {"schema_version": 999, "date": "x"})  # old schema
    (tmp_path / "2026-06-19.json").write_text("{ not valid json", encoding="utf-8")    # corrupt
    rows = R.trend_rows(1)
    assert len(rows) == 1 and rows[0]["date"] == "2026-06-26"
    assert rows[0]["faith"] == 0.9 and rows[0]["fb_signal"] is False


def test_trend_empty_is_graceful(tmp_path, monkeypatch):
    import radar.eval.report as R
    monkeypatch.setattr(R.Paths, "eval", tmp_path)
    assert R.print_trend(1) == 0
