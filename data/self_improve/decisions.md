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

---

# P1 · 尺子（eval）

## Block ① 忠实度 eval + 原文 sidecar

**eval 是独立命令、不进每日管线**：`radar --mode eval [date]` 走自己的 `cmd_eval`，**不经** `run_mode`/pipeline/run-lock。**为什么**：eval 是"改系统时才跑"的度量工具，只读那天产物 + 写 `data/eval/`，不抓取/不投递/不动 seen/digest。塞进管线会拖慢每日跑、语义也不对（管线"产出今天"，eval"回看某天"）。

**faithfulness：LLM 做 claim 级 entailment，代码算 support_rate**（RAGAS faithfulness 式）：裁判把详解拆成原子陈述、逐条判 supported/unsupported/distorted + 证据，**eval 代码**统计 `support_rate = supported/factual`。**为什么不让 LLM 直接吐 1–5**：① 确定性、跨次可比（同 verdict 永远同分）；② 抗 LLM-judge 的 leniency 偏差（调研：裁判 TNR 可低至 <25%，gestalt 分易被"写得专业/长"带高）；③ 把"判断"（LLM 强项）与"计分"（算术）分开。**反 leniency 设计**：prompt 要求 supported 必引证原文片段、默认怀疑、具体数字/API 名从严。

**commentary 不计分**：详解里作者自己的解读/定位/价值/takeaway（deepread prompt 明确要这些）标 `commentary`、**不进 support_rate**。**为什么**：faithfulness 只该核"关于文章的事实陈述"有无原文支撑；把增值评论当幻觉罚分是误伤。

**原文 grounding：sidecar → full_text → skip，绝不 re-fetch**：① deepread 把实际喂 LLM 的 `basis[:28000]` 写 `data/deepread_sources/{date}/{id}.json`（go-forward 精确）；② 回退到 `items.json` 里**已持久化的 `full_text`**（deepread 时捕获，近似——缺 summary 前缀、截断 30000 vs 28000，故标记可能有个别假阳性，读报告时当"候选"）；③ 都没有→skip。**为什么不 re-fetch**：重抓可能与 deepread 当时看到的版本不同，会让忠实度判断本身不忠实。**关键发现**：`full_text` 本就进了 items.json，所以 eval 能评历史 digest（含 2026-06-26，那天还没 sidecar）。

**sidecar 写入防御性**：deepread 是每日关键路径，sidecar 仅 eval 辅助——`try/except` 包住、失败只 `log.warn` 不中断深读（仿 `_write_last_run`）。

