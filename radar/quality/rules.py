"""Composable quality-gate rules. Each is a small, independent filter — add one
without touching the others. Applied in the order the quality_gate stage lists.
"""
from __future__ import annotations

import re

import yaml

from ..core.config import Paths
from ..core.models import Item, RunContext
from ..core.ports import QualityRule
from ..core.registry import register


@register("quality", "noise_blocklist")
class NoiseBlocklistRule(QualityRule):
    """Drop marketing/funding/SEO noise (config/blocklist.yaml — agent-editable)."""
    name = "noise_blocklist"

    def apply(self, items: list[Item], ctx: RunContext) -> list[Item]:
        bl = yaml.safe_load(Paths.blocklist_yaml.read_text(encoding="utf-8")) or {}
        blocked_ids = set(bl.get("source_ids", []))
        patterns = [re.compile(p, re.I) for p in bl.get("title_patterns", [])]
        kept: list[Item] = []
        for it in items:
            if it.source_id in blocked_ids or any(p.search(it.title) for p in patterns):
                ctx.bump("gate.blocked")
                continue
            kept.append(it)
        return kept


@register("quality", "threshold")
class ThresholdRule(QualityRule):
    """Hard relevance floor — below it, drop (宁缺毋滥). Needs triage scores."""
    name = "threshold"

    def apply(self, items: list[Item], ctx: RunContext) -> list[Item]:
        thr = ctx.config.relevance_threshold
        kept = [it for it in items if (it.score or 0) >= thr]
        ctx.bump("gate.below_threshold", len(items) - len(kept))
        return kept


@register("quality", "cap")
class CapRule(QualityRule):
    """Coarse-sort by score and cap to the FINALIST pool — the rerank stage then
    does the relative ranking + diversity to pick the final max_items."""
    name = "cap"

    def apply(self, items: list[Item], ctx: RunContext) -> list[Item]:
        ranked = sorted(items, key=lambda it: (it.score or 0) + 0.4 * it.weight, reverse=True)
        cap = ctx.config.finalist_pool
        if len(ranked) > cap:
            ctx.bump("gate.capped", len(ranked) - cap)
        return ranked[:cap]
