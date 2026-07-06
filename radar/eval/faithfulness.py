"""Faithfulness eval — is each 详解 grounded in the source the deep-read LLM saw?

Method (RAGAS-style, reference-free): a judge LLM decomposes the 详解 into atomic
claims and labels each `supported` / `unsupported` / `distorted` against the source
text — and our CODE computes the support_rate from those per-claim verdicts. We do
NOT ask the LLM for a holistic 1–5 score: counting in code is deterministic, stays
comparable across runs, and resists the well-documented LLM-judge *leniency* bias.

Grounding source, in order of fidelity:
  1. sidecar  — data/deepread_sources/{date}/{id}.json: the EXACT text deepread fed
                the LLM (written going forward; precise).
  2. full_text — already persisted in {date}.items.json (captured at deepread time);
                near-exact (misses the summary prefix, capped slightly differently),
                so historical digests written before sidecars existed can still be
                evaluated. Flagged so the report can caveat possible false positives.
  3. none      — no source / degraded 详解 → skipped (and counted, for honest coverage).

We never re-fetch the article: a fresh fetch could differ from what the LLM actually
saw, which would make the faithfulness verdict itself unfaithful.

Robustness (token-frugal, never silently fail):
  - every failed judge is classified (rate_limit / timeout / parse_error / llm_error)
    and surfaced — not collapsed into an opaque "failed".
  - on a rate-limit / overload, the run aborts the rest EARLY instead of grinding
    through doomed calls and burning the subscription window.
  - results carry a content hash; re-running reuses prior scored results for unchanged
    items (resume), so a partial failure never costs those tokens twice.
"""
from __future__ import annotations

import hashlib
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Optional

from ..core.config import Paths
from ..core.io import read_json
from ..llm._json import salvage_objects

# 详解 degrade marker emitted by deepread when no body could be fetched.
_DEGRADE_PREFIX = "（原文"
# mirror the deep-read grounding budget so the judge sees ~what the model saw
# (V5: deepread feeds 80K — a judge capped at the old 28K would false-flag every claim
# grounded in the back 2/3 of the source as unsupported)
_MAX_SOURCE_CHARS = 80000
# substrings that mean "the subscription/API limit was hit" → transient, abort early
_RATE_MARKERS = ("overload", "rate", "429", "529", "limit", "quota", "usage", "exhaust")


def resolve_grounding(item: dict, date: str) -> tuple[Optional[str], str]:
    """Return (source_text, label) where label ∈ {sidecar, full_text, none}."""
    sc = read_json(Paths.deepread_sources / date / f"{item.get('id')}.json")
    if isinstance(sc, dict) and sc.get("source_text"):
        return sc["source_text"], "sidecar"
    ft = item.get("full_text")
    if ft:
        return ft, "full_text"
    return None, "none"


def _skip_reason(item: dict, grounding_label: str) -> Optional[str]:
    """Why this item can't be faithfulness-judged, or None if it can."""
    explain = (item.get("explain_zh") or "").strip()
    if not explain:
        return "no_explain"
    if explain.startswith(_DEGRADE_PREFIX):
        return "degraded"            # deepread itself refused (no body) — nothing to judge
    if grounding_label == "none":
        return "no_source"           # have an explanation but lost the source text
    return None


def _classify_error(err: str) -> str:
    """Bucket an LLM failure so the report can say *why* a judge call failed."""
    e = (err or "").lower()
    if e.startswith("json parse"):
        return "parse_error"
    if e == "timeout" or "timeout" in e:
        return "timeout"
    if any(m in e for m in _RATE_MARKERS):
        return "rate_limit"
    return "llm_error"


def _content_key(source_text: Optional[str], item: dict, prompt_fp: str = "") -> str:
    """Stable hash of (source seen, 详解 judged, judge prompt) — re-judge only if any
    changed. Folding in the prompt fingerprint means tweaking the rubric busts the cache."""
    blob = (source_text or "") + "\x00" + (item.get("explain_zh") or "") + "\x00" + prompt_fp
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:16]


def _tally(claims: list[dict]) -> dict:
    """Reduce per-claim verdicts to counts. support_rate is supported / factual."""
    factual = [c for c in claims if c.get("type") == "factual"]
    supported = [c for c in factual if c.get("verdict") == "supported"]
    issues = [c for c in factual
              if c.get("verdict") in ("unsupported", "distorted")]
    commentary = [c for c in claims if c.get("type") == "commentary"]
    rate = round(len(supported) / len(factual), 3) if factual else None
    return {
        "n_factual": len(factual),
        "n_supported": len(supported),
        "n_commentary": len(commentary),
        "support_rate": rate,
        "issues": [{"claim": c.get("claim"), "verdict": c.get("verdict"),
                    "why": c.get("evidence")} for c in issues],
    }


def _salvage_claims(text: str) -> list[dict]:
    """Recover complete flat claim objects from a truncated/fenced response. The judge
    sometimes opens a ```json fence and gets cut off before closing it, so the whole
    object won't parse — but each claim is a flat {...} (no nesting), which salvage_objects
    can pull out individually. The dropped tail is just the one half-written claim."""
    return [c for c in salvage_objects(text or "")
            if isinstance(c, dict) and c.get("type") and c.get("verdict")]


