"""Readable eval report — merge the faithfulness + ranking signals into one scannable
top-line, a markdown report, and a cross-day trend. This is PURE FORMATTING of the eval
dict that run.py already built (no new computation, no template engine).

Three honesty red lines (set by Block ① / ②, enforced here too):
  1. coverage always shown — the support_rate mean carries "基于 N/总 篇、跳过 Y", so a
     90% that only scored half the items can't read as "90% overall".
  2. feedback respects MIN_PAIRS — below it, we print "样本太少不构成信号 (K 对)", never a
     bare 0/50/100% that thin data would make misleading.
  3. the independent-judge agreement / tau is labelled 〔诊断〕(stability, not correctness,
     don't optimise) — never "排序对了 Z%".
"""
from __future__ import annotations

from collections import Counter
from typing import Any, Optional

from ..core.config import Paths
from ..core.io import atomic_write_text, read_json


def _pct(x: Optional[float]) -> str:
    return "—" if x is None else f"{round(x * 100)}%"


# ---------------- top-line (one row, both signals) ----------------
def top_line(report: dict) -> str:
    f = report.get("faithfulness") or {}
    r = report.get("ranking") or {}
    fb = r.get("feedback") or {}
    j = r.get("independent_judge") or {}

    # 1) faithfulness — coverage is mandatory
    if f.get("mean_support_rate") is not None:
        faith = (f"忠实度 {_pct(f['mean_support_rate'])}"
                 f"（基于 {f.get('n_scored', 0)}/{f.get('n_total', 0)} 篇有原文；"
                 f"跳过 {f.get('n_skipped', 0)} 篇；标记幻觉/失真 {f.get('n_issues', 0)} 处）")
    else:
        faith = (f"忠实度 —（无可评篇；跳过 {f.get('n_skipped', 0)}/{f.get('n_total', 0)}）")
    parts = [faith]

    # 2) feedback — guard MIN_PAIRS
    if fb.get("is_signal"):
        parts.append(f"排序-反馈 {_pct(fb.get('pairwise_accuracy'))}（{fb.get('n_pairs', 0)} 对）")
    elif fb:
        parts.append(f"排序-反馈：样本太少不构成信号（{fb.get('n_pairs', 0)} 对）")

    # 3) independent judge — diagnostic, not a score
    if j and j.get("kendall_tau") is not None:
        parts.append(f"独立裁判一致度〔诊断〕{_pct(j.get('pairwise_agreement'))}"
                     f"（n={j.get('n')}，τ={j.get('kendall_tau')}）")

    return " · ".join(parts)


# ---------------- shared detail lines (console + md) ----------------
def _faith_rows(f: dict) -> list[dict]:
    return [r for r in f.get("items", []) if r.get("status") == "scored"]


def _flagged(f: dict) -> list[tuple]:
    return [(r, c) for r in _faith_rows(f) for c in r.get("issues", [])]


# ---------------- console ----------------
def console(date: str, report: dict) -> None:
    f = report.get("faithfulness") or {}
    r = report.get("ranking") or {}
    fb = r.get("feedback") or {}
    j = r.get("independent_judge") or {}

    print(f"\n╔═ eval {date} ═══════════════════════════════")
    print(f"║ {top_line(report)}")
    print("╚════════════════════════════════════════════")

    print("\n— 忠实度 · 逐篇 —")
    if f.get("skip_breakdown"):
        print("  跳过：" + "，".join(f"{k}×{v}" for k, v in f["skip_breakdown"].items()))
    if f.get("rate_limited"):
        print("  ⚠ 撞额度/限流提前停手；额度恢复后重跑自动续上（已评走缓存）")
    for it in f.get("items", []):
        if it.get("status") == "scored":
            tag = "✓" if not it.get("issues") else f"⚠{len(it['issues'])}"
            cached = " (缓存)" if it.get("cached") else ""
            print(f"  [{it.get('grounding_source', '?'):9}] {tag} {_pct(it.get('support_rate'))}  "
                  f"{(it.get('title') or '')[:50]}{cached}")
        else:
            print(f"  [{'—':9}] · {(it.get('skip_reason') or it.get('status')):16} "
                  f"{(it.get('title') or '')[:50]}")
    flagged = _flagged(f)
    if flagged:
        print("\n  标记的问题（当「候选」核，注意 full_text 近似可能假阳性）：")
        for it, c in flagged:
            print(f"   • [{(it.get('title') or '')[:34]}] {c.get('verdict')}: {(c.get('claim') or '')[:64]}")
            if c.get("why"):
                print(f"       ↳ {(c.get('why') or '')[:104]}")

    print("\n— 排序合理性 —")
    if fb.get("is_signal"):
        print(f"  反馈成对准确率 {_pct(fb.get('pairwise_accuracy'))}"
              f"（👍 排在 👎 前 {fb.get('correct_pairs')}/{fb.get('n_pairs')}）")
    elif fb:
        print(f"  反馈：{fb.get('note', '暂无')}"
              f"（👍{fb.get('n_up', 0)} · 👎{fb.get('n_down', 0)} → {fb.get('n_pairs', 0)} 对）")
    if j and j.get("kendall_tau") is not None:
        print(f"  独立裁判〔稳定性诊断、非正确性分、勿优化〕：τ={j.get('kendall_tau')}，"
              f"成对一致 {_pct(j.get('pairwise_agreement'))}（n={j.get('n')}）")
    elif j:
        print(f"  独立裁判：{j.get('error') or j.get('note') or '不可用'}")


