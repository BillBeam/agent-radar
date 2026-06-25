"""RSS/Atom source adapter."""
from __future__ import annotations

from ..core.models import Item, Source, TimeWindow
from ..core.registry import register
from ._base import BaseSource
from ._feed import parse_feed


@register("source", "rss")
class RssSource(BaseSource):
    def fetch(self, source: Source, window: TimeWindow) -> list[Item]:
        content = self.get_bytes(source.url, accept="application/rss+xml, application/atom+xml, application/xml")
        items = parse_feed(content, source)
        return [it for it in items if window.is_fresh(it.published_at)]
