# Phase C 自证 · deepread 深度一致（四轴）+ item checkpoint + per-call trace

- 日期：2026-06-30；脚本：`scripts/prove_deepread.py 2026-06-26`（top 3，订阅、零额外计费）。
- 公开论文详解，无 USER.md → 脱敏可公开。

## ① 深度一致：四轴是否每篇都覆盖
样例：**"Why Multi-Step Tool-Use RL Collapses…"**（重读后的新详解）。四条必给轴全部齐备且扎实：

- **① 核心机制**：三个机制逐个拆透（"结构崩溃≠能力崩溃" / 控制 token 被不成比例放大→policy mass 重分配 / 五种监督信号×同步-交错两范式），引式号、可复现。
- **② 证据/数据**：`BFCL-V3` 基准、训练 300 条、`Qwen2.5-1.5B`/`Qwen3-1.7B`、Table 1 具体分（纯 GRPO→0.0/1.5、PRS 25.75、ETS 23.25）、训练曲线 Fig.2/Fig.5；并明判「**这是一篇实证扎实的论文，非观点性论述**」。← 证据轴固化生效。
- **③ 局限/失败模式**：5 条（交错法的 OOD 掉点代价、SFT-then-RL 不稳健、同步监督失配、RL 强依赖底模先验、只在 1.5B/1.7B 验证·控制 token 机制是否迁移大模型未讨论）。← 去掉了原「（若有）」可选门。
- **④ 新在哪 / 为何对他重要**：新机制/新方法（PRS）/新结论（交错>分阶段>同步）+ 可迁移点（控制 token 脆弱性、四态可观测 taxonomy）+ 适用边界（训练侧结论，编排闭源模型迁移有限）。

（源材料丰富，故走满详解；"真但薄→诚实简短不注水"中间档已写进 `prompts/deepread.md`，本例未触发。）

## ② item-level checkpoint（崩溃重跑跳过已完成）
```
首跑:  deepread · attempted=3 ok=3 resumed=0
复跑:  deepread · attempted=3 ok=0 resumed=3 · 新 LLM 调用 0 次（应为 0）✓
```
key 折进 `prompt_fp=sha1(deepread.md)`：改 deepread.md 会自动失效旧深读、重跑应用新框架（②③ 联动）。

## ③ per-call trace（每调用 token+延迟，per-stage 汇总进 last_run.json）
```
deepread: 3 调用 · 711956.9ms · in 8607 / out 52208 tok · opus
```
观测价值：一眼看出 **deepread(opus) 是最慢最贵的 stage**（712s、52k 输出 token）——无人值守日跑现在可定位瓶颈。`Tracer.event` 加了 `threading.Lock`（deepread 3 worker 并发安全）。无 $ 模型（订阅、剥 KEY）。

## 不回退 B
`rerank.py` 本阶唯一改动 = 给 LLM 调用加观测 `tag=self.name`；「对他新」注入逻辑字节不变、`prompts/rerank.md` 零改动（`git diff` 实证）。C 是与 B 正交的叠加层。
