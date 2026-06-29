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
