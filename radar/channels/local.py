"""Local channel — always-on markdown archive (the tier-1 reliable delivery)."""
from __future__ import annotations

from ..core.config import Paths
from ..core.io import atomic_write_text
from ..core.models import Digest, RunContext
from ..core.ports import Channel
from ..core.registry import register


@register("channel", "local")
class LocalChannel(Channel):
    name = "local"

    def is_enabled(self, config) -> bool:
        return config.channels.local

    def send(self, digest: Digest, ctx: RunContext) -> bool:
        local = ctx.started_at.astimezone()
        out_dir = Paths.digests / local.strftime("%Y") / local.strftime("%m")
        path = out_dir / f"{digest.date}{'-weekly' if digest.kind == 'weekly' else ''}.md"
        atomic_write_text(path, digest.markdown)
        atomic_write_text(Paths.digests / "latest.md", digest.markdown)

        # maintain a simple reverse-chron index (dedup by date+kind)
        index = Paths.digests / "index.md"
        entry = f"- [{digest.date} · {digest.kind}]({path.relative_to(Paths.digests)}) — 精选 {len(digest.items)} 条"
        existing = [ln for ln in (index.read_text(encoding="utf-8").splitlines()
                                  if index.exists() else [])
                    if ln.strip() and f"[{digest.date} · {digest.kind}]" not in ln]
        atomic_write_text(index, "# Agent Radar 历史\n\n" + "\n".join([entry] + existing) + "\n")

        ctx.log.info("local archive saved", path=str(path.relative_to(Paths.root)))
        return True