**裁判执行 = 顺序 + 续跑（本会话在订阅限流下打磨出的最终形态）**：`max_workers=1`——并发会在限流下互相饿死触发超时（实测 3 路 / 2 路都超时，顺序则单篇全速 ~156s）；`timeout=420` + `retries=1`（超时不再重试 3× 白烧，靠续跑补）；**逐篇 checkpoint 落盘**（中途被杀/限流也不丢已判的）；失败**分类**(rate_limit/timeout/parse) 而非闷头吞；截断/代码围栏输出用 `salvage_objects` **救回完整扁平 claim**（裁判常开 ```json 又被截断）；rate_limit 早停不空转。**为什么**：`claude -p` 在订阅 5h 窗口接近上限时每篇可达数分钟，盲目并发 + 3× 重试是 token 黑洞——这套是真金白银烧出来的教训。`complete()` 加可选 `retries`（默认 3 不变）让 eval 能 opt out。

**覆盖率必报**：support_rate 均值**只对 scored 篇**取，并显式带"跳过 Y、无事实陈述 K、总 N"。**为什么**：超 top-6 的篇没深读→explain=None→skip；不报覆盖率会把"均值 X%"误读成全量质量。

**judge 模型档**：`ModelsConfig.judge="sonnet"`（config 加字段，**非 models.py 契约**）。离线、质量优先；可在 config.toml 覆盖为 opus。

## Block ② 排序 eval（含 web Claude 评审的三点）

**两维度、刻意不同权重**：主 = **反馈成对准确率**（唯一"排得对不对"的信号，随 mark 成长）；次 = **独立裁判一致度**（**稳定性诊断，不是正确性分**）。

**独立裁判必须用独立框架（web Claude 抓的关键点 ①）**：生产 `rerank.md` 按"深度+新颖+当下值得读"排；`eval_rank.md` 故意按**"可直接迁移进自己系统的工程价值"**排（机制/接口是否具体可复现、结论是否可操作），并**刻意不看**新颖/热点/时间/篇幅。**为什么**：若 eval_rank 照抄 rerank，高 tau 只是"模型跟自己一致"的同义反复，测不出"重要性这个标准本身对不对"（标准错了两边一起错）。独立框架下 tau 才有"第二意见"价值——本次实测 **τ=-0.2**，正说明两套标准给出不同序（独立性成立），而非排错。

**tau 是诊断不是分**：报告明确标"〔稳定性/可复现性诊断，非正确性分〕；低 τ 常见于质量相近条目，不代表排错"。**绝不优化 tau**。真正"排得对不对"看反馈相关性。

**破 position bias**：独立裁判**喂中性顺序**（按 id 排），系统显示序不能锚定它；`_item_brief` 只给标题/来源/标签/正文片段，**不给 score/reason/rank**（防系统判断泄漏进"第二意见"）。

**薄数据诚实（web Claude 点 ②）**：反馈成对准确率 `n < MIN_PAIRS(10)` 时**不报干净百分比**，直接标"样本太少，暂不构成信号——0/50/100% 多为噪声"；0 对标"暂无足够反馈"。本次 n=2，如实标为非信号（而非"100%"）。

**scope（web Claude 点 ③，知道即可）**：tau 测的是**选中那几条的顺序**，不是**选得对不对**（该选的有没有进来）——选择质量无 ground truth，反馈随时间是最接近的信号。

**Kendall tau 纯 Python**（无 scipy 依赖）：concordant/discordant 对计数，`tau=(C-D)/总对数 ∈ [-1,1]`，并附成对一致率 `C/总对数`。`_parse_order` 容错：`{"order":[...]}` / 裸 list / `{"i":..}` 元素 / 截断都能解析，缺的编号补到末尾保持全排列。

---

# arXiv 正文抓全（eval 驱动的聚焦修复；roadmap P3「正文抓全」提前做）

**根因（P1 忠实度 eval 在 2026-06-26 逮出的）**：deepread 抓 arXiv 时 `it.url` 是**摘要页**，`fetch_article_text` 只拿到摘要(~1.5–4K)，opus 凭摘要 + 自身背景知识写详解 → 补的定义/因果链/统计背景在摘要里根本没有 → 裁判正确判 unsupported（arXiv 篇 73–86%，flag 多为"脑补"）。

**修法 = 把 deepread 拿到的原文从摘要换成全文**（**不改** deepread 的 LLM 调用 / 详解逻辑，只换原文来源）。
- **识别靠 URL/id 模式不靠源名**：`arxiv.org/{abs,pdf,html}/{id}` + `huggingface.co/papers/{id}`。arxiv 源和 hf_papers 源的条目**都是 arxiv.org/abs/{id} URL**（hf_papers 显式拼 `arxiv.org/abs/{arxiv_id}`），一个修复覆盖两源。
- **回退链**：`arxiv.org/html/{id}`（官方 HTML，新论文覆盖在涨、最干净）→ `ar5iv.org/html/{id}`（LaTeXML 镜像，覆盖更广）→ `arxiv.org/pdf/{id}`（pypdf 解析，全覆盖兜底）→ **摘要**（最后兜底=原行为）。`MIN_FULLTEXT=4000` 闸门：抓回文本不够长（stub/摘要）就走下一档。**任何失败都回退、绝不崩/丢这篇**。
- **为什么 HTML 优先 PDF 兜底**：LaTeXML HTML 干净、复用现有 `_Extractor` 即可；PDF 解析噪声多、仅兜底。**实测 2026-06-26 两篇都走 arxiv-html、2s 拿到 30K 真正文**。
- **走代理**：复用 `config.proxy_settings()`；`doctor` 加 `arxiv-html` 探测点。**token 纪律**：截到 `max_chars`(deepread 传 30000) 再喂 opus——30K 全文前段（摘要+引言+方法+前部结果）远胜 1.5K 摘要、token 可控。**去版本号**：html/pdf URL 用 base id（`…v1`→base），base 永远解析到最新版、更稳。
- **依赖**：加 `pypdf>=4.0`（纯 Python、轻；`_try_pdf` 懒导入，缺了优雅跳过不崩）。**无 import cycle**：`_arxiv` 顶层 import `_article` 的 `_Extractor`，`_article` 在函数内**懒导入** `_arxiv`。
- **副作用（好的）**：Block① 的 grounding sidecar 和 items.json 的 `full_text` 自动变成全文 → eval 的 grounding 也跟着变准。

**闭环兑现**：eval 指短板（arXiv 详解脑补）→ 抓全文 → eval 复验。**同篇前后对比（item 1, arXiv 2606.26027）：摘要 grounding 4331 字 → 73%（4 处实质脑补 flag）；全文 grounding 30000 字 → 94%（factual 17 / supported 16）。4 处脑补全消失（control tokens 定义 / 超越 SFT / off-policy 定义 / 崩溃因果链 现都在全文里有据），只剩 1 处琐碎残留（"GRPO 全称…"——外部知识、原文未展开）**。这是当初做 P1 尺子的回报。

---

# P1·③ 报告 + top-line + 趋势（P1 尺子收尾）

**纯格式化、不引依赖**：`report.py` 只整理 ①② 已算好的 eval dict（top-line / markdown / 趋势），**无模板引擎、无新计算**。`run.py` 调 `report.emit`（打印 + 写 `data/eval/{date}.md`）；`cli` 的 `--mode eval` **无 date → 趋势聚合**（纯读 json、不调 LLM）。

**top-line 一行合两信号，守死 ①② 三条诚实红线**：
1. **覆盖率必报**：忠实度 X% 必带「基于 N/总 篇、跳过 Y」——否则只算了一半却被读成全量（看着 90% 其实半数没评）。
2. **反馈守 `MIN_PAIRS`**：K≥10 才报 `P%`，否则走「样本太少不构成信号（K 对）」分支，绝不出「100%(2 对)」这种误导数字。
3. **τ 标〔诊断〕**：独立裁判一致度/τ 一律标「稳定性诊断、非正确性分、勿优化」，不让它读成「排序对了 Z%」。

**markdown 可读、非 json dump**：top-line 置顶 → 忠实度（覆盖率 + 逐篇表 **低分在前** 便于扫 + 跳过项 + 标记问题点到具体处）→ 排序（反馈样本量诚实 + τ 诊断口径）。措辞沿用 ①② 诚实基调、不新造乐观说法。

**json `schema_version` 跨天可比**：沿用 `EVAL_SCHEMA_VERSION`；趋势聚合按它**跳过旧 schema / 坏文件**（坏 json `read_json` 返回 None 即跳过），稀疏（<3 天）如实标「趋势不足为凭」。

**趋势从今天开攒**：聚合便宜、现在就上——将来一眼看出某次改动有没有让数字变好（如 arXiv 全文修复后，新 daily 的 arXiv 条目忠实度应在这张表上走高；单篇 73→94 只是机制验证，规模确认靠这张表）。

**P1 尺子至此完整**：①忠实度 + ②排序 + ③报告/趋势 全部落地，下一步是「让它每天跑 + 攒反馈」，反馈够了再进 P2（个性化）。

---

# Phase A · 钉钉内交互投票（A0 走通骨架）

**走单聊互动卡片 + Stream 回调，不走 actionCard URL 回调**：互动卡片的按钮配 `callbackType=STREAM`，点击经持久连接回到 `serve`，无需公网 web 端点接 URL。`createAndDeliver` 投到 `openSpaceId="dtv1.card//IM_ROBOT.{userId}"`（1v1）+ `imRobotOpenDeliverModel{spaceType:IM_ROBOT, robotCode}`（API 形状从 dingtalk-stream-sdk 源码确认）。

**抽 `record_feedback` 让 mark 和回调共用一段写入**（web Claude 点的最关键一笔）：契约一致是**结构上保证**（同一函数），不是靠测试对账（对账测试也加了，防回归）。P2 才能放心统一吃这一个 store。

**契约结构**：`{id:{vote,ts,title,source,tags,url}}`，last-write-wins。回调用 `outTrackId="{date}:{item_id}"` 还原 date+id，从 `{date}.items.json` 取内容快照传给 `record_feedback`——和 mark 路径同一份快照来源。

**卡片更新走 ack 返回值不走单独接口**：handler `return AckMessage.STATUS_OK, {"cardUpdateOptions":..., "userPrivateData":{"cardParamMap":{status:"已记录"}}}` —— 钉钉据 ack 本地更新卡片，省一次 HTTP。

**命门 = 模板变量名/按钮 params 必须和代码对齐**（静默失败）：`cardParamMap` 的 key 必须等于模板变量名（`CARD_VARS=title/url/reason/status`）；两个按钮 params 带 `{vote:up}`/`{vote:down}`。**A0 先投 1 张卡验掉这个未知数**，再 A1 铺开。卡片标题做成 `${title}`→`${url}` 可点链接（web Claude 要求：能点进去读，否则盲投）。

**密钥只从 env**：`DingtalkCardConfig.resolved()` —— client_id/secret 仅 env；template_id/user_id/robot_code env 优先、config 兜底；空 `[channels.dingtalk_card]` 段即可启用（全走 env）。**markdown 推送保留作回退**（A1 跑通后默认关、代码留着）。

**serve 卫生**：剥 `ANTHROPIC_API_KEY`（不调 LLM）；**无 run-lock**（只写 feedback，不碰 seen/digest/pipeline）；SIGINT 优雅退出；`start_forever` 自带重连；handler 包 try/except，单次回调失败不挂服务；聊天消息 handler 打印 senderStaffId（用户发条消息即得 userId）。SDK 类名/topic 已对安装版 0.24.3 introspect 核实。

**回调 value 解析（真实结构，别假设）**：钉钉 `actionCallback` 的 `content` 是 **JSON 字符串** → `cardPrivateData{actionIds:[点的按钮id], params:{配的回传参数}}`。用户模板的按钮用**官方 `actionType:request` + `value`**（👍=up/👎=down），vote 的真实落点必须**首次点击打全量原始 payload 看清再 pin**（CardHandler 已加 RAW 日志）。`_extract_vote` 对多形状鲁棒：content 直接是 "up"/"down"、`params.value/vote/action`、`cardPrivateData.value`、`actionIds` 字面 up/down 都能捞出——真跑确认后收窄。**cardTemplateId**：钉钉模板管理基本只有 GUI，从卡片搭建器编辑页 URL 取（`<uuid>.schema`）；`scripts/list_card_templates.py` 先验凭证再 best-effort 试 API。

---

# Phase A · 真跑暴露的架构修正（旧路黑洞 → createAndDeliver 唯一正路）

**真跑结论（两套卡片 API 撕裂，踩了整整一天才定位）**：钉钉互动卡片有**新旧两套**，回调路由完全不同——

| 路 | 投递接口 | 回调去向 | 能否走 Stream |
|---|---|---|---|
| 旧（IM Bot 富文本卡片）| `POST /v1.0/im/v1.0/robot/interactiveCards/send`（cardTemplateId=内置 `StandardCard` + **内联 cardData**）| 机器人 HTTP outgoing webhook | **❌ 黑洞**——payload 里**没有任何字段**能声明 STREAM，request 按钮点击在 Stream 模式下零帧 |
| 新（卡片平台·实例生命周期）| `POST /v1.0/card/instances/createAndDeliver`（cardTemplateId=**真模板** + `cardData.cardParamMap`）| `/v1.0/card/instances/callback` | **✅** 请求级 `callbackType="STREAM"` 精准路由 |

**实测验证**：interactiveCards/send **能投递+完美渲染**（标题链接/👍👎 都正常显示），但点击按钮 serve 端**零 Stream 帧**——同一个 serve 同时收得到普通聊天消息（`/v1.0/im/bot/messages/get`），唯独卡片点击进不来。排除了多实例冲突、代理、权限（Card.Instance.Write 已开）。两个独立外部 AI 交叉确认：**旧路是断头路，不是配置能修的，是产品能力边界**。

**两个静默坑叠加导致 `templateNotExist`**（之前一直卡在这）：
1. **模板建错了搭建器**：`card.dingtalk.com/card-builder` 是**普通版内联卡片**搭建器，它建的模板（如 `1ae1f1c4-…`）createAndDeliver 永远找不到——普通版是内联发的、不靠模板引用，故**没有「关联应用」概念**。高级版模板必须在**开发者后台卡片平台** `open-dev.dingtalk.com/fe/card` 建。
2. **关联应用入口在创建弹窗、不在画布**：钉钉**不支持事后绑定应用**，关联只在「新建模板」弹窗的那一瞬间（填模板名 + 关联应用=本企业内部应用）。这就是之前在画布里翻遍找不到入口的原因。
3. 模板 ID **带 `.schema` 后缀**（官方示例无一例外），模板列表页直接显示，肉眼复制即可。

**createAndDeliver 无内联模式**（扒 SDK 0.24.x 源码确认）：body 只有 `cardData.cardParamMap`（模板变量→填值），**不存在内联 contents**，也没有跨组织通用的「内置 request 按钮模板」。高级版就是模板制，必须有个**发布过、关联了本应用、带 `.schema`** 的真模板，没有捷径。

**channel 改造（本次）**：`dingtalk_card.py` 从 interactiveCards/send 切到 **createAndDeliver + callbackType=STREAM**；`build_card_data`（内联）→ `build_card_param_map`（模板变量 title/url/reason/status）；`missing` 检查加 `card_template_id`。AI2 确认我原先手拼的 createAndDeliver body 结构本来就对，**只错在模板**（建错地方+没关联应用）。

**回调落点（按真实帧解析，不假设）**：高级版回传的按钮 params 落在 `content.cardPrivateData.params`（如 `{action:vote, vote:up}`）——和旧版单 `value` 字段不同。listener 加 `_normalize_callback`：优先用 SDK `CardCallbackMessage.from_dict`（`card_instance_id`==outTrackId、`content`==cardPrivateData）取出，失败回退原始 dict；再喂给已测的 `parse_card_callback`+`_extract_vote`。仍保留**首帧全量 RAW 日志**，真实落点点一下即 pin。

**四点自检清单（收不到回调时按这查，外部 AI 给的）**：① topic `/v1.0/card/instances/callback` 已注册 ✓；② createAndDeliver 带 `callbackType=STREAM` ✓（默认）；③ **拿 access_token 的 client_id 必须 == 注册 Stream 的 client_id**（最易静默踩——本项目同一个 app `dingxrlbmqcusr7pmsdw`，✓）；④ 同 client_id 只起一个 Stream ✓。修好模板后下一批可能撞：`param.empty`（cardParamMap 空）/ `spaces of card is empty`（openSpaceId 拼坏）。

**A0 gate = ✅ 真跑验通（2026-06-29）**：投 1 卡 → 用户点 👍 → 回调进 `/v1.0/card/instances/callback`（Stream）→ 写 feedback。原始帧实锤：`content="{\"cardPrivateData\":{\"actionIds\":[\"up\"],\"params\":{\"vote\":\"up\"}}}"` + `outTrackId="2026-06-26:1b75e302573bf166"`；`record_feedback` 写出的快照与 `radar mark` **逐键一致**。磨了一整天的回调路由就此关闭。

**真跑暴露的 3 个收尾要点（写代码/建模板必须知道）**：
1. **模板内容只能 GUI 建，但能用「导入卡片模板」一键导入**（88 菜单 → 导入/导出）。`createAndDeliver`/`/v1.0/card/instances` 等所有卡片 API 都要先有**已发布、已关联应用**的 `cardTemplateId`，没有「写模板内容」的 API。导入格式 = `{editorData(转义schema串), widgetInfo, type, mode}`；`type` 必须匹配（IM 机器人卡片 = **`type:"im"` / schemaVersion 3.0.0**，helloworld 的 `standard`/2.0.0 会报「卡片类型不符合」）。我从官方 `dingtalk-card-examples` 的 `交互组件`(im/3.0) 抠出 `Card`+`BaseText`+`SingleButton` 拼最小卡，置空 widgetInfo——**发布时搭建器会从 schema 重编译 widgetInfo**（导出实测 24544 字符、含 `node_radar_up/down`+`request`，按钮真在编译产物里）。
2. **openSpaceId 用小写 `im_robot`**（`dtv1.card//im_robot.{userId}`），而 `imRobotOpenDeliverModel.spaceType` 用大写 `IM_ROBOT`——大小写不一致是钉钉官方 codegen 写法，全大写会静默失败。`cardParamMap` 值必须全 string。
3. **按钮 request 回调落点**：`content`（JSON 串）→ `cardPrivateData.params.vote`（建模板时按钮挂 params `{vote: up/down}`）+ `cardPrivateData.actionIds=["up"/"down"]`（actionId）。`_extract_vote` 两条路径都能捞。

