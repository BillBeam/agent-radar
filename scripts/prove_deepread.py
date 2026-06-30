"""Phase C self-prove: deepread depth-consistency (4 axes) + item-level checkpoint + trace.

Re-deep-reads a real {date}.items.json with the refined prompt (which auto-busts the old
checkpoint via prompt_fp), prints one 详解 so you can see ① 核心机制 ② 证据/数据 ③ 局限/
失败模式 ④ 新在哪 all covered (thin sources honestly marked, not 注水), then RE-RUNS to
show the checkpoint reuses completed items (0 new LLM calls). Prints the per-stage trace.

    python scripts/prove_deepread.py 2026-06-26
"""
from __future__ import annotations

import sys

from radar.core import registry
from radar.core.config import Paths, load_config
from radar.core.io import read_json
from radar.core.models import Item, RunContext, TimeWindow
from radar.core.runner import build_llm
from radar.obs import Logger, Tracer
from radar.stages.deepread import DeepReadStage, NO_TEXT


def _ctx(config, llm, trace, log):
    c = RunContext(run_id="prove-deepread", mode="daily", config=config, window=TimeWindow(48))
    c.log, c.trace, c.llm = log, trace, llm
    return c


def main(argv) -> int:
    date = argv[1] if len(argv) > 1 else "2026-06-26"
    registry.load_adapters()
    config = load_config()
    config.deepread_top_k = min(config.deepread_top_k, 3)   # 3 篇够看深度，省钱
    log = Logger("prove-deepread", echo=True)
    trace = Tracer("prove-deepread")

    raw = read_json(Paths.digests / f"{date}.items.json") or []
    if not raw:
        print(f"no items for {date}")
        return 1
    items = [Item(**d) for d in raw]
    for it in items:
        it.explain_zh = None       # clear stale 详解 → re-read with the new prompt

    llm = build_llm(config, log, trace)
    if llm is None:
        print("no LLM (claude -p) available")
        return 1

    print(f"== deepread 深度一致 + checkpoint · {date} · top {config.deepread_top_k} ==\n")
    ctx = _ctx(config, llm, trace, log)
    ctx.items = [it.model_copy() for it in items]
    DeepReadStage().run(ctx)
    done = [it for it in ctx.items[: config.deepread_top_k]
            if it.explain_zh and it.explain_zh != NO_TEXT]
    print(f"\n深读完成 {len(done)} 篇。样例详解（验 ①机制 ②证据/数据 ③局限/失败 ④新在哪 是否齐全）:\n")
    for it in done[:1]:
        print(f"【{it.title}】\n{it.explain_zh}\n" + "-" * 70)

    print("\n--- 复跑（验 checkpoint 跳过已完成项）---")
    llm2 = build_llm(config, log, trace)
    ctx2 = _ctx(config, llm2, trace, log)
    ctx2.started_at = ctx.started_at       # same date → same checkpoint file
    ctx2.items = [it.model_copy() for it in items]
    DeepReadStage().run(ctx2)
    new_calls = (getattr(llm2, "by_stage", {}).get("deepread") or {}).get("calls", 0)
    print(f"复跑: resumed={ctx2.stats.get('deepread.resumed', 0)} · deepread 新 LLM 调用 {new_calls} 次（应为 0）")

    print(f"\nper-stage trace 汇总（首跑，每调用 token+延迟）:")
    for tag, s in (getattr(llm, "by_stage", {}) or {}).items():
        print(f"  {tag:>10}: {s['calls']} 调用 · {s['ms']}ms · in {s['input']} / out {s['output']} tok · {s.get('model')}")
    log.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
