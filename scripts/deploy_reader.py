"""Deploy (or re-deploy) ONE day's 详解 reading page to Cloudflare Pages, straight from the local
archive — no pipeline re-run, no LLM. Handy for first-time verification and back-filling old days.

Reads creds from ENV (CLOUDFLARE_API_TOKEN / CLOUDFLARE_ACCOUNT_ID / AGENT_RADAR_WEB_SECRET); the web
secret stays in YOUR env — this script NEVER prints it, only the resulting …/<seg>/ URL (a one-way
capability token). Run it in your own shell so the secret never leaves your machine.

Usage:  python scripts/deploy_reader.py 2026-06-30
Prereq: `[channels.web_reader] project_name = "…"` in config.toml (or CLOUDFLARE_PAGES_PROJECT in env),
        and a Pages project that exists — create once with:
          npx wrangler pages project create <name> --production-branch main
"""
import sys
from pathlib import Path

from radar.channels.web_reader import WebReaderChannel
from radar.core.config import Paths, WebReaderConfig, load_config
from radar.core.models import Digest, RunContext, TimeWindow
from radar.obs import Logger, Tracer

if len(sys.argv) != 2:
    print(__doc__)
    raise SystemExit(2)
date = sys.argv[1]

md_path = Paths.digests / date[:4] / date[5:7] / f"{date}.md"
if not md_path.exists():
    print(f"no archived digest at {md_path} — run a daily for {date} first, or pick a date under data/digests/.")
    raise SystemExit(1)
md = md_path.read_text(encoding="utf-8")

config = load_config()
if config.channels.web_reader is None:                 # allow running with creds/project purely from env
    config.channels.web_reader = WebReaderConfig()
ctx = RunContext(run_id="deploy-reader", mode="daily", config=config, window=TimeWindow(48))
ctx.log, ctx.trace = Logger("deploy-reader", echo=True), Tracer("deploy-reader")

ok = WebReaderChannel().send(Digest(date=date, items=[], markdown=md), ctx)
print(f"\ndeployed = {ok}")
if ok:
    print(f"reader_url = {ctx.stats.get('reader_url')}")
    print("→ open that URL on your phone. In a real daily, each voting-card row links to its #item-N there.")
else:
    print("deploy failed — see the warn logs above (missing env? project not created yet? npx/network?).")
