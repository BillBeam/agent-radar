"""Offline eval entry — `radar --mode eval [date]`.

Reads a past day's products (`{date}.items.json`, `{date}.json` feedback) plus the
grounding source, scores quality, and writes a comparable report to data/eval/{date}.json
(+ a readable .md). This is a measurement tool — it never runs the daily pipeline and
never mutates a digest. Rendering lives in report.py; this module just orchestrates.
"""
from __future__ import annotations

from typing import Any, Optional

from ..core.config import Paths, RadarConfig, load_config
from ..core.io import atomic_write_json, read_json
from . import report
from .faithfulness import eval_faithfulness
from .ranking import eval_ranking

EVAL_SCHEMA_VERSION = 1   # bump if the report shape changes (keeps cross-day reports comparable)


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

    # ranking: feedback pairwise (primary) + independent-judge stability diagnostic (one
    # cheap LLM call). faithfulness above is fully resumed from cache on a re-run, so the
    # marginal cost of re-running eval is mostly this single judge call.
    feedback = read_json(Paths.feedback / f"{date}.json", {})
    ranking = eval_ranking(items, feedback if isinstance(feedback, dict) else {},
                           llm=llm, model=config.models.judge)

    report_dict = {
        "schema_version": EVAL_SCHEMA_VERSION,
        "date": date,
        "n_items": len(items),
        "faithfulness": faith,
        "ranking": ranking,
    }
    atomic_write_json(Paths.eval / f"{date}.json", report_dict)
    report.emit(date, report_dict)                 # console top-line + sections, writes .md
    print(f"\n完整报告 → data/eval/{date}.json + data/eval/{date}.md")
    return report_dict


def run_trend() -> int:
    """`radar --mode eval` with no date — aggregate recent days into a trend table."""
    return report.print_trend(EVAL_SCHEMA_VERSION)
