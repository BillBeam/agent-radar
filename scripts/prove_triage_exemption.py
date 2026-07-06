"""B3 重放验证：重大发布豁免真的抬对了吗、护栏真的压住了吗（haiku 方差 → 多轮）。

用缓存候选池（默认 2026-07-05，含用户点名的 v2.1.201 + 真实重大发布 Introducing Claude
Sonnet 5 / Claude Tag）+ 构造的 Opus 4.6 级反事实（tests/fixtures/timeliness_cases.json，
id 带 SYNTHETIC 标记），同一子池、同一顺序，分别跑旧/新 rubric 各若干轮：

  ① 重大前沿发布（真实 + 构造）  → 新 rubric 稳定 ≥8
  ② v2.1.201 式例行补丁          → 新 rubric 稳定 ≤4（豁免不误抬）
  ③ nightly / sdk 碎 tag         → 分数不动（照旧低）
  ④ 论文样本                     → 新旧中位数差 ≤1.5（变化只来自豁免条款）

用法：
  python scripts/prove_triage_exemption.py                       # 默认 07-05 池，old×2 new×3
  python scripts/prove_triage_exemption.py --old-rubric git:HEAD # 旧 rubric 取 git 某版
"""
from __future__ import annotations

import argparse
import json
import random
import statistics
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import yaml  # noqa: E402

from radar.core.config import Paths, load_config  # noqa: E402
from radar.llm.claude_code import ClaudeCodeLLM  # noqa: E402
from radar.obs import Logger  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures" / "timeliness_cases.json"

# 观察对象（title 前缀匹配）：
#   major  = 标题即自明的新模型代际（+构造反事实）→ 硬断言 ≥8
#   surface= 靠 og:description 证据才判得动的重大能力发布 → 硬断言 ≥6（过质量门=能上桌）
#   patch  = 例行补丁 tag → 硬断言 ≤4；noise = nightly/sdk 碎 tag → 硬断言 ≤4
#   watch  = 边缘观察（minor 版本、ops 通告）→ 只报表不断言
MAJOR_TITLES = ("Introducing Claude Sonnet 5", "Introducing Claude Opus 4.6")
SURFACE_TITLES = ("Introducing Claude Tag",)
PATCH_TITLES = ("v2.1.201", "v2.1.200")
NOISE_PREFIXES = ("sdk/", "Release v0.51.0-nightly", "0.143.0-alpha")
WATCH_TITLES = ("CLI v3.0.37", "Redeploying Fable 5")


def enrich_bare_labs(pool: list[dict]) -> int:
    """labs 光杆标题条目现场补 og:description（复用 B3b 的 extract_description 真路径；
    不写生产缓存——重放只读不动 data/state）。"""
    from radar.core.config import load_config as _lc
    from radar.sources.html import HtmlSource, extract_description
    src = HtmlSource(config=_lc(), log=None)
    n = 0
    for c in pool:
        if c["category"] == "labs" and not (c.get("summary") or "").strip():
            try:
                c["summary"] = extract_description(src.get_text(c["url"], timeout=15, retries=1))
                n += bool(c["summary"])
            except Exception:  # noqa: BLE001 — 拉不到就保持光杆，重放照样诚实
                pass
    return n


def load_pool(date: str) -> list[dict]:
    cands = json.loads((Paths.candidates / f"{date}.json").read_text(encoding="utf-8"))
    releases = [c for c in cands if c["source_id"].startswith("gh-")]
    labs = [c for c in cands if c["category"] == "labs"]
    papers = [c for c in cands if c["category"] == "papers"]
    random.Random(42).shuffle(papers)          # 可复现的论文样本
    subset = releases + labs + papers[:10]
    fx = json.loads(FIXTURES.read_text(encoding="utf-8"))["major_release_counterfactual"]
    subset.append({"source_id": "SYNTHETIC", "source_name": fx["source_name"],
                   "category": fx["category"], "title": fx["title"],
                   "summary": fx["summary"], "url": fx["url"]})
    return subset


def rubric_text(spec: str) -> str:
    if spec.startswith("git:"):
        return subprocess.run(["git", "show", f"{spec[4:]}:prompts/triage.md"],
                              cwd=ROOT, capture_output=True, text=True, check=True).stdout
    return Path(spec).read_text(encoding="utf-8")


def triage_once(llm: ClaudeCodeLLM, pool: list[dict], system: str) -> dict[str, float]:
    """一轮重放，逐字复刻 TriageStage 的行格式与任务措辞。"""
    tax = yaml.safe_load(Paths.taxonomy_yaml.read_text(encoding="utf-8")) or {}
    lines = [f"[{i}] ({c['category']}|{c['source_name']}) {c['title']} :: "
             f"{(c.get('summary') or '')[:160]}" for i, c in enumerate(pool)]
    user = (f"TOPIC TAXONOMY (use exact strings): {', '.join(tax.get('topics', []))}\n"
            f"SELF_COMPONENTS: {', '.join(tax.get('self_components', []))}\n\n"
            f"Score these {len(pool)} candidates per the rubric. Return ONLY the JSON array.\n\n"
            + "\n".join(lines))
    data, res = llm.complete_json(user, system=system, model="haiku",
                                  tag="prove_exemption", timeout=300)
    if not isinstance(data, list):
        raise RuntimeError(f"triage replay failed: {res.error}")
    by_i = {int(r["i"]): float(r.get("score", 0)) for r in data if isinstance(r, dict) and "i" in r}
    return {pool[i]["title"]: s for i, s in by_i.items() if i < len(pool)}


