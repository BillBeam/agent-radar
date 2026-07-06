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
6. **记忆 + 个性化**（已落地）：SQLite FTS5 记忆 + 手填「已会清单」接进重排——推「对你重要**且对你新**」的
7. **尺子 + 周度自省**（已落地）：每天 daily 跑完自动 eval 当天详解忠实度/排序；每周日 reviewer 自动盘点 eval 趋势/投票/源分布出**草案建议**推钉钉——**改不改由你拍板，零自动应用**（对话深挖 / 代码级自进化仍在规划）

---

## 核心特性

| 维度 | 说明 |
|------|------|
| 🎯 **高质量把关** | pointwise rubric 分诊 + 多层质量门 + 反幻觉 grounding，宁缺毋滥 |
| 🇨🇳 **中文详解** | 不是翻译，是讲懂——对标技术博客精读 / 源码笔记的深度 |
| 🧩 **可拓展架构** | ports-and-adapters 六边形：加一个源/渠道/规则 = 加一个文件或一行配置，不动核心 |
| 💰 **零额外计费** | LLM 走 `claude -p` headless + 你的订阅；记忆走本地 SQLite FTS5（无嵌入/向量库）；不调付费 API |
| 🛡️ **生产级健壮** | 每源熔断、每段降级、原子写、结构化日志 + 全链路 trace、单测 + eval 回归 |
| ⏱️ **时效性有保证书** | 窗口内不截尾（arXiv 分页早停）、停机不永久漏（per-source 补课窗，14 天封顶）、重大发布当天浮上来（triage 豁免+护栏）；保证与边界白纸黑字：[docs/SOURCE_GUARANTEES.md](docs/SOURCE_GUARANTEES.md) |
| 🔁 **自我进化**（E1 已上线） | 每周日自动盘点 eval/投票/源分布 → 草案建议推钉钉，**零自动应用、用户拍板**；对话式改配置与代码级闭环（E2）规划中 |

---

## 架构总览

**一句话**：Python 是确定性 harness（可靠、可扩展），Claude（订阅）是被注入的理解/判断器，记忆与配置由文件持有、被两面共享。

```
launchd（每天 08:30） ─> python -m radar --mode daily
   │
   ├─ [1] Fetch        源适配器并行抓取（无 LLM，永远先出候选池）→ data/candidates/{date}.json
   ├─ [2] Triage       claude -p 按主题 rubric 打分/打标签/判自相关（便宜模型）
   ├─ [3] Quality Gate 噪声拒绝 + 相关性硬阈值 + 封顶（可组合规则，宁缺毋滥）
   ├─ [4] Rerank       listwise 相对排序 + USER.md 已会清单降权（「对你重要且对你新」）
   ├─ [5] Critic       「有真料吗」批判层——诚实标 ⚠️可跳过（仅标注，每篇照样深读）
   ├─ [6] Deep-read    拉全文（arXiv 全文链，抓 120K/喂 80K）→ opus 教学级七节详解＋mermaid 图＋结果表
   │                   ——全部 10 篇都深读（顶配模型吃订阅额度：这是本系统最大的单日额度开销，约束见 docs/SPEC §4）
   ├─ [7] Synthesize   双渲染（钉钉精简版 + 完整版）
   ├─ [8] Deliver      CF Pages 阅读页 + 钉钉互动卡（👍/👎）+ 本地归档 + Mac 通知
   └─ [9] Remember     写内容记忆（FTS5）
   └─ 跑完自动 → radar --mode eval 今天（忠实度/排序尺子；失败只记日志不碰投递）

launchd（每周日 21:00） ─> python -m radar --mode review    E1 周度盘点 → 草案推钉钉（零自动应用）
launchd（常驻 KeepAlive） ─> python -m radar --mode serve    钉钉 Stream 监听 👍/👎 → feedback
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

# 5. 手动跑一次每日管线（抓取 → 分诊 → 质量门 → 重排 → 批判层 → 详解 → 投递）
#    生产模式是 launchd 每天 08:30 自动跑（见下节），手动跑只用于调试/补跑
python -m radar --mode daily

# 跑测试
pytest
```

