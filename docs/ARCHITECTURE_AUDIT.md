# Agent Radar 架构审计 —— 对照顶级 agent harness 的诚实差距地图

> 独立审计（执笔会话未参与 Agent Radar 实现）。立场：冷眼挑硬伤、反 cargo-cult、不自我打高分。
> 审计日期 2026-06-29。所有参照 harness 论断均引真实源码 `file:line`；Agent Radar 论断均亲读其代码。

---

## 0. 方法与证据链

- **Agent Radar 本体**：通读 ~3984 行 Python 全部核心（pipeline/runner/ports/llm/各 stage/eval/serve/feedback/config/obs）+ SPEC/PHASES/decisions.md，逐条带 `file:line`。
- **本地 Claude Code 源码**（`src/`，用户机最新可得版）：精读 Agentic Loop（`query.ts`）、Tool System（`Tool.ts`/`services/tools/`）、Permission（`utils/permissions/`）、Context（`services/compact/`、`memdir/`）、Sub-agent（`tools/AgentTool/`）。
- **Hermes v0.14.0**（本地 checkout，约落后今天 1 个月，已标注版本注意点）：learning loop（`tools/skill_manager_tool.py`/`agent/background_review.py`）、记忆（`tools/memory_tool.py`/`hermes_state.py`）、Honcho 用户建模（`plugins/memory/honcho/`）、网关（`gateway/`）、模型后端（`providers/`）、cron（`cron/`）。
- **前沿**：Anthropic 工程博客（building-effective-agents / context-engineering / multi-agent-research-system / managed-agents）、12-Factor Agents、LangGraph/deepagents/Temporal、Mem0/Zep/Letta、OTel GenAI、awesome-agent-harness、agent-harness-generator。

---

## 1. 一句话结论（BLUF）

**按"是不是 harness"的结构标尺量，Agent Radar 不是 harness、而且离得很远**——它没有 LLM 驱动的控制流、没有工具循环、没有权限模型、没有动态上下文、没有记忆、没有学习闭环。`radar/core/pipeline.py:19` 就是一句 `for stage in self.stages`。

**但"离 harness 多远"是错的标尺。** 前沿（Anthropic《Building Effective Agents》、12-Factor Agents #8/#10/#12）**明确推荐**对这种"结构已知、步骤固定"的任务用 workflow 而非 agent。Agent Radar 的"确定性 DAG + 无状态一次性 LLM + 六边形端口"正是 Anthropic 概括的"解耦大脑与双手"，**是该长成的样子，不是落后**。

真正的差距集中在 **Face 2 / 路线图 B-D-E（记忆、个性化、对话、自进化）——那里目前是 0 行空包**。而那里 Hermes 的 learning loop 是**精确**模板。核心判断：**Agent Radar 应当 = 一条精良的专用流水线（Face 1）+ 借 Hermes 模式做小规模 learning loop（Face 2/B/D/E），而不是变成 Hermes 那样的通用 agent**；并且要拒绝两个最诱人的 cargo-cult：**① 把流水线 agent 化；② 用向量-RAG 建记忆栈**（CC 和 Hermes 都不用向量做记忆）。

---

## 2. 逐维度四问

> 每条四问：① 参照 harness 怎么做（引源码）；② Agent Radar 现状；③ 真缺口 or 不必补（理由，反 cargo-cult）；④ 若该补，落 A–E 哪阶、怎么接。

### 维度 1 · Agentic Loop（LLM 驱动控制流）

**①** CC 的循环是 `query.ts:306` 的 `while(true)` async generator，跨迭代靠可变 `State`（`query.ts:204-217`）；**不靠 `stop_reason` 判断要不要继续**（`query.ts:553-557` 明写 stop_reason 不可靠），而是扫描流式内容里有无 `tool_use` block → `needsFollowUp` 作唯一退出信号；工具结果拼进下一轮 `State.messages`（`query.ts:1715-1727`）。关键："the harness has no plan and no stage list"——**模型每轮决定下一步**，`maxTurns` 默认无上限。

**②** Agent Radar **完全相反且故意如此**：`pipeline.py:19` 线性循环；`runner.py:25` `DAILY_STAGES` 硬编码；`claude_code.py:50` 每次 `claude -p --max-turns 1`、docstring 直写 "no tool loop"。控制流 100% 开发者定死 = workflow。

**③ 不是缺口，是正确选择。** 前沿原话：workflow（预定义代码路径）vs agent（LLM 自驱）应按任务可预测性选；"只在简单方案不够时才加复杂度"。fetch→triage→rerank→deepread 的步骤集是**已知且固定**的，给它装 LLM 驱动循环只会换来不确定性、延迟、成本，不换质量。**坚决反对在主管线引入 agentic loop。**

