# Agent Radar 全面自审（两层）— 2026-07-07

> **性质**：Claude Code 独立产出的开放式自审。第一层 = 实现 vs 文档承诺（猎问题 + 方案）；第二层 = 预期本身对不对（质疑根本决策）。**本轮零代码改动**——只审、只设计方案、给路线图。
> **基线**：commit `6103c7d`（as-of 2026-07-08 晨，迁移验收周）。命名沿用发起日 07-07；**证据含 07-08 迁移日运行数据**（三版本、部署超时、pmset 缺失均为核心实锤），故数据实际截止 = 07-08 晨，今晚验收跑只补入"附录·验收观察"。
> **方法**：3 路代码/文档/运维只读探索 + 4 域并行执行层审计（正确性/闭环/隐私/运维）+ 1 轮方法论红队 + 主线 ~30 个只读探针 + 8 题业界联网调研。所有 file:line 主线抽验 10% 逐字核对；每条标【实证】/【推断】/【无法验证】；方案标【调研到（带源）】/【推断】。
> **隐私**：本报告过 `leak_scan` 零命中方提交。涉及泄漏内容只写"N 处/类别"、绝不回显；未修复弱点的可利用细节（部署标识、端点、注入手法）不进本公开文件，完整版落 gitignored 目录。

---

## 一、执行判词（BLUF）

**它还活着吗？** 勉强——**"每天自动跑"这个前提当前悬空**：迁移到新机后旧机唯一的睡眠唤醒防线没重建、机器就在电池上、17:30 触发撞睡眠即断供（07-06/07-07 已连续断过），且**断供无外部告警、无人知晓**。管线本身工程扎实（255 测试绿、六边形整洁、V5 详解忠实度 93-95%），但可靠性地基是纸糊的。

**它可信吗？** 部分——**三处"自我评估失真"侵蚀可信**：① 周报每周逐字向用户承诺"再投几票排序就开始学你口味"，而这条线**代码里根本没接**（反馈从不进 rerank）；② 断供 0 投递日被健康读数报成"全部正常出刊"、还被 LLM 草案放大成"连续 9 天无掉线"；③ 北极星要的"选得准"**没有任何尺子**，唯一在测的忠实度已饱和到不再产生信息。系统在"精确讲解"上可信，在"选得准 + 越用越准"上是**自我感觉良好的空转**。

**它安全吗？** 有活暴露——公开仓的 git 出站**无任何强制闸**，已下线的敏感页在多个 CF 历史部署 URL **仍公网可访问**，泄漏词表**漏收了关键身份词**且缺失即"放行发布"。

**最紧迫 top 3**：① 睡眠唤醒 + 外部 dead-man 告警（否则"每天"不成立）；② 隐私四洞（git 闸 + 历史部署下线 + 词表补全 + fail-closed）；③ RunLock 夺锁并发双投 + 部分投递永久漏报（两个数据正确性 🔴）。

**方向层一句话**：三个根本决策今天该推翻——**固定 10 篇 opus 全量深读**（额度/阅读/测量三重不可持续）、**faithfulness 每日全量当主尺**（测了不重要的、没测最重要的）、**opus 独占无护栏**（最贵 stage 耦合最不透明限流却零保护）。详见第二部分。

---

## 二、方向层判决（预期本身对不对）★

> 用户明示"第二层价值高于第一层"。这里质疑的不是实现、是**当初的设定本身**。敢下判断，不罗列"见仁见智"。

### 2.1 三个现在会推翻的根本决策

#### 推翻①：固定 10 篇 opus 全量深读 → 自适应分层深读

- **当初为何这么定**：V4→V5 转向时，真实使用证明"用户不点原文、详解=唯一阅读"，于是把深读从 top_k=6 提到 10、grounding 28K→80K，让"每篇都深读到自足"。这个洞察是对的。
- **今天为何该变**：洞察被**过度泛化**——"每篇被读时要自足"被扩成"每篇都要 opus 全量深读"。三路证据会合：
  - **额度**【调研+运行实证】：单日 daily ≈442K token（opus deepread 90K 输出 + cache_creation 232K），叠加自动链的 eval（~190K）；同日多版本天（如迁移日 3×daily+3×eval）单历日 output 270K+。而 2026 起 Claude 订阅额度是**跨所有模型/平台共享的单一周桶**、Opus 又是最紧的一档（来源：github.com/anthropics/claude-code/issues/17084；morphllm/truefoundry 的 2026 限额解析）。opus×10 是单日最大烧量项，decisions 自记"曾一度打空额度窗口"。
  - **消费端**【实证缺失】：网页阅读站**没有任何阅读遥测**，零证据证明第 4–10 篇被读过；10 篇 × 3.6–17K 字对一个慢热型读者 ≈ 每天 1 小时+。
  - **测量端**：eval 又对这 10 篇各做 80K grounding 复判，测量成本≈生产成本，而它测的忠实度已饱和（见推翻②）。
