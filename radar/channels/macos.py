"""macOS channel — desktop notification when a digest is ready."""
from __future__ import annotations

import json
import subprocess

from ..core.models import Digest, RunContext
from ..core.ports import Channel
from ..core.registry import register


@register("channel", "macos")
class MacOSChannel(Channel):
    name = "macos"

    def is_enabled(self, config) -> bool:
        return config.channels.macos

    def send(self, digest: Digest, ctx: RunContext) -> bool:
        title = "Agent Radar"
        subtitle = f"{digest.date} · 精选 {len(digest.items)} 条"
        msg = "今日前沿已就绪 — 开 /agent-radar 跟我讨论"
        # json.dumps gives safely-quoted AppleScript string literals
        script = (f"display notification {json.dumps(msg)} with title {json.dumps(title)} "
                  f"subtitle {json.dumps(subtitle)} sound name \"Glass\"")
        try:
            subprocess.run(["osascript", "-e", script], capture_output=True, timeout=10)
            return True
        except Exception as e:  # noqa: BLE001
            ctx.log.warn("macos notify failed", error=repr(e)[:120])
            return False
