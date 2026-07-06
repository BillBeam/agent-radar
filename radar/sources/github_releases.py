"""GitHub releases adapter — REST API first, releases.atom as fallback.

Why not atom-only anymore (B1b, probed 2026-07-06): GitHub serves EXACTLY 10 entries in
releases.atom regardless of any client-side limit. For high-frequency repos that is not
even one day of depth — cline's 10 entries spanned 9 HOURS during an sdk/* tag burst, so
an atom-only fetch can miss releases between two consecutive daily runs, and no catch-up
window can recover what the feed no longer serves. The unauthenticated REST API returns
up to 100 per page (we take 30); one request per source per day is far inside the 60/h
rate limit, and any API failure (403 rate-limit, outage) falls back to the atom feed.

Source url may be a full atom url, or params.repo = "owner/name" (preferred; REST needs it).
"""
from __future__ import annotations

from datetime import datetime, timezone

from ..core.models import Item, Source, TimeWindow
from ..core.registry import register
from ._base import BaseSource
from ._feed import parse_feed, strip_html

API = "https://api.github.com/repos/{repo}/releases"
DEFAULT_PER_PAGE = 30


def _parse_dt(s: str) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


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

    def _fetch_api(self, source: Source) -> list[Item]:
        repo = source.params.get("repo")
        if not repo:
            raise ValueError("no params.repo — atom fallback only")
        per_page = int(source.params.get("per_page", DEFAULT_PER_PAGE))
        data = self.get_json(API.format(repo=repo) + f"?per_page={per_page}", timeout=25)
        items: list[Item] = []
        for r in data or []:
            if not isinstance(r, dict):
                continue
            title = (r.get("name") or r.get("tag_name") or "").strip()
            url = r.get("html_url")
            if not title or not url:
                continue
            items.append(Item.create(
                source=source, title=title, url=url,
                published_at=_parse_dt(r.get("published_at") or ""),
                summary=strip_html(r.get("body") or "")[:700],
            ))
        return items

    def fetch(self, source: Source, window: TimeWindow) -> list[Item]:
        try:
            items = self._fetch_api(source)
        except Exception as e:  # noqa: BLE001 — rate limit / outage / no repo → atom fallback
            if self.log:
                self.log.warn("gh releases API failed — falling back to atom (depth 10)",
                              source=source.id, error=repr(e)[:120])
            content = self.get_bytes(self._feed_url(source), accept="application/atom+xml")
            items = parse_feed(content, source, limit=int(source.params.get("per_page",
                                                                            DEFAULT_PER_PAGE)))
        for it in items:
            if "release" not in it.tags:
                it.tags.append("release")
        return [it for it in items if window.is_fresh(it.published_at)]
