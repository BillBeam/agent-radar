# Agent Radar — 完整需求与设计规范（SPEC）

> 本文是项目唯一的「真相源」。任何人/AI 继续开发前必读。
> 它描述：项目意图 → 硬性约束 → 架构 → 数据契约 → 每个组件现状 → 路线图 P0–P4 → 协作方式。

---

## 0. 一句话

一个**会推送、会讲解、有记忆、能自我进化**的前沿 agent/harness 技术情报 agent：每天抓取全球最前沿的 agent/harness 工程内容 → AI 理解后产出**中文详解** → 按用户口味推钉钉 → 并把读到的前沿技术**用来升级它自己**。运行在本地、走 Claude Code 订阅额度。

## 1. 背景与目标

服务对象是一名专注 **agent/harness 方向**的资深工程师（关注 Agent 编排、多智能体、LLMOps、可观测性、Eval、RAG、Memory、MCP/工具接入、容器隔离等工程方向）。痛点：除了啃 Claude Code 源码，不知道平时还能从哪持续获取**同等工程深度**的 agent/harness 技术；且英文原生前沿多、读起来慢。

目标：一个 agent，做到——
1. 每天把英文前沿**理解透、产出中文详解**，按用户口味推到钉钉；
2. **有记忆**：记得过往推送（串成主题 thread），也记得用户这个人（个性化）；
3. **能对话**：每天就推送内容跟用户讲解、讨论、答疑（基于今日事实 + 过往记忆）；
4. **会自我进化**：对话中按上下文自动迭代系统本身（改源/权重/prompt）、提取记忆、创建/编辑工具与 skill；并把**每天读到的前沿技术 eval 验证后用来升级自己**。

它本身也是一个有分量的工程作品——记忆系统 + agentic 深读 + 对话式 HITL + 自我修改，集中体现现代 agent harness 的多项核心能力。

## 2. 硬性约束（不可违背）

实现这个 agent 时必须始终满足以下约束，违背任何一条都要回退：

1. **技术真实、可 defend**：技术实现要前沿且真实，每个设计决策的「为什么这么选 / 什么时候不用」都要想清楚、能 defend。不堆砌概念，要真正落地、对需求有用。
2. **边实现边调研（JIT 前沿对齐）**：不在规划阶段一次性把调研做完。**每个组件开工前**先做一次聚焦的「当下 SOTA 调研 + 选型」——读本地 Claude Code 源码 + 本地参考 harness + web 最新前沿——再写代码。避免脱离实现语境的过时调研。
3. **生产级健壮，不是 demo**：容错/可观测/测试/数据完整性/安全/可运维一个不少；但不做单用户用不上的重型分布式 HA（k8s/多副本/共识）。
4. **走订阅、不额外计费**：LLM 调用走 `claude -p` headless + 订阅；嵌入用本地模型。绝不引入按量计费的付费 API。
5. **对话驱动开发**：这个 agent 是**通过和用户对话来开发的**。AI（CLI 或网页版 Claude）做一切执行（写码/跑/测试/调度），**不给用户派需要手动完成的任务**。用户的全部参与 = 对话。唯一需要用户在对话里给的输入：① 钉钉 webhook（只有用户账号能建）② 口味校准 ③ 对自我修改 diff 拍板「上/再改」。
6. **可拓展/可迭代/自进化拉满**：加能力 = 加一个文件或一行配置，**绝不牵一发动全身、缝缝补补**。靠 ports-and-adapters + 数据驱动行为实现。
7. **质量第一、宁缺毋滥**：只推最前沿、实时、有工程深度的内容。前沿少的日子就少推，绝不用旧闻/营销稿/水文凑数。
8. **中文详解要讲懂**：英文原文经 AI 理解后产出中文详解（不是翻译），对标技术博客精读 / 源码笔记的深度；关键英文术语首次出现即解释。

> 注：约束 2 里的「参考 harness」指本地的一个 agent harness 参考实现，**不是** Nous Research 的 Hermes 模型。

## 3. 双面架构

