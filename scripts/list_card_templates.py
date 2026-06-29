"""Get your DINGTALK_CARD_TEMPLATE_ID.

Validates the DingTalk creds (fetches an access token) and best-effort tries an OpenAPI to list
this org's interactive-card templates. DingTalk's template management is mostly GUI, so if no
list API answers, the script tells you exactly where the id lives in the card-builder URL.

    source .env && python scripts/list_card_templates.py
"""
from __future__ import annotations

import os
import sys

import requests

OAPI = "https://api.dingtalk.com"
_S = requests.Session()
_S.trust_env = False   # DingTalk is domestic — never via the (Western) proxy


def _token(client_id: str, client_secret: str) -> str:
    r = _S.post(f"{OAPI}/v1.0/oauth2/accessToken", timeout=20,
                json={"appKey": client_id, "appSecret": client_secret})
    r.raise_for_status()
    return r.json()["accessToken"]


def main() -> int:
    cid, csec = os.getenv("DINGTALK_CLIENT_ID"), os.getenv("DINGTALK_CLIENT_SECRET")
    if not cid or not csec:
        print("set DINGTALK_CLIENT_ID / DINGTALK_CLIENT_SECRET first (e.g. `source .env`)")
        return 1
    try:
        token = _token(cid, csec)
    except Exception as e:  # noqa: BLE001
        print(f"❌ token failed — check your client_id/secret: {e!r}")
        return 1
    print("✅ creds OK (access token obtained).\n")

    # best-effort: DingTalk template management is mostly GUI; these may 404, that's fine.
    for path in ("/v1.0/card/templates", "/v1.0/card/templates/query", "/v1.0/card/instances/templates"):
        try:
            r = _S.get(f"{OAPI}{path}", timeout=15,
                       headers={"x-acs-dingtalk-access-token": token})
            if r.status_code == 200 and r.content:
                print(f"templates via {path}:\n{r.text[:2000]}")
                return 0
        except Exception:  # noqa: BLE001
            pass

    print("ℹ️ No template-list API answered — get the id from the card builder (reliable):")
    print("   1) open https://card.dingtalk.com/card-builder → 我的模版 → 「agent radar」→ 编辑")
    print("   2) the templateId is in the browser URL — looks like  xxxxxxxx-xxxx-…-xxxx.schema")
    print("   3) put it in .env:  DINGTALK_CARD_TEMPLATE_ID=<that id>")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
