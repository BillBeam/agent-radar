"""Deep-read stage — produces a grounded Chinese 详解 for the top items.

For each top item we fetch the real article text (deterministic) and pass ONLY that
to the LLM, which writes a structured Chinese explanation. Grounding the model in
fetched text (not its prior) is the anti-hallucination mechanism. Items whose text
can't be fetched degrade to title+link rather than fabricating. Runs concurrently.
"""
from __future__ import annotations

import hashlib
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from ..core.config import Paths
from ..core.io import atomic_write_json, read_json
from ..core.models import Item, RunContext
from ..core.ports import Stage
from ..core.registry import register
from ._article import fetch_article_text
from .critic import high_conf_skip

MIN_BASIS_CHARS = 200
NO_TEXT = "（原文正文未能获取，仅标题+链接可读）"


def _write_source_sidecar(ctx: RunContext, it: Item, source_text: str) -> None:
    """Persist the *exact* grounding text deepread fed the LLM, keyed by item id,
    so a later offline eval (P1 尺子) can judge whether the 详解 is faithful to it.

    Best-effort by design: deepread is the daily critical path, and this is only an
    eval aid — a failed write must NEVER interrupt the deep-read (cf. _write_last_run).
    """
    try:
        date = ctx.started_at.astimezone().strftime("%Y-%m-%d")
        atomic_write_json(
            Paths.deepread_sources / date / f"{it.id}.json",
            {"item_id": it.id, "url": it.url, "title": it.title,
             "source_text": source_text, "chars": len(source_text),
             "ts": datetime.now().astimezone().isoformat(timespec="seconds")},
        )
    except Exception as e:  # noqa: BLE001 — an eval-aid write must not break deepread
        ctx.log.warn("deepread sidecar write failed", id=it.id, error=repr(e)[:120])


@register("stage", "deepread")
class DeepReadStage(Stage):
    name = "deepread"

    def run(self, ctx: RunContext) -> None:
        if ctx.llm is None or not ctx.items:
            return
        system = Paths.prompts.joinpath("deepread.md").read_text(encoding="utf-8")
        prompt_fp = hashlib.sha1(system.encode("utf-8")).hexdigest()[:12]

        # critic gate: high-confidence obvious garbage yields its deepread slot to the
        # next-better item — deepread still does top_k (a quality SWAP, NOT a saving; opus
        # is only saved at the boundary when eligible < top_k). borderline (conf=low) stays.
        # FILTERS the deepread pool only — does NOT touch ctx.items, so synthesize keeps B's
        # order + [N] (the skipped item still shows in the brief, annotated 可跳过, no 详解).
        eligible = [it for it in ctx.items if not high_conf_skip(ctx, it)]
        critic_skipped = len(ctx.items) - len(eligible)
        top = eligible[: ctx.config.deepread_top_k]

        # checkpoint: a crashed/re-run deepread reuses already-done items (same id + same
        # prompt). Refining deepread.md changes prompt_fp → everything re-runs with the new
        # framework (mirrors the faithfulness eval's resume).
        date = ctx.started_at.astimezone().strftime("%Y-%m-%d")
        ckpt_path = Paths.deepread_ckpt / f"{date}.json"
        prior = read_json(ckpt_path) or {}
        carried = prior.get("items", {}) if prior.get("prompt_fp") == prompt_fp else {}
        ckpt = {"date": date, "prompt_fp": prompt_fp, "items": dict(carried)}
        ckpt_lock = threading.Lock()

        def work(it: Item) -> None:
            done = ckpt["items"].get(it.id)
            if done is not None:                          # resume: skip fetch + LLM
                it.full_text = done.get("full_text")
                it.explain_zh = done.get("explain_zh")
                ctx.bump("deepread.resumed")
                return
            fetched = ""
            try:
                fetched = fetch_article_text(it.url, config=ctx.config, max_chars=30000)
            except Exception as e:  # noqa: BLE001
                ctx.log.warn("deepread fetch failed", url=it.url, error=repr(e)[:120])
            it.full_text = fetched or None
            basis = ((it.summary or "") + "\n\n" + fetched).strip()
            if len(basis) < MIN_BASIS_CHARS:
                it.explain_zh = NO_TEXT
                ctx.bump("deepread.no_text")
            else:
                grounding = basis[:28000]   # the exact source text the LLM sees
                _write_source_sidecar(ctx, it, grounding)
                user = (f"标题: {it.title}\n来源: {it.source_name}\n链接: {it.url}\n\n"
                        f"原文:\n{grounding}")
                res = ctx.llm.complete(user, system=system,
                                       model=ctx.config.models.deepread, timeout=360, tag=self.name)
                if res.ok and res.text.strip():
                    it.explain_zh = res.text.strip()
                    ctx.bump("deepread.ok")
                else:
                    it.explain_zh = NO_TEXT
                    ctx.bump("deepread.failed")
                    ctx.log.warn("deepread llm failed", url=it.url, error=(res.error or "")[:120])
            with ckpt_lock:                               # checkpoint after each item (crash-resume)
                ckpt["items"][it.id] = {"explain_zh": it.explain_zh, "full_text": it.full_text}
                try:
                    atomic_write_json(ckpt_path, ckpt)
                except Exception:  # noqa: BLE001 — checkpoint must never break deepread
                    pass

        with ThreadPoolExecutor(max_workers=3) as pool:
            futs = [pool.submit(work, it) for it in top]
            for fut in as_completed(futs):
                try:
                    fut.result()
                except Exception as e:  # noqa: BLE001 — one item's crash mustn't abort the batch
                    ctx.log.warn("deepread item crashed", error=repr(e)[:120])

        ctx.log.info("deepread", attempted=len(top), ok=ctx.stats.get("deepread.ok", 0),
                     resumed=ctx.stats.get("deepread.resumed", 0),
                     no_text=ctx.stats.get("deepread.no_text", 0),
                     failed=ctx.stats.get("deepread.failed", 0), critic_skipped=critic_skipped)