```
        ┌─────────── 共享 substrate（文件即真相，git 管理）───────────┐
        │  config/(sources·taxonomy·blocklist) · prompts/ · 记忆(SQLite FTS5) │
        │  data/digests · skills/(可被自我创建/编辑) · CLAUDE.md(操作手册,P2)  │
        └────────────────────────────────────────────────────────────────┘
            ▲ 读/写                                       ▲ 读/写
   ┌────────┴─────────┐                       ┌───────────┴────────────┐
   │ Face 1 自动管线   │  每天产出 digest+记忆  │ Face 2 对话式 agent     │  随时讨论 + 自我进化
   │ launchd→claude -p │  推钉钉               │ /agent-radar 会话(订阅)  │  （P2+，开发中）
   └──────────────────┘                       └────────────────────────┘
```

- **Face 1（已实现 P0）**：`launchd` 定时 → `python -m radar --mode daily`。确定性步骤纯 Python；需要「智能」的步骤经 `LLMClient` port → `claude -p`。无人值守。
- **Face 2（规划 P2）**：用户开一个 Claude Code 会话（`/agent-radar`）跟 agent 讨论今天的内容。它的「理解/讨论/自我修改」= Claude Code 原生能力（读写文件、建 skill、用工具）+ 项目根 `CLAUDE.md` 操作手册约定的协议。同样走订阅。
  - **消歧**：**Face 2 是贯穿的对话基底机制**（P2 起即可被复用——如对话中提取记忆/改配置）；**面向用户的「会聊深挖」能力作为交付物落在 P4/E**。所以「Face 2」标 P2、「会聊」标 P4 不矛盾。

## 4. 运行机制（订阅额度，作者问过的关键点）

```
launchd（以用户身份跑 → 能读 ~/.claude 订阅登录态）
  └─> scripts/run_daily.sh → python -m radar --mode daily
       ├─ 确定性步骤（抓取/去重/质量门/记忆/投递）：纯 Python，无 LLM
       └─ 需要"智能"的步骤 → LLMClient port → claude -p "<prompt>" --output-format json --model <tier>
```

关键点：
- `claude -p` 是 Claude Code headless 模式。**没设 `ANTHROPIC_API_KEY` + 订阅登录 → 走订阅额度、非 API 计费**。
- LLM 适配器（`radar/llm/claude_code.py`）**主动从子进程 env 剥离 `ANTHROPIC_API_KEY`**，确保永不静默切到 API 计费；用 `--system-prompt` 替换 CC 默认重 prompt（省、聚焦）；`--max-turns 1` 强制单次确定性 completion。
- **模型分层控额度**：分诊用便宜档（haiku），深读/合成用强档（opus/sonnet）。配 `config.toml [models]`。
- **降级**：LLM 步骤失败时，确定性管线仍产出按源权重排序的原始 top 列表，保证每天至少有东西。

## 5. 数据契约（稳定，勿随意改）

定义在 `radar/core/models.py`。各 stage 只依赖这两个 schema、不互相依赖 → 改一段不波及别段。

- **`Item`**：一个归一化候选，沿管线被原地 enrich。关键字段：`id`(=sha1(url), 驱动去重) · `source_id/source_name/category/weight` · `title/url/published_at/summary` · `tags`(话题标签) · `score`(0–10 分诊分) · `reason`(中文精华) · `self_applicable`+`target_component`(自相关) · `full_text`(深读拉的正文) · `explain_zh`(中文详解) · `links`(关联的过往推送 id)。
- **`Digest`**：成品。`markdown`(完整详解→本地) + `markdown_brief`(精简→钉钉) + `items` + `stats`。
- **`RunContext`**：每次运行的可变状态，串起整条管线，注入服务（`llm`/`memory`/`log`/`trace`）。
- **`TimeWindow`**：新鲜度窗口（daily 48h，papers 96h；dedup 保证不重推，故窗口可宽）。
- **同日多版本工件（2026-07-08）**：`data/digests/{date}.items.json` + 归档 md 永远指向**当前版**；同日重投且 item id 序变化时，旧版先改名 `{date}.v{k}.*` 存档、`{date}.versions.json`（append-only，含 lost 墓碑=投递过但数据不可得）登记后再写新版——重跑不清史，deepread 按 item checkpoint 跨版本复用不重付。

## 6. 组件现状（ports-and-adapters）

抽象接口定义在 `radar/core/ports.py`：`SourceAdapter`/`QualityRule`/`Stage`/`Channel`/`LLMClient`。adapter 靠 `@register(kind, name)`（`radar/core/registry.py`）自注册，config 按名引用。