- **更好的替代**【调研到 + 推断】：**动态篇数分层**——由 critic verdict + rerank 梯度决定"今天几篇够格 opus 深读"（如真前沿 3–5 篇深读 + 其余一句话洞察卡 + 网页"展开深读"按需触发单篇 opus）。业界最像的同类物 **smol.ai/news（swyx，~80K 订阅，"99% 由可定制 research agent 生成"）就是分层摘要而非固定 N 全深读**（来源：news.smol.ai）。**关键：这不是砍深度**（被读的那篇仍全量自足），是**砍盲目全量**——slow day 不硬凑 10 篇 opus。
- **迁移代价**：小。`deepread_top_k` 已是配置；改成"按 critic/rerank 动态定 N" + 加"按需深潜"入口。真正的风险是**用户感知**（"少了篇数=缩水"），需用"省下的额度换更稳的无人值守 + 随时可深潜"说服，并给他看额度数据。**这一个推翻同时缓解推翻②③和多条执行层 🔴（额度、L11 白烧、eval 成本）。**

#### 推翻②：faithfulness 每日全量当主尺 → 抽样 faithfulness + 新建"选得准"尺

- **当初为何**：P1 第一把尺子，faithfulness（详解拆 claim 逐条核原文、代码算 support_rate）直接度量"详解有没有瞎编"，daily 后自动跑接 E1 闭环。这把尺子本身做得很扎实（抗 leniency、covereage 必报、跨语言判中文详解 vs 英文原文）。
- **今天为何该变**：**测了不重要的、没测最重要的**。
  - faithfulness 已饱和 93–95%（方差≈0 = 尺子不再产生新信息），却每天烧 opus/sonnet 复判 10 篇 × 80K。
  - 而北极星"选得准 = 重要性 × 对他新"**从来没有尺子**——`ranking.py:20` 代码自己承认"τ judges the ORDER not whether the right items were SELECTED (selection has no ground truth)"；排序 eval 靠反馈（9 票、MIN_PAIRS=10 从未达标 = 空转）+ τ（明标"诊断非正确性分"）。
- **更好的替代**【调研到 + 推断】：
  - **faithfulness 降本**：换 MiniCheck 式小模型检查器（770M 参数达 GPT-4 级 fact-check、成本 1/400，EMNLP 2024，arxiv 2404.10774）或**降为每周抽样**。保留（不废除）——07-06 薄源事件里 faithfulness 真驱动过一次"发现→修→重测"，它是**饱和尺不是废尺**。但跨语言（中文详解 vs 英文原文）需实测 MiniCheck 适配性。
  - **新建"选得准"尺**【调研到】：次日对照公开聚合器 top 榜（smol.ai 归档 / HN Algolia 按日期）**机械化反查"该选没选的 miss"** = reference-free 的选题质量尺，不依赖稀疏反馈。这正是把"漏检遗憾"变成可量化信号。
- **迁移代价**：中。MiniCheck 跨语言适配需实测；"选得准"尺是新模块（读次日社区信号 diff candidates/digests）。但省下的每日 opus 复判额度可覆盖新尺成本。

#### 推翻③：opus 独占、无额度护栏 → 保留订阅但加硬护栏

- **当初为何**：硬约束"绝不按量计费 API、只走 claude -p 订阅"。约束本身对（不烧钱、用户明确不要按量）。
- **今天为何该变**：推翻的不是订阅，是**"独占且零护栏"这个从没被质疑的默认**。【调研+实证】weekly 单桶跨模型共享 + Opus 最紧 + 最贵的 deepread stage **连续失败无熔断**（实测 07-08 曾 20 连发大 payload 零字节挂起、白烧 40 分钟；最坏可达 ~4 小时空转），而 `token_budget_per_run=200K` 只软告警、**判据还漏算了占大头的 cache_creation（232K）**→ 形同虚设。最贵 stage 耦合最不透明限流却零保护。
- **更好的替代**【推断 + 调研】：**订阅内加护栏**——① 动态 N（=推翻①，天然削峰）；② token 预算计入 cache_creation 并升级为**硬闸**（超限停后续 opus + 标 degraded）；③ deepread **连续 K 次全灭即熔断本 stage**（直接修 40 分钟白烧）；④ ccusage（OSS，读 ~/.claude JSONL）监控周桶 + 错峰；⑤〔**存量待他拍板**〕仅 deepread 留一个"有界 API 兜底"逃生门（硬预算封顶、默认关）作断供保险——**这条触及硬约束边界，必须他明确授权**。
- **迁移代价**：小–中，全是加护栏不动架构；逃生门那条需他拍板。

### 2.2 五支架逐条（浓缩）

