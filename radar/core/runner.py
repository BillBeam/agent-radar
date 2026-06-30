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
from .io import atomic_write_json
from .lock import RunLock
from .models import RunContext, TimeWindow, utcnow
from .pipeline import Pipeline
from ..obs import Logger, Tracer

# canonical stage order. `remember` (P2) writes content memory after deliver; `recall`
# stays unregistered for now — rerank reads ctx.memory directly (LEAN, see decisions.md).
# Unregistered stages are skipped, so the list can name future stages harmlessly.
DAILY_STAGES = [
    "fetch", "triage", "quality_gate", "rerank", "critic", "recall",
    "deepread", "synthesize", "deliver", "remember",
]


def new_run_id(mode: str) -> str:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=4))
    return f"{stamp}-{mode}-{suffix}"


def build_llm(config: RadarConfig, log: Logger, trace=None):
    """Instantiate the configured LLM backend, if registered. `trace` (optional) lets the
    client emit a per-LLM-call event + roll up per-stage tokens/latency for observability."""
    try:
        cls = registry.get("llm", "claude_code")
    except KeyError:
        log.warn("no llm adapter registered yet (claude_code) — LLM stages will no-op")
        return None
    return cls(config=config, log=log, trace=trace)


def build_memory(config: RadarConfig, log: Logger):
    """Instantiate the content-memory store, if enabled. Never break a run for memory:
    any failure → None (rerank/remember degrade to non-personalized behavior)."""
    if not getattr(config, "memory", None) or not config.memory.enabled:
        return None
    try:
        from ..memory.store import MemoryStore
        return MemoryStore()
    except Exception as e:  # noqa: BLE001
        log.warn("memory store unavailable (degrading)", error=repr(e)[:160])
        return None


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
    ctx.llm = build_llm(config, log, trace)
    ctx.memory = build_memory(config, log)
    return ctx


def _write_last_run(ctx: RunContext) -> None:
    """Persist a run summary so `radar status` can tell what happened / if it broke."""
    fh = ctx.stats.get("fetch_health", {}) or {}
    selected = len(ctx.digest.items) if ctx.digest else len(ctx.items)
    usage = dict(getattr(ctx.llm, "usage_total", {})) if ctx.llm else {}
    summary = {
        "run_id": ctx.run_id,
        "mode": ctx.mode,
        "finished_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "duration_s": round((utcnow() - ctx.started_at).total_seconds(), 1),
        "sources": {"live": fh.get("live"), "total": fh.get("total"),
                    "failed": fh.get("failed", [])},
        "candidates": ctx.stats.get("candidates", 0),
        "selected": selected,
        "deepread_ok": ctx.stats.get("deepread.ok", 0),
        "triage_coverage": ctx.stats.get("triage_coverage"),
        "triage_degraded": ctx.stats.get("triage_degraded", False),
        "tokens": usage,
        "by_stage": dict(getattr(ctx.llm, "by_stage", {})) if ctx.llm else {},
        "delivered": ctx.stats.get("delivered", {}),
        "errors": ctx.errors[:5],
    }
    try:
        atomic_write_json(Paths.state / "last_run.json", summary)
    except Exception as e:  # noqa: BLE001 — never let bookkeeping break a run
        ctx.log.warn("failed to write last_run.json", error=repr(e))


def run_mode(mode: str, config: Optional[RadarConfig] = None) -> RunContext:
    config = config or load_config()
    ctx = make_context(mode, config)

    lock = RunLock(Paths.state / "run.lock")
    if not lock.acquire():
        ctx.log.error("another run holds the lock — aborting",
                      held=getattr(lock, "held_by", {}))
        ctx.errors.append("run-lock held by another live process")
        ctx.trace.close()
        ctx.log.close()
        return ctx

    ctx.log.info("run start", mode=mode, window_h=ctx.window.hours)
    if os.environ.get("ANTHROPIC_API_KEY"):
        ctx.log.warn("ANTHROPIC_API_KEY is set — claude -p will bill the API, "
                     "not your subscription. Unset it to use the subscription.")
    try:
        build_pipeline(mode, ctx).run(ctx)
    finally:
        _write_last_run(ctx)
        tok = getattr(ctx.llm, "usage_total", {}) if ctx.llm else {}
        ctx.log.info("run done", errors=len(ctx.errors),
                     tokens_in=tok.get("input"), tokens_out=tok.get("output"),
                     llm_calls=tok.get("calls"))
        if config.token_budget_per_run and tok.get("output", 0) and \
                (tok.get("input", 0) + tok.get("output", 0)) > config.token_budget_per_run:
            ctx.log.warn("token budget exceeded (soft)", budget=config.token_budget_per_run,
                         used=tok.get("input", 0) + tok.get("output", 0))
        lock.release()
        ctx.trace.close()
        ctx.log.close()
    return ctx