### 6.1 源（`radar/sources/`）— ✅ 已实现
6 类适配器，28 源验活全过：
- `rss`(RSS/Atom) · `arxiv`(官方 export API，类目+关键词+时间排序，**窗口感知分页**：页 200、越过窗口边界早停、跨页硬顶 600——旧单请求 cap=50 曾 5/7 跑顶格、96h 窗实测匹配 >200 条即静默截尾 150+，2026-07-06 修) · `hackernews`(Algolia API，points 门槛) · `github_releases`(**REST API per_page=30 优先、`releases.atom` 兜底**——atom 服务端只给 10 条，高频仓如 cline 实测 10 条仅跨 9 小时) · `hf_papers`(HF Daily Papers) · `html`(无 feed 的博客，stdlib 链接抽取；**空摘要用文章页 og:description 补齐**——磁盘缓存、opt-in、稳态零额外请求，否则光杆标题进 triage 判不了轻重)。
- **逐源「不漏」真值表**（窗口×深度×饱和史×停机行为×保证等级，全实测）：`docs/SOURCE_GUARANTEES.md`。
- 源注册表在 `config/sources.yaml`（分类、带权重、可读——单独当 reading list 都值）。
- HTTP 基类 `_base.py`：超时 + 退避重试 + **主动忽略环境 `HTTP_PROXY`**（该环境的代理是不可达的公司代理）。
- `python -m radar --mode validate` 逐个验活。

### 6.2 Fetch stage（`radar/stages/fetch.py`）— ✅
并行抓取，**每源 circuit-break**（一个死源不拖累整跑），归一化 → 去重(vs `seen.json`) → 原子落盘 `data/candidates/{date}.json`（LLM 失败可事后补跑）。
**停机补课窗（B2，2026-07-06）**：`data/state/fetch_state.json` 持久化每源上次成功 fetch 时间戳；有效窗口 = max(配置窗, 距该源上次成功 + 12h 余量)、14 天封顶——整机停机或单源连挂（如 07-06 早 arXiv 三连超时）都不再永久漏；正常连跑窗口零膨胀（实测）。
**salvage 重试（2026-07-07）**：抓完后对失败源整体再试一轮（20s settle）——醒来补跑时代理未就绪会让源在暗醒窗口烧光快重试而「死在错误时刻」（07-07 实锤 18/28 源如此阵亡，fetch 结束时网络其实已恢复）；两轮都死才保持失败（B2 次日放大窗口）。配套 `run-daily.sh` 网络就绪门（经代理探 generate_204，最多 40×30s 醒时等待）+ `caffeinate -is` 防跑中被 idle sleep 切片。

### 6.3 Triage stage（`radar/stages/triage.py`）— ✅
一次批量 `claude -p`(haiku) 给候选池 pointwise 0–10 打分 + 打标签 + 判自相关。rubric 在 `prompts/triage.md`（harness/工程深度 > 论文 > 模型发布 PR > 融资/口水，**外加重大前沿发布豁免**（2026-07-06）：核心厂商新模型家族/旗舰代际/重大能力/协议变更即使细节薄 → 8–10；**新一方命名产品地板** ≥6–7（简介是营销空话也上桌）；单向护栏保补丁·nightly·例行 release notes·地区可用性 照旧 0–4——修前 Introducing Claude Sonnet 5 / Claude Tag 曾连续 3–5 跑进池而从未上桌）。只传 title+source+summary 省 token。**分块打分（2026-07-07）**：池按 80 条/块多次调用（全局索引）——B1 放开截尾后首个 219 条池曾让单发调用输出超时 ×3 而整池降级；现在单块失败只对该片走启发式（coverage 如实入账），全部块失败才整池降级。LLM 失败降级为权重启发式。

### 6.4 Quality gate（`radar/stages/quality_gate.py` + `radar/quality/rules.py`）— ✅
可组合规则按序跑：`noise_blocklist`(配 `config/blocklist.yaml`) → `threshold`(<6 丢) → `cap`(按 score+0.4×weight 排序，封顶 max_items)。产出把关漏斗 stats。

