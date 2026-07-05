"""E1 data-level reviewer — `radar --mode review`（每周日 launchd 自动跑，手动随时可跑）.

Closes the "ruler built but nobody reads it" loop (SPEC §9 · E1): aggregate the system's
own products — eval trend (per-day data/eval/*.json IS the structured cross-day store),
votes, top-10 source mix, the self_applicable/target_component annotations triage already
emits, critic skip stats, and the WATCHLIST — into one weekly review markdown with an LLM
DRAFT (observations + suggestions), then push a top-line summary to the DingTalk 1v1.

Hard lines (per the user's structural correction, 2026-07-05):
  * AUTO = run + deliver, NEVER apply — this module writes ONLY
    data/self_improve/reviews/{date}-review.md; no config/prompt/code is ever touched.
  * every data source degrades independently: missing/broken → an honest "暂无数据" line,
    never a crash; LLM/quota failure degrades to data-sections-only; a DingTalk push
    failure only logs (the local report already exists).
  * the pushed summary passes the same leak_scan 口径 as committed artifacts (private
    channel, same red line) — on any hit it degrades to a generic pointer.
"""
from __future__ import annotations

import json
import os
import re
from collections import Counter
from datetime import datetime
from typing import Any, Optional

import requests

from ..core.config import Paths, RadarConfig, load_config
from ..core.io import atomic_write_text, read_json
from ..eval import report as eval_report
from ..eval.ranking import MIN_PAIRS
from ..eval.run import EVAL_SCHEMA_VERSION
from .leak_scan import scan_text

SELF_IMPROVE_DIR = Paths.data / "self_improve"
REVIEWS_DIR = SELF_IMPROVE_DIR / "reviews"
WATCHLIST_FILE = SELF_IMPROVE_DIR / "WATCHLIST.md"

_TOKEN_URL = "https://api.dingtalk.com/v1.0/oauth2/accessToken"
_OTO_URL = "https://api.dingtalk.com/v1.0/robot/oToMessages/batchSend"
_DRAFT_PAYLOAD_CAP = 14000     # chars of aggregate JSON fed to the reviewer LLM


# ---------------- gather (read-only; every source degrades独立) ----------------
def gather(now: Optional[str] = None) -> dict:
    """Aggregate the system's own products. Read-only; each block fails soft."""
    g: dict[str, Any] = {
        "generated_at": now or datetime.now().strftime("%Y-%m-%d %H:%M"),
        "min_pairs": MIN_PAIRS,
    }

    try:  # 1) eval trend — data/eval/{date}.json already IS the cross-day structured store
        g["eval_trend"] = eval_report.trend_rows(EVAL_SCHEMA_VERSION)
    except Exception:  # noqa: BLE001
        g["eval_trend"] = []

    votes: list[dict] = []
    try:  # 2) votes（D 阶备粮）— pairs use ranking.py's cross-product口径
        for p in sorted(Paths.feedback.glob("*.json")):
            d = read_json(p)
            if not isinstance(d, dict) or not d:
                continue
            up = sum(1 for v in d.values() if isinstance(v, dict) and v.get("vote") == "up")
            down = sum(1 for v in d.values() if isinstance(v, dict) and v.get("vote") == "down")
            votes.append({"date": p.stem, "up": up, "down": down, "pairs": up * down})
    except Exception:  # noqa: BLE001
        pass
    g["votes"] = votes

    days: list[dict] = []
    sa: list[dict] = []
    try:  # 3) top-10 source mix + self_applicable annotations (producer exists, this is the consumer)
        for p in sorted(Paths.digests.glob("*.items.json")):
            items = read_json(p)
            if not isinstance(items, list) or not items:
                continue
            date = p.name.replace(".items.json", "")
            days.append({"date": date, "n": len(items),
                         "sources": dict(Counter((it.get("source_name") or "?") for it in items))})
            for it in items:
                if isinstance(it, dict) and it.get("self_applicable"):
                    sa.append({"date": date, "id": it.get("id"),
                               "title": (it.get("title") or "")[:80],
                               "target_component": it.get("target_component")})
    except Exception:  # noqa: BLE001
        pass
    g["digest_days"] = days
    g["self_applicable"] = sa

    crit: list[dict] = []
    try:  # 4) critic skip stats
        for p in sorted(Paths.critic.glob("*.json")):
            d = read_json(p)
            items = (d or {}).get("items") if isinstance(d, dict) else None
            if not isinstance(items, list):
                continue
            skips = [i for i in items if isinstance(i, dict) and i.get("skip")]
            crit.append({"date": (d.get("date") or p.stem), "n": len(items), "n_skip": len(skips),
                         "skips": [{"title": (s.get("title") or "")[:60],
                                    "conf": s.get("conf"), "why": s.get("why")} for s in skips]})
    except Exception:  # noqa: BLE001
        pass
    g["critic"] = crit

    try:  # 5) WATCHLIST（盘点对象）
        g["watchlist"] = (WATCHLIST_FILE.read_text(encoding="utf-8")
                          if WATCHLIST_FILE.exists() else None)
    except Exception:  # noqa: BLE001
        g["watchlist"] = None
    return g