### 无人值守（launchd 三件套）

三个 launchd agent 一键装好：`com.agentradar.daily`（每天 08:30，跑完自动 eval 当天）+ `com.agentradar.serve`（常驻接投票，KeepAlive）+ `com.agentradar.review`（每周日 21:00 盘点，草案摘要推钉钉）：

```bash
# .env 里加一行代理（无人值守 daily 抓西方源 + claude CLI 用）：HTTPS_PROXY=http://<代理>:<端口>
bash scripts/install-launchd.sh all         # 生成并加载（plist 自动填本仓库绝对路径）
launchctl list | grep agentradar            # 确认在跑；日志见 data/state/launchd-*.log
```

> ⚠️ **TCC 前提**：仓库**不能**放在 `~/Desktop`、`~/Documents`、`~/Downloads` 等 macOS 隐私保护目录——launchd 干净上下文里的 `/bin/bash` 读不了这些路径，agent 会 `Operation not permitted` + 126 循环（本仓库因此迁到 `~/agent-radar`）。放家目录普通路径即可，无需给 bash 完全磁盘访问。

> ⏱️ **时效性语义（诚实边界）**：每日 08:30 单跑 → 任何爆点的送达延迟 ≤ 下一个 08:30（~24h 上限），**不是实时告警产品**。Mac 合盖睡眠 = 醒来补跑一次；关机 = 当次跳过。停机/单源故障期间错过的内容由 **per-source 补课窗**自动捞回（有效窗口 = 距该源上次成功抓取 + 12h 余量，14 天封顶）——关 3 天，重开首跑窗口自动放大到 ~3 天+。逐源深度与保证等级见 [docs/SOURCE_GUARANTEES.md](docs/SOURCE_GUARANTEES.md)。X/Twitter 有意不覆盖（用户自己刷）。

完整说明（代理处理、改时间、cron 替代、卸载）见 **[deploy/README.md](deploy/README.md)**。

### 尺子与周度自省（自动运转，用户只拍板）

- **每天**：daily 结束后自动 `radar --mode eval <今天>`——裁判 LLM 把每篇详解拆成事实主张、逐条回深读时的原文找证据（忠实度），加上排序合理性，报告落 `data/eval/{date}.json+.md`；撞额度靠逐篇存盘、次日自动续。
- **每周日**：`radar --mode review` 聚合 eval 趋势 / 👍👎 投票 / top-10 源分布 / 「能改进自己」标注 / critic 统计 / WATCHLIST → 周报存 `data/self_improve/reviews/`，top-line 摘要自动推钉钉 1v1。
- **红线**：review 只产出**观察与草案**（纯文本 diff 建议）——**永远不会自动改任何配置/prompt/代码**；看完拍板后由人执行。手动随时可跑：`radar --mode eval`（无参数=跨天趋势表）、`radar --mode review --dry-run`（只出数据段）。

### 反馈投票常驻（serve，可选）

每日 digest 通过钉钉**企业机器人单聊**投**一张互动列表卡**：每行 `[N] 🆕/📚 标题 / 中文理由`（含 critic 的 ⚠️可跳过 标注）+ 👍/👎，**阅读+投票同一条消息、顺序固定、不刷屏**。要让点击直接写回反馈，常驻一个 Stream 监听：

```bash
# 凭证从 env 读（DINGTALK_CLIENT_ID/SECRET/ROBOT_CODE/CARD_TEMPLATE_ID/USER_ID，见本地 .env）。
# 钉钉是国内服务、Stream 长连接不能走西方代理 → 显式剥代理：
env -u HTTP_PROXY -u HTTPS_PROXY NO_PROXY='*' python -m radar --mode serve
# 生产方式 = 上面 launchd 的 com.agentradar.serve（KeepAlive + 登录自启，install-launchd.sh all 已含）；
# 手动命令仅用于调试；临时挂后台备选：nohup bash scripts/run-serve.sh >data/state/serve.log 2>&1 &
```