### 6.5 Deep-read（`radar/stages/deepread.py` + `_article.py`）— ✅（V5 教学级，2026-07-06）
对**全部入选条目**（`deepread_top_k` = `daily_max_items` = 10）：拉真实正文（arXiv 走全文链 arxiv-html→ar5iv→pdf；抓取上限 120K 字符）→ 只把正文喂给 `claude -p`(**opus 钉死**)，grounding 预算 80K ≈ 全喂（超长先砍 References/Appendix、再智能截断保头尾）→ 产出 **V5 教学级七节详解**（🎯核心洞察 / 📖背景动机 / 🔧机制完整拆解＋mermaid 图 / 🧪实验证据＋表格且每个数字必须被解释 / ⚠️局限 / 💡对读者应用 / 🔗原文与深挖）。设计红线：**完整绝不靠「堆」实现**（教而非倒、裸列数字=违规）；**图表零造数**（数字/结构属 factual、忠实度尺子照核）。**正文拉不到就降级「仅标题+链接」、不杜撰**（反幻觉）；薄源注〔源材料提示〕诚实简短；critic 判定仅作 ⚠️可跳过 标注、**不再让位深读名额**。并发 3 路 + 逐篇 checkpoint（额度中断续跑）。prompt 在 `prompts/deepread.md`（V4→V5 转向理由见 decisions.md：真实使用证明读者不点原文，详解=唯一阅读→必须自足）。

### 6.6 Synthesize（`radar/stages/synthesize.py`）— ✅
产出两个渲染：`markdown`(完整详解，落本地) + `markdown_brief`(精简：TL;DR + 每条标题/链接/一句话精华/标签)。结构确定性拼装，LLM 只写 TL;DR。持久化 `{date}.items.json` 供重渲染（不重跑 opus）。

### 6.7 Channels（`radar/channels/`）+ Deliver（`radar/stages/deliver.py`）— ✅
- 四渠道全自动：`web_reader`(CF Pages 阅读站，见下) → `dingtalk_card`(1v1 互动列表卡，每行 👍/👎 → Stream 回调写 feedback)（同日新版本 outTrackId 加 `:v{n}` 后缀——钉钉对复用 outTrackId 静默忽略新数据） → `local`(完整版 md 归档 + latest.md + index.md，always-on) → `macos`(osascript 通知)。旧 `dingtalk` 群 webhook 已停用（保留代码作回退）。
- deliver 迭代启用渠道、**隔离失败**、投递后才标记 `seen.json`（防重推）。
- **Web 情报台（2026-07-07，`_design.py`/`_site.py`/`_site_stats.py`）**：web_reader 每跑幂等重建整站——每日详解页（目录+锚点+上一天/下一天）+ **主页 HUB**（今日头条+三入口，seg=HMAC(secret,"home")，唯一需要收藏的 URL）+ **归档台**（"index"，倒序每天 `[N]` 标题+一句话洞察直达锚点）+ **数据统计**（"stats"，构建时聚合：反馈画像/忠实度趋势/每日构成/主题热力/系统健康，内联 SVG 零 JS 零后端）。统一设计系统（克制高级、light/dark、字体异步加载系统栈兜底）；**每页写盘前过 leak 闸**（命中=跳过该页）；站点根维持 404、全站 noindex。 同日多版本：最新页顶部版本注记（含 lost 墓碑如实列出），历史版出 `{seg}/v{k}/` 只读子页（同能力信封、过同一 leak 闸、不参与投票）。
- **网页投票（同源）**：站点随部署携带 Pages `_worker.js`——`POST /vote`（页面 seg 即能力令牌，worker 以 WEB_SECRET 重算 HMAC 校验）+ `GET /votes`（独立派生 bearer）；`radar --mode serve` 内置轮询线程把票并进 `record_feedback`（与 `radar mark`/钉钉卡逐键一致，last-wins）。钉钉卡与网页**双通道**并存；KV 未绑定时页面投票钮不渲染、其余零影响。
- **网页手动触发（2026-07-09，`radar/serve/trigger.py`）**：主页「⟳ 立即抓取」→ 同源 `POST /trigger`（seg=HMAC(secret,"home") 即能力令牌）→ KV → serve 内 25s 轮询线程 `GET /trigger`（独立派生 bearer `trigger-read`）接单 → 起 `scripts/run-daily.sh` → `POST /trigger/state` 回报 queued→running→done/failed，页面读回状态、不是黑盒。**动机**：launchd 定时跑不管 Mac 是否醒着/插电，07-07/07-08/07-09 连续三跑被睡眠切碎（`caffeinate -s` 在电池上是官方 no-op，软件无解）——改成「人在机器边上时按一下」。**一次请求只花一次 opus 的三道闸**：worker 20 分钟冷却 + 在途拒绝（`busy` 不计冷却，因未花额度）；poller 游标**先写后跑**（KV 最终一致 ~60s 会重放同一请求）；管线自身 `RunLock`（`core/lock.py:is_held` 只读探针）兜底。`trigger_api` 与 `vote_api` **分开开关**（投票免费、触发要跑满一轮深读）；回报 note 只含非密字段（篇数/深读数/渠道），绝不带路径、stderr、代理 URL。

