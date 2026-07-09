#!/usr/bin/env bash
# Unattended daily pipeline (fetch → … → deliver). Called by launchd/cron.
# Privacy-safe: no absolute paths baked in — resolves the repo root relatively.
set -euo pipefail
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"      # → repo root

# .env (gitignored) holds DingTalk card creds (DINGTALK_*) and, for the unattended daily,
# HTTPS_PROXY so fetch can reach the Western sources. DingTalk delivery strips the proxy
# channel-side (session.trust_env=False), so fetch-via-proxy + deliver-domestic coexist.
[ -f .env ] && { set -a; . ./.env; set +a; }
unset ANTHROPIC_API_KEY                                    # force subscription, never API billing
# launchd's clean context has a minimal PATH — the claude CLI (Homebrew cask) lives in
# /opt/homebrew/bin; ~/.local/bin covers alternative installs. Without this, every LLM
# stage dies with FileNotFoundError under launchd (found by the first real review run).
export PATH="/opt/homebrew/bin:$HOME/.local/bin:$PATH"

PY=".venv/bin/python"; [ -x "$PY" ] || PY="$(command -v python3)"

# --- network-ready gate (2026-07-07 postmortem) ---
# A launchd wake-up run can start in a dark-wake with the proxy tunnel still dead: 18/28
# sources burned their retries into RemoteDisconnected before the real wake brought the
# network up. Park here until a probe THROUGH the ambient proxy succeeds. `sleep` only
# ticks while the machine is awake, so this rides across sleep cycles and re-probes on
# each wake. Give up after ~20 awake-minutes and run anyway — fetch's salvage pass, the
# B2 catch-up window and the digest's degradation banner handle a truly dead network honestly.
net_ready=0
for i in $(seq 1 40); do
  code="$(curl -s -o /dev/null --max-time 8 -w '%{http_code}' https://www.gstatic.com/generate_204 || true)"
  case "$code" in
    2*|3*) net_ready=1; [ "$i" -gt 1 ] && echo "[run-daily] network ready after $i probes" >&2; break ;;
  esac
  echo "[run-daily] network/proxy not ready (probe #$i, code=${code:-none}) — retrying in 30s" >&2
  sleep 30
done
[ "$net_ready" = 1 ] || echo "[run-daily] network still not ready after 40 probes — proceeding anyway" >&2

# --- AC-power gate (2026-07-09 postmortem) ---
# caffeinate: once the run starts, don't let idle/system sleep chop it into dark-wake
# fragments mid-pipeline (07-07: fetch was sliced across 4 wake windows). No-op off macOS.
# ⚠️ On BATTERY caffeinate -s is a documented no-op: closed-lid maintenance sleep still
# slices the run. Three runs in a row (07-07 dark-wake, 07-08 battery, 07-09 clamshell) were
# chopped into wake windows with no network — each produced a DEGRADED digest that overwrote
# latest.md and the home page, and delivered nothing to the phone. A skipped run is strictly
# better than that: it leaves yesterday's good digest intact and says why.
# Unattended (launchd) only. An interactive/manual run (a tty, or AGENT_RADAR_FORCE=1) is the
# user standing at the machine — never block that. The home page's ⟳ button is the recovery
# path, and it spawns this script, so it sets AGENT_RADAR_FORCE=1 through the trigger poller.
on_battery() { command -v pmset >/dev/null 2>&1 && pmset -g batt | grep -q "Battery Power"; }
if [ "${AGENT_RADAR_FORCE:-0}" != "1" ] && [ ! -t 1 ] && on_battery; then
  for i in $(seq 1 10); do            # give a just-woken laptop a chance to see its charger
    on_battery || break
    echo "[run-daily] on battery (check #$i) — waiting 60s for AC" >&2
    sleep 60
  done
  if on_battery; then
    echo "[run-daily] ⚠️ still on battery — SKIPPING this scheduled run (sleep would slice it into" >&2
    echo "[run-daily]    a degraded digest that overwrites the good one). Tap ⟳ on the home page." >&2
    "$PY" scripts/push_note.py \
      "🔌 今天的定时跑跳过了：Mac 在电池上，跑到一半会被睡眠切碎。插上电，或到主页点「⟳ 立即抓取」。" \
      >/dev/null 2>&1 || true
    exit 0                            # not a failure — a decision. launchd stays happy.
  fi
  echo "[run-daily] AC restored — proceeding" >&2
fi
CAFF=""; command -v caffeinate >/dev/null 2>&1 && CAFF="caffeinate -is"
$CAFF "$PY" -m radar --mode daily          # set -e: daily's failure exits here with its rc (eval skipped)

# Daily succeeded → keep the ruler running unattended: eval today's digest (faithfulness + ranking).
# Best-effort by design — eval failure/quota-exhaustion must never taint the already-delivered daily
# or this script's exit code; the per-item checkpoint in data/eval/{date}.json resumes on the next run.
$CAFF "$PY" -m radar --mode eval "$(date +%F)" \
  || echo "[run-daily] eval failed (rc=$?) — checkpoint kept, resumes next run" >&2