**④** 唯一可议处：`deepread` 抓正文若想"多跳"（顺着文章里的 repo/引用再抓一层），那是**一次有界检索**，不是循环——见维度 7，归 P3 可选。

---

### 维度 2 · Tool System

**①** CC：`Tool<Input,Output>` 契约，Zod inputSchema（`Tool.ts:362-695`），`call()` 返 `Promise<ToolResult>` + `onProgress` 回调；fail-closed 默认（`isConcurrencySafe→false`、`isReadOnly→false`，`Tool.ts:757-769`）；**错误作为 `is_error` tool_result 返回、绝不抛回循环**（`toolExecution.ts` 多处）；`maxResultSizeChars` 超限落盘 + 预览；工具是硬编码数组（`tools.ts:193-251`），并发按 `isConcurrencySafe` 分区并行（cap 10）。

**②** Agent Radar **没有 Tool 抽象**：`ports.py` 只有 Source/Quality/Stage/Channel/LLMClient。`LLMClient.complete` 签名带 `allow_tools` 参数（`ports.py:85`）但 `claude_code._run` 从不使用——**死参数**。LLM 不能调任何工具。

**③ 不必补 Tool 系统。** LLM 在本系统里是"判断器/讲解器"，输入靠确定性 stage 喂、输出是结构化 JSON/中文文本，没有"让模型自选动作"的场景。**真该做的小事：删掉 `allow_tools` 死参数**（它暗示了一个不存在的能力，是诚实债）。

**④** 若 P4 对话深挖落地，Face 2 走的是 **Claude Code 原生工具能力**（SPEC §3 已这么设计：开一个 CC 会话当对话大脑），Agent Radar **不需要自建** Tool 系统——这是它"专用工具"定位的红利：把通用 agent 能力外包给 CC harness 本身。

---

### 维度 3 · Permission Model

**①** CC 是一套多层授权引擎：hook → deny/ask 规则 → `tool.checkPermissions()` → mode → allow 规则 → AI 分类器 → 人工提示，按 behavior 首次命中（`permissions.ts:1158-1318`）；`bypassPermissions` 仍受 deny/ask/safetyCheck 压制；HITL 用 React setState 队列、`await` 挂起到回调（`useCanUseTool.tsx`、`interactiveHandler.ts`）；"allow always" 落 settings.json 持久规则。**连压缩用的 summarizer 都是个"被 canUseTool 拒绝所有工具"的 agent**（`compact.ts:1125`）。

**②** Agent Radar **无权限模型**——因为没有要门禁的动作（LLM 不调工具、不写文件、不执行命令）。

**③ 当前完全不必补**（取证 agent 原话："Agent Radar needs none of it"）。**但有一个未来触发条件**：路线图 E（自进化）一旦让系统改自己（prompt/config/代码），就需要一道**最小授权门**。注意：那道门**不是** CC 的完整规则引擎，而是 CC 那个"focused agent + 极窄工具白名单"模式（`compact.ts:1125` 拒绝全部工具；Hermes 的 background-review fork 只白名单 memory+skills，`background_review.py`）。

**④** 落 **E**。形态 = SPEC §9 已规划的"diff → HITL 拍板 → commit + 回归 auto-rollback" + 一个**白名单**（只允许改 `prompts/*`、`config/*.yaml`、权重；代码级改动必须 worktree 隔离 + 过 pytest/eval 才允许）。CC/Hermes 都验证了"自维护用窄白名单 agent"这条安全路径。

---

### 维度 4 · Context Engineering

**①** CC **没有单一 `smart_truncate`**，而是分层：混合 real+estimate token 记账（`tokens.ts:226`，非字符长度）→ tool-result 2 层落盘预算（per-tool 50K / per-message 200K，`toolResultStorage.ts`）→ **microcompact**（只清*旧*工具结果、保留最近 N，`microCompact.ts`）→ **full compaction**（LLM 摘要，跑成 `maxTurns:1` + 拒绝全部工具的 fork agent，固定 9 段模板，附 transcript 指针，压后重注入最近 5 个读过的文件，`compact.ts`）→ 每轮注入 `<system-reminder>`。Hermes 的活引擎 `ContextCompressor`（`context_compressor.py`）5 阶（非 LLM 剪枝去重→护头→按预算留尾→LLM 摘要中段→迭代更新）、threshold 0.50、**anti-thrashing**（连续 2 次省<10% 就跳过）、结构化 handoff 模板、`/compress` 结束父会话建子续接。

