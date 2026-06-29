"""DingTalk interactive-card channel — per-item 👍/👎 voting cards (Phase A).

Delivers ONE compact card per deep-read item to the user 1-on-1 via the robot. A 👍/👎 tap fires a
`request` callback that — and this is the whole point — routes to the **Stream** long connection
(caught by `radar --mode serve`), which writes feedback through the SAME `record_feedback` as
`radar mark`. This card is the **voting layer**; the markdown `dingtalk` channel stays on as the
**reading layer** (clickable links + full 详解). They line up 1:1 via the `[N]` number.
Secrets come ONLY from env. No proxy (DingTalk is domestic).

API: POST {OAPI}/v1.0/card/instances/createAndDeliver  (卡片平台·创建并投递，高级版)
  body: cardTemplateId = a template BUILT IN open-dev.dingtalk.com/fe/card AND associated with THIS
        app at creation time (ends in `.schema`) — the only path whose request-button callback
        reaches Stream; the old robot interactiveCards/send route black-holes the callback (HTTP).
        + callbackType="STREAM"  → callback lands on /v1.0/card/instances/callback (registered in serve)
        + cardData.cardParamMap  → fills the template's ONE `markdown` variable (string, plain text)
        + openSpaceId="dtv1.card//im_robot.{userId}" (LOWERCASE) + imRobotOpenDeliverModel{robotCode}
        + outTrackId="{date}:{id}" → ties the click back to the item

The template var name MUST match cardParamMap's key (`markdown`) exactly, and its two buttons must be
`request` actions carrying params {"vote":"up"/"down"} — a silent-failure contract (decisions.md).
The `markdown` variable carries a PLAIN-TEXT compact line `[N] 🆕/📚 Title — reason` (no clickable
link: the template's BaseText renders plain text; the markdown brief is the reading layer).
"""
from __future__ import annotations

import os
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
_TITLE_MAX = 80
_REASON_MAX = 60


def _clip(s: str | None, n: int) -> str:
    """Trim+cap to n chars with an ellipsis — keep the card a one/two-line scan."""
    s = (s or "").strip()
    return (s[: n - 1] + "…") if len(s) > n else s


def build_card_param_map(item: Item, num: int, marker: str) -> dict:
    """The template's `markdown` variable value (string): the compact voting line
    `[N] 🆕/📚 Title — reason`. [N] = the item's number in the canonical display order (same as the
    brief); marker = 🆕 (today-new) / 📚 (backfill). Plain text, kept short (decisions.md: no
    clickable link — that lives in the markdown reading layer)."""
    body = f"[{num}] {marker} {_clip(item.title, _TITLE_MAX)}"
    reason = _clip(item.reason, _REASON_MAX)
    if reason:
        body += f" — {reason}"
    return {"markdown": body}


def build_send_request(date: str, item: Item, num: int, marker: str, creds: dict) -> dict:
    """Body for /v1.0/card/instances/createAndDeliver (1v1 robot push, STREAM callback).
    outTrackId={date}:{id} ties a click back to the item; the 👍/👎 request buttons live in the
    template (params {"vote": up/down}) and fire the Stream callback. Field shapes verified against
    DingTalk's own createAndDeliver codegen — note openSpaceId uses LOWERCASE `im_robot` while
    imRobotOpenDeliverModel.spaceType is UPPERCASE `IM_ROBOT` (silent-fail trap)."""
    uid = creds["user_id"]
    out_track = f"{date}:{item.id}"
    nonce = os.getenv("DINGTALK_OUTTRACK_NONCE")   # opt-in: force a fresh card instance (re-deliver/re-test)
    if nonce:
        out_track += f":{nonce}"
    return {
        "userId": uid,
        "userIdType": 1,
        "cardTemplateId": creds.get("card_template_id"),
        "outTrackId": out_track,
        "callbackType": "STREAM",
        "cardData": {"cardParamMap": build_card_param_map(item, num, marker)},
        "imRobotOpenDeliverModel": {"spaceType": "IM_ROBOT", "robotCode": creds.get("robot_code")},
        "imRobotOpenSpaceModel": {"supportForward": True},
        "openSpaceId": f"dtv1.card//im_robot.{uid}",
    }


def _canonical_order(items: list[Item]) -> list[Item]:
    """Mirror synthesize.py's display order (fresh first, then backfill) WITHOUT importing/altering
    synthesize — so a card's [N] equals the brief's [N]. Within each group the input order
    (rerank / items.json order) is preserved."""
    fresh = [it for it in items if it.published_at is not None]
    backfill = [it for it in items if it.published_at is None]
    return fresh + backfill


def item_numbering(items: list[Item]) -> dict:
    """{item.id: (N, marker)} — N is the 1-based position in the canonical display order, marker is
    🆕 (today-new, has published_at) / 📚 (backfill, undated). Built from the FULL list so the
    card's [N]+marker line up 1:1 with the brief, even though the deep-read items that actually get
    cards are a non-contiguous subset."""
    return {it.id: (n, "🆕" if it.published_at is not None else "📚")
            for n, it in enumerate(_canonical_order(items), 1)}


def deep_read_items(digest: Digest) -> list[Item]:
    """Items worth a card: those with a real 详解 (skip the degrade marker)."""
    return [it for it in digest.items
            if it.explain_zh and not it.explain_zh.startswith(_DEGRADE_PREFIX)]


@register("channel", "dingtalk_card")
class DingtalkCardChannel(Channel):
    name = "dingtalk_card"
    a0_one_card = False   # A1: deliver every deep-read item (A0 set this True for the 1-card gate)

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

        numbering = item_numbering(digest.items)   # [N]+marker over the FULL list (matches the brief)
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
            num, marker = numbering.get(it.id, (0, "🆕"))
            if self._deliver_one(session, token, creds, digest.date, it, num, marker, ctx):
                ok += 1
            time.sleep(0.3)
        ctx.log.info("dingtalk_card delivered", cards=ok, attempted=len(items))
        return ok > 0

    def _token(self, session: requests.Session, creds: dict) -> str:
        r = session.post(_TOKEN_URL, timeout=20,
                         json={"appKey": creds["client_id"], "appSecret": creds["client_secret"]})
        r.raise_for_status()
        return r.json()["accessToken"]

    def _deliver_one(self, session, token, creds, date, item: Item, num: int, marker: str, ctx) -> bool:
        try:
            r = session.post(_SEND_URL, json=build_send_request(date, item, num, marker, creds), timeout=20,
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
