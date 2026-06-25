"""Quality gate — applies composable quality rules in order, after triage."""
from __future__ import annotations

from ..core import registry
from ..core.models import RunContext
from ..core.ports import Stage
from ..core.registry import register

# explicit order (registration order is not guaranteed)
RULE_ORDER = ["noise_blocklist", "threshold", "cap"]


@register("stage", "quality_gate")
class QualityGateStage(Stage):
    name = "quality_gate"

    def run(self, ctx: RunContext) -> None:
        items = ctx.items if ctx.items else list(ctx.candidates)
        before = len(items)
        for rname in RULE_ORDER:
            rule = registry.get("quality", rname)()
            items = rule.apply(items, ctx)
        ctx.items = items
        ctx.stats["funnel"] = {
            "candidates": len(ctx.candidates),
            "scored": before,
            "blocked": ctx.stats.get("gate.blocked", 0),
            "below_threshold": ctx.stats.get("gate.below_threshold", 0),
            "selected": len(items),
        }
        ctx.log.info("quality gate", before=before, after=len(items),
                     blocked=ctx.stats.get("gate.blocked", 0),
                     below_threshold=ctx.stats.get("gate.below_threshold", 0))
