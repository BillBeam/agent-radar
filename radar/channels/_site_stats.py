"""Stats dashboard — build-time aggregation over data/ (items / eval / feedback / state) +
the 统计 page renderer. Zero LLM, zero backend: every number is recomputed on each daily run.

Privacy: this page emits AGGREGATES ONLY — counts, percentages, neutral topic tags, source
categories, run health. Nothing from USER.md, no identity, no employer/business domain; the
rendered HTML still passes the shared leak gate in `_site.build_site` before it may be written.

Charts are hand-rolled inline SVG (no JS, no external libs), colored via CSS variables so
light/dark both read. The categorical palette is the dataviz-validated 5-slot set (blue/aqua/
yellow/green/violet, stepped per mode); category→slot mapping is FIXED (color follows the
entity, never the day's rank), segments keep 2px surface gaps, and every series is labeled
in text (legend with counts) — the sub-3:1 light-mode slots lean on those visible labels.
"""
from __future__ import annotations

import html as _html
import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from ..core.config import Paths
from ..core.io import read_json

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# category → (chinese label, palette slot class). FIXED mapping — a day without papers must
# not repaint harness. Unmapped categories fold into 其他/gray.
CATEGORIES: list[tuple[str, str, str]] = [
    ("papers", "论文", "s1"),
    ("harness", "工程实践", "s2"),
    ("labs", "厂商发布", "s3"),
    ("framework", "框架", "s4"),
    ("community", "社区", "s5"),
]
_CAT_SLOT = {key: (label, slot) for key, label, slot in CATEGORIES}
_OTHER = ("其他", "sx")


# ---------------- data readers (shared with _site) ----------------

def list_item_dates() -> list[str]:
    """Dates that have a persisted {date}.items.json — ascending."""
    out = []
    for p in Paths.digests.glob("*.items.json"):
        d = p.name.replace(".items.json", "")
        if _DATE_RE.match(d):
            out.append(d)
    return sorted(out)


def read_items(date: str) -> list[dict]:
    items = read_json(Paths.digests / f"{date}.items.json", []) or []
    return [it for it in items if isinstance(it, dict)]


# ---------------- aggregation ----------------

def _cat_counts(items: list[dict]) -> dict[str, int]:
    out: dict[str, int] = {}
    for it in items:
        c = it.get("category") or "other"
        c = c if c in _CAT_SLOT else "other"
        out[c] = out.get(c, 0) + 1
    return out


def _eval_trend() -> list[dict]:
    """Per-day faithfulness (mean support rate, %) from data/eval/{date}.json (schema 1 only)."""
    out = []
    for p in sorted(Paths.eval.glob("*.json")):
        d = p.name[:-5]
        if not _DATE_RE.match(d):
            continue
        doc = read_json(p, {}) or {}
        if doc.get("schema_version") != 1:
            continue
        fa = doc.get("faithfulness") or {}
        rate = fa.get("mean_support_rate")
        if isinstance(rate, (int, float)) and fa.get("n_scored", 0):
            out.append({"date": d, "pct": round(rate * 100, 1), "n": fa.get("n_scored", 0)})
    return out


def _votes(days: int = 3650) -> dict:
    """Aggregate 👍/👎 across data/feedback/*.json (whole history — it IS the picture).
    `best_pairs` follows the ranking ruler's semantics: pairs form WITHIN one day
    (that day's 👍 × that day's 👎) — the D-stage progress is the best single day."""
    up = down = 0
    tag_up: dict[str, int] = {}
    best_pairs = 0
    best_day = ("", 0, 0)
    n_dates = 0
    for p in sorted(Paths.feedback.glob("*.json")):
        d = p.name[:-5]
        if not _DATE_RE.match(d):
            continue
        doc = read_json(p, {}) or {}
        if not isinstance(doc, dict):
            continue
        d_up = d_down = 0
        for snap in doc.values():
            if not isinstance(snap, dict):
                continue
            v = snap.get("vote")
            if v == "up":
                d_up += 1
                for t in snap.get("tags") or []:
                    tag_up[t] = tag_up.get(t, 0) + 1
            elif v == "down":
                d_down += 1
        if d_up + d_down:
            n_dates += 1
        up += d_up
        down += d_down
        if d_up * d_down > best_pairs:
            best_pairs = d_up * d_down
            best_day = (d, d_up, d_down)
    top_up = sorted(tag_up.items(), key=lambda kv: (-kv[1], kv[0]))[:6]
    return {"up": up, "down": down, "pairs": best_pairs, "best_day": best_day,
            "dates": n_dates, "top_up_tags": top_up}