点 👍/👎 → 回调经 Stream 写进 `data/feedback/{date}.json`（与终端 `radar mark` 完全同结构）。**阅读+投票折进同一张卡**：每行 = 中文理由(+⚠️可跳过) + 可点原文链接（Markdown 组件自动识别裸 URL；钉钉卡片 Markdown 不吃 `[text](url)`/`**bold**`）+ 👍赞｜👎踩 并排（ButtonList）。卡片模板见 `deploy/dingtalk-card-template.json`。完整逐篇详解仍在本地 `data/digests/` 归档。

### 运行机制（为什么不额外计费）

`claude -p` 是 Claude Code 的 headless/print 模式。只要环境里**没有设 `ANTHROPIC_API_KEY`** 且 Claude Code 是订阅登录，这些调用就**走订阅额度、不按 API token 计费**。本项目的 LLM 适配器会主动从子进程环境里剥离 `ANTHROPIC_API_KEY`，确保永远不会静默切到 API 计费。记忆检索（P2）用本地 SQLite FTS5，无需嵌入/向量库，也零额外成本。

---

## 当前状态

| 阶段 | 内容 | 状态 |
|------|------|------|
| **P0** | 每日管线：28 源抓取 → 分诊 → 质量门 → 中文详解 → 双语 digest → 钉钉+本地 | ✅ 已跑通 |
| **P1** | 尺子（eval）：忠实度 eval + 排序 eval + 报告/趋势——**已接进每日管线**（daily 跑完自动 eval 当天） | ✅ 已完成 |
| **P2** | 懂你：记忆（SQLite FTS5 · CJK trigram + USER.md，不向量）+ 个性化（已会主题降权）——真跑 A/B 验证：已会沉下去、真前沿不误杀 | ✅ 已完成 |
| **P3** | 讲到极致：critic 批判层诚实标 ⚠️可跳过 + 详解 V5 教学级（全 10 篇 opus·80K 全文 grounding·七节·mermaid 图+结果表·每个数字必须被解释，2026-07-06 起；前身 V4 四轴点燃器）+ arXiv 正文抓全（ar5iv 护栏 + 智能截断）；「扩覆盖」按 C2 决策收紧（不稀释英文前沿，源分布进 WATCHLIST 观察） | ✅ 大体落地 |
| **P4** | 会聊 + 自进化：**E1 数据级 reviewer 已上线**（每周日自动盘点 → 草案推钉钉 → 用户拍板，零自动应用）；E 会聊（对话深挖）与 E2 代码级自指闭环（worktree A/B + HITL）仍规划 | 🔨 E1 已落地 |

P0 实测：扫 28 源 → 候选 ~130 → 精选 10 → 6 篇深读，全程订阅、errors=0、四渠道投递（阅读页/钉钉卡/本地/通知）。

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
│   ├── cli.py                唯一入口：--mode daily|weekly|validate|doctor|status|eval|review|serve + mark 子命令
│   ├── core/                 models(契约) · ports(接口) · registry(自注册) · pipeline · config · runner · io
│   ├── sources/              源适配器：rss · arxiv · hackernews · github_releases · hf_papers · html
│   ├── stages/               流水线段：fetch · triage · quality_gate · rerank · critic · deepread · synthesize · deliver
│   ├── quality/              质量规则：noise_blocklist · threshold · cap
│   ├── channels/             投递：web_reader(CF Pages) · dingtalk_card(互动卡) · local · macos · dingtalk(加签)
│   ├── memory/               内容记忆（SQLite FTS5）
│   ├── eval/                 P1 尺子：faithfulness · ranking · report（每日自动跑）
│   ├── self_improve/         E1 周度 reviewer（review.py）+ 泄漏扫描（leak_scan.py）
│   ├── llm/                  LLM 后端：claude_code(claude -p, 默认)
│   └── obs/                  可观测：结构化日志 + 全链路 trace
├── config/                   sources.yaml · taxonomy.yaml · blocklist.yaml · config.example.toml
├── prompts/                  triage · rerank · critic · deepread · eval_* · review（提示词即数据，可调）
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