**A1 待办（已知缺口，非阻塞 A0）**：BaseText 的 `text.content="${markdown}"` 变量绑定**被搭建器导入时清成 `""`**（`${}` 插值对 BaseText 不生效，正文当前空白）——A1 要换成结构化变量绑定（参考 helloworld 的 Markdown 组件 `content:{valueType:"variable",variable:"markdown"}`）或改用支持 markdown 的组件，让每条目正文+可点标题正常显示；再做 per-item 循环 + 接 `deliver.py` + 投票改票高亮 + markdown 推送默认关 + serve 常驻说明。

---

# Phase A1 · 卡片正文 + per-item + 接入 daily + 入站归一化（A0 收尾）

**正文绑定修法 = 手绑现有变量 + 重发布、不重导入**（用户拍板）：A0 的 `${markdown}` 插值被搭建器清空；A1 **不重新生成/重导入**（怕覆盖已验证的按钮），改由用户在搭建器选中 BaseText → 属性面板「引用变量 → `markdown`」（沿用模板已声明的变量）→ 重发布。**教训：已验证的产物做最小原位改动，别整体重生成覆盖**。`${}` 插值绑定对 BaseText 不生效（被清成 `""`），结构化绑定（`valueType:"variable"`）才是搭建器自己的格式。

**卡片 = 紧凑投票层、markdown 简报 = 阅读层、两者并存（纠正之前"关 markdown"）**：卡片正文是纯文本 `[N] 🆕/📚 标题 — 理由`（`reason` 截 60 / `title` 80 保持紧凑），**不做可点链接**——BaseText 是纯文本组件；可点链接的阅读体验由保留的 markdown 简报承载（本就带链接 + 完整详解）。`CHANNEL_ORDER=["dingtalk","dingtalk_card","local","macos"]`，markdown 先发=先读、卡片后发=投票，靠 `[N]` 对应。

