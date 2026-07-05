"""Probe: does the rerank.md "新 benchmark / 新测量方法" exemption work — without over-exempting?

Read-only replay on a cached {date}.items.json (default 2026-07-03), P0-clean context.
Measures the RAW `_llm_rank` order (the prompt change only touches that layer;
`_select_diverse` is an orthogonal, unit-tested mechanical layer).

Arms (all sequential, sonnet listwise):
  new-pers-A/B ×2 reps  — new prompt, personalized, two neutral input orders (id asc/desc)
                          → prove [5] MemSyco / [6] EvoPolicyGym float stably into the top
  new-guard-A/B         — same + ONE synthetic mediocre twin ("unify existing benchmarks,
                          re-evaluate on a larger dataset, leaderboard confirms prior
                          rankings" — exactly the guardrail's 不豁免 cases, same
                          memory+eval tags as [5]) → prove it is NOT lifted
  old-guard-A           — OLD prompt (git HEAD) on the guard pool → prove the exemption
                          didn't improve the mediocre twin's rank
  new-base-A            — personalize OFF → prove the exemption didn't leak into baseline

    python scripts/probe_rerank_benchmark_exemption.py [2026-07-03] [--out results.json]

Needs: filled USER.md, subscription claude -p. Writes nothing but the --out file.
"""
from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from radar.core import registry
from radar.core.config import Paths, load_config
from radar.core.io import read_json
from radar.core.models import Item, RunContext, TimeWindow
from radar.core.runner import build_llm, build_memory
from radar.obs import Logger, Tracer
from radar.stages.rerank import RerankStage, load_known_topics

MEDIOCRE = {
    "id": "00probe0mediocre",
    "source_id": "arxiv-probe",          # unique source → diversity cap can never touch it
    "source_name": "arXiv (agent/LLM, recency)",
    "category": "papers",
    "title": "AgentMemBench-XL: A Unified Leaderboard for LLM Agent Memory Evaluation",
    "summary": (
        "We unify six existing agent-memory benchmarks into one expanded suite and "
        "re-evaluate 23 popular LLM agents on a larger combined dataset. Our updated "
        "leaderboard largely confirms prior rankings; we release all prompts and code."
    ),
    "tags": ["paper", "memory", "eval"],  # same surface as [5] MemSyco — the twin test
    "url": "https://arxiv.org/abs/2607.99999",
    "published_at": "2026-07-02T12:00:00Z",
    "score": 7.0,
    "weight": 1.1,
}

TRACKED = {                               # 07-03 display [N] → item id prefix
    "[5] MemSyco-Bench(记忆谄媚)": "fd0309dc034bc6b2",
    "[6] EvoPolicyGym(策略演化eval)": "4f017ca43c728975",
    "[8] Demystifying-evals(科普)": "414ce4b52e368291",
    "[9] Effective-harnesses(实践)": "c2414e3a01987302",
    "[G] AgentMemBench-XL(平庸孪生)": "00probe0mediocre",
}


def _old_prompt_dir() -> Path:
    """prompts/ dir holding the OLD (git HEAD) rerank.md, in a temp location."""
    old = subprocess.run(["git", "show", "HEAD:prompts/rerank.md"],
                         capture_output=True, text=True, check=True).stdout
    d = Path(tempfile.mkdtemp(prefix="probe-old-prompts-"))
    (d / "rerank.md").write_text(old, encoding="utf-8")
    return d


def _rank_once(name, pool, ctx_factory, *, personalize, prompts_dir=None):
    """One raw _llm_rank pass; returns {'order': [ids best-first], 'why': {id: why}} or error."""
    ctx = ctx_factory()
    ctx.config.memory.personalize_rerank = personalize
    items = [copy.deepcopy(it) for it in pool]
    saved = Paths.prompts
    try:
        if prompts_dir is not None:
            Paths.prompts = prompts_dir
        ranked = RerankStage()._llm_rank(items, ctx)
    finally:
        Paths.prompts = saved
    if ctx.stats.get("rerank_degraded") or len(ranked) != len(items):
        return {"error": f"degraded or lossy (got {len(ranked)}/{len(items)})"}
    return {"order": [it.id for it in ranked],
            "why": {it.id: (it.reason or "") for it in ranked}}


