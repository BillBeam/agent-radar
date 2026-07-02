"""DingTalk file channel — pushes the FULL deep-read 详解 (`Digest.markdown`) to the SAME 1v1
as the voting card, as an openable/saveable **.docx file**. If the docx path fails (python-docx
missing, render error, upload/send reject) it auto-falls-back to **chunked markdown messages**
(OTO sampleMarkdown, proven) — so the 详解 ALWAYS reaches the phone.

Reuses the enterprise-robot creds of `dingtalk_card` (same robot / same 1v1). Content is the
already-rendered `Digest.markdown` — NEVER regenerated. Secrets from env only; DingTalk is
domestic → no proxy. Aligns with the voting card by `[N]`.
"""
from __future__ import annotations

import json
import time

import requests

from ..core.models import Digest, RunContext
from ..core.ports import Channel
from ..core.registry import register
from .dingtalk import _chunk   # byte-aware section splitter (markdown fallback)

_OAPI = "https://api.dingtalk.com"
_TOKEN_URL = f"{_OAPI}/v1.0/oauth2/accessToken"
_OTO_URL = f"{_OAPI}/v1.0/robot/oToMessages/batchSend"
_UPLOAD_URL = "https://oapi.dingtalk.com/media/upload"   # classic media upload (v1.0 token works)
_DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


@register("channel", "dingtalk_file")
class DingtalkFileChannel(Channel):
    name = "dingtalk_file"

    def is_enabled(self, config) -> bool:
        # piggybacks on the card's enterprise-robot creds; own toggle defaults on.
        # web_reader (if configured) supersedes the docx file — the 详解 rides the card's per-row
        # link instead, so we don't also spam a separate file message (his stated dislike).
        if getattr(config.channels, "web_reader", None) is not None:
            return False
        return bool(config.channels.dingtalk_file and config.channels.dingtalk_card is not None)

    def send(self, digest: Digest, ctx: RunContext) -> bool:
        cfg = ctx.config.channels.dingtalk_card
        if cfg is None:
            return False
        creds = cfg.resolved()
        missing = cfg.missing(("client_id", "client_secret", "robot_code", "user_id"))
        if missing:
            ctx.log.warn("dingtalk_file disabled — missing creds", missing=missing)
            return False
        md = (digest.markdown or "").strip()
        if not md:
            return False

        session = requests.Session()
        session.trust_env = False   # DingTalk is domestic — never via the proxy
        try:
            token = self._token(session, creds)
        except Exception as e:  # noqa: BLE001
            ctx.log.warn("dingtalk_file token failed", error=repr(e)[:160])
            return False

        # UPPER BOUND: docx file. LOWER BOUND (proven): chunked markdown. Never falls through.
        try:
            if self._send_docx(session, token, creds, digest, md, ctx):
                return True
            ctx.log.warn("dingtalk_file: docx path returned false → markdown fallback")
        except Exception as e:  # noqa: BLE001
            ctx.log.warn("dingtalk_file: docx error → markdown fallback", error=repr(e)[:160])
        return self._send_markdown(session, token, creds, md, ctx)

    def _token(self, session, creds) -> str:
        r = session.post(_TOKEN_URL, timeout=20,
                         json={"appKey": creds["client_id"], "appSecret": creds["client_secret"]})
        r.raise_for_status()
        return r.json()["accessToken"]

    def _send_docx(self, session, token, creds, digest, md, ctx) -> bool:
        from ._docx_render import markdown_to_docx   # lazy — missing python-docx → fallback
        data = markdown_to_docx(md)
        up = session.post(_UPLOAD_URL, params={"access_token": token, "type": "file"},
                          files={"media": (f"digest-{digest.date}.docx", data, _DOCX_MIME)}, timeout=30)
        media_id = (up.json() if up.content else {}).get("media_id")
        if up.status_code != 200 or not media_id:
            ctx.log.warn("dingtalk_file upload rejected", status=up.status_code, body=(up.text or "")[:300])
            return False
        param = {"mediaId": media_id, "fileName": f"Agent-Radar-{digest.date}-详解.docx", "fileType": "docx"}
        body = {"robotCode": creds.get("robot_code"), "userIds": [creds["user_id"]],
                "msgKey": "sampleFile", "msgParam": json.dumps(param, ensure_ascii=False)}
        r = session.post(_OTO_URL, json=body, timeout=20,
                         headers={"x-acs-dingtalk-access-token": token, "Content-Type": "application/json"})
        data_ = r.json() if r.content else {}
        if r.status_code == 200 and not data_.get("code"):
            ctx.log.info("dingtalk_file docx delivered (1v1)", bytes=len(data))
            return True
        ctx.log.warn("dingtalk_file sampleFile rejected", status=r.status_code,
                     code=data_.get("code"), body=(r.text or "")[:300])
        return False

    def _send_markdown(self, session, token, creds, md, ctx) -> bool:
        parts = _chunk(md)
        ok_all = True
        for i, part in enumerate(parts):
            title = f"Agent Radar 详解 ({i + 1}/{len(parts)})" if len(parts) > 1 else "Agent Radar 详解"
            body = {"robotCode": creds.get("robot_code"), "userIds": [creds["user_id"]],
                    "msgKey": "sampleMarkdown", "msgParam": json.dumps({"title": title, "text": part}, ensure_ascii=False)}
            try:
                r = session.post(_OTO_URL, json=body, timeout=20,
                                 headers={"x-acs-dingtalk-access-token": token, "Content-Type": "application/json"})
                if r.status_code != 200 or (r.json() if r.content else {}).get("code"):
                    ok_all = False
                    ctx.log.warn("dingtalk_file md part rejected", part=i + 1, status=r.status_code, body=(r.text or "")[:200])
            except Exception as e:  # noqa: BLE001
                ok_all = False
                ctx.log.warn("dingtalk_file md part failed", part=i + 1, error=repr(e)[:140])
            if len(parts) > 1:
                time.sleep(0.5)
        if ok_all:
            ctx.log.info("dingtalk_file markdown fallback delivered (1v1)", parts=len(parts))
        return ok_all
