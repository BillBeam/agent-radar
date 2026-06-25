"""DingTalk channel — custom-robot webhook, markdown message, HMAC 加签.

Enabled only when config.channels.dingtalk.webhook is set (you paste it in chat;
I wire it). Long digests are split on section boundaries into multiple messages.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import re
import time
import urllib.parse

import requests

from ..core.models import Digest, RunContext
from ..core.ports import Channel
from ..core.registry import register

LIMIT_BYTES = 19000  # DingTalk markdown body limit is 20000 BYTES (CJK = 3 bytes/char)


def _sign(webhook: str, secret: str) -> str:
    ts = str(round(time.time() * 1000))
    string_to_sign = f"{ts}\n{secret}".encode("utf-8")
    digest = hmac.new(secret.encode("utf-8"), string_to_sign, hashlib.sha256).digest()
    sign = urllib.parse.quote_plus(base64.b64encode(digest))
    sep = "&" if "?" in webhook else "?"
    return f"{webhook}{sep}timestamp={ts}&sign={sign}"


def _bytes(s: str) -> int:
    return len(s.encode("utf-8"))


def _hard_split(text: str, limit: int) -> list[str]:
    """Last resort: split a single oversized block on char boundaries by byte budget."""
    out: list[str] = []
    cur: list[str] = []
    cur_b = 0
    for ch in text:
        b = len(ch.encode("utf-8"))
        if cur and cur_b + b > limit:
            out.append("".join(cur))
            cur, cur_b = [], 0
        cur.append(ch)
        cur_b += b
    if cur:
        out.append("".join(cur))
    return out


def _chunk(md: str, limit: int = LIMIT_BYTES) -> list[str]:
    """Split on section (##) / item (###) boundaries, packing chunks under the
    DingTalk byte limit; hard-split any single block that still exceeds it."""
    if _bytes(md) <= limit:
        return [md]
    blocks = [b for b in re.split(r"(?=^#{2,3} )", md, flags=re.M) if b]
    chunks: list[str] = []
    cur = ""
    for b in blocks:
        if cur and _bytes(cur) + _bytes(b) > limit:
            chunks.append(cur)
            cur = b
        else:
            cur += b
    if cur:
        chunks.append(cur)
    final: list[str] = []
    for c in chunks:
        final.extend([c] if _bytes(c) <= limit else _hard_split(c, limit))
    return final


@register("channel", "dingtalk")
class DingtalkChannel(Channel):
    name = "dingtalk"

    def is_enabled(self, config) -> bool:
        return config.channels.dingtalk is not None

    def send(self, digest: Digest, ctx: RunContext) -> bool:
        cfg = ctx.config.channels.dingtalk
        if cfg is None:
            return False
        url = _sign(cfg.webhook, cfg.secret) if cfg.secret else cfg.webhook
        parts = _chunk(digest.markdown_brief or digest.markdown)
        session = requests.Session()
        session.trust_env = False
        ok_all = True
        for idx, part in enumerate(parts):
            title = f"Agent Radar {digest.date}" + (f" ({idx + 1}/{len(parts)})" if len(parts) > 1 else "")
            payload = {"msgtype": "markdown", "markdown": {"title": title, "text": part}}
            try:
                r = session.post(url, json=payload, timeout=20)
                data = r.json()
                if data.get("errcode") != 0:
                    ok_all = False
                    ctx.log.warn("dingtalk rejected", errcode=data.get("errcode"),
                                 errmsg=data.get("errmsg"))
            except Exception as e:  # noqa: BLE001
                ok_all = False
                ctx.log.warn("dingtalk send failed", error=repr(e)[:140])
            if len(parts) > 1:
                time.sleep(0.5)  # be gentle with the per-robot rate limit
        if ok_all:
            ctx.log.info("dingtalk sent", parts=len(parts))
        return ok_all