### 6.8 LLM 后端（`radar/llm/claude_code.py`）— ✅
`claude -p` 封装：剥离 API key、重试、模型分层、`complete()`/`complete_json()`(宽松 JSON 抽取 `_json.py`)。

### 6.9 可观测（`radar/obs/`）— ✅ 基础
结构化 JSON 日志(`Logger`) + 全链路 trace(`Tracer`，每 stage span + 计时落 `data/trace/{run_id}.jsonl`)。

### 6.10 配置 + CLI + Runner（`radar/core/`）— ✅
`config.py`(pydantic 校验、fail-fast、路径常量) · `cli.py`(`--mode doctor/status/validate/daily/weekly/...`) · `runner.py`(组装 RunContext + 注入服务 + 按列表组装 stage + 降级) · `pipeline.py`(stage 编排，非 critical 失败则降级跳过) · `io.py`(原子写)。

## 7. 质量把关（宁缺毋滥）

多层、可组合、可观测：①新鲜度门 ②相关性硬阈值(<6 丢，slow day 少推不降标) ③源加权+噪声拒绝 ④反幻觉 grounding(详解落原文，拉不到降级) ⑤去重 ⑥宁缺毋滥(少则明写) ⑦footer 漏斗(候选 N→过门 K→推送 J)。

## 8. 记忆与理解（P2，未起 — 文件 + FTS5 + USER.md）

**记忆由 harness 持有、喂给 Claude**（LLM 不长期记忆，harness 记），和 Claude Code 的 CLAUDE.md/`memdir` 记忆机制同源。两类：
- **内容记忆（过往推送）** = **SQLite + FTS5（CJK trigram，他读中文详解）**：存每条推过的条目 + 详解 + 标签 + 日期，`Recall` stage 让 agent 说「延续上周 X / 与 Y 对比 / Z 主题第 N 篇」，串 thread 追踪进展。**复用现成 `data/digests/{date}.items.json` + `state/seen.json`，不重造**；抄 Hermes `hermes_state.py` 的 FTS5 小规模做法。
- **用户记忆（关于用户）** = **`USER.md`**（人可读、对话可编辑；**含个人画像 → gitignore，同 CLAUDE.md 待遇，个人/职业上下文不进公开库；仓库只提交 `USER.example.md` 模板；缺失则 rerank 优雅退化为「领域新颖性」，clone 即可跑**）：演化画像（背景、知识水平=英文待提高→中文详解、口味=harness 深度优先、**已会清单**、反馈史）。初值用已知信息播种，靠 Face 2 对话 + 反馈演化；召回靠 **LLM 选择/注入**——抄 CC `memdir` + `findRelevantMemories`（Sonnet 读「文件名+description」manifest 选相关文件，**非向量相似度**）。

**选型 = SQLite + FTS5 + `USER.md` + LLM 选择，不上向量。** 新增 `radar/memory/`(relational/profile) + `recall`/`remember` stage；记忆端口 adapter 边界保留——真要嵌入再加一个 vector adapter，**默认不上**。三条理由（把反 cargo-cult 编进蓝图、防漂回向量栈）：
1. **两个最相关参照都不用向量做记忆**：CC 用 LLM 选文件（`src/memdir/findRelevantMemories.ts`，全库 grep `embedding|cosine|knn|sqlite-vec` 对记忆零命中）；Hermes 用 FTS5/BM25 + trigram（`hermes_state.py:291/320`）。
2. **Radar 记忆规模小**（每天 ~10 条推送、单用户画像），FTS5/BM25 + LLM 选择绰绰有余，向量库是过度工程。
3. **RAG 对 BeamBill 本就不新**（北极星明列 RAG / context-engineering / IMA 是他已掌握的）——为"像个先进 RAG 系统"而建 = 违背"对他精确/对他新"的北极星。

