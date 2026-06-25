"""Generic HTML adapter — best-effort link extraction for sources without a feed
(e.g. Anthropic engineering). stdlib only, no bs4.

params:
  url_contains: "/engineering"   # keep only links whose href contains this
  limit: 25
  min_text_len: 18               # skip nav/short anchors
Items have no published_at (None) → they pass the freshness window and rely on
dedup (seen.json) + triage to avoid stale repeats.
"""
from __future__ import annotations

from html.parser import HTMLParser
from urllib.parse import urljoin

from ..core.models import Item, Source, TimeWindow
from ..core.registry import register
from ._base import BaseSource

_TITLE_PREFIXES = ("Featured ", "New ", "Announcement ", "Read more ")


def _clean_title(text: str, max_len: int = 80) -> str:
    """Cards often mash heading+blurb in one <a>. Strip noise prefixes and cap at
    a word boundary so titles read cleanly in the digest / DingTalk."""
    text = text.strip()
    for pre in _TITLE_PREFIXES:
        if text.startswith(pre):
            text = text[len(pre):]
    if len(text) > max_len:
        text = text[: max_len - 1].rsplit(" ", 1)[0] + "…"
    return text


class _LinkExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            href = dict(attrs).get("href")
            if href:
                self._href = href
                self._text = []

    def handle_data(self, data):
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self._href is not None:
            text = " ".join(" ".join(self._text).split())
            self.links.append((self._href, text))
            self._href = None
            self._text = []


@register("source", "html")
class HtmlSource(BaseSource):
    def fetch(self, source: Source, window: TimeWindow) -> list[Item]:
        html = self.get_text(source.url, timeout=25)
        parser = _LinkExtractor()
        parser.feed(html)

        contains = source.params.get("url_contains", "")
        limit = int(source.params.get("limit", 25))
        min_len = int(source.params.get("min_text_len", 18))

        seen: set[str] = set()
        items: list[Item] = []
        for href, text in parser.links:
            url = urljoin(source.url, href)
            if contains and contains not in url:
                continue
            if len(text) < min_len or url in seen:
                continue
            seen.add(url)
            items.append(Item.create(source=source, title=_clean_title(text), url=url))
            if len(items) >= limit:
                break
        return items
