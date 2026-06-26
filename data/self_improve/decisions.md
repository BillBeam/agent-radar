# 设计决策记录（非平凡选择的「为什么这样 / 为什么不」）

> 每条记录一个非平凡的实现选择，便于审查与日后回看。P0 加固期开始。

## D · 可达性 / 代理

**选择**：代理做成一等公民，解析顺序 = 显式 config 代理 > 环境变量代理（`HTTPS_PROXY/HTTP_PROXY/ALL_PROXY`）> 直连。默认 `use_env_proxy=true`（尊重环境变量）。

**为什么**：用户在国内、多数源是西方站点，靠系统/终端的代理（环境变量）才能抓到。之前代码 `trust_env = bool(self.proxy)` —— 没在 config 里显式配代理时 `trust_env=False`，把环境变量代理**一刀切关掉**了，导致"明明挂了代理却抓不到西方源"。这是真 bug。

**为什么不**：
- 不默认直连：国内直连大概率抓不到西方源。
- 不硬编码某个代理地址：每台机器/网络不同；环境变量是最通用的约定，requests 原生支持。
- 显式 config 代理优先于环境变量：方便在某些环境覆盖（如环境变量里是不可达的公司代理时，用 config 指定一个可用的）。

**落点**：`config.proxy_settings() -> (proxies, trust_env)`；`sources/_base.py` 与 `stages/_article.py` 统一走它；`doctor` 经解析后的代理实测 openai/hf/github/arxiv 可达性+延迟，全失败/无代理大声告警。实测：env 代理被正确启用，4 源经代理全部可达。

## C · 可信层（静默 bug）

**triage 部分失败的处理**：① 整数组 JSON 解析失败 → **逐元素 salvage**（正则抽 flat `{…}` 各自 parse），能用多少用多少，而不是整批降级。② 未被模型覆盖的 index **不再静默记 0**（会被阈值默默丢掉、digest 悄悄缩水），改为走**单条权重启发式**并标「未被分诊覆盖」+ 记 `triage_coverage`，<0.8 告警。**为什么**：原行为下"模型只返回 3/6 条"会让 digest 从 6 条无声缩到 3 条，用户无从察觉。

**全源失败 ≠ 安静空 digest**：fetch 计算 `fetch_health{live,total,failed}`，全挂则 `ctx.errors` + error 日志；synthesize 顶部健康行「今天 X/Y 源成功，失败：…」，空 digest 区分「真没料(明天见)」vs「抓取大面积失败(检查代理)」。**为什么**：原来全网断了也输出"明天见"、errors=0，用户以为没料其实是坏了。

**last_run.json**：runner finally 落 run 摘要（源健康/候选/精选/深读/triage 覆盖率/降级/**token 用量**/错误），`status` 读它。token 预算暂软提示（超限 warn）。

**run-lock**：`core/lock.py`，`data/state/run.lock`(PID+时间戳)，活进程持有则中止、僵死锁(进程死/超 1h)自动夺回。**为什么**：deliver 后才写 seen，两个并发跑会双投递 + 抢 state。**为什么不**用 OS 文件锁(flock)：跨平台 + 要可读僵死信息(PID/时间)，自管 pidfile 更直观可调试。

## B · 选择层（区分度 + 新鲜 + 多样）

**triage 真覆盖全池**：删掉 `sorted(by weight)[:80]` 预砍 → score 全池（默认 ~130 全进），仅当 >200 时按**新鲜度**裁。**为什么**：按源权重预砍会让低权重社区源的好货在打分前就消失，违背"不漏好货"（web Claude 抓的真 bug）。

**破"全 9" = retrieve-then-rerank**：triage 绝对 0–10 必然挤顶、无排序价值。改为 triage 粗筛打标签/滤垃圾 → quality_gate 裁到 finalist 池(24) → 新增 `rerank` stage 做 **listwise 相对排序**（数组顺序=名次，模型被逼出梯度）+ 每条「为何压过淘汰线」当 reason；再按名次赋 score 梯度（可证非 flat）。**为什么 listwise 不 pairwise**：pairwise N² 调用太贵；listwise 一次调用、对 ≤24 池足够且产出干净梯度。**为什么 rerank 用 sonnet**：池小、排序质量比成本重要。失败回退 triage 分数序，不丢条目。