# ---------------- LLM draft（观察 + 草案；失败降级、绝不自动应用） ----------------
def _draft(llm: Any, g: dict, config: RadarConfig) -> tuple[Optional[str], Optional[str]]:
    """(draft_markdown, fail_reason) — draft=None means the review degrades to data-only."""
    try:
        system = Paths.prompts.joinpath("review.md").read_text(encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        return None, f"reviewer prompt 读取失败：{e!r}"
    payload = json.dumps(g, ensure_ascii=False, default=str,
                         separators=(",", ":"))[:_DRAFT_PAYLOAD_CAP]
    res = llm.complete(f"数据 JSON：\n{payload}", system=system,
                       model=config.models.judge, timeout=300, retries=1, tag="review")
    if getattr(res, "ok", False) and (res.text or "").strip():
        return res.text.strip(), None
    return None, (getattr(res, "error", None) or "空响应")


def _count_suggestions(draft: Optional[str]) -> int:
    """Heuristic (display only): numbered lines inside the 草案建议 section — NOT the whole
    draft, whose 观察/WATCHLIST sections are numbered too (first real run counted 6+1+5=12
    for a single suggestion)."""
    if not draft:
        return 0
    m = re.search(r"草案建议", draft)
    seg = draft[m.end():] if m else draft
    m2 = re.search(r"WATCHLIST", seg)
    if m2:
        seg = seg[:m2.start()]
    return len(re.findall(r"^\s{0,3}(?:\*\*)?\d+[.、]", seg, re.M))


# ---------------- summary（推钉钉的 top-line；四句 + 全文指针） ----------------
def build_summary(g: dict, draft: Optional[str], draft_reason: Optional[str],
                  out_rel: str) -> str:
    lines: list[str] = []

    trend = g.get("eval_trend") or []
    scored = [r for r in trend if r.get("faith") is not None]
    if scored:
        r0 = scored[0]
        lines.append(f"📏 忠实度 {round(r0['faith'] * 100)}%"
                     f"（{r0.get('n_scored', 0)}/{r0.get('n_total', 0)} 篇，{r0.get('date')}，"
                     f"grounding {r0.get('grounding', '?')}）；可比天数 {len(trend)}")
    else:
        lines.append("📏 eval 尺子暂无读数（data/eval/ 空）")

    votes = g.get("votes") or []
    up = sum(v.get("up", 0) for v in votes)
    down = sum(v.get("down", 0) for v in votes)
    best = max((v.get("pairs", 0) for v in votes), default=0)
    mp = g.get("min_pairs", MIN_PAIRS)
    if best >= mp:
        lines.append(f"🗳 投票累计 👍{up}/👎{down}——单日 {best} 对 ≥ {mp}，排序反馈已构成信号")
    else:
        lines.append(f"🗳 投票累计 👍{up}/👎{down}——单日最高 {best} 对 < {mp}（MIN_PAIRS），"
                     f"排序反馈未成信号，D 阶还差 {mp - best} 对")

    days = g.get("digest_days") or []
    if days:
        d0 = days[-1]
        mix = "、".join(f"{k}×{v}" for k, v in
                        sorted(d0["sources"].items(), key=lambda kv: -kv[1]))
        lines.append(f"📚 最近一跑（{d0['date']}）top-{d0['n']} 源：{mix}")
    else:
        lines.append("📚 暂无 digest 数据")

    if draft:
        lines.append(f"📝 草案建议 {_count_suggestions(draft)} 条待拍板（零自动应用）")
    else:
        lines.append(f"📝 本次无 LLM 草案（{draft_reason or '未调用'}）——仅数据观察段")

    lines.append(f"\n全文：{out_rel}")
    return "\n\n".join(lines)


# ---------------- render（周报 markdown） ----------------
def render_markdown(g: dict, draft: Optional[str], draft_reason: Optional[str],
                    summary: str, date: str) -> str:
    L: list[str] = []
    L.append(f"# Agent Radar 周度 review — {date}\n")
    L.append(f"> 生成于 {g.get('generated_at')}。本报告只有**观察与草案**——"
             f"**不会被自动应用**；要不要改、怎么改，由用户拍板后人工执行。\n")
    L.append("## Top-line\n")
    L.append(summary + "\n")

    L.append("## 1. eval 趋势（尺子读数）\n")
    trend = g.get("eval_trend") or []
    if trend:
        L.append("| 日期 | 忠实度(覆盖) | grounding | 排序-反馈 | 独立裁判τ〔诊断〕 |")
        L.append("|---|---|---|---|---|")
        for r in trend:
            faith = (f"{round(r['faith'] * 100)}% ({r.get('n_scored', 0)}/{r.get('n_total', 0)})"
                     if r.get("faith") is not None else f"—（{r.get('n_total', 0)} 全跳过）")
            fbk = (f"{round((r.get('fb_acc') or 0) * 100)}%（{r.get('fb_pairs', 0)}对）"
                   if r.get("fb_signal") else f"样本太少（{r.get('fb_pairs', 0)}对）")
            tau = f"τ={r['tau']} (n={r.get('judge_n')})" if r.get("tau") is not None else "—"
            L.append(f"| {r.get('date')} | {faith} | {r.get('grounding', '—')} | {fbk} | {tau} |")
        L.append("")
        L.append("grounding：sidecar=深读模型真看的原文（精确）；full_text=近似兜底（可能假阳性）。"
                 "混合 grounding 的天、以及详解格式改版（压缩件→四轴）前后的天，均值不可直接连线比较。\n")
    else:
        L.append("暂无 eval 报告（data/eval/ 空）——每日 daily 跑完会自动补一份。\n")

    L.append("## 2. 投票（D 阶备粮）\n")
    votes = g.get("votes") or []
    if votes:
        for v in votes:
            L.append(f"- {v['date']}：👍{v['up']} / 👎{v['down']}（{v['pairs']} 对）")
        up = sum(v["up"] for v in votes)
        down = sum(v["down"] for v in votes)
        best = max(v["pairs"] for v in votes)
        L.append(f"- **累计 👍{up} / 👎{down}；单日最高 {best} 对，MIN_PAIRS={g.get('min_pairs')}**\n")
    else:
        L.append("暂无投票数据（钉钉卡 👍/👎 或 `radar mark`）。\n")

    L.append("## 3. top-10 源分布\n")
    days = g.get("digest_days") or []
    if days:
        for d in days:
            mix = "、".join(f"{k}×{v}" for k, v in
                            sorted(d["sources"].items(), key=lambda kv: -kv[1]))
            L.append(f"- {d['date']}（{d['n']} 条）：{mix}")
        L.append("")
    else:
        L.append("暂无 digest 数据。\n")

    L.append("## 4. 自相关标注（self_applicable → E1 原料）\n")
    sa = g.get("self_applicable") or []
    if sa:
        for s in sa:
            L.append(f"- {s['date']} [{s.get('target_component') or '?'}] {s['title']}")
        L.append("")
    else:
        L.append("本期无 self_applicable 标注条目。\n")

    L.append("## 5. critic 可跳过\n")
    crit = g.get("critic") or []
    if crit:
        for c in crit:
            L.append(f"- {c['date']}：{c['n_skip']}/{c['n']} 标可跳过"
                     + ("" if not c["skips"] else "——"
                        + "；".join(f"「{s['title']}」({s['conf']}) {s['why'] or ''}".strip()
                                    for s in c["skips"][:3])))
        L.append("")
    else:
        L.append("暂无 critic 旁车数据。\n")

    L.append("## 6. WATCHLIST 盘点\n")
    wl = g.get("watchlist")
    if wl:
        L.append(wl.strip() + "\n")
    else:
        L.append(f"（{WATCHLIST_FILE} 不存在——先播种观察项。）\n")

    L.append("## 7. 观察与草案建议（LLM 草案 · 零自动应用）\n")
    if draft:
        L.append(draft + "\n")
    else:
        L.append(f"（本次 LLM 草案不可用：{draft_reason or '未调用'}——只有上面的数据观察段。"
                 "额度恢复后手动 `radar --mode review` 可补。）\n")

    L.append("---\n*E1 数据级 reviewer：读标注与尺子 → 提草案 → 用户拍板。自动的是「跑与送达」，不是「改」。*\n")
    return "\n".join(L)


# ---------------- DingTalk push（失败只 log；同一泄漏口径自检） ----------------
def push_summary_dingtalk(text: str, *, session: Any = None) -> tuple[bool, str]:
    """Send the weekly top-line to the same 1v1 the daily card goes to (OTO sampleMarkdown).
    Never raises — the local report already exists; a push failure is only reported."""
    creds = {k: os.getenv(f"DINGTALK_{k.upper()}")
             for k in ("client_id", "client_secret", "robot_code", "user_id")}
    missing = [f"DINGTALK_{k.upper()}" for k, v in creds.items() if not v]
    if missing:
        return False, f"缺环境变量：{','.join(missing)}"
    s = session or requests.Session()
    s.trust_env = False               # DingTalk is domestic — never via the Western proxy
    try:
        r = s.post(_TOKEN_URL, timeout=20,
                   json={"appKey": creds["client_id"], "appSecret": creds["client_secret"]})
        r.raise_for_status()
        token = r.json()["accessToken"]
        payload = {"robotCode": creds["robot_code"], "userIds": [creds["user_id"]],
                   "msgKey": "sampleMarkdown",
                   "msgParam": json.dumps({"title": "Agent Radar 周度 review", "text": text},
                                          ensure_ascii=False)}
        r2 = s.post(_OTO_URL, json=payload, timeout=20,
                    headers={"x-acs-dingtalk-access-token": token,
                             "Content-Type": "application/json"})
        data = r2.json() if r2.content else {}
        if r2.status_code == 200 and not data.get("code"):
            return True, "sent"
        return False, f"status={r2.status_code} code={data.get('code')} msg={str(data.get('message'))[:120]}"
    except Exception as e:  # noqa: BLE001
        return False, repr(e)[:160]


# ---------------- orchestrate ----------------
def run_review(llm: Any = None, config: Optional[RadarConfig] = None,
               dry_run: bool = False) -> int:
    """Gather → (LLM draft) → write reviews/{date}-review.md → push summary.
    dry_run: data sections only — no LLM call, no DingTalk push, still writes the file."""
    config = config or load_config()
    g = gather()

    if dry_run:
        draft, draft_reason = None, "dry-run（未调 LLM）"
    elif llm is None:
        draft, draft_reason = None, "无 LLM 后端"
    else:
        draft, draft_reason = _draft(llm, g, config)

    date = datetime.now().strftime("%Y-%m-%d")
    out = REVIEWS_DIR / f"{date}-review.md"
    out_rel = f"data/self_improve/reviews/{date}-review.md"
    summary = build_summary(g, draft, draft_reason, out_rel)
    md = render_markdown(g, draft, draft_reason, summary, date)

    try:
        REVIEWS_DIR.mkdir(parents=True, exist_ok=True)
        atomic_write_text(out, md)
    except Exception as e:  # noqa: BLE001
        print(f"review 报告写入失败：{e!r}")
        return 1

    print(f"\n╔═ 周度 review {date} ═══════════════════════")
    for ln in summary.splitlines():
        if ln.strip():
            print(f"║ {ln}")
    print("╚════════════════════════════════════════════")
    print(f"报告 → {out_rel}")

    if not dry_run:
        # same leak 口径 as committed artifacts: a hit degrades the push, never sends原文
        hits, warn = scan_text(summary, source="review-summary")
        if warn:
            print(warn)
        push_text = summary if not hits else (
            f"周度 review 已生成（摘要含敏感词已抑制，命中 {len(hits)} 处——先本地看）\n\n全文：{out_rel}")
        if hits:
            print(f"⚠ 摘要泄漏自检命中 {len(hits)} 处——已降级为通用指针，去 {out_rel} 排查。")
        ok, detail = push_summary_dingtalk(push_text)
        print(f"钉钉摘要推送：{'✓ ' + detail if ok else '✗ ' + detail}（失败只记录，不影响报告本体）")
    return 0
