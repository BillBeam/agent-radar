"""Hacker News adapter — Algolia search API, points-gated, keyword-targeted.

params:
  keywords:   ["AI agent","LLM","MCP","agentic","tool use",...]
  min_points: 80
  per_kw: 20

2026-07-09: Algolia dropped `points` from the HN index's `numericAttributesForFiltering`, so
`numericFilters=points>N` now 400s on BOTH /search and /search_by_date — every keyword failed
and the source silently contributed 0 items (it swallows per-keyword errors by design). The
points gate therefore moved client-side, and the recency gate moved server-side onto
`created_at_i` (still filterable). `/search` replaces `/search_by_date` because the HN index's
custom ranking is points-desc — within the window the top `per_kw` hits are the popular ones,
which is what the gate wanted anyway. `search_by_date` would have handed back the newest 20
stories (nearly all under the threshold) and the client-side gate would drop almost all of them.
"""
from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import urlencode

from ..core.models import Item, Source, TimeWindow
from ..core.registry import register
from ._base import BaseSource, SourceError

API = "https://hn.algolia.com/api/v1/search"
DEFAULT_KEYWORDS = ["AI agent", "agentic", "LLM agent", "MCP", "tool use", "agent harness"]


def _parse_dt(s: str) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


@register("source", "hackernews")
class HackerNewsSource(BaseSource):
    def fetch(self, source: Source, window: TimeWindow) -> list[Item]:
        kws = source.params.get("keywords", DEFAULT_KEYWORDS)
        min_points = int(source.params.get("min_points", 80))
        per_kw = int(source.params.get("per_kw", 20))
        cutoff = int(window.cutoff.timestamp())      # server-side recency (created_at_i is filterable)
        by_url: dict[str, Item] = {}
        failed = 0
        for kw in kws:
            qs = urlencode({
                "query": kw, "tags": "story",
                "numericFilters": f"created_at_i>{cutoff}", "hitsPerPage": per_kw,
            })
            try:
                data = self.get_json(f"{API}?{qs}")
            except Exception as e:  # noqa: BLE001 — one keyword failing shouldn't kill the source
                failed += 1
                if self.log:
                    self.log.warn("hn keyword failed", kw=kw, error=repr(e))
                continue
            for h in data.get("hits", []):
                oid = h.get("objectID")
                url = h.get("url") or f"https://news.ycombinator.com/item?id={oid}"
                title = h.get("title")
                if not title:
                    continue
                if int(h.get("points") or 0) < min_points:   # the gate Algolia no longer applies
                    continue
                when = _parse_dt(h.get("created_at", ""))
                if not window.is_fresh(when):
                    continue
                by_url[url] = Item.create(
                    source=source, title=title, url=url, published_at=when,
                    summary=f"HN: {h.get('points', 0)} points · {h.get('num_comments', 0)} comments",
                )
        # Per-keyword tolerance must not hide a whole-source outage. When the Algolia contract
        # broke on 2026-07-09 every keyword 400'd, each was swallowed as a WARN, and the source
        # still counted toward `sources_live` — it contributed 0 items for a full day with no
        # alarm. If NOTHING succeeded, the source is down: say so and let fetch's salvage pass
        # and the fetch_health alert do their job.
        if kws and failed == len(kws):
            raise SourceError(f"all {failed} HN keywords failed — source is down, not empty")
        return list(by_url.values())
