"""Hacker News adapter — Algolia search API, points-gated, keyword-targeted.

params:
  keywords:   ["AI agent","LLM","MCP","agentic","tool use",...]
  min_points: 80
  per_kw: 20
"""
from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import urlencode

from ..core.models import Item, Source, TimeWindow
from ..core.registry import register
from ._base import BaseSource

API = "https://hn.algolia.com/api/v1/search_by_date"
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
        by_url: dict[str, Item] = {}
        for kw in kws:
            qs = urlencode({
                "query": kw, "tags": "story",
                "numericFilters": f"points>{min_points}", "hitsPerPage": per_kw,
            })
            try:
                data = self.get_json(f"{API}?{qs}")
            except Exception as e:  # noqa: BLE001 — one keyword failing shouldn't kill the source
                if self.log:
                    self.log.warn("hn keyword failed", kw=kw, error=repr(e))
                continue
            for h in data.get("hits", []):
                oid = h.get("objectID")
                url = h.get("url") or f"https://news.ycombinator.com/item?id={oid}"
                title = h.get("title")
                if not title:
                    continue
                when = _parse_dt(h.get("created_at", ""))
                if not window.is_fresh(when):
                    continue
                by_url[url] = Item.create(
                    source=source, title=title, url=url, published_at=when,
                    summary=f"HN: {h.get('points', 0)} points · {h.get('num_comments', 0)} comments",
                )
        return list(by_url.values())
