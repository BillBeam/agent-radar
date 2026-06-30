# Agent Radar 🛰️

> 一个**会推送、会讲解、有记忆、能自我进化**的前沿 Agent/Harness 技术情报 agent。
> 每天自动抓取全球最前沿的 agent/harness 工程内容，用 AI 理解后产出**中文详解**，按你的口味推送到钉钉——并且它自己也在不断变强。

本项目既是一个**每天真正在用的工具**，也是一个**严肃的 agent harness 工程实践**（生产级健壮性，非 demo）。它运行在本地、走 Claude Code 订阅额度（不额外按 API 计费）。

---

## 这是什么

痛点：想持续追踪 agent/harness 领域最前沿的**工程级**技术（像读 Claude Code 源码那种深度），但不知道每天去哪找、英文原文读起来慢。

Agent Radar 把这件事自动化并做到极致：

1. **每天抓取** 28+ 高信号源（前沿实验室博客、harness 仓库 release、arXiv、HN、newsletter…）
2. **AI 分诊**：按一套贴合「agent 工程深度」的 rubric 给每条 0–10 打分、打标签、判断是否「能用来改进本系统」
3. **质量门**：硬阈值 + 噪声拒绝 + 封顶，**宁缺毋滥**
4. **深读详解**：对 top 条目拉全文，用 Claude Opus 产出**结构化中文详解**（背景/机制/术语/价值/局限），严格落原文、反幻觉
5. **双语 digest**：钉钉发**精简版**（扫一眼就懂），本地存**完整逐篇详解**（精读 / 深聊）
6. **记忆 + 对话 + 自我进化**（开发中）：记得过往推送和你这个人；能跟你讨论今天的内容；并把读到的前沿技术 **eval 验证后用来升级自己**

---

## 核心特性

| 维度 | 说明 |
|------|------|
| 🎯 **高质量把关** | pointwise rubric 分诊 + 多层质量门 + 反幻觉 grounding，宁缺毋滥 |
| 🇨🇳 **中文详解** | 不是翻译，是讲懂——对标技术博客精读 / 源码笔记的深度 |
| 🧩 **可拓展架构** | ports-and-adapters 六边形：加一个源/渠道/规则 = 加一个文件或一行配置，不动核心 |
| 💰 **零额外计费** | LLM 走 `claude -p` headless + 你的订阅；记忆走本地 SQLite FTS5（无嵌入/向量库）；不调付费 API |
| 🛡️ **生产级健壮** | 每源熔断、每段降级、原子写、结构化日志 + 全链路 trace、单测 + eval 回归 |
| 🔁 **自我进化**（规划中） | 对话中改自己的配置/prompt、自创建 skill；把前沿技术 eval-gated 应用到自身 |

---

## 架构总览

**一句话**：Python 是确定性 harness（可靠、可扩展），Claude（订阅）是被注入的理解/判断器，记忆与配置由文件持有、被两面共享。

```
launchd（定时） ─> python -m radar --mode daily
   │
   ├─ [1] Fetch        源适配器并行抓取（无 LLM，永远先出候选池）→ data/candidates/{date}.json
   ├─ [2] Triage       claude -p 按主题 rubric 打分/打标签/判自相关（便宜模型）
   ├─ [3] Quality Gate 噪声拒绝 + 相关性硬阈值 + 封顶（可组合规则，宁缺毋滥）
   ├─ [4] Recall       从记忆检索相关过往推送 + 用户画像        （P2，下一步）
   ├─ [5] Deep-read    拉全文 → Opus 产出中文详解（落原文，反幻觉，并发）
   ├─ [6] Synthesize   双语 digest（精简版 + 完整版）
   ├─ [7] Deliver      钉钉（加签，精简版）+ 本地归档（完整版）+ Mac 通知
   └─ [8] Remember     写内容记忆 + 更新用户画像              （P2，下一步）
```

**两种运行模式，共享同一套代码与数据**（详见 [docs/SPEC.md](docs/SPEC.md)）：
- **Face 1 自动管线**：`launchd` 定时跑，无人值守产出 digest 并推送
- **Face 2 对话式 agent**（对话基底机制，P2 起即可被复用；面向用户的「会聊深挖」交付能力落在 P4）：开一个 Claude Code 会话跟它讨论今天的内容，对话中它会**提取记忆、改自己的配置、甚至自创建 skill**

