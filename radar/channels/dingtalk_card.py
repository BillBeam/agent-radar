"""DingTalk interactive-card channel — ONE list card per digest (Phase A1 redesign).

Delivers a SINGLE interactive card whose Loop renders one row per deep-read item:
`[N] 🆕/📚 + bold Chinese reason + small title + compact 👍/👎`. One message ⇒ order is
guaranteed and the chat isn't spammed with N separate cards. The per-row vote travels in each
button's **actionId** as `up_<id>` / `down_<id>` — verified on DingTalk: `${loop.x}` resolves in
a loop button's actionId (the callback returns it in `cardPrivateData.actionIds`), but NOT in its
params. `serve` splits that token back into vote + item_id. The markdown `dingtalk` channel stays
as the reading layer; the two line up by `[N]`. Secrets come ONLY from env. No proxy (domestic).

Template (card-builder, im/3.0.0): a `loopArray` variable `items` (schema: num/marker/title/
reason/up_token/down_token) drives a Loop; each row binds `${loop.<field>}`. Loop-context
bindings survive import (unlike a global `${var}`, which the builder clears) — so the template is
built+published once, no GUI re-bind. cardParamMap values must be strings ⇒ `items` is a JSON string.
"""
from __future__ import annotations

import json
import os
from typing import Any

import requests

from ..core.models import Digest, Item, RunContext
from ..core.ports import Channel
from ..core.registry import register

_OAPI = "https://api.dingtalk.com"
_TOKEN_URL = f"{_OAPI}/v1.0/oauth2/accessToken"
_SEND_URL = f"{_OAPI}/v1.0/card/instances/createAndDeliver"
_DEGRADE_PREFIX = "（原文"   # deepread's "no body" marker — skip those
_REASON_MAX = 70


def _clip(s: str | None, n: int) -> str:
    s = (s or "").strip()
    return (s[: n - 1] + "…") if len(s) > n else s


def _canonical_order(items: list[Item]) -> list[Item]:
    """Mirror synthesize.py's display order (fresh→backfill) WITHOUT importing/altering synthesize,
    so a row's [N] equals the brief's. Within each group input order is preserved."""
    fresh = [it for it in items if it.published_at is not None]
    backfill = [it for it in items if it.published_at is None]
    return fresh + backfill


def item_numbering(items: list[Item]) -> dict:
    """{item.id: (N, marker)} — N is the 1-based position in canonical order, marker 🆕/📚. Built
    over the FULL list so rows line up 1:1 with the brief even though deep-read items are a subset."""
    return {it.id: (n, "🆕" if it.published_at is not None else "📚")
            for n, it in enumerate(_canonical_order(items), 1)}


def deep_read_items(digest: Digest) -> list[Item]:
    """Items worth a row: those with a real 详解 (skip the degrade marker)."""
    return [it for it in digest.items
            if it.explain_zh and not it.explain_zh.startswith(_DEGRADE_PREFIX)]


def build_items(digest: Digest, ctx: RunContext | None = None) -> list[dict]:
    """The list card's rows — one per item in canonical display order (contiguous [1..N]), ONE
    message. The template's Markdown component renders `[${loop.num}] ${loop.marker} ${loop.reason}`,
    so `reason` carries the 中文一句话 (critic ⚠️可跳过 folded in when flagged) + the article url on
    its own line — DingTalk's card Markdown auto-links a bare url, but does NOT render `[text](url)`
    inline links or `**bold**` (both verified on-device). Plus `up_/down_` vote tokens. All strings."""
    numbering = item_numbering(digest.items)
    critic = ((getattr(ctx, "stats", None) or {}).get("critic") or {}) if ctx else {}
    rows = []
    for it in _canonical_order(digest.items):
        num, marker = numbering.get(it.id, (0, "🆕"))
        v = critic.get(it.id) or {}
        reason = it.reason or ""
        if v.get("skip"):
            label = "可跳过" if v.get("conf") == "high" else "疑似可跳过"
            why = (v.get("why") or "").strip()
            reason = f"⚠️ {label}" + (f" · {why}" if why else "") + f" · {reason}"
        body = _clip(reason, _REASON_MAX)
        if it.url:
            body = f"{body}\n{it.url}"   # bare url on its own line — the Markdown component auto-links it
        rows.append({
            "num": str(num),
            "marker": marker,
            "reason": body,
            "up_token": f"up_{it.id}",
            "down_token": f"down_{it.id}",
        })
    return rows


