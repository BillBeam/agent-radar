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

# caffeinate: once the run starts, don't let idle/system sleep chop it into dark-wake
# fragments mid-pipeline (07-07: fetch was sliced across 4 wake windows). No-op off macOS.
CAFF=""; command -v caffeinate >/dev/null 2>&1 && CAFF="caffeinate -is"
$CAFF "$PY" -m radar --mode daily          # set -e: daily's failure exits here with its rc (eval skipped)

# Daily succeeded → keep the ruler running unattended: eval today's digest (faithfulness + ranking).
# Best-effort by design — eval failure/quota-exhaustion must never taint the already-delivered daily
# or this script's exit code; the per-item checkpoint in data/eval/{date}.json resumes on the next run.
$CAFF "$PY" -m radar --mode eval "$(date +%F)" \
  || echo "[run-daily] eval failed (rc=$?) — checkpoint kept, resumes next run" >&2
