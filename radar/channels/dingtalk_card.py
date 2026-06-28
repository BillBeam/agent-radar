"""DingTalk interactive-card channel — per-item 👍/👎 cards (Phase A).

Delivers deep-read items as interactive cards to the user 1-on-1 (IM_ROBOT). The 👍/👎
buttons fire STREAM callbacks, caught by `radar --mode serve`, which writes feedback through
the SAME `record_feedback` as `radar mark`. The markdown `dingtalk` channel stays as a
configurable fallback. Secrets come ONLY from env. No proxy (DingTalk is domestic).

Confirmed API (dingtalk-stream-sdk-python):
  token    POST {OAPI}/v1.0/oauth2/accessToken           {appKey, appSecret} → accessToken
  deliver  POST {OAPI}/v1.0/card/instances/createAndDeliver  (header x-acs-dingtalk-access-token)
           body: cardTemplateId + outTrackId + cardData.cardParamMap(str values) + callbackType
           + openSpaceId="dtv1.card//IM_ROBOT.{userId}" + imRobotOpenSpaceModel
           + imRobotOpenDeliverModel{spaceType:"IM_ROBOT", robotCode}
The cardParamMap keys MUST match the template's variable names (the 命门 — see CARD_VARS).

A0 scope: deliver ONE card (the first deep-read item) to validate the template lifeline end
to end. A1 lifts the cap to all deep-read items and wires this into deliver.py.
"""
from __future__ import annotations

import time
from typing import Any

import requests

from ..core.models import Digest, Item, RunContext
from ..core.ports import Channel
from ..core.registry import register

_OAPI = "https://api.dingtalk.com"
_TOKEN_URL = f"{_OAPI}/v1.0/oauth2/accessToken"
_DELIVER_URL = f"{_OAPI}/v1.0/card/instances/createAndDeliver"

# Template variable names the card platform template MUST define (keep in sync with the template).
CARD_VARS = ("title", "url", "reason", "status")
_DEGRADE_PREFIX = "（原文"   # deepread's "no body" marker — skip those (nothing to read/vote on)


def _essence(item: Item, limit: int = 80) -> str:
    return ((item.reason or item.title or "").strip())[:limit]


def card_param_map(item: Item) -> dict[str, str]:
    """Template variable values (all strings). `title`/`url` let the template render the title as
    a clickable link so the user can read before voting; `status` is filled to 已记录 on click."""
    return {
        "title": (item.title or "")[:80],
        "url": item.url or "",
        "reason": _essence(item),
        "status": "",
    }


def build_card_request(date: str, item: Item, creds: dict) -> dict:
    """The createAndDeliver body for a 1v1 IM_ROBOT card. outTrackId={date}:{id} ties a click
    back to the item; the 👍/👎 buttons (defined in the template) carry the vote in their params."""
    deliver_model: dict[str, Any] = {"spaceType": "IM_ROBOT"}
    if creds.get("robot_code"):
        deliver_model["robotCode"] = creds["robot_code"]
    return {
        "cardTemplateId": creds["card_template_id"],
        "outTrackId": f"{date}:{item.id}",
        "cardData": {"cardParamMap": {k: str(v) for k, v in card_param_map(item).items()}},
        "callbackType": "STREAM",
        "openSpaceId": f"dtv1.card//IM_ROBOT.{creds['user_id']}",
        "imRobotOpenSpaceModel": {"supportForward": True},
        "imRobotOpenDeliverModel": deliver_model,
    }


def deep_read_items(digest: Digest) -> list[Item]:
    """Items worth a card: those with a real 详解 (skip the degrade marker)."""
    return [it for it in digest.items
            if it.explain_zh and not it.explain_zh.startswith(_DEGRADE_PREFIX)]


@register("channel", "dingtalk_card")
class DingtalkCardChannel(Channel):
    name = "dingtalk_card"
    a0_one_card = True   # A0: deliver a single card to validate the lifeline; A1 sets this False

    def is_enabled(self, config: Any) -> bool:
        return config.channels.dingtalk_card is not None

    def send(self, digest: Digest, ctx: RunContext) -> bool:
        cfg = ctx.config.channels.dingtalk_card
        if cfg is None:
            return False
        creds = cfg.resolved()
        missing = cfg.missing(("client_id", "client_secret", "card_template_id", "user_id"))
        if missing:
            ctx.log.warn("dingtalk_card disabled — missing creds/ids", missing=missing,
                         hint="set env DINGTALK_CLIENT_ID/SECRET (+ user_id, card_template_id)")
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
            r = session.post(_DELIVER_URL, json=build_card_request(date, item, creds), timeout=20,
                             headers={"x-acs-dingtalk-access-token": token,
                                      "Content-Type": "application/json"})
            data = r.json() if r.content else {}
            if r.status_code == 200 and not data.get("code"):
                return True
            ctx.log.warn("dingtalk_card rejected", status=r.status_code,
                         code=data.get("code"), msg=data.get("message"))
            return False
        except Exception as e:  # noqa: BLE001
            ctx.log.warn("dingtalk_card deliver failed", id=item.id, error=repr(e)[:160])
            return False