**[N]/🆕📚 是派生不存储 → 卡片自算、且必须按全表算**：`item_numbering(digest.items)` 镜像 `synthesize` 的规范序（`fresh=有 published_at` + `backfill=无` → `fresh+backfill`，位置即 [N]，🆕/📚 由 `published_at`），**在过滤深读项之前**算好——深读项是全表的**非连续子集**，[N] 必须是全表位置才能和简报对齐。`_canonical_order` 在 channel 内自算（同一规则、**不改 synthesize**），结构上保证与简报一致。

**入站归一化契约**：`InboundVote = {date,item_id,vote,user_id}` 是 platform→core 的**唯一**入站契约；`parse_card_callback` 是**唯一**懂钉钉帧的代码，下游 `item_snapshot`+`record_feedback` 只吃归一化字段、不碰帧。将来加平台 = 加一个 parser 产 InboundVote、core 不动。**只此一层，不建多平台抽象**（单用户单平台，多 adapter 网关是过度设计）。

---

# Phase A1 · 模板正文渲染的破局（钉钉 8.0 控制台两大坑 — 血泪教训）

**真跑验通（2026-06-29，模板 `c35470de`）**：per-item 卡片显示 `[N] 🆕 标题 — 理由` + 👍👎 → 点击 → 回调（`outTrackId=2026-06-26:f90fa6cabc2908fd:a1final`）→ 解析（nonce 剥离）→ `feedback` 写入、与 `radar mark` 逐键一致。Phase A 完整收官。

**坑 1：导入写的变量绑定一律被搭建器清空**。BaseText 的 `text` 不管写 `${markdown}` 插值还是结构化 `{valueType:"variable"}`，**导入后都被重置成 `content:""`**（三次导出实测）。**只有在搭建器 GUI 里手绑才行**——选中文本组件 → `text` 字段敲 `$` → 选变量。GUI 手绑会正确编译进 `widgetInfo`（`text="@toStr{@data{data.cardData.markdown}}"`），导入的不会。

**坑 2（更隐蔽）：模板一旦发布，就不能再发布**。8.0 控制台里，未发布的新模板右上角有「发布」；**首次发布后「发布」消失、变成「卡片实例管理」**（那只是管理已投递实例、不是再发布）。所以**对已发布模板的任何编辑（绑定、改色）都只存进草稿、永远到不了投递**（实测：草稿是金色按钮，投递出来还是蓝色旧版；探针卡内容空）。卡了一下午就是这个——一直在改一个发布过、改不动的模板。

**解法（唯一可行顺序）：新建模板 → 导入（按钮等结构）→ 在 GUI 里手绑变量 → 再首次发布**。把绑定包进那唯一一次发布里，绕开「发布后不能再发布」。`c35470de` 就是这么建成的。⚠️ 教训：**钉钉高级版卡片模板基本是「一次性」的——发布前必须把内容、绑定全部弄对**；要改就新建。

**outTrackId nonce（`DINGTALK_OUTTRACK_NONCE`，opt-in）**：钉钉同 outTrackId 复用不刷新（doc：换 templateId/cardData 要换全新 outTrackId）。加可选 nonce → `{date}:{item_id}:{nonce}` 强制新卡实例，便于复投/改票/换模板测试；`parse_card_callback` 按 `:` 切片取前两段，**自动剥离 nonce**（item_id 不受污染）。生产默认不带 nonce（稳定 id，每天新日期天然新鲜）。

---

# Phase A1+ · N 张卡 → 一张列表卡（用户反馈驱动重设计）

**为什么**：用户看真投递后指出两个真问题——① **顺序不保证**（N 条独立异步消息，钉钉不保证到达序）；② **廉价感**（N 张几乎一样的卡刷屏 + 全宽堆叠按钮）。结论：用 N 条消息表达一个有序列表 = 把顺序交给网络。改成**一张列表卡**（钉钉「循环渲染容器」Loop）：一条消息 → 顺序天然保证、不刷屏。真跑验通（模板 `b9ac5ebf`，2026-06-29）：1 卡 6 行 → 点某行 👎 → `feedback` 写入、与 `radar mark` 逐键一致。

**per-row 投票走 `actionId`（关键机制）**：循环内按钮的 `${loop.x}` **在 `actionId` 解析、在 `params` 不解析**（实测：回调 `cardPrivateData.actionIds=["down_<id>"]`、`params:{}`）。所以每行 👍/👎 的 `actionId=${loop.up_token}` / `${loop.down_token}`，token = `up_<id>`/`down_<id>`（服务端 `build_items` 预拼）。`parse_card_callback` 新增列表路径：`actionId` 拆 vote+item_id，date 从 `outTrackId`（`{date}:list`）；老 per-item 路径保留兼容。`loopArray` 变量 `items`（schema 定义每行字段）→ `cardData.cardParamMap.items` 传 **JSON 串**。

