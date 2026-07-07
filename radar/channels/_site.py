"""Static site builder — turns data/ into the whole 情报台: every day's reading page +
home hub + 归档 archive + 数据统计 stats, all under unguessable segs on one CF Pages project.

Idempotent by construction: everything is re-derived from `data/digests/*.items.json`, the
local markdown archive, `data/eval` / `data/feedback` / `data/state` — running it twice
writes the same site. Called by the web_reader channel on every daily run (today's markdown
is passed inline because the local-archive channel runs AFTER web_reader), and by
`scripts/rebuild_site.py` for manual full rebuilds.

Segs: day pages keep seg=HMAC(secret, date); the hub pages use fixed keys under the same
secret — "home" (the ONE bookmark), "index" (archive), "stats". Same privacy envelope as
day pages: unguessable + noindex + gitignored output; the site root stays 404.

Every page passes the leak gate BEFORE it is written: a hit means that page is skipped
(the old file, if any, stays), never published — same discipline as the weekly review.
"""
from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

from ..core.config import Paths
from ..core.io import atomic_write_text
from ._design import page_shell
from ._site_stats import collect_stats, list_item_dates, read_items, render_stats_page
from ._web_render import render_day_page

_EVAL_BOX = "╔═ eval"     # the local archive appends the eval box after delivery — never ship it
_WORKER_SRC = Paths.root / "deploy" / "site_worker.js"   # same-origin /vote endpoint (PART 4)

_WEEKDAYS = "一二三四五六日"


def _weekday_zh(date: str) -> str:
    try:
        return "周" + _WEEKDAYS[datetime.strptime(date, "%Y-%m-%d").weekday()]
    except ValueError:
        return ""


def _archive_md(date: str) -> Optional[str]:
    """The day's full markdown from the local archive (data/digests/YYYY/MM/{date}.md),
    with the post-delivery eval box stripped — that box is terminal telemetry, not 详解."""
    p = Paths.digests / date[:4] / date[5:7] / f"{date}.md"
    if not p.exists():
        return None
    md = p.read_text(encoding="utf-8")
    return md.split(_EVAL_BOX)[0].rstrip() + "\n"


def _leak_gate(html: str, name: str, log: Any = None) -> bool:
    """True = clean, safe to write. A hit blocks THIS page only and is loudly logged."""
    try:
        from ..self_improve.leak_scan import scan_text
        hits, warning = scan_text(html, source=f"site:{name}")
    except Exception as e:  # noqa: BLE001 — a broken scanner must fail CLOSED
        if log:
            log.warn("leak gate errored — page NOT written", page=name, error=repr(e)[:120])
        return False
    if hits:
        if log:
            log.warn("leak gate HIT — page NOT written", page=name, hits=len(hits))
        return False
    if warning and log:
        log.warn("leak gate vocabulary incomplete (page written under builtin terms only)",
                 page=name, note=warning[:120])
    return True


# ---------------- hub pages ----------------

HUB_CSS = """
.doors{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px;margin:1.4em 0}
.door{display:block;background:var(--surface);border:1px solid var(--border);border-radius:14px;
padding:16px 18px;color:var(--fg);transition:border-color .12s,transform .12s}
.door:hover{border-color:var(--accent);text-decoration:none;transform:translateY(-1px)}
.door .eyebrow{font-family:ui-monospace,"SF Mono",Menlo,monospace;font-size:.68rem;
letter-spacing:.12em;color:var(--faint)}
.door .t{font-size:1.05rem;font-weight:650;margin:.35em 0 .25em}
.door .d{font-size:.85rem;color:var(--muted);line-height:1.55}
.hero{margin:1.6em 0 .4em;padding:18px 20px;background:var(--surface);border:1px solid var(--border);
border-radius:14px}
.hero .eyebrow{font-family:ui-monospace,"SF Mono",Menlo,monospace;font-size:.7rem;
letter-spacing:.12em;color:var(--faint)}
.hero .ht{font-size:1.12rem;font-weight:650;line-height:1.55;margin:.4em 0 .3em}
.hero .ht a{color:var(--fg)}
.hero .ht a:hover{color:var(--accent);text-decoration:none}
.hero .hw{font-size:.92rem;color:var(--muted)}
.day-card{margin:1.3em 0;padding:16px 18px}
.day-card header{display:flex;justify-content:space-between;align-items:baseline;gap:10px;
margin-bottom:.4em}
.day-card header a{font-weight:650;color:var(--fg);font-size:1.02rem}
.day-card header a:hover{color:var(--accent)}
.dl{list-style:none;margin:0;padding:0}
.dl li{padding:.42em 0;border-bottom:1px solid var(--hairline);font-size:.93rem;line-height:1.6}
.dl li:last-child{border-bottom:none}
.dl .idx{font-family:ui-monospace,"SF Mono",Menlo,monospace;color:var(--accent);
font-size:.8rem;margin-right:.35em}
.dl .why{color:var(--muted);font-size:.86rem}
.dl a{color:var(--fg)}
.dl a:hover{color:var(--accent)}
"""


