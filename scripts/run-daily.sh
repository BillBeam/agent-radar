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

PY=".venv/bin/python"; [ -x "$PY" ] || PY="$(command -v python3)"
exec "$PY" -m radar --mode daily
