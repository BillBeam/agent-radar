#!/usr/bin/env bash
# Long-running DingTalk Stream listener (catches 👍/👎 card taps → writes feedback/{date}.json).
# Called by launchd (KeepAlive). DingTalk Stream is domestic → the proxy MUST be stripped.
set -euo pipefail
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"      # → repo root

[ -f .env ] && { set -a; . ./.env; set +a; }              # DingTalk creds (DINGTALK_*)
# Strip every proxy var AFTER sourcing .env (the long-lived Stream conn cannot go via a
# Western proxy), and the subscription key.
unset ANTHROPIC_API_KEY HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy
export NO_PROXY='*'

PY=".venv/bin/python"; [ -x "$PY" ] || PY="$(command -v python3)"
exec "$PY" -m radar --mode serve
