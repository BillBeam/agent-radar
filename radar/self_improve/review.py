"""E1 data-level reviewer — `radar --mode review`（每周日 launchd 自动跑，手动随时可跑）.

Closes the "ruler built but nobody reads it" loop (SPEC §9 · E1): aggregate the system's
own products — eval trend (per-day data/eval/*.json IS the structured cross-day store),
votes, top-10 source mix, the self_applicable/target_component annotations triage already
emits, critic skip stats, and the WATCHLIST — into one weekly review markdown with an LLM
DRAFT (observations + suggestions), then push a top-line summary to the DingTalk 1v1.

Hard lines (per the user's structural correction, 2026-07-05):
  * AUTO = run + deliver, NEVER apply — this module writes ONLY
    data/self_improve/reviews/{date}-review.md (+ the gitignored reading page under
    data/web/); no config/prompt/code is ever touched.
  * every data source degrades independently: missing/broken → an honest "暂无数据" line,
    never a crash; LLM/quota failure degrades to data-sections-only; page-publish failure
    only drops the link; a DingTalk push failure only logs (the local report exists).
  * the pushed summary passes the same leak_scan 口径 as committed artifacts (private
    channel, same red line) — on any hit it degrades to a generic pointer; the reading
    page passes the same scan BEFORE it is written/deployed (publish.py).
  * the push and the report are written FOR THE USER, not for a developer console
    (2026-07-05 feedback: "可读性很差"): no code constants / internal field names
    (MIN_PAIRS, sidecar, grounding, support_rate, D 阶, 可比天数…), every number carries a
    one-phrase explanation of what it counts, and the full report is a tappable web page —
    never a local file path on his phone.
"""
from __future__ import annotations

import json
import os
import re
from collections import Counter
from datetime import datetime, timedelta
from typing import Any, Optional

import requests

from ..core.config import Paths, RadarConfig, load_config
from ..core.io import atomic_write_text, read_json
from ..core.text import demote_headings, smart_truncate
from ..eval import report as eval_report
from ..eval.ranking import MIN_PAIRS
from ..eval.run import EVAL_SCHEMA_VERSION
from .leak_scan import scan_text
from .publish import publish_review

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
                         "skips": [{"title": smart_truncate(s.get("title") or "", 60),
                                    "conf": s.get("conf"), "why": s.get("why")} for s in skips]})
    except Exception:  # noqa: BLE001
        pass
    g["critic"] = crit

    try:  # 5) WATCHLIST（盘点对象）
        g["watchlist"] = (WATCHLIST_FILE.read_text(encoding="utf-8")
                          if WATCHLIST_FILE.exists() else None)
    except Exception:  # noqa: BLE001
        g["watchlist"] = None

    runs: list[dict] = []
    try:  # 6) run health — the daily digest archives carry the rerank-degradation banner,
        #    so「本周运行正常」in the summary rests on data, not on a claim
        for p in sorted(Paths.digests.rglob("????-??-??.md")):
            try:
                degraded = "排序降级" in p.read_text(encoding="utf-8")
            except Exception:  # noqa: BLE001
                continue
            runs.append({"date": p.stem, "degraded": degraded})
    except Exception:  # noqa: BLE001
        pass
    g["run_health"] = runs
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
    # 480s：07-05 真跑 300s 超时一次（长中文草案 + 14K 数据），与 rerank 的同款教训（240→480）
    res = llm.complete(f"数据 JSON：\n{payload}", system=system,
                       model=config.models.judge, timeout=480, retries=1, tag="review")
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


# ---------------- summary（推钉钉的正文：四段人话，像同事的周更，不是监控面板） ----------------
# 纪律（用户 2026-07-05 拍板）：内部常量/代码词一律不得出现（MIN_PAIRS、sidecar、grounding、
# support_rate、D 阶、可比天数…），每个数字自带一句「它数的是什么」；链接行由 run_review 拼接。
def _health_line(g: dict) -> str:
    runs = (g.get("run_health") or [])[-7:]
    if not runs:
        return "🩺 运行：本地还没有日报记录——daily 跑起来后，这里会有每周的健康小结。"
    span = (f"最近 {len(runs)} 期日报（{runs[0]['date']} ~ {runs[-1]['date']}）"
            if len(runs) > 1 else f"最近一期日报（{runs[0]['date']}）")
    bad = [r["date"] for r in runs if r.get("degraded")]
    if not bad:
        return f"🩺 运行：{span}全部正常出刊，排序都没降级。"
    return (f"🩺 运行：{span}里，{'、'.join(bad)} 这天排序超时退回了粗排"
            f"（当天推送顶部有标注），其余正常。")


