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
"$PY" -m radar --mode daily                # set -e: daily's failure exits here with its rc (eval skipped)

# Daily succeeded → keep the ruler running unattended: eval today's digest (faithfulness + ranking).
# Best-effort by design — eval failure/quota-exhaustion must never taint the already-delivered daily
# or this script's exit code; the per-item checkpoint in data/eval/{date}.json resumes on the next run.
"$PY" -m radar --mode eval "$(date +%F)" \
  || echo "[run-daily] eval failed (rc=$?) — checkpoint kept, resumes next run" >&2
