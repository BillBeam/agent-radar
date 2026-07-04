"""Stable data contracts shared across every pipeline stage.

`Item` is the lingua franca: sources produce it, stages enrich it (score, tags,
中文详解), channels render it. Keeping this schema stable is what lets a stage
depend only on the schema and never on another stage — the core of the
ports-and-adapters design.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = 1


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def make_item_id(url: str) -> str:
    """Stable id from the canonical url — drives cross-day dedup."""
    return hashlib.sha1(url.strip().encode("utf-8")).hexdigest()[:16]


class SourceType(str, Enum):
    rss = "rss"
    arxiv = "arxiv"
    hackernews = "hackernews"
    github_releases = "github_releases"
    hf_papers = "hf_papers"
    html = "html"


class Source(BaseModel):
    """One entry in config/sources.yaml."""
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    category: str
    type: SourceType
    url: str
    weight: float = 1.0
    enabled: bool = True
    # per-type knobs (e.g. arxiv categories, hn points threshold, html selector)
    params: dict[str, Any] = Field(default_factory=dict)


class Item(BaseModel):
    """A normalized candidate, enriched in place as it flows through stages."""
    model_config = ConfigDict(extra="ignore")

    schema_version: int = SCHEMA_VERSION
    id: str
    source_id: str
    source_name: str
    category: str
    weight: float = 1.0

    title: str
    url: str
    published_at: Optional[datetime] = None
    summary: str = ""          # short feed-provided abstract
    snippet: str = ""          # extra context from the feed

    # ---- enrichment (filled by later stages) ----
    tags: list[str] = Field(default_factory=list)         # topic-taxonomy labels
    score: Optional[float] = None                         # triage relevance 0-10
    reason: Optional[str] = None                          # one-line triage reason
    self_applicable: bool = False                         # could improve radar itself
    target_component: Optional[str] = None                # which radar component
    full_text: Optional[str] = None                       # fetched in deep-read
    explain_zh: Optional[str] = None                      # 中文详解 (markdown)
    links: list[str] = Field(default_factory=list)        # related past-push ids

    @classmethod
    def create(
        cls,
        *,
        source: Source,
        title: str,
        url: str,
        published_at: Optional[datetime] = None,
        summary: str = "",
        snippet: str = "",
    ) -> "Item":
        return cls(
            id=make_item_id(url),
            source_id=source.id,
            source_name=source.name,
            category=source.category,
            weight=source.weight,
            title=title.strip(),
            url=url.strip(),
            published_at=published_at,
            summary=summary.strip(),
            snippet=snippet.strip(),
        )


class Digest(BaseModel):
    """The finished daily/weekly product."""
    model_config = ConfigDict(extra="ignore")

    schema_version: int = SCHEMA_VERSION
    kind: str = "daily"               # daily | weekly
    date: str                         # YYYY-MM-DD
    generated_at: datetime = Field(default_factory=utcnow)
    items: list[Item] = Field(default_factory=list)
    markdown: str = ""           # full, rich 详解 — local archive
    markdown_brief: str = ""     # concise, skimmable — DingTalk / IM
    stats: dict[str, Any] = Field(default_factory=dict)


@dataclass
class TimeWindow:
    """Recency window for a fetch run (keeps things 实时)."""
    hours: float

    @property
    def cutoff(self) -> datetime:
        from datetime import timedelta
        return utcnow() - timedelta(hours=self.hours)

    def is_fresh(self, when: Optional[datetime]) -> bool:
        if when is None:
            return True  # undated items pass the window, judged later by triage
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        return when >= self.cutoff


# ---- display freshness: 🆕今日新增 vs 📚首次收录 ----
# Widest per-source recency leash (arxiv/hf run 96h): a DATED item older than this is
# back-catalog we're collecting for the first time, not "today's news". Before html sources
# carried dates, "dated ⇒ fresh" was safe; now an index page can yield months-old dated posts.
FRESH_MAX_AGE_H = 96.0


def is_display_fresh(item: "Item") -> bool:
    """THE single definition of the 🆕/📚 split. synthesize (grouping + [N] numbering +
    items.json order) and dingtalk_card (row numbering/markers) must both use this —
    if they ever disagree, card votes / `radar mark N` map to the wrong item."""
    return item.published_at is not None and TimeWindow(FRESH_MAX_AGE_H).is_fresh(item.published_at)


@dataclass
class RunContext:
    """Mutable per-run state threaded through the pipeline.

    Not a pydantic model on purpose — it carries live objects (logger, tracer,
    llm client) that aren't serializable.
    """
    run_id: str
    mode: str                       # daily | weekly | ...
    config: Any                     # RadarConfig (avoids import cycle)
    window: TimeWindow
    started_at: datetime = field(default_factory=utcnow)

    sources: list[Source] = field(default_factory=list)
    candidates: list[Item] = field(default_factory=list)   # raw fetched pool
    items: list[Item] = field(default_factory=list)        # surviving / selected
    digest: Optional[Digest] = None

    # injected services (set by the runner)
    llm: Any = None
    memory: Any = None
    log: Any = None
    trace: Any = None

    stats: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    def bump(self, key: str, n: int = 1) -> None:
        self.stats[key] = self.stats.get(key, 0) + n
