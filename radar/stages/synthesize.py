"""Synthesize stage — assemble two renderings of the digest:

- markdown        : full, rich 中文详解 → local archive (read deliberately / Face 2)
- markdown_brief  : skimmable → DingTalk / IM (TL;DR + per-item title/link/essence)

Structure is deterministic Python; the LLM only writes the short TL;DR prose.
Final items (with 详解) are persisted so briefs can be re-rendered without re-running.
"""
from __future__ import annotations

import re

from ..core.config import Paths
from ..core.io import atomic_write_json
from ..core.models import Digest, Item, RunContext, is_display_fresh
from ..core.ports import Stage
from ..core.registry import register
from ..core.text import demote_headings, smart_truncate, strip_trailing_date
from ..core.versioning import archive_if_new
from .critic import critic_verdict

_WEEKDAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]

_SECTION = {
    "harness": ("🔧 Harness / 工程", 0),
    "papers": ("🧠 论文 / 研究", 1),
    "framework": ("🧩 框架 / 工具", 2),
    "labs": ("🚀 实验室 / 模型", 3),
    "china": ("🚀 实验室 / 模型", 3),
    "newsletter": ("📰 观点 / 长文", 4),
    "community": ("💬 社区信号", 5),
}

_NO_TEXT_PREFIX = "（原文"


def _section_of(it: Item) -> tuple[str, int]:
    return _SECTION.get(it.category, ("📌 其他", 9))


def _tldr(ctx: RunContext, items: list[Item]) -> str:
    if ctx.llm is None or not items:
        return ""
    lines = [f"- {it.title} :: {it.reason or ''}" for it in items[:8]]
    prompt = ("下面是今天精选的前沿 agent/harness 条目。用中文写 3–5 条极简「今日要点」"
              "bullet，每条≤30字，点出今天最值得注意的趋势/突破，不要逐条复述标题。"
              "只输出 markdown bullet：\n\n" + "\n".join(lines))
    res = ctx.llm.complete(prompt, system="你是简洁的科技编辑，只输出要点 bullet。",
                           model=ctx.config.models.synthesize, timeout=120, tag="synthesize")
    return res.text.strip() if res.ok else ""


def _title(it: Item) -> str:
    return strip_trailing_date(it.title)


def _essence(it: Item, limit: int = 120) -> str:
    """Fallback one-liner — first real paragraph of the 详解, cleaned + truncated."""
    src = it.explain_zh
    if src and not src.startswith(_NO_TEXT_PREFIX):
        for para in src.split("\n\n"):
            p = para.strip()
            if p and not p.lstrip().startswith("#"):
                return smart_truncate(re.sub(r"[*`#>]", "", p).strip(), limit)
    return it.reason or ""


def _critic_note(verdict: dict | None) -> str:
    """Annotation for a 'skippable' verdict, or ''. We annotate — never delete: he still
    gets the title link + reason and decides for himself (he's the expert)."""
    if not verdict or not verdict.get("skip"):
        return ""
    label = "可跳过" if verdict.get("conf") == "high" else "疑似可跳过"
    why = (verdict.get("why") or "").strip()
    return f"⚠️ {label}" + (f" · {why}" if why else "")


def _render_full(it: Item, num: int | None = None, verdict: dict | None = None) -> str:
    """Local archive: rich 详解. ### item header (only heading per item — the
    inlined explanation uses bold lines, defensively demoted)."""
    prefix = f"[{num}] " if num else ""
    tags = ("　" + " · ".join(it.tags[:4])) if it.tags else ""
    note = _critic_note(verdict)
    note_line = f"> {note}\n\n" if note else ""
    body = (demote_headings(it.explain_zh)
            if (it.explain_zh and not it.explain_zh.startswith(_NO_TEXT_PREFIX))
            else (it.explain_zh or it.reason or ""))
    return f"### {prefix}[{_title(it)}]({it.url})\n*{it.source_name}*{tags}\n\n{note_line}{body}\n"


def _render_brief(it: Item, num: int | None = None, verdict: dict | None = None) -> str:
    """DingTalk-safe scannable card: clean title link + one-line why + plain source
    tail + divider. No backticks (DingTalk doesn't render inline code), no score, no ★.
    A small [N] prefix lets you `radar mark <date> N` to thumbs-up/down."""
    prefix = f"[{num}] " if num else ""
    why = (it.reason or _essence(it)).strip()
    note = _critic_note(verdict)
    note_line = f"{note}\n" if note else ""
    return (f"**{prefix}[{_title(it)}]({it.url})**\n"
            f"{note_line}{why}\n"
            f"*— {it.source_name}*\n\n---\n")


def _health_line(ctx: RunContext) -> str:
    """A warning line for the digest header when sources failed — so the user can
    tell 'no news today' from 'fetching broke'."""
    fh = ctx.stats.get("fetch_health") or {}
    live, total, failed = fh.get("live", 0), fh.get("total", 0), fh.get("failed", [])
    if total and live == 0:
        return f"> ⚠️ **抓取大面积失败：0/{total} 源成功** —— 不是没料，是网络/代理挂了，请跑 `radar doctor`。\n"
    if failed:
        more = "…" if len(failed) > 6 else ""
        return (f"> ⚠️ 今天 {live}/{total} 源成功，失败：{', '.join(failed[:6])}{more}"
                f"——缺口不丢，该源恢复后自动补课。\n")
    return ""


