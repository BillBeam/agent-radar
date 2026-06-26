"""Shared text tidiers for presentation — title/essence cleanup.

Kept tiny and dependency-free. Used by source title extraction and the synthesize
renderers so titles and one-liners never end mid-word or carry a trailing date.
"""
from __future__ import annotations

import re

# trailing "Apr 08, 2026" / "Nov 26 2025" style dates that cards mash into titles
_DATE_TAIL = re.compile(
    r"\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2},?\s+20\d\d\s*$",
    re.I,
)
_DATE_ISO_TAIL = re.compile(r"\s+\d{4}-\d{2}-\d{2}\s*$")
_HEADING_LINE = re.compile(r"^\s{0,3}#{1,6}\s*(.+?)\s*#*\s*$", re.M)


def strip_trailing_date(text: str) -> str:
    text = _DATE_TAIL.sub("", text or "")
    text = _DATE_ISO_TAIL.sub("", text)
    return text.strip()


def smart_truncate(text: str, limit: int, ellipsis: str = "…") -> str:
    """Truncate to <= limit chars without cutting mid-word. For English we back off
    to the last space near the limit; CJK chars are atomic so a hard cut is fine."""
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    sp = cut.rfind(" ")
    if sp > 0 and sp >= limit - 25:  # a real word boundary close enough → don't split the word
        cut = cut[:sp]
    return cut.rstrip(" ,.;:·、，。") + ellipsis


def demote_headings(md: str) -> str:
    """Defensive: turn any leftover markdown headings (#..######) into bold lines,
    so an inlined explanation never out-ranks the item's own ### header."""
    return _HEADING_LINE.sub(lambda m: f"**{m.group(1).strip()}**", md or "")
