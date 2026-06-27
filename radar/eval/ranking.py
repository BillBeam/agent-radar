"""Ranking eval — is the digest's order reasonable?

Two angles, deliberately weighted differently:

  PRIMARY — feedback pairwise accuracy (the real correctness signal, grows with use):
    of the user's 👍/👎 pairs, how often did the 👍 item appear ABOVE the 👎 item in
    what he actually saw (the display order)? This is the only signal that tracks
    "did we order it RIGHT *for him*." It's thin early on, so below MIN_PAIRS we report
    it as "not yet a signal" rather than a clean 0/50/100% that would read as a result.

  SECONDARY — independent-judge agreement, as a STABILITY DIAGNOSTIC, not a score:
    a judge re-ranks the items and we report Kendall tau vs the display order. Two things
    make it a real second opinion rather than a tautology: (1) the judge uses a DIFFERENT
    rubric than production rerank (transferable-value, see prompts/eval_rank.md), so high
    tau means the order is robust *across criteria*, not just "the model agreeing with
    itself"; (2) the judge sees the items in a NEUTRAL order (sorted by id) so the
    system's ranking can't anchor it (position bias).
    LOW tau is NORMAL when items are similar quality — it does NOT mean "wrong order".
    Do NOT optimize tau. And note the scope: tau judges the ORDER of the items shown,
    not whether the right items were SELECTED (selection has no ground truth — feedback
    over time is the closest signal for that).
"""
from __future__ import annotations

from typing import Any, Optional

from ..core.config import Paths
from ..llm._json import salvage_objects

# Below this many (👍,👎) pairs, pairwise accuracy is 0/50/100% noise, not a signal.
MIN_PAIRS = 10


# ---------------- primary: feedback pairwise accuracy ----------------
def feedback_pairwise(items: list[dict], feedback: dict) -> dict:
    """Display order = rank (items.json is persisted in the order he saw). For every
    (👍, 👎) pair, did the 👍 rank higher? Honest about thin samples."""
    rank = {it.get("id"): i for i, it in enumerate(items)}
    ups = [i for i, v in (feedback or {}).items()
           if isinstance(v, dict) and v.get("vote") == "up" and i in rank]
    downs = [i for i, v in (feedback or {}).items()
             if isinstance(v, dict) and v.get("vote") == "down" and i in rank]
    pairs = [(u, d) for u in ups for d in downs]
    correct = sum(1 for u, d in pairs if rank[u] < rank[d])
    n = len(pairs)
    is_signal = n >= MIN_PAIRS

    if n == 0:
        note = "暂无足够反馈（需 👍 和 👎 各至少一条）"
    elif not is_signal:
        note = f"样本太少（{n} 对 < {MIN_PAIRS}），暂不构成信号——0/50/100% 多为噪声"
    else:
        note = f"基于 {n} 对标记"

    return {
        "n_up": len(ups), "n_down": len(downs), "n_pairs": n,
        "correct_pairs": correct,
        "pairwise_accuracy": round(correct / n, 3) if n else None,
        "is_signal": is_signal, "min_pairs": MIN_PAIRS, "note": note,
    }


# ---------------- secondary: independent-judge stability diagnostic ----------------
def _kendall_tau(order_a: list, order_b: list) -> tuple[Optional[float], Optional[float]]:
    """Kendall tau-a + pairwise-agreement between two orderings of the same ids.
    +1 = identical order, -1 = reversed, 0 = uncorrelated. Pure Python (no scipy)."""
    rb = {x: i for i, x in enumerate(order_b)}
    ids = [x for x in order_a if x in rb]          # common ids, in A's order
    ra = {x: i for i, x in enumerate(ids)}
    n = len(ids)
    if n < 2:
        return None, None
    conc = disc = 0
    for i in range(n):
        for j in range(i + 1, n):
            s = (ra[ids[i]] - ra[ids[j]]) * (rb[ids[i]] - rb[ids[j]])
            if s > 0:
                conc += 1
            elif s < 0:
                disc += 1
    total = n * (n - 1) // 2
    return round((conc - disc) / total, 3), round(conc / total, 3)


def _item_brief(it: dict, idx: int) -> str:
    """Neutral content for the judge — title/source/tags + a content snippet. Deliberately
    NO score/reason/rank, so the system's own judgment can't leak into the second opinion."""
    content = (it.get("explain_zh") or it.get("summary") or "").strip().replace("\n", " ")
    tags = ", ".join(it.get("tags") or [])
    head = f"[{idx}] {it.get('title') or '(无标题)'}（来源 {it.get('source_name') or '?'}"
    head += f"；标签 {tags}）" if tags else "）"
    return head + (f"\n    {content[:300]}" if content else "")


def _parse_order(parsed: Any, raw: str, n: int) -> Optional[list]:
    """Extract a best-first index permutation from the judge. Robust to {"order":[...]},
    a bare list, or truncated/fenced output; appends any dropped indices at the end."""
    order = None
    if isinstance(parsed, dict) and isinstance(parsed.get("order"), list):
        order = parsed["order"]
    elif isinstance(parsed, list):
        order = parsed
    else:                                           # salvage from raw text
        objs = salvage_objects(raw or "")
        for o in objs:
            if isinstance(o.get("order"), list):
                order = o["order"]
                break
    if order is None:
        return None
    seen: list = []
    for x in order:
        xi = x.get("i") if isinstance(x, dict) else x
        if isinstance(xi, int) and 0 <= xi < n and xi not in seen:
            seen.append(xi)
    for i in range(n):                              # keep it a full ordering
        if i not in seen:
            seen.append(i)
    return seen


def independent_judge(items: list[dict], *, llm: Any, model: str,
                      system: Optional[str] = None) -> dict:
    """Re-rank by an independent rubric from a neutral order; report tau vs display order."""
    rankable = [it for it in items if it.get("id") and it.get("title")]
    if len(rankable) < 2:
        return {"n": len(rankable), "note": "条目太少，无法做排序诊断"}
    if system is None:
        system = Paths.prompts.joinpath("eval_rank.md").read_text(encoding="utf-8")

    neutral = sorted(rankable, key=lambda it: it.get("id"))   # break position bias
    listing = "\n".join(_item_brief(it, i) for i, it in enumerate(neutral))
    parsed, res = llm.complete_json(f"条目（顺序已打乱，不代表排名）：\n\n{listing}",
                                    system=system, model=model, timeout=180, retries=1)
    order = _parse_order(parsed, getattr(res, "text", "") or "", len(neutral))
    if order is None:
        return {"n": len(neutral), "error": (getattr(res, "error", None) or "unparseable")[:120]}

    judge_ids = [neutral[i].get("id") for i in order]
    display_ids = [it.get("id") for it in rankable]
    tau, agreement = _kendall_tau(display_ids, judge_ids)
    return {
        "n": len(neutral),
        "kendall_tau": tau,
        "pairwise_agreement": agreement,
        "rubric": "transferable-value (independent of production rerank)",
        "note": "稳定性/可复现性诊断，非正确性分；低 τ 常见于质量相近的条目，不代表排错",
        "low_n_caveat": len(neutral) < 5,
    }


def eval_ranking(items: list[dict], feedback: dict, *, llm: Any = None,
                 model: Optional[str] = None, system: Optional[str] = None) -> dict:
    """Primary feedback signal + (if an LLM is given) the independent-judge diagnostic."""
    out = {"feedback": feedback_pairwise(items, feedback)}
    out["independent_judge"] = (
        independent_judge(items, llm=llm, model=model, system=system) if llm else None
    )
    return out