- **① 今天从零还会这么选吗**：每天 10 篇全量深读 ❌（见推翻①）；精确=重要性×对他新 ⚠️（漏了**时机/可执行性**——近期有一个一次性的高强度学习窗口临近，系统的"已会降权"却在优化相反目标；漏了**学习连续性/ZPD**——慢热型要的是"在他前沿边缘"不只是"他没见过"）；自托管本地机 ✅（订阅 CLI 锁死有登录态的机器）但当初没把"无人值守可靠性"当一等公民=失误；公开仓 ❌（为"网页版 Claude 拉仓深审"选 public，代价是整类历史泄漏风险，若私有集成可用则是纯沉没默认）。
- **② 沉没成本陷阱**：docx 渠道（web 启用即永久休眠的死重量）、停用的群 webhook 代码、eval 每日全量 10 篇（饱和后仍付的 opus 税）、双投票通道并存的复杂度（而总票=9）、两份 config.example.toml、Item.links/snippet 死字段、recall 幽灵 stage——一路"建了就留"。
- **③ 没被质疑的默认**：每天推送（vs 按需/按事件/按额度——连续断供证明"每天"本就脆弱）；固定 10 篇（vs 动态）；被动阅读（vs 主动对话入口——北极星八词有"能对话"却从未接线，且无遥测验证"他会读完"）；faithfulness 当主尺（默认"忠实=质量"，但北极星是"选得准"）。
- **④ 更高杠杆的替代方向**：(a) 分诊+按需深潜做到极致 > 把 10 篇都做深；(b) **让他就某篇追问、系统据此学兴趣**——对话是**最密的个性化信号源**，杠杆远高于 9 票/17 天且语义模糊的 👍/👎；(c) 个性化真杠杆**不在 rerank 措辞，在"选得准尺 + 隐式信号（网页阅读遥测）"**。
- **⑤ 会推翻的根本决策**：见 2.1 三条。

### 2.3 高杠杆新增方向（非推翻，但该做）

- **冲刺补课 canon 模式**【调研+推断】：近期有一个一次性的高强度学习窗口，最高杠杆可能是"领域正典补课包"（一次性 canon 深读 + 主题周卷）而非每日 delta。建议加 `--mode canon`（按主题聚合历史 + 补前沿），与每日增量并存——**增加模式，不推翻每日**。
- **"能对话"钩子**【红队反赌 + 主线复核】：Face 2 铁律 = 外包 Claude Code 原生会话，基底其实已零代码存在（仓库目录开会话即可读 digests/memory.db）。缺的不是 harness，是**①一页 HOWTO ②把对话兴趣信号回流 rerank/USER.md 的钩子**。值得轻量试点（不违"永不自建对话 harness"）。
- **源盲区补齐**【调研到】：**OpenAI Agents SDK**（github.com/openai/openai-agents-python/releases）、**Google ADK 2.0**（github.com/google/adk-python，2026-06-30 Go 2.0 GA）、**MCP 规范仓库**（2026-07-28 RC = launch 以来最大修订、三周后发布）三个 releases feed 都在用户域正中靶心却未收录，零代码一段 YAML。与"不加中文源/不覆盖 X"的有意收紧无关。

### 2.4 暂缓判定登记簿（n 不足，不在噪声上推翻）

| 命题 | 为何暂缓 | 判定规则 | 复核触发 |
|---|---|---|---|
| 精确公式是否该纳入"时机/curriculum" | 需数周阅读+反馈数据 | 攒够 2 周干净跑后看已会降权是否误伤学习路径 | 2 周连续无断供后 |
| 投票 👍/👎 是否该拆成多轴（不相关/已会/质量差） | 全史仅 9 票 | 待 MIN_PAIRS≥10 达标、排序尺真校准后再评是否需多轴 | 反馈配对达标后 |
| 是否搬迁宿主机 | 订阅 CLI 锁死本地机 | 先加固（唤醒+dead-man+备份）观察一个月无人值守稳定性 | 加固后满 1 月 |

---

## 三、执行层发现（实现 vs 预期）

> 严重度：🔴动摇使命 / 🟡拉低质量 / 🟢锦上添花。证据：【码】读代码实证 /【运】运行数据实证 /【推】推断待验。工时：S(<0.5d)/M(0.5–2d)/L(>2d)。所有条目 file:line 主线抽验 10%。

### 3.1 🔴 动摇使命级（11 条，展开）

**【正确性】**

