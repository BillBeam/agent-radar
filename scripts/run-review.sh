#!/usr/bin/env bash
# Weekly E1 reviewer (radar --mode review) — aggregates eval trend / votes / source mix /
# self_applicable / critic / WATCHLIST into data/self_improve/reviews/{date}-review.md and
# pushes the top-line summary to the DingTalk 1v1. DRAFTS ONLY — it never applies anything.
# Called by launchd (Sunday evening); safe to run manually any time.
set -euo pipefail
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"      # → repo root

# .env: DingTalk creds for the summary push + HTTPS_PROXY for the claude CLI (draft model).
# The DingTalk push strips the proxy itself (session.trust_env=False), so both coexist.
[ -f .env ] && { set -a; . ./.env; set +a; }
unset ANTHROPIC_API_KEY                                    # force subscription, never API billing
# launchd's clean context has a minimal PATH — the claude CLI (Homebrew cask) lives in
# /opt/homebrew/bin; ~/.local/bin covers alternative installs (draft model needs it).
export PATH="/opt/homebrew/bin:$HOME/.local/bin:$PATH"

PY=".venv/bin/python"; [ -x "$PY" ] || PY="$(command -v python3)"
"$PY" -m radar --mode review
