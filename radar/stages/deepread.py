"""Deep-read stage — produces a grounded Chinese 详解 for the top items.

For each top item we fetch the real article text (deterministic) and pass ONLY that
to the LLM, which writes a structured Chinese explanation. Grounding the model in
fetched text (not its prior) is the anti-hallucination mechanism. Items whose text
can't be fetched degrade to title+link rather than fabricating. Runs concurrently.

V5 (完整教学级深读): every digest item gets a full 详解 — deepread_top_k == daily_max_items,
the model is pinned to opus, and the WHOLE fetched body is fed (GROUNDING_CAP == 80K; the
old 28K budget threw away 65% of what we fetched and was the direct cause of the 07-05
"关键结果表在缺失段" self-reports). Critic verdicts are annotation-only (brief/reading-page
⚠️ label) — they no longer gate deepread: the user decided 每一篇都要详解.

Slot policy (7.3 复盘, still live for the eligible > top_k case): the top_k slots go to the
highest-ranked FULLY-GROUNDED items — an arXiv item whose full text couldn't really be
fetched (abstract-page fallback / ar5iv-redirect stub) yields its slot to the next
fully-grounded item. With V5's top_k covering every item this naturally never triggers on a
normal daily; thin items are still deep-read, with an explicit 「源材料薄」 note injected so
the prompt's honest-brevity mode kicks in deterministically.
"""
from __future__ import annotations

import hashlib
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from ..core.config import Paths
from ..core.io import atomic_write_json, read_json
from ..core.models import Item, RunContext
from ..core.ports import Stage
from ..core.registry import register
from ._article import fetch_article_text
from ._arxiv import arxiv_id_from_url

MIN_BASIS_CHARS = 200
NO_TEXT = "（原文正文未能获取，仅标题+链接可读）"
GROUNDING_CAP = 80000     # V5: feed the WHOLE fetched body (≈20–30K tokens — opus 窗口绰绰有余);
                          # the old 28K budget discarded 65% of an 80K fetch
FETCH_CAP = 120000        # fetch headroom beyond the grounding budget so smart truncation
                          # (tail-section cut → head+tail keep) decides what fits — NOT a blind
                          # head-cut at fetch time (07-05: [3] hit the old 80K fetch cap exactly,
                          # losing its ending before truncation could preserve it)
LLM_TIMEOUT = 1200        # V5 详解是长产出；07-06 首跑实测每篇 115–819s（77.5K grounding 的
                          # 大部头 819s 距 900s 只剩 10% 余量）→ 1200 给慢时段留空间，
                          # 超时只在失败路径上才有代价。真实耗时进 trace(deepread_item)
THIN_NOTE_CHARS = 2500    # ANY grounding below this gets the thin note (not just arXiv stubs) —
                          # 07-06 尺子实锤：两条 release 短页(1.2K/1.6K)没吃到提示，opus 用
                          # 背景知识补「它是什么/各包干什么」→ 忠实度 60/73%（真但不在原文=违约）
_THIN_NOTE = ("〔源材料提示〕本篇只拿到较薄的源材料（可能仅条目列表/摘要级内容）。"
              "按「源材料薄」规则诚实简短讲解，绝不注水；尤其**绝不用你自己的背景知识去补"
              "「这个东西是什么/它能干什么/它的定位」**——原文没描述的功能、性质、背景，"
              "一律不写，或明说「原文未描述」。原文列表里有什么就讲什么。\n\n")
THIN_ARXIV_CHARS = 8000   # an arXiv "full text" below this is an abstract page / redirect
                          # stub, not the paper (abs pages extract to ~4-6K; real bodies ≥ ~12K)
_ELISION = "\n\n〔……原文过长，中段截断，以下为结尾部分……〕\n\n"
# tail sections that carry no grounding value, matched as their own (optionally numbered)
# heading line — only searched in the back part of the text (see _cut_tail_sections)
_TAIL_HEAD = re.compile(
    r"^\s{0,8}(?:[0-9]{1,2}[.\s)]{0,3}|[A-D][.\s)]{1,3})?"
    r"(references|bibliography|acknowledg\w*|appendix|appendices)\s*$",
    re.I | re.M,
)