def main(argv) -> int:
    date = next((a for a in argv[1:] if not a.startswith("-")), "2026-07-03")
    out_path = Path(argv[argv.index("--out") + 1]) if "--out" in argv else None

    registry.load_adapters()
    config = load_config()
    log = Logger("probe-exemption", echo=True)

    raw = read_json(Paths.digests / f"{date}.items.json")
    if not raw:
        print(f"no items for {date}")
        return 1
    base_pool = [Item(**it) for it in raw]
    if not load_known_topics(Paths.user_md):
        print("USER.md 无已会清单 — probe pointless")
        return 1

    llm = build_llm(config, log)
    memory = build_memory(config, log)
    if llm is None:
        print("no LLM available")
        return 1

    def _ctx():
        c = RunContext(run_id="probe", mode="daily", config=config, window=TimeWindow(48))
        c.log = log
        c.trace = Tracer("probe")
        c.llm, c.memory = llm, memory
        return c

    asc = sorted(base_pool, key=lambda it: it.id)
    desc = list(reversed(asc))
    guard = [Item(**MEDIOCRE)] + base_pool
    guard_asc = sorted(guard, key=lambda it: it.id)
    guard_desc = list(reversed(guard_asc))
    old_dir = _old_prompt_dir()

    # transparency: the exact candidate lines (incl. memory markers) the LLM will see
    stage, ctx0 = RerankStage(), _ctx()
    print("== guard 池候选行（asc 序，含记忆标记）==")
    for i, it in enumerate(guard_asc):
        print(" ", stage._candidate_line(i, it, ctx0, config.memory.recent_days))

    runs = [
        ("new-pers-A#1", asc, True, None), ("new-pers-A#2", asc, True, None),
        ("new-pers-B#1", desc, True, None), ("new-pers-B#2", desc, True, None),
        ("new-guard-A", guard_asc, True, None), ("new-guard-B", guard_desc, True, None),
        ("old-guard-A", guard_asc, True, old_dir),
        ("new-base-A", asc, False, None),
    ]
    results = {}
    for name, pool, pers, pdir in runs:
        print(f"RUN {name} start ({len(pool)} items, personalize={pers}, "
              f"prompt={'OLD' if pdir else 'NEW'})", flush=True)
        t0 = time.monotonic()
        r = _rank_once(name, pool, _ctx, personalize=pers, prompts_dir=pdir)
        if "error" in r:                  # sonnet is flaky today (transient exit-1) — one retry
            print(f"RUN {name} attempt1 failed ({r['error']}) — retrying once", flush=True)
            r = _rank_once(name, pool, _ctx, personalize=pers, prompts_dir=pdir)
        r["secs"] = round(time.monotonic() - t0)
        results[name] = r
        if "error" in r:
            print(f"RUN {name} ERROR: {r['error']}", flush=True)
            continue
        pos = {label: (r["order"].index(iid) + 1 if iid in r["order"] else None)
               for label, iid in TRACKED.items()}
        brief = "  ".join(f"{lab.split(' ')[0]}#{p}" for lab, p in pos.items() if p)
        print(f"RUN {name} done → {brief}", flush=True)

    print("\n== 汇总（rank = 原始 LLM 序，1=最佳）==")
    header = f"{'run':<14}" + "".join(f"{lab.split(' ')[0]:>7}" for lab in TRACKED)
    print(header)
    for name, r in results.items():
        if "error" in r:
            print(f"{name:<14}  ERROR: {r['error']}")
            continue
        row = f"{name:<14}"
        for lab, iid in TRACKED.items():
            p = r["order"].index(iid) + 1 if iid in r["order"] else None
            row += f"{('#' + str(p)) if p else '—':>7}"
        print(row)

    print("\n== why（追踪条目，逐 run）==")
    for name, r in results.items():
        if "error" in r:
            continue
        for lab, iid in TRACKED.items():
            w = r.get("why", {}).get(iid, "")
            if w:
                print(f"  {name} {lab.split(' ')[0]}: {w}")

    if out_path:
        out_path.write_text(json.dumps(
            {"date": date, "tracked": TRACKED, "results": results},
            ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nsaved → {out_path}")
    log.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
