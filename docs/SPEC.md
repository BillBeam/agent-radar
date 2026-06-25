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
        │  config/(sources·taxonomy·blocklist) · prompts/ · 记忆(sqlite+vec) │
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

## 6. 组件现状（ports-and-adapters）

抽象接口定义在 `radar/core/ports.py`：`SourceAdapter`/`QualityRule`/`Stage`/`Channel`/`LLMClient`。adapter 靠 `@register(kind, name)`（`radar/core/registry.py`）自注册，config 按名引用。

### 6.1 源（`radar/sources/`）— ✅ 已实现
6 类适配器，28 源验活全过：
- `rss`(RSS/Atom) · `arxiv`(官方 export API，类目+关键词+时间排序) · `hackernews`(Algolia API，points 门槛) · `github_releases`(`releases.atom`) · `hf_papers`(HF Daily Papers) · `html`(无 feed 的博客，stdlib 链接抽取)。
- 源注册表在 `config/sources.yaml`（分类、带权重、可读——单独当 reading list 都值）。
- HTTP 基类 `_base.py`：超时 + 退避重试 + **主动忽略环境 `HTTP_PROXY`**（该环境的代理是不可达的公司代理）。
- `python -m radar --mode validate` 逐个验活。

### 6.2 Fetch stage（`radar/stages/fetch.py`）— ✅
并行抓取，**每源 circuit-break**（一个死源不拖累整跑），归一化 → 去重(vs `seen.json`) → 原子落盘 `data/candidates/{date}.json`（LLM 失败可事后补跑）。

### 6.3 Triage stage（`radar/stages/triage.py`）— ✅
一次批量 `claude -p`(haiku) 给候选池 pointwise 0–10 打分 + 打标签 + 判自相关。rubric 在 `prompts/triage.md`（harness/工程深度 > 论文 > 模型发布 PR > 融资/口水）。只传 title+source+summary 省 token。LLM 失败降级为权重启发式。

### 6.4 Quality gate（`radar/stages/quality_gate.py` + `radar/quality/rules.py`）— ✅
可组合规则按序跑：`noise_blocklist`(配 `config/blocklist.yaml`) → `threshold`(<6 丢) → `cap`(按 score+0.4×weight 排序，封顶 max_items)。产出把关漏斗 stats。

### 6.5 Deep-read（`radar/stages/deepread.py` + `_article.py`）— ✅
对 top K 条：拉真实正文(stdlib 抽取，最多 30K 字符) → 只把正文喂给 `claude -p`(opus) → 产出结构化中文详解。**正文拉不到就降级「仅标题+链接」、不杜撰**（反幻觉）。并发 3 路。prompt 在 `prompts/deepread.md`。

### 6.6 Synthesize（`radar/stages/synthesize.py`）— ✅
产出两个渲染：`markdown`(完整详解，落本地) + `markdown_brief`(精简：TL;DR + 每条标题/链接/一句话精华/标签)。结构确定性拼装，LLM 只写 TL;DR。持久化 `{date}.items.json` 供重渲染（不重跑 opus）。

### 6.7 Channels（`radar/channels/`）+ Deliver（`radar/stages/deliver.py`）— ✅
- `local`(完整版 md 归档 + latest.md + index.md，always-on) · `macos`(osascript 通知) · `dingtalk`(自定义机器人 webhook，**HMAC 加签**，发精简版，**按字节**分块——钉钉限 20000 bytes，CJK 3 字节/字)。
- deliver 迭代启用渠道、**隔离失败**、投递后才标记 `seen.json`（防重推）。

### 6.8 LLM 后端（`radar/llm/claude_code.py`）— ✅
`claude -p` 封装：剥离 API key、重试、模型分层、`complete()`/`complete_json()`(宽松 JSON 抽取 `_json.py`)。

### 6.9 可观测（`radar/obs/`）— ✅ 基础
结构化 JSON 日志(`Logger`) + 全链路 trace(`Tracer`，每 stage span + 计时落 `data/trace/{run_id}.jsonl`)。

### 6.10 配置 + CLI + Runner（`radar/core/`）— ✅
`config.py`(pydantic 校验、fail-fast、路径常量) · `cli.py`(`--mode doctor/status/validate/daily/weekly/...`) · `runner.py`(组装 RunContext + 注入服务 + 按列表组装 stage + 降级) · `pipeline.py`(stage 编排，非 critical 失败则降级跳过) · `io.py`(原子写)。

## 7. 质量把关（宁缺毋滥）

多层、可组合、可观测：①新鲜度门 ②相关性硬阈值(<6 丢，slow day 少推不降标) ③源加权+噪声拒绝 ④反幻觉 grounding(详解落原文，拉不到降级) ⑤去重 ⑥宁缺毋滥(少则明写) ⑦footer 漏斗(候选 N→过门 K→推送 J)。

## 8. 记忆与理解（P1，开发中 — Memory System：向量库 + 关系型）

**记忆由 harness 持有、喂给 Claude**（LLM 不长期记忆，harness 记），和 Claude Code 的 CLAUDE.md/记忆机制同源。两类：
- **内容记忆（过往推送）**：每条推过的条目 + 详解 + 标签 + 日期 → sqlite(push-log/thread) + 向量(本地嵌入)。→ `Recall` stage 让 agent 说「延续上周 X / 与 Y 对比 / Z 主题第 N 篇」，串 thread 追踪进展。
- **用户记忆（关于用户）**：演化画像（背景、知识水平=英文待提高→中文详解、口味=harness 深度优先、反馈史）。初值用已知信息播种，靠 Face 2 对话 + 反馈演化。存为 markdown 事实文件（人可读、git 友好、对话可编辑）+ sqlite/向量双形态。

