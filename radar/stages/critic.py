"""Critic stage — the "is there real substance?" axis (signal density).

Orthogonal to rerank's "对他新" novelty: rerank orders by importance × novelty-to-him;
critic flags items that LOOK important but are low-signal (vendor PR, rehash surveys,
clickbait, no-data thought-pieces, second-hand). Annotation-ONLY + safe:
- runs on the **≤10 selected finalists** (`ctx.items`, post-rerank) — NOT the triage pool.
- verdicts land on `ctx.stats["critic"]` (Item is frozen; `tags` would corrupt the memory
  signal; `reason` is the brief why).
- V5: verdicts surface as the honest ⚠️可跳过 label in the brief + reading page, and
  NOTHING else — they no longer gate deepread (每一篇都要详解, user decision). Never
  silently cut — he's the expert and reads with the label in view.
Degrades to a no-op if the LLM is absent/fails (no annotation, deepread unchanged).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from ..core.config import Paths
from ..core.io import atomic_write_json
from ..core.models import Item, RunContext
from ..core.ports import Stage
from ..core.registry import register
from ..llm._json import salvage_objects

_VALID_CONF = {"high", "low"}
_NEUTRAL = {"skip": False, "conf": "low", "why": ""}


def critic_verdict(ctx: RunContext, item: Item) -> dict:
    """The critic verdict for an item, or a neutral default. Shared by deepread + synthesize
    so there's one definition of 'what did critic say about this item'."""
    return (ctx.stats.get("critic") or {}).get(item.id, _NEUTRAL)


@register("stage", "critic")
class CriticStage(Stage):
    name = "critic"
    critical = False

    def run(self, ctx: RunContext) -> None:
        items = ctx.items
        if ctx.llm is None or not items:
            return
        system = Paths.prompts.joinpath("critic.md").read_text(encoding="utf-8")
        lines = [
            f"[{i}] ({it.category}|{it.source_name}) 〔{' · '.join(it.tags or [])}〕 "
            f"{it.title} :: {(it.summary or '')[:160]}"
            for i, it in enumerate(items)
        ]
        user = ("判断下列每条候选有没有真料（有真东西可得吗）。只返回 JSON 数组。\n\n"
                + "\n".join(lines))
        data, res = ctx.llm.complete_json(user, system=system, model=ctx.config.models.critic, tag=self.name)
        if not isinstance(data, list) or not data:
            data = salvage_objects(res.text) if res.text else []
        if not isinstance(data, list) or not data:
            ctx.log.warn("critic failed — no verdicts (deepread/brief unaffected)",
                         error=getattr(res, "error", None))
            return

        verdicts: dict[str, dict] = {}
        for entry in data:
            if not isinstance(entry, dict) or "i" not in entry:
                continue
            try:
                idx = int(entry["i"])
            except (ValueError, TypeError):
                continue
            if not (0 <= idx < len(items)):
                continue
            skip = bool(entry.get("skip"))
            conf = entry.get("conf") if entry.get("conf") in _VALID_CONF else "low"
            why = (entry.get("why") or "").strip() if skip else ""
            verdicts[items[idx].id] = {"skip": skip, "conf": conf, "why": why}

        ctx.stats["critic"] = verdicts
        n_skip = sum(1 for v in verdicts.values() if v["skip"])
        n_high = sum(1 for v in verdicts.values() if v["skip"] and v["conf"] == "high")
        ctx.stats["critic_summary"] = {"judged": len(verdicts), "skip": n_skip, "high_conf_skip": n_high}
        self._write_sidecar(ctx, items, verdicts)
        ctx.log.info("critic", judged=len(verdicts), skip=n_skip, high_conf_skip=n_high)

    def _write_sidecar(self, ctx: RunContext, items: list[Item], verdicts: dict) -> None:
        """Durable record of the verdicts (for self-prove / re-render). Best-effort."""
        try:
            date = ctx.started_at.astimezone().strftime("%Y-%m-%d")
            rows = [{"id": it.id, "title": it.title, "tags": it.tags,
                     **verdicts.get(it.id, _NEUTRAL)} for it in items]
            atomic_write_json(
                Paths.critic / f"{date}.json",
                {"date": date, "n": len(items), "items": rows,
                 "ts": datetime.now().astimezone().isoformat(timespec="seconds")},
            )
        except Exception as e:  # noqa: BLE001 — a sidecar write must not break the run
            ctx.log.warn("critic sidecar write failed", error=repr(e)[:120])