def build_list_request(date: str, rows: list[dict], creds: dict) -> dict:
    """Body for /v1.0/card/instances/createAndDeliver — ONE list card. cardParamMap.items is the
    JSON-string of the rows (loopArray). outTrackId={date}:list (the whole digest = one card; the
    per-row id comes from the clicked button's actionId, not outTrackId). Optional nonce forces a
    fresh instance for re-delivery/testing."""
    uid = creds["user_id"]
    out_track = f"{date}:list"
    nonce = os.getenv("DINGTALK_OUTTRACK_NONCE")
    if nonce:
        out_track += f":{nonce}"
    return {
        "userId": uid,
        "userIdType": 1,
        "cardTemplateId": creds.get("card_template_id"),
        "outTrackId": out_track,
        "callbackType": "STREAM",
        "cardData": {"cardParamMap": {"items": json.dumps(rows, ensure_ascii=False)}},
        "imRobotOpenDeliverModel": {"spaceType": "IM_ROBOT", "robotCode": creds.get("robot_code")},
        "imRobotOpenSpaceModel": {"supportForward": True},
        "openSpaceId": f"dtv1.card//im_robot.{uid}",
    }


@register("channel", "dingtalk_card")
class DingtalkCardChannel(Channel):
    name = "dingtalk_card"

    def is_enabled(self, config: Any) -> bool:
        return config.channels.dingtalk_card is not None

    def send(self, digest: Digest, ctx: RunContext) -> bool:
        cfg = ctx.config.channels.dingtalk_card
        if cfg is None:
            return False
        creds = cfg.resolved()
        missing = cfg.missing(("client_id", "client_secret", "card_template_id", "robot_code", "user_id"))
        if missing:
            ctx.log.warn("dingtalk_card disabled — missing creds/ids", missing=missing,
                         hint="env DINGTALK_CLIENT_ID/SECRET + CARD_TEMPLATE_ID(.schema) + ROBOT_CODE + USER_ID")
            return False

        rows = build_items(digest, ctx)
        if not rows:
            ctx.log.warn("dingtalk_card: no items to deliver")
            return False

        session = requests.Session()
        session.trust_env = False   # DingTalk is domestic — never via the proxy
        try:
            token = self._token(session, creds)
        except Exception as e:  # noqa: BLE001
            ctx.log.warn("dingtalk_card token failed", error=repr(e)[:160])
            return False

        # Reading is folded into each card row (title + reason + ⚠️) → ONE message, no separate brief.
        ok = self._deliver(session, token, creds, digest.date, rows, ctx)
        ctx.log.info("dingtalk_card list delivered", rows=len(rows), ok=ok)
        return ok

    def _token(self, session: requests.Session, creds: dict) -> str:
        r = session.post(_TOKEN_URL, timeout=20,
                         json={"appKey": creds["client_id"], "appSecret": creds["client_secret"]})
        r.raise_for_status()
        return r.json()["accessToken"]

    def _deliver(self, session, token, creds, date, rows, ctx) -> bool:
        try:
            r = session.post(_SEND_URL, json=build_list_request(date, rows, creds), timeout=20,
                             headers={"x-acs-dingtalk-access-token": token,
                                      "Content-Type": "application/json"})
            data = r.json() if r.content else {}
            if r.status_code == 200 and not data.get("code"):
                return True
            ctx.log.warn("dingtalk_card rejected", status=r.status_code,
                         code=data.get("code"), errmsg=data.get("message"), body=(r.text or "")[:300])
            return False
        except Exception as e:  # noqa: BLE001
            ctx.log.warn("dingtalk_card deliver failed", error=repr(e)[:160])
            return False
