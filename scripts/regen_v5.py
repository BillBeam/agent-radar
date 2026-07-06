"""Regenerate ONE archived day's 详解 with the current (V5) deepread — offline, no fetch stage.

Why this exists: V5 (教学级完整深读) shipped after 2026-07-05 was delivered; the acceptance run
regenerates that real day's 10 items with opus + full-text grounding + diagrams, then redeploys
the SAME reading-page URL (seg is date-derived → idempotent). Nothing upstream is re-decided:
items, order, [N] numbers, critic verdicts all come from the archived run.

Safety rails:
- [N]-order preservation is ASSERTED: the regenerated items.json must keep the exact id order of
  the original (feedback / `radar mark` map by display order). Mismatch → restore + abort.
- The original artifacts (digest md / items.json / sidecars / eval) are backed up once to
  data/real-llm-runs/v5-regen-<date>/ before anything is overwritten.
- Grounding fetch falls back to the archived full_text when a live re-fetch comes back shorter
  (pages move/rot between the original run and the regen).
- deepread's own per-item checkpoint makes this resumable mid-way (额度中断兜底).

Usage:
  python scripts/regen_v5.py 2026-07-05 --only 1,8      # dev pair: deepread only, no re-render
  python scripts/regen_v5.py 2026-07-05                 # full: deepread all → synthesize → archive
  python scripts/regen_v5.py 2026-07-05 --deploy        # …and redeploy the reading page (needs env creds)
"""
from __future__ import annotations

import argparse
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from radar.core import registry                                    # noqa: E402
from radar.core.config import Paths, load_config                   # noqa: E402
from radar.core.io import read_json                                # noqa: E402
from radar.core.models import Item, RunContext, TimeWindow         # noqa: E402
from radar.core.runner import build_llm                            # noqa: E402
from radar.obs import Logger, Tracer                               # noqa: E402


def _parse_header(md: str) -> dict:
    """Recover the deterministic header/footer numbers from the archived digest so the
    regenerated page keeps the original truthful stats (we re-run NO upstream stage)."""
    out = {"sources": 0, "candidates": 0, "fresh": 0, "backfill": 0, "skipped_seen": 0,
           "below_threshold": 0, "blocked": 0}
    m = re.search(r"扫描 (\d+) 源 · 候选 (\d+) · 今日新增 (\d+)(?: · 首次收录 (\d+))? · 跳过已读 (\d+)", md)
    if m:
        out.update(sources=int(m.group(1)), candidates=int(m.group(2)), fresh=int(m.group(3)),
                   backfill=int(m.group(4) or 0), skipped_seen=int(m.group(5)))
    f = re.search(r"淘汰低于阈值 (\d+)、噪声 (\d+)", md)
    if f:
        out.update(below_threshold=int(f.group(1)), blocked=int(f.group(2)))
    return out