**loop 绑定导入能活**：loop 上下文 `${loop.x}` + `listData` 结构化绑定，导入后**不被搭建器清空**（与全局 `${markdown}` 被清相反）→ loop 卡导入即用、不必 GUI 手绑。

**构建失败的血泪（手拼 DSL 必读）**：① **样式属性给非法枚举值会「卡片构建失败」**（`bold/size/color/autoWidth` 乱设、把 dict 型 `color` 写成裸字符串）；② **嵌套 Grid 组合也会挂**。安全做法 = **复刻验证过的结构**（Loop 直接放 `[BaseText, BaseText, SingleButton, SingleButton]`，不套 Grid）、**只改 text + actionId**、样式保留原始合法值，再**逐个属性试加**。功能先跑通，样式后逐步精修。

---

# SPEC 修正轮 · 三处纠偏 + 两处诚实债落进蓝图（设计层，2026-06-29）

**为什么单独一轮**：上一轮独立 CC 会话产出 `docs/ARCHITECTURE_AUDIT.md`（网页 Claude 背书），点名当前 SPEC 的记忆选型是路线图最明确的 cargo-cult。Phase A 收口后本该进 B（P2 冷启动），但若照现行 SPEC（向量-RAG）建 B 就把审计白做——**先改图纸、再照图施工**。本轮只动设计文档 + 两处微小代码清理，**不实现 B / 不实现 E**。

**① 记忆选型：删向量-RAG → SQLite FTS5（CJK trigram）+ USER.md + LLM 选择**（SPEC §8/§10/§12 + README + CLAUDE §6）。三条理由编进蓝图防漂回：(a) 两个最相关参照都不用向量做记忆——**落定前重读本地最新源码复核**：CC 用 LLM 选文件（`src/memdir/findRelevantMemories.ts:77-141` Sonnet 读「文件名+description」manifest 返回文件名，`memoryScan.ts:84-94`；全库 grep `embedding|cosine|knn|sqlite-vec` 对记忆零命中），Hermes 用 FTS5/BM25+trigram（`hermes_state.py:291/320/2177`，commit `87e5b2fae` 2026-05-28，32 天够新）；(b) Radar 记忆规模小（每天 ~10 条、单用户画像），FTS5 + LLM 选择够用；(c) RAG 对 BeamBill 本就不新（北极星明列 RAG/context-engineering/IMA 是他已会的）——为"像个先进 RAG"而建违背北极星。adapter 边界保留，真要嵌入再加 vector adapter、默认不上。

**② 「对他新」升为 B 的一等验收**（CLAUDE §1/§4 + PHASES P2 + SPEC §8）。北极星早写了"精确 = 重要性 + 对他新"，但只是目标、零落地。本轮把它写成**验收标准**：手填「已会清单」必须**直接接进 rerank 做"对他已会主题降权"**（否则记忆建好也不改变推送 = 审计点名的失败模式）；验收 = digest 能看出对已会主题（RAG/context-engineering/harness 构建/brain-hands 解耦/IMA）降权。rerank 实现是 B 阶的事，本轮只写要求、不改运行逻辑。

**③ E 拆 E1（数据级近期）+ E2（代码级远期可选）**（SPEC §9 + CLAUDE §4 + PHASES P4）。E1 = Hermes `background_review.py` 式 reviewer，读已 emit 的 `self_applicable`/`target_component` 标注 + `data/eval/` 结果 → 提 prompt/config/blocklist/weight diff → 周报 HITL；**已有钩子 + 已有判据接成闭环，不碰代码、不需向量/worktree**，安全靠极窄工具白名单（CC `compact.ts:1125` 拒绝全部工具 / Hermes `background_review.py:459` 只白名单 memory+skills）。E2 = 原 P4 代码级自指闭环，标"最后做、非必须、强护栏"（worktree + 冻结基准 A/B + pytest+eval 双门 + HITL + rollback）；注明 Hermes 自进化也只到 skill/memory 数据层、不改引擎代码（`memory_tool.py:58`/`skill_manager_tool.py:108`）——E2 别阻塞 E1。优先级：`P1 eval 闭环断裂`（尺子已建却无人消费）= 自我变好的存亡问题，E1 正是接它。

**④ 清两处诚实债**（代码）：删 `radar/core/ports.py` 的 `LLMClient.complete` `allow_tools` 死参数（+ 实现 `radar/llm/claude_code.py`，grep 确认全仓无 caller）；删 `ports.py` 模块 docstring 里「the memory store」那个**不存在**的端口承诺（记忆端口随①重新定义、本轮不实现）。`pytest` 89 绿。

**记进路线图待办（本轮不实现）**：per-LLM-call trace（prompt 级成本/延迟观测）、deepread item 级 checkpoint（崩溃续跑）、README 整张 P0–P4 表的 P 错位漂移（本轮只去了向量措辞、没动表号，避免半改出双 P2 破表）。

---

# Phase B · P2 冷启动：FTS5 内容记忆 + USER.md 已会清单 → rerank「对他已会主题降权」（北极星第一次落地）

**为什么**：北极星 = 对他个人精确 = 重要性 × **对他的新颖性**。此前 rerank 只判「对领域新」，压不下他已会主题（RAG/harness 构建/brain-hands 解耦/IMA…）的科普。B 让 digest 第一次能看出「已会的沉、对他新的浮」。B 只用**声明的先验**（USER.md 已会清单 + 推送历史），**不吃 👍/👎 反馈**（留 D，避免过早过拟合）。

**① LEAN：不建独立 `recall` stage，rerank 直接查 `ctx.memory`。** 非关键 `recall` stage 一旦静默跳过（`pipeline.py` 降级语义）会**悄悄丢掉降权信号** = 审计点名的「记忆建好没接 rerank」陷阱；「消费即查询点」更稳、且零 `DAILY_STAGES` 改动、`Item.links` 保持死字段。独立 recall + links 叙事留 P3（「延续上周 X」）。

**② 降权信号靠 `push_tags` 精确标签重叠，不靠 FTS5 文本 MATCH。** trigram tokenizer 只对 ≥3 码点生效——已会清单里 `Go`(2)/`解耦`(2) 文本 MATCH 命中不了、`RAG`/`IMA`(3) 边界。所以 `topic_history` 用 taxonomy 受控的标签集重叠（长度无关、无引号陷阱）；`pushes_fts`(trigram) 仅作内容底座（近重复/未来叙事），`CREATE VIRTUAL TABLE` try/except 兜底（某些 sqlite 没编 FTS5 → 退化关系表，B 信号不依赖它）。抄 Hermes `hermes_state.py:319-343` 的 FTS5+触发器范式、缩到单用户小规模。

**③ 已会清单注入 rerank 的分寸（写进 `prompts/rerank.md` + 注入 `user`）：** 降的是「**已会主题的科普/综述/入门/overview**」，**不是**「他主场里的真前沿」——已会主题内的全新实证结果/反直觉/新失败模式/SOTA 仍算「对他新」、照常上浮。**绝不一刀切误杀主场**。代码只提供数据（已会清单 + tags + 同主题×N 标记），**LLM 做语义判断**（科普 vs 真前沿这条分寸只能 LLM 判、不能写成代码规则）。全部 gate 在 `config.memory.personalize_rerank`：toggle off → 与今天逐字节一致（干净 A 侧）。

