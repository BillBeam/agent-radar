"""Triage stage — pointwise LLM relevance scoring of the candidate pool.

One batched `claude -p` call (cheap model) scores every candidate 0–10 against the
topic rubric, tags it, flags self-applicable items. Pointwise (not pairwise) is more
stable; only title+source+summary is sent (token-cheap). Degrades to a weight
heuristic if the LLM call fails, so the run still produces something.
"""
from __future__ import annotations

from datetime import timezone

import yaml

from ..core.config import Paths
from ..core.models import Item, RunContext
from ..core.ports import Stage
from ..core.registry import register
from ..llm._json import salvage_objects


def _recency_key(it: Item) -> float:
    if it.published_at is None:
        return 0.0  # undated → neutral
    dt = it.published_at
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


@register("stage", "triage")
class TriageStage(Stage):
    name = "triage"

    def run(self, ctx: RunContext) -> None:
        # Score the WHOLE pool — never pre-cut by source weight (a low-weight
        # community source's gem must still reach triage/rerank). Only a
        # recency-based safety trim if the pool is enormous.
        cap = ctx.config.triage_pool_cap
        if len(ctx.candidates) > cap:
            pool = sorted(ctx.candidates, key=_recency_key, reverse=True)[:cap]
            ctx.log.warn("triage pool exceeded cap — trimmed by recency (not weight)",
                         n=len(ctx.candidates), cap=cap)
        else:
            pool = list(ctx.candidates)
        if not pool:
            ctx.items = []
            return

        if ctx.llm is None:
            self._fallback(pool, ctx, "no llm")
            return

        tax = yaml.safe_load(Paths.taxonomy_yaml.read_text(encoding="utf-8")) or {}
        topics = ", ".join(tax.get("topics", []))
        components = ", ".join(tax.get("self_components", []))
        system = Paths.prompts.joinpath("triage.md").read_text(encoding="utf-8")

        lines = [
            f"[{i}] ({it.category}|{it.source_name}) {it.title} :: {(it.summary or '')[:160]}"
            for i, it in enumerate(pool)
        ]
        user = (
            f"TOPIC TAXONOMY (use exact strings): {topics}\n"
            f"SELF_COMPONENTS: {components}\n\n"
            f"Score these {len(pool)} candidates per the rubric. Return ONLY the JSON array.\n\n"
            + "\n".join(lines)
        )

        data, res = ctx.llm.complete_json(user, system=system, model=ctx.config.models.triage, tag=self.name)
        if not isinstance(data, list) or not data:
            # one bad element shouldn't nuke the batch — salvage flat objects
            salvaged = salvage_objects(res.text) if res.text else []
            if salvaged:
                data = salvaged
                ctx.log.warn("triage json malformed — salvaged objects", got=len(salvaged))
            else:
                self._fallback(pool, ctx, res.error or "bad triage output")
                return

        by_index = {int(r["i"]): r for r in data if isinstance(r, dict) and "i" in r}
        scored = 0
        unscored = 0
        for i, it in enumerate(pool):
            r = by_index.get(i)
            if not r:
                # NOT a silent 0: uncovered items go through the heuristic + a marker
                it.score = self._heuristic_score(it)
                it.reason = "（未被分诊覆盖 · 启发式兜底）"
                unscored += 1
                continue
            it.score = float(r.get("score", 0))
            it.reason = r.get("reason")
            for t in r.get("tags", []) or []:
                if t not in it.tags:
                    it.tags.append(t)
            it.self_applicable = bool(r.get("self_applicable"))
            it.target_component = r.get("target_component")
            scored += 1

        ctx.items = pool
        coverage = scored / len(pool) if pool else 1.0
        ctx.stats["triage_coverage"] = round(coverage, 2)
        ctx.stats["triage_unscored"] = unscored
        ctx.bump("triaged", scored)
        if coverage < 0.8:
            ctx.log.warn("triage low coverage — heuristic-filled the rest",
                         coverage=round(coverage, 2), unscored=unscored, pool=len(pool))
        sa = sum(1 for it in pool if it.self_applicable)
        ctx.log.info("triage", pool=len(pool), scored=scored, unscored=unscored,
                     self_applicable=sa, model=ctx.config.models.triage)

    @staticmethod
    def _heuristic_score(it: Item) -> float:
        return round(6.0 * it.weight, 1)  # weight 1.0→6.0, 1.4→8.4, 0.9→5.4

    def _fallback(self, pool: list[Item], ctx: RunContext, why: str) -> None:
        """Whole-batch heuristic so the run still yields high-signal items."""
        for it in pool:
            it.score = self._heuristic_score(it)
            it.reason = "（降级：未经 LLM 分诊）"
        ctx.items = pool
        ctx.stats["triage_degraded"] = True
        ctx.stats["triage_coverage"] = 0.0
        ctx.log.warn("triage degraded → weight heuristic", reason=why, pool=len(pool))