# ---------------- markdown ----------------
def markdown(date: str, report: dict) -> str:
    f = report.get("faithfulness") or {}
    r = report.get("ranking") or {}
    fb = r.get("feedback") or {}
    j = r.get("independent_judge") or {}
    L: list[str] = []

    L.append(f"# Agent Radar eval — {date}\n")
    L.append(f"> {top_line(report)}\n")

    # faithfulness
    L.append("## 忠实度（详解是否忠于原文）\n")
    if f.get("mean_support_rate") is not None:
        L.append(f"support_rate 均值 **{_pct(f['mean_support_rate'])}**，"
                 f"基于 **{f.get('n_scored', 0)}/{f.get('n_total', 0)}** 篇（有原文且有事实陈述）；"
                 f"跳过 {f.get('n_skipped', 0)} 篇"
                 + (f"（{ '，'.join(f'{k}×{v}' for k,v in f['skip_breakdown'].items()) }）"
                    if f.get("skip_breakdown") else "")
                 + f"；标记幻觉/失真 **{f.get('n_issues', 0)}** 处。\n")
    else:
        L.append(f"无可评篇（{f.get('n_total', 0)} 篇全部跳过——多为没进深读 top-k 的条目）。\n")
    if f.get("rate_limited"):
        L.append("> ⚠ 本次撞额度/限流提前停手，未评完；额度恢复后重跑会自动续上（已评走缓存）。\n")

    rows = _faith_rows(f)
    if rows:
        L.append("| 详解 | grounding | support_rate | 标记 |")
        L.append("|---|---|---|---|")
        for it in sorted(rows, key=lambda x: (x.get("support_rate") is None, x.get("support_rate", 0))):
            L.append(f"| {(it.get('title') or '')[:54]} | {it.get('grounding_source', '?')} "
                     f"| {_pct(it.get('support_rate'))} | {len(it.get('issues', []))} |")
        L.append("")
    skipped = [it for it in f.get("items", []) if it.get("status") != "scored"]
    if skipped:
        L.append("**跳过**：" + "；".join(
            f"{(it.get('title') or '')[:36]}（{it.get('skip_reason') or it.get('status')}）"
            for it in skipped) + "\n")

    flagged = _flagged(f)
    if flagged:
        L.append("### 标记的问题（当「候选」核，full_text 近似可能有假阳性）\n")
        for it, c in flagged:
            L.append(f"- **{(it.get('title') or '')[:46]}** — `{c.get('verdict')}`：{c.get('claim')}")
            if c.get("why"):
                L.append(f"  - ↳ {c.get('why')}")
        L.append("")

    # ranking
    L.append("## 排序合理性\n")
    if fb.get("is_signal"):
        L.append(f"- **反馈成对准确率 {_pct(fb.get('pairwise_accuracy'))}**"
                 f"（👍 排在 👎 前 {fb.get('correct_pairs')}/{fb.get('n_pairs')} 对）")
    elif fb:
        L.append(f"- 反馈：{fb.get('note', '暂无')}"
                 f"（👍{fb.get('n_up', 0)} · 👎{fb.get('n_down', 0)} → {fb.get('n_pairs', 0)} 对）")
    if j and j.get("kendall_tau") is not None:
        L.append(f"- 独立裁判 **τ={j.get('kendall_tau')}**，成对一致 {_pct(j.get('pairwise_agreement'))}"
                 f"（n={j.get('n')}）—— 〔**稳定性/可复现性诊断，非「排得对不对」，勿优化**〕；"
                 f"低 τ 常见于质量相近的条目。")
    elif j:
        L.append(f"- 独立裁判：{j.get('error') or j.get('note') or '不可用'}")
    L.append("")
    L.append(f"---\n*schema v{report.get('schema_version', '?')} · "
             f"忠实度=逐条 support_rate 均值；分数稳、可跨天比。*\n")
    return "\n".join(L)