def _quality_line(g: dict) -> str:
    r0 = next((r for r in (g.get("eval_trend") or []) if r.get("faith") is not None), None)
    if r0 is None:
        return "🔍 详解质量：暂无抽查数据（每天日报跑完会自动抽查一轮）。"
    pct = round(r0["faith"] * 100)
    n, total = r0.get("n_scored", 0), r0.get("n_total", 0)
    briefs = max(total - n, 0)
    tail = f"（当天另外 {briefs} 条只有一句话简介，无需核查）" if briefs else ""
    if pct >= 100:
        return (f"🔍 详解质量：抽查了 {r0.get('date')} 深读的全部 {n} 篇，"
                f"每条事实陈述都能在原文里找到依据，零幻觉{tail}。")
    return (f"🔍 详解质量：抽查了 {r0.get('date')} 深读的 {n} 篇，{pct}% 的事实陈述能在原文找到依据，"
            f"少数出入已在完整周报里点到具体位置{tail}。")


def _votes_line(g: dict) -> str:
    votes = g.get("votes") or []
    up = sum(v.get("up", 0) for v in votes)
    down = sum(v.get("down", 0) for v in votes)
    total = up + down
    mp = g.get("min_pairs", MIN_PAIRS)
    if total == 0:
        return (f"🗳 你的投票：还没有票。在每天的卡片上顺手点 👍/👎——同一天里的赞和踩会两两组成对比，"
                f"凑满 {mp} 次对比，排序就开始按你的口味校准。")
    best_row = max(votes, key=lambda v: v.get("pairs", 0))
    best = best_row.get("pairs", 0)
    if best >= mp:
        return (f"🗳 你的投票：累计 {total} 票（👍{up}/👎{down}），已经够排序开始学你的口味了——"
                f"之后每一票都在继续校准。")
    example = (f"目前最多的一天是 {best_row['date']}：{best_row['up']} 赞 × {best_row['down']} 踩"
               f"＝{best} 次对比" if best else "目前还没有哪天同时有赞和踩")
    return (f"🗳 你的投票：累计 {total} 票（👍{up}/👎{down}）。排序校准吃的是「同一天里赞×踩的两两对比」——"
            f"{example}，凑满 {mp} 次就开始生效——还差 {mp - best} 次，"
            f"找一天把喜欢和不喜欢的各点几条就够了。")


def _decide_line(draft: Optional[str], draft_reason: Optional[str]) -> str:
    if draft:
        n = _count_suggestions(draft)
        if n:
            return (f"📝 待拍板：本周有 {n} 条改进草案在完整周报里等你点头——"
                    f"系统只起草，改不改、怎么改都由你。")
        return "📝 待拍板：本周没有需要你拍板的改进建议。"
    return "📝 待拍板：本周的 AI 观察稿没生成成功，周报里是完整的数据记录；不影响下周自动重试。"


def build_summary(g: dict, draft: Optional[str], draft_reason: Optional[str]) -> str:
    """四段：运行 / 详解质量 / 你该做什么 / 有没有要拍板的。不含链接行（推送时拼接）。"""
    return "\n\n".join([_health_line(g), _quality_line(g),
                        _votes_line(g), _decide_line(draft, draft_reason)])


# ---------------- render（周报 markdown——会渲染成阅读页，同一套术语纪律） ----------------
# 纪律第二条（用户 2026-07-05 晚追加）：页面只放**计算过的人话结论**，不罗列原始清单
# （论文题目墙/逐日源分布/观察清单原文都是给机器或审计看的，原始数据在 data/ 各文件里一字不丢）。
def _human_grounding(s: Optional[str]) -> str:
    """'sidecar×6' / 'full_text×2+sidecar×4' → 人话（×N＝按该口径核对的篇数）。"""
    return (s or "—").replace("sidecar", "深读原文").replace("full_text", "重取原文")


_COMPONENT_CN = {"memory": "记忆", "orchestration": "编排", "eval": "评测", "deepread": "深读",
                 "quality_gate": "质量闸", "llm_backend": "模型后端", "triage": "粗筛",
                 "rerank": "排序", "fetch": "抓取", "synthesize": "成稿", "deliver": "投递"}
_TREND_ROWS_CAP = 8      # 页面只放最近 8 次抽查，更早的在本地数据里


def _week_dates(date: str, days: int = 7) -> set[str]:
    """The review week = `date` and the 6 days before it (deterministic, string dates)."""
    try:
        end = datetime.strptime(date, "%Y-%m-%d").date()
    except ValueError:
        return set()
    return {(end - timedelta(days=i)).isoformat() for i in range(days)}