**A-1 RunLock 1h 僵死阈值夺走仍在跑的长跑锁 → 并发双投**（`lock.py:14,52,57`）【码】
`STALE_AFTER_SECONDS=3600`，`ts` 仅 acquire 时写一次、**无心跳刷新**。长跑单调增到 >1h（deepread 常态 28.8min + triage/deepread 任一重试即破 64min；L11 的连发达数小时）时 `_pid_alive=True` 但 `age<3600=False` → `and` 为假 → **落入夺锁分支**，即使原进程还在跑 deepread。此时第二个 run 启动（用户事故日手动补跑习惯，decisions 实证 07-07/07-08 均手动补过；或周日 review 撞上；或手动 regen）→ 夺锁并发 → 钉钉双投（archive_if_new 升版出两张卡）+ opus 额度双烧 + seen/items/fetch_state/checkpoint 互相 last-writer 覆盖。**最毒的相关性：跑得最久的日子恰是最想手动补跑的日子**——RunLock 唯一存在理由却在最需要时失效。**方案**：pid 存活即视为持锁（age 不推翻活 pid），僵死判定只用于 pid 已死；或 sanity cap 抬到 ≥6h + 跑中周期刷新 ts 心跳。**工时 S**。

**A-2 部分投递失败被当成功 → 条目永久漏报**（`deliver.py:44`；`ctx.errors` 仅 3 处写入）【码+运】
`if results.get("local") or any(results.values()): _mark_seen`——local 归档成功即标 seen。渠道返回 False 完全静默（不 log、不进 ctx.errors，仅抛异常才 warn 且仍不进 errors）。故 web_reader+dingtalk_card 都 False 但 local=True → 10 条全标 seen → 次日 fetch 跳过 → **面向用户的渠道啥都没收到、条目却永久烧成 seen**。今晨 zn2b 真跑即命中（web_reader=False 记 errors=0）。**精确后果**（第二份 A1 校正）：卡片直达详解页的 seg 链永久失效+无补投（消息不可变），但页内容非永久缺失——次日成功 web 部署 build_site 会把该天页补建到稳定 seg（用户只能经 home/归档摸到，卡片直达链冻死）。**方案**：mark_seen 门槛改"≥1 面向用户渠道成功"（local 归档不算送达）+ 渠道 False append ctx.errors。**工时 S**。

**【个性化/自进化闭环】**

**A-3 "投票→越用越准"文档超卖、闭环空转**（`review.py:206,214` vs `rerank.py:71`）【码+运，抽验坐实 rerank 零命中 feedback】
周报**逐字**对用户说"凑满 {mp} 次对比排序就开始按你口味校准…凑满就开始生效"；但 rerank 的个性化输入只有 `load_known_topics(USER.md)` + `topic_history`，**全文件无一处读 `Paths.feedback`**。凑满 10 对也只解锁 ranking.py 的显示、不改任何 rerank 行为（吃 👍/👎 是 D 阶、一直推迟）。实测全史 9 票、单日最高 8 对、MIN_PAIRS=10 从未达标。**用户越投越信、系统原地空转**——这是最直接侵蚀"越用越准"使命的一条。**方案**：要么落地 D 阶（反馈真进 rerank preamble/加权），要么把周报+README 的"校准排序/越用越准"改为诚实措辞（当前投票只驱动周报+eval 显示）。**工时 M（落地 D）/ S（改措辞）**。

**A-4 北极星"选得准/对他新"没有可用尺子**（`ranking.py:20-21`）【码】
代码自认"τ judges the ORDER, not whether the right items were SELECTED (selection has no ground truth)"。三尺：faithfulness 测"写得忠实"、独立 judge 明标"稳定性诊断非正确性分"、feedback-pairwise 饿死。**核心能力无尺 = 自进化无判据**。对应方向层推翻②。**方案**：反馈攒起前引入"对他新"代理尺（USER.md 已会命中率/topic 复现统计）或次日社区信号 hit-rate【调研到：smol.ai 归档/HN Algolia】。**工时 M**。

**A-5 E1 幸存者偏差：断供日被读成"全部正常出刊"**（`review.py:124-129`）【码+运】
run-health 只从**已存在的 .md 归档**读、只认 `"排序降级" in text`。07-06 是 0 投递日（arxiv 挂），该文件不含降级横幅 → 读成"全部正常出刊"；LLM 草案（`2026-07-08-review.md:77`）放大成"连续 9 天无降级、无掉线"。**断供在健康读数里隐形还被坐实**，用户据此以为系统稳、恰恰错过最该拍板的可用性事故。**方案**：run-health 数据源改 `last_run.json`/投递条数（0 或欠投即标事故），不从归档反推单一横幅。**工时 S**。

**【隐私】隐私防线四洞（合并展开，全 🔴）**

**A-6 CF 历史部署快照永生 · 活暴露**（`web_reader.py:61`；`_site.py:288`）【运】
deploy_site 仅部署无 deployment delete；"下线"仅 rmtree 本地。已下线的敏感页在**多个历史部署 URL 仍公网 HTTP 200 + 含泄漏内容**（生产主域已 404）；根因=CF Pages 每次部署生成不可变快照。**处置矩阵**：路径 A 删历史部署（保留生产别名；删快照不影响卡片=卡片指向稳定别名非快照）=**推荐**；路径 B 轮换 WEB_SECRET=**确认无效**（快照是静态文件、secret 只改未来 seg）；路径 C 接受（双重不可枚举但标识一旦泄漏退化单重）。**本轮只出矩阵、用户拍板，不擅自删。工时 S（执行删除）**。

