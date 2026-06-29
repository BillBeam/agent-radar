"""Dev/ops: re-deliver an existing digest's interactive cards WITHOUT re-running the pipeline.

A0 self-prove + A1 testing. Loads {date}.items.json, rebuilds the Digest, and calls the
dingtalk_card channel's send() (A0 = one card). Needs the DingTalk env creds set.

    DINGTALK_CLIENT_ID=... DINGTALK_CLIENT_SECRET=... DINGTALK_USER_ID=... \
    DINGTALK_CARD_TEMPLATE_ID=... DINGTALK_ROBOT_CODE=... \
    python scripts/deliver_cards.py 2026-06-26
"""
from __future__ import annotations

import sys

from radar.core import registry
from radar.core.config import Paths, load_config
from radar.core.io import read_json
from radar.core.models import Digest, Item, RunContext, TimeWindow
from radar.obs import Logger, Tracer


def main(date: str, index: int = 0) -> int:
    registry.load_adapters()
    config = load_config()
    if config.channels.dingtalk_card is None:
        from radar.core.config import DingtalkCardConfig
        config.channels.dingtalk_card = DingtalkCardConfig()   # enable via env only (no config.toml edit)
    raw = read_json(Paths.digests / f"{date}.items.json")
    if not raw:
        print(f"no digest items for {date} (data/digests/{date}.items.json)")
        return 1
    digest = Digest(date=date, items=[Item(**it) for it in raw])

    # pick the index-th deep-read item to deliver — a different item => a fresh outTrackId
    # ({date}:{id}), so re-testing renders a brand-new card instead of reusing the prior one
    from radar.channels.dingtalk_card import deep_read_items
    dr = deep_read_items(digest)
    if dr:
        pick = dr[index % len(dr)]
        digest.items = [pick]
        print(f"delivering deep-read item #{index % len(dr)}: {pick.title[:70]!r}")

    ctx = RunContext(run_id="a0-deliver", mode="daily", config=config, window=TimeWindow(48))
    ctx.log = Logger("a0-deliver", log_path=Paths.state / "radar.log", echo=True)
    ctx.trace = Tracer("a0-deliver")
    from radar.channels.dingtalk_card import DingtalkCardChannel
    ok = DingtalkCardChannel().send(digest, ctx)
    ctx.log.close()
    print("✅ delivered — check DingTalk" if ok else "❌ delivery failed (see warnings above)")
    return 0 if ok else 1


if __name__ == "__main__":
    _date = sys.argv[1] if len(sys.argv) > 1 else "2026-06-26"
    _idx = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    raise SystemExit(main(_date, _idx))
