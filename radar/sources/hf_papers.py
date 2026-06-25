"""Hugging Face Daily Papers adapter (curated daily paper list)."""
from __future__ import annotations

from datetime import datetime, timezone

from ..core.models import Item, Source, TimeWindow
from ..core.registry import register
from ._base import BaseSource

API = "https://huggingface.co/api/daily_papers"


def _parse_dt(s: str) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


@register("source", "hf_papers")
class HFPapersSource(BaseSource):
    def fetch(self, source: Source, window: TimeWindow) -> list[Item]:
        url = source.url or API
        data = self.get_json(url, timeout=25)
        if isinstance(data, dict):
            data = data.get("papers") or data.get("dailyPapers") or []
        items: list[Item] = []
        for entry in data:
            paper = entry.get("paper", entry) if isinstance(entry, dict) else {}
            arxiv_id = paper.get("id") or paper.get("arxivId")
            title = paper.get("title") or entry.get("title")
            if not title:
                continue
            link = f"https://arxiv.org/abs/{arxiv_id}" if arxiv_id else paper.get("url")
            if not link:
                continue
            when = _parse_dt(paper.get("publishedAt") or entry.get("publishedAt") or "")
            if not window.is_fresh(when):
                continue
            it = Item.create(
                source=source, title=title.strip(), url=link, published_at=when,
                summary=(paper.get("summary", "") or "")[:700],
            )
            it.tags.append("paper")
            items.append(it)
        return items