**②** Agent Radar 只有**静态截断**：`[:160]`/`[:28000]`/`smart_truncate`。token 预算仅 `runner.py:129` 软告警、不强制。

**③ 对 Face 1（无状态管线）= 不是缺口。** 每个 LLM 调用都是独立一次性的，**根本没有累积上下文要压缩**，静态截断足矣。**对 Face 2（P4 对话）= 真缺口**，但还没到——一旦多轮对话累积，必须有压缩。前沿的高 ROL 借鉴：keep-recent-N 驱逐 + token 阈值触发的结构化 LLM 摘要，而非盲目长度截断。

**④** 落 **P4/E**。Face 2 若用 CC 原生会话，**压缩直接吃 CC 的 microcompact/autocompact，不用自建**；若自建对话引擎，照搬 Hermes `ContextCompressor` 的"非 LLM 剪枝先行 + anti-thrashing + 结构化 handoff"。**现在不要动**——管线没有上下文要管。

---

### 维度 5 · Memory & State（跨会话记忆 + 用户模型）

**①** 关键发现——**两个顶级 harness 都不用向量做记忆**：
- CC：记忆是**文件**（`CLAUDE.md` 每轮注入 + `memdir/` 四类 typed 文件，`MEMORY.md` 索引），召回靠 **LLM 选择**（`findRelevantMemories.ts` 让 Sonnet 按 frontmatter 挑 ≤5 个文件，querySource `memdir_relevance`）；**全代码库 grep `embedding|cosine|knn` 零命中**（取证 agent 确证为"confirmed negative"）。
- Hermes：两个 char-budget 平文件 `MEMORY.md`+`USER.md`（`memory_tool.py`，会话开始**冻结快照**注入以稳 prefix cache）；跨会话检索 `hermes_state.py` 的 **SQLite FTS5**（含 CJK trigram）、`session_search` 工具**零 LLM**（BM25+snippet+锚定±5 窗+会话首尾），**LLM 只用来起 3-7 词标题**（`title_generator.py`，旧 LLM-summary 读路径已删除）。
- 用户建模（Hermes Honcho）：用户/AI 各为 `peer`，每轮 async 写入，`dialectic_query`→服务端 `peer.chat()` 返回综合 NL 画像、capped 600 字注入 + `honcho_reasoning` 工具（`plugins/memory/honcho/`）。

**②** Agent Radar **记忆 = 0 行空包**：`radar/memory/__init__.py` 0 字节；`ctx.memory`（`models.py:158`）从不赋值（确证 vestigial）；`config.py:40` `memory_db` 路径定义未用；`DAILY_STAGES` 的 `recall`/`remember` 是**未注册的空槽**（无对应文件、无 `@register`）；`ports.py` docstring 提到"the memory store"端口但**该端口不存在**。唯一已有的"跨天状态"= `seen.json` 去重 + `first_seen.json` 首见戳 + `feedback/{date}.json`（含内容快照）。

**③ 真缺口，且是 P2 北极星的地基。** 但**选型必须纠偏**：SPEC §8 计划 sqlite-vec + BGE-M3 + 混合 BM25+dense+RRF+rerank——**这是 cargo-cult**。理由三条：(a) CC/Hermes 这两个最相关的参照都**不用向量**做记忆；(b) Agent Radar 的记忆规模小（每天 ~10 条推送、单用户画像），FTS5/BM25 + LLM 选择绰绰有余；(c) **RAG 对 BeamBill 本就"不新"**（CLAUDE.md 明列 RAG/context-engineering/IMA 是他已会的），花力气建 RAG 记忆栈既过度又不服务"对他新"。

**④** 落 **B（冷启动）+ D（反馈精修）**。推荐落点（直接照 Hermes 小规模搬）：
- **内容记忆**：复用现成的 `{date}.items.json` + `seen.json`；加一个 `hermes_state.py` 式的 **SQLite + FTS5**（CJK trigram，BeamBill 读中文详解）存历史推送，`recall` stage 零 LLM 检索"上周相关/同主题第 N 篇"，串 thread。**不引入嵌入模型。**
- **用户记忆**：一个 `USER.md`（人可读、git 友好、对话可编辑）存画像（背景、已会清单、口味、反馈史），**B 阶先手填冷启动**（不依赖反馈），synthesize/rerank 把它 capped 注入 prompt。Honcho 的自建等价物 = 维度 6 的 background-review→`USER.md` 闭环。

---

### 维度 6 · Learning Loop / 自进化 ★（B/D/E 的核心，Hermes 是精确模板）

