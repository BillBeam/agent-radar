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

---

# 7.3 跑后诊断：深读分配机制 + 深读"不够深"根因 + 去重 bug 修复（2026-07-03）

**深读分配机制（查清、未改）**：深读集 = **rerank 名次 top-6**（`rerank.py:60-61` 把 `it.score` 覆写成排名梯度 rank1=10.0…rank10=1.9，深读取 score≥5.5 的前 6；critic 高置信 skip 让位）；`[N]` 是 synthesize 的 **fresh+backfill 显示序**、与 rerank 名次不同（故深读 `[N]` 非连续）。7.3 实况：rerank 把 3 篇 Anthropic 工程博客排 rank1-3（=显示 [7][8][9]）、3 篇 arxiv 排 rank4-6（=[1][2][3]）；**[5] MemSyco(memory)、[6] EvoPolicyGym(eval) 排 rank8-9 掉出前 6 → 只一句话**，而 [2](自主科研 pipeline，离他核心远) rank5、[3](AgenticSTS memory) rank6 进了深读。[10] 被 critic skip=high（营销向 tool-use）。**问题**：memory/eval 是他核心工作域，[5][6] 沉了、[2] 反而深读——rerank「对他新」是否把他**工作域**（不只"已会"）也降权值得他判断；但 [3] 也是 memory 却 rank6，非 memory 一刀切。**未动 rerank/`rerank.md`/USER.md**（B/C 分寸敏感、误杀主场最贵）——摆真实分数给他 + web Claude 拍板；可能方向是 USER.md 区分"已会 basics→降 overview"vs"工作域→要前沿"。

**深读"不够深"根因（查清、未改，先诊断）**：6 篇深读 3 篇建在残缺原文上，两类根因（读缓存 `source_text` 长度实锤）——① **长度上限截断**：[2] arxiv、[8] Anthropic 博客 `source_text` 都=**28000（正好 grounding 上限）**、结尾断句中（[8] "…but the gradi"）→ 正文比 28000 长、被 `deepread.py [:28000]` / `fetch_article_text max_chars=30000` 截了，**非抽取失败**。② **arxiv 正文抽取失败→退摘要**：[3] `source_text` 仅 **5407 字、结尾 "Disable MathJax"（=arxiv abs 摘要页页脚）**→ html/ar5iv/pdf 全链失败退摘要（`2607.*` 太新、html/ar5iv 未出）。**提案（他定）**：长度截→评估调高上限或智能截（留 intro/method/results、砍 references）；抽取失败→太新的论文正文客观不可得，可**检测"仅摘要/薄"→深读名额让位给料足的**（连问题一：[3] 仅摘要却占深读名额、[5][6] 完整却一句话，说不通）。均属 P3「正文抓更全」，先摆根因、未盲改。

**去重 bug（明确 bug、已修+补测，`137 绿`）**：`fetch.py` pool 按 `it.id=sha1(url)` 判重 → [3]`…/abs/2607.02255v1`(arxiv 源) 与 [4]`…/abs/2607.02255`(hf 源) URL 差 `v1` → 判成两条。修：`fetch.py` 加 `_dedup_key()`——arxiv 项用 `arxiv_id_from_url()` + 去 `vN` 后缀作键（`arxiv:2607.02255`）、跨源/跨 v 判重；非 arxiv 保持 per-url id 不变。补 `tests/test_fetch_dedup.py`。（跨**天** `seen` 判重仍按 url-id、未动，属另一机制，留意后续。）只改 fetch 层，未碰 rerank/deepread/`models.py`。

# 7.3 跑后修复包（2026-07-04）—— B名额/C截断/ar5iv护栏/rerank超时/E日期标签

## 根因判定（先查真相，全部有实锤）
1. **[3] 深读薄的真根因不是「论文太新拿不到全文」**：ar5iv 对未转换论文 301/307 重定向回 arxiv.org/abs
   摘要页，摘要页抽出 ~4.7K > MIN_FULLTEXT(4000) 闸门 → 假报 src=ar5iv「成功」→ **PDF 兜底从未被尝试**。
   实测 2607.02255 的 PDF 一直可用（护栏后 src=pdf len=80000）。修：`_arxiv._try_html` 检查最终
   r.url 落在 /abs/ 即判失败（摘要页永远不是全文）。
2. **07-03 排序=粗筛分序，个性化 rerank 当晚根本没跑**：radar.log 22:06/22:10/22:14 三连 timeout →
   「rerank failed — falling back to triage score order」；trace 726.1s = 240s×3 + 2s + 4s 退避，分毫不差。
   历史成功调用 181s/227s，贴着 240s 默认超时线。→ 当天 [5][6] 沉底不能归罪 USER.md/rerank.md（个性化
   未生效）。修：rerank 显式 timeout=480；降级置 stats["rerank_degraded"] → digest 头部横幅（rank→梯度分
   会把回退顺序伪装成自信的个性化排序，必须可见）；claude_code 失败尝试也记 trace/by_stage（本次事故
   正是被「成功才记录」的观测盲区藏住的：by_stage 里 rerank 整段消失）。
3. **Anthropic 索引页有日期、适配器没取**：卡片结构 `<h3>标题</h3><div>Apr 23, 2026</div>`，日期就在锚点
   文本里（strip_trailing_date 一直在从标题剥它=它一直都在）。真实日期显示这些工程文是 2025-11~2026-04
   的旧文——「往期补课=无日期」的表象下其实是「旧文被当无日期收录」。

## 改法与分寸
- **B 名额政策（deepread.py）**：先并发探所有 eligible 的 grounding（纯 HTTP；opus 只跑选中的），arXiv 项
  fetched < THIN_ARXIV_CHARS(8000) 判「薄」（摘要页抽出 4-6K，真全文 ≥12K），薄的让位给下一条完整的；
  完整不足 top_k 才用薄的补满（诚实降级，V4 prompt 会如实说明截断）。判「薄」用长度阈值而非链路 src
  标志——src 标志刚被 ar5iv 重定向骗过一次。checkpoint 项复用 full_text 不重抓。非 arXiv（博客）
  行为不变（页面即文章）。
- **C 智能截断（deepread.py）**：FETCH_CAP 80000 抓够 → 砍尾部低信息节（References/Bibliography/
  Acknowledgments/Appendix，只认正文后 60% 里的独立标题行）→ 仍超预算则「头 70% + 尾 30%」夹显式
  省略标记，段落/句边界吸附，绝不断句中。GROUNDING_CAP 28000 不变（不涨 opus token）。
- **显示新鲜度统一谓词（models.is_display_fresh）**：🆕 = 有日期且 ≤96h（对齐最宽源 leash：arxiv/hf 96h）。
  synthesize 与 dingtalk_card 必须共用——过去两边各自写「有日期即新」靠巧合一致，html 日期落地后
  必然分叉 → 卡片 [N]/投票/mark 会映射错条目。fetch 的 bounded-history 同步泛化为「无日期或过窗口」
  （feed 源上游已按窗口过滤，零变化；只约束 html 源旧文，维持原每源 8 条上限——洪水控制不变、日期变诚实）。
- **标签改说法（E2）**：📚「往期补课（无发布日期，首次收录）」→「首次收录（往期/无日期内容，非重复推送）」，
  头部计数「往期补课 N」→「首次收录 N」——旧措辞读起来像「旧存货重复推」，实义是「第一次进雷达」。
- **不动**：rerank 排序语义 / USER.md / deepread_top_k / 模型分层 / V4 详解 prompt / 去重（5eccba3）。
  A（主场 memory/eval 被压）与 D（源结构）只出诊断数据待拍板，探针只读重放、不写管线状态。

## 自证（真数据）
- 07-03 名额重放（去重后无 [4]、[10] critic skip/high）：深读 = [7][8][9][1][2][5] —— [5]（memory 域、
  完整 grounding 80000）✅进深读，[3]（薄 4705）✅让位；[6] rank#8 仍一句话（名额=6，符合预期）。
- 截断真跑：[2] basis 80702→27712（References 零残留、结尾=论文真结论段）；[8] 39780→27419
  （旧硬截断口「…but the gradi」消失，新结尾=博客收尾段）。
- Anthropic 现抓：11/12 卡解析出真日期（2025-11-24 ~ 2026-04-23），featured 卡无日期落 None（符合预期）。
- pytest 153 绿（137 → 153，新增 deepread 名额/截断/护栏/降级横幅/日期解析/失败调用观测等 16 项）。

# ★ P0 隐私修复（2026-07-04）：claude -p 上下文渗漏 —— 每次 LLM 调用都带着 CLAUDE.md