def bucket(title: str) -> str:
    if any(title.startswith(t) for t in MAJOR_TITLES):
        return "major"
    if any(title.startswith(t) for t in SURFACE_TITLES):
        return "surface"
    if any(title.startswith(t) for t in PATCH_TITLES):
        return "patch"
    if any(title.startswith(p) for p in NOISE_PREFIXES):
        return "noise"
    if any(title.startswith(t) for t in WATCH_TITLES):
        return "watch"
    return "other"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="2026-07-05")
    ap.add_argument("--old-rubric", default="git:HEAD",
                    help="旧 rubric 来源：git:REF 或文件路径")
    ap.add_argument("--runs-old", type=int, default=2)
    ap.add_argument("--runs-new", type=int, default=3)
    args = ap.parse_args()

    cfg = load_config()
    llm = ClaudeCodeLLM(config=cfg, log=Logger("prove-exemption", echo=True))
    pool = load_pool(args.date)
    old_sys, new_sys = rubric_text(args.old_rubric), rubric_text(str(Paths.prompts / "triage.md"))
    if "Major frontier release exemption" not in new_sys:
        print("新 rubric 里没有豁免条款——你确定改过 prompts/triage.md 了吗？")
        return 2
    if "Major frontier release exemption" in old_sys:
        print(f"警告：旧 rubric（{args.old_rubric}）也含豁免——对照将失效。")

    enriched = enrich_bare_labs(pool)
    print(f"重放池 {len(pool)} 条（gh releases + labs + 论文样本×10 + 构造反事实×1）；"
          f"labs 光杆标题现场补 og:description ×{enriched}\n")
    runs: dict[str, list[dict[str, float]]] = {"old": [], "new": []}
    for arm, system, n in (("old", old_sys, args.runs_old), ("new", new_sys, args.runs_new)):
        for k in range(n):
            print(f"  [{arm} rubric] run {k + 1}/{n} …")
            runs[arm].append(triage_once(llm, pool, system))
    if not runs["old"]:
        runs["old"] = [{}]   # --runs-old 0：只验新臂（旧基线已另有记录），表里旧列为空

    # ---- 汇总表 ----
    papers_delta: list[float] = []
    rows = []
    for c in pool:
        t = c["title"]
        olds = [r.get(t) for r in runs["old"] if t in r]
        news = [r.get(t) for r in runs["new"] if t in r]
        if not news:
            continue
        b = bucket(t)
        if c["category"] == "papers" and olds:
            papers_delta.append(statistics.median(news) - statistics.median(olds))
        if b != "other" or c["category"] == "papers":
            rows.append((b, t[:52], olds or ["—"], news))

    order = {"major": 0, "surface": 1, "patch": 2, "noise": 3, "watch": 4, "other": 5}
    rows.sort(key=lambda r: order[r[0]])
    print("\n| 类别 | 条目 | 旧 rubric | 新 rubric |")
    print("|---|---|---|---|")
    for b, t, olds, news in rows:
        print(f"| {b} | {t} | {olds} | {news} |")

    # ---- 断言 ----
    def all_runs(pred, title_match) -> bool:
        ok = True
        for r in runs["new"]:
            for t, s in r.items():
                if title_match(t) and not pred(s):
                    ok = False
        return ok

    checks = [
        ("① 重大发布（标题自明的真实 Sonnet 5 + 构造 Opus 4.6）新 rubric 全轮 ≥8",
         all_runs(lambda s: s >= 8, lambda t: bucket(t) == "major")),
        ("①b Claude Tag（og:description 证据补齐后）新 rubric 全轮 ≥6（过质量门=能上桌）",
         all_runs(lambda s: s >= 6, lambda t: bucket(t) == "surface")),
        ("② 补丁 tag（v2.1.201/200）新 rubric 全轮 ≤4",
         all_runs(lambda s: s <= 4, lambda t: bucket(t) == "patch")),
        ("③ nightly/sdk 碎 tag 新 rubric 全轮 ≤4",
         all_runs(lambda s: s <= 4, lambda t: bucket(t) == "noise")),
        ("④ 论文样本新旧中位数差中位 ≤1.5",
         abs(statistics.median(papers_delta)) <= 1.5 if papers_delta else True),
    ]
    print()
    failed = False
    for name, ok in checks:
        print(("PASS " if ok else "FAIL ") + name)
        failed |= not ok
    if papers_delta:
        print(f"    论文样本 Δ中位数 = {statistics.median(papers_delta):+.1f} "
              f"（逐篇 Δ: {[round(d, 1) for d in papers_delta]}）")

    out = Path(__file__).resolve().parents[1] / "data" / "real-llm-runs" / "local"
    out.mkdir(parents=True, exist_ok=True)
    evidence = out / f"triage-exemption-replay-{args.date}.json"
    evidence.write_text(json.dumps(
        {"pool_size": len(pool), "runs": runs,
         "checks": {n: ok for n, ok in checks}},
        ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n证据落盘：{evidence}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
