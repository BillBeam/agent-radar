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
from ..core.models import Digest, Item, RunContext
from ..core.ports import Stage
from ..core.registry import register

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
                           model=ctx.config.models.synthesize, timeout=120)
    return res.text.strip() if res.ok else ""


def _essence(it: Item, limit: int = 130) -> str:
    """One-line gist for the brief — first real paragraph of the 详解, else triage reason."""
    src = it.explain_zh
    if src and not src.startswith(_NO_TEXT_PREFIX):
        for para in src.split("\n\n"):
            p = para.strip()
            if p and not p.lstrip().startswith("#"):
                p = re.sub(r"[*`#>]", "", p).strip()
                return p[:limit] + ("…" if len(p) > limit else "")
    return it.reason or ""


def _render_full(it: Item) -> str:
    badge = f" · ★可改进本系统（{it.target_component}）" if it.self_applicable else ""
    tags = ("　`" + "` `".join(it.tags) + "`") if it.tags else ""
    head = f"### [{it.title}]({it.url})\n`{it.source_name}` · 相关度 {it.score:.0f}{badge}{tags}\n"
    return head + "\n" + (it.explain_zh or it.reason or "") + "\n"


def _render_brief(it: Item) -> str:
    badge = " ★可改进本系统" if it.self_applicable else ""
    tags = ("　" + " ".join(f"#{t}" for t in it.tags[:3])) if it.tags else ""
    return (f"**[{it.title}]({it.url})**\n"
            f"`{it.source_name}` · 相关度 {it.score:.0f}{badge}{tags}\n"
            f"{_essence(it)}\n")


def _health_line(ctx: RunContext) -> str:
    """A warning line for the digest header when sources failed — so the user can
    tell 'no news today' from 'fetching broke'."""
    fh = ctx.stats.get("fetch_health") or {}
    live, total, failed = fh.get("live", 0), fh.get("total", 0), fh.get("failed", [])
    if total and live == 0:
        return f"> ⚠️ **抓取大面积失败：0/{total} 源成功** —— 不是没料，是网络/代理挂了，请跑 `radar doctor`。\n"
    if failed:
        more = "…" if len(failed) > 6 else ""
        return f"> ⚠️ 今天 {live}/{total} 源成功，失败：{', '.join(failed[:6])}{more}\n"
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
        header = (f"# Agent Radar · {date}（{weekday}）\n\n"
                  f"> 扫描 {len(ctx.sources)} 源 · 候选 {funnel.get('candidates', 0)} · "
                  f"精选 {len(items)} · 跳过已读 {ctx.stats.get('skipped_seen', 0)}\n"
                  + _health_line(ctx))

        tldr = _tldr(ctx, items)
        tldr_block = f"\n## 🎯 {title_kind} TL;DR\n\n{tldr}\n" if tldr else ""

        groups: dict[str, list[Item]] = {}
        for it in items:
            sec, _ = _section_of(it)
            groups.setdefault(sec, []).append(it)
        ordered = sorted(groups.items(), key=lambda kv: min(_section_of(i)[1] for i in kv[1]))

        full_parts: list[str] = []
        brief_parts: list[str] = []
        for sec, sec_items in ordered:
            full_parts.append(f"\n## {sec}\n")
            brief_parts.append(f"\n## {sec}\n")
            for it in sorted(sec_items, key=lambda x: x.score or 0, reverse=True):
                full_parts.append(_render_full(it))
                brief_parts.append(_render_brief(it))

        sa = sum(1 for it in items if it.self_applicable)
        deep = sum(1 for it in items if it.explain_zh and not it.explain_zh.startswith(_NO_TEXT_PREFIX))
        full_footer = (f"\n---\n*把关漏斗：候选 {funnel.get('candidates', 0)} → 过门 {len(items)}"
                       f"（淘汰低于阈值 {funnel.get('below_threshold', 0)}、噪声 {funnel.get('blocked', 0)}）"
                       f" · 自相关 {sa} 条 · run `{ctx.run_id}`*\n")
        brief_footer = (f"\n---\n📄 完整逐篇中文详解（{deep} 篇深读）已存本地归档。"
                        f"想深挖哪篇，开 `/agent-radar` 跟我聊。\n")

        ctx.digest = Digest(
            kind=ctx.mode, date=date, items=items, stats=ctx.stats,
            markdown=header + tldr_block + "".join(full_parts) + full_footer,
            markdown_brief=header + tldr_block + "".join(brief_parts) + brief_footer,
        )
        # persist final items (with 详解) so briefs/eval can re-render without re-running
        atomic_write_json(Paths.digests / f"{date}.items.json",
                          [it.model_dump(mode="json") for it in items])
        ctx.log.info("synthesized", full_chars=len(ctx.digest.markdown),
                     brief_chars=len(ctx.digest.markdown_brief), sections=len(groups), tldr=bool(tldr))
