"""Phase B self-prove: does injecting USER.md 已会清单 down-weight 已会-topic 科普 in rerank?

Runs RerankStage TWICE on a real {date}.items.json — side A (personalize OFF = today's
behavior) vs side B (personalize ON) — and prints the rank delta, so you can SEE the
已会 科普 rows sink while the 已会-domain frontier holds. Then a guardrail: ONE
transferable-value judge pass + dual Kendall τ. A τ DROP in B is EXPECTED (personalization
departs from the domain-value axis); a COLLAPSE would mean the order got scrambled / the
frontier got killed. Lead with the per-item delta; treat Δτ as a band; LLM sampling adds
noise, so eyeball the labeled rows (or re-run).

    python scripts/prove_rerank_personalization.py 2026-06-29 [--highlight <id> ...]

Needs: a filled USER.md (gitignored) with a 已会清单; a {date}.items.json; subscription
(no ANTHROPIC_API_KEY). No DingTalk creds needed (nothing is delivered).
"""
from __future__ import annotations

import copy
import sys

from radar.core import registry
from radar.core.config import Paths, load_config
from radar.core.io import read_json
from radar.core.models import Item, RunContext, TimeWindow
from radar.core.runner import build_llm, build_memory
from radar.obs import Logger, Tracer
from radar.stages.rerank import RerankStage, load_known_topics


def _run_side(items, ctx, *, personalize: bool):
    ctx.config.memory.personalize_rerank = personalize
    ctx.items = [copy.deepcopy(it) for it in items]   # rerank mutates score/reason
    RerankStage().run(ctx)
    return list(ctx.items)


def _judge_order(items, llm, config, log):
    """One transferable-value judge pass → best-first list of ids (the guardrail order)."""
    from radar.eval.ranking import _item_brief, _parse_order
    dicts = [it.model_dump(mode="json") for it in items if it.id and it.title]
    if len(dicts) < 2:
        return None
    neutral = sorted(dicts, key=lambda d: d.get("id"))      # break position bias
    listing = "\n".join(_item_brief(d, i) for i, d in enumerate(neutral))
    system = Paths.prompts.joinpath("eval_rank.md").read_text(encoding="utf-8")
    parsed, res = llm.complete_json(f"条目（顺序已打乱，不代表排名）：\n\n{listing}",
                                    system=system, model=config.models.judge, timeout=180, retries=1)
    order = _parse_order(parsed, getattr(res, "text", "") or "", len(neutral))
    if order is None:
        log.warn("judge guardrail unparseable — skipping Δτ")
        return None
    return [neutral[i].get("id") for i in order]


def main(argv) -> int:
    date = next((a for a in argv[1:] if not a.startswith("-")), "2026-06-29")
    highlight = set(argv[argv.index("--highlight") + 1:]) if "--highlight" in argv else set()

    registry.load_adapters()
    config = load_config()
    log = Logger("prove-rerank", echo=True)
    Tracer("prove-rerank")

    raw = read_json(Paths.digests / f"{date}.items.json")
    if not raw:
        print(f"no items for {date} (data/digests/{date}.items.json) — run a daily first")
        return 1
    items = [Item(**it) for it in raw]

    known = load_known_topics(Paths.user_md)
    if not known:
        print(f"⚠ USER.md 无 已会清单 ({Paths.user_md}) — B 侧会等同 A 侧。先填 USER.md。")
        return 1

    llm = build_llm(config, log)
    memory = build_memory(config, log)
    if llm is None:
        print("no LLM (claude -p) available")
        return 1
    print(f"== A/B rerank · {date} · {len(items)} 条 · 已会清单已载入 · 同主题记忆={'有' if memory else '无'} ==\n")

    def _ctx():
        c = RunContext(run_id="prove", mode="daily", config=config, window=TimeWindow(48))
        c.log = log
        c.trace = Tracer("prove")
        c.llm, c.memory = llm, memory
        return c

    a = _run_side(items, _ctx(), personalize=False)
    b = _run_side(items, _ctx(), personalize=True)
    rank_a = {it.id: i for i, it in enumerate(a)}
    rank_b = {it.id: i for i, it in enumerate(b)}
    why_b = {it.id: (it.reason or "") for it in b}

    print(f"{'A→B':>6} {'Δ':>4}  〔标签〕标题 | why_B")
    print("-" * 92)
    for it in a:                                  # walk A's order
        ra, rb = rank_a[it.id], rank_b.get(it.id)
        d = (rb - ra) if rb is not None else None
        hint = "  ←已会科普? 沉↓" if (d and d > 0) else ("  ←浮↑" if (d and d < 0) else "")
        flag = "★" if it.id in highlight else " "
        dd = f"{d:+d}" if d is not None else "  ·"
        tags = ",".join((it.tags or [])[:3])
        print(f"{flag}{ra:>2}→{(rb if rb is not None else '·'):>2} {dd:>4}  〔{tags}〕{(it.title or '')[:46]} | {why_b.get(it.id,'')[:22]}{hint}")

    from radar.eval.ranking import _kendall_tau
    judge_ids = _judge_order(items, llm, config, log)
    if judge_ids:
        ta, _ = _kendall_tau([it.id for it in a], judge_ids)
        tb, _ = _kendall_tau([it.id for it in b], judge_ids)
        print("\n护栏（transferable-value judge；τ 掉是预期、非『更差』；崩塌才是打乱/误杀）:")
        print(f"  τ(A,judge)={ta}   τ(B,judge)={tb}   Δτ={round((tb or 0) - (ta or 0), 3)}")
    log.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