def emit(date: str, report: dict) -> None:
    """Print the console report and write the markdown alongside the json."""
    console(date, report)
    try:
        atomic_write_text(Paths.eval / f"{date}.md", markdown(date, report))
    except Exception as e:  # noqa: BLE001 — a report-write failure must not fail the eval
        print(f"  (markdown 报告写入失败: {e!r})")


# ---------------- cross-day trend ----------------
def trend_rows(schema_version: int) -> list[dict]:
    """Aggregate data/eval/*.json into trend rows (newest first). Skips bad / old-schema
    files so the table only mixes comparable runs."""
    rows: list[dict] = []
    try:
        files = sorted(Paths.eval.glob("*.json"), reverse=True)
    except Exception:  # noqa: BLE001
        return rows
    for p in files:
        rep = read_json(p)
        if not isinstance(rep, dict) or rep.get("schema_version") != schema_version:
            continue
        f = rep.get("faithfulness") or {}
        r = rep.get("ranking") or {}
        fb = r.get("feedback") or {}
        j = r.get("independent_judge") or {}
        # grounding mix per day — sidecar (exact) vs full_text (approximate) days must not
        # be read as one continuous line, so the trend surfaces what each mean stands on.
        g = Counter(it.get("grounding_source") or "?"
                    for it in f.get("items", []) if it.get("status") == "scored")
        rows.append({
            "date": rep.get("date") or p.stem,
            "faith": f.get("mean_support_rate"),
            "n_scored": f.get("n_scored", 0), "n_total": f.get("n_total", 0),
            "grounding": "+".join(f"{k}×{v}" for k, v in sorted(g.items())) or "—",
            "g_kinds": sorted(g),
            "fb_signal": bool(fb.get("is_signal")),
            "fb_acc": fb.get("pairwise_accuracy"), "fb_pairs": fb.get("n_pairs", 0),
            "tau": j.get("kendall_tau"), "judge_n": j.get("n"),
        })
    return rows


def print_trend(schema_version: int, min_days: int = 3) -> int:
    rows = trend_rows(schema_version)
    if not rows:
        print("还没有可用的 eval 报告（data/eval/*.json）——先跑 `radar --mode eval <date>`。")
        return 0
    print(f"\n=== eval 趋势（{len(rows)} 天，schema v{schema_version}）===")
    print(f"{'日期':<12}{'忠实度(覆盖)':<22}{'grounding':<24}{'排序-反馈':<22}{'独立裁判τ〔诊断〕'}")
    for r in rows:
        faith = (f"{_pct(r['faith'])} ({r['n_scored']}/{r['n_total']})"
                 if r["faith"] is not None else f"—（{r['n_total']} 全跳过）")
        fbk = (f"{_pct(r['fb_acc'])}（{r['fb_pairs']}对）" if r["fb_signal"]
               else f"样本太少（{r['fb_pairs']}对）")
        tau = f"τ={r['tau']} (n={r['judge_n']})" if r["tau"] is not None else "—"
        print(f"{r['date']:<12}{faith:<22}{r.get('grounding', '—'):<24}{fbk:<22}{tau}")
    kinds = {k for r in rows for k in r.get("g_kinds", [])}
    if kinds:
        print("\ngrounding：sidecar=深读模型真看的原文（精确）；full_text=近似兜底（可能假阳性）。"
              "混合 grounding 的天、以及详解格式改版（压缩件→四轴）前后的天，均值不可直接连线比较。")
    if len(rows) < min_days:
        print(f"\n⚠ 数据还少（{len(rows)} 天），趋势不足为凭——多跑几天再看。"
              "（arXiv 全文修复后，新 daily 的 arXiv 条目忠实度应在此表上走高。）")
    return 0