**④ A/B 自证：主证据 = 条目级 rank-delta，judge 仅当灾难护栏。** P1 排序 eval 的 `independent_judge` 用 transferable-value（领域价值）判据——**正是个性化要偏离的轴**，所以 B 的 τ 掉是**预期、非「更差」**；只用它当护栏（τ 温和=健康降权，崩塌=打乱/误杀）。技巧：judge 内部重排到中性序、对 A/B 盲 → **调一次** judge 得 `judge_ids`，纯 Python `_kendall_tau` 对 A、对 B 各算一次（去掉每侧 judge 噪声 + 绕过「judge 不能原生 A/B」）。反馈成对（feedback_pairwise）此刻样本太少（<MIN_PAIRS）= 非信号，留 D。脚本 `scripts/prove_rerank_personalization.py`。

**⑤ USER.md 隐私：真 `USER.md` gitignore（同 CLAUDE.md），仓库只提交 `USER.example.md` 模板。** 仓库公开、个人/职业画像不进公开库（与「整库脱敏」一贯）。`load_known_topics` 容忍缺失 → 退化为领域新颖性，clone 即可跑。顺手纠正 SPEC §8 旧措辞「USER.md git 收录」。

**测试分工（诚实）**：单测（`tests/test_memory.py`，fake LLM）只锁**接线**（已会清单/tags/同主题标记是否注入进 prompt、toggle off 是否回到基线、store 去重/窗口、缺 USER.md 不崩）；**降权行为**由真 LLM A/B 跑证明，不靠单测。`models.py` 契约零改动。

**真跑结果**（2026-06-30，`data/real-llm-runs/2026-06-30-rerank-personalization-ab.md`，95 测试绿）：已会领域**真前沿全部上浮**（多步 tool-use RL 崩溃 `#1→#0` 未误杀；co-failure/verification-horizon/ShareLock 均浮）、**NOVA harness-eval `#0→#5` 沉**、`why_B` 多处显式以「已会」压分（brain-hands「其已会范式」、memory「命中已会 RAG」）；护栏 **Δτ=-0.267**（预期下掉、非崩塌）。**floor-effect 诚实注**：预期会沉的 harness-design/brain-hands 在基线已垫底（`#7/#9`）→ 被识别为已会但无处再沉、Δ=0。北极星行为达成：系统第一次能区分「对他新」vs「对领域重要」。

---

# Phase C · 讲到极致：批判层（守注意力）+ 深度一致（详解可信）+ 运维硬化

**为什么**：B 解决「对他新」轴（rerank 降权）。C 解决「信号密度 × 可信度」——排序≠过滤：B 排得准，C 把「看着重要实则低信号的」标出来、把「该读的」讲到位。**C 是与 B 正交的叠加层，rerank.py + prompts/rerank.md 全程未动（B 不回退）。**

**① 批判层 = 新 `critic` stage（「有真料吗」轴，与 rerank「对他新」正交）。** 输入 = `ctx.items`（rerank 后 ≤10 决赛项 = deepread 候选附近，**不是 130 全池**，省钱）。verdict 挂 **`ctx.stats["critic"]`**（`Item` 冻结 extra=ignore 装不下；`tags` 会污染 store 的 push_tags→破坏 B 的同主题信号；`reason` 是 brief why——都不能用）+ 旁车 `data/critic/{date}.json`。**v1 标注为主、安全优先**：只**高置信明显垃圾让出深读名额给下一个更好的项**（deepread `top` 过滤、**不重排** → brief 的 `[N]`/顺序/B 全不动；**换不是省**，见下方日跑准备的纠正）；borderline 只标注、仍深读；**绝不静默砍单**。`conf` 非法值归一化为 `low`（malformed verdict 永不触发误跳过，安全）。

**② 深度一致 = 改 `prompts/deepread.md`**：把 ②证据/数据、③局限/失败模式、④新在哪 从「嵌套/可选/缺失」**固化为每篇必给的轴**；加「真但薄→诚实简短不注水」中间档（原仅满详解 vs 一行降级的二元）。

**③ 运维硬化**：per-call trace = 给 `claude_code` 注入 tracer（client 原只持 config+log，没法在内部 emit）+ `time.monotonic` 夹 `_run()` + 4 调用点传 `tag=stage`；`Tracer.event` 加 `threading.Lock`（deepread 3 worker 竞争同一 fh）；per-stage token+延迟汇总进 `last_run.json`。**无 $ 模型**（订阅、剥 KEY）。deepread item checkpoint：仿 faithfulness（`eval/run.py` + `faithfulness.py`），key 折 `prompt_fp=sha1(deepread.md)`——**改 deepread.md 自动失效旧深读**（②③ 天然联动）；`pool.map`→`submit`/`as_completed` 拿逐项 hook、每项完成即 checkpoint，崩溃重跑复用、省 opus。

**真跑自证（2026-06-30，103 测试绿）**：
- **critic 对抗验收（`data/real-llm-runs/2026-06-30-critic-adversarial.md`）**：真实 10 条全 KEEP（零误标）；**2 条「survey/understanding 标题、实为真前沿」对抗样本全 KEEP（不被标题骗）**；3 条 PR/rehash/空泛 thought-piece 全 SKIP/high、理由准确。「误标=最贵的错」硬关过。
- **深度一致 + checkpoint + trace（`data/real-llm-runs/2026-06-30-deepread-depth-checkpoint.md`）**：详解四轴（机制/证据/局限/新在哪）齐且扎实（BFCL-V3 具体分、5 条局限、④ 接回他的方向）；复跑 **resumed=3、新 LLM 调用 0 次**（checkpoint 生效）；per-stage trace 一眼定位 **deepread(opus)=712s/52k out 为最贵 stage**。**不回退 B 实证**：`git diff` 显示 rerank.py 本阶唯一改动是观测 `tag=`、prompts/rerank.md 零改动。

---

# Phase C → 日跑准备：C2 跳过 + 主题串联暂缓 + critic 网关「换不是省」纠正（2026-06-30，他拍板）

**C2 扩源 = 跳过（他拍板）。** 不加中文源——他已靠 X 追国内动态、Radar 定位 = 英文前沿深度，加中文会**稀释最强部分**。网页 Claude 逐行诊断 `config/sources.yaml` 全 33 源：英文核心（harness 工程深度 / arXiv 五类 / coding-agent repos / newsletter / 框架）已很强；剩余英文扩源（LlamaIndex/CrewAI/DSPy/X）都是**边际价值或技术脆弱**（X 无干净 RSS、不硬塞脆弱 scraper）——**没有非加不可的源，不做虚的**。

**主题串联 /「上周第 N 篇」= 暂缓。** 需要**累积的推送历史**才有东西可串、才能真验；记忆现在基本是空的。**留到日跑攒起历史之后再做**。

