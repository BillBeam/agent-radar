"""RSS/Atom parsing shared by rss, github_releases, and arxiv adapters."""
from __future__ import annotations

import re
from calendar import timegm
from datetime import datetime, timezone
from typing import Optional

import feedparser

from ..core.models import Item, Source

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def strip_html(text: str) -> str:
    return _WS_RE.sub(" ", _TAG_RE.sub(" ", text or "")).strip()


def entry_date(entry: dict) -> Optional[datetime]:
    for key in ("published_parsed", "updated_parsed"):
        t = entry.get(key)
        if t:
            return datetime.fromtimestamp(timegm(t), tz=timezone.utc)
    return None


def parse_feed(content: bytes, source: Source, limit: int = 60) -> list[Item]:
    parsed = feedparser.parse(content)
    items: list[Item] = []
    for e in parsed.entries[:limit]:
        link = e.get("link")
        title = strip_html(e.get("title", ""))
        if not link or not title:
            continue
        summary = strip_html(e.get("summary", "") or e.get("description", ""))[:700]
        items.append(Item.create(
            source=source, title=title, url=link,
            published_at=entry_date(e), summary=summary,
        ))
    return items
