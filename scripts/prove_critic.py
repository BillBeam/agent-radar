"""Phase C self-prove: does critic flag low-signal garbage WITHOUT being fooled by titles?

Runs CriticStage on a real {date}.items.json PLUS crafted adversarial samples:
  - "survey/overview/understanding-titled but ACTUALLY frontier" (new benchmark / first
    measurements / new failure mode) → must NOT be flagged skip (judge content, not title);
  - genuine garbage (vendor PR / rehash primer / opinion thought-piece) → should be skip.
The 分寸 ("误标=最贵的错") lives or dies on the survey-but-frontier rows staying skip=false.
No USER.md involved → desensitized, safe as committed evidence.

    python scripts/prove_critic.py 2026-06-26
"""
from __future__ import annotations

import sys

from radar.core import registry
from radar.core.config import Paths, load_config
from radar.core.io import read_json
from radar.core.models import Item, RunContext, Source, SourceType, TimeWindow
from radar.core.runner import build_llm
from radar.obs import Logger, Tracer
from radar.stages.critic import CriticStage


def _adversarial() -> list[tuple[Item, str]]:
    """Crafted hard cases (title vs content). Returns (item, expectation)."""
    s = Source(id="adv", name="ADV", category="papers", type=SourceType.rss, url="http://adv", weight=1.0)
    specs = [
        ("A Survey of Long-Context Agent Memory Systems",
         "Beyond cataloguing methods, we run 14 memory systems on a NEW 50k-interaction benchmark and "
         "report a counter-intuitive result: vector-RAG memory underperforms a flat append-only log by "
         "18% on multi-session recall — a new failure mode we name 'retrieval drift', with ablations.",
         ["memory", "rag"], "应 KEEP（survey 标题，实为新基准 + 反直觉结果 + 新失败模式）"),
        ("Understanding Tool-Use Failures in LLM Agents",
         "We don't review failures — we instrument 8 agents and present the FIRST measurements showing "
         "tool-call error compounds super-linearly with chain length, introduce a new metric, and ablate.",
         ["tool-use", "eval"], "应 KEEP（understanding 标题，实为一手测量 + 新机制）"),
        ("Acme AI Launches AgentFlow 2.0 — The Future of Enterprise Agents",
         "Today we're thrilled to announce AgentFlow 2.0: 40% faster inference, enterprise SSO, and a new "
         "partner program. Available now — sign up for early access!",
         ["llmops"], "应 SKIP/high（纯厂商发布稿，无技术实质）"),
        ("The Ultimate Guide to RAG: Everything You Need to Know",
         "A comprehensive overview: what RAG is, the main retrieval methods (BM25, dense, hybrid), and "
         "best practices for chunking. A great primer to get started.",
         ["rag"], "应 SKIP（已知内容 rehash 入门，无新综合）"),
        ("Why 2026 Will Be the Year of Agents",
         "I believe agents will transform everything. Here are my thoughts on where the industry is heading "
         "and why every company needs an agent strategy.",
         ["llmops"], "应 SKIP（空泛观点，无数据无方法）"),
    ]
    out = []
    for title, summary, tags, expect in specs:
        it = Item.create(source=s, title=title, url="http://adv/" + title[:24].replace(" ", "-"))
        it.summary = summary
        it.tags = tags
        out.append((it, expect))
    return out


def main(argv) -> int:
    date = argv[1] if len(argv) > 1 else "2026-06-26"
    registry.load_adapters()
    config = load_config()
    log = Logger("prove-critic", echo=True)
    trace = Tracer("prove-critic")

    raw = read_json(Paths.digests / f"{date}.items.json") or []
    real = [Item(**d) for d in raw]
    adv = _adversarial()
    expect = {it.id: e for it, e in adv}
    items = real + [it for it, _ in adv]

    llm = build_llm(config, log, trace)
    if llm is None:
        print("no LLM (claude -p) available")
        return 1
    ctx = RunContext(run_id="prove-critic", mode="daily", config=config, window=TimeWindow(48))
    ctx.log, ctx.trace, ctx.llm = log, trace, llm
    ctx.items = items

    print(f"== critic · {date} 真实 {len(real)} 条 + 对抗 {len(adv)} 条 ==\n")
    CriticStage().run(ctx)
    verdicts = ctx.stats.get("critic", {})

    print("\n--- 真实批 ---")
    _dump(real, verdicts, expect)
    print("\n--- 对抗样本（标题 vs 内容；分寸最难一关）---")
    _dump([it for it, _ in adv], verdicts, expect)

    s = ctx.stats.get("critic_summary", {})
    by = (getattr(llm, "by_stage", {}) or {}).get("critic", {})
    print(f"\n小结: judged={s.get('judged')} skip={s.get('skip')} high_conf_skip={s.get('high_conf_skip')}"
          f" · critic 调用 {by.get('calls')} 次 / {by.get('ms')}ms / in {by.get('input')} out {by.get('output')} tok")
    log.close()
    return 0


def _dump(items, verdicts, expect):
    for it in items:
        v = verdicts.get(it.id, {})
        mark = "🚫SKIP" if v.get("skip") else "✅KEEP"
        why = v.get("why") or ""
        print(f"{mark} [{v.get('conf','?'):>4}] {it.title[:56]}")
        if why:
            print(f"         理由: {why}")
        if it.id in expect:
            print(f"         {expect[it.id]}")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