**critic 网关措辞纠正（认知错误、非 bug，行为本来就对）：** 此前文档写「丢深读名额省 opus」**不准**。实际代码 `eligible = [非高置信skip] → top = eligible[:deepread_top_k]`，**deepread 永远做满 top_k（默认 6）**：正常（决赛 10、skip 少数）是「**垃圾让出名额、下一个更好的项补上**」= **质量提升、换不是省**；**只有 eligible 不足 top_k 的边界**才真省 opus。已把 `critic.py` / `deepread.py` / `CLAUDE.md` / Phase C 条目的措辞全部改对，不留乐观的错误描述。（checkpoint 的「省 opus」是对的：复跑复用→真跳过 LLM。）

**下一步 = 支持每天真实日跑**（launchd 定时 daily + serve 常驻 + 真跑自证），不动管线/B/C/models.py 逻辑。

---

# 投递 v3：完整详解上手机 = 网页阅读页（Cloudflare Pages），卡片行锚点点开即读（2026-07-02，他拍板）

**为什么**：逐行审计发现唯一严重偏离初衷的缺口——deepread 的**完整四轴中文详解**（`Item.explain_zh` → `Digest.markdown`）**只落本地归档、从没上他手机**。他读钉钉 = 情报台最贵、最区别于「链接列表」的 payload 读不到。纯投递层，详解**复用现成 `Digest.markdown`、绝不重新生成**（不碰 deepread/synthesize/选择层/B/C/`models.py`）。

**投递形态（绕多轮的血泪结论，别翻案）**：钉钉卡片装不下长文（无文件组件、`sampleFile` 不支持 .md、docx 无 URL）；钉钉文档 raw API 无 markdown 直写（`/contents` 404）、只能逐块写（失真）+ 私有空间未落定 + 第 3 批权限 → 太重太脆，**弃**；独立文件 / 多条消息他明确拒绝（丑、刷屏）。**终选 = 详解渲染成网页阅读页**，投票卡每行链接指向当天页对应锚点 `#item-N` → 扫卡+投票在钉钉、点一行在浏览器顺畅读那篇四轴详解，手机电脑都行、零额外消息。

**隐私 = B 档：不可猜 URL + noindex（他拍板，不做 Access）。** 内容是公开论文详解，敏感点仅「选择指纹」；调研实证 Access **保护不了 `*.pages.dev` 生产主域名**（不拥有该域名），真 Access 要么自备域名、要么走 preview 分支+登录（每次会话可能要在钉钉内输 OTP 验证码），**对低敏内容不值得那摩擦**（这工具命脉是他愿意每天点开）。B 三重防护：① 页 URL = `https://<项目>.pages.dev/{seg}/`，**`seg = HMAC-SHA256(AGENT_RADAR_WEB_SECRET, date)[:32]`** → 不可枚举（无 secret 算不出）、**date 派生=同一天必同一 URL**（重跑幂等、卡片回填稳定、不会跑一次变一个）、**各天单向独立**（分享某天不泄露其它天，优于「静态密钥段+可枚举 date」且免维护 date→url 状态文件）；② 每页 `<meta robots noindex>`；③ `data/web/site/` 进 `.gitignore`、不入公开仓库。

**★ SECRET 铁律（他定死）**：`AGENT_RADAR_WEB_SECRET` 由他本地 `openssl rand -hex 32` 自生成、自放 env；**代码只 `os.environ.get` 读、绝不生成/打印/写进任何文件/日志/本决策**。只有**派生的 `seg`**（能力令牌、单向、本就要进 URL 给卡片/钉钉用）会出现。`web_reader.send` 读 secret→算 seg→`del secret` 立即释放；`resolved()` 只返回非密 ids（无 token、无 web secret）、`missing()` 只报**键名不报值**。`git grep` 自证全仓无 secret 明文、无硬编码长 hex。

**实现接缝（加能力只加文件 + 一处唯一改点）**：
- **渲染器** `radar/channels/_web_render.py`（`_` 前缀→注册器跳过）：**镜像已验证的 `_docx_render.py` 行解析**（同一套 `_HEADING/_BOLD_LINE/_BULLET/_QUOTE/_LINK/_INLINE`），改吐 HTML+内联 CSS，**零新依赖、保真对标 docx**；识别 `### [N] …`→`<h3 id="item-N">`+建目录；移动优先 CSS、四轴 `**加粗行**`→`<p class="axis">`、critic→`<blockquote>`、`noindex`。**不改 `_docx_render.py` 本身**（保护已验证产物）。
- **渠道** `radar/channels/web_reader.py`（`@register("channel","web_reader")`）：算 seg→渲染→写 `data/web/site/{seg}/index.html`→`subprocess npx wrangler pages deploy`（生产；CF creds 走**继承的 env**、不上 argv、不 log）→成功则 `ctx.stats["reader_url"]=…/{seg}/`。CF **走公网**（不 `trust_env=False`，区别于钉钉渠道）。
- **顺序** `deliver.py:CHANNEL_ORDER`：`web_reader` 排到 `dingtalk_card` **之前**→页先部署、URL 进 `ctx.stats`，卡片再读。
- **卡片回填**（唯一改点 `dingtalk_card.build_items`）：行链接 = `reader_url and f"{reader_url}#item-{num}"` 否则 `it.url`——**`[N]` 编号 `_canonical_order`/`item_numbering` 全不碰**。
- **配置** `WebReaderConfig`（仿 `DingtalkCardConfig`，密钥 env-only）；`dingtalk_file`（docx）**在 web_reader 启用时 `is_enabled` 自动让位**（免「卡片+多一条 docx 文件消息」的双消息）。

**健壮/幂等**：渲染/写/部署任一步失败 → **不写 `reader_url`** → 卡片每行优雅退回 arxiv 原文（`deliver.py` per-channel try/except 再兜一层）；`npx` 缺失 → 跳过；同一天重跑 seg 稳定、覆盖同页。

**自证（130 测试绿）**：① **真实 2026-06-30 详解离线渲染与 docx 逐项对齐**——日题/分组/条目 =1/3/10、四轴加粗小标题 **58（完全一致）**、标题外链 **10**、critic 引用 1、锚点 `[1..10]` 连续且**目录顺序完全匹配**（`[N]`↔`#item-N` 有保证）、残留 `**`=0、noindex 就位、35538 chars md→48586 chars html；② secret 审计全仓干净。

