"""Dev/ops: re-deliver an existing digest's interactive cards WITHOUT re-running the pipeline.

A1 self-prove: loads {date}.items.json, rebuilds the full Digest, and calls the dingtalk_card
channel's send() — which delivers ONE card per deep-read item, each numbered [N]+🆕/📚 to match the
markdown brief. Needs the DingTalk env creds set.

    set -a; . ./.env; set +a
    python scripts/deliver_cards.py 2026-06-26

Note: outTrackId is stable ({date}:{id}); an item already delivered keeps its old card (DingTalk
ignores cardData changes on a reused outTrackId). Not-yet-delivered items render fresh.
"""
from __future__ import annotations

import sys

from radar.core import registry
from radar.core.config import Paths, load_config
from radar.core.io import read_json
from radar.core.models import Digest, Item, RunContext, TimeWindow
from radar.obs import Logger, Tracer


def main(date: str) -> int:
    registry.load_adapters()
    config = load_config()
    if config.channels.dingtalk_card is None:
        from radar.core.config import DingtalkCardConfig
        config.channels.dingtalk_card = DingtalkCardConfig()   # enable via env only (no config.toml edit)
    raw = read_json(Paths.digests / f"{date}.items.json")
    if not raw:
        print(f"no digest items for {date} (data/digests/{date}.items.json)")
        return 1
    digest = Digest(date=date, items=[Item(**it) for it in raw])   # full list → correct [N] per card

    from radar.channels.dingtalk_card import DingtalkCardChannel, deep_read_items
    print(f"delivering 1 list card with {len(deep_read_items(digest))} rows for {date}")
    ctx = RunContext(run_id="a1-deliver", mode="daily", config=config, window=TimeWindow(48))
    ctx.log = Logger("a1-deliver", log_path=Paths.state / "radar.log", echo=True)
    ctx.trace = Tracer("a1-deliver")
    ok = DingtalkCardChannel().send(digest, ctx)
    ctx.log.close()
    print("✅ delivered — check DingTalk" if ok else "❌ delivery failed (see warnings above)")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else "2026-06-26"))
