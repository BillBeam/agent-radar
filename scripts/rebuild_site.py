#!/usr/bin/env python
"""Rebuild the whole reading site (every day page + home/archive/stats) from data/ and
optionally deploy it — for design changes and backfills; the daily run does this
automatically via the web_reader channel.

    .venv/bin/python scripts/rebuild_site.py [--deploy]

Needs (env / .env): AGENT_RADAR_WEB_SECRET; --deploy adds CLOUDFLARE_API_TOKEN +
CLOUDFLARE_ACCOUNT_ID. Prints derived URLs only — never the secret.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from radar.channels._mermaid import mermaid_to_svg          # noqa: E402
from radar.channels._site import build_site                 # noqa: E402
from radar.core.config import load_config                   # noqa: E402
from radar.obs import Logger                                 # noqa: E402


def main() -> int:
    deploy = "--deploy" in sys.argv
    secret = os.environ.get("AGENT_RADAR_WEB_SECRET")
    if not secret:
        print("AGENT_RADAR_WEB_SECRET missing (load .env first)", file=sys.stderr)
        return 2
    cfg = load_config()
    wr = cfg.channels.web_reader
    r = wr.resolved() if wr else {}
    log = Logger("rebuild-site", echo=True)

    res = build_site(secret, vote_api=r.get("vote_api"), trigger_api=r.get("trigger_api"),
                     mermaid=mermaid_to_svg, log=log)
    del secret
    base = r.get("base_url") or ""
    print(f"built={len(res['built'])} skipped={res['skipped'] or '[]'}")
    print(f"home    → {base}{res['nav']['home']}")
    print(f"archive → {base}{res['nav']['archive']}")
    print(f"stats   → {base}{res['nav']['stats']}")
    for d, u in sorted(res["day_urls"].items())[-3:]:
        print(f"day {d} → {base}{u}")

    if deploy:
        from radar.channels.web_reader import deploy_site
        ok, detail = deploy_site(r.get("project_name") or "")
        print(f"deploy: {'OK' if ok else 'FAILED'} ({detail})")
        return 0 if ok else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