**起步选型（JIT 调研时刷新）**：`sqlite + sqlite-vec`（关系型+向量同库一个文件）+ 本地 **BGE-M3** 嵌入 + **Contextual Retrieval + 混合 BM25+dense+RRF+重排**；无 ML 依赖时先 BM25，adapter 边界无痛升级。新增 `radar/memory/`(relational/vector/profile) + `recall`/`remember` stage。

## 9. 自我进化（P2–P4，规划）

三个层级：
- **能力级（P2–P3）**：Face 2 对话中**改自己的配置/prompt**、**提取记忆**、**自创建/编辑 skill 与 adapter**。靠项目根 `CLAUDE.md` 操作手册（讨论风格 + 记忆提取协议 + 自我迭代协议 + skill 创建协议 + 护栏）让「项目目录里的一个 Claude Code 会话」成为 radar 的对话大脑。
- **参数级（P3）**：`radar/evolve/` 周度 reflect（据反馈调源权重/话题侧重/阈值）+ discover（从深读正文挖新高信号源、验活后提议加进 sources.yaml）+ metrics（自评估指标）。
- **架构/技术级（P4，自指闭环）★最强**：**把每天调研到的前沿技术用来升级自己**。五步（eval 门控 + HITL，安全可控）：① 分诊时标 `self_applicable`+`target_component`（taxonomy 话题=radar 自己的组件）② 入 `data/self_improve/backlog.jsonl` ③ agent 读自己的 `radar/` 代码产出改动 diff ④ **git worktree 隔离**跑改动，用**冻结基准集**(过往候选池 + 用户 👍/👎 + faithfulness 标注)做 **A/B** ⑤ 过 eval + 护栏 → diff 进周报让用户拍板 → commit；回归自动 rollback。护栏：eval 客观门控 + worktree 隔离 + git 可回滚 + HITL + 自我修改代码必须先过 `pytest`+eval 才允许 commit。

> 飞轮：调研前沿 → 标自相关 → eval 验证 → 升级自己 → 自己变强 → 调研更准更深 → ……

## 10. 技术选型（起步基线/方向锚，JIT 调研时刷新到当下最前沿）

| 组件 | 采用方向 | 对应能力域 |
|---|---|---|
| 核心 loop/上下文 | Context Engineering(Write/Select/Compress/Isolate) + Agent Skills 分层加载 | 上下文窗口管理 |
| 编排 | routing(按复杂度) + orchestrator-worker(独立条目并行) + evaluator-optimizer | Agent 编排/多智能体 |
| 分诊 | pointwise rubric + 边界项 self-consistency + LLM-judge 去偏 | 意图识别/Eval |
| 深读 grounding | evaluator-optimizer/Reflexion + RAGAS 式 faithfulness | 幻觉检测 |
| 内容记忆 | MemGPT/Letta 自编辑记忆块 + Generative-Agents 反思 + 时序 thread | 短期上下文+长期记忆 |
| 检索 | Contextual Retrieval + 混合 BM25+dense+RRF + 重排 | 混合检索/Re-ranking |
| 本地嵌入/库 | BGE-M3 + sqlite-vec | 向量库+关系型 |
| 自我修改 | Voyager 式技能库 + Gödel-agent 自省 + Alita 按需生成工具 | Reflection/Tool Use |
| 自评估 | reference-free(faithfulness/relevance) + trajectory/outcome + hard-negative mining | Hard Negative Mining |

**故意不用**（场景不需要、可 defend）：全程多智能体(15x token)、重型 eval 平台(DeepEval/Phoenix)、重型知识图谱、本地大模型。

## 11. 工程健壮性（生产级，已部分内建）

已有：每源熔断、每段降级、原子写、结构化日志 + trace、pydantic config 校验、单测、proxy-safe HTTP、投递幂等 + seen 去重。
**待补（P0 收尾）**：run-lock 并发锁、token 预算强制、`--mode status/doctor` 完善、钉钉失败告警、schema 迁移、备份/恢复、数据保留裁剪、WebFetch SSRF 防护。

## 12. 路线图与当前进度

- **P0 每日管线** ✅ 已跑通（fetch→triage→quality_gate→deepread→synthesize→deliver，28 源，中文详解，钉钉+本地）
- **P1 记忆/检索** 🔜 进行中（sqlite+vec/BGE-M3/混合检索，Recall/Remember，digest 关联）
- **P2 Face 2 对话** 📋（CLAUDE.md 操作手册 + 记忆提取 + config 自我迭代）
- **P3 skill 自创建 + evolve + eval** 📋（周度反思/挖源/指标 + 冻结基准 + A/B harness）
- **P4 自指闭环** 📋（自相关→backlog→自改 diff→worktree A/B→HITL 上线）

每阶段都可独立交付、可演示。每个组件开工前先做 JIT 前沿调研。

## 13. 协作方式

- **对话驱动**：AI 做一切执行，不给用户派手动任务（见约束 5）。
- **架构纪律**：新能力走 adapter + 注册表；不改 `radar/core/models.py` 的稳定契约。
- **每次改动**：`pytest` 不能挂；外部行为变化要在对话里贴证据给用户看。
- **决策留痕**：组件选型的「为什么/何时不用」记进 `data/self_improve/decisions.md`。
- **git 卫生**：`config.toml`(含钉钉密钥) 永不入库（已 gitignore）；提交信息结尾带 `Co-Authored-By: Claude`。