def collect_stats(today: str, *, window_days: int = 14) -> dict[str, Any]:
    """The whole dashboard model, from local data only. `today` anchors the recency windows
    (passed in by the caller — build time)."""
    cutoff = (datetime.strptime(today, "%Y-%m-%d") - timedelta(days=window_days - 1)).strftime("%Y-%m-%d")
    days = []
    tag_counts: dict[str, int] = {}
    vendor_hits: list[dict] = []
    total_items = 0
    all_dates = list_item_dates()
    for d in all_dates:
        items = read_items(d)
        total_items += len(items)
        if d < cutoff:
            continue
        days.append({"date": d, "n": len(items), "cats": _cat_counts(items)})
        for it in items:
            for t in it.get("tags") or []:
                tag_counts[t] = tag_counts.get(t, 0) + 1
            # 厂商发布 = labs 类目，或任何源里 Introducing/Announcing 级命名发布；
            # 普通 harness 仓的例行 release（CLI vX.Y.Z 补丁）不算「厂商发布」。
            if (it.get("category") == "labs" or
                    str(it.get("title", "")).lower().startswith(("introducing", "announcing"))):
                vendor_hits.append({"date": d, "title": it.get("title", ""), "url": it.get("url", "")})

    from ..eval.ranking import MIN_PAIRS
    last_run = read_json(Paths.state / "last_run.json", {}) or {}
    fetch_state = read_json(Paths.state / "fetch_state.json", {}) or {}
    last_ok = (fetch_state.get("last_success") or {})
    stale: list[tuple[str, float]] = []
    now = datetime.strptime(today, "%Y-%m-%d")
    for sid, stamp in last_ok.items():
        try:
            age_d = (now - datetime.fromisoformat(stamp).replace(tzinfo=None)).total_seconds() / 86400
        except ValueError:
            continue
        if age_d > 2.0:
            stale.append((sid, round(age_d, 1)))
    stale.sort(key=lambda kv: -kv[1])

    votes = _votes()
    votes["need"] = MIN_PAIRS
    return {
        "today": today,
        "window_days": window_days,
        "n_days_total": len(all_dates),
        "n_items_total": total_items,
        "days": days,
        "eval": _eval_trend(),
        "votes": votes,
        "tags": sorted(tag_counts.items(), key=lambda kv: (-kv[1], kv[0]))[:12],
        "vendors": vendor_hits[-8:][::-1],
        "health": {
            "run_id": last_run.get("run_id", ""),
            "finished_at": str(last_run.get("finished_at", ""))[:16].replace("T", " "),
            "duration_min": round(float(last_run.get("duration_s") or 0) / 60),
            "errors": len(last_run.get("errors") or []),
            "live": (last_run.get("sources") or {}).get("live"),
            "total": (last_run.get("sources") or {}).get("total"),
            "failed": ((last_run.get("sources") or {}).get("failed") or [])[:6],
            "failed_n": len((last_run.get("sources") or {}).get("failed") or []),
            "selected": last_run.get("selected"),
            "deepread_ok": last_run.get("deepread_ok"),
            "stale_sources": stale[:6],
        },
    }


# ---------------- SVG helpers ----------------

def _rbar_h(x: float, y: float, w: float, h: float, r: float = 4.0) -> str:
    """Horizontal bar path — rounded at the DATA end (right) only, square at the baseline."""
    w = max(w, 0.1)
    r = min(r, w, h / 2)
    return (f"M{x:.1f},{y:.1f} h{w - r:.1f} a{r},{r} 0 0 1 {r},{r} v{h - 2 * r:.1f} "
            f"a{r},{r} 0 0 1 -{r},{r} h-{w - r:.1f} z")