**A-7 git 出站无强制闸 → 已有内容进公开仓**（`.git/hooks` 无 pre-commit；leak_scan 仅手动 CLI）【运】
leak_scan 的自动消费者只有 3 处出站渲染面，**无 push 面闸**。实测 HEAD：词表**可识别**的命中仍有约 8–9 处躺在 tracked 文件（decisions.md / SPEC.md / ARCHITECTURE_AUDIT.md）。这是"闸能抓、没人拉"。**方案**：装 pre-commit hook 跑 leak_scan 扫暂存 fail-closed + commit-msg hook。**工时 S**。

**A-8 泄漏词表漏收关键身份词**（词表机制）【运】
根因=手工提炼"按注意力窗口采样"（只扫部分档案）vs 身份词"按语义散布全文"。同类漏网还有细分部门/职级/母校/履历时间线特征等"只有认识他的人才知道"的词。**方案**【调研+推断】：`scripts/derive_leak_terms.py` 按固定类别 checklist（雇主含历史全部/人物/教育/地理/产品线/履历特征）从档案派生 + 人工确认；doctor 检词表 mtime 早于档案即告警"画像更新词表滞后"。**工时 M**。

**A-9 leak 闸 fail-open 无识别层分级**（`leak_scan.py:41`；`_site.py:93`/`publish.py:137`/`review.py:467`）【码】
词表缺失只返内置通用词 + warning，而闸调用方只看 hits、warning 仅 log 然后照发。词表缺失/不可读 → 识别层整层失效、仅剩 6 个通用词、公开页照发。对被身份事故坑过的用户，这是最危险的默认。**方案**：load_patterns 返 identity_ok；**公开出站闸 identity_ok=False → fail-closed 拒发**，私有出站可保留 fail-open。**工时 S**。

**【运维】**

**A-10 睡眠触发不设防 · 比出事那天更弱**（`pmset -g sched` 无唤醒项；`run-daily.sh:39`）【运】
旧机的 `pmset repeat wakeorpoweron` 唯一防线没随迁移重建；本机 `sleep 1`（空闲 1 分钟即睡）、此刻在电池上（caffeinate -s 电池态是文档化 no-op）。launchd StartCalendarInterval **不主动唤醒**、只醒来补跑一次（可能落无网 DarkWake）= 07-07 断供同款死法，现在防护比出事那天更弱。网络门/caffeinate 只救"已醒着跑"的 DarkWake 切片、救不了"根本不到点醒"。**方案**【调研到】：`sudo pmset repeat wakeorpoweron … 17:25`（早触发 5 分钟）+ 无人值守窗口插电 + `pmset -c sleep 0`（AC 上才让 caffeinate 生效）+ "错过即补跑"显式化。**工时 S**。

**A-11 零备份 · 已真丢过数据**（`tmutil: No destinations`；versions.json v2 lost 墓碑）【运】
memory.db（个性化全部积累）/feedback（距 D 阶只差 2 对）/decisions.md 全在 gitignored data/、无 git 无 Time Machine 无云。v2 已丢过一次证明非理论。一次故障 = 北极星倒退冷启动。**方案**：memory.db+feedback+digests+self_improve 每日快照（restic/rsync 到独立卷或私有远端）+ 开 Time Machine。**工时 S**。

### 3.2 🟡 拉低质量级（表格）