def _backup_once(date: str, md_path: Path, items_path: Path) -> Path:
    # under local/ (gitignored): the backup carries FULL digests/sidecars — never commit
    dst = Paths.data / "real-llm-runs" / "local" / f"v5-regen-{date}"
    if dst.exists():
        return dst
    dst.mkdir(parents=True)
    for src, name in [(md_path, f"v4-{date}.md"), (items_path, f"v4-{date}.items.json"),
                      (Paths.eval / f"{date}.json", f"v4-eval-{date}.json"),
                      (Paths.eval / f"{date}.md", f"v4-eval-{date}.md")]:
        if src.exists():
            shutil.copy2(src, dst / name)
    side = Paths.deepread_sources / date
    if side.exists():
        shutil.copytree(side, dst / "v4-sidecars")
    print(f"[backup] V4 artifacts → {dst.relative_to(Paths.root)}")
    return dst


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("date")
    ap.add_argument("--only", help="comma-separated display numbers (dev mode: deepread only)")
    ap.add_argument("--deploy", action="store_true", help="redeploy the reading page after render")
    args = ap.parse_args()
    date = args.date

    registry.load_adapters()
    config = load_config()
    md_path = Paths.digests / date[:4] / date[5:7] / f"{date}.md"
    items_path = Paths.digests / f"{date}.items.json"
    if not md_path.exists() or not items_path.exists():
        print(f"missing archive for {date} ({md_path} / {items_path})")
        return 1
    original_md = md_path.read_text(encoding="utf-8")
    original_items_raw = read_json(items_path)
    original_ids = [d["id"] for d in original_items_raw]
    hdr = _parse_header(original_md)
    items = [Item.model_validate(d) for d in original_items_raw]
    print(f"[load] {len(items)} items · header {hdr}")

    _backup_once(date, md_path, items_path)

    run_id = f"{datetime.now():%Y%m%d-%H%M%S}-v5regen"
    ctx = RunContext(
        run_id=run_id, mode="daily", config=config, window=TimeWindow(48),
        started_at=datetime.fromisoformat(f"{date}T08:30:00+08:00"),
    )
    ctx.log = Logger(run_id, log_path=Paths.state / "radar.log")
    ctx.trace = Tracer(run_id, trace_path=Paths.trace / f"{run_id}.jsonl")
    ctx.llm = build_llm(config, ctx.log, ctx.trace)
    ctx.sources = list(range(hdr["sources"]))            # synthesize only uses len()
    ctx.stats["funnel"] = {"candidates": hdr["candidates"],
                           "below_threshold": hdr["below_threshold"], "blocked": hdr["blocked"]}
    ctx.stats["skipped_seen"] = hdr["skipped_seen"]
    critic = read_json(Paths.critic / f"{date}.json") or {}
    ctx.stats["critic"] = {r["id"]: {"skip": r.get("skip", False), "conf": r.get("conf", "low"),
                                     "why": r.get("why", "")}
                           for r in critic.get("items", [])}

    # freshness is a NOW-relative predicate — days later everything would flip to 📚 and
    # renumber. Pin the original 🆕/📚 split (fresh = the first `fresh` items of the
    # canonical order) so grouping, [N] and items.json stay byte-identical in order.
    fresh_ids = {it.id for it in items[: hdr["fresh"]]}
    from radar.stages import synthesize as syn_mod
    syn_mod.is_display_fresh = lambda it: it.id in fresh_ids

    # live re-fetch may rot between the original run and now — never trade DOWN: keep the
    # archived full_text when it is the longer grounding.
    from radar.stages import deepread as dr_mod
    stored = {it.url: (it.full_text or "") for it in items}
    orig_fetch = dr_mod.fetch_article_text

    def fetch_with_archive_fallback(url, config=None, max_chars=dr_mod.FETCH_CAP):
        try:
            live = orig_fetch(url, config=config, max_chars=max_chars) or ""
        except Exception:  # noqa: BLE001
            live = ""
        return live if len(live) >= len(stored.get(url, "")) else stored[url]

    dr_mod.fetch_article_text = fetch_with_archive_fallback

    if args.only:
        picks = {int(n) for n in args.only.split(",")}
        ctx.items = [it for n, it in enumerate(items, 1) if n in picks]
        print(f"[dev] deepread only on {sorted(picks)} — no re-render, checkpoint accumulates")
    else:
        ctx.items = items

    dr_mod.DeepReadStage().run(ctx)
    for n, it in enumerate(items, 1):
        if it in ctx.items:
            ln = len(it.explain_zh or "")
            nmm = (it.explain_zh or "").count("```mermaid")
            ntb = (it.explain_zh or "").count("|---")
            print(f"  [{n}] 详解 {ln:>6} chars · mermaid×{nmm} · 表×{ntb} · {it.title[:46]}")

    if args.only:
        print("[dev] done (deepread only).")
        return 0

    syn_mod.SynthesizeStage().run(ctx)
    new_ids = [d["id"] for d in read_json(items_path)]
    if new_ids != original_ids:                          # [N] must keep mapping to the same item
        (items_path).write_text(__import__("json").dumps(original_items_raw, ensure_ascii=False),
                                encoding="utf-8")
        print("[ABORT] display order changed — items.json restored, nothing deployed.")
        return 1
    print(f"[order] [N]→id mapping preserved ({len(new_ids)} items)")

    registry.get("channel", "local")().send(ctx.digest, ctx)
    if args.deploy:
        ok = registry.get("channel", "web_reader")().send(ctx.digest, ctx)
        print(f"[deploy] ok={ok} url={ctx.stats.get('reader_url')}")

    u = getattr(ctx.llm, "usage_total", {})
    print(f"[tokens] {u}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