def judge_item(llm: Any, item: dict, source_text: str, *,
               model: str, system: str) -> dict:
    """Run the judge on one item. Returns {"ok": True, **tally} on success, or
    {"ok": False, "error_kind": ..., "error": ...} on failure (never raises)."""
    user = (f"原文:\n{source_text[:_MAX_SOURCE_CHARS]}\n\n"
            f"=====\n\n详解:\n{(item.get('explain_zh') or '').strip()}")
    # generous ceiling: an 80K-char source under a throttled subscription is slow, and
    # the judge's per-claim output is large (V5 details carry more factual claims). Run
    # sequentially (max_workers=1) so each call gets full throughput rather than racing
    # siblings into the timeout. retries=1: a timed-out judge shouldn't retry 3× (wastes
    # tokens) — resume picks it up later.
    parsed, res = llm.complete_json(user, system=system, model=model, timeout=600, retries=1)
    raw = getattr(res, "text", "") or ""

    claims: Optional[list] = None
    if isinstance(parsed, dict) and isinstance(parsed.get("claims"), list):
        claims = parsed["claims"]
        note = parsed.get("note")
    else:
        # whole-parse failed. If it was a JSON/format problem (not a call failure),
        # try to salvage the complete claim objects from the raw text.
        err = getattr(res, "error", None) or ""
        kind = _classify_error(err) if parsed is None else "parse_error"
        if kind == "parse_error":     # truncated/fenced output — try to salvage flat claims
            salvaged = _salvage_claims(raw)
            if salvaged:
                claims, note = salvaged, "(salvaged from truncated/fenced output)"
        if claims is None:
            return {"ok": False, "error_kind": kind, "error": (err or raw)[:200]}

    tally = _tally(claims)
    tally["note"] = note
    tally["salvaged"] = note is not None and "salvaged" in note
    tally["ok"] = True
    return tally


def _aggregate(per_item: list, n_total: int, model: str, rate_limited: bool) -> dict:
    """Build the faithfulness summary from per-item results (None = not yet judged)."""
    done = [r for r in per_item if r is not None]
    scored = [r for r in done if r.get("status") == "scored"]
    rates = [r["support_rate"] for r in scored]
    skips = Counter(r["skip_reason"] for r in done if r.get("status") == "skipped")
    return {
        "model": model,
        "n_total": n_total,
        "n_scored": len(scored),
        "n_no_factual": sum(1 for r in done if r.get("status") == "no_factual"),
        "n_skipped": sum(1 for r in done if r.get("status") == "skipped"),
        "n_reused": sum(1 for r in done if r.get("cached")),
        "mean_support_rate": round(sum(rates) / len(rates), 3) if rates else None,
        "n_issues": sum(len(r.get("issues", [])) for r in scored),
        "skip_breakdown": dict(skips),
        "rate_limited": rate_limited,
        "items": done,
    }


def eval_faithfulness(llm: Any, items: list[dict], date: str, *,
                      model: str, system: Optional[str] = None,
                      max_workers: int = 1, prior: Optional[dict] = None,
                      checkpoint: Optional[Any] = None) -> dict:
    """Judge every gradable 详解 and aggregate. Reports honest coverage
    (mean_support_rate over SCORED items only) and a failure breakdown.

    `prior` = a previous run's faithfulness dict; scored/no-factual items whose content
    is unchanged are reused (resume) so re-runs don't re-spend tokens. `checkpoint`, if
    given, is called with the partial aggregate after each item so a killed/throttled run
    never loses completed judgments. Runs sequentially by default — concurrent sonnet
    calls starve each other under a throttled subscription and hit the timeout; a
    rate-limit aborts the remaining items early."""
    if system is None:
        system = Paths.prompts.joinpath("eval_faithfulness.md").read_text(encoding="utf-8")
    prompt_fp = hashlib.sha1(system.encode("utf-8")).hexdigest()[:8]

    reusable = {}
    for r in (prior or {}).get("items", []):
        if r.get("status") in ("scored", "no_factual") and r.get("key"):
            reusable[(r.get("id"), r["key"])] = r

    rate_limited = threading.Event()

    def assess(item: dict) -> dict:
        text, label = resolve_grounding(item, date)
        base = {"id": item.get("id"), "title": item.get("title"),
                "grounding_source": label}

        reason = _skip_reason(item, label)
        if reason is not None:
            return {**base, "status": "skipped", "skip_reason": reason}

        key = _content_key(text, item, prompt_fp)
        cached = reusable.get((item.get("id"), key))
        if cached is not None:
            return {**cached, "grounding_source": label, "cached": True}

        if rate_limited.is_set():        # a sibling already hit the limit — don't waste the call
            return {**base, "status": "skipped", "skip_reason": "aborted_rate_limit"}

        result = judge_item(llm, item, text or "", model=model, system=system)
        if not result["ok"]:
            if result["error_kind"] == "rate_limit":
                rate_limited.set()       # stop the rest of the batch
            return {**base, "status": "skipped", "skip_reason": result["error_kind"],
                    "error": result.get("error")}

        tally = {k: v for k, v in result.items() if k != "ok"}
        status = "scored" if tally["support_rate"] is not None else "no_factual"
        return {**base, "status": status, "key": key,
                "grounding_chars": len(text or ""), **tally}

    results: list = [None] * len(items)               # keep item order regardless of finish order
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futs = {pool.submit(assess, item): i for i, item in enumerate(items)}
        for fut in as_completed(futs):
            results[futs[fut]] = fut.result()
            if checkpoint is not None:                 # persist progress after every item
                try:
                    checkpoint(_aggregate(results, len(items), model, rate_limited.is_set()))
                except Exception:  # noqa: BLE001 — checkpointing must never break the eval
                    pass

    return _aggregate(results, len(items), model, rate_limited.is_set())