**「对他新」要求（B 阶实现，本轮只写要求、不改运行逻辑）**：rerank 的"新颖"判据要从"**对领域新**"细化为"**对 BeamBill 新**"——读 `USER.md` 的已会清单对**他已会主题降权**（`prompts/rerank.md` 的改动是 B 阶的事）。**验收**：digest 里能看出对已会主题（RAG / context-engineering / harness 构建 / brain-hands 解耦 / IMA）的降权——已会的沉下去、真正对他新的浮上来。

## 9. 自我进化（E1 近期 / E2 远期可选）

**能力级（P2–P3）**：Face 2 对话中**改自己的配置/prompt**、**提取记忆**、**自创建/编辑 skill 与 adapter**。靠项目根 `CLAUDE.md` 操作手册（讨论风格 + 记忆提取协议 + 自我迭代协议 + skill 创建协议 + 护栏）让「项目目录里的一个 Claude Code 会话」成为 radar 的对话大脑。

自指闭环——**把每天调研到的前沿技术用来升级自己**——拆成 E1（先做）+ E2（远期可选）：

- **E1 · 数据级 reviewer（近期、低风险、即时价值）★先做**：一个 Hermes `background_review.py` 式的 reviewer——读 `prompts/triage.md` **已经在 emit** 的 `self_applicable`+`target_component` 标注 + `data/eval/{date}.json` 的 eval 结果 → 提一个 **prompt/config/blocklist/weight 的 diff**（含原"参数级"的调源权重/话题侧重/阈值 + 从深读正文 discover 新高信号源）→ **周报给用户拍板（HITL）→ 应用**。**这是「已有标注钩子 + 已有 eval 判据」接成闭环，不碰代码、不需向量、不需 worktree。** 安全模式抄两家：自维护 reviewer 用**极窄工具白名单**（CC `compact.ts:1125` 压缩 agent 拒绝全部工具；Hermes `background_review.py:459` 只白名单 memory+skills）。
  **〔落地注 2026-07-05〕E1 第一步已上线**：`radar --mode review`（launchd 每周日 21:00 自动跑）聚合 eval 趋势 / 👍👎 投票 / top-10 源分布 / 自相关标注 / critic 统计 / WATCHLIST → 观察+草案周报（`data/self_improve/reviews/`）+ top-line 摘要自动推钉钉 1v1（推送前过与提交物同口径的泄漏自检）。本轮**只草案、零自动应用**——「应用」仍由用户拍板后人工执行；eval 本体同日起接在每日 daily 之后自动跑（失败只 log、逐篇 checkpoint 次日续）。reviewer 本体是聚合代码 + 单次 LLM 草案调用，无任何写配置/写代码工具。
- **E2 · 代码级自指闭环（远期、可选、强护栏）⚠最后做、非必须**：agent 读自己的 `radar/` 代码产出改动 diff → **git worktree 隔离**跑改动，用**冻结基准集**(过往候选池 + 用户 👍/👎 + faithfulness 标注)做 **A/B** → 过 eval + 护栏 → diff 进周报让用户拍板 → commit；回归自动 rollback。护栏：eval 客观门控 + worktree 隔离 + git 可回滚 + HITL + 自我修改代码必须先过 `pytest`+eval 才允许 commit。**注**：Hermes 自己的自进化也**只到 skill/memory 数据层、不改引擎代码**（`memory_tool.py`/`skill_manager_tool.py` 路径围栏）——E2 是更大的赌注，**别让它阻塞 E1 的即时价值**。

> **优先级红线**：`P1 eval 闭环断裂`——尺子已建、却**无人消费** eval 结果——是"系统会不会**自我变好**"的存亡问题；**E1 正是接这个闭环**，应在 E2 之前做。
> 飞轮：调研前沿 → 标自相关 → eval 验证 → 升级自己 → 自己变强 → 调研更准更深 → ……

## 10. 技术选型（起步基线/方向锚，JIT 调研时刷新到当下最前沿）

