# 部署：无人值守（launchd 三件套）

让 Agent Radar 每天自动跑、投票实时接住、每周自动盘点——三个 launchd agent：

| agent | 干什么 | 频率 |
|---|---|---|
| `com.agentradar.daily` | `--mode daily`：抓取 → 分诊 → 重排 → 批判 → 深读 → 四渠道投递；**跑完自动 `--mode eval` 当天**（尺子；失败只记日志） | 每天 08:30 |
| `com.agentradar.serve` | `--mode serve` 常驻：接钉钉 👍/👎 卡片点击 → 写 `feedback/{date}.json` | 常驻（崩了自动拉起、登录自启） |
| `com.agentradar.review` | `--mode review`：E1 周度盘点（eval 趋势/投票/源分布/自相关标注/WATCHLIST）→ 草案周报 + 摘要推钉钉；**零自动应用** | 每周日 21:00 |

> ⚠️ **TCC 前提（血泪）**：仓库**不能**放在 `~/Desktop`、`~/Documents`、`~/Downloads` 等 macOS 隐私保护目录——launchd 干净上下文里的 `/bin/bash` 读不了这些路径（`Operation not permitted` + KeepAlive 126 循环；`launchctl load` 当场第一次能跑是继承了终端 TCC 的假象）。放家目录普通路径（如 `~/agent-radar`）即可，无需给 bash 完全磁盘访问。

## 一次装好

```bash
# 0) venv + 依赖见根 README；准备 .env（gitignored），至少要有：
#      DINGTALK_CLIENT_ID / SECRET / ROBOT_CODE / CARD_TEMPLATE_ID / USER_ID
#    再加一行给「无人值守 daily」抓西方源用的代理：
#      HTTPS_PROXY=http://<你的代理>:<端口>
#    （serve 会自动剥代理——钉钉 Stream 是国内、不能走西方代理。）

# 1) 生成并加载（脚本自动把本仓库绝对路径填进 ~/Library/LaunchAgents 的 plist）
bash scripts/install-launchd.sh all         # 或 daily / serve / review 单独装（both=daily+serve）

# 2) 确认在跑
launchctl list | grep agentradar
tail -f data/state/launchd-serve.log        # serve 日志（应立刻在监听）
tail -f data/state/launchd-daily.log        # daily 日志（每天 08:30 后看；尾部跟着当天 eval）
tail -f data/state/launchd-review.log       # review 日志（每周日 21:00 后看）

# 立刻手动触发（不等定时）：
launchctl start com.agentradar.daily
launchctl start com.agentradar.review

# 卸载：
bash scripts/install-launchd.sh uninstall
```

## 代理为什么这么处理
- **daily 抓取**要走代理（多数源是西方站点）→ `run-daily.sh` 从 `.env` 读 `HTTPS_PROXY`；钉钉投递在 channel 内 `session.trust_env=False` 自动剥代理，所以「抓取走代理 + 投递走国内直连」在**同一进程**里并存。
- **serve**（钉钉 Stream 长连接）是国内服务、**绝不能走西方代理** → `run-serve.sh` source 完 `.env` 后把所有 `*_PROXY` 全 unset、`NO_PROXY='*'`。

## 改时间 / 不想用 launchd
- 改频率：编辑 `deploy/com.agentradar.daily.plist` 的 `StartCalendarInterval`（或改装好后 `~/Library/LaunchAgents/` 里那份，再 `launchctl unload/load`）。
- 不用 launchd：`run-daily.sh` 可直接进 cron；serve 可 `nohup bash scripts/run-serve.sh > data/state/serve.log 2>&1 &`。

> 生成的 `~/Library/LaunchAgents/com.agentradar.*.plist` 含你的绝对路径与代理，**留在本地、不进仓库**（仓库只放 `deploy/*.plist` 模板 + `scripts/run-*.sh` + `install-launchd.sh`，全部脱敏）。