**①** Hermes 的学习闭环（全部基于文件，无重型基建）：
- **Skills**：磁盘 `SKILL.md`+YAML（`skill_manager_tool.py`），`skill_manage` 工具运行时 create/patch/edit（patch 是模糊 find/replace）；系统提示令 agent"完成 5+ 工具调用的复杂任务后存为 skill；用到过时 skill 立即 patch"（`prompt_builder.py:166`）；**3 层渐进披露**（系统提示只放 name+description 索引→`skill_view` 拉正文→拉链接文件）。
- **周期自提醒 = 按 cadence 的 background-review fork**（`background_review.py`）：每 10 轮 spawn 一个 fork agent，replay 对话问"该存/改哪条记忆或 skill"，**工具白名单只 memory+skills**，跑在 response 之后不抢用户任务，继承父 prefix cache 省 ~26% 成本。**用户的纠正/不满是一等信号**（"this is too verbose"→嵌成偏好，下次会话即生效）。
- **长程 Curator**（`curator.py`）：7 天 idle-gated 跨会话整理 agent-created skills，纯函数 active→stale→archived + fork agent 整合，带 tar.gz 备份 + rollback、只动 `created_by:agent` 的 provenance。

**②** Agent Radar **自进化 = 0 行空包**：`evolve/`、`self_improve/` 空；`cli.py:16` 的 `evolve` mode → `cmd_stub` "not implemented"；`decisions.md` 是**人/CC 写的**留痕、非 agent 自写。**但第一个钩子已存在**：`triage.md` 让 LLM 标 `self_applicable`+`target_component` 存进 items.json——等于"自改候选"已在产生，缺的是消费端。P1 eval（`faithfulness.py`/`ranking.py`）是**真功夫的自改判据**，但**没有任何东西读它去改系统**。

**③ 真缺口，且是项目"越用越准/自进化"愿景的命脉。** 但要分清两个层级：
- **数据级自改（prompts/config/blocklist/权重）**：低风险、Hermes 已验证的模式，**该做**。
- **代码级自改（改 `radar/` 源码）= SPEC §9 P4 的"自指闭环"**：这是**最过度雄心**的部分。Hermes 的"自进化"也只到 skill/memory（数据）层，**不改自己的引擎代码**。Agent Radar 想让 agent 改自己的 Python 代码，风险/复杂度远超 Hermes，且对一个单用户日推工具收益存疑。

**④** 落 **E**，但**重排优先级**：
- 先做**数据级闭环**（小、安全、即用）：一个 Hermes 式 reviewer 读 `items.json` 里的 `self_applicable` 标注 + `data/eval/` 的 eval 结果 → 提一个 **prompt/config/blocklist/weight 的 diff** → 周报里给 BeamBill 拍板 → 应用。这把"已有的标注钩子 + 已有的 eval 判据"接成闭环，**不碰代码、不需向量、不需 worktree**。
- 代码级自改（SPEC §9 P4）**保留为远期、可选、强护栏**（worktree 隔离 + 冻结基准 A/B + pytest+eval 双门 + HITL + auto-rollback——SPEC 已设计对，但应明确标为"最后做、非必须"）。
- 安全模式直接抄 CC/Hermes：**自维护 agent 用极窄工具白名单**（`compact.ts:1125` 拒绝全部；`background_review.py` 只 memory+skills）。

---

### 维度 7 · Sub-agents / 并行

**①** CC：`tools/AgentTool/` 全套——`isolation: worktree|remote`（`loadAgentsDir.ts:126`），fork 继承上下文+共享 cache vs 全新 subagent 零上下文（`prompt.ts:86,103,145`），`spawnMultiAgent.ts` 团队。Hermes：`tools/delegate_tool.py` 起 fresh `AIAgent(skip_memory=True, skip_context_files=True, 受限工具集)`，ThreadPoolExecutor 默认 3、`MAX_DEPTH=1` 扁平，**父只见 summary**。Anthropic multi-agent research system：lead 规划 + 3-5 个并行 subagent（独立上下文），各返浓缩发现。

**②** Agent Radar 的"并行" = `deepread.py:79` 的 `ThreadPoolExecutor(max_workers=3)`——**Python 线程并行、同进程、无 agent 隔离、非 LLM 驱动委派**。

**③ 主体不必补。** 前沿确证：多智能体 ~15× token，只对"广度优先的独立子任务 + 价值上限够高"才划算；Agent Radar 没有 peer agent（A2A 无意义），"并行跑 130 条 deepread"就是 goroutine/线程并发、**不是**多智能体。**软例外**：若 P3 想做每篇 top 的"多跳深研"（顺着原文追 repo/引用/对比），**一个有界的、独立上下文的 research 子代理/篇**是唯一可能合身处——照 Anthropic research subagent 模式（返 1-2K 摘要）。

