"""Pipeline runner — executes a list of Stages against a RunContext.

Every stage runs inside a trace span. A non-critical stage that raises is logged,
counted as degradation, and skipped — the run continues (graceful degradation).
A critical stage that raises aborts the run. This is the harness backbone: the
deterministic spine stays up even when an LLM stage fails.
"""
from __future__ import annotations

from .models import RunContext
from .ports import Stage


class Pipeline:
    def __init__(self, stages: list[Stage]):
        self.stages = stages

    def run(self, ctx: RunContext) -> RunContext:
        for stage in self.stages:
            with ctx.trace.span(f"stage:{stage.name}"):
                try:
                    stage.run(ctx)
                except Exception as e:  # noqa: BLE001
                    msg = f"stage {stage.name!r} failed: {e!r}"
                    ctx.errors.append(msg)
                    ctx.bump("stage_errors")
                    ctx.log.error("stage failed", stage=stage.name, error=repr(e))
                    if stage.critical:
                        ctx.log.error("critical stage aborted run", stage=stage.name)
                        raise
                    ctx.log.warn("degrading: skipping failed stage", stage=stage.name)
        return ctx
