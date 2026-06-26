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