| # | 域 | 发现 | file:line | 证据 | 方案（工时） |
|---|---|---|---|---|---|
| B-1 | 正确/运维 | **deepread 无熔断 · 最坏 ~4h 白烧**（L11；两 agent 独立坐实=高置信；与 A-1 占锁、推翻③联动）| `deepread.py:228,257`（未传 retries→默认 3）；`claude_code.py:156` | 【运】07-08 hsxr 40min42s 内 20 连发零字节挂起 | deepread 传 retries=1 + 连续 K=3 灭即熔断 + wall-clock 上限（S）|
| B-2 | 运维 | **成功语义只认 stage 异常 · 空投递/部分成功记成功**（B3；与 A-2 同族）| `runner.py:114`；七日矩阵 07-06/07-08 | 【运】07-06 卡片 0 条记 errors=0 | 派生 `run_health=ok\|degraded\|failed` + 按 run_id 归档不覆写（M）|
| B-3 | 运维 | **web_reader 部署单发无重试 · 失败记成功**（N1）| `web_reader.py:36,61` | 【运】今晨 zn2b 部署超时 | deploy_site 加 2-3 次指数退避重试（S）|
| B-4 | 运维 | **额度无硬闸 + 软预算漏算 cache token**（对应推翻③）| `config.py:164`；`runner.py:147` | 【运】budget 判据=input+output、漏 cache_creation 232K→形同虚设 | 预算计入 cache_creation 改硬闸（M）|
| B-5 | 运维 | **本机跑在触发 L11 的 CLI 2.1.204 · 防回归 pin 没迁移 + mmdc 缺**| `.env` 缺 `AGENT_RADAR_CLAUDE_BIN`；`claude --version`=2.1.204 | 【运】 | 装良好 CLI 到固定路径+.env 补 pin+装 mermaid-cli（S）|
| B-6 | 闭环 | **topic_history"近N天同主题×K"信号损坏**| `store.py:131`；`arxiv.py:74` | 【码+运】paper tag 覆盖 68% → K=44~54 零区分度、还给前沿论文灌降权信号（与"对他新"相反），靠 LLM 豁免兜底 | 计数排除 paper/release 文档类型 tag（S）|
| B-7 | 闭环 | **eval/review 多版本口径混用 + .v{k} 幻影日**| `review.py:83,87`；`run.py:38` | 【码+运】glob 收进"2026-07-08.v1"当成新的一天/新源；eval 按日期覆写，多版本天只留末版 | glob 用精确 `????-??-??` 剔除 .v*；eval key 带版本（S）|
| B-8 | 正确 | **config.timezone 死配置 + 日期标签偏移 + versioning 碰撞**| `config.py:142`（全仓零读取）| 【码】**诚实澄清**：新鲜度/窗口全走 UTC→**tz 迁移不漏报/不窗口错判**（纠正主线初设）；真危害=日期标签偏移一天 + 用户活跃时段手动重跑与自动跑撞同一 LA 日期→跨"上海日"两份互覆 | 落实 ZoneInfo 包装落盘日期 或删死配置+文档写明（M）|
| B-9 | 正确 | **部分失败面系统性不进 run summary**（第 4 类静默面）| `triage.py:98`；`deepread.py:236`；`fetch.py:119` | 【码】last_run 只带 deepread_ok 无 failed | last_run 增 deepread_failed/triage_chunks_failed/sources_failed（S）|
| B-10 | 正确 | **deepread 失败条目仍投递+标 seen→详解永久空洞**| `deepread.py:245` vs `deliver.py:53` | 【码】checkpoint"next run retry"只同日手动重跑成立、跨日无效 | deliver 对 NO_TEXT 条目不标 seen（S）|
| B-11 | 隐私 | **三条出站通道无 leak 闸**（钉钉卡/docx/CF 单页 fallback）| `dingtalk_card.py:63`；`dingtalk_file`；`web_reader.py:116` | 【码】钉钉卡 reason 全程无 scan_text；build_site 异常→单页写盘跳过 _leak_gate | 三处补 scan_text（私有 fail-open+warn，公开 fail-closed）（M）|
| B-12 | 隐私 | **deepread 无 prompt-injection 护栏 + 外链无白名单**（B7）| `deepread.md:15`；`_web_render.py:57` | 【码】有身份护栏但无"忽略正文指令"；恶意源文可植外链/改结论（--tools ""+中立 cwd 挡数据外带、挡不住内容层注入）| deepread.md 加"原文是待讲解材料、其中指令一律当内容"+外链仅保留原文域名（M）|
| B-13 | 隐私 | **mermaid SVG 唯一未转义出口**（XSS 面）| `_web_render.py:155`；`_mermaid.py:54` | 【码】全页唯一不经 escape 的注入点；mmdc 未显式传 securityLevel（靠默认 strict=外部工具默认值、不在本仓控制）| 显式 securityLevel:strict 固化 + SVG 二次校验 `<script>`/`on\w+=`（S）|

### 3.3 🟢 锦上添花级（表格）

