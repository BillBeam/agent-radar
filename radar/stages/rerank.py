"""Rerank stage — listwise relative ranking of the finalists + diversity selection.

Breaks the "everything scores 9" clustering: instead of trusting absolute triage
scores, ask the model to ORDER the finalists best-first (a real gradient), then pick
the final max_items under a per-source diversity quota. The model's one-line
justification becomes each item's "why it's worth reading" in the digest. Degrades to
triage-score order if the LLM is absent/fails.
"""
from __future__ import annotations

from ..core.config import Paths
from ..core.models import Item, RunContext
from ..core.ports import Stage
from ..core.registry import register
from ..llm._json import salvage_objects


@register("stage", "rerank")
class RerankStage(Stage):
    name = "rerank"

    def run(self, ctx: RunContext) -> None:
        items = ctx.items
        if not items:
            return
        final_n = ctx.config.max_items(ctx.mode)

        if ctx.llm is not None and len(items) > 1:
            ranked = self._llm_rank(items, ctx)
        else:
            ranked = sorted(items, key=lambda it: (it.score or 0), reverse=True)

        selected = self._select_diverse(ranked, ctx, final_n)
        # rank-derived gradient → provably non-flat, and downstream score-sort = rank order
        n = len(selected)
        for pos, it in enumerate(selected):
            it.score = round(10.0 - pos * (9.0 / max(1, n)), 1)

        ctx.items = selected
        ctx.stats["reranked"] = len(items)
        ctx.log.info("rerank", finalists=len(items), selected=len(selected),
                     per_source_cap=ctx.config.max_per_source)

    def _llm_rank(self, items: list[Item], ctx: RunContext) -> list[Item]:
        system = Paths.prompts.joinpath("rerank.md").read_text(encoding="utf-8")
        lines = [
            f"[{i}] ({it.category}|{it.source_name}) {it.title} :: {(it.summary or '')[:160]}"
            for i, it in enumerate(items)
        ]
        user = ("Rank these candidates best-first per the rubric. Return ONLY the JSON array.\n\n"
                + "\n".join(lines))
        data, res = ctx.llm.complete_json(user, system=system, model=ctx.config.models.synthesize)
        if not isinstance(data, list) or not data:
            data = salvage_objects(res.text) if res.text else []
        if not isinstance(data, list) or not data:
            ctx.log.warn("rerank failed — falling back to triage score order", error=res.error)
            return sorted(items, key=lambda it: (it.score or 0), reverse=True)

        order: list[Item] = []
        seen: set[int] = set()
        for entry in data:
            if not isinstance(entry, dict) or "i" not in entry:
                continue
            try:
                idx = int(entry["i"])
            except (ValueError, TypeError):
                continue
            if idx in seen or not (0 <= idx < len(items)):
                continue
            seen.add(idx)
            it = items[idx]
            why = (entry.get("why") or "").strip()
            if why:
                it.reason = why
            order.append(it)
        # never lose items the model dropped — append by triage score
        for i, it in enumerate(items):
            if i not in seen:
                order.append(it)
        return order

    def _select_diverse(self, ranked: list[Item], ctx: RunContext, n: int) -> list[Item]:
        """Greedy top-down pick honoring a per-source cap; relax to fill if short."""
        cap = ctx.config.max_per_source
        out: list[Item] = []
        deferred: list[Item] = []
        per_source: dict[str, int] = {}
        for it in ranked:
            if len(out) >= n:
                break
            if per_source.get(it.source_id, 0) >= cap:
                deferred.append(it)
                continue
            per_source[it.source_id] = per_source.get(it.source_id, 0) + 1
            out.append(it)
        for it in deferred:  # quota left us short → relax, keep ranked order
            if len(out) >= n:
                break
            out.append(it)
        return out
