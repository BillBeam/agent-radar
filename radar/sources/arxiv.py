"""arXiv adapter — official export API, category + keyword filtered, recency-sorted.

params:
  categories: ["cs.AI","cs.CL","cs.MA","cs.SE"]      # cs.LG dropped (C2): general-ML noise leak
  keywords:   ["agent","agentic","tool use","MCP",...]  # ANDed (any-of) on abstract; agent-focused
  max_results: 600   # hard ceiling ACROSS pages, not a per-request guess

Pagination (B1, 2026-07-06): a single capped request silently truncates — probed reality:
the 96h window before the 07-03 run matched >200 items, so the old max_results=50 dropped
150+ that day (5 of 7 recorded runs sat exactly at the 50 cap). We now page through the
API (sortBy=submittedDate desc) and stop early once a page crosses the window boundary,
so quiet days still cost one request while busy/catch-up windows are never cut short.
"""
from __future__ import annotations

import time
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
DEFAULT_MAX_RESULTS = 600
PAGE_SIZE = 200        # per-request size; arXiv allows far more but modest pages fail/retry cheaper
PAGE_DELAY_S = 3.0     # arXiv API etiquette: ~3s between successive requests
TIMEOUT_S = 60         # export API is slow under load (real 30s read-timeout on 2026-07-06)


@register("source", "arxiv")
class ArxivSource(BaseSource):
    def _url(self, source: Source, *, start: int = 0, per_page: int | None = None) -> str:
        cats = source.params.get("categories", DEFAULT_CATS)
        kws = source.params.get("keywords", DEFAULT_KEYWORDS)
        if per_page is None:
            per_page = int(source.params.get("max_results", DEFAULT_MAX_RESULTS))
        cat_q = " OR ".join(f"cat:{c}" for c in cats)
        search = f"({cat_q})"
        if kws:
            kw_q = " OR ".join(f'abs:"{k}"' for k in kws)
            search += f" AND ({kw_q})"
        qs = urlencode({
            "search_query": search,
            "sortBy": "submittedDate",
            "sortOrder": "descending",
            "start": start,
            "max_results": per_page,
        })
        return f"{API}?{qs}"

    def fetch(self, source: Source, window: TimeWindow) -> list[Item]:
        ceiling = int(source.params.get("max_results", DEFAULT_MAX_RESULTS))
        items: list[Item] = []
        start = 0
        while start < ceiling:
            per_page = min(PAGE_SIZE, ceiling - start)
            content = self.get_bytes(self._url(source, start=start, per_page=per_page),
                                     timeout=TIMEOUT_S)
            page = parse_feed(content, source, limit=per_page)
            items.extend(page)
            dated = [it.published_at for it in page if it.published_at]
            # submittedDate-desc ordering: once this page reaches past the window
            # boundary, every deeper page is staler still — stop paging.
            if len(page) < per_page or (dated and not window.is_fresh(min(dated))):
                break
            start += len(page)
            time.sleep(PAGE_DELAY_S)
        for it in items:
            if "paper" not in it.tags:
                it.tags.append("paper")
        return [it for it in items if window.is_fresh(it.published_at)]
