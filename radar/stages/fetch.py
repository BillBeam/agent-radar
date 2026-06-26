"""Fetch stage — deterministic, no LLM. The reliable spine of every run.

Per-source failures are isolated (one dead feed never kills the run). The raw
candidate pool is always written to disk first, so a later LLM failure can be
recovered / re-run offline.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from ..core import registry
from ..core.config import Paths
from ..core.io import atomic_write_json, read_json
from ..core.models import Item, RunContext, TimeWindow
from ..core.ports import Stage
from ..core.registry import register
from ..sources import load_sources


@register("stage", "fetch")
class FetchStage(Stage):
    name = "fetch"
    critical = False

    def run(self, ctx: RunContext) -> None:
        sources = load_sources()
        ctx.sources = sources
        seen = set(read_json(Paths.seen_json, {}).keys())
        first_seen: dict[str, str] = read_json(Paths.first_seen_json, {}) or {}
        today = ctx.started_at.astimezone().strftime("%Y-%m-%d")
        max_undated = ctx.config.max_undated_per_source

        adapters: dict[str, object] = {}
        pool: dict[str, Item] = {}
        per_source: dict[str, int] = {}

        for s in sources:
            try:
                adapter = adapters.get(s.type.value)
                if adapter is None:
                    adapter = registry.get("source", s.type.value)(config=ctx.config, log=ctx.log)
                    adapters[s.type.value] = adapter
                # per-source recency override (e.g. papers lag → wider leash)
                eff = TimeWindow(float(s.params.get("max_age_hours", ctx.window.hours)))
                items = adapter.fetch(s, eff)
            except Exception as e:  # noqa: BLE001 — circuit-break this source only
                ctx.log.warn("source failed", source=s.id, error=repr(e))
                ctx.bump("source_errors")
                per_source[s.id] = -1
                continue

            kept = 0
            undated_kept = 0
            for it in items:
                if it.id in seen:
                    ctx.bump("skipped_seen")
                    continue
                if it.published_at is None:
                    # bounded history: a dateless source (e.g. a blog index page) must
                    # not dump its whole back-catalog as "today".
                    if undated_kept >= max_undated:
                        continue
                    undated_kept += 1
                first_seen.setdefault(it.id, today)  # remember when we first saw it
                existing = pool.get(it.id)
                if existing is None or it.weight > existing.weight:
                    pool[it.id] = it
                kept += 1
            per_source[s.id] = kept

        ctx.candidates = list(pool.values())
        ctx.bump("candidates", len(ctx.candidates))
        ctx.stats["per_source"] = per_source
        # prune first_seen to ~120 days, then persist
        cutoff = (datetime.now() - timedelta(days=120)).strftime("%Y-%m-%d")
        first_seen = {k: v for k, v in first_seen.items() if v >= cutoff}
        atomic_write_json(Paths.first_seen_json, first_seen)

        date = ctx.started_at.astimezone().strftime("%Y-%m-%d")
        atomic_write_json(
            Paths.candidates / f"{date}.json",
            [it.model_dump(mode="json") for it in ctx.candidates],
        )
        live = sum(1 for v in per_source.values() if v >= 0)
        failed = [sid for sid, v in per_source.items() if v < 0]
        ctx.stats["fetch_health"] = {"live": live, "total": len(sources), "failed": failed}
        if sources and live == 0:
            # all sources down ≠ "no news" — surface it loudly, don't go quietly empty
            ctx.errors.append(f"ALL {len(sources)} sources failed to fetch "
                              f"— network/proxy likely down")
            ctx.log.error("ALL sources failed", total=len(sources))
        elif failed:
            ctx.log.warn("some sources failed", live=live, total=len(sources), failed=failed[:8])
        ctx.log.info("fetched", candidates=len(ctx.candidates),
                     sources_live=live, sources_total=len(sources),
                     skipped_seen=ctx.stats.get("skipped_seen", 0))