def _cut_tail_sections(text: str) -> str:
    """Drop everything from the first tail-section heading in the back 60% of the text
    (references live at the end; the same word early in the body is prose, not the section)."""
    m = _TAIL_HEAD.search(text, int(len(text) * 0.4))
    return text[: m.start()].rstrip() if m else text


def _snap_end(text: str, limit: int) -> str:
    """text[:limit] pulled back to the nearest paragraph/sentence boundary (never
    mid-sentence). Falls back to the hard cut if no boundary lives in the last 40%."""
    piece = text[:limit]
    for sep in ("\n\n", "。", ". ", "\n"):
        i = piece.rfind(sep)
        if i >= int(limit * 0.6):
            return piece[: i + len(sep)].rstrip()
    return piece


def _snap_start(text: str, pos: int) -> str:
    """text[pos:] advanced to the next boundary so the kept tail starts clean."""
    window_end = pos + int((len(text) - pos) * 0.4)
    for sep in ("\n\n", "。", ". ", "\n"):
        i = text.find(sep, pos)
        if i != -1 and i < window_end:
            return text[i + len(sep):].lstrip()
    return text[pos:]


def smart_grounding(basis: str, cap: int = GROUNDING_CAP) -> str:
    """Fit the grounding into the budget WITHOUT the old mid-sentence hard cut (7.3 复盘:
    `[:28000]` chopped [2]/[8] mid-word and lost their results/conclusions).
    1) fits → unchanged; 2) drop tail sections (references/appendix/acknowledgments);
    3) still over → keep head (~70%, intro/method) + tail (~30%, results/conclusion) around
    an explicit elision marker, snapped to boundaries — the 详解 sees how the piece ENDS."""
    if len(basis) <= cap:
        return basis
    text = _cut_tail_sections(basis)
    if len(text) <= cap:
        return text
    budget = cap - len(_ELISION)
    head = _snap_end(text, int(budget * 0.7))
    tail = _snap_start(text, len(text) - (budget - len(head)))
    return head + _ELISION + tail


def _adequate(it: Item, fetched: str) -> bool:
    """Is this grounding deep-read adequate? arXiv items need a REAL full text — the
    fulltext chain can silently degrade to the abstract page (e.g. ar5iv 30x→abs stub
    passes its length gate), and an abstract-grounded 详解 is exactly the depth failure
    the 7.3 复盘 caught ([3]). Non-arXiv pages ARE the article — unchanged behavior."""
    if arxiv_id_from_url(it.url):
        return len(fetched) >= THIN_ARXIV_CHARS
    return True