**多样性配额**：rerank 选最终 N 时每源 ≤ `max_per_source`(3)，贪心按名次填、超额 defer、不足再放宽补满。防单源（如 Anthropic 博客）刷屏。

**新鲜度 + 往期补课分离**：① fetch 对无日期源 `max_undated_per_source`(8) 限流（防 back-catalog 灌爆）+ `first_seen.json` 给每条打首见戳（首见才算新，之后 seen 去重）。② synthesize 按 `published_at` 有无分「🆕 今日新增 / 📚 往期补课」两区，头部如实写计数。**为什么**：`is_fresh(None)=True` 让无日期博客索引页把整个历史当"今日"灌入；分区 + 限流 + 如实计数 = 不再把旧文当今日（web Claude 抓的真 bug）。

**多样性配额"够不到 N 则放宽"**：`_select_diverse` 贪心按名次填，每源超 `max_per_source` 则 defer；若过完一遍仍 < max_items，再从 defer 补满。**取舍**：够 N 优先 vs 严格配额。选前者——论文经 HF/arXiv 聚合器进来"源"粒度偏粗，真要防的是单个博客刷屏；真跑实测（2026-06-26）finalist 仅 ~3 个不同源，严格配额只能出 9 条，放宽补到 10（第 4 条 Anthropic 排最末 rank 10）。要改严格是 `_select_diverse` 去掉 defer 补满那段、一行的事。

## A · 呈现层（修丑）

**标题清洗**：`_LinkExtractor` 优先取 `<a>` 内 h1–h4 文本（卡片真标题在 heading、blurb 在 `<p>`，取 heading 避免 mash）；`_clean_title` 去尾日期 + 智能截断。**已知局限**：「混了摘要又无内层 heading」的源可能仍残留——真跑自检暴露而非默认全干净（2026-06-26 实测 10 条标题全干净）。

**钉钉去反引号**：DingTalk markdown 不渲染 `` `inline code` ``/代码块，原 brief 用 `` `source` ``/`` `tag` `` 是脏的元凶。改 brief 卡片 = `**标题(链接)**` + 一行 why(rerank reason) + `*— 来源*` + `---` 分隔；删「相关度 N」「★」hashtag。`★可改进本系统` 两版都删（几乎每条都亮=噪音），self_applicable 仍存 items.json 供 P4。

**详解层级**：`deepread.md` 明令小标题用加粗行不用 `#/##/###`；synthesize `demote_headings` 防御性把残留 `#` 转粗体——条目 `###` 是每条唯一标题，不再打架。

**智能截断**：`core/text.smart_truncate` 英文回退到词边界、CJK 原子字符硬截不过删；标题/essence 共用。

## E · 反馈种子（P2 用）

**规范显示序（web Claude 抓的真正确性漏洞）**：`[N]` 编号、`items.json` 持久化、`mark` 映射必须同一个"你从上往下读的顺序"。原 items.json 按 rerank 序写，但 digest 按 fresh→backfill 显示——两序不一定一致（今天碰巧一致只因 fresh 分都高过 backfill），哪天一篇 backfill 排进 fresh 中间，`mark N` 就**静默标错条目**。修法：synthesize 定 `ordered = fresh + backfill` 为唯一规范序，`number_of` 从它生成 `[N]`，`items.json` 和 `digest.items` 都按它持久化。三者永远对齐。

**feedback 存内容快照**（web Claude 建议）：不只 `{id:{vote,ts}}`，存 `{id:{vote,ts,title,source,tags,url}}`。**为什么**：P2 要从 👍/👎 学口味，需要条目内容；光存 id 要回头 join 一堆每日 items.json，又脆又烦。快照让 P2 自包含、不怕 items.json 被清/挪。

**mark 子命令 + 健壮**：`radar mark <date> <N...> [--up/--down]`（argparse 子命令，默认 --up）。编号越界跳过给提示、items.json 不存在不崩、重复 mark 后写覆盖。`[N]` 前缀克制（标题前一个小号，不破坏 A 的干净卡片）。**不改 models.py**：编号来自持久化顺序、不入 Item。
