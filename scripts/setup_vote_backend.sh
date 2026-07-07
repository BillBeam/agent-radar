#!/usr/bin/env bash
# One-time vote-backend setup (idempotent). Everything else (page buttons, _worker.js,
# serve poller) is already shipped and waits on this.
#
# Prereq: CLOUDFLARE_API_TOKEN must ALSO carry "Workers KV Storage:Edit" (the Pages-only
# token can deploy sites + set secrets but cannot create the KV namespace — verified
# 2026-07-07). Widen the token in the CF dashboard, then run:
#
#     bash scripts/setup_vote_backend.sh
#
# Steps: ① create KV namespace "agent-radar-votes" (reuse if it exists) → ② bind it as
# VOTES to the Pages project's production env → ③ remind about WEB_SECRET (already set
# 2026-07-07) → ④ tell you the config.toml flip that turns the page buttons on.
set -euo pipefail
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
[ -f .env ] && { set -a; . ./.env; set +a; }

PROJECT="${CLOUDFLARE_PAGES_PROJECT:-agent-radar}"
: "${CLOUDFLARE_API_TOKEN:?need CLOUDFLARE_API_TOKEN in env/.env}"
: "${CLOUDFLARE_ACCOUNT_ID:?need CLOUDFLARE_ACCOUNT_ID in env/.env}"
API="https://api.cloudflare.com/client/v4/accounts/$CLOUDFLARE_ACCOUNT_ID"
AUTH=(-H "Authorization: Bearer $CLOUDFLARE_API_TOKEN" -H "Content-Type: application/json")

# ① namespace (reuse by title if present)
NS_ID=$(curl -s "${AUTH[@]}" "$API/storage/kv/namespaces?per_page=100" |
  python3 -c 'import json,sys;d=json.load(sys.stdin);print(next((n["id"] for n in d.get("result") or [] if n["title"]=="agent-radar-votes"),""))')
if [ -z "$NS_ID" ]; then
  NS_ID=$(curl -s "${AUTH[@]}" -X POST "$API/storage/kv/namespaces" \
    --data '{"title":"agent-radar-votes"}' |
    python3 -c 'import json,sys;d=json.load(sys.stdin);print(d["result"]["id"] if d.get("success") else "")')
  [ -n "$NS_ID" ] || { echo "❌ KV namespace create failed — token still lacks Workers KV Storage:Edit?"; exit 1; }
  echo "✓ KV namespace created: agent-radar-votes"
else
  echo "✓ KV namespace exists: agent-radar-votes"
fi

# ② bind as VOTES on the Pages project (production)
OK=$(curl -s "${AUTH[@]}" -X PATCH "$API/pages/projects/$PROJECT" \
  --data "{\"deployment_configs\":{\"production\":{\"kv_namespaces\":{\"VOTES\":{\"namespace_id\":\"$NS_ID\"}}}}}" |
  python3 -c 'import json,sys;print(json.load(sys.stdin).get("success"))')
[ "$OK" = "True" ] || { echo "❌ Pages project binding failed"; exit 1; }
echo "✓ bound as VOTES → $PROJECT (production)"

echo "
Done. 剩两步：
  1. config.toml 的 [channels.web_reader] 里加一行:  vote_api = \"/vote\"
  2. 重新部署一次站点（下一次 daily 会自动做，或手动:
     .venv/bin/python scripts/rebuild_site.py --deploy）
之后阅读页的 👍/👎 即通；serve 每 60s 把票并进 feedback。"
