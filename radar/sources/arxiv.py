"""arXiv adapter — official export API, category + keyword filtered, recency-sorted.

params:
  categories: ["cs.AI","cs.CL","cs.MA","cs.SE"]      # cs.LG dropped (C2): general-ML noise leak
  keywords:   ["agent","agentic","tool use","MCP",...]  # ANDed (any-of) on abstract; agent-focused
  max_results: 50
"""
from __future__ import annotations

from urllib.parse import urlencode

from ..core.models import Item, Source, TimeWindow
from ..core.registry import register
from ._base import BaseSource
from ._feed import parse_feed

API = "http://export.arxiv.org/api/query"
# C2 收紧：cs.LG（通用 ML 大类）去掉 = 模型噪声主漏口；宽词 LLM/language model/reasoning 去掉。
DEFAULT_CATS = ["cs.AI", "cs.CL", "cs.MA", "cs.SE"]
DEFAULT_KEYWORDS = ["agent", "agentic", "multi-agent", "tool use", "function calling",
                    "MCP", "retrieval", "RAG", "agent harness", "planning", "orchestration"]


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