| 组件 | 采用方向 | 对应能力域 |
|---|---|---|
| 核心 loop/上下文 | Context Engineering(Write/Select/Compress/Isolate) + Agent Skills 分层加载 | 上下文窗口管理 |
| 编排 | routing(按复杂度) + orchestrator-worker(独立条目并行) + evaluator-optimizer | Agent 编排/多智能体 |
| 分诊 | pointwise rubric + 边界项 self-consistency + LLM-judge 去偏 | 意图识别/Eval |
| 深读 grounding | evaluator-optimizer/Reflexion + RAGAS 式 faithfulness | 幻觉检测 |
| 记忆 | SQLite FTS5（CJK trigram）存历史推送 + USER.md 自编辑画像（CC memdir / Hermes 同源）+ 时序 thread | 短期上下文+长期记忆 |
| 检索/召回 | FTS5 BM25 关键词 + LLM 选择相关文件（非向量相似度） | 关键词检索/记忆召回 |
| 自我修改 | Voyager 式技能库 + Gödel-agent 自省 + Alita 按需生成工具 | Reflection/Tool Use |
| 自评估 | reference-free(faithfulness/relevance) + trajectory/outcome + hard-negative mining | Hard Negative Mining |

**故意不用**（场景不需要、可 defend）：**向量嵌入 / RAG 运行时**（记忆规模小、FTS5+LLM 选择够用、RAG 对他不新）、全程多智能体(15x token)、重型 eval 平台(DeepEval/Phoenix)、重型知识图谱、本地大模型。

## 11. 工程健壮性（生产级，已部分内建）

已有：每源熔断、每段降级、原子写、结构化日志 + trace、pydantic config 校验、单测、proxy-safe HTTP、投递幂等 + seen 去重。
**待补（P0 收尾）**：run-lock 并发锁、token 预算强制、`--mode status/doctor` 完善、钉钉失败告警、schema 迁移、备份/恢复、数据保留裁剪、WebFetch SSRF 防护。
**待补（观测/可靠性，路线图待办）**：per-LLM-call trace（prompt 级成本/延迟可观测，无人值守日跑需要）、deepread item 级 checkpoint（崩溃重跑跳过已完成项；忠实度 eval 已有、deepread 还没有）。

## 12. 路线图与当前进度

- **P0 每日管线** ✅ 已跑通（fetch→triage→quality_gate→rerank→deepread→synthesize→deliver，28 源，中文详解，钉钉+本地）
- **P0-H 加固 + P1 尺子（eval）** ✅ 已完成（D/C/B/A/E 加固 + 忠实度/排序 eval + 报告/趋势；Phase A 钉钉投票收口）
- **P2 懂你（记忆 + 个性化）** ✅ 已完成（**SQLite FTS5 + USER.md**，非向量；`remember` stage + rerank 直查记忆〔LEAN：独立 Recall 暂缓〕；**对他已会主题降权**真跑 A/B 验证：已会沉、真前沿不误杀；P0 隐私修复后首次干净成立）
- **P3 讲到极致** ✅ 大体落地（critic 批判层诚实标可跳过〔换名额不砍名额〕+ V4 四轴详解 + 正文抓全〔arXiv 全文 + ar5iv 重定向护栏 + 智能截断〕；「扩覆盖」按 C2 决策**收紧**——不稀释英文前沿、源分布进 WATCHLIST 观察）
- **P4 会聊 + 自进化** 🔨 E1 第一步已落地（2026-07-05：`--mode review` 每周日自动盘点 → 草案摘要推钉钉 → 用户拍板，**零自动应用**；见 §9 落地注）；E 会聊（对话深挖）与 **E2 代码级自指闭环**〔worktree A/B→HITL，远期可选、强护栏〕仍规划

> **优先级红线**：`P1 eval 闭环断裂`——尺子已建却无人消费 eval 结果，**E1 接上它**才让系统真的自我变好。**〔2026-07-05 已接上〕**：eval 每日自动跑（daily 后链式）+ review 每周自动盘点并送达，用户只拍板。
每阶段都可独立交付、可演示。每个组件开工前先做 JIT 前沿调研。

## 13. 协作方式

- **对话驱动**：AI 做一切执行，不给用户派手动任务（见约束 5）。
- **架构纪律**：新能力走 adapter + 注册表；不改 `radar/core/models.py` 的稳定契约。
- **每次改动**：`pytest` 不能挂；外部行为变化要在对话里贴证据给用户看。
- **决策留痕**：组件选型的「为什么/何时不用」记进 `data/self_improve/decisions.md`。
- **git 卫生**：`config.toml`(含钉钉密钥) 永不入库（已 gitignore）；提交信息结尾带 `Co-Authored-By: Claude`。
