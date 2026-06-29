"""DingTalk interactive-card channel — per-item 👍/👎 cards (Phase A).

Delivers deep-read items as interactive cards to the user 1-on-1 via the robot. A 👍/👎 tap
fires a `request` callback that — and this is the whole point — routes to the **Stream** long
connection (caught by `radar --mode serve`), which writes feedback through the SAME
`record_feedback` as `radar mark`. The markdown `dingtalk` channel stays as a fallback.
Secrets come ONLY from env. No proxy (DingTalk is domestic).

API: POST {OAPI}/v1.0/card/instances/createAndDeliver  (卡片平台·创建并投递，高级版)
  body: cardTemplateId = a template BUILT IN open-dev.dingtalk.com/fe/card AND associated with
        THIS app at creation time (ends in `.schema`) — the only path whose request-button
        callback reaches Stream; the old robot interactiveCards/send route black-holes the
        callback (HTTP only), confirmed by real testing.
        + callbackType="STREAM"  → callback lands on /v1.0/card/instances/callback (registered in serve)
        + cardData.cardParamMap  → fills the template variables (title/url/reason/status)
        + openSpaceId="dtv1.card//IM_ROBOT.{userId}" + imRobotOpenDeliverModel{robotCode} → 1v1 push
        + outTrackId="{date}:{id}" → ties the click back to the item

The template's variable names MUST match cardParamMap's keys exactly, and its two buttons must be
`request` actions carrying params {"action":"vote","vote":"up"/"down"} — a silent-failure contract
(decisions.md). createAndDeliver has NO inline-content mode: the card content lives in the template,
we only pass variable values.

A0 scope: deliver ONE card (the first deep-read item) to validate the loop end to end. A1 lifts
the cap to all deep-read items and wires this into deliver.py.
"""
from __future__ import annotations

import json
import time
from typing import Any

import requests

from ..core.models import Digest, Item, RunContext
from ..core.ports import Channel
from ..core.registry import register

_OAPI = "https://api.dingtalk.com"
_TOKEN_URL = f"{_OAPI}/v1.0/oauth2/accessToken"
_SEND_URL = f"{_OAPI}/v1.0/card/instances/createAndDeliver"
_DEGRADE_PREFIX = "（原文"   # deepread's "no body" marker — skip those


def _essence(item: Item, limit: int = 120) -> str:
    return ((item.reason or item.title or "").strip())[:limit]


def build_card_param_map(item: Item) -> dict:
    """The template-variable values (createAndDeliver fills these into the template). Keys MUST
    match the template's variable names exactly — the A0 template (imported, helloworld-derived)
    has ONE `markdown` variable: a bold clickable title link + the one-line reason. All values
    are strings (cardParamMap requires strings). A1 can split this into title/url/reason/status."""
    title = (item.title or "")[:120]
    url = item.url or ""
    reason = _essence(item)
    head = f"**[{title}]({url})**" if url else f"**{title}**"
    return {"markdown": f"{head}\n\n{reason}"}


def build_send_request(date: str, item: Item, creds: dict) -> dict:
    """Body for /v1.0/card/instances/createAndDeliver (1v1 robot push, STREAM callback).
    outTrackId={date}:{id} ties a click back to the item; the 👍/👎 request buttons live in the
    template (params {"vote": up/down}) and fire the Stream callback. Field shapes verified against
    DingTalk's own createAndDeliver codegen for this template — note openSpaceId uses LOWERCASE
    `im_robot` while imRobotOpenDeliverModel.spaceType is UPPERCASE `IM_ROBOT` (silent-fail trap)."""
    uid = creds["user_id"]
    return {
        "userId": uid,
        "userIdType": 1,
        "cardTemplateId": creds.get("card_template_id"),
        "outTrackId": f"{date}:{item.id}",
        "callbackType": "STREAM",
        "cardData": {"cardParamMap": build_card_param_map(item)},
        "imRobotOpenDeliverModel": {"spaceType": "IM_ROBOT", "robotCode": creds.get("robot_code")},
        "imRobotOpenSpaceModel": {"supportForward": True},
        "openSpaceId": f"dtv1.card//im_robot.{uid}",
    }


def deep_read_items(digest: Digest) -> list[Item]:
    """Items worth a card: those with a real 详解 (skip the degrade marker)."""
    return [it for it in digest.items
            if it.explain_zh and not it.explain_zh.startswith(_DEGRADE_PREFIX)]


@register("channel", "dingtalk_card")
class DingtalkCardChannel(Channel):
    name = "dingtalk_card"
    a0_one_card = True   # A0: deliver a single card to validate the loop; A1 sets this False

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

        items = deep_read_items(digest)
        if not items:
            ctx.log.warn("dingtalk_card: no deep-read items to deliver")
            return False
        if self.a0_one_card:
            items = items[:1]

        session = requests.Session()
        session.trust_env = False   # DingTalk is domestic — never via the proxy
        try:
            token = self._token(session, creds)
        except Exception as e:  # noqa: BLE001
            ctx.log.warn("dingtalk_card token failed", error=repr(e)[:160])
            return False

        ok = 0
        for it in items:
            if self._deliver_one(session, token, creds, digest.date, it, ctx):
                ok += 1
            time.sleep(0.3)
        ctx.log.info("dingtalk_card delivered", cards=ok, attempted=len(items))
        return ok > 0

    def _token(self, session: requests.Session, creds: dict) -> str:
        r = session.post(_TOKEN_URL, timeout=20,
                         json={"appKey": creds["client_id"], "appSecret": creds["client_secret"]})
        r.raise_for_status()
        return r.json()["accessToken"]

    def _deliver_one(self, session, token, creds, date, item: Item, ctx) -> bool:
        try:
            r = session.post(_SEND_URL, json=build_send_request(date, item, creds), timeout=20,
                             headers={"x-acs-dingtalk-access-token": token,
                                      "Content-Type": "application/json"})
            data = r.json() if r.content else {}
            if r.status_code == 200 and not data.get("code"):
                return True
            ctx.log.warn("dingtalk_card rejected", status=r.status_code,
                         code=data.get("code"), errmsg=data.get("message"),
                         body=(r.text or "")[:300])
            return False
        except Exception as e:  # noqa: BLE001
            ctx.log.warn("dingtalk_card deliver failed", id=item.id, error=repr(e)[:160])
            return False
