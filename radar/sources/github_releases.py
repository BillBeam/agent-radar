"""GitHub releases adapter — uses the predictable releases.atom feed.

Source url may be a full atom url, or params.repo = "owner/name" (preferred).
"""
from __future__ import annotations

from ..core.models import Item, Source, TimeWindow
from ..core.registry import register
from ._base import BaseSource
from ._feed import parse_feed


@register("source", "github_releases")
class GithubReleasesSource(BaseSource):
    def _feed_url(self, source: Source) -> str:
        repo = source.params.get("repo")
        if repo:
            return f"https://github.com/{repo}/releases.atom"
        url = source.url
        if url.endswith("/releases.atom"):
            return url
        return url.rstrip("/") + "/releases.atom"

    def fetch(self, source: Source, window: TimeWindow) -> list[Item]:
        content = self.get_bytes(self._feed_url(source), accept="application/atom+xml")
        items = parse_feed(content, source, limit=15)
        for it in items:
            if "release" not in it.tags:
                it.tags.append("release")
        return [it for it in items if window.is_fresh(it.published_at)]