## 发现路径
A 诊断探针的**基线 arm**（无任何个性化注入）的 why 里出现了只存在于本地 CLAUDE.md/SPEC 的信息
（读者的自进化路线图、他自建的评测系统）→ 起疑 → 直接问 claude -p 它上下文里有什么 → 实锤：
**从仓库目录跑 `claude -p` 会自动加载 cwd 及所有祖先目录的 CLAUDE.md**（`--system-prompt` 只换系统
提示词、压不住这个）。生产管线 triage/rerank/critic/deepread/synthesize/eval 的每一次调用都带着
两份 gitignore 的身份档案（本仓库操作手册 + 祖先目录的另一份含更完整个人背景）。

## 为什么严重
- deepread 的产出上**公开 Cloudflare 阅读页**——V4 身份护栏一直是唯一防线；2026-06-30 详解④冒出
  雇主场景、V3 探针泄漏雇主，**根源都是上下文渗漏而非模型先验**（当时误判为「opus 自行带入/幻觉」）。
- B 的 A/B 设计被污染：personalize_rerank=off 的「基线」其实也认识读者 → toggle 对照失真。
- 个性化必须只走受控通道（USER.md → rerank preamble），不能走 cwd 意外。

## 修法与验证
- `claude_code._run` 子进程加 `cwd=<per-user tmp>/agent-radar-llm-cwd`（/var/folders 祖先链上无任何
  CLAUDE.md）；测试断言 cwd 不在仓库下且全祖先无 CLAUDE.md。
- 修后真问验证：「无任何项目上下文」✅。残留=Claude Code 账号级注入的用户邮箱（公开化名身份，
  非档案信息，客户端不可剥）。
- 影响预期：排序/详解丢掉那份「意外的读者知识」→ 前后排序不可直接对比；这正是把个性化收回
  受控通道的代价，B 的 A/B 从此才是干净的。

# 7.3 诊断 A/D 收口（2026-07-04）—— 重放数据与结论（涉及排序/源结构的改动待拍板，本条只存数据与提案）

## A. [5][6] 主场沉底的根因链（三层，全实锤）
1. 直接根因 = rerank 超时回退（已修，见上一条）：07-03 实际顺序就是 haiku 粗筛分序。
2. 重放验证（sonnet listwise，2 种中性输入序；P0 修复后干净上下文 3 arm 成功 + 修复前条件 2 arm 对照）：
   | 条目 | 07-03实际(回退) | pers-A | pers-B | base-A |
   |---|---|---|---|---|
   | [5] 记忆谄媚 benchmark | #8 | #3 | #6 | #8 |
   | [6] 策略演化 eval 环境 | #9 | #4 | #7 | #9 |
   | [8] eval 入门科普(博客) | #2→吃了深读 | #10 | #10 | #10 |
   | [9] harness 最佳实践(博客) | #3→深读 | #9 | #4 | #1 |
   → **个性化不是在误杀主场，而是在拯救主场**：基线 arm 把 [5][6] 排 #8/#9（「角度较窄/较通用」），
   个性化 arm 靠「已会主题里的新失败模式/新结果照常上浮」豁免把它们拉回 #3~#7。
   → [8] 在全部干净 arm 一致垫底 #10（连基线都判它科普向）——7.3 它排 #2 还吃了深读，纯回退事故。
   → 修复合并推演（rerank 正常 + 名额政策 + ar5iv 护栏 + 去重）：pers-A 序下深读集=[7][2][5][6][3][1]，
   主场前沿两条全深读。
3. 结论：**USER.md 与 rerank.md 的「对他新」分寸不需要防误杀修改**。可选微调（待拍板）：rerank.md
   豁免清单显式加「新 benchmark / 新测量方法」（现靠「新失败模式/一手数据」间接覆盖；pers-B 里 [6]
   仍被「偏综述基准」压到 #7）。观察项（不动）：⟨近30天同主题×K⟩在常推主题上恒高（论文侧 ×8/×9），
   当前豁免能救真前沿（[5] 逃过降档）；且单 arm 有序效应（[9] 在 pers-A #9 / pers-B #4）说明
   listwise 排序天然带噪声，单日名次不宜过度解读——先攒几天干净跑再定。

## D. 「源太单一 / harness 源缺席」真实数据（triage+gate 重放，haiku；07-03 原始分未持久化、
##    当天条件已不可复现——干净条件近似，作趋势判断、不作精确回放）
- 137 候选按源：arxiv 50 + hf 39（论文占 65%）· latent-space 10 · anthropic-eng 8 · anthropic-news 8 ·
  hackernews 8 · simonwillison 3 · gh-* 11。28/28 源全活零抓取失败；13 源当天有产出，其余无新帖。
- 重放分：anthropic-eng 均 7.6、8/8 过阈、7/8 进 finalist（压倒性）；arxiv 22/50 过阈 12 进；
  hf 10/39 过阈 3 进；latent-space 4/10 过阈 1 进（7.0）；simonwillison 1/3 过阈 1 进（7.0）；
  gh-*/HN/anthropic-news 绝大多数 0-4 分（release notes/nightly/招聘贴/产品新闻，低分有理有据）。
- **harness 工程源不是被系统卡死**：两条 7 分工程好文进了 finalist-24，只是在回退的纯分数序下
  永远排在 8 分论文后面、出不了 top-10；rerank 正常后按工程价值参与 listwise 竞争。
- 跳过已读 7 = 06-26 交付 4 篇 + 06-30 交付 3 篇 anthropic-engineering（仍挂索引页）——被跳过的
  恰是他已读过的最强 harness 内容（机制正常、非缺陷）；当日 arxiv/hf 无一在 96h 窗口内重现。
- 多天分布：4/4 次真实跑 top-10 全部出自 arxiv/hf/anthropic-eng（40/40 条）。系统性无疑，但本轮
  修的三件（rerank 生效、科普沉底、日期诚实）都会改变格局 → **提案：源结构先不动，攒 2-3 天
  干净跑分布再定**（样本也少：仅 4 天、其中 2 天在个性化上线前）。
- HF 重复 arxiv 实锤两次（07-03 [3]/[4]；06-30 Scaling the Horizon 双源两条）→ 去重已修（同篇合并、
  取高权重）。**提案：HF 保留**——重复成本已消，其独立价值=人工精选信号；不建议降权/去源。

## E3. 无日期源历史边界（说明）
索引页 limit=20；backfill 每源每跑 ≤8（现含过期旧文）；交付即 seen 退役（已退 7）→ 存量单调耗尽、
无「整档涌入」风险；日期修复后旧文以真日期落「首次收录」。featured 卡版式无日期 → 如实留无日期。

# 源结构收口（2026-07-04）—— 本轮不动 sources.yaml（正式决定，把 D 提案落为结论）

**决定：不调源权重、不砍源、不加多样性配额。** 没有「正确的旋钮」可拧：
1. **「harness 源缺席 top-10」的主因已随 rerank 超时修复消除**：07-03 的 top-10 是回退的
   triage 粗筛分序（好工程文永远排在 8 分论文后），不是权重问题；两条 7 分 harness 好文
   本来就进了 finalist-24。rerank 正常后它们按工程价值参与 listwise 竞争。次因=当天
   harness 源没新货（跳过已读 7 = 他已读过的最强 harness 内容，机制正常）。
2. **harness/工程源本就是最高权重**（对照 config/sources.yaml 实值：anthropic-engineering
   1.5、simonwillison 1.4、gh-claude-code 1.4、latent-space 1.3 > arxiv/hf 1.1）。再调高
   会打偏：有内容的日子过度顶上、没内容的日子照样帮不上。
3. **HF 重复 arXiv 的成本已被去重修复（5eccba3）消掉**；HF 剩下的是人工精选信号的独立
   价值 → 保留、不降权。
4. **唯一可拧的「top-10 里 ≥N 条工程源」多样性下限**：会在没好货的日子硬塞低质条目，
   是新功能不是修 bug —— 本轮不做。

**正确动作**：源结构待 2-3 天干净跑（rerank 生效 + 科普沉底 + 日期诚实的新格局）的 top-10
来源分布出来后再评估；很可能 rerank 修复后已自然改善。样本现状（4 天、其中 2 天在个性化
上线前）也不足以支撑结构性改动。

# ★ LLM 调用可靠性修复（2026-07-04 晚）：claude -p 偶发工具调用烧 turn → --tools "" 纯文本化（7bd71c8）