**④** 落 **P3 可选特性**，非核心。当前 `ThreadPoolExecutor` 已够。

---

### 维度 8 · 错误恢复 / 韧性

**①** 参照：Temporal/LangGraph durable execution（checkpoint + 中断恢复）、12-Factor #5/#6/#12（统一执行/业务状态、launch/pause/resume、stateless reducer）；Hermes cron `tick()` 用 fcntl 文件锁做 at-most-once（**先推进 next_run 再执行**）、`[SILENT]`/空输出软失败投递门。

**②** Agent Radar 已有：每 stage graceful degradation（`pipeline.py:23-31`）、非关键失败降级续跑、原子写、`seen.json` 投递幂等（投递成功才标 seen，`deliver.py:39`）、RunLock 僵死夺回、`last_run.json` 摘要、serve 的 `start_forever` 自带重连。**eval 已有逐篇 checkpoint 可续跑**（`faithfulness.py`）。

**③ 基本够，且符合"50 行 durable pattern 而非 Temporal 集群"的前沿建议。** 真实小缺口两个：(a) **管线本身无 item 级断点续跑**——deepread 跑到一半崩了，重跑要重抓重读（eval 有 checkpoint，管线没有）；(b) token 预算只软告警、不强制（`runner.py:129`），订阅限流下可能空烧。这两个是"硬伤"里最轻的一档。

**④** 落 **C/P3 的健壮性收尾**：deepread 加 per-item checkpoint（仿 eval 的 `faithfulness.py` checkpoint，落 `data/candidates` 或临时态），崩溃重跑跳过已完成项；token 预算从"软告警"升级为"接近上限时早停 + 标记未完成"（fetch/triage 已抓的可事后补跑）。**不引入 Temporal/durable engine**（单 cron job 用不上）。

---

### 维度 9 · 多渠道 Gateway / 入站

**①** Hermes 单进程多平台：`BasePlatformAdapter(ABC)` + 归一化 `MessageEvent` + `build_session_key`（单一真相，按 platform/chat/user/thread）+ `set_message_handler` 间接层，~28 平台含 DingTalk。**重要：Hermes 的 `gateway/platforms/dingtalk.py` 与 Agent Radar 的 `serve/listener.py` 几乎一模一样**（WebSocket + Handler 回调 + AckMessage + 懒加载 + 重连）——收敛进化，Radar 的写法是对的。

**②** Agent Radar：`serve/listener.py` + `dingtalk_card.py` **单平台钉钉硬编码**；`deliver.py` 的 `CHANNEL_ORDER` 还没加 `dingtalk_card`（A1 待办）。

**③ 不必泛化成 28 适配器网关。** 单用户、单平台（钉钉），建 `BasePlatformAdapter` 抽象层是**纯过度设计**。**但有一个低成本的解耦值得做**（防未来锁死）：把 serve 的回调 handler 从"钉钉帧→`record_feedback`"中间抽一个**归一化入站事件**（仿 Hermes `MessageEvent` 的极简版：`{date, item_id, vote, user_id}`，`listener.py:55` 的 `parse_card_callback` 其实已经是这个形状了），让"写反馈"逻辑不绑钉钉帧结构。这样换/加平台是加一个 parser，不动核心。

**④** 落 **A1 收尾**时顺手做（把 `parse_card_callback` 的归一化产物作为唯一入站契约）。**不要**现在建多平台抽象。

---

### 维度 10 · 模型无关后端

**①** Hermes：声明式 `ProviderProfile` dataclass（base_url/auth/quirks 数据化，"transport 读它而非收 20 个 bool flag"）+ ~28 provider 插件 + 5 种 `api_mode` wire 适配器（chat_completions/anthropic_messages/codex_responses/bedrock_converse/copilot_acp）+ 跨 provider 529/429 failover + 凭证池轮换。

**②** Agent Radar：`LLMClient` 是端口（`ports.py:73`），但只 1 实现（`claude_code`）、硬编码订阅模式；重试仅同一 `claude -p` 退避（`claude_code.py:101`），**无跨 provider failover**；模型分层靠调用方传 `model=`（haiku/sonnet/opus）。

