# 真实日跑自证 · 2026-06-30（首条 B+C 全开的无人值守 daily）

真实配置（真 USER.md、抓取走代理、投递钉钉）跑一次完整 `--mode daily`。脱敏：只录运行
指标与 per-stage trace，不录 digest 正文（正文在 gitignore 的 `data/digests/`，本地阅读）。

## 端到端：干净跑通、零错误
```
pipeline = fetch → triage → quality_gate → rerank → critic → deepread → synthesize → deliver → remember
fetched   候选 117 · 源 28/28 live（代理抓取全通）· 跳过已读 4
quality   117 → 24（过门）
rerank    24 → 选 10
critic    judged=10 skip=1 high_conf_skip=1      ← 抓到「同一篇论文两源重复」标 SKIP/high
deepread  attempted=6 ok=6 critic_skipped=1       ← 网关生效：重复项让出名额给下一个更好的项
deliver   {dingtalk: True, local: True, macos: True}   ← 全渠道投递成功
remember  pushed=10                                ← 内容记忆写入
run done  errors=0
```

## per-stage trace（per-call token/延迟，新增可观测）
```
triage    1 调用 · haiku  · 14442 out · 161s
rerank    1 调用 · sonnet · 11402 out · 181s
critic    1 调用 · sonnet ·  2306 out ·  40s
deepread  6 调用 · opus   · 95768 out · 1354s   ← 一眼看出最慢最贵的 stage（22.5 分钟）
synthesize 1 调用· sonnet ·  1662 out ·  32s
```
观测价值实锤：无人值守日跑的瓶颈（deepread/opus）现在可定位、可优化。

## 真跑暴露并修掉的 bug
critic 抓到重复项标 SKIP/high，但 digest 里 **⚠️可跳过 标注没渲染**——`synthesize._emit`
没把 verdict 传进渲染函数（单测只直接测渲染函数、漏了这段接线）。已修 + 补集成测试（104 绿）。
**这正是真跑的价值：单测全绿也会漏接线，端到端真跑才照出来。**

## 投票卡 + 投递合并到 1v1（截图反馈后修）
- 日跑此前没投票卡 = `config.toml` 缺 `[channels.dingtalk_card]`（已补，空段即启用、凭证只从 env）。
- 用户截图反馈两点已修：① **卡片序号跳号**（只投深读 6 项、按全表位置编号 → `[1][2][3][8][9][10]`）
  → 改投**全部 10 项、连续 [1]-[10]**、与简报逐条对应（真投 10 行验过）。② **简报在群、卡在 1v1
  两处** → 选「全合到 1v1」：简报改由企业机器人 **OTO sampleMarkdown** 发到 1v1，阅读+投票同一会话
  （真投验过：简报 2268 chars 落 1v1，且 **⚠️可跳过 标注随简报渲染**＝`_emit` 修复连带证实）。

## 无人值守
`bash scripts/install-launchd.sh both` 一键装 daily 定时 + serve 常驻（详见 `deploy/README.md`）。
