"""Remember stage — after delivery, record what we pushed today into content memory.

Runs last in DAILY_STAGES (already wired). Non-critical: a memory write must never
break or roll back a successful delivery. The tags it stores (from triage) are what
later runs' rerank uses to detect "近 N 天同主题已推过".
"""
from __future__ import annotations

from ..core.models import RunContext
from ..core.ports import Stage
from ..core.registry import register


@register("stage", "remember")
class RememberStage(Stage):
    name = "remember"
    critical = False

    def run(self, ctx: RunContext) -> None:
        if ctx.memory is None or ctx.digest is None:
            return
        items = ctx.digest.items or []
        if not items:
            return
        try:
            n = ctx.memory.remember_digest(ctx.digest.date, items)
            ctx.bump("remembered", n)
            ctx.log.info("remember", pushed=n, date=ctx.digest.date)
        except Exception as e:  # noqa: BLE001 — memory is best-effort, never break the run
            ctx.log.warn("remember failed (degrading)", error=repr(e)[:160])