**③ 不必补第二后端。** 硬约束 1 写死"绝不引入按量计费 API、只走订阅"——**第二个后端是被需求禁止的，不是没做到**。端口只有 1 实现在这里是**对的**（保留未来座位）。**真该做的小事**：删 `LLMClient.complete` 的 `allow_tools` 死参数（同维度 2）。"该不该补第二实现证明端口有用"——**反对为证明而补**；端口的价值在"约束变了能无痛换"，不在"现在就有俩"。

**④** 无需动作。若哪天嵌入用本地模型（SPEC 提过），那是 `radar/memory/` 里的一个新 adapter，与 LLMClient 端口无关。

---

### 维度 11 · 可观测 / 审计 / 回放

**①** 前沿：OTel GenAI 语义约定（agent/workflow/tool/model span + 延迟/token metric，可跨厂商）；Arize Phoenix/Langfuse 做多步 trajectory 分析。CC：每 tool 调用、每轮、token、权限决策 provenance 全程可追。

**②** Agent Radar 已有 harness 级基础：`obs/__init__.py` 结构化 JSON 日志 + per-run Tracer（`span_start/end/error`+ms，`pipeline.py:20` 每 stage 套 span）+ `last_run.json` token 记账。**缺口：LLM 调用本身不进 trace**（`claude_code.complete` 不 emit event）——有 run/stage 级、无 prompt 级 trajectory（deepread sidecar + items.json 提供部分输入/输出留存）。

**③ 真小缺口，且因每天无人值守跑、最该补。** 前沿建议正中要害：per-stage 的 items in/out、tokens、$/run、延迟、error/retry、"digest 是否投递成功"——这套日推 metric 清单 Agent Radar 差一个 **per-LLM-call span**（含 model/prompt 指纹/token/耗时/重试次数）。**不必上 Langfuse/Phoenix**，但 span 命名可对齐 OTel GenAI（model/tool/workflow），以便将来想接也无痛。

**④** 落 **C/P3 收尾**：`claude_code.complete` 里给每次调用 emit 一个 trace event（已有 Tracer，几行的事），run 完汇总成 per-stage 成本/延迟表进 `last_run.json`。低成本高回报。

---

### 维度 12（我补充）· "破全-9 / 对他新"的个性化机制 ★ —— P2 北极星，最难也最核心

**①** 这是 Agent Radar 独有的核心能力（通用 harness 没有直接对应），但参照点是 Hermes 的用户建模：把"用户已知什么/偏好什么"建成可查的 representation（Honcho peer / `USER.md`），**在生成时把它注入**，让输出"对这个人"而非"通用地好"。

**②** Agent Radar 现在只排"重要性"：`triage.md`/`rerank.md` 按"agent/harness 工程深度 + 新颖 + 当下值得读"打分排序——**但"新颖"是对领域的新颖，不是对 BeamBill 的新颖**。系统**无法压低"他已会"的主题**（RAG/context-engineering/IMA，CLAUDE.md 明列）。CLAUDE.md 北极星原话："精确 = 重要性 + 对他的新颖性……这是最难、最核心的能力"——**目前零实现**。

**③ 这是最该补的"真缺口"，且依赖维度 5（记忆/用户模型）落地。** 它不是 harness 特性的照搬，是这个产品的灵魂。

**④** 落 **B（冷启动手填"已会清单"）→ D（反馈精修）**：
- B：`USER.md` 里手填"已掌握主题/不想再看的"，在 rerank prompt 里加一条"对用户已标注掌握的主题降权"。**立即可用、不依赖反馈数据**（当前反馈仅几条真票，确证数据太薄）。
- D：等 👍/👎 攒够（`ranking.py:MIN_PAIRS=10` 是现成的"够不够"判据），用反馈精修"对他新"的权重——这正是 Hermes background-review"把用户信号转成下次生效的偏好"的小规模版（Radar 的信号是投票、比对话纠正更简单）。

---

### 维度 13（我补充）· 记忆检索选型：向量-RAG vs 文件+FTS5+LLM 选择 ★ —— 路线图最大纠偏

**①** 见维度 5 ①：**CC 用 LLM 选文件、Hermes 用 FTS5/BM25 + LLM 起标题，两者都不用向量嵌入做记忆**（CC grep `embedding|cosine|knn` 零命中）。前沿对单用途工具的记忆建议也是"本地 KV/JSONL + 去重 + 主题滚动摘要"，而非记忆运行时。

**②** Agent Radar SPEC §8 + §10 计划：sqlite-vec + 本地 BGE-M3 嵌入 + Contextual Retrieval + 混合 BM25+dense+RRF+重排。**尚未实现**（记忆是空包），所以**纠偏正当其时、零沉没成本**。

