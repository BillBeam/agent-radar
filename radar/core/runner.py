"""Run assembly: build a RunContext, wire services, assemble + run the pipeline.

This is the single place that composes adapters into a run. Stage order is data
(a list); stages not yet registered are skipped with a warning, so the pipeline
grows as phases land without any core edit.
"""
from __future__ import annotations

import os
import random
import string
from datetime import datetime
from pathlib import Path
from typing import Optional

from . import registry
from .config import Paths, RadarConfig, load_config
from .models import RunContext, TimeWindow
from .pipeline import Pipeline
from ..obs import Logger, Tracer

# canonical stage order; memory stages (recall/remember) land in P1
DAILY_STAGES = [
    "fetch", "triage", "quality_gate", "recall",
    "deepread", "synthesize", "deliver", "remember",
]


def new_run_id(mode: str) -> str:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=4))
    return f"{stamp}-{mode}-{suffix}"


def build_llm(config: RadarConfig, log: Logger):
    """Instantiate the configured LLM backend, if registered."""
    try:
        cls = registry.get("llm", "claude_code")
    except KeyError:
        log.warn("no llm adapter registered yet (claude_code) — LLM stages will no-op")
        return None
    return cls(config=config, log=log)


def build_pipeline(mode: str, ctx: RunContext) -> Pipeline:
    stages = []
    for name in DAILY_STAGES:
        try:
            cls = registry.get("stage", name)
        except KeyError:
            ctx.log.warn("stage not registered yet — skipping", stage=name)
            continue
        stages.append(cls())
    ctx.log.info("pipeline assembled", stages=[s.name for s in stages])
    return Pipeline(stages)


def make_context(mode: str, config: RadarConfig) -> RunContext:
    registry.load_adapters()
    run_id = new_run_id(mode)
    log = Logger(run_id, log_path=Paths.state / "radar.log")
    trace = Tracer(run_id, trace_path=Paths.trace / f"{run_id}.jsonl")
    ctx = RunContext(
        run_id=run_id,
        mode=mode,
        config=config,
        window=TimeWindow(config.window_hours(mode)),
        log=log,
        trace=trace,
    )
    ctx.llm = build_llm(config, log)
    return ctx


def run_mode(mode: str, config: Optional[RadarConfig] = None) -> RunContext:
    config = config or load_config()
    ctx = make_context(mode, config)
    ctx.log.info("run start", mode=mode, window_h=ctx.window.hours)
    if os.environ.get("ANTHROPIC_API_KEY"):
        ctx.log.warn("ANTHROPIC_API_KEY is set — claude -p will bill the API, "
                     "not your subscription. Unset it to use the subscription.")
    try:
        build_pipeline(mode, ctx).run(ctx)
    finally:
        ctx.log.info("run done", stats=ctx.stats, errors=len(ctx.errors))
        ctx.trace.close()
        ctx.log.close()
    return ctx