def _probe_grounding(it: Item, ctx: RunContext, carried: dict) -> str:
    """The fetch half of deepread, run for every eligible item BEFORE slots are assigned
    (cheap HTTP — opus only runs for selected items). Resumed items reuse the
    checkpointed full_text instead of re-fetching."""
    done = carried.get(it.id)
    if done is not None:
        return done.get("full_text") or ""
    try:
        return fetch_article_text(it.url, config=ctx.config, max_chars=FETCH_CAP)
    except Exception as e:  # noqa: BLE001 — a fetch failure degrades, never crashes
        ctx.log.warn("deepread fetch failed", url=it.url, error=repr(e)[:120])
        return ""


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

        # V5: critic verdicts are annotation-only (the brief/reading page keeps the honest
        # ⚠️可跳过 label) — they no longer gate deepread. 每一篇都要详解 (user decision).
        eligible = list(ctx.items)

        # checkpoint: a crashed/re-run deepread reuses already-done items (same id + same
        # prompt). Refining deepread.md changes prompt_fp → everything re-runs with the new
        # framework (mirrors the faithfulness eval's resume). Loaded BEFORE the grounding
        # probe so resumed items skip the re-fetch.
        date = ctx.started_at.astimezone().strftime("%Y-%m-%d")
        ckpt_path = Paths.deepread_ckpt / f"{date}.json"
        prior = read_json(ckpt_path) or {}
        carried = prior.get("items", {}) if prior.get("prompt_fp") == prompt_fp else {}
        ckpt = {"date": date, "prompt_fp": prompt_fp, "items": dict(carried)}
        ckpt_lock = threading.Lock()

        # slot policy: probe every eligible item's grounding first (concurrent HTTP), then
        # hand the top_k slots to fully-grounded items in rank order; thin/abstract-only
        # items yield their slot and only fill what's left (see module docstring).
        with ThreadPoolExecutor(max_workers=3) as pool:
            texts = list(pool.map(lambda it: _probe_grounding(it, ctx, carried), eligible))
        fetched = {it.id: t for it, t in zip(eligible, texts)}
        full = [it for it in eligible if _adequate(it, fetched[it.id])]
        thin = [it for it in eligible if not _adequate(it, fetched[it.id])]
        top = full[: ctx.config.deepread_top_k]
        if len(top) < ctx.config.deepread_top_k:   # not enough fully-grounded → degrade honestly
            top += thin[: ctx.config.deepread_top_k - len(top)]
            order = {it.id: i for i, it in enumerate(eligible)}
            top.sort(key=lambda it: order[it.id])
        selected_ids = {it.id for it in top}
        # the honest-brevity note goes to arXiv stubs AND any very short page — the slot
        # policy's `thin` stays arXiv-only (a short blog IS its whole article; it keeps
        # its slot, it just must not be padded from prior knowledge)
        thin_ids = ({it.id for it in thin}
                    | {it.id for it in eligible if len(fetched.get(it.id) or "") < THIN_NOTE_CHARS})
        thin_skipped = [it for it in thin if it.id not in selected_ids]
        if thin_skipped:
            ctx.stats["deepread.thin_skipped"] = [it.id for it in thin_skipped]
            ctx.log.info("deepread slots: thin/abstract-only groundings yielded to fully-grounded items",
                         skipped=[f"{it.id}:{it.title[:36]}" for it in thin_skipped])

        def work(it: Item) -> None:
            done = ckpt["items"].get(it.id)
            if done is not None:                          # resume: skip fetch + LLM
                it.full_text = done.get("full_text")
                it.explain_zh = done.get("explain_zh")
                ctx.bump("deepread.resumed")
                return
            text = fetched.get(it.id, "")
            it.full_text = text or None
            basis = ((it.summary or "") + "\n\n" + text).strip()
            if len(basis) < MIN_BASIS_CHARS:
                it.explain_zh = NO_TEXT
                ctx.bump("deepread.no_text")
            else:
                grounding = smart_grounding(basis)   # the exact source text the LLM sees
                _write_source_sidecar(ctx, it, grounding)
                # thin grounding → deterministic honest-brevity trigger (the prompt's
                # 「源材料薄」 mode must not depend on the model noticing thinness itself)
                note = _THIN_NOTE if it.id in thin_ids else ""
                user = (f"{note}标题: {it.title}\n来源: {it.source_name}\n链接: {it.url}\n\n"
                        f"原文:\n{grounding}")
                t0 = time.monotonic()
                res = ctx.llm.complete(user, system=system, model=ctx.config.models.deepread,
                                       timeout=LLM_TIMEOUT, tag=self.name)
                ms = round((time.monotonic() - t0) * 1000, 1)
                if res.ok and res.text.strip():
                    it.explain_zh = res.text.strip()
                    ctx.bump("deepread.ok")
                else:
                    it.explain_zh = NO_TEXT
                    ctx.bump("deepread.failed")
                    ctx.log.warn("deepread llm failed", url=it.url, error=(res.error or "")[:120])
                if ctx.trace is not None:     # per-item 额度/时长遥测 (V5 长产出要盯真实耗时)
                    try:
                        ctx.trace.event("deepread_item", id=it.id, ms=ms, ok=res.ok,
                                        grounding_chars=len(grounding),
                                        out_chars=len(res.text or ""), thin=it.id in thin_ids)
                    except Exception:  # noqa: BLE001 — tracing must never break deepread
                        pass
            if it.explain_zh == NO_TEXT:
                # NEVER checkpoint a failure: a checkpointed NO_TEXT gets reused by every
                # resume, turning a one-off LLM/fetch hiccup into a permanent hole for the
                # day (bit us live 2026-07-06). Leaving it out costs one retry next run.
                return
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
                     failed=ctx.stats.get("deepread.failed", 0),
                     thin=len(thin_ids), thin_skipped=len(thin_skipped))
