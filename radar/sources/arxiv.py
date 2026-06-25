"""arXiv adapter — official export API, category + keyword filtered, recency-sorted.

params:
  categories: ["cs.AI","cs.CL","cs.MA","cs.SE","cs.LG"]
  keywords:   ["agent","LLM","language model",...]  # ANDed (any-of) on abstract
  max_results: 50
"""
from __future__ import annotations

from urllib.parse import urlencode

from ..core.models import Item, Source, TimeWindow
from ..core.registry import register
from ._base import BaseSource
from ._feed import parse_feed

API = "http://export.arxiv.org/api/query"
DEFAULT_CATS = ["cs.AI", "cs.CL", "cs.MA", "cs.SE", "cs.LG"]
DEFAULT_KEYWORDS = ["agent", "agentic", "LLM", "language model", "tool use", "retrieval"]


@register("source", "arxiv")
class ArxivSource(BaseSource):
    def _url(self, source: Source) -> str:
        cats = source.params.get("categories", DEFAULT_CATS)
        kws = source.params.get("keywords", DEFAULT_KEYWORDS)
        n = int(source.params.get("max_results", 50))
        cat_q = " OR ".join(f"cat:{c}" for c in cats)
        search = f"({cat_q})"
        if kws:
            kw_q = " OR ".join(f'abs:"{k}"' for k in kws)
            search += f" AND ({kw_q})"
        qs = urlencode({
            "search_query": search,
            "sortBy": "submittedDate",
            "sortOrder": "descending",
            "max_results": n,
        })
        return f"{API}?{qs}"

    def fetch(self, source: Source, window: TimeWindow) -> list[Item]:
        content = self.get_bytes(self._url(source), timeout=30)
        items = parse_feed(content, source, limit=int(source.params.get("max_results", 50)))
        for it in items:
            if "paper" not in it.tags:
                it.tags.append("paper")
        return [it for it in items if window.is_fresh(it.published_at)]