def render_home(*, latest_date: str, latest_items: list[dict], n_days: int, n_items: int,
                nav: dict, day_url: str, votes_total: int, faith_pct: Optional[float]) -> str:
    wd = _weekday_zh(latest_date)
    head = latest_items[0] if latest_items else None
    hero = ""
    if head:
        import html as _h
        reason = head.get("reason") or ""
        hero = (f'<div class="hero"><span class="eyebrow">今日头条 · [1]</span>'
                f'<div class="ht"><a href="{day_url}#item-1">{_h.escape(str(head.get("title", "")))}</a></div>'
                + (f'<div class="hw">{_h.escape(reason)}</div>' if reason else "") + "</div>")
    faith = f"忠实度 {faith_pct:.0f}%" if faith_pct is not None else "忠实度待累计"
    doors = (
        f'<div class="doors">'
        f'<a class="door" href="{day_url}"><span class="eyebrow">TODAY</span>'
        f'<div class="t">今日详解</div><div class="d">{latest_date} {wd} · {len(latest_items)} 篇教学级深读</div></a>'
        f'<a class="door" href="{nav["archive"]}"><span class="eyebrow">ARCHIVE</span>'
        f'<div class="t">往期归档</div><div class="d">{n_days} 天 · {n_items} 篇，按天回翻、直达任一篇</div></a>'
        f'<a class="door" href="{nav["stats"]}"><span class="eyebrow">STATS</span>'
        f'<div class="t">数据统计</div><div class="d">投票 {votes_total} 次 · {faith}，看机器对你的了解</div></a>'
        f"</div>"
    )
    body = (
        f"<h1>每日前沿 agent 情报</h1>"
        f'<p class="readout">{latest_date} {wd} · 最新一期 {len(latest_items)} 篇 · 每天 08:30 自动扫描 28 源</p>'
        f"{hero}{doors}"
        '<p class="hint" style="color:var(--muted);font-size:.85rem">'
        "收藏本页即可：每天的新详解、归档与统计都从这里进。</p>"
    )
    return page_shell(title="Agent Radar · 主页", body=body, active="home", nav=nav,
                      extra_css=HUB_CSS, foot_note="读 → 投票 → 它越来越懂你")


def render_archive(*, dates_desc: list[str], day_urls: dict[str, str],
                   items_by_date: dict[str, list[dict]], nav: dict,
                   built_days: Optional[set] = None) -> str:
    """Days whose reading page exists link to it (+#item-N anchors); a day WITHOUT a page
    (no archive md, or blocked by the leak gate) still lists its items but links each row
    to the ORIGINAL article instead — no dead links, no re-publishing gated content."""
    import html as _h
    total = sum(len(v) for v in items_by_date.values())
    cards = []
    for d in dates_desc:
        has_page = built_days is None or d in built_days
        url = day_urls.get(d) if has_page else None
        items = items_by_date.get(d, [])
        if not items and not url:
            continue
        rows = "".join(
            (f'<li><a href="{url}#item-{i + 1}"><span class="idx">[{i + 1}]</span>'
             f"{_h.escape(str(it.get('title', '')))}</a>"
             if url else
             f'<li><a href="{_h.escape(str(it.get("url", "")), quote=True)}" target="_blank" '
             f'rel="noopener"><span class="idx">[{i + 1}]</span>'
             f"{_h.escape(str(it.get('title', '')))}</a>")
            + (f' <span class="why">— {_h.escape(str(it.get("reason") or ""))}</span>'
               if it.get("reason") else "") + "</li>"
            for i, it in enumerate(items))
        head = (f'<a href="{url}">{d} {_weekday_zh(d)}</a>' if url
                else f"<span>{d} {_weekday_zh(d)}</span>")
        n = f'<span class="readout">{len(items)} 篇</span>' if items else ""
        cards.append(f'<section class="day-card card"><header>'
                     f"{head}{n}</header>"
                     f'<ol class="dl">{rows}</ol></section>')
    body = ("<h1>往期归档</h1>"
            f'<p class="readout">{len(cards)} 天 · {total} 篇详解 · 最新在上</p>'
            + "".join(cards))
    return page_shell(title="Agent Radar · 往期归档", body=body, active="archive", nav=nav,
                      extra_css=HUB_CSS, foot_note="点日期读整期 · 点标题直达那篇")


# ---------------- the builder ----------------

