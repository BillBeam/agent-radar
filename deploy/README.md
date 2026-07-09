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

## 无人值守的两个前提（2026-07-09 三连事故后加）

定时跑只有在**机器醒着、插着电**时才可能跑完整。三次连续断供（07-07 dark-wake、07-08 电池
切片、07-09 合盖睡眠）都是同一件事：跑被睡眠切成碎片，每个醒来窗口里网络是死的，管线诚实降级
后什么也没推到手机上。`caffeinate -s` **在电池上是官方 no-op**，软件盖不住。

**1. 唤醒计划**（一次性，需要 sudo；迁移新机时会丢，记得重装）

```sh
sudo pmset repeat wakeorpoweron MTWRFSU 17:25:00   # daily 跑在 17:30
pmset -g sched                                     # 核对
```

**2. 插电**。没插电时 `run-daily.sh` 会**主动跳过**这次定时跑（等 10 分钟 AC 未到 → 退出 0），
并往钉钉推一句「今天的定时跑跳过了」。这是设计：跳过好过产出一份被睡眠切碎的降级简报去覆盖
昨天完好的那份。恢复路径 = 主页的「⟳ 立即抓取」按钮（它带 `AGENT_RADAR_FORCE=1`，不受这道闸约束）。

## 钉死 claude CLI 版本（必须）

`.env` 里设 `AGENT_RADAR_CLAUDE_BIN=<path>`。**不钉 = 每天开盲盒**：homebrew 在 2026-07-08
23:59 把 cask 从 2.1.204 换成 **2.1.205**，而 2.1.205 会把每个超过 ~301s 的流式响应砍断
（`API Error: Connection closed mid-response`）——V5 深读几乎每篇都超过 300s，于是产品的核心
一环静默失效了两天，零告警。2.1.201 正常。

```sh
npm_config_prefix=$HOME/.local/share/agent-radar/cc-2.1.201 \
  npm i -g @anthropic-ai/claude-code@2.1.201
echo 'AGENT_RADAR_CLAUDE_BIN=$HOME/.local/share/agent-radar/cc-2.1.201/bin/claude' >> .env
python -m radar --mode doctor      # 未钉住 / 撞上 2.1.205 都会 ⚠
```

解钉之前先复测：拿一篇 >300s 的真实深读提示词跑一遍，确认新版本能跑完。