@register("stage", "synthesize")
class SynthesizeStage(Stage):
    name = "synthesize"

    def run(self, ctx: RunContext) -> None:
        items = ctx.items or []
        local = ctx.started_at.astimezone()
        date = local.strftime("%Y-%m-%d")
        weekday = _WEEKDAYS[local.weekday()]
        funnel = ctx.stats.get("funnel", {})

        if not items:
            fh = ctx.stats.get("fetch_health") or {}
            if fh.get("total") and fh.get("live", 0) == 0:
                body = (f"> ⚠️ **抓取大面积失败：0/{fh['total']} 源成功** —— 不是没料，是网络/代理挂了。"
                        f"跑 `radar doctor` 查可达性。\n")
            else:
                body = "> 今日扫描后没有命中阈值的高信号内容。宁缺毋滥，明天见。\n"
            md = f"# Agent Radar · {date}（{weekday}）\n\n{body}"
            ctx.digest = Digest(kind=ctx.mode, date=date, items=[], markdown=md,
                                markdown_brief=md, stats=ctx.stats)
            return

        title_kind = "每周深读" if ctx.mode == "weekly" else "今日"
        # items arrive in rerank rank-order; split by DISPLAY freshness (recent-dated =
        # today's new; undated OR stale-dated = back-catalog first collected now) so we
        # never pass off old posts as "today" — shared predicate with dingtalk_card.
        fresh = [it for it in items if is_display_fresh(it)]
        backfill = [it for it in items if not is_display_fresh(it)]
        # CANONICAL display order = fresh→backfill (each in rank order). This single
        # order drives the [N] numbers, items.json persistence AND `radar mark` — so the
        # number you see in DingTalk always maps to the right item.id (no silent mismatch).
        ordered = fresh + backfill
        number_of = {id(it): n for n, it in enumerate(ordered, 1)}
        counts = f"今日新增 {len(fresh)}" + (f" · 首次收录 {len(backfill)}" if backfill else "")
        degraded = ("> ⚠️ 本日排序降级：rerank 未成功（LLM 超时/失败），条目顺序为粗筛分数序、个性化未生效。\n"
                    if ctx.stats.get("rerank_degraded") else "")
        # Thin-delivery note (07-08「为什么只有9篇」): fewer than the cap means fewer items
        # cleared the quality gate — say so, so a short day never reads as a broken funnel.
        cap = ctx.config.max_items(ctx.mode)
        thin = (f"> 入选 {len(items)}/{cap}：过质量门的只有这些——宁缺毋滥，不凑数。\n"
                if len(items) < cap else "")
        header = (f"# Agent Radar · {date}（{weekday}）\n\n"
                  f"> 扫描 {len(ctx.sources)} 源 · 候选 {funnel.get('candidates', 0)} · "
                  f"{counts} · 跳过已读 {ctx.stats.get('skipped_seen', 0)}\n"
                  + _health_line(ctx) + thin + degraded)

        tldr = _tldr(ctx, ordered)
        tldr_block = f"\n## 🎯 {title_kind} TL;DR\n\n{tldr}\n" if tldr else ""

        full_parts: list[str] = []
        brief_parts: list[str] = []

        def _emit(group: list[Item], heading: str | None) -> None:
            if not group:
                return
            if heading:
                full_parts.append(f"\n## {heading}\n")
                brief_parts.append(f"\n## {heading}\n")
            for it in group:  # rank order within group; numbering follows display order
                num = number_of[id(it)]
                v = critic_verdict(ctx, it)   # critic's 可跳过 verdict (neutral if critic didn't run)
                full_parts.append(_render_full(it, num, v))
                brief_parts.append(_render_brief(it, num, v))

        if backfill:  # only label when there's a contrast to draw
            _emit(fresh, "🆕 今日新增")
            # NOT a re-push: these are pieces first collected today whose publish date is
            # old or missing (e.g. blog index back-catalog) — the old wording 往期补课
            # read like stale re-runs and confused the reader.
            _emit(backfill, "📚 首次收录（往期/无日期内容，非重复推送）")
        else:
            _emit(fresh, None)

        sa = sum(1 for it in items if it.self_applicable)
        deep = sum(1 for it in items if it.explain_zh and not it.explain_zh.startswith(_NO_TEXT_PREFIX))
        full_footer = (f"\n---\n*把关漏斗：候选 {funnel.get('candidates', 0)} → 过门 {len(items)}"
                       f"（淘汰低于阈值 {funnel.get('below_threshold', 0)}、噪声 {funnel.get('blocked', 0)}）"
                       f" · 自相关 {sa} 条 · run `{ctx.run_id}`*\n")
        brief_footer = (f"\n📄 完整逐篇中文详解（{deep} 篇深读）已存本地归档。"
                        f"想深挖哪篇，开 /agent-radar 跟我聊。\n")

        ctx.digest = Digest(
            kind=ctx.mode, date=date, items=ordered, stats=ctx.stats,
            markdown=header + tldr_block + "".join(full_parts) + full_footer,
            markdown_brief=header + tldr_block + "".join(brief_parts) + brief_footer,
        )
        # persist in CANONICAL display order so {date}.items.json[N-1] == digest item [N]
        # == `radar mark <date> N`. This alignment is the whole point of the order above.
        # Same-day re-runs with a DIFFERENT item set are a new VERSION: the previous
        # items.json/archive md are suffixed .v{k} first (never silently clobbered), and the
        # version number rides ctx.stats so the card gets a fresh outTrackId (DingTalk ignores
        # cardData on a reused one — the 07-08 migration-day collision).
        ver = archive_if_new(date, [it.id for it in ordered], run_id=ctx.run_id, log=ctx.log)
        ctx.stats["digest_version"] = ver
        atomic_write_json(Paths.digests / f"{date}.items.json",
                          [it.model_dump(mode="json") for it in ordered])
        ctx.log.info("synthesized", full_chars=len(ctx.digest.markdown),
                     brief_chars=len(ctx.digest.markdown_brief),
                     fresh=len(fresh), backfill=len(backfill), tldr=bool(tldr),
                     version=ver)
