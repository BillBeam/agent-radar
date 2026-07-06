"""Fetch stage — deterministic, no LLM. The reliable spine of every run.

Per-source failures are isolated (one dead feed never kills the run). The raw
candidate pool is always written to disk first, so a later LLM failure can be
recovered / re-run offline.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Optional

from ..core import registry
from ..core.config import Paths
from ..core.io import atomic_write_json, read_json
from ..core.models import Item, RunContext, Source, TimeWindow
from ..core.ports import Stage
from ..core.registry import register
from ..sources import load_sources
from ._arxiv import arxiv_id_from_url

_VER = re.compile(r"v\d+$")


def _parse_stamp(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _effective_window(source: Source, ctx: RunContext,
                      last_success: dict[str, str]) -> tuple[TimeWindow, float]:
    """Catch-up window (B2): downtime or a failed source must not become a permanent miss.

    Feed adapters window-filter upstream, so anything published outside the configured
    window while the machine was off (or while THIS source kept failing) used to be dropped
    forever. The effective window therefore stretches to cover the gap since this source's
    last successful fetch (+margin for publish lag), capped so a long-dormant machine can't
    pull whole archives. A source with no recorded success (first run / newly added) gets
    its configured window — there is no gap to catch up on.

    Returns (window, configured_hours) — configured_hours lets the caller tell 'genuinely
    fresh' from 'caught-up backlog' when reporting."""
    configured = float(source.params.get("max_age_hours", ctx.window.hours))
    prev = _parse_stamp(last_success.get(source.id))
    if prev is None:
        return TimeWindow(configured), configured
    gap_h = (ctx.started_at - prev).total_seconds() / 3600 + ctx.config.catchup_margin_hours
    eff = max(configured, min(gap_h, ctx.config.catchup_max_hours))
    return TimeWindow(eff), configured


def _dedup_key(it: Item) -> str:
    """Dedup key that collapses the SAME arXiv paper across sources AND version suffixes —
    `arxiv-agents` (`…/abs/2607.02255v1`) vs `hf-daily-papers` (`…/abs/2607.02255` or
    `huggingface.co/papers/2607.02255`) — which otherwise hash to different per-URL ids and
    list the paper twice. Non-arXiv items keep their per-URL id (behavior unchanged)."""
    aid = arxiv_id_from_url(it.url)
    return f"arxiv:{_VER.sub('', aid)}" if aid else it.id


def _needs_backfill_cap(it: Item, window: TimeWindow) -> bool:
    """True for back-catalog items: DATELESS or STALE-DATED (outside the source's recency
    window). Both are 'first seen now, not published now' — bounded per source per run so
    an index page can't dump its whole archive into one day's candidates. Stale-dated items
    only arrive from index-scrape sources (html) — feed adapters window-filter upstream, so
    this preserves the exact pre-date-parsing flood control for them."""
    return it.published_at is None or not window.is_fresh(it.published_at)


@register("stage", "fetch")
class FetchStage(Stage):
    name = "fetch"
    critical = False

    def run(self, ctx: RunContext) -> None:
        sources = load_sources()
        ctx.sources = sources
        seen = set(read_json(Paths.seen_json, {}).keys())
        first_seen: dict[str, str] = read_json(Paths.first_seen_json, {}) or {}
        fetch_state = read_json(Paths.fetch_state_json, {}) or {}
        last_success: dict[str, str] = fetch_state.get("last_success", {}) or {}
        today = ctx.started_at.astimezone().strftime("%Y-%m-%d")
        now_stamp = ctx.started_at.isoformat(timespec="seconds")
        max_undated = ctx.config.max_undated_per_source

        adapters: dict[str, object] = {}
        pool: dict[str, Item] = {}
        per_source: dict[str, int] = {}
        catchup: dict[str, float] = {}

        for s in sources:
            try:
                adapter = adapters.get(s.type.value)
                if adapter is None:
                    adapter = registry.get("source", s.type.value)(config=ctx.config, log=ctx.log)
                    adapters[s.type.value] = adapter
                # per-source recency override (papers lag → wider leash), stretched to
                # cover any gap since this source's last SUCCESSFUL fetch (B2 catch-up)
                eff, configured_h = _effective_window(s, ctx, last_success)
                if eff.hours > configured_h:
                    catchup[s.id] = round(eff.hours, 1)
                items = adapter.fetch(s, eff)
                last_success[s.id] = now_stamp  # success = adapter returned (0 items is fine)
            except Exception as e:  # noqa: BLE001 — circuit-break this source only
                ctx.log.warn("source failed", source=s.id, error=repr(e))
                ctx.bump("source_errors")
                per_source[s.id] = -1
                continue  # last_success untouched → next run auto-widens this source's window

            kept = 0
            backfill_kept = 0
            for it in items:
                if it.id in seen:
                    ctx.bump("skipped_seen")
                    continue
                if _needs_backfill_cap(it, eff):
                    # bounded history: back-catalog (dateless OR stale-dated index posts)
                    # must not dump its whole archive as "today".
                    if backfill_kept >= max_undated:
                        continue
                    backfill_kept += 1
                first_seen.setdefault(it.id, today)  # remember when we first saw it
                key = _dedup_key(it)                  # arXiv id-normalized (cross-source/version)
                existing = pool.get(key)
                if existing is None or it.weight > existing.weight:
                    pool[key] = it
                kept += 1
            per_source[s.id] = kept

        ctx.candidates = list(pool.values())
        ctx.bump("candidates", len(ctx.candidates))
        ctx.stats["per_source"] = per_source
        if catchup:
            ctx.stats["catchup"] = catchup
            ctx.log.info("catch-up windows widened (gap since last successful fetch)",
                         sources=catchup)
        # prune first_seen to ~120 days, then persist
        cutoff = (datetime.now() - timedelta(days=120)).strftime("%Y-%m-%d")
        first_seen = {k: v for k, v in first_seen.items() if v >= cutoff}
        atomic_write_json(Paths.first_seen_json, first_seen)
        atomic_write_json(Paths.fetch_state_json, {"last_success": last_success})

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
        # per_source in the log line: saturation history must be auditable later
        # (last_run.json is overwritten每跑; the 07-06 audit had to reconstruct it from pool files)
        ctx.log.info("fetched", candidates=len(ctx.candidates),
                     sources_live=live, sources_total=len(sources),
                     skipped_seen=ctx.stats.get("skipped_seen", 0),
                     per_source=per_source)
