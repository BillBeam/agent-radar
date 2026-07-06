"""Generic HTML adapter — best-effort link extraction for sources without a feed
(e.g. Anthropic engineering). stdlib only, no bs4.

params:
  url_contains: "/engineering"   # keep only links whose href contains this
  limit: 25
  min_text_len: 18               # skip nav/short anchors
  enrich_summary: true           # fill empty summaries from each page's og:description (cached)
Dates: index cards often print the publish date inside the link (Anthropic renders
'<h3>Title</h3><div>Apr 23, 2026</div>') — we parse it so genuinely-new posts enter as
🆕今日新增 with a real date and old posts are honestly 📚首次收录 (7.3 复盘: everything
was undated → labeled 无日期 backfill). Cards without a date keep published_at=None →
bounded back-catalog handling as before. No window filter here either way: the whole
point of this source is collecting the not-yet-seen back-catalog (fetch bounds it).

Summary enrichment (B3b, 2026-07-06): index cards carry NO blurb, so items reached triage
as bare titles — "Introducing Claude Tag" (a major Slack-agent capability launch) sat in the
pool 5 runs unscoreable because neither model nor human can tell majorness from that title.
For sources opting in, empty summaries are filled from the target page's og:description /
meta description, cached on disk (url → text, negative results too) so the steady-state cost
is zero extra requests; per-run fetches are capped and any failure leaves summary empty.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Optional
from urllib.parse import urljoin

from ..core.config import Paths
from ..core.io import atomic_write_json, read_json
from ..core.models import Item, Source, TimeWindow
from ..core.registry import register
from ..core.text import smart_truncate, strip_trailing_date
from ._base import BaseSource

_MAX_ENRICH_FETCHES = 12   # per source per run — first run backfills over a few days
_DESC_RE = (
    re.compile(r'<meta[^>]+(?:property|name)=["\'](?:og:description|description)["\'][^>]*'
               r'content=["\']([^"\']+)["\']', re.I),
    re.compile(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]*'
               r'(?:property|name)=["\'](?:og:description|description)["\']', re.I),
)

_TITLE_PREFIXES = ("Featured ", "New ", "Announcement ", "Read more ")
_MONTHS = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}
_DATE_RE = re.compile(
    r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+(\d{1,2}),\s*(20\d{2})\b")


def _published(anchor_text: str) -> Optional[datetime]:
    """Publish date from a card's full anchor text. The date sits AFTER the title/blurb,
    so take the LAST match; None (→ undated handling) when the card shows no date."""
    m = None
    for m in _DATE_RE.finditer(anchor_text):
        pass
    if m is None:
        return None
    try:
        return datetime(int(m.group(3)), _MONTHS[m.group(1).lower()[:3]], int(m.group(2)),
                        tzinfo=timezone.utc)
    except ValueError:
        return None


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
            # (href, title-ish text, FULL anchor text) — the full text keeps the card's
            # date (rendered outside the heading) available for _published()
            self.links.append((self._href, heading or anchor, anchor))
            self._href = None
            self._text = []
            self._heading = []
            self._in_heading = 0


def extract_description(page_html: str) -> str:
    """og:description / meta description from a page (attribute order varies)."""
    for pat in _DESC_RE:
        m = pat.search(page_html)
        if m:
            return " ".join(m.group(1).split())[:500]
    return ""


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
        for href, text, anchor in parser.links:
            url = urljoin(source.url, href)
            if contains and contains not in url:
                continue
            if len(text) < min_len or url in seen:
                continue
            seen.add(url)
            items.append(Item.create(source=source, title=_clean_title(text), url=url,
                                     published_at=_published(anchor)))
            if len(items) >= limit:
                break
        if source.params.get("enrich_summary"):
            self._enrich_summaries(items)
        return items

    def _enrich_summaries(self, items: list[Item]) -> None:
        """Fill empty summaries from each page's meta description. Disk-cached (negative
        results too) → steady state fetches nothing; capped per run; never raises."""
        try:
            cache: dict[str, str] = read_json(Paths.html_summary_cache, {}) or {}
        except Exception:  # noqa: BLE001
            cache = {}
        fetched = 0
        dirty = False
        for it in items:
            if it.summary:
                continue
            if it.url in cache:
                it.summary = cache[it.url]
                continue
            if fetched >= _MAX_ENRICH_FETCHES:
                continue
            fetched += 1
            try:
                desc = extract_description(self.get_text(it.url, timeout=15, retries=1))
            except Exception:  # noqa: BLE001 — enrichment is best-effort, item stays bare
                continue  # not cached → retried next run
            cache[it.url] = desc  # "" too: a page with no description shouldn't be re-fetched daily
            it.summary = desc
            dirty = True
        if dirty:
            try:
                atomic_write_json(Paths.html_summary_cache, cache)
            except Exception:  # noqa: BLE001
                pass
