"""Deep-read stage — produces a grounded Chinese 详解 for the top items.

For each top item we fetch the real article text (deterministic) and pass ONLY that
to the LLM, which writes a structured Chinese explanation. Grounding the model in
fetched text (not its prior) is the anti-hallucination mechanism. Items whose text
can't be fetched degrade to title+link rather than fabricating. Runs concurrently.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from ..core.config import Paths
from ..core.io import atomic_write_json
from ..core.models import Item, RunContext
from ..core.ports import Stage
from ..core.registry import register
from ._article import fetch_article_text

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
        top = ctx.items[: ctx.config.deepread_top_k]
        system = Paths.prompts.joinpath("deepread.md").read_text(encoding="utf-8")

        def work(it: Item) -> None:
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
                return
            grounding = basis[:28000]   # the exact source text the LLM sees
            _write_source_sidecar(ctx, it, grounding)
            user = (f"标题: {it.title}\n来源: {it.source_name}\n链接: {it.url}\n\n"
                    f"原文:\n{grounding}")
            res = ctx.llm.complete(user, system=system,
                                   model=ctx.config.models.deepread, timeout=360)
            if res.ok and res.text.strip():
                it.explain_zh = res.text.strip()
                ctx.bump("deepread.ok")
            else:
                it.explain_zh = NO_TEXT
                ctx.bump("deepread.failed")
                ctx.log.warn("deepread llm failed", url=it.url, error=(res.error or "")[:120])

        with ThreadPoolExecutor(max_workers=3) as pool:
            list(pool.map(work, top))

        ctx.log.info("deepread", attempted=len(top), ok=ctx.stats.get("deepread.ok", 0),
                     no_text=ctx.stats.get("deepread.no_text", 0),
                     failed=ctx.stats.get("deepread.failed", 0))
