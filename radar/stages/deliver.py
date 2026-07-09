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

# web_reader = full 详解 → a CF Pages reading page; it runs BEFORE dingtalk_card so its per-day URL
# lands in ctx.stats["reader_url"] and the card's per-row link can point at …/<seg>/#item-N (falling
# back to arxiv if web delivery is off/failed). dingtalk_card = per-item 👍/👎 voting layer;
# dingtalk_file = full 详解 as a docx file (older reading layer, auto-suppressed when web_reader is on).
# dingtalk (group markdown) is off by default (no webhook). All independently gated by is_enabled().
CHANNEL_ORDER = ["dingtalk", "web_reader", "dingtalk_card", "dingtalk_file", "local", "macos"]
SEEN_RETENTION_DAYS = 60

# Channels that actually put the digest in front of the user. `local` is an archive on this
# Mac and `macos` is a desktop notification behind a closed lid — neither means "he read it".
# Marking items seen on those alone burns them: they are dropped from every future candidate
# pool and can never be pushed again. That is what happened on 2026-07-08 — both remote
# channels failed (wrangler timeout + DingTalk DNS), `local` succeeded, and the day's 10 items
# were retired without ever reaching the phone. An item is "seen" only once it has left the box.
REMOTE_CHANNELS = ("dingtalk", "web_reader", "dingtalk_card", "dingtalk_file")


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

        # Dedup bookkeeping. Retire items ONLY when a remote channel actually took them (see
        # REMOTE_CHANNELS). If every remote channel failed — a dead network, an expired token —
        # leave the items unseen so the next run pushes them again; a re-push is cheap, a
        # silently retired item is gone for good. When no remote channel is configured at all,
        # the local archive IS the delivery and marks seen as before.
        remote = [c for c in REMOTE_CHANNELS if c in results]
        if remote:
            delivered = any(results[c] for c in remote)
            if not delivered:
                ctx.stats["seen_withheld"] = len(ctx.digest.items)
                ctx.log.warn("nothing reached a remote channel — items stay unseen for a retry",
                             items=len(ctx.digest.items), results=results)
        else:
            delivered = bool(results.get("local"))
        if delivered:
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
