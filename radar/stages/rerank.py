"""Rerank stage — listwise relative ranking of the finalists + diversity selection.

Breaks the "everything scores 9" clustering: instead of trusting absolute triage
scores, ask the model to ORDER the finalists best-first (a real gradient), then pick
the final max_items under a per-source diversity quota. The model's one-line
justification becomes each item's "why it's worth reading" in the digest. Degrades to
triage-score order if the LLM is absent/fails.
"""
from __future__ import annotations

from pathlib import Path

from ..core.config import Paths
from ..core.models import Item, RunContext
from ..core.ports import Stage
from ..core.registry import register
from ..llm._json import salvage_objects


def load_known_topics(path) -> str:
    """Body of the first `## ` section whose heading contains '已会' (up to the next `## `).
    Returns '' if the file is absent/unreadable or has no such section — a missing or
    hand-edited USER.md must never break rerank (it just falls back to domain novelty).
    Dumb + robust on purpose (no YAML / markdown parser)."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, ValueError):
        return ""
    out: list[str] = []
    capturing = False
    for ln in text.splitlines():
        if ln.lstrip().startswith("## "):
            if capturing:
                break
            capturing = "已会" in ln
            continue
        if capturing:
            out.append(ln)
    return "\n".join(out).strip()


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
        mem = getattr(ctx.config, "memory", None)
        known = load_known_topics(Paths.user_md) if (mem and mem.personalize_rerank) else ""

        if known:                                    # personalize: enrich lines + add preamble
            recent_days = mem.recent_days
            lines = [self._candidate_line(i, it, ctx, recent_days)
                     for i, it in enumerate(items)]
            preamble = (
                "读者（资深 agent/harness 工程师）已掌握下列主题——对这些主题的**科普 / 综述 / "
                "入门 / overview / best-practices 回顾**大幅降权（他早已懂）；但这些主题里的**全新"
                "实证结果 / 反直觉发现 / 新失败模式 / SOTA 突破**仍属“对他新”，照常上浮，切勿因"
                "命中已会领域就一刀切误杀他的主场。\n已会主题：\n" + known + "\n\n"
            )
        else:                                        # toggle off / no USER.md → baseline (byte-identical)
            lines = [
                f"[{i}] ({it.category}|{it.source_name}) {it.title} :: {(it.summary or '')[:160]}"
                for i, it in enumerate(items)
            ]
            preamble = ""

        user = (preamble
                + "Rank these candidates best-first per the rubric. Return ONLY the JSON array.\n\n"
                + "\n".join(lines))
        # listwise ranking of ≤24 finalists legitimately runs 3-4 min (succeeded at 181s and
        # 227s); the 240s default timed out 3× on 2026-07-03 and silently degraded the whole
        # day's ordering to triage-score order — give it real headroom.
        data, res = ctx.llm.complete_json(user, system=system, model=ctx.config.models.synthesize,
                                          timeout=480, tag=self.name)
        if not isinstance(data, list) or not data:
            data = salvage_objects(res.text) if res.text else []
        if not isinstance(data, list) or not data:
            ctx.log.warn("rerank failed — falling back to triage score order", error=res.error)
            # surfaced in the digest header — a degraded ordering must never look like a
            # confident personalized ranking (the rank→gradient scores would fake it)
            ctx.stats["rerank_degraded"] = True
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

    def _candidate_line(self, i: int, it: Item, ctx: RunContext, recent_days: int) -> str:
        line = f"[{i}] ({it.category}|{it.source_name}) {it.title} :: {(it.summary or '')[:160]}"
        if it.tags:
            line += f"  〔标签 {' · '.join(it.tags)}〕"
        marker = self._topic_marker(it, ctx, recent_days)
        return line + (f"  {marker}" if marker else "")

    def _topic_marker(self, it: Item, ctx: RunContext, recent_days: int) -> str:
        """'近 N 天同主题×K' if memory has K earlier same-topic pushes; '' otherwise / on error."""
        if ctx.memory is None:
            return ""
        try:
            hist = ctx.memory.topic_history(it, recent_days)
            count = int(hist.get("count", 0)) if isinstance(hist, dict) else 0
        except Exception:  # noqa: BLE001 — memory is best-effort, never break ranking
            return ""
        return f"⟨近{recent_days}天同主题×{count}⟩" if count else ""

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
