# 逐源「不漏」真值表 — 时效性保证审计（2026-07-06）

> 背景：用户把时效性立为硬要求——「7 月 6 日跑的，不能漏掉 7 月 5 日→6 日之间的 agent/harness
> 技术爆点」。本文对六类源适配器逐一回答：**一次跑能看到多远、什么时候会漏、停机后能不能补回来**。
> 所有数字来自 2026-07-06 的真实探针（`decisions.md` 同日条目有过程）与历史跑数据重建，不是猜测。
> 结论先行：修复前有两个结构性漏口（arXiv 截尾、停机永久漏），2026-07-06 起由 B1/B1b/B2 关闭；
> 「爆点浮不上来」是第三个洞（分数端），由 B3（triage 重大发布豁免）关闭。

## 通用机制（先读这个，表格才有意义）

- **捕捉是 seen-based**：`seen.json` 只记「已投递」的条目。没被选中的候选**每天都会重新进池**，
  直到被投递或滑出时间窗——一次没上榜 ≠ 永久错过（实证：Introducing Claude Tag 连续 5 跑在池）。
- **窗口**：daily 配置窗 48h；论文源（arxiv / hf_papers）per-source 放宽到 96h（公告有 1–2 天滞后）。
  🆕/📚 显示分界统一为 96h（`models.is_display_fresh`）。
- **停机补课（B2，2026-07-06 起）**：`data/state/fetch_state.json` 持久化**每个源上次成功 fetch 的
  时间戳**。有效窗口 = max(配置窗, 距该源上次成功 + 12h 余量)，封顶 14 天。整机停机 3 天 → 重开首跑
  全源窗口自动放大到 ~84h；**单个源连挂**（如 2026-07-06 早 arXiv 三连超时）也一样为该源单独放大。
  正常连跑（gap≈24h + 12h 余量 < 48h）窗口不膨胀——实测零膨胀。
- **无日期/过窗（backfill）条目**：不滤掉，按「📚首次收录」入池，每源每跑限 8 条单调耗尽——
  迟到但绝不永久漏（代价：可能晚几天才上桌）。
- **全局安全帽**：triage_pool_cap=400（超出按 recency 裁——B1 后周中池可到 ~300，200 的旧帽会
  静默抵消 B1，已同步提高）。

## 真值表

