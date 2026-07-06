# 时效性收口真跑证据（2026-07-06）

对应 decisions.md「时效性与爆点捕捉全链路收口」。全部数字来自当天真实探针/真跑，无估算。

## 1. 饱和史重建（洞① 实锤）

各日候选池 arxiv-agents 计数（池文件重建；池内 arXiv 项与 fetch kept 一致——同 id 时 arxiv 先注册先赢）：

| 日期 | 池 arxiv 计数 | 顶格? |
|---|---|---|
| 06-20 | 50 | ✅ 顶格 |
| 06-21 | 50 | ✅ 顶格 |
| 06-26 | 50 | ✅ 顶格 |
| 06-30 | 50 | ✅ 顶格 |
| 07-03 | 50 | ✅ 顶格 |
| 07-05 | 43 | 未 |
| 07-06 | （arXiv 当天超时失败） | — |

n=200 探针重建 07-03 跑（13:57 UTC）的 96h 窗：**窗内匹配 >200**（200 条全落窗内、最老仅到
06-30T16:25）→ 旧 cap=50 当天至少截掉 150 条。07-05 窗重建：144 条匹配 vs cap 50。
当前（07-06，周末后）96h 窗内仅 40 条——截尾集中在工作日。

## 2. 源深度实测（洞①b + PART A 真值表数据源）

- GitHub releases.atom 服务端一律 **10 条**：claude-code 10条/9.33天（1.07 发/天）·
  **cline 10条/0.38天（26 发/天，sdk/* 洪泛）** · codex 10条/7天 · gemini-cli 10条/10天（nightly 1/天）。
- GitHub REST `/releases?per_page=30`（修后真跑）：claude-code **30 条 / 29.1 天**，newest3 = v2.1.201/200/199。
- RSS 服务端深度：openai-news 1028条/全史 · deepmind 100条/253天 · simonwillison 30条/10.5天 ·
  latent-space 20条/8.9天 · qwen 44条/全史 · newsletters 20条/86-434天 · **langchain-changelog 0 条（异常，validate 跟踪）**。
- HF daily_papers：50 条/8 天。HN：各关键词 48h 内命中 ≤3/20（6× 余量，无饱和）。

## 3. B1 修后真跑（2026-07-06）

cap=600、页 200、窗口边界早停：**1 页、40 条入窗、不饱和**（`saturated: false`）；
60s 超时下拉取成功（同日早晨旧代码 30s 超时三连败）。

## 4. B2 双臂真跑（真实 28 源、state 全部隔离在 scratchpad）

| 臂 | fetch_state | 结果 |
|---|---|---|
| 正常连跑 | 全源 last_success = 24h 前 | **catchup 放大 = {}（零膨胀）**，候选 56 |
| 停机 3 天 | 全源 last_success = 72h 前 | 全部 48h 源放大到 **84.0h**（72+12 余量）；arxiv/hf（96h leash）**未被放大**（84<96 零误伤）；候选 76 |

停机臂捞回的「配置窗外、补课窗内」dated 条目（节选）：**gh-claude-code v2.1.201（58.8h 老——
用户点名的案例本尊）**、v2.1.200、v2.1.199、cline CLI v3.0.36/35、codex 0.143.0-alpha.35、
simonwillison ×4（周末文）、latent-space ×2、deepmind A24 合作 ×1。

## 5. 洞③ 真实受害者（分数端）

从未投递（seen.json + 全部 items.json 零命中），但反复进池：

| 条目 | 进池次数 | 首次进池 |
|---|---|---|
| Introducing Claude Tag | **5 跑**（06-26/06-30/07-03/07-05/07-06） | 06-26 |
| Introducing Claude Sonnet 5 | 3 跑（07-03/07-05/07-06） | 07-03 |
| Redeploying Fable 5 / Claude Science | 各 3 跑 | 07-03 |

（四条今天仍在池——B3 生效后的下一个真跑即自然验收。）

## 6. B3 重放（旧 rubric×2 / 新 rubric×3，同池同序，haiku）

三轮迭代收敛（每轮同池同序 36 条=gh releases 17 + labs 9 + 论文样本 10 + 构造反事实 1；haiku）：

- **v1（只有豁免+护栏）**：合成 Opus 4.6 [5,5]→**[9,9,9]** 完美、v2.1.201 [5,5]→[2,1,2] 完美、
  nightly/sdk 纹丝不动、论文 Δ中位 0.0——**但真实 Sonnet 5 [4,8,1] 大方差、Claude Tag [1,2,1] 不动**
  → 暴露证据缺口（html 光杆标题）→ 加 B3b enrichment + 光杆标题规则。
- **v2（+B3b）**：Sonnet 5 修稳 **[8,9,8]**；Claude Tag 仍 [2,1,1]——og:description 查实是纯营销
  空话，haiku 压低是正确行为 → 加 B3c 新一方产品地板。
- **v3（+B3c，终版 rubric）**：**五项全 PASS**——

| 类别 | 条目 | 旧 rubric（v2 基线） | 终版 rubric |
|---|---|---|---|
| 重大 | Introducing Claude Sonnet 5（真实） | [4.0, 2.0] | **[8, 9, 9]** |
| 重大 | Introducing Claude Opus 4.6（构造） | [5.0, 3.0] | **[9, 9, 9]** |
| 新产品地板 | Introducing Claude Tag（真实受害者） | [0.0, 1.0] | **[6, 7, 6]**（过 6 分质量门=能上桌） |
| 补丁 | v2.1.201（用户点名） | [3.0, 5.0] | [2, 3, 2] |
| 补丁 | v2.1.200 | [2.0, 2.0] | [2, 3, 2] |
| 碎 tag | sdk/* ×9 · nightly ×2 · alpha ×1 | 全 0–1 | 全 0–1（纹丝不动） |
| 观察 | CLI v3.0.37（cline minor） | [5.0, 6.0] | [5, 5, 5]（合理中档） |
| 观察 | Redeploying Fable 5（ops 通告） | [0.0, 0.0] | [1, 1, 2]（正确不抬） |
| 论文×10 | —— | —— | Δ中位 0.0（变化只来自豁免条款） |

漏斗推演（07-05 当天数据）：Sonnet 5 得 8-9 → gate 排序键 ~8.4-9.4 稳进 24 finalist；Claude Tag
6-7 → 键 6.4-7.4，当天 cap 边界在 ~6.0-6.5（31 项抢 24 席）→ 也进 finalist；之后 rerank（未动）
按工程价值/对他新排 top-10。四条重大发布至今仍在池中——**下一个真跑就是自然验收**。
证据 JSON：`data/real-llm-runs/local/triage-exemption-replay-2026-07-05*.json`（gitignored）。

## 7. v2.1.201 端到端 trace

发布 07-03T23:50:35Z → 07-03 跑(13:57 UTC)在发布前、07-04 无跑 → **07-05 两跑进池 ✓**（97 候选，
triage 全池覆盖）→ 未过 gate/finalist（当天 97→24）→ 未投递 → 07-06 跑时 **48.9h 恰出 48h 窗**
（差 52 分钟）。结论：覆盖 ✓，例行补丁被压低 = 分寸正常；真正的洞是上表的 Claude Tag/Sonnet 5，
由 B3 修。
