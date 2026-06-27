"""Offline eval entry — `radar --mode eval [date]`.

Reads a past day's products (`{date}.items.json`, `{date}.json` feedback) plus the
grounding source, scores quality, and writes a comparable report to data/eval/{date}.json.
This is a measurement tool — it never runs the daily pipeline and never mutates a digest.

Block ① wires faithfulness; ranking (②) and the polished top-line report (③) extend this.
"""
from __future__ import annotations

from typing import Any, Optional

from ..core.config import Paths, RadarConfig, load_config
from ..core.io import atomic_write_json, read_json
from .faithfulness import eval_faithfulness

EVAL_SCHEMA_VERSION = 1   # bump if the report shape changes (keeps cross-run reports comparable)


def run_eval(date: str, *, llm: Any, config: Optional[RadarConfig] = None) -> Optional[dict]:
    """Evaluate the digest produced on `date`. Returns the report dict (also persisted),
    or None if there's no digest for that date."""
    config = config or load_config()
    items = read_json(Paths.digests / f"{date}.items.json")
    if not items:
        print(f"no digest for {date} — nothing to eval "
              f"(looked for data/digests/{date}.items.json)")
        return None

    # resume: reuse a prior run's scored items (unchanged content) so a re-run after a
    # partial/throttled failure never re-spends those tokens.
    prior = read_json(Paths.eval / f"{date}.json")
    prior_faith = prior.get("faithfulness") if isinstance(prior, dict) else None

    def _persist(faith_partial: dict) -> None:
        """Checkpoint after every item — a killed/throttled run keeps its progress."""
        atomic_write_json(Paths.eval / f"{date}.json", {
            "schema_version": EVAL_SCHEMA_VERSION, "date": date,
            "n_items": len(items), "faithfulness": faith_partial,
        })

    faith = eval_faithfulness(llm, items, date, model=config.models.judge,
                              prior=prior_faith, checkpoint=_persist)

    report = {
        "schema_version": EVAL_SCHEMA_VERSION,
        "date": date,
        "n_items": len(items),
        "faithfulness": faith,
    }
    atomic_write_json(Paths.eval / f"{date}.json", report)
    _print_faithfulness(date, faith)
    print(f"\nfull report → data/eval/{date}.json")
    return report


def _pct(x: Optional[float]) -> str:
    return "—" if x is None else f"{x * 100:.0f}%"


def _print_faithfulness(date: str, f: dict) -> None:
    """Minimal readable summary (Block ③ adds the polished top-line + markdown)."""
    print(f"\n=== eval {date} · 忠实度 ===")
    reused = f"，复用 {f['n_reused']}" if f.get("n_reused") else ""
    print(f"support_rate 均值 {_pct(f['mean_support_rate'])} "
          f"（基于 {f['n_scored']}/{f['n_total']} 篇有原文且有事实陈述的；"
          f"跳过 {f['n_skipped']}，无事实陈述 {f['n_no_factual']}{reused}）；"
          f"标记问题 {f['n_issues']} 处")
    if f.get("skip_breakdown"):
        print("  跳过明细：" + "，".join(f"{k}×{v}" for k, v in f["skip_breakdown"].items()))
    # loud banner when the subscription window was hit — the run is incomplete, not bad
    if f.get("rate_limited"):
        print("  ⚠ 撞到额度/限流，已提前停手——剩余未评。额度恢复后重跑会自动续上"
              "（已评的走缓存、不重花 token）。")

    for r in f["items"]:
        if r["status"] == "scored":
            tag = "✓" if not r["issues"] else f"⚠{len(r['issues'])}"
            cached = " (缓存)" if r.get("cached") else ""
            print(f"  [{r['grounding_source']:9}] {tag} {_pct(r['support_rate'])}  "
                  f"{(r.get('title') or '')[:50]}{cached}")
        else:
            why = r.get("skip_reason") or r["status"]
            print(f"  [{'—':9}] · {why:16} {(r.get('title') or '')[:50]}")

    # surface flagged issues so they can be eyeballed for false positives
    flagged = [(r, c) for r in f["items"] if r["status"] == "scored" for c in r.get("issues", [])]
    if flagged:
        print("\n  标记的问题（读时当「候选」，注意 full_text 近似可能有假阳性）：")
        for r, c in flagged:
            print(f"   • [{(r.get('title') or '')[:36]}] {c['verdict']}: {c['claim']}")
            if c.get("why"):
                print(f"       ↳ {c['why']}")
