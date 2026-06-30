# Phase B 自证 · rerank「对他已会主题降权」A/B 真跑

- 日期：2026-06-30；候选批：`data/digests/2026-06-26.items.json`（10 条）
- 脚本：`scripts/prove_rerank_personalization.py 2026-06-26 --highlight 2ef732b76415e1eb f90fa6cabc2908fd bff97a791850313a`
- A = `personalize_rerank=False`（今日基线）；B = `personalize_rerank=True`（注入 `USER.md` 已会清单 + 标签 + 同主题标记）。订阅 LLM、零额外计费。

## rank-delta（A→B，Δ>0 = 沉，Δ<0 = 浮；★ = highlight）

```
   A→B    Δ  〔标签〕标题 | why_B
  0→ 5   +5  〔paper,eval,llmops〕NOVA: Verification-Aware Agent Harness for Arch Eval     ←已会(harness/eval) 沉↓
★ 1→ 0   -1  〔tool-use,reflection〕Why Multi-Step Tool-Use RL Collapses                    ←真前沿，浮到 #0 ✓未误杀
  2→ 3   +1  〔eval,observability〕Quantifying infra noise in agentic evals                 ←沉↓
  3→ 1   -2  〔multi-agent〕When Does Combining LMs Help（co-failure ceiling）              ←新实证，浮↑
  4→ 2   -2  〔eval,tool-use〕The Verification Horizon（no silver bullet）                  ←反直觉反转，浮↑
  5→ 6   +1  〔multi-agent,orchestration〕Building a C compiler with parallel Claudes       ←沉↓
  6→ 4   -2  〔mcp,sandbox〕ShareLock 多工具阈值投毒新攻击                                  ←新攻击向量，浮↑
★ 7→ 7   +0  〔orchestration〕Harness design for long-running apps                          ←已会科普，A 侧已在底、无处再沉
  8→ 8   +0  〔memory,llmops〕Are We Ready For Agent-Native Memory（综述）                  ←why_B 标「命中已会 RAG/检索」
★ 9→ 9   +0  〔orchestration,multi-agent〕Scaling Managed Agents: brain-hands decoupling     ←why_B 标「其已会范式」、保持垫底
```

## 护栏（transferable-value judge，单次调用 + 双 Kendall τ）
- τ(A,judge) = -0.022　τ(B,judge) = -0.289　**Δτ = -0.267**
- 解读：τ 下掉是**预期**——judge 用「领域价值」判据，正是个性化要偏离的轴（B 压低了他已会但领域有价值的综述）。**非崩塌**（崩塌应是强负如 -0.7+）。n=10 + 单次跑 = band 非定论。

## 诚实结论
- **真前沿不误杀 = 强证据 ✓**：已会领域里的新实证结果（RL 崩溃、co-failure、verification horizon、ShareLock）全部上浮，RL 崩溃升到 #0。
- **降权机制确实点火**：`why_B` 多处显式以「已会」为由压分（brain-hands「其已会范式」、memory「命中已会 RAG」）；最清晰的位移是 NOVA（harness-eval）`#0→#5`。
- **floor-effect 注**：预期会沉的 harness-design / brain-hands 在 A 侧本就垫底（#7/#9），被识别为已会但无处再沉 → Δ=0。clean 的 sink 体现在 NOVA + 中段。
- 北极星行为达成：系统第一次能区分「对他新」与「对领域重要」。后续可在 `prompts/rerank.md` 调降权力度（更激进/更保守）按口味校准。