**③ 这是路线图里最明确的 cargo-cult。** 三重理由（见维度 5 ③）：两个顶级参照都不用向量；Radar 记忆规模小；RAG 对 BeamBill 不新。坚持上 BGE-M3+混合检索 = 为"像个先进 RAG 系统"而上，违背北极星。

**④** **直接改 SPEC §8/§10 的选型**：内容记忆用 **SQLite + FTS5（CJK trigram）**（抄 `hermes_state.py`），用户记忆用 **`USER.md` + LLM 选择/注入**（抄 CC `memdir` + `findRelevantMemories`）。`radar/core/ports.py` 的记忆端口设计成"profile/relational/profile"即可，**adapter 边界保留**——真有一天要嵌入，再加一个 vector adapter，但默认不上。

---

## 3. 差距地图（按真实优先级排序）

### A. 架构硬伤 / 真缺口（该补，按"对北极星的杠杆"排序）

| # | 缺口 | 性质 | 落点 |
|---|---|---|---|
| 1 | **"对他新"个性化零实现**（维度 12）：只排重要性、压不下"他已会"的主题 | 北极星灵魂缺失 | B→D |
| 2 | **跨会话记忆零实现**（维度 5）：`ctx.memory` vestigial、recall/remember 空槽、无历史串联 | 地基缺失，且阻塞 #1 | B |
| 3 | **eval→改进闭环断裂**（维度 6）：P1 尺子很好但无人消费；`self_applicable` 标注无下游 | 自进化命脉、但已有两端只差接线 | E（先数据级） |
| 4 | **记忆选型偏向重向量-RAG**（维度 13）：SPEC §8 计划 cargo-cult 栈 | 路线图纠偏（零沉没成本） | 改 SPEC |
| 5 | **无 per-LLM-call 可观测**（维度 11）：无人值守日跑却缺 prompt 级成本/延迟 trace | 运维真缺口、低成本 | C/P3 |
| 6 | **管线无 item 级断点 + token 预算不强制**（维度 8）：崩溃重跑重烧、限流空烧 | 韧性小硬伤 | C/P3 |

### B. 路线图 B/D/E 自然会覆盖（设计已对，只是没写）

- Face 2 对话引擎 + 其上下文压缩（维度 4）→ 直接吃 CC 原生会话能力，**不自建**。
- 用户画像演化、反馈精修（维度 5/12 的 D 部分）→ B 手填 + D 反馈精修。
- 数据级自改闭环（维度 6 的 E 第一步）。
- serve 入站归一化解耦（维度 9）→ A1 顺手。

### C. 不必补（照搬即过度设计 / 被需求禁止 / cargo-cult）

| 项 | 为什么不 |
|---|---|
| 主管线 agentic loop / 工具循环（维度 1/2） | 步骤固定已知，前沿明确推荐 workflow；加循环只换不确定性+成本 |
| 多智能体编排 / A2A / orchestrator-worker（维度 7） | ~15× token、无 peer agent、并行 deepread 是线程并发不是多智能体 |
| 完整权限规则引擎（维度 3） | 无可门禁的动作；E 自改只需窄白名单 + HITL，非 CC 全引擎 |
| 向量-RAG 记忆栈 BGE-M3/sqlite-vec/混合检索（维度 13） | CC/Hermes 都不用向量；规模小；RAG 对 BeamBill 不新 |
| 第二个 LLM 后端（维度 10） | 硬约束禁止按量 API；端口留座即可，反对"为证明而补" |
| 多平台网关抽象（维度 9） | 单用户单平台，建 28-adapter 抽象纯过度 |
| 内部 cron 调度器 / Temporal（维度 8） | 单日 job，launchd + run-lock 已足 |
| 执行沙箱 / MCP 化适配器 / 代码级自改（早做） | 不跑不可信代码；适配器已是干净端口；代码自改是最远期可选 |

---

## 4. A–E 路线图修正建议

1. **B 之前先改 SPEC §8/§10 的记忆选型**（维度 13）：删 BGE-M3+sqlite-vec+混合 RAG，改 SQLite+FTS5（CJK trigram）+ `USER.md`+LLM 选择。这是零成本纠偏，越早越好（记忆还没动手）。

2. **B 阶把"对他新"提为一等目标**（维度 12）：B 不只是"记忆冷启动 + 手填画像"，而是**手填画像要直接接进 rerank 的"对他已会主题降权"**——否则记忆建好了也没改变推送。建议 B 的验收标准 = "digest 里能看出对 BeamBill 已会主题的降权"。