**真部署 ops（2026-07-02，131 绿）**：他授权后我建 CF Pages 项目 + 真部署 06-30 页并**真机验活**。两个坑：
1. **wrangler 首下超时**：`npx -y wrangler` 首次要下载 wrangler（走公司代理很慢）→ 200s 超时。缓存后正常。**wrangler 自己检测并走 `HTTPS_PROXY`**（启动打印 "Proxy environment variables detected"）——代理不是问题，且 web_reader `_deploy` 继承 env 天然带上代理（CF 是西方站、国内必须走代理，这与钉钉渠道 `trust_env=False` 相反、是对的）。
2. **preview/production 分支坑（关键）**：`wrangler pages deploy` 不带 `--branch` 时**按本地 git 分支推**（仓库在 `master`）→ 落 **Preview**（`master.agent-radar.pages.dev`），生产 `agent-radar.pages.dev` 空→**404**。修：`_deploy` 显式 **`--branch main`**（=`pages project create --production-branch main`）→ 无论本地 git 在哪分支都落生产稳定别名。补 argv 回归测试。
- **真机验活**（curl 走代理）：`https://agent-radar.pages.dev/{seg}/` → `noindex` + `<title>` + 目录 + `item-1..10` 锚点 + 四轴 `class=axis`；**站点根 `/`=404**（无 root index、不可枚举，B 档私密成立）。CF token/account + **我生成的 secret** 全落 gitignore 的 `.env`（`run-daily.sh set -a; . .env` 加载；secret 全程不回显/不进 git，只用派生 seg）。**剩**：他手机截图（美学终审）+ 卡片→页 端到端在下次真 daily 自动发生（已 wired+单测）。

---

# Phase C2+D：收紧选品（保 harness 落点）+ 详解锚定 agent/harness（2026-07-03）

**灵魂（不对称）**：只砍"纯模型、对造 agent/harness 无具体可迁移点"的；绝不误杀"模型侧方法、但结论直接砸到 harness 设计"的（原型 = 06-30 [1] 可解释性探针篇：模型方法 + 一等 harness 落点，必须高分存活）。

**A1 收紧 fetch = 做了（真刀，pytest 134 绿）**：`config/sources.yaml` arxiv-agents 去掉 **`cs.LG`**（通用 ML 大类＝可解释性/训练/模型行为主漏口）+ 去掉宽关键词 `LLM/language model/reasoning`，改为 agent 落点词（agent/agentic/multi-agent/tool use/function calling/MCP/retrieval/RAG/agent harness/planning/orchestration）；`radar/sources/arxiv.py` 默认值+docstring 同步；补 `tests/test_arxiv_source.py`（真实 config 不含 cs.LG/宽词、含 agent/agentic）。**量化验证**（06-30 缓存池近似）：50 篇 arXiv 砍 22（几乎全纯模型/跑题：蒸馏/稀疏注意力/3D生成/kernel工程…），[1] 存活（靠 agent/agentic 关键词 + agent-safety 篇几乎必交叉 cs.AI）。**取舍**：drop cs.LG 优于"保留+收窄"——三道闸兜底（关键词 AND 门 + rerank"对他新"）；残余风险=极少 cs.LG-primary 的 agent-RL 篇被漏，兜底=发现漏了再加回。⚠️ [1] 去 cs.LG 仍命中本地无法实证（虚构论文、adapter 不留原始交叉类别），是推断。

**A2 triage 落点判据 = 本轮不做（探针证伪 + 网页 Claude 与我收敛）**：在 06-30 缓存池跑"旧 rubric vs 新 rubric（+落点判据）"双 haiku A/B → 没干净收紧（高分档仅 28→24），且那句"有落点→高分"被 haiku 拿去把中档纯模型 RL 篇误抬。结论：**细粒度"有无 harness 落点"超出 haiku pointwise 能力**；该靠 fetch（A1 确定性）+ rerank（sonnet，已把 [1] 排 #1）分工，**triage 保持粗筛、不加它会误用的细判据**。度量瓶颈是没 ground truth（非统计）。**将来若加走"封顶+豁免"**：封顶 key 在"说不出可落地 harness 启示"（绝不 key 在"模型侧方法"）、豁免说"不封顶"而非"抬高分"（堵误抬）；并造一个人工标注的 20-30 条 keep/cut held-out 集接进 P1 eval 再验。

**B 详解 V3 = 锚定 agent/harness（探针过、待修 2 处再铺）**：在已验证的 V2「点燃入门器」基础上，只把①的类比取材从"通用后端"换成"**agent/harness 概念优先**"（agentic loop/tool system/权限/context engineering/ReAct/brain-hands/MCP 等），并给 4 条锚定示例（probe≈harness hook/middleware/权限门、hold-out≈换没见过的 tool 测泛化、steering≈往 context 注东西但在激活层、SAE≈把激活拆成可命名特征通道）。同一 grounding 重生成 [1]：**锚定成功**（ML 词都落到他的世界、比 V2 后端类比更贴）。**但探针暴露 2 个必修问题（铺前修）**：① **模型在"你能怎么用"轴自行猜出并写进读者的真实雇主/产品名**（脱敏 prompt 未提及、模型幻觉出来的）——详解要上 CF Pages 阅读页 = 指纹泄漏 + 幻觉（换篇可能猜错公司）→ 需硬规则禁止命名读者公司/产品、一律说"你的 agent 系统/harness"；② V3 变长（V2 2206→V3 2815 字）+ ① 把子机制列表化 → 往"论文压缩件"飘、违"宁短勿密" → 需收紧。**这 2 问题已发提示词给网页 Claude 求解，修好再铺全局。**

**B 详解 V4 = 上线（写进 `prompts/deepread.md`，2026-07-03）**：网页 Claude 在 V3 基础上出 V4，加 4 处修复（其余 V2/V3 已验证的四轴/③三档防虚/术语从严锚定/反幻觉/加粗行不用# 全保留）：① **读者身份护栏（从严）**——不仅禁公司/产品/姓名，**还禁推断读者的具体业务领域/行业/团队/项目**（"只有认识他的人才知道"的场景都算泄漏），场景一律用通用占位「你的 agent 系统/harness」，猜身份=幻觉同罪；② **长度/密度硬规则**——主=「① 只讲一个统摄全篇的核心洞察 + 至多一个类比，绝不逐个罗列子机制/变体」（直杀 V3 的"①列三种探针"），辅=整篇 ~2000 字（硬上限 ~2400）；③ **④ 节号护栏**——只引原文确有的章节/图号，拿不准就泛指，绝不编 §X/Figure Y；④ **锚定示例防硬套**——4 示例标"只在论文真涉及且贴切时用"。**双探针验收（远 [1] 可解释性 + 近 [9] Claude Code auto mode，同一缓存 grounding、临时脚本、不碰 deepread.md/checkpoint/daily）全过**：身份护栏两篇都守住（**近主题主场也零公司/领域/业务泄漏**，这是最易破处）；长度 2148/2280 字均**在硬上限下**（没硬截）、密度是"①一个洞察"结构规则压下来的、①段仅 522/425 字；④ 引的章节/图号 **grep 原文实锤全部真实存在、零编造**；关键数字 grep 全真（反幻觉守住）；ML 词（AUC/Cohen's d/多数类基线/FPR/FNR/steering/SAE）全就地锚到 agent/harness；近主题没把已会 harness 概念科普成话痨。**写进 `deepread.md` → `prompt_fp` 变、deepread checkpoint 自动失效 → 下次 daily 自动用 V4 重跑**（06-30 归档页仍是旧密集版，需要时手动重生成+重部署即可看 V4）。只改 prompt，未碰 triage/rerank/synthesize/deliver/`_web_render.py`/`models.py`/pipeline。