> 设计哲学、完整需求、每个组件的实现细节与后续路线，全部在 **[docs/SPEC.md](docs/SPEC.md)**。
> 一份**真实产出样例**见 **[docs/sample-digest.md](docs/sample-digest.md)**。

---

## 快速开始

```bash
# 1. 安装（Python ≥ 3.11）
python3 -m venv .venv && source .venv/bin/activate
pip install -e .

# 2. 配置（复制示例，按需填钉钉 webhook；不填则只走本地归档 + Mac 通知）
cp config/config.example.toml config.toml
#   编辑 config.toml：填 [channels.dingtalk] 的 webhook + secret（加签模式）

# 3. 自检（确认依赖/claude CLI/订阅/源都就绪）
python -m radar --mode doctor

# 4. 验活所有信息源（逐个拉一遍，报告死链）
python -m radar --mode validate

# 5. 跑一次每日管线（抓取 → 分诊 → 质量门 → 详解 → 投递）
python -m radar --mode daily

# 跑测试
pytest
```

### 无人值守每天跑（launchd）

让它每天自动跑 + 投票实时接住，两个 launchd agent 一键装好（`com.agentradar.daily` 定时 + `com.agentradar.serve` 常驻）：

```bash
# .env 里加一行代理（无人值守 daily 抓西方源用）：HTTPS_PROXY=http://<代理>:<端口>
bash scripts/install-launchd.sh both        # 生成并加载（plist 自动填本仓库绝对路径）
launchctl list | grep agentradar            # 确认在跑；日志见 data/state/launchd-*.log
```

完整说明（代理处理、改时间、cron 替代、卸载）见 **[deploy/README.md](deploy/README.md)**。

### 反馈投票常驻（serve，可选）

每日 digest 会向钉钉机器人单聊投**一张列表卡**（Loop 渲染，每行 `[N] 🆕/📚 + 中文理由` + 👍/👎；一条消息、顺序固定、不刷屏）。要让点击直接写回反馈，常驻一个 Stream 监听：

```bash
# 凭证从 env 读（DINGTALK_CLIENT_ID/SECRET/ROBOT_CODE/CARD_TEMPLATE_ID/USER_ID，见本地 .env）。
# 钉钉是国内服务、Stream 长连接不能走西方代理 → 显式剥代理：
env -u HTTP_PROXY -u HTTPS_PROXY NO_PROXY='*' python -m radar --mode serve
# 常驻：上面 launchd 的 com.agentradar.serve 已做这件事（KeepAlive + 开机自启，见 deploy/README.md）；
# 临时挂后台也可：nohup bash scripts/run-serve.sh >data/state/serve.log 2>&1 &
```

点 👍/👎 → 回调经 Stream 写进 `data/feedback/{date}.json`（与终端 `radar mark` 完全同结构）。卡片是**投票层**、markdown 简报是**阅读层**（带可点链接 + 完整详解），靠 `[N]` 一一对应。

### 运行机制（为什么不额外计费）

`claude -p` 是 Claude Code 的 headless/print 模式。只要环境里**没有设 `ANTHROPIC_API_KEY`** 且 Claude Code 是订阅登录，这些调用就**走订阅额度、不按 API token 计费**。本项目的 LLM 适配器会主动从子进程环境里剥离 `ANTHROPIC_API_KEY`，确保永远不会静默切到 API 计费。记忆检索（P2）用本地 SQLite FTS5，无需嵌入/向量库，也零额外成本。

---

## 当前状态