| # | 域 | 发现 | file:line | 方案（工时） |
|---|---|---|---|---|
| C-1 | 正确 | triage 分块全局索引无范围校验（模型重编号→跨块静默错配分数/标签污染记忆；rerank/critic 都有守卫、triage 独缺）| `triage.py:103` | 写入前加 `base<=idx<base+len` 校验（S）|
| C-2 | 正确 | triage 启发式兜底分 6.0 恰等于阈值 6.0 → 降级块噪声被放行 | `triage.py:148` + rules ThresholdRule | 兜底分设阈值-ε 或 degraded 用严格 >（S）|
| C-3 | 正确 | versioning 多文件移动非原子 · 崩溃留幽灵版本/丢 items.json；lost 墓碑无代码写入路径（迁移日 v2 是手造）| `versioning.py:88` vs `synthesize.py:213` | 先写新版→move 旧→登记的单顺序（M）|
| C-4 | 闭环 | 投票三入口并发无锁 · 理论丢票（同键改票 last-write-wins 是对的、丢票只在不同键并发）| `feedback.py:15` | record_feedback 加 fcntl.flock（S）|
| C-5 | 闭环 | USER.md 已会清单靠字面"已会"匹配 · 改标题静默失效（唯一 live 个性化通道单点无体检）| `rerank.py:35` | doctor 加"已会清单已加载 K 主题"探测（S）|
| C-6 | 隐私 | rerank why/reason 或把"已会画像"写公开页（二次泄漏面，leak 词表不含"已会技能"措辞拦不住）| `rerank.py:120`→`_site.py:141` | rerank.md 约束 why 只讲文章本身、不指称读者已掌握主题（S）|
| C-7 | 隐私 | neutral-cwd 三不变量 + 词表在位未进 doctor（换机重置靠运气）| `cli.py` cmd_doctor | doctor 加祖先链无 CLAUDE.md/词表在位 两检（S）|
| C-8 | 运维 | 8 个 enabled 源全史零产出无告警（多数周月更正常，langchain-changelog 已知 0、gh-aider/autogen/letta 值得核）| candidates 9 日聚合 | review 加 per-source 连续≥N 天 0 条"疑似静默"清单（S）|
| C-9 | 运维 | 日志/运行态无限增长无轮转（launchd-daily.log 532K、digests 4.9M 只增不减）| `obs:33,68` | 日志接 newsyslog + trace/candidates/deepread_sources 加 N 天保留（S）|
| C-10 | 运维 | webvotes 线程 cursor int() 未包裹 · 可静默死（KeepAlive 不重启子线程）| `webvotes.py:43` | cursor 读进 try + _loop 包 try/except 重启（S）|
| C-11 | 运维 | plist 写死 LA 钟点 · DST 每年两次手动 +1h | plist 注释 | 接受手动 或改相对 UTC 触发+脚本换算（S）|
| C-12 | 债 | rerank 复用 models.synthesize 隐式耦合 / 两份 config.example.toml / Item.links,snippet 死字段 / recall 幽灵 stage / evolve 空包（stub 正确=E2 未做，符合承诺）| 多处 | 加 models.rerank 键、合并 example、删死字段/文档标注（S）|

### 3.4 诚实负结果（查过、没问题——反主线疑点）

- **窗口/新鲜度数学全 aware-UTC 无错窗**（两份 A1 独立复核）：`is_fresh`/`is_display_fresh`/`TimeWindow.cutoff`/96h leash/B2 补课窗 gap 比较两端一律 aware-UTC，naive `datetime.now()` 仅用于日期字符串裁剪（LA-vs-LA 自洽）。**tz 迁移不造成漏报/窗口错判/分区错乱**（纠正主线初始假设，如实采纳）。
- **memory.db 去重干净**：item_id PRIMARY KEY + INSERT OR REPLACE，07-05 双跑=合法不同文章；问题只在 topic_history 的 tag 语义（B-6）不在去重。
- **rerank 注入未被代码稀释**：listwise 解析→rank 梯度→多样性配额三层忠实，个性化确在 LLM 语义层生效（07-04 probe 前沿浮上、科普沉底），非纯 prompt 措辞。
- **self_applicable 链路闭合到消费者**（triage emit→review gather→渲染+喂草案），止步"呈现给用户"无自动改动 = 符合 E1 零自动应用边界，非"标了没人用"。
- **secret 处理干净**：env-only + del secret + 不上 argv + 不 log；无 shell=True/eval/verify=False；_web_render 文本路径 XSS 转义逐点无洞（仅 SVG 缺口 B-13）。
- **deepread checkpoint"失败绝不进"成立**；store.py topic_history SQL 全参数化无注入。

---

## 四、回归对账表（vs `docs/ARCHITECTURE_AUDIT.md` 2026-06-29）

> 上次独立架构审计列了 6 个"真缺口"。40 天后现状——项目真的推进了，但也长出新债。

| 上次缺口 | 上次结论 | 今天现状 | 判定 |
|---|---|---|---|
| #1 "对他新"个性化零实现 | B→D | USER.md→rerank 已落地且 LLM 层真生效（probe 实证）；**但反馈从不进 rerank（A-3）、topic_history 信号损坏（B-6）** | **半 fixed / 半 open** |
| #2 跨会话记忆零实现 | B | memory.db FTS5 落地、remember stage 在跑 | **fixed**（tag 语义待修 B-6） |
| #3 eval→改进闭环断裂 | E1 | review 周报上线、eval 自动链 | **结构 fixed / 新 open**：run-health 幸存者偏差（A-5） |
| #4 记忆选型偏向向量-RAG | 改 SPEC | 已纠偏 FTS5+USER.md、SPEC 回写 | **fixed**（调研复核结论仍成立，见方向层记忆 tripwire） |
| #5 无 per-LLM-call 可观测 | C/P3 | trace per-call + by_stage token 已加 | **fixed** |
| #6 管线无 item 断点 + token 预算不强制 | C/P3 | deepread checkpoint 已加；**token 预算仍只软告警且漏算 cache（B-4）** | **半 fixed / 半 open** |