def build_site(secret: str, *,
               today: Optional[tuple[str, str]] = None,
               vote_api: Optional[str] = None,
               mermaid: Optional[Callable[[str], Optional[str]]] = None,
               site_dir: Optional[Path] = None,
               log: Any = None) -> dict:
    """Render the whole site into `site_dir` (default data/web/site). Returns
    {"nav": {...}, "day_urls": {date: "/{seg}/"}, "built": [names], "skipped": [names]}.
    `today=(date, markdown)` supplies the digest that is not yet in the local archive."""
    from .web_reader import _seg   # single seg definition — never duplicated
    site = site_dir or (Paths.web / "site")
    nav = {"home": f"/{_seg(secret, 'home')}/",
           "archive": f"/{_seg(secret, 'index')}/",
           "stats": f"/{_seg(secret, 'stats')}/"}

    dates = set(list_item_dates())
    if today:
        dates.add(today[0])
    dates = sorted(dates)
    day_urls = {d: f"/{_seg(secret, d)}/" for d in dates}
    built: list[str] = []
    skipped: list[str] = []

    def _emit(rel: str, html: str, name: str) -> bool:
        if not _leak_gate(html, name, log):
            skipped.append(name)
            return False
        atomic_write_text(site / rel / "index.html", html)
        built.append(name)
        return True

    # -- day pages (today from the inline markdown; the rest from the local archive) --
    # Two passes: ① pre-scan each day's MARKDOWN through the leak gate to fix the set of
    # publishable days — so the prev/next chain and archive links never point at a page
    # that the gate will refuse (e.g. the pre-privacy-fix 2026-06-30 详解). ② render only
    # those; the rendered HTML still passes the final gate before writing (belt+braces).
    items_by_date: dict[str, list[dict]] = {}
    md_map: dict[str, str] = {}
    for d in dates:
        items_by_date[d] = read_items(d)
        md = today[1] if (today and d == today[0]) else _archive_md(d)
        if md and _leak_gate(md, f"day-md:{d}", log):
            md_map[d] = md
        elif md:
            skipped.append(f"day:{d}")
    clean_days = [d for d in dates if d in md_map]
    for i, d in enumerate(clean_days):
        prev_d = clean_days[i - 1] if i > 0 else None
        next_d = clean_days[i + 1] if i + 1 < len(clean_days) else None
        items = items_by_date[d]
        html = render_day_page(
            md_map[d], date=d, mermaid_svg=mermaid, nav=nav,
            prev_day=(day_urls[prev_d], prev_d) if prev_d else None,
            next_day=(day_urls[next_d], next_d) if next_d else None,
            vote_api=vote_api,
            item_ids={str(j + 1): it["id"] for j, it in enumerate(items) if it.get("id")} or None,
        )
        _emit(day_urls[d].strip("/"), html, f"day:{d}")

    # A day that did NOT make it this run must not linger from an older build — the whole
    # dir redeploys as a snapshot, so a stale file would keep re-publishing gated content.
    written = {n.split(":", 1)[1] for n in built if n.startswith("day:")}
    for d in dates:
        if d not in written:
            stale = site / day_urls[d].strip("/")
            if stale.exists():
                shutil.rmtree(stale, ignore_errors=True)
                if log:
                    log.warn("stale day page removed from site (gate/no-md)", date=d)

    latest = dates[-1] if dates else None
    stats_model = collect_stats(today[0] if today else (latest or datetime.now().strftime("%Y-%m-%d")))

    # -- hub pages --
    if latest:
        votes = stats_model["votes"]
        faith = stats_model["eval"][-1]["pct"] if stats_model["eval"] else None
        home_html = render_home(
            latest_date=latest, latest_items=items_by_date.get(latest, []),
            n_days=len(dates), n_items=sum(len(v) for v in items_by_date.values()),
            nav=nav, day_url=day_urls[latest],
            votes_total=votes["up"] + votes["down"], faith_pct=faith)
        _emit(nav["home"].strip("/"), home_html, "home")

        built_days = {n.split(":", 1)[1] for n in built if n.startswith("day:")}
        arch_html = render_archive(dates_desc=list(reversed(dates)), day_urls=day_urls,
                                   items_by_date=items_by_date, nav=nav,
                                   built_days=built_days)
        _emit(nav["archive"].strip("/"), arch_html, "archive")

    stats_html = render_stats_page(stats_model, nav=nav)
    _emit(nav["stats"].strip("/"), stats_html, "stats")

    # -- same-origin vote endpoint (PART 4): ship the Pages worker with the site --
    if _WORKER_SRC.exists():
        shutil.copyfile(_WORKER_SRC, site / "_worker.js")

    if log:
        log.info("site built", pages=len(built), skipped=skipped or None,
                 days=len(dates))
    return {"nav": nav, "day_urls": day_urls, "built": built, "skipped": skipped}