| 源（适配器） | 窗口 | 一次跑深度（实测） | 饱和史（修复前） | 停机 1 / 3 / 7 天（B2 后） | 不漏保证等级 |
|---|---|---|---|---|---|
| **arxiv**（API 分页） | 96h | B1 后：600 硬顶，页 200、越过窗口边界即早停（平日实测 1 页） | **7 跑中 5 跑顶格 50**；07-03 的 96h 窗实测匹配 **>200** → 当天静默截掉 **150+** | API 支持任意回溯（submittedDate 排序）→ 补课完整；14 天顶 ≈ 500 条 < 600 硬顶 | **强**（残余：API 当天挂 → 次日 per-source 补课，96h leash 本身已容 1–2 天故障） |
| **hf_papers** | 96h | 服务端 ~50 条 ≈ 8 天 | 未见顶格（29–39/跑） | 1/3 天完整；>8 天超服务端深度丢失，**但 arXiv id 跨源去重 → 几乎全部经 arxiv 源补回** | **中（冗余强）**——它本质是 arxiv 的策展视图 |
| **rss** | 48h | 解析上限 60；服务端深度实测：openai-news 1028 条/全史 · deepmind 100/252d · simonwillison 30/10.5d · latent-space 20/8.9d · qwen 44/全史 · newsletters 20 条/86–434d | 无 | ≤7 天全部可补（最浅 latent-space ≈8.9d 贴边）；>10 天受 feed 自身深度限制 | **强（≤7 天）**；⚠ langchain-changelog 2026-07-06 探针返回 0 条（`--mode validate` 跟踪） |
| **github_releases** | 48h | B1b 后：**REST API 30 条优先**（claude-code 实测 30 条=29.1 天）；atom 兜底仅 **10 条（GitHub 服务端硬上限，我们此前的 limit=15 形同虚设）** | atom 10 条对高频仓极浅：**cline 的 10 条实测只跨 9 小时**（sdk/* 碎 tag 洪泛）——连两次日跑之间都盖不住 | claude-code ~1.07 发/天 → 30 条 ≈ 28 天 ✓；cline 含 sdk ~26 发/天 → 30 条 ≈ 1.2 天（碎 tag 会漏，CLI 主线 ~1 发/天仍够） | **强（REST 路径）/ 弱（atom 兜底 + 高频仓）**——兜底期间高频仓可能漏碎 tag，属已知诚实边界 |
| **hackernews** | 48h | `/search` 每关键词 20 条（按 points 降序，窗口内） | 实测当前 48h 内过闸 ≤5 条/跑（未见饱和） | 窗口内取最高分的 20 条 → 补课回溯受 `created_at_i>cutoff` 约束，不再受排序位次影响 | **中-强**（>20 条 ≥min_points/kw/窗才漏——从未观测到） |
|  | | ⚠ **2026-07-09 上游回归**：Algolia 把 `points` 从 `numericAttributesForFiltering` 摘掉，`numericFilters=points>N` 在两个端点都 400。适配器按设计吞掉 per-keyword 异常 → **整源静默归零一整天**（07-08 尚有 10-11 条）。已改 `/search`（custom ranking = points desc）+ 服务端 `created_at_i` 窗 + **客户端 points 闸**。教训：per-keyword 容错掩盖了全源故障，`sources_live` 仍报它活着。 | | | |
| **html**（anthropic news/eng） | 无窗口滤 | index 页 limit 20–25 卡；**B3b 起空摘要用文章页 og:description 补齐**（磁盘缓存、稳态零额外请求） | 无（backfill 8/源/跑单调耗尽） | 停机不丢（页面还在）；深度=index 页本身 | **强（慢滴灌）**——迟到可达数天，绝不永久漏；「迟到 + 光杆标题无证据 + 分数被压」曾让 Claude Sonnet 5 / Claude Tag 发布在池 3–5 跑上不了桌（分数端 B3 修、证据端 B3b 修） |

## 三个结构性洞与修复（2026-07-06 收口）

1. **B1 arXiv 截尾**：cap 50 → 600（跨页硬顶）+ 窗口感知分页早停 + 超时 30→60s。
   **keywords/categories 一个字未动**（A1 收紧零回退——提的是网眼内的容量，不是放大网眼）。
2. **B1b GitHub 深度**：releases.atom 服务端只给 10 条 → REST `/releases?per_page=30` 优先、
   atom 兜底（限流/故障时管线照活）。
3. **B2 停机补课**：per-source last-success 时间戳 → 有效窗口自动放大（+12h 余量，14 天封顶）。
   比「单一全局时间戳」强一档：单源连挂也补，不只整机停机。
4. **B3 分数端**（`prompts/triage.md`）：重大前沿发布豁免（新模型家族/旗舰代际/重大能力/协议变更
   → 8–10，即使工程细节薄）+ 单向护栏（补丁/nightly/例行 release notes 照旧 0–4，不因核心厂商抬分）
   + 光杆标题规则（标题自明的新模型代际照豁免；陌生名不许靠品牌猜高分）
   + **新一方产品地板**（核心厂商 Introducing 的命名新产品/agent 界面即使简介是营销空话 → 至少
   6–7 上桌；地区可用性/上架云/定价/办公室开张照旧 0–4；地板不抬 8+）。
5. **B3b 证据端**（`radar/sources/html.py`）：html 源空摘要条目用文章页 og:description 补齐
   （opt-in、磁盘缓存、每跑封顶、失败留空）——Claude Tag 这类「标题判不出轻重」的发布从此
   带着证据进 triage。

## 爆点渠道覆盖比对（PART C，2026-07-06 核查）

**已覆盖（确认）**：OpenAI News RSS ✓ · DeepMind Blog RSS ✓ · Anthropic News + Engineering（html+og:description）✓ ·
gh releases：claude-code / codex / gemini-cli / cline / aider / openhands / opencode / swe-agent / langgraph /
autogen / letta / mcp-servers / deepseek ✓ · HN（points 门槛）✓ · arXiv + HF papers ✓ · simonwillison /
latent-space / qwen-blog / hf-blog / langchain-changelog / 三大 newsletter ✓。

**核查过的候选缺口**：

| 候选 | 结论 | 依据（实测） |
|---|---|---|
| `modelcontextprotocol/modelcontextprotocol`（MCP 规范仓库）releases | **建议加（唯一增补提案，待拍板）** | releases.atom 活、全史仅 8 个 release 全是规范版本（低频高信号）；**下一版规范 2026-07-28 的 RC 已挂出**——三周后就是一次真协议爆点；github_releases 适配器零代码、一段 YAML |
| OpenAI platform changelog（API 级变更） | 缺，但**不建议现在加** | 重 JS 单页（445KB html），html 适配器（抽 `<a>` 卡片）形态不合；重大 API 变更已有 openai-news RSS + HN + simonwillison 冗余 |
| Anthropic docs release-notes / Claude 平台 changelog | 缺，但**不建议** | 同上（998KB 重 JS docs 页）；内容与 gh-claude-code releases / anthropic-news 高度重叠 |
| blog.google（DeepMind / AI 频道 RSS） | 可行但**不建议** | 两 feed 均活（各 20 条），但月度汇总+PR 稿占比高；Gemini 旗舰发布已有 deepmind-blog + HN + gemini-cli releases 三重冗余 |
| X/Twitter | **有意不覆盖** | 用户自己刷，边界明确 |

## 时效性的诚实边界（白纸黑字）

- **节奏**：每日 08:30 单跑。任何事件的送达延迟 ≤ 下一个 08:30（**~24h 上限**）。
  这不是实时告警产品——那是另一个产品形态，有意不做。
- **前提**：Mac 必须活着。合盖睡眠 → 醒来补跑一次；关机 → 当次跳过等下一周期（launchd 语义）。
  停机期间的内容由 B2 补课窗捞回（≤14 天）。
- **「不漏」的定义**：seen-based、在覆盖源范围内、受各源服务端深度约束（见表）。
  窗口内不截尾（B1）、停机不永久漏（B2 ≤14 天）、gh 深度 30/跑（B1b）。
- **覆盖边界**：X/Twitter **有意不覆盖**（用户自己刷）；中文源有意不加（C2 决策：不稀释英文前沿）。
