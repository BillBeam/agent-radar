# Phase C 自证 · critic「有真料吗」+ 对抗样本（标题 vs 内容）

- 日期：2026-06-30；脚本：`scripts/prove_critic.py 2026-06-26`
- 输入：真实批 `2026-06-26.items.json` 10 条 + **crafted 对抗样本 5 条**（无 USER.md，脱敏可公开）。
- critic 模型：`sonnet`；订阅、零额外计费。

## 结果

**真实批 10 条 → 全 KEEP**（已过 triage+rerank 质量门，确实都有真料，**零误标**）。

**对抗样本 5 条（分寸最难一关：判内容、不判标题）：**

| 条目 | 标题暗示 | critic | 期望 |
|---|---|---|---|
| A Survey of Long-Context Agent Memory Systems | survey（综述） | ✅ **KEEP** | KEEP（实为新基准+反直觉结果+新失败模式）✓ |
| Understanding Tool-Use Failures in LLM Agents | understanding（科普） | ✅ **KEEP** | KEEP（实为一手测量+新机制）✓ |
| Acme AI Launches AgentFlow 2.0 | 发布稿 | 🚫 SKIP/high「厂商发布稿，功能罗列+邀测，无技术实质」 | SKIP ✓ |
| The Ultimate Guide to RAG: Everything You Need | 入门指南 | 🚫 SKIP/high「RAG 入门综述，已知内容重组，无新综合/新数据」 | SKIP ✓ |
| Why 2026 Will Be the Year of Agents | thought-piece | 🚫 SKIP/high「空泛，纯观点/趋势，无数据无机制」 | SKIP ✓ |

## 诚实结论
- **不被标题骗（铁证）**：两条 survey/understanding 标题、实为真前沿的样本 **全部 KEEP**——critic 判的是**内容**（新基准/一手测量/新机制），不是标题。这是「误标=最贵的错」的硬关，过了。
- **标中真垃圾**：3 条 PR/rehash/空泛 thought-piece 全部 SKIP/high，理由准确。
- **零误标真料**：10 条已过门的真实条目全 KEEP；批判层是**与 rerank 正交的新轴**（「有真料吗」），不动 rerank 的「对他新」。
- **可观测**：per-stage trace 汇总生效（critic 1 调用 / 36.3s / out 2295 tok）。
- 北极星贡献：每条到他面前的，先被「对他新」排准（B），再被「有真料吗」守住注意力（C）。