def _rbar_v(x: float, y: float, w: float, h: float, r: float = 4.0) -> str:
    """Vertical bar/segment path — rounded at the TOP (data end) only."""
    h = max(h, 0.1)
    r = min(r, h, w / 2)
    return (f"M{x:.1f},{y + r:.1f} a{r},{r} 0 0 1 {r},-{r} h{w - 2 * r:.1f} "
            f"a{r},{r} 0 0 1 {r},{r} v{h - r:.1f} h-{w:.1f} z")


def _svg_faithfulness(trend: list[dict], w: int = 640, h: int = 190) -> str:
    """Single-series line: per-day mean support rate. Direct labels on ends; <title> everywhere."""
    if len(trend) < 2:
        return ""
    pad_l, pad_r, pad_t, pad_b = 40, 20, 16, 30
    pw, ph = w - pad_l - pad_r, h - pad_t - pad_b
    lo = min(80.0, min(p["pct"] for p in trend) - 4)
    lo = max(0.0, 5 * (lo // 5))
    span = 100.0 - lo or 1

    def xy(i: int, pct: float) -> tuple[float, float]:
        x = pad_l + pw * (i / (len(trend) - 1))
        y = pad_t + ph * (1 - (pct - lo) / span)
        return x, y

    grid, labels = [], []
    ticks = [lo, (lo + 100) / 2, 100.0]
    for tv in ticks:
        _, gy = xy(0, tv)
        grid.append(f'<line x1="{pad_l}" y1="{gy:.1f}" x2="{w - pad_r}" y2="{gy:.1f}" class="grid"/>')
        labels.append(f'<text x="{pad_l - 8}" y="{gy + 4:.1f}" class="tick" text-anchor="end">{tv:.0f}</text>')
    pts = [xy(i, p["pct"]) for i, p in enumerate(trend)]
    path = "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    dots = []
    for (x, y), p in zip(pts, trend):
        dots.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4" class="dot">'
                    f"<title>{_html.escape(p['date'])} · 忠实度 {p['pct']}%（核查 {p['n']} 篇）</title></circle>")
    # direct label on the CURRENT value only (selective labels; ticks carry the scale)
    x, y = pts[-1]
    labels.append(f'<text x="{x:.1f}" y="{y - 10:.1f}" class="dlabel" '
                  f'text-anchor="end">{trend[-1]["pct"]:.0f}%</text>')
    for idx in (0, len(trend) - 1):
        x, _ = pts[idx]
        anchor = "start" if idx == 0 else "end"
        labels.append(f'<text x="{x:.1f}" y="{h - 8}" class="tick" '
                      f'text-anchor="{anchor}">{trend[idx]["date"][5:]}</text>')
    return (f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="忠实度趋势" class="viz">'
            f'{"".join(grid)}<path d="{path}" class="tline"/>{"".join(dots)}{"".join(labels)}</svg>')


def _svg_day_stack(days: list[dict], w: int = 640, h: int = 200) -> str:
    """One vertical bar per day; segments = source categories in FIXED slot order, 2px gaps."""
    if not days:
        return ""
    pad_l, pad_r, pad_t, pad_b = 30, 10, 12, 26
    pw, ph = w - pad_l - pad_r, h - pad_t - pad_b
    max_n = max(d["n"] for d in days) or 1
    slot_w = pw / len(days)
    bar_w = min(34.0, slot_w * 0.62)
    parts, labels = [], []
    order = [k for k, _, _ in CATEGORIES] + ["other"]
    for i, d in enumerate(days):
        x = pad_l + slot_w * i + (slot_w - bar_w) / 2
        y = pad_t + ph
        for cat in order:
            n = d["cats"].get(cat, 0)
            if not n:
                continue
            seg_h = ph * n / max_n
            y -= seg_h
            label, slot = _CAT_SLOT.get(cat, _OTHER)
            parts.append(f'<path d="{_rbar_v(x, y + 1, bar_w, max(seg_h - 2, 1), 3)}" class="{slot}">'
                         f"<title>{_html.escape(d['date'])} · {label} {n} 篇</title></path>")
        labels.append(f'<text x="{x + bar_w / 2:.1f}" y="{pad_t + ph - ph * d["n"] / max_n - 6:.1f}" '
                      f'class="dlabel" text-anchor="middle">{d["n"]}</text>')
        if len(days) <= 8 or i % 2 == (len(days) - 1) % 2:
            labels.append(f'<text x="{x + bar_w / 2:.1f}" y="{h - 8}" class="tick" '
                          f'text-anchor="middle">{d["date"][5:]}</text>')
    base = pad_t + ph
    return (f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="每日推送构成" class="viz">'
            f'<line x1="{pad_l}" y1="{base}" x2="{w - pad_r}" y2="{base}" class="axisline"/>'
            f'{"".join(parts)}{"".join(labels)}</svg>')


def _svg_tag_bars(tags: list[tuple[str, int]], w: int = 640) -> str:
    """Horizontal magnitude bars, one hue (sequential job), value labels at the data end."""
    if not tags:
        return ""
    row_h, bar_h, label_w = 30, 13, 190
    h = row_h * len(tags) + 8
    max_n = max(n for _, n in tags) or 1
    pw = w - label_w - 46
    rows = []
    for i, (tag, n) in enumerate(tags):
        y = 4 + row_h * i
        bw = pw * n / max_n
        rows.append(
            f'<text x="{label_w - 10}" y="{y + bar_h - 2}" class="tagl" text-anchor="end">{_html.escape(tag)}</text>'
            f'<path d="{_rbar_h(label_w, y, bw, bar_h)}" class="s1"><title>{_html.escape(tag)} · {n} 篇</title></path>'
            f'<text x="{label_w + bw + 8:.1f}" y="{y + bar_h - 2}" class="dlabel">{n}</text>')
    return (f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="高频主题" class="viz">'
            f'{"".join(rows)}</svg>')


# ---------------- page ----------------

STATS_CSS = """
.viz{width:100%;height:auto;display:block;
--vs1:#2a78d6;--vs2:#1baf7a;--vs3:#eda100;--vs4:#008300;--vs5:#4a3aa7;--vsx:#9AA1AB}
@media (prefers-color-scheme:dark){.viz{--vs1:#3987e5;--vs2:#199e70;--vs3:#c98500;--vs4:#008300;
--vs5:#9085e9;--vsx:#6B7280}}
.viz .grid{stroke:var(--hairline);stroke-width:1}
.viz .axisline{stroke:var(--border);stroke-width:1}
.viz .tick{font-size:11px;fill:var(--faint);font-family:ui-monospace,Menlo,monospace}
.viz .dlabel{font-size:11.5px;fill:var(--muted);font-family:ui-monospace,Menlo,monospace}
.viz .tagl{font-size:12.5px;fill:var(--fg)}
.viz .tline{fill:none;stroke:var(--data);stroke-width:2;stroke-linejoin:round;stroke-linecap:round}
.viz .dot{fill:var(--bg);stroke:var(--data);stroke-width:2}
.viz .s1{fill:var(--vs1)}.viz .s2{fill:var(--vs2)}.viz .s3{fill:var(--vs3)}
.viz .s4{fill:var(--vs4)}.viz .s5{fill:var(--vs5)}.viz .sx{fill:var(--vsx)}
.legend{display:flex;flex-wrap:wrap;gap:8px 16px;margin:.6em 0 0;font-size:.82rem;color:var(--muted)}
.legend .sw{display:inline-block;width:10px;height:10px;border-radius:3px;margin-right:6px;
vertical-align:-1px}
.sw.s1{background:#2a78d6}.sw.s2{background:#1baf7a}.sw.s3{background:#eda100}
.sw.s4{background:#008300}.sw.s5{background:#4a3aa7}.sw.sx{background:#9AA1AB}
@media (prefers-color-scheme:dark){.sw.s1{background:#3987e5}.sw.s2{background:#199e70}
.sw.s3{background:#c98500}.sw.s5{background:#9085e9}.sw.sx{background:#6B7280}}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:10px;margin:1em 0}
.tile{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:12px 14px}
.tile-v{font-size:1.75rem;font-weight:700;letter-spacing:-.02em;line-height:1.2;
font-variant-numeric:tabular-nums}
.tile-l{font-size:.76rem;color:var(--muted);margin-top:2px}
.meter{height:10px;border-radius:999px;background:var(--surface2);border:1px solid var(--border);
overflow:hidden;margin:.5em 0 .3em}
.meter i{display:block;height:100%;background:var(--data);border-radius:999px}
.hint{font-size:.84rem;color:var(--muted)}
section.panel{margin:2.4em 0}
.panel h2{margin-top:0}
.hlist{list-style:none;margin:.5em 0;padding:0}
.hlist li{display:flex;gap:10px;align-items:baseline;padding:.5em 0;
border-bottom:1px solid var(--hairline);font-size:.92rem}
.hlist li:last-child{border-bottom:none}
.hlist .st{flex:none;font-size:.95rem}
.hlist .k{color:var(--muted);flex:none;min-width:7.5em}
.vlist{list-style:none;margin:.4em 0;padding:0;font-size:.92rem}
.vlist li{padding:.45em 0;border-bottom:1px solid var(--hairline)}
.vlist li:last-child{border-bottom:none}
.vlist .d{font-family:ui-monospace,Menlo,monospace;font-size:.78rem;color:var(--faint);margin-right:.6em}
.empty{color:var(--muted);font-size:.9rem;background:var(--surface);border:1px dashed var(--border);
border-radius:10px;padding:.9em 1em}
"""


def _tiles(votes: dict) -> str:
    total = votes["up"] + votes["down"]
    t = [
        (str(total), "累计投票"),
        (str(votes["up"]), "👍 有用"),
        (str(votes["down"]), "👎 可跳过"),
        (f"{min(votes['pairs'], votes['need'])}/{votes['need']}", "排序尺子进度"),
    ]
    tiles = "".join(f'<div class="tile"><div class="tile-v">{_html.escape(v)}</div>'
                    f'<div class="tile-l">{_html.escape(l)}</div></div>' for v, l in t)
    return f'<div class="tiles">{tiles}</div>'


def _legend(days: list[dict]) -> str:
    totals: dict[str, int] = {}
    for d in days:
        for c, n in d["cats"].items():
            totals[c] = totals.get(c, 0) + n
    parts = []
    for key, label, slot in CATEGORIES:
        if totals.get(key):
            parts.append(f'<span><i class="sw {slot}"></i>{label} {totals[key]}</span>')
    if totals.get("other"):
        parts.append(f'<span><i class="sw sx"></i>其他 {totals["other"]}</span>')
    return f'<div class="legend">{"".join(parts)}</div>' if parts else ""


def render_stats_page(model: dict, nav: Optional[dict] = None) -> str:
    from ._design import page_shell
    v = model["votes"]
    h = model["health"]
    pairs, need = v["pairs"], v["need"]
    pct = min(100, round(100 * pairs / need)) if need else 0

    # ① feedback picture
    if v["up"] + v["down"]:
        remain = max(0, need - pairs)
        bd, bu, bdn = v.get("best_day") or ("", 0, 0)
        pair_hint = (f"同一天里的每个 👍 和每个 👎 构成一次「谁该排前面」的对比。"
                     f"目前最好的一天（{bd}）：{bu} × {bdn} = {pairs} 次；"
                     + (f"单日凑满 {need} 次，那天的排序就有了以你口味为准的正确性信号——还差 {remain} 次。"
                        if remain else f"已凑满 {need} 次，排序尺子有了以你口味为准的正确性信号。"))
        top_tags = ""
        if v["top_up_tags"]:
            rows = _svg_tag_bars(v["top_up_tags"])
            top_tags = f'<p class="hint" style="margin-top:1.2em">你点过 👍 的主题分布：</p>{rows}'
        fb = (_tiles(v)
              + f'<div class="meter" role="img" aria-label="配对进度 {pairs}/{need}"><i style="width:{pct}%"></i></div>'
              + f'<p class="hint">{_html.escape(pair_hint)}</p>' + top_tags)
    else:
        fb = ('<p class="empty">还没有投票记录。在钉钉卡片或每日详解页点 👍/👎，'
              "这里就会开始画出「机器对你的了解」。</p>")

    # ② trend
    trend_svg = _svg_faithfulness(model["eval"])
    trend_block = (trend_svg + '<p class="hint">每天由独立评审把详解逐句对回原文——'
                   "100% = 抽查的所有事实主张都有原文支撑。</p>") if trend_svg else \
        '<p class="empty">忠实度核查天数还不够画趋势（需要 ≥2 天）。</p>'
    stack_svg = _svg_day_stack(model["days"])
    stack_block = (f"{stack_svg}{_legend(model['days'])}") if stack_svg else \
        '<p class="empty">暂无推送记录。</p>'

    # ③ heat
    tags_svg = _svg_tag_bars(model["tags"])
    vendors = model["vendors"]
    vend_html = ("<ul class='vlist'>" + "".join(
        f'<li><span class="d">{_html.escape(x["date"][5:])}</span>'
        f'<a href="{_html.escape(x["url"], quote=True)}" target="_blank" rel="noopener">'
        f"{_html.escape(x['title'])}</a></li>"
        for x in vendors) + "</ul>") if vendors else \
        '<p class="empty">近两周没有捕捉到厂商重大发布。</p>'

    # ④ health
    ok = "🟢" if h["errors"] == 0 else "🔴"
    src_st = "🟢" if (h["live"] or 0) == (h["total"] or 0) else "🟡"
    rows = [
        f'<li><span class="st">{ok}</span><span class="k">上次运行</span>'
        f"<span>{_html.escape(h['finished_at'])} · {h['duration_min']} 分钟 · "
        f"{'无错误' if h['errors'] == 0 else str(h['errors']) + ' 个错误'}</span></li>",
        f'<li><span class="st">{src_st}</span><span class="k">来源覆盖</span>'
        f"<span>{h['live']}/{h['total']} 个源正常"
        + ((f"（失败：{_html.escape('、'.join(h['failed']))}"
            + (f" 等 {h['failed_n']} 个" if h["failed_n"] > len(h["failed"]) else "") + "）")
           if h["failed"] else "") + "</span></li>",
        f'<li><span class="st">📬</span><span class="k">上次产出</span>'
        f"<span>入选 {h['selected']} 条 · 深读完成 {h['deepread_ok']} 篇</span></li>",
    ]
    if h["stale_sources"]:
        names = "、".join(f"{sid}（{age:.0f} 天）" for sid, age in h["stale_sources"][:4])
        rows.append('<li><span class="st">🟡</span><span class="k">超期未成功</span>'
                    f"<span>{_html.escape(names)} — 补课窗口会自动加宽捞回</span></li>")
    health = f'<ul class="hlist">{"".join(rows)}</ul>'

    body = (
        "<h1>数据统计</h1>"
        f'<p class="readout">截至 {_html.escape(model["today"])} · 累计 {model["n_days_total"]} 天 · '
        f'{model["n_items_total"]} 篇详解 · 每次日跑自动刷新</p>'
        f'<section class="panel"><h2>🗳 你的反馈画像</h2>{fb}</section>'
        f'<section class="panel"><h2>📈 推送趋势</h2><h3>详解忠实度（%）</h3>{trend_block}'
        f'<h3>每日推送构成（近 {model["window_days"]} 天）</h3>{stack_block}</section>'
        f'<section class="panel"><h2>🔥 前沿热力（近 {model["window_days"]} 天）</h2>'
        f'<h3>高频主题</h3>{tags_svg or "<p class=empty>暂无数据。</p>"}'
        f"<h3>捕捉到的厂商发布</h3>{vend_html}</section>"
        f'<section class="panel"><h2>🩺 系统健康</h2>{health}</section>'
    )
    return page_shell(title="Agent Radar · 数据统计", body=body, active="stats",
                      nav=nav, extra_css=STATS_CSS,
                      foot_note="全部数字来自本地运行数据的构建时聚合")