3. **E 拆成 E1（数据级，近期）+ E2（代码级，远期可选）**（维度 6）：
   - **E1**：reviewer 读 `self_applicable` 标注 + `data/eval/` → 提 prompt/config/blocklist/weight diff → 周报 HITL。**这是已有钩子+已有判据的接线，应在 E2 之前做，且不需向量/不需 worktree。**
   - **E2**：SPEC §9 P4 代码级自指闭环，明确标"最后做、非必须、强护栏"。Hermes 的自进化止于 skill/memory 数据层——Radar 想改自己的引擎代码是更大胆的赌注，别让它阻塞 E1 的即时价值。

4. **C/P3 收尾补两件低成本运维硬化**（维度 8/11）：per-LLM-call trace + 成本表；deepread item 级 checkpoint + token 预算早停。这两件让"无人值守日跑"真正可信。

5. **A1 顺手解耦 serve 入站**（维度 9）：把 `parse_card_callback` 的归一化产物定为唯一入站契约，未来加平台 = 加 parser。

6. **删两处诚实债**：`LLMClient.complete` 的 `allow_tools` 死参数、`ports.py` docstring 里不存在的"memory store"端口承诺——它们让架构看起来比实际多。

---

## 5. 诚实结论

### 离一个"真 harness"有多远？

**按结构定义：很远。** Agent Radar 没有 agentic loop、工具系统、权限模型、动态上下文、记忆、学习闭环中的任何一个——这六项是 harness 的定义性特征，它一个都没有，且大多是**故意**没有。Face 2（对话/记忆/个性化/自进化）整块是 0 行空包。

**但这是错的标尺，用它评分会得出错误结论。** 前沿（Anthropic、12-Factor）明确：对"结构已知、步骤固定"的任务，**workflow 优于 agent**。Agent Radar 的确定性 DAG + 无状态一次性 LLM + 六边形端口，正是被推荐的形态、是 Anthropic"解耦大脑与双手"的同构实现。它的 `decisions.md` 里每条选择都有"为什么不"——**反 cargo-cult 的纪律已经内建**，这比多数项目强。

**用对的标尺（对 BeamBill 精确 + 能长出 B/D/E）：**
- **Face 1（管线）**：基本完成、工程扎实、贴近推荐形态。P1 eval（claim 级 faithfulness + 诚实的排序诊断）甚至**领先**于一个典型情报工具。真实差距只在"接缝"：per-call 可观测、item 级断点——都是小事。
- **Face 2 + B/D/E**：**未开始**。这里才是真正的工作量，也是真正该借 harness 模式的地方。而 Hermes 是**精确**模板：文件式记忆、background-review fork、FTS5 检索、`skill_manage` 式自改——但要**按小规模借模式，不搬整套机器**。

### 它"该不该"成为一个全功能 harness？

**不该。** 三层论证：

1. **任务性质决定形态。** 它是单用途日推工具，不是开放式编码 agent。给它装 Hermes 那样的通用 agentic loop / 28 平台网关 / 多 provider 后端 / 多智能体编排，是用复杂度换它用不上的灵活性——前沿原话"只在简单方案不够时才加复杂度"。

2. **通用 agent 能力可以外包，不必自建。** SPEC §3 已经想清楚了一招漂亮棋：Face 2 的"对话/工具/自我修改"直接用 **Claude Code 原生会话能力**当对话大脑。这意味着 Agent Radar **永远不需要自己造** Tool 系统、权限引擎、上下文压缩——这些吃 CC 这个真 harness 的红利。它只需把自己做成"一个 CC 会话能优雅操作的、文件即真相的专用 substrate"。

3. **它该长成的是"精良专用流水线 + Hermes 式 learning loop"，而非 Hermes。** 正确的终态：
   - **管线（Face 1）保持 workflow**，只补接缝。
   - **B/D/E 借 Hermes 的 learning-loop 模式**（文件式记忆 + 周期 review fork + FTS5 + 窄白名单自改），但**坚决拒绝**：向量-RAG 记忆、多智能体、主管线 agent 化、代码级自改早做。
   - **自进化止于数据级（prompt/config/memory）优先**，代码级（SPEC §9 P4）作远期可选强护栏项——Hermes 自己也只进化到数据层。

**一句话**：Agent Radar 不需要变成 harness 来变好；它需要**保持是一条好流水线**，并在记忆/个性化/自进化这三处，**借 Hermes 的模式而非体量**——同时把最诱人的两个 cargo-cult（向量-RAG、流水线 agent 化）按死。它现在的"非 harness"不是病，是对的诊断下的对的处方；真正的病是 B/D/E 还没写，而处方已经在 Hermes 的源码里，只是要**抄思想、不抄规模**。