## 发现路径（豁免探针连中三次才露头的间歇性 bug）
跑 rerank.md 豁免验证探针时，sonnet listwise 调用偶发 exit 1 + **空 stderr**、非瞬态不重试 →
rerank 静默降级。查 claude CLI 会话日志（~/.claude/projects/<neutral-cwd>/*.jsonl）实锤：
`max_turns_reached, maxTurns=1, turnCount=2` —— 模型第一个 turn 没答题，而是调了
**ReportFindings**（code-review 工具，claude -p 默认工具集里的一员），tool_result 回来后
需要第二个 turn 才能给答案，撞上 `--max-turns 1` 被 CLI 判失败。排序类 prompt（"按 rubric
排序"）形状像 review 任务，诱发概率不低（当晚 3/5 次）。

## 为什么之前没暴露
- `--max-turns 1` 的本意就是「无工具循环的单次补全」（模块 docstring 原文），但它只挡了
  **循环**、没挡模型**尝试**调工具——一试就烧掉唯一 turn。
- 失败面目是 exit 1 + 空 stderr（真实报错在被丢弃的 stdout JSON 里），wrapper 判非瞬态
  不重试 → 与超时(240s)不同签名，之前的超时修复救不了它。

## 修法（一行 argv + 补测）
`claude_code._run` 加 `--tools ""`（CLI 官方语义：禁用全部内置工具）→ 工具通道机械性
不存在，模型只能文本作答。**附带收益 = P0 同旨**：封死「模型在管线调用里自行 Read 文件 /
浏览网页」的残余上下文污染通道（管线调用必须纯文本进出，个性化只走 USER.md→preamble）。
补 argv 断言测试（--tools "" + --max-turns 1 并存）。pytest 155 绿。

## 实证
- 修复前（07-04 晚探针 v1/v2）：5 次调用 3 次烧 turn 失败。
- 修复后（探针 v3，同晚同负载）：前 6 组 12+ 次 sonnet 调用**零工具烧 turn 失败**
  （v3 深夜最后两组失败是 sonnet 过载超时，签名不同=timeout，与本 bug 无关，次日晨补跑）。
- 该 bug 不修，任何一天的 daily 都可能随机丢个性化排序（降级横幅会亮，但排序已是粗筛序）。

# rerank.md「新 benchmark / 新测量方法」豁免落地（2026-07-04/05）—— A 收口的可选微调转正式改动

**为什么改**：豁免清单原文只点名「实证结果/反直觉发现/新失败模式/新机制/SOTA/一手数据」，
"首次让某能力或失败模式变得可度量"的 benchmark 只能靠间接覆盖——读者工作域正是评测框架+memory，
这类条目（07-03 [5] MemSyco / [6] EvoPolicyGym）是他要的主场前沿；[6] 在旧 prompt 重放里被
「偏综述基准」压到 #7 即语义漏洞的症状。**补命名、独立于 07-03 数据成立，不是在噪声上调参。**

**改了什么**（只动 `prompts/rerank.md` 三处）：① 豁免清单加「新 benchmark·新评测环境·新测量方法
（首次让某能力或失败模式变得可度量的那种）」② 紧跟分寸护栏（"又一个同类基准/换个数据集重测/
常规 leaderboard 刷分"不豁免）③ 「保」例补「首个能测 agent 长程记忆谄媚性的 benchmark」。
preamble（rerank.py）/ USER.md / 其余管线零改动。

**验证**（`scripts/probe_rerank_benchmark_exemption.py` 只读重放，8 arm 全数据 + 结论详见
`data/real-llm-runs/2026-07-04-rerank-benchmark-exemption-probe.md`）：
- [5] 个性化 arm 5/6 次 **#1**（旧 #3/#6）；[6] #2~#5（旧 #4/#7，旧 prompt 同池对照仍 #7）——
  两种中性输入序 × 2 重复稳定，不再被顺序摆布。
- **平庸孪生护栏测试**（合成 AgentMemBench-XL：与 [5] 同域同标签、"合并六基准+更大数据集重测+
  刷 leaderboard"）：新 prompt 两次 **#11 全场垫底**（低于科普），旧 prompt 对照 #10 →
  豁免没抬平庸 benchmark 一寸，护栏把"同类重测"压得更明确。
- 基线泄漏检查：个性化关时 [9] 仍 #1（已会降权未误触发=门控成立）、[6] #10（豁免未泄漏）；
  [5] 基线 #3 vs 旧基线 #8 诚实记录为例句轻微一般化，方向无害、幅度在单 run 方差内，观察。
- 科普 [8] 全部个性化 arm 恒 #10——主功能（已会科普降权）未被带偏。

# 2026-07-05 首次干净全量日跑 —— 五条验证清单结果 + launchd 无人值守被 macOS TCC 挡住（收尾轮 #3）

**跑**：run_id `20260705-111307-daily-s9cm`，19.7 分钟，errors=0，四渠道全投递（web 页 + 钉钉卡
10 行 + 本地 + macOS 通知）。28/28 源全活，106 候选，跳过已读 16，triage 106/106 全覆盖
（1 次 haiku 超时被重试救回、且失败尝试如实入 by_stage——观测修复按设计工作）。

**五条验证（全部有实锤）**：
1. ★公开页零身份泄漏 = **过**。泄漏扫描（雇主/业务域/姓名/求职语境/读者场景短语等 30+ 模式）
   对公开页 + 本地归档双扫：唯一命中「字节」为「逐字转发字节(bytes)」技术语义，人工判非泄漏。
   线上页 200 + noindex + 与本地渲染逐字节一致 + 站点根 404（不可枚举成立）。
2. 无降级横幅 = **过**。两产出物 0 处「本日排序降级」；by_stage.rerank 真实在场（1 call，
   305.5s < 480s，sonnet）——修复后第一次在真实日跑里跑通的个性化排序（07-03 此处三连超时）。
3. 排序/名额质量 = **过**。三条 2025 年老工程博客（overview/已知套路回顾）排 #8-10 沉底不深读；
   主场前沿上浮：[1] 反常识实证 #1、[2]「首次量化 agent 行为越界新失败模式」#2 进深读——
   **新豁免条款在生产首跑即按设计工作**；[6] AgenticDataBench why=「无独特测量增量」#6 ——
   **护栏在生产同场压住"又一个同类基准"**，没顶上去也没误杀。深读 6/6 成功、thin_skipped=0
   （4 条 arXiv grounding 全为 27-28K 智能截断完整源；6.5K 那条是 GitHub 页原文=页面即文章，
   非 arXiv 薄判范围）。
4. 日期标签 = **过**。🆕 7 条全部真实 ≤96h（07-02/03）；📚 3 条 Anthropic 旧文带真日期
   （2025-09/10/11）诚实落「首次收录」；头部计数 7/3 吻合。
5. 综合 = **管线内容全绿，可信任**；但无人值守载体没装成，见下。

**launchd 被 macOS TCC 挡住（探针实锤，别再踩）**：仓库在 `~/Desktop` 下，Desktop 是 TCC
保护目录——launchd agent 的纯上下文读不到（探针 plist 证实：`ls ~/` OK、`ls ~/Desktop`
Operation not permitted、读 run-serve.sh DENIED）。`launchctl load` 当场 spawn 的第一个实例
能跑是**继承了加载它的终端会话的 TCC 上下文**（假象），崩溃后 KeepAlive 重启即 126 循环；
明早 08:30 的 daily 同样必挂。已撤装两个 agent（比留着静默失败好）、serve 恢复 nohup 手动
常驻（已验证连上 Stream）。**修复选项（他选）**：
a) 系统设置 → 隐私与安全性 → 完全磁盘访问（或"文件与文件夹"）给 `/bin/bash` 授权——
   标准个人自动化做法，需要他 GUI 操作；授权后 `bash scripts/install-launchd.sh both` 即可。
b) 仓库迁出 `~/Desktop`（如 `~/agent-radar`）——干净但是一次迁移（.venv 内嵌绝对路径需重建、
   本地档案路径全变），另约时间做。
在此之前：daily 手动触发（本次流程）、serve nohup 常驻。**顺手修正**：.env 补了
HTTPS_PROXY/HTTP_PROXY（run-daily.sh 注释一直说该在这、实际从没加——launchd/干净环境下
fetch 需要它；交互跑不受影响，钉钉渠道 trust_env=False 自动剥离不冲突）。

# 2026-07-05 收官四件合一 —— 无人值守落地 + 尺子适配 V4 + E1 最小闭环 + 文档照实（含"尺子必须自动运转"结构性修正）

**为什么**：核心产品成型、地面干净后，对照初衷还开着四个缺口：①"每天自动"字面不成立（TCC 挡 launchd、serve nohup、daily 手动）；②eval 尺子造于 V4 之前、V4 忠实度基线不存在；③E1 空转（尺子建成后 data/eval 只有 1 天=没人消费）；④PHASES/README 失真。**用户批准计划时加了结构性修正：评估尺子必须自动运转，不是又造一个手动命令——"跑"和"送达"全自动，用户只拍板（本地文件=没人读的投递教训，再次成立）。**

**① 迁移（方案 b）**：`mv ~/Desktop/claude-code/agent-radar → ~/agent-radar`（同卷原子，stat 同设备号实证）。零丢失核对：find 清单 304 文件迁移前后 diff 为空；.env/config.toml/USER.md/CLAUDE.md/memory.db/feedback×2 共 7 个 md5 逐一相同。`.venv` 删旧重建（唯一内嵌旧路径的东西；源码 grep `/Users/kuzfu` 全空=脚本动态解析+plist `__REPO__` 占位当初设计的红利）。pytest 155 绿、status/validate 正常、`~/` 无 CLAUDE.md（P0 祖先链干净）。**残留提醒：其它终端/IDE 窗口需 cd 新路径；CC 会话建议从 ~/agent-radar 重开。**

**② launchd 三件套 + 两个真雷**：`install-launchd.sh all`（新默认；both=daily+serve 兼容保留）装 daily 08:30 / serve KeepAlive / review 周日 21:00，全部 bootout+bootstrap 干净重载。探针 plist 证纯上下文可读新仓库（read_rc=0/venv_rc=0）；`kickstart -k` 后由 launchd 亲拉的 serve 连上钉钉 Stream（log 里旧 Desktop 路径的 Operation not permitted 尸体和新实例的 wss endpoint 同框=前后对照实锤）。**雷 1（review 首跑抓到，救了次日 daily）：launchd 最小 PATH 没有 claude CLI（Homebrew cask 在 /opt/homebrew/bin）→ 所有 LLM stage FileNotFoundError**——run-daily.sh/run-review.sh 补 `export PATH="/opt/homebrew/bin:$HOME/.local/bin:$PATH"`。雷 2（历史）：`launchctl load` 首实例继承终端 TCC 的假象——本轮验证一律走 launchd 亲发起（kickstart/start）。

**③ daily→eval 自动链**：run-daily.sh 去掉 `exec` 尾行（exec 后追加的命令永远不执行——用户点名的坑），改为 daily 成功后跑 `radar --mode eval $(date +%F)`；eval 失败只 log 到 stderr、不碰已完成投递、不改脚本对 daily 的成败上报；撞额度靠 P1 的逐篇 checkpoint 次日自动续。选链式不选独立 plist：天然串行零竞态（daily 时长 6-40 分钟不定，独立 job 掐不准），且同一环境（.env/代理/unset ANTHROPIC_API_KEY）不用复制。

**④ eval 适配 V4（只动 eval 侧，deepread prompt 零改动）**：factual/commentary 口径 prompt 里本就有，风险在裁判划线。`eval_faithfulness.md` 补四轴边界：③轴祈使建议行=commentary（即使跟解释）、④轴裸章节指针=导航 commentary，**但任一轴内嵌的事实断言拆出照核**；判定纪律加防过纠（按断言内容判、不按轴一刀切，"漏抽会让幻觉躲进建议里"）。`report.py` 趋势表加 grounding 列 + "混合 grounding/跨格式改版不可连线"脚注。prompt_fp 机制让旧缓存自动作废、全量重判（设计红利）。**07-05 真跑抽查：7 处 issue 全为真捕获、零③轴误伤，一轮过、无需迭代**；"V4 砍数字→n_factual 变小分数抖"的担忧未成立（每篇仍 14-20 条 factual）。

**⑤ V4 忠实度基线（尺子第一次咬 V4）**：07-05 晨跑 **93%**（6/10 篇、sidecar×6、7 处标记：15 倍以偏概全/擅加 ABAC 类比/合并独立发现/"partly"改排他/把"排名保持稳定"说反等——全是真问题，正是尺子该抓的）；07-03 **95%**（6/10、sidecar×6、5 处标记：擅加"多跳"术语/自行编类型划分/外推不存在的对照/给 probes 编定义/2 份 YAML 说成 4 份）。排序：两天反馈 0 对如实报；独立裁判 τ=-0.467（07-05，重跑用缓存 0 重花补的）/ τ=0.2（07-03），〔诊断〕口径勿优化。06-26 旧基线（90%、full_text 近似、压缩件格式）与 V4 天不可连线——趋势表已带口径列。**晨跑基线快照存 `data/state/baseline-20260705-am/`**（同名文件会被晚跑链式 eval 覆盖）。

**⑥ E1 第一步（--mode review）**：radar/self_improve/review.py——确定性聚合（eval 趋势复用 trend_rows=per-day json 本就是跨天结构化存储，不另建 trends 文件；投票 vs MIN_PAIRS；各天 top-10 源分布；self_applicable 标注**第一次有消费者**；critic skip；WATCHLIST 盘点）+ 单次 LLM 草案（prompts/review.md：只草案/引用数字必须来自 JSON/≤5 条/不建议改 deepread 除非多天系统性下滑/零身份）+ top-line 摘要推钉钉 1v1（复用 OTO sampleMarkdown + trust_env=False；发送失败只 log；**推送前过 leak_scan 同口径自检，命中即降级为通用指针**）。每源独立容错（缺天/坏 JSON→该段如实"暂无数据"）；LLM 失败降级纯数据段；--dry-run 不调 LLM 不推送。**launchd 真跑两次**：第一次抓到 PATH 雷（见②）+数据段/推送全通；修后第二次 **LLM 草案生成 + 钉钉真实送达 + 泄漏自检 0 命中**。**reviewer 首跑即出真信号**：(a) target_component 标签漂移（同一篇 06-21 标 orchestration、07-03 标 llm_backend）；(b) 07-03 一组同名 AgenticSTS（48eb06c1/bbb1a60e）穿透去重且 critic 未标——草案只提 1 条且自带"n=1 先观察别为单次样本改逻辑"的克制。**摘要"草案 12 条"计数 bug 当场修**（启发式把观察/盘点的编号行全数了→改为只数草案节，补测）。WATCHLIST.md 播种 5 项（源分布/价值分层/截断数字/[5] 上浮/已推迟大项）。

**⑦ leak_scan**：radar/self_improve/leak_scan.py（可 import）+ scripts/leak_scan.py（CLI）。两层：内置通用词类（职业/雇佣语境；相邻字面量拼接写死，文件自身不含触发词）+ 本地词表 `data/self_improve/leak_terms.local.txt`（**gitignored——词表本身就是身份数据**；缺词表大声警告只跑内置类、绝不静默通过）。本轮全部提交物 0 命中过闸。**遗留待拍板：SPEC.md 两处既有内容命中本地词表（§技术选型/北极星段的已会清单枚举）——非本轮引入、已在公开仓库历史里，是否脱敏由用户定，本轮不擅动。**

**⑧ launchd daily 真跑终证**：`launchctl start com.agentradar.daily`（纯 launchd 上下文）→ run_id `20260705-174004-daily-skvo`，**370.9s（深读 checkpoint 复用，晨跑 19.7 分钟→晚跑 6.2 分钟）、28/28 源、97 候选、10 精选、6 深读、triage 覆盖 1.0、errors=[]、四渠道全 True**；阅读页同 URL 幂等重部署 HTTP 200（title=Agent Radar · 2026-07-05）。daily 完成后 eval 链自动点火（run-daily.sh 链）。**完整重启（登录自启）终验待用户下次重启后确认。**

**明确不做（全进 WATCHLIST 观察，别提前）**：E 会聊、E2 代码级、源权重、价值分层、截断策略。

**文档**：PHASES（P1①②③✅+自动运转口径、P2✅"P0 修复后首次干净成立"、P3 大体落地+"覆盖更广→C2 收紧"、P4=E1 落地；新增"投递与详解的演进"大白话小节）、README（三件套+TCC 前提+"尺子与周度自省"节+管线图/状态表/目录图照实）、SPEC §9 落地注+§12 现状、deploy/README 三 agent 表+TCC 血泪。本地 CLAUDE.md 代码地图+resume 点同步（不 push）。

# 2026-07-05 晚 review 推送人话化 + 周报上阅读页 —— 把「写给开发者的遥测」修成「写给用户的周报」

**为什么（用户当晚反馈）**：21:00 首次自动 review 送达成功（循环活了），但推送内容"可读性很差，我很难理解"——他说得对，三个病根全是老教训在 review 自己输出上的重演：①内部术语裸奔（代码常量/路线图黑话直接上手机——刚在详解上做完术语锚定、review 没适用同一纪律）；②数字无解释（"6/10 篇"哪 6 为何 10；"8 对"7 票怎么变 8 对，没人讲过同日赞×踩配对）；③"全文"指向本地路径=手机上一行死文字（投递教训原样重演：本地文件=没人读，详解修了九轮才上阅读页、review 掉回同一坑）。

**A. 摘要四段人话（build_summary 重写）**：🩺运行 / 🔍详解质量 / 🗳你的投票（该做什么）/ 📝待拍板，一屏读完、语气=同事周更。运行段有据可依：gather 新增第 6 块、从日报归档（digests/YYYY/MM/*.md）读「排序降级」横幅——"本周正常"是数据不是口头保证。质量段把 6/10 讲透（"另外 4 条只有一句话简介，无需核查"）。投票段用他自己的真实数据现场教配对数学（"2026-06-26：2 赞×4 踩＝8 次对比，凑满 10 次就开始生效——还差 2 次"）。**禁用词落成 tests/test_review.py 的 FORBIDDEN 断言清单**（MIN_PAIRS/sidecar/grounding/D 阶/可比天数/support_rate），摘要+周报模板+推送文本三处防回归；摘要任何分支不得出现 `data/` 路径（同样断言）。链接行由 run_review 拼接、summary 本体进周报页「一眼看完」节（页面不自引）。

**B. 周报上阅读页（新 radar/self_improve/publish.py）**：seg=HMAC-SHA256(AGENT_RADAR_WEB_SECRET, "review-"+date)[:32]——与日报页同 secret、同 CF Pages 项目、同隐私档位（不可枚举/noindex/data/web gitignored/站点根 404 实测），"review-"前缀把周报 seg 与同日日报 seg 命名空间隔开（单测断言不相等），date 派生⇒重跑幂等同 URL（实测两次部署同 URL）。渲染镜像 _web_render（复用 _inline_html+CSS；加表格样式、「一眼看完」卡片、内嵌 WATCHLIST 过 demote_headings 防双 h1）。**闸门顺序是命门：leak_scan 在写入 data/web/site/ 之前**——被标记内容绝不能落进部署目录，否则下一次 daily 部署会把它捎上线（单测：命中→不写盘不部署）。降级矩阵：leak 命中→不发链接、推送如实说一句；部署失败→推送照发只丢链接；未配置→一句"本地归档"。deploy 从 web_reader 抽成模块级 deploy_site() 两处共用（--branch main 的 404 教训只写一处）。推送末行 `[点开完整周报（网页版）](url)`（OTO sampleMarkdown 的 [t](u) 有 6-30 简报实测先例）。

**C. 术语纪律贯到底**：render_markdown 全人话化（表头「核对依据」、sidecar×6→深读原文×6、"对"→"同日两两对比 N 次"、τ 配"仅诊断非质量分"+「怎么读」段）；prompts/review.md 加规则 7（叙述禁用代码常量/字段名、数字带"它数的是什么"；**唯一例外＝草案「改什么」必须精确点名文件/配置键**——行动指令要可执行，与规则 3 不冲突）。

**真跑与草案超时插曲（全程诚实降级、按设计工作）**：新代码两次真跑（22:01/22:04）：页面部署 HTTP 200（title/表格/一眼看完卡片/noindex 就位）+ 推送 ✓ sent + 页面 leak 预检 0 命中（词表完整加载）——但 LLM 草案连续超时。追查：pong 6s ✓、350 字生成 31s ✓、草案调用 300s/480s/900s 三连超时零输出；**旧 prompt 对照实验同样 480s 超时→排除本轮规则 7 是诱因**；21:00 launchd 同形调用 300s 内成功过。定性=当晚长生成流不稳（环境/高峰），非本改动引入、非结构问题；draft timeout 300→480 保留（周任务 headroom 无害；900 无增益不再加码）。**降级路径因此获得一次真实生产演示**：推送照发、📝段如实"AI 观察稿没生成成功，不影响下周自动重试"、文件数据段完整。下周日 21:00 自动重试即补（或任一网络平稳时手动 `radar --mode review`）。

**验证**：pytest 167→181（新增：四段式/禁用词清单/无路径断言/页面渲染与 XSS 转义/leak 闸先于写盘/部署失败与未配置降级/推送含链接与三条降级路径集成）；提交物 leak_scan 0 命中；页面手机端排版待用户真机点开确认（美学终审照旧归他）。改动纯投递/呈现层：models.py/daily/deepread/rerank/选择层零改动，零自动应用契约不变（自动的仍只是跑与送达）。

**第二轮（同晚）：周报页从「数据存档」改「策展周报」——用户点开链接后反馈"不像人话、大段罗列论文题目、体验不好"。** 病根=页面照排了给机器/审计看的原始清单：§4 是 47 行英文论文题目墙、§6 是整段开发黑话的 WATCHLIST 原文、§3 是逐日源分布罗列。**新纪律（render 注释里写死）：页面只放计算过的人话结论，不罗列原始清单——原始数据在 data/ 各文件里一字不丢，喂 LLM 的 JSON 载荷不变。**逐节改法：§3→本周合计散文（"本周 3 期共精选 30 条：arXiv 12、Anthropic 10…"+「首次出现的源」一句）；§4→环节计数一段话（"24 条与雷达自身相关：记忆 8、评测 6、编排 5…"，component 名走 _COMPONENT_CN 人话映射，**零题目罗列**）；§6→只列 5 个观察项名（判据/出处留本地，AI 草稿周会逐项盘点）；§5 保留（本来就是"拦了什么"的有用短行）但标题摘录改 smart_truncate 词边界+省略号；§1 趋势表封顶最近 8 次+「怎么读」拆三个要点；各数据节统一按「本周=date 往前 7 天」过滤（_week_dates，防止清单随历史无限增长）。效果：报告 9.6K→2.3K chars（-76%）、手机约一屏半、每节都是结论不是 dump。**同 URL 幂等重部署、不重复推送**（摘要未变，他已收到的链接直接打开新版）。防回归：test_render_curated_no_raw_dumps（题目墙/清单原文/周窗过滤断言）+ 趋势封顶测试，pytest 183。

# V4→V5：详解从「入门点燃器」改「完整教学级深读」（2026-07-06，用户拍板的定位第三次演进）

**为什么改（前提变化，不是审美摇摆）**：V1 压缩件（完整但读不动）→ V4 点燃器（读得动但薄、
细节外包「点原文+AI 精读」）→ V5 完整教学级。多天真实使用证明**他不点原文**（英文读得慢），
详解就是他对这些论文的唯一阅读——「细节交给原文」的 V4 契约对他事实上不成立，详解必须自足。
他的两个怀疑全被代码实锤：① 抓了 80K 只喂 28K（GROUNDING_CAP，抓到的 65% 被扔，07-05 六篇
深读五篇自标「中段截断」的直接原因）；② top_k=6，每天 4 条只有一句话。

**改了什么**（5 commits：9542a0b 配置与名额 / 9e3b3cc V5 prompt / 29d2bb4 渲染器 /
9d2fc51 尺子适配 / b6aa54f 真跑热修；另 regen 脚本+文档）：
- deepread=opus 钉死、top_k 6→10（=daily_max，每篇都深读）；critic 只标注⚠️不再让位；
  薄源照深读+注入〔源材料提示〕确定性触发诚实简短模式。
- GROUNDING_CAP 28K→80K（≈全喂）；FETCH_CAP 80K→120K——**留余量让 smart truncation 而非
  抓取端盲头切决定去留**（实据：07-05 [3] 恰好顶满 80K 帽丢结尾；提额后抓到 102,140、
  砍参考文献尾节后 77,574 整篇全喂、连截断标记都不需要）。没有一路提到 120K 全喂：80K≈
  20–27K token/篇 ×10 篇已是单日最大额度项，再放大收益边际（多数论文正文<80K）。
- V5 prompt 七节结构（🎯洞察/📖背景/🔧机制逐个成节拆解/🧪实验全呈现/⚠️局限/💡应用/🔗原文
  改「供核对与引用」）。★灵魂红线=**完整绝不靠「堆」实现**（V1 之死在堆不在长）：教而非倒、
  裸列数字=违规、公式先大白话、每节教学自检。V4 护栏逐字保留（身份护栏/反幻觉/术语锚定/
  Why→How/深度自适应「快进≠砍节」）。
- 图文并茂取舍：**mermaid→构建时 mmdc 静态 SVG 内嵌**（本机探针通过：首跑 62s 含下载、
  后续秒级+内容哈希缓存幂等；--svgId 按图隔离 CSS、透明底+CSS 白底=暗色可读）；页面保持
  零 JS/零外部请求，**不走** mermaid.min.js 客户端兜底（探针过了就不留双路径）；**不让模型
  出裸 SVG**（失败面大、表格+mermaid 已覆盖结构与数字，防跑偏「不引重前端框架」）；数字
  呈现一律 markdown 表格（渲染器新支持，横滚+畸形行补齐不崩页）；坏图优雅降级代码块。
- 尺子只动 eval 侧：judge 看满 80K（_MAX_SOURCE_CHARS 28K 不改会把落在原文后 2/3 的真
  claim 全误判 unsupported）；表格/图数字=factual 照核；claim 15–30 条；趋势脚注标格式切换。

**真跑热修（全量重生成当场抓到的两雷，b6aa54f）**：[2] 一次 opus `exit 1`+空 stderr →
被判非瞬态零重试 → NO_TEXT **还被写进 checkpoint**=之后每次续跑永久复用失败。修：失败绝不
进 checkpoint（一次抖动≠当日永久空洞）+ 无诊断 `exit N:` 归为瞬态可重试（丢一篇详解比多试
一次贵；带真实诊断的照旧不重试）。修后 [2] 重试一发成功（17,104 chars）——确证是瞬态。

**验证（07-05 全量重生成，同 URL 幂等重部署）**：10/10 篇 V5 详解 3,636–17,104 chars
（两薄源 release 诚实短），12 张 mermaid 全部 mmdc 渲染成功零降级、28 张结果表；页面 541KB
自包含、10 锚点+目录（每篇标 字数·约X分钟，8–38min）+返回顶部、noindex、根 404；[N]→id
显示序断言通过（feedback/mark 映射不破）；leak_scan 归档 md+部署页 **0 命中**。per-item
遥测（新 trace 首战）：每篇 LLM 115–819s、均值 454s——[3] 819s 距 900s 超时仅 10% 余量
→ LLM_TIMEOUT 提到 1200s。V4 产物全量备份 `data/real-llm-runs/local/v5-regen-2026-07-05/`（gitignored——内含 digest/sidecar 全文，不入公共仓库）。

**忠实度终数（三轮，中途额度耗尽靠 checkpoint 零重复付费续齐）**：10/10 scored、**mean 94.9%**、
n_factual 合计 305（V4 基线 6 篇约 100）——主张量 3 倍、篇幅 5 倍下忠实度持平 V4 基线（93/95%）。
**尺子当天完成第一次「发现→修→重测」闭环**：薄源两篇首判 73%/60%，定性=背景知识补事实（真但
不在 grounding=违约）；根因=`_adequate` 只认 arXiv 薄源、release 短页没吃到确定性提示 →
THIN_NOTE_CHARS=2500（任何 <2.5K grounding 注提示）+ 提示与 system prompt 加「绝不用背景知识
补『它是什么/能干什么/定位』」硬语 → 手术重跑两篇（详解砍掉的正是垫的背景）→ 重判 **78%/100%**。
尺子另有两处教科书式真捕获：[1]「128k 爆炸」上色（unsupported）、[7] mermaid 图把「计算置信度」
画错步骤（distorted）——**「图表数字/结构=factual 照核」新规第一次真实咬合**。遗留观察进
WATCHLIST 候选：薄源上图更易越「只画原文结构」线（[4] 剩余 4 处全是解读性推断/图画推断结构）。
全量证据：`data/real-llm-runs/2026-07-06-v5-regen.md`。

**顺手排除一个假警报**：07-06 daily「耗时 8380s」实为机器睡眠（fetch monotonic 仅 205s，
墙钟跨 2h18m=合盖），selected=0 是周一早池小（候选 29 全低于阈值/已读）非故障；若 08:30
常合盖可考虑 pmset 定时唤醒，留他拍板。

# 时效性与爆点捕捉全链路收口（2026-07-06）—— 三洞齐修 + 逐源真值表 + 端到端追踪

**为什么**：用户把时效性立为硬要求（「07-06 跑的绝不能漏 07-05→06 的 agent/harness/Claude/OpenAI
爆点」，点名案例「最近的 claude tag」）。web Claude 审计实锤三洞：① arXiv cap=50 打满截尾；
② 停机超窗 = feed 上游窗滤后永久漏；③ 重大发布被 triage rubric 压死（model-release PR ≤5-6）。
本轮先查真相再动手（探针全实测），修三洞 + 把保证边界写成文档。

## 先查出的真相（比审计估计更严重，全部实测）

- **arXiv 截尾远超估计**：拿 n=200 重建 07-03 跑的 96h 窗 → 窗内匹配 **>200 条**（200 条探针
  自身都打满、最老只回到 06-30），旧 cap=50 当天截掉 **150+ 而非「第 51 条起」**；7 跑里 5 跑
  顶格 50（06-20/21/26/30、07-03 池计数重建）。周末枯水期窗内仅 ~40 → 截尾集中在工作日。
- **GitHub releases.atom 服务端硬编码只给 10 条**——我们的 limit=15 形同虚设。折天数：
  claude-code 10 条=9.3 天（尚可）；**cline 10 条=9 小时**（sdk/* 碎 tag 洪泛）——atom 路径连
  两次日跑之间的 24h 都保证不了。
- **洞③有真实受害者，不是假设**：`Introducing Claude Tag` 连续 **5 跑**在候选池（06-26 起）、
  `Introducing Claude Sonnet 5` 连续 **3 跑**（07-03 起）、`Redeploying Fable 5`/`Claude
  Science` 同样——**全部从未投递**（seen.json/所有 items.json 零命中）。用户问的「最近的
  claude tag」十有八九指 Claude Tag 产品发布——它一直在池里、一直被分数压死。这也证明
  seen-based 捕捉在按设计工作（没上桌就每天回池），死的是分数端。
- **07-06 早 arXiv 三连 read-timeout（30s）**：单源故障的真实样本，B2 的 per-source 补课
  正好治它（当天 96h leash 本身也兜住了）。
- 顺手发现：langchain-changelog 探针返回 0 条（记入 SOURCE_GUARANTEES，validate 跟踪）。

## 修了什么（B1/B1b/B2/B3 + 一个下游帽）

1. **B1 arXiv 分页防截尾**（`radar/sources/arxiv.py` + `sources.yaml`）：单请求 cap=50 →
   **窗口感知分页**（页 200、页间 3s 礼貌延迟、某页最老条目越过窗口边界即早停、跨页硬顶 600、
   超时 30→60s）。**keywords/categories 一个字未动**（A1 收紧零回退——提网眼内容量≠放大网眼）。
   为什么 600 不是任务书说的 150-200：探针证明 200 都会在工作日 96h 窗打满；600 = 实测工作日
   峰值(~250) × B2 十四天补课余量，早停保证平日仍只发 1 个请求（真跑实测 1 页、40 条、不饱和）。
2. **B1b GitHub REST 优先**（`radar/sources/github_releases.py`）：REST `/releases?per_page=30`
   优先（无鉴权 60 req/h，我们 10 源/天用不到零头）、atom 兜底（限流/故障照活）。真跑 claude-code
   REST 深度 30 条=29.1 天（atom 10 条=9.3 天）。残余诚实边界：atom 兜底期间高频仓可能漏碎 tag。
3. **B2 停机补课窗**（`radar/stages/fetch.py` + `config.py`）：`data/state/fetch_state.json`
   持久化 **per-source** 上次成功 fetch 时间戳；有效窗口 = max(配置窗, gap+12h 余量)、14 天封顶；
   失败源不刷戳 → 下跑自动为它放大。**比任务书的单一全局时间戳强一档**：今天 arXiv 单源超时
   这种局部故障也补，不只整机停机。真跑双臂验证：正常连跑（gap 24h）**零膨胀**、模拟 3 天停机
   全 48h 源放大到 84h 并**捞回 v2.1.201 本尊（58.8h 老、已出配置窗）**+ 周末 simonwillison/
   latent-space/deepmind/claude-code v2.1.199-201 等；96h leash 源（arxiv/hf）84h<96h 零误伤。
   已播种真实 fetch_state.json（27 源=今晨成功时刻；arxiv-agents=07-05 它上次成功，诚实）。
4. **B3 重大发布豁免**（`prompts/triage.md`）：豁免（核心厂商新模型家族/旗舰代际/重大能力/
   协议·标准变更 → 8-10 即使细节薄）+ 单向护栏（补丁 vX.Y.Z/nightly/alpha·beta/依赖升级/例行
   release notes → 照旧 0-4，不因核心厂商抬分）+ 保/压例句（Opus 4.6→8+、MCP 规范新版→8+；
   v2.1.201→≤4、nightly→≤2）。措辞复刻 rerank.md benchmark 豁免的成熟模式（豁免+括号分寸注+
   例句对）。5-6 档同步改「notable **(non-major)** model release」消除自相矛盾。
   **B3b 证据端补齐（第一轮重放逼出来的）**：第一轮重放豁免对**带摘要的**构造反事实完美咬合
   （Opus 4.6 旧 [5,5]→新 [9,9,9]）、护栏完美（v2.1.201 [5,5]→[2,1,2]、nightly/sdk 纹丝不动、
   论文 Δ中位 0.0），**但真实受害者没救回来**：Introducing Claude Sonnet 5 新 rubric 下 [4,8,1]
   大方差、Claude Tag [1,2,1] 纹丝不动。根因不是豁免措辞——是 **html 源卡片无 blurb、这些条目
   summary 为空**，haiku 只有光杆标题可判（Claude Tag 查实 = Slack 内 @Claude 委派任务的团队级
   agent 能力发布、Anthropic 自称内部 65% 产品代码经它产出——正中用户靶心的重大能力，但标题上
   人类也判不出）。修两头：① `prompts/triage.md` 光杆标题规则（标题自明的新模型代际→照豁免；
   陌生产品名→**不许靠品牌猜高分**，按给定证据判——保持单向）；② `radar/sources/html.py`
   **enrich_summary**（opt-in，anthropic-news/engineering 开启）：空 summary 用文章页
   og:description 补，磁盘缓存（`data/state/html_summaries.json`，空结果不缓存以便重试）、
   每跑封顶 12 次抓取、失败留空绝不编——稳态零额外请求。
   **B3c 新一方产品地板（第二轮重放逼出）**：证据补齐后 Sonnet 5 修稳 [8,9,8]，但 Claude Tag
   仍 [2,1,1]——查实其 og:description 是纯营销空话（"a new way for teams to work with
   Claude"，零技术信号），haiku 在该证据下压低**是 rubric 的正确行为**（不许靠品牌猜）。问题
   变成：厂商简介空话时，「新命名一方产品存在」本身就是 agent 工程师的当天必知信号。修 =
   豁免加中间档：**核心厂商 Introducing 的新命名一方产品/agent 界面 → 地板 6-7**（够过质量门
   上桌，一行 digest 由他自己决定点不点），护栏三连：地区可用性/上架某云/定价套餐/办公室/合作
   宣传照旧 0-4；地板不抬 8+（8-10 仍只属于模型代际/重大能力/协议变更）；例句保 Claude Tag→6-7、
   压 Seoul office→0-1。
5. **triage_pool_cap 200→400**（`config.py`）：B1 后工作日池可到 ~300，旧 200 的 recency 裁剪
   会静默把 B1 在下游抵消掉（被裁的老论文次日即出窗=永久没被打过分）。风险注记：单次 haiku 批
   到 ~350 条时输出 ~55K token，接近上限；已有 salvage+覆盖率兜底护栏，若真跑出现覆盖率告警，
   下一步是 triage 分块（本轮不做）。
6. **可观测**：fetch 日志行加 per_source 计数（本轮审计只能靠池文件重建饱和史的教训）+
   catch-up 放大时单独 log + `ctx.stats["catchup"]`。

## B3 重放验证（同池同序，旧 rubric×2 / 新 rubric×3，haiku）

三轮迭代收敛（每轮同池同序 36 条=gh releases 17 + labs 9 + 论文样本 10 + 构造反事实 1；haiku）：

- **v1（只有豁免+护栏）**：合成 Opus 4.6 [5,5]→**[9,9,9]** 完美、v2.1.201 [5,5]→[2,1,2] 完美、
  nightly/sdk 纹丝不动、论文 Δ中位 0.0——**但真实 Sonnet 5 [4,8,1] 大方差、Claude Tag [1,2,1] 不动**
  → 暴露证据缺口（html 光杆标题）→ 加 B3b enrichment + 光杆标题规则。
- **v2（+B3b）**：Sonnet 5 修稳 **[8,9,8]**；Claude Tag 仍 [2,1,1]——og:description 查实是纯营销
  空话，haiku 压低是正确行为 → 加 B3c 新一方产品地板。
- **v3（+B3c，终版 rubric）**：**五项全 PASS**——

| 类别 | 条目 | 旧 rubric（v2 基线） | 终版 rubric |
|---|---|---|---|
| 重大 | Introducing Claude Sonnet 5（真实） | [4.0, 2.0] | **[8, 9, 9]** |
| 重大 | Introducing Claude Opus 4.6（构造） | [5.0, 3.0] | **[9, 9, 9]** |
| 新产品地板 | Introducing Claude Tag（真实受害者） | [0.0, 1.0] | **[6, 7, 6]**（过 6 分质量门=能上桌） |
| 补丁 | v2.1.201（用户点名） | [3.0, 5.0] | [2, 3, 2] |
| 补丁 | v2.1.200 | [2.0, 2.0] | [2, 3, 2] |
| 碎 tag | sdk/* ×9 · nightly ×2 · alpha ×1 | 全 0–1 | 全 0–1（纹丝不动） |
| 观察 | CLI v3.0.37（cline minor） | [5.0, 6.0] | [5, 5, 5]（合理中档） |
| 观察 | Redeploying Fable 5（ops 通告） | [0.0, 0.0] | [1, 1, 2]（正确不抬） |
| 论文×10 | —— | —— | Δ中位 0.0（变化只来自豁免条款） |

漏斗推演（07-05 当天数据）：Sonnet 5 得 8-9 → gate 排序键 ~8.4-9.4 稳进 24 finalist；Claude Tag
6-7 → 键 6.4-7.4，当天 cap 边界在 ~6.0-6.5（31 项抢 24 席）→ 也进 finalist；之后 rerank（未动）
按工程价值/对他新排 top-10。四条重大发布至今仍在池中——**下一个真跑就是自然验收**。
证据 JSON：`data/real-llm-runs/local/triage-exemption-replay-2026-07-05*.json`（gitignored）。

## PART D：v2.1.201 端到端 trace（可复述版）

发布 2026-07-03T23:50:35Z（北京 07-04 07:50）→ 07-03 跑（21:57）在发布**前**、07-04 无跑
（launchd 07-05 才装）→ **07-05 两跑均进池 ✓**（97 候选之一，triage 全池覆盖=必被打分；当天
quality gate 97→24、below_threshold=66、cap 再砍 7），未进 finalist/top-10 → 未投递 → 07-06
跑时 48.9h 龄**恰好滑出 48h 窗**（差 52 分钟）。结论：**覆盖 ✓、被当例行补丁压低=分寸正常**
（其 summary「Sonnet 5 会话不再用 mid-conversation system role 发 harness 提醒」有轻度 harness
相关性，旧 rubric 下 4-6 边缘属合理）；新 rubric 下重放稳定 ≤4=护栏工作。反事实：假如 07-05
两跑那天停机，B2 补课窗已实测能在 07-06+ 把它捞回。而**真正的洞是 Claude Tag/Sonnet 5**（见上）
——B3 修的就是它们；四条重大发布今天仍在池里，明早首个真跑就是 B3 的自然验收。

## 不做/边界（有意）

- rerank 的 B/C 逻辑零改动；deepread/synthesize/deliver/models.py 零改动。
- PART C 增补只提 1 个（MCP 规范仓库 releases，github_releases 适配器零代码、8 个 release 全是
  规范版本、2026-07-28 新版 RC 已挂）待用户拍板；查过不建议：OpenAI platform changelog 与
  Anthropic docs release-notes（重 JS 页、html 适配器形态不合、与 openai-news/gh-claude-code
  冗余）、blog.google 两 feed（活着但月度汇总+PR 稿多，Gemini 旗舰已有 deepmind-blog+HN+
  gemini-cli 三重冗余）。X/Twitter 维持有意不覆盖。
- hf_papers 服务端 ~50 条深度不修：arXiv id 跨源去重使其本质是 arxiv 的策展视图，补课走 arxiv。

---

# 2026-07-07：断供根因（睡眠×fetch）三件套修复 + triage 分块 + Web 情报台（主页/归档/统计/网页投票）

## PART 0：07-07 只投 1 条 —— 根因与修复（全部日志实锤，非推测）

**时间线（radar.log + trace 交叉核对）**：launchd 08:44 DarkWake 触发（08:30 Mac 在睡）→
fetch 阶段墙钟 2h00m / 单调时钟只走 4.1min（**睡眠切片的铁证**：monotonic 在 macOS 睡眠时冻结）
→ 失败按 09:53 / 10:13 / 10:30 / 10:41 四个暗醒窗成簇（每簇 RemoteDisconnected ×3 快重试 ≈2.7s/源
=烧进死代理）→ 10:44 真醒后网络恢复，**源表排在末尾的 9 个源全部成功**（谁活谁死由 sources.yaml
顺序决定=纯时机事故）；arxiv-agents 独立 503×3（连挂第 3 天：07-06 读超时、07-07 503）。
逐阶段计数：**fetch 13 候选（skipped_seen=0）→ triage 13 → 质量门 1（below_threshold=12）→
rerank 1 → deepread 1（opus 258s 成功）→ 投递 1**。⚠️**头号嫌疑「opus 额度墙」被证伪**：当日
opus 仅 1 次调用且成功；选择层/质量门行为全部正常——是输入饿死，不是管线坏。
**07-06 其实更糟**：27/28 源活但 arxiv 挂 → 29 候选全部 below_threshold → **当天 0 投递**
（dingtalk_card=false），用户没收到任何推送。降级横幅本身工作正常（digest 头部如实写了 9/28）。

**修复三件套（每件针对一个确证环节）**：
1. **fetch salvage 重试**（`fetch.py`，`SALVAGE_DELAY_S=20` 模块常量）：抓完后对失败源整体再试
   一轮。当天场景下 fetch 结束时网络已恢复 → 一轮 salvage 能把 18 个「死在错误时刻」的源全部
   捞回；两轮都死才保持 -1（B2 明日照常放大窗口）。**为什么不加大 _base 每源重试**：3×快重试
   烧死代理的根因是「时机」不是「次数」，加大只会拖慢所有正常失败。
2. **run-daily.sh 网络就绪门**：起跑先经环境代理探 gstatic/generate_204，40×30s——`sleep` 只在
   醒着时走表 → 天然跨睡眠周期、每个醒来窗都会重探；探不通也放行（salvage/B2/横幅诚实兜底）。
   **为什么探 gstatic 不探 apple**：captive.apple.com 国内直连可达，会在「代理死、直连活」时
   假阳性——探针必须走 fetch 同一条路径。
3. **caffeinate -is 包住 daily+eval**：起跑后不再被 idle sleep 切成暗醒碎片（07-07 fetch 被切
   4 段就是没有它）。合盖睡眠软件层挡不住 → 配套用户操作项：`sudo pmset repeat wakeorpoweron
   MTWRFSU 08:25:00`（08:25 全醒，网络就绪迎 08:30；需 sudo，留他执行）。

**triage 分块**（当天补跑立刻暴露的下一颗雷）：B1 放开截尾后首个 200+ 池（219 条）让单发 haiku
要吐 219 个 JSON 对象 → 三连超时 → 全池降级权重启发式（07-06 决策里预留的「若真跑出现覆盖率
告警，下一步是 triage 分块」当天兑现）。修 = `CHUNK_SIZE=80` 全局索引分块：单块失败只对该片
启发式（coverage 如实入账），全部块失败才整池降级。**当日补跑（triage 已降级那次）选择放行不
重跑**：rerank(sonnet)/critic/deepread(opus) 全真，先把完整 digest 拿回来；分块修复走明早 08:30
自然验收。补跑另证：**B1+B2 首次真实生效**（arxiv 窗口感知分页一口气 184 条、28/28 全活）。

**当日恢复操作**：备份早间产物 → `seen.json` 摘掉已投的 Vera（deepread checkpoint 复用=零 opus
重付）让终版 digest 是「早间 1 条的超集」而不是丢掉他已读那篇 → `DINGTALK_OUTTRACK_NONCE=r2`
免同 outTrackId 卡片去重 → run-daily.sh 全链补跑（含自动 eval）。

## Web 情报台（PART 1-5）：关键选择与理由

- **设计系统单文件 `_design.py`**：token/字阶/chrome（主页·归档·统计导航+footer）一处定义，
  日报/主页/归档/统计/周报五类页共用 → 「一个产品」而不是五张散页。方向=他拍板的克制高级
  （Linear/Stripe 文档系）：发丝线代替阴影、单强调色（scope 蓝）+数据青绿、唯一动效=header
  雷达扫描小标（prefers-reduced-motion 静止）、mono readout 行把真实遥测当身份元素。
- **字体降级优先**：只异步加载 Inter（print→all swap，断网/被墙零阻塞零跳版），**中文不加载
  webfont**——他设备上 PingFang SC 本就是最优 CJK 面，思源多 MB 下载在无代理手机上纯负担。
- **投票走 Pages `_worker.js` 同源路由而非独立 Worker**：一次决策消掉三个坑——CORS、workers.dev
  与 pages.dev 在墙内可达性差异、独立部署漂移；`/vote`+`/votes` 与静态页同域同部署。
  **鉴权**：页面 seg 本身就是能力令牌（worker 持 WEB_SECRET 重算 HMAC(date) 拒无效——CF 本就
  托管这些页面内容，secret 上 worker 不扩大实质暴露面）；读端 `/votes` 用独立派生
  HMAC(secret,"vote-read")[:32] 做 bearer（永不出现在任何页面里）。**跨语言自证**：node
  WebCrypto 与 python 派生 seg/bearer 逐字节一致。写入端 serve 轮询 60s → `record_feedback`
  （与 mark 逐键一致=测试锁死）；游标落盘断点续传；run-serve.sh 在剥代理前把 HTTPS_PROXY 存成
  AGENT_RADAR_WEB_PROXY 专供轮询（Stream 域内直连与 pages.dev 走代理并存）。
  **现状 gate**：CF token 只有 Pages 权限（实测 KV create 401、Pages secret ✓ 已设 WEB_SECRET）
  → 差一次性授权（token 加 Workers KV Storage:Edit 后跑 scripts/setup_vote_backend.sh，或
  dashboard 手点建 namespace+绑 VOTES）；在此之前页面投票钮不渲染（vote_api 未配置），钉钉卡
  投票照常=双通道退化安全。
- **归档/主页/统计全部构建时静态聚合**（items.json/eval/feedback/state），幂等重跑同 URL；
  统计页只出聚合数（调色板过 dataviz 六检 light+dark、类别→色槽固定映射、2px 分隔、图例可见
  计数补足低对比槽；空态给行动指引不给坏图）。
- **leak 闸前置到每页写盘之前**（同周报纪律）：命中=只跳过该页并留声。**首跑即咬合**：06-30
  旧详解（隐私修复前产物）10 处命中（含 3 个本地身份词）→ 新设计**拒绝重渲染**；线上旧版
  06-30 页仍在（他当天亲验过内容定为低敏）——**下线与否留他拍板**，归档页该天条目正常列出。
- **站点根 `/` 维持 404**（无 index）；全部页面 noindex；`data/web/` 不入库。

## 不做/边界（有意）

- 不做实时后端/交互式 BI/登录/聊天后端/重 SPA/外部 CDN 依赖（字体为唯一优雅降级增强）。
- V5 深读 prompt/深度/选择层/models.py 契约零改动；投票双通道保留（钉钉卡不动）。
- 统计页图表零 JS（原生 <title> 提示即够）——个人静态仪表盘不值得为悬浮层引入脚本面。

## 07-08「只推 9 篇」根因 = arxiv 429 饿池 + HTTP 退避打错方向

- **现象**：07-08 daily 只推 9 篇（非常态 10）。**定位链（全实锤）**：`quality gate before=76
  after=9`——只有 9/76 过 6.0 硬地板（67 条 below_threshold）；池只有 76（常态 130-220）因
  `arxiv-agents` 429 归 0（昨天 184、今天 -1）；9 篇里 **6 篇 HF papers**（多样性配额 3→6 放宽
  填补，正因 arxiv 这个论文对手缺席）。**结论：9 篇是诚实产出、非漏斗 bug**——`daily_max=10`
  是上限不是配额，薄池就少推（宁缺毋滥）；漏斗如实运走了每个过 6.0 的项。
- **真正可修的弱点**：`_base._get` 对 429 用 0.8s/1.6s 线性退避 = 朝着「你太快了」的服务器
  猛敲、2.4s 放弃；salvage 20s 后同样撞墙。这是 arxiv 连续第 3 个坏天（前两天 503）；反复
  快敲还会延长封禁。
- **修**：`_base._get` 分流退避——`429/500/502/503/504` 认作「限流/瞬态」，**honor
  `Retry-After`**（delta-秒 + HTTP-date 两式解析、封顶 120s），无头则升级退避 **5/15/30s**；
  **传输错误（死代理 `ConnectionError`，`response=None`）保持 0.8/1.6s 快退**——网络真死仍
  快速失败、不拖慢（护住 CLAUDE 记的「network dead 快 fail」路径）。tests/test_source_backoff.py
  6 测锁定（429 恢复/Retry-After/耗尽/503/传输快退/解析）。
- **刻意不做（错误的「修法」）**：不降 `relevance_threshold=6.0`、不 pad 到 10——都违反
  宁缺毋滥、会推次阈值项冒充满额。
- **安全网**：漏的论文不丢——`arxiv last_success=07-07`，B2 补课窗（`catchup_max=336h=14d`）
  下次 arxiv 成功抓时回填缺口。
- **诚实边界**：短/间歇 429 现在能在跑内自愈；若 arxiv 硬封代理 IP 数小时，本跑仍可能薄池、
  靠 B2 回填——**不保证每天满 10**（那取决于当天有多少真过 6.0 的料）。