**上次"不必补"清单复核**：主管线不 agent 化 ✅ 坚守；不多智能体 ✅；不向量 RAG ✅（调研复核仍成立）；不第二 LLM 后端——**方向层推翻③对此松动**（提议 deepread 有界 API 兜底逃生门，触及此边界，待用户拍板）。

---

## 五、处理顺序路线图

> 原则：先堵"动摇使命"的可靠性/隐私血口，再修数据正确性，方向层推翻按"缓解面最广"落地。

**第 0 档 · 最紧迫（无人值守成立的前提，全 S 工时、当天可完成）**
1. **睡眠唤醒 + dead-man**（A-10 + B-2 的 ping）：`pmset repeat wakeorpoweron 17:25` + 插电 + healthchecks.io ping → 钉钉告警。**这是"每天自动跑"字面成立的前提**。
2. **隐私血口**：装 git pre-commit/commit-msg leak 闸（A-7）+ 词表补关键身份词（A-8）+ leak 闸公开面 fail-closed（A-9）+ 删 CF 历史泄漏部署（A-6，用户拍板路径 A）。
3. **两个数据正确性 🔴**：RunLock pid 活即持锁（A-1）+ mark_seen 门槛改面向用户渠道（A-2）。
4. **备份**（A-11）：memory.db+feedback 每日快照。
5. **CLI pin**（B-5）：.env 补 AGENT_RADAR_CLAUDE_BIN 到良好版本。

**第 1 档 · 短期（1–2 周，堵可信性血口）**
6. E1 幸存者偏差修 run-health 数据源（A-5）；成功语义派生 run_health 字段（B-2）。
7. **诚实措辞**（A-3）：周报/README 去掉"投票→越用越准"超卖，或落地 D 阶让它成真（二选一，需用户定方向）。
8. deepread 熔断 + retries=1（B-1）+ 额度硬闸计入 cache（B-4）——**配合方向层推翻③**。
9. 部署重试（B-3）、静默失败面进 last_run（B-9）、失败条目不标 seen（B-10）。

**第 2 档 · 中期（方向层落地，需用户拍板方向）**
10. **推翻①动态分层深读**（最高杠杆，缓解额度/阅读/测量三面）——先加网页阅读遥测取消费端数据、再动 top_k。
11. **推翻②抽样 faithfulness + 新建"选得准"尺**（A-4 + 次日社区信号反查）。
12. topic_history 修 tag 语义（B-6）、注入护栏 + SVG 净化（B-12/B-13）、多版本 eval 口径（B-7）。
13. **新增方向**：源盲区补 3 个 releases feed（零代码）；canon 模式试点；"能对话" HOWTO + 兴趣回流钩子。

**方向层"最该先想清楚"的一个**：**推翻①（动态分层深读）**——它是额度可持续性（推翻③）、测量成本（推翻②）、L11 白烧、阅读负担的共同上游。想清楚它，下游三件事一起松动。但落地前必须先补**网页阅读遥测**，否则"砍到几篇"没有数据依据。

---

## 六、探针附录（下次自审可重跑）

```bash
# 睡眠面
pmset -g sched; pmset -g | grep -E 'sleep|standby'; pmset -g batt
# 备份面
tmutil destinationinfo
# CF 历史部署活暴露（不回显泄漏内容，只看状态码）
npx --no-install wrangler pages deployment list --project-name <proj>
# 额度画像（四字段 + cache）
python3 -c "import json;d=json.load(open('data/state/last_run.json'));print(d['tokens'],d['by_stage'])"
# 投票配对数 vs MIN_PAIRS
python3 -c "import json,glob;[print(f, sum(1 for v in json.load(open(f)).values() if v.get('vote')=='up'), sum(1 for v in json.load(open(f)).values() if v.get('vote')=='down')) for f in glob.glob('data/feedback/*.json')]"
# topic_history tag 污染
python3 -c "import sqlite3;db=sqlite3.connect('file:data/memory.db?immutable=1',uri=True);print('paper占比',db.execute(\"select count(distinct item_id) from push_tags where tag='paper'\").fetchone()[0],'/',db.execute('select count(distinct item_id) from push_tags').fetchone()[0])"
# git 出站泄漏（用本地词表，输出永不外传）
bash scripts/leak_scan.py $(git ls-files)
# rerank 是否读 feedback
grep -n feedback radar/stages/rerank.py || echo "反馈不进 rerank（A-3 坐实）"
# 逐源静默归零
for f in data/candidates/*.json; do echo "$f"; python3 -c "import json,sys;d=json.load(open(sys.argv[1]));from collections import Counter;print(Counter(i['source_id'] for i in d))" "$f"; done
```

---

*本报告为只读审计产物，零代码改动。所有方案待用户拍板后另行落地（改进零自动应用）。敏感处置细节（部署标识、端点、注入手法、泄漏命中原文）见 gitignored 完整版。*

