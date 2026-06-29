"""Deliver stage — send the digest to every enabled channel, isolate failures,
then mark delivered items as seen (so they're never pushed twice)."""
from __future__ import annotations

from datetime import datetime, timedelta

from ..core import registry
from ..core.config import Paths
from ..core.io import atomic_write_json, read_json
from ..core.models import RunContext
from ..core.ports import Stage
from ..core.registry import register

# dingtalk = markdown reading layer (sent first), dingtalk_card = per-item 👍/👎 voting layer
# (sent right after); they line up via [N]. Both are independently gated by is_enabled().
CHANNEL_ORDER = ["dingtalk", "dingtalk_card", "local", "macos"]
SEEN_RETENTION_DAYS = 60


@register("stage", "deliver")
class DeliverStage(Stage):
    name = "deliver"

    def run(self, ctx: RunContext) -> None:
        if not ctx.digest:
            ctx.log.warn("no digest to deliver")
            return
        results: dict[str, bool] = {}
        for cname in CHANNEL_ORDER:
            channel = registry.get("channel", cname)()
            if not channel.is_enabled(ctx.config):
                continue
            try:
                results[cname] = channel.send(ctx.digest, ctx)
            except Exception as e:  # noqa: BLE001 — one channel must not break others
                results[cname] = False
                ctx.log.warn("channel failed", channel=cname, error=repr(e)[:140])
        ctx.stats["delivered"] = results

        # dedup bookkeeping: only mark seen if it actually went somewhere durable
        if results.get("local") or any(results.values()):
            self._mark_seen(ctx)
        ctx.log.info("delivered", results=results)

    def _mark_seen(self, ctx: RunContext) -> None:
        seen = read_json(Paths.seen_json, {})
        if not isinstance(seen, dict):
            seen = {}
        date = ctx.started_at.astimezone().strftime("%Y-%m-%d")
        for it in ctx.digest.items:
            seen[it.id] = date
        cutoff = (datetime.now() - timedelta(days=SEEN_RETENTION_DAYS)).strftime("%Y-%m-%d")
        seen = {k: v for k, v in seen.items() if v >= cutoff}
        atomic_write_json(Paths.seen_json, seen)
        ctx.bump("marked_seen", len(ctx.digest.items))
