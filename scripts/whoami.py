"""Get YOUR DingTalk userId from your mobile, so the card can be delivered to you 1v1.

    cd agent-radar && set -a && source .env && set +a && \
        source .venv/bin/activate && python scripts/whoami.py <你的钉钉手机号>

On success it prints your userId and writes DINGTALK_USER_ID into .env. The mobile stays local
(never printed, never committed). If the app lacks 通讯录 read permission, it says so.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import requests

OAPI_OLD = "https://oapi.dingtalk.com"
_S = requests.Session()
_S.trust_env = False   # DingTalk is domestic — no proxy


def _write_env_userid(uid: str) -> None:
    p = Path(".env")
    lines = [ln for ln in (p.read_text(encoding="utf-8").splitlines() if p.exists() else [])
             if "DINGTALK_USER_ID" not in ln]
    lines.append(f"DINGTALK_USER_ID={uid}")
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(mobile: str) -> int:
    cid, csec = os.getenv("DINGTALK_CLIENT_ID"), os.getenv("DINGTALK_CLIENT_SECRET")
    if not cid or not csec:
        print("source .env first (DINGTALK_CLIENT_ID / DINGTALK_CLIENT_SECRET)")
        return 1
    tok = _S.get(f"{OAPI_OLD}/gettoken", params={"appkey": cid, "appsecret": csec},
                 timeout=20).json().get("access_token")
    if not tok:
        print("❌ token failed (old-style gettoken)")
        return 1
    r = _S.post(f"{OAPI_OLD}/topapi/v2/user/getbymobile",
                params={"access_token": tok}, json={"mobile": mobile}, timeout=20).json()
    uid = (r.get("result") or {}).get("userid")
    if not uid:
        print(f"❌ couldn't resolve userid (errcode={r.get('errcode')}, {r.get('errmsg')}).")
        print("   → the app likely lacks 通讯录/成员信息读取 permission. Either grant it in the")
        print("     开发者后台 → 权限管理, or get your userid from 管理后台 → 通讯录 → 你自己.")
        return 1
    print(f"✅ your userId = {uid}  (written to .env as DINGTALK_USER_ID)")
    _write_env_userid(uid)
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: python scripts/whoami.py <你的钉钉手机号>")
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1].strip()))
