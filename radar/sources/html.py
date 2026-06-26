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
from ..core.text import smart_truncate, strip_trailing_date
from ._base import BaseSource

_TITLE_PREFIXES = ("Featured ", "New ", "Announcement ", "Read more ")


def _clean_title(text: str, max_len: int = 80) -> str:
    """Best-effort: strip noise prefixes + trailing dates, cap at a word boundary.
    (A card that mashes title+blurb with no inner heading may still leak some blurb —
    that's a known limitation, surfaced in the run report rather than hidden.)"""
    text = strip_trailing_date(text.strip())
    for pre in _TITLE_PREFIXES:
        if text.startswith(pre):
            text = strip_trailing_date(text[len(pre):])
            break
    return smart_truncate(text, max_len)


class _LinkExtractor(HTMLParser):
    """Collect (href, title) per <a>. Prefer the anchor's inner heading (h1–h4)
    text as the title — cards put the real title in a heading and the blurb in a
    <p>, so taking the heading avoids mashing them together."""

    _HEADINGS = {"h1", "h2", "h3", "h4"}

    def __init__(self) -> None:
        super().__init__()
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._text: list[str] = []
        self._heading: list[str] = []
        self._in_heading = 0

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            href = dict(attrs).get("href")
            if href:
                self._href = href
                self._text = []
                self._heading = []
                self._in_heading = 0
        elif tag in self._HEADINGS and self._href is not None:
            self._in_heading += 1

    def handle_data(self, data):
        if self._href is not None:
            self._text.append(data)
            if self._in_heading > 0:
                self._heading.append(data)

    def handle_endtag(self, tag):
        if tag in self._HEADINGS and self._href is not None and self._in_heading > 0:
            self._in_heading -= 1
        if tag == "a" and self._href is not None:
            heading = " ".join(" ".join(self._heading).split())
            anchor = " ".join(" ".join(self._text).split())
            self.links.append((self._href, heading or anchor))
            self._href = None
            self._text = []
            self._heading = []
            self._in_heading = 0


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