| 阶段 | 内容 | 状态 |
|------|------|------|
| **P0** | 每日管线：28 源抓取 → 分诊 → 质量门 → 中文详解 → 双语 digest → 钉钉+本地 | ✅ 已跑通 |
| **P1** | 尺子（eval）：忠实度 eval + 排序 eval + 报告/趋势（离线 `radar --mode eval`） | ✅ 已完成 |
| **P2** | 懂你：记忆/检索（SQLite FTS5 · CJK trigram + USER.md + LLM 选择，不向量）+ 个性化（对已会主题降权），digest 出现「与上周 X 关联」 | 🔜 下一步 |
| **P3** | 讲到极致：批判层诚实标可跳过 + 深度一致 + 正文抓全 + 扩覆盖 | 📋 规划 |
| **P4** | 会聊 + 自进化：对话深挖（`CLAUDE.md` 操作手册 + 对话提取记忆 + 改自己的配置）；E1 数据级 reviewer（自相关标注+eval→配置/prompt/skill diff→周报 HITL，窄白名单）；E2 代码级自指闭环（前沿技术 eval-gated 升级自己，HITL + worktree 隔离 A/B） | 📋 规划 |

P0 实测：扫 28 源 → 候选 ~130 → 精选 10 → 6 篇 Opus 深读，全程订阅、零报错。

---

## 扩展性示例

「加能力 = 加一个文件 / 一行配置」，核心不动：

| 想加什么 | 怎么加 |
|---------|--------|
| 一个新源（某博客/某仓库 release） | 在 `config/sources.yaml` 加一段 |
| 一种新源类型（Reddit / X / 公众号） | 在 `radar/sources/` 写一个实现 `SourceAdapter` 的文件，加 `@register("source","xxx")` |
| 一个新推送渠道（飞书 / Slack / 邮件） | 在 `radar/channels/` 写一个实现 `Channel` 的文件 |
| 一条新质量规则 | 在 `radar/quality/` 加一个 `QualityRule` |
| 调判断口味 / 详解风格 | 改 `prompts/*` 或 `config/taxonomy.yaml`（无需改代码） |

---

## 目录结构

```
agent-radar/
├── radar/                    核心包（domain + ports，很少改）
│   ├── cli.py                唯一入口：--mode daily|weekly|validate|doctor|status|...
│   ├── core/                 models(契约) · ports(接口) · registry(自注册) · pipeline · config · runner · io
│   ├── sources/              源适配器：rss · arxiv · hackernews · github_releases · hf_papers · html
│   ├── stages/               流水线段：fetch · triage · quality_gate · deepread · synthesize · deliver
│   ├── quality/              质量规则：noise_blocklist · threshold · cap
│   ├── channels/             投递：dingtalk(加签) · local · macos
│   ├── llm/                  LLM 后端：claude_code(claude -p, 默认)
│   └── obs/                  可观测：结构化日志 + 全链路 trace
├── config/                   sources.yaml · taxonomy.yaml · blocklist.yaml · config.example.toml
├── prompts/                  triage.md · deepread.md（提示词即数据，可调）
├── tests/                    单测（注入 fake LLM，无需网络/真实调用）
├── data/                     运行产物（gitignored）：candidates · digests · state(seen.json) · trace
└── docs/                     SPEC.md（完整需求）· sample-digest.md（真实样例）
```

---

## 给协作者（含网页版 Claude）的说明

这个项目由**三方协作开发**：项目发起者（定方向/做决策）+ Claude Code（本地，做一切实现）+ 网页版 Claude（连接本仓库，审查代码 + 设计提示词）。协作模式详见 **[docs/REQUIREMENTS.md](docs/REQUIREMENTS.md)**。

如果你（人或 AI）要继续开发：

1. **先读 [docs/SPEC.md](docs/SPEC.md)** —— 完整的项目意图、所有硬性约束、架构设计、每个组件的实现状态、P1→P4 路线。这是唯一的技术「真相源」。**再读 [docs/REQUIREMENTS.md](docs/REQUIREMENTS.md)** 了解需求全貌与三方协作方式。
2. **遵守约束**：技术实现要前沿且真实可 defend；实现前先调研该组件的当下 SOTA（读源码 + web）；生产级健壮；中文详解要讲懂；宁缺毋滥。
3. **架构纪律**：新能力走 adapter + 注册表，不要改核心契约（`radar/core/models.py` 的 `Item`/`Digest`）。
4. **跑 `python -m radar --mode doctor`** 确认环境，`pytest` 确认没改坏。

---

*Built with [Claude Code](https://claude.com/claude-code).*