def render_markdown(g: dict, draft: Optional[str], draft_reason: Optional[str],
                    summary: str, date: str) -> str:
    L: list[str] = []
    L.append(f"# Agent Radar 周报 — {date}\n")
    L.append(f"> 生成于 {g.get('generated_at')}。这份周报只做**观察与草案**——"
             f"**不会被自动应用**；要不要改、怎么改，由你拍板后人工执行。\n")
    L.append("## 一眼看完\n")
    L.append(summary + "\n")

    wk = _week_dates(date)

    L.append("## 1. 详解质量走势（忠实度抽查）\n")
    trend = g.get("eval_trend") or []
    if trend:
        L.append("| 日期 | 忠实度（核查/全部） | 核对依据 | 投票对比 | 复排一致度 τ（仅诊断） |")
        L.append("|---|---|---|---|---|")
        for r in trend[:_TREND_ROWS_CAP]:
            faith = (f"{round(r['faith'] * 100)}%（{r.get('n_scored', 0)}/{r.get('n_total', 0)} 篇）"
                     if r.get("faith") is not None else f"—（{r.get('n_total', 0)} 篇全跳过）")
            fbk = (f"{round((r.get('fb_acc') or 0) * 100)}%（{r.get('fb_pairs', 0)} 次对比）"
                   if r.get("fb_signal") else f"对比不足（仅 {r.get('fb_pairs', 0)} 次）")
            tau = f"τ={r['tau']}（{r.get('judge_n')} 条）" if r.get("tau") is not None else "—"
            L.append(f"| {r.get('date')} | {faith} | {_human_grounding(r.get('grounding'))} "
                     f"| {fbk} | {tau} |")
        L.append("")
        if len(trend) > _TREND_ROWS_CAP:
            L.append(f"（只列最近 {_TREND_ROWS_CAP} 次抽查，更早的在本地数据里。）\n")
        L.append("**怎么读**：")
        L.append("- 忠实度＝抽查深读详解、其中事实陈述能在原文找到依据的比例；"
                 "「6/10 篇」＝当天 10 条里 6 条有完整详解（其余是一句话简介，无需核查）。")
        L.append("- 核对依据：「深读原文」＝对着深读模型当时真实读到的原文核对（准）；"
                 "「重取原文」＝事后重新抓的（可能有出入）——两种口径的天、以及详解格式改版前后的天，"
                 "别连成一条线比。")
        L.append("- 投票对比＝同一天里赞×踩两两组成的对比次数；τ＝换个独立评委再排一遍的一致程度，"
                 "只看排序稳不稳定，不是质量分。")
        L.append("")
    else:
        L.append("暂无质量抽查数据——日报跑起来后每天会自动抽查一轮。\n")

    L.append("## 2. 你的投票\n")
    votes = g.get("votes") or []
    if votes:
        for v in votes:
            L.append(f"- {v['date']}：👍{v['up']} / 👎{v['down']}（同日两两对比 {v['pairs']} 次）")
        up = sum(v["up"] for v in votes)
        down = sum(v["down"] for v in votes)
        best = max(v["pairs"] for v in votes)
        L.append(f"- **累计 👍{up} / 👎{down}；单日最多 {best} 次对比，"
                 f"凑满 {g.get('min_pairs')} 次（同一天里每个赞×每个踩算一次）排序就开始按你的口味校准**\n")
    else:
        L.append("还没有投票记录（钉钉卡片上的 👍/👎 和本地 `radar mark` 都会记进来）。\n")

    L.append("## 3. 本周精选来自哪里\n")
    days = g.get("digest_days") or []
    week_days = [d for d in days if d.get("date") in wk]
    if week_days:
        tot: Counter = Counter()
        for d in week_days:
            tot.update(d.get("sources") or {})
        mix = "、".join(f"{k} {v} 条" for k, v in tot.most_common())
        L.append(f"本周 {len(week_days)} 期日报共精选 {sum(tot.values())} 条：{mix}。")
        if len(days) > 1:      # 新面孔：最近一期里此前从未出现过的源
            seen_before = set().union(*(set(d.get("sources") or {}) for d in days[:-1]))
            fresh = [k for k in (days[-1].get("sources") or {}) if k not in seen_before]
            if fresh:
                L.append(f"最近一期（{days[-1]['date']}）首次出现的源：{'、'.join(fresh)}。")
        L.append("")
    else:
        L.append("本周暂无日报数据。\n")

    L.append("## 4. 可反哺雷达自身的内容\n")
    sa_week = [s for s in (g.get("self_applicable") or []) if s.get("date") in wk]
    if sa_week:
        comp = Counter(_COMPONENT_CN.get(s.get("target_component") or "", s.get("target_component") or "其他")
                       for s in sa_week)
        mix = "、".join(f"{k} {v} 条" for k, v in comp.most_common())
        L.append(f"本周精选里有 {len(sa_week)} 条与雷达自己的构造直接相关（按可能受益的环节数：{mix}）。"
                 "它们讲的方法可能用得回这套系统，已记录为自我改进的原料；"
                 "若 AI 草稿从中看出可行改动，会出现在下方草案段。\n")
    else:
        L.append("本周没有与雷达自身相关的条目。\n")

    L.append("## 5. 质检「可跳过」标记\n")
    crit_week = [c for c in (g.get("critic") or []) if c.get("date") in wk]
    if crit_week:
        L.append("发布前的质检会把高置信的水货（重复/无实质）标出来、让出深读名额，本周拦下的：\n")
        for c in crit_week:
            L.append(f"- {c['date']}：{c['n_skip']}/{c['n']} 条被标可跳过"
                     + ("" if not c["skips"] else "——"
                        + "；".join(f"「{s['title']}」({s['conf']}) {s['why'] or ''}".strip()
                                    for s in c["skips"][:3])))
        L.append("")
    else:
        L.append("暂无质检数据。\n")

    L.append("## 6. 观察项清单\n")
    wl = g.get("watchlist")
    wl_items = re.findall(r"^##\s+(.+?)\s*$", wl, re.M) if wl else []
    if wl_items:
        L.append(f"你定的「先不动、但要盯」共 {len(wl_items)} 项：\n")
        for t in wl_items:
            L.append(f"- {t}")
        L.append("")
        L.append("（每项的判据与出处在本地清单里；有 AI 草稿的周会在下方逐项盘点进展。）\n")
    elif wl:
        # 清单存在但没有 ## 条目结构 → 原文降级内嵌（#/## 降为加粗行，别抢周报层级）
        L.append(demote_headings(wl.strip()) + "\n")
    else:
        L.append("（观察项清单还没建——之后周报会逐项盘点。）\n")

    L.append("## 7. 观察与改进草案（AI 起草 · 等你拍板）\n")
    if draft:
        L.append(draft + "\n")
    else:
        L.append(f"（本次 AI 草稿没生成——原因：{draft_reason or '未调用'}。上面各节仍是完整数据；"
                 "下周日会自动重试，手动 `radar --mode review` 也可随时补一次。）\n")

    L.append("---\n*每周日自动生成：只读系统自己的数据 → 提观察与草案 → 你拍板。"
             "自动的只有「跑与送达」，任何改动都要人来做。*\n")
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
                   "msgParam": json.dumps({"title": "Agent Radar 周报", "text": text},
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
def _link_block(url: Optional[str], status: str) -> str:
    """推送末段：有页给可点链接；没页给一句如实说明——永远不给本地文件路径（手机上是死文字）。"""
    if url:
        return f"\n\n👉 [点开完整周报（网页版）]({url})"
    if status == "leak":
        return ("\n\n⚠️ 完整周报这次没发布网页版：发布前自检发现疑似敏感词，先压下了；"
                "周报本体已在本地生成，排查后可手动重发。")
    if status in ("disabled", "missing"):
        return "\n\n（完整周报已在本地归档；网页版未配置，配好后会自动带链接。）"
    return "\n\n（完整周报网页版这次没部署成功——要点都在上面四条里，本地报告完整；下周会再试。）"


def run_review(llm: Any = None, config: Optional[RadarConfig] = None,
               dry_run: bool = False) -> int:
    """Gather → (LLM draft) → write reviews/{date}-review.md → publish reading page → push.
    dry_run: data sections only — no LLM call, no publish, no DingTalk push, still writes the file."""
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
    summary = build_summary(g, draft, draft_reason)
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

    if dry_run:
        return 0

    # 周报上阅读页：leak 闸在写盘/部署之前（publish.py）；任何失败只降级链接、不挡推送
    url, pub_status, pub_detail = publish_review(md, date=date, config=config)
    print(f"周报网页：{url if url else f'未发布（{pub_status}：{pub_detail}）'}")

    push_text = f"📊 Agent Radar 周报 · {date}\n\n{summary}{_link_block(url, pub_status)}"
    # same leak 口径 as committed artifacts: a hit degrades the push, never sends原文
    hits, warn = scan_text(push_text, source="review-summary")
    if warn:
        print(warn)
    if hits:
        push_text = ("📊 本周周报已生成，但外发自检发现疑似敏感词，这次不推正文——"
                     "请在本地打开本周周报排查后手动重发。")
        print(f"⚠ 摘要泄漏自检命中 {len(hits)} 处——已降级为通用指针，去 {out_rel} 排查。")
    ok, detail = push_summary_dingtalk(push_text)
    print(f"钉钉摘要推送：{'✓ ' + detail if ok else '✗ ' + detail}（失败只记录，不影响报告本体）")
    return 0
