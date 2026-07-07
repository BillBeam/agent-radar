"""DingTalk Stream listener — the first long-running, two-way component.

Holds a persistent connection; when the user taps 👍/👎 on a card, the callback writes feedback
through the SAME `record_feedback` as `radar mark` (so the store/shape can't drift) and updates
the card to「已记录」. It also logs the sender's userId on any chat message (so the user gets their
userId just by messaging the bot). Strips ANTHROPIC_API_KEY (no LLM here). No run-lock — it only
ever writes feedback, never the pipeline's seen/digest state.

The pure helpers (parse_card_callback / item_snapshot / _card_update_response) carry the logic and
are unit-tested; the SDK wiring in run_listener is lazy-imported and validated by the real A0 run.
"""
from __future__ import annotations

import json
import os
import signal
from typing import Optional

from ..core.config import DingtalkCardConfig, Paths, RadarConfig, load_config
from ..core.feedback import record_feedback
from ..core.io import read_json
from ..obs import Logger


_VOTES = ("up", "down")

# ── Inbound contract ──────────────────────────────────────────────────────────────────────────
# The ONLY thing that crosses from "platform" into "core" is a normalized vote event:
#     InboundVote = {"date": str, "item_id": str, "vote": "up"|"down", "user_id": str|None}
# `parse_card_callback` is the SOLE DingTalk-frame-aware code; everything downstream (item_snapshot,
# record_feedback) works off this dict, never a raw frame. Adding another platform later = add one
# parser that emits InboundVote — core stays untouched. (No multi-platform abstraction beyond this.)
_INBOUND_KEYS = ("date", "item_id", "vote", "user_id")


def _extract_vote(content) -> Optional[str]:
    """Find the up/down vote across the shapes a `actionType:request` + `value` button can
    produce. DingTalk's `content` is a JSON STRING → cardPrivateData{actionIds, params}; the
    button's value may land in params (value/vote/action), in cardPrivateData.value, at
    content.value, or as the actionId itself. We probe all of them (the real shape is logged
    raw on the first click and then pinned). Returns 'up'/'down' or None."""
    if isinstance(content, str):
        s = content.strip().strip('"')
        if s.lower() in _VOTES:                  # the value passed straight through as content
            return s.lower()
        try:
            content = json.loads(content)
        except (ValueError, TypeError):
            return None
    if not isinstance(content, dict):
        return None
    cpd = content.get("cardPrivateData") or {}
    params = cpd.get("params") or {}
    candidates = [params.get("value"), params.get("vote"), params.get("action"),
                  cpd.get("value"), content.get("value")]
    candidates += list(cpd.get("actionIds") or [])     # e.g. an actionId literally "up"/"down"
    for c in candidates:
        if isinstance(c, str) and c.strip().lower() in _VOTES:
            return c.strip().lower()
    return None


def _card_private(content) -> dict:
    """content (a JSON STRING for DingTalk) → its cardPrivateData dict {actionIds, params}, or {}."""
    if isinstance(content, str):
        try:
            content = json.loads(content)
        except (ValueError, TypeError):
            return {}
    return (content.get("cardPrivateData") or {}) if isinstance(content, dict) else {}


def parse_card_callback(data: dict) -> Optional[dict]:
    """The platform→core boundary → an **InboundVote** {date, item_id, vote, user_id} (see
    _INBOUND_KEYS), or None. Two card shapes, in priority order:
    • LIST card (current): the clicked row's button actionId is `up_<id>` / `down_<id>` — vote and
      item_id ride in the actionId (verified: `${loop.x}` resolves in a loop button's actionId but
      NOT its params); date is outTrackId's first segment ('{date}:list').
    • PER-ITEM card (back-compat, old cards still in chat): outTrackId='{date}:{item_id}' (+ optional
      nonce), vote recovered by _extract_vote.
    Accepts raw frame keys or the normalized ones from _normalize_callback. The ONLY DingTalk-aware code."""
    if not isinstance(data, dict):
        return None
    out_track_id = data.get("outTrackId") or ""
    user_id = data.get("userId")
    cpd = _card_private(data.get("content"))
    # list card: actionId carries vote + item_id as `up_<id>` / `down_<id>`
    for aid in (cpd.get("actionIds") or []):
        if isinstance(aid, str):
            for v in _VOTES:
                if aid.startswith(v + "_") and len(aid) > len(v) + 1:
                    return {"date": out_track_id.split(":", 1)[0], "item_id": aid[len(v) + 1:],
                            "vote": v, "user_id": user_id}
    # per-item card: outTrackId '{date}:{item_id}' (+ optional nonce), vote via _extract_vote
    if ":" in out_track_id:
        parts = out_track_id.split(":")
        if len(parts) >= 2 and parts[1] != "list":
            vote = _extract_vote(data.get("content"))
            if vote in _VOTES:
                return {"date": parts[0], "item_id": parts[1], "vote": vote, "user_id": user_id}
    return None


def _normalize_callback(raw: dict, sdk) -> dict:
    """Normalize a Stream card-callback frame to {outTrackId, content, userId} the way
    parse_card_callback expects. Prefer the SDK's CardCallbackMessage (it knows the envelope:
    card_instance_id == outTrackId, content == cardPrivateData{...}); fall back to the raw dict
    if the SDK shape differs. Pure-ish: `sdk` is the dingtalk_stream module (or None in tests)."""
    out_id = content = user = None
    try:
        msg = sdk.CardCallbackMessage.from_dict(raw) if sdk else None
        if msg is not None:
            out_id = (getattr(msg, "card_instance_id", None) or getattr(msg, "out_track_id", None)
                      or getattr(msg, "outTrackId", None))
            content = getattr(msg, "content", None)
            user = getattr(msg, "user_id", None) or getattr(msg, "userId", None)
    except Exception:  # noqa: BLE001 — SDK shape drift must never block; raw fallback covers it
        pass
    if not isinstance(raw, dict):
        raw = {}
    return {
        "outTrackId": out_id or raw.get("outTrackId") or raw.get("cardInstanceId"),
        "content": content if content is not None else raw.get("content"),
        "userId": user or raw.get("userId"),
    }


def item_snapshot(date: str, item_id: str) -> dict:
    """Recover the item dict from {date}.items.json (for the feedback content snapshot).
    Falls back to a minimal dict so a vote is still recorded even if the digest is gone."""
    items = read_json(Paths.digests / f"{date}.items.json", []) or []
    for it in items:
        if isinstance(it, dict) and it.get("id") == item_id:
            return it
    return {"id": item_id}


def _card_update_response(vote: str) -> dict:
    """Ack-response that flips the card to a 已记录 state (DingTalk applies the update from the ack)."""
    mark = "👍" if vote == "up" else "👎"
    return {
        "cardUpdateOptions": {"updateCardDataByKey": True},
        "userPrivateData": {"cardParamMap": {"status": f"✅ 已记录 {mark}"}},
    }


def run_listener(config: Optional[RadarConfig] = None) -> int:
    config = config or load_config()
    log = Logger("serve", log_path=Paths.state / "radar.log", echo=True)

    cfg = config.channels.dingtalk_card or DingtalkCardConfig()
    creds = cfg.resolved()
    if not creds.get("client_id") or not creds.get("client_secret"):
        log.error("serve needs DINGTALK_CLIENT_ID / DINGTALK_CLIENT_SECRET in env")
        log.close()
        return 1
    try:
        import dingtalk_stream
    except ImportError:
        log.error("serve needs the Stream SDK — `pip install dingtalk-stream`")
        log.close()
        return 1

    os.environ.pop("ANTHROPIC_API_KEY", None)   # serve never calls the LLM

    # Reading-page votes: poll the site's same-origin /votes into the SAME feedback store
    # (daemon thread; silently off unless web_reader + AGENT_RADAR_WEB_SECRET are configured).
    wr = config.channels.web_reader
    if wr is not None:
        try:
            from .webvotes import start_poller
            start_poller(base_url=wr.resolved().get("base_url") or "", log=log)
        except Exception as e:  # noqa: BLE001 — the poller must never block the card listener
            log.warn("web-vote poller failed to start", error=repr(e)[:120])

    credential = dingtalk_stream.Credential(creds["client_id"], creds["client_secret"])
    client = dingtalk_stream.DingTalkStreamClient(credential)

    class CardHandler(dingtalk_stream.CallbackHandler):
        async def process(self, callback):  # noqa: ANN001
            raw = getattr(callback, "data", None)
            try:
                log.info("card callback RAW (A0 — pin the vote field from this)",
                         payload=json.dumps(raw, ensure_ascii=False)[:1200])
                norm = _normalize_callback(raw if isinstance(raw, dict) else {}, dingtalk_stream)
                parsed = parse_card_callback(norm) or parse_card_callback(raw)
                if parsed:
                    record_feedback(parsed["date"], item_snapshot(parsed["date"], parsed["item_id"]),
                                    parsed["vote"])
                    log.info("feedback via card", date=parsed["date"],
                             item_id=parsed["item_id"], vote=parsed["vote"])
                    return dingtalk_stream.AckMessage.STATUS_OK, _card_update_response(parsed["vote"])
                log.warn("card callback unparseable — see RAW above to pin the vote field",
                         keys=list((raw or {}).keys()))
            except Exception as e:  # noqa: BLE001 — one bad callback must not kill the service
                log.error("card callback handler error", error=repr(e)[:200])
            return dingtalk_stream.AckMessage.STATUS_OK, "OK"

    client.register_callback_handler(dingtalk_stream.CallbackHandler.TOPIC_CARD_CALLBACK, CardHandler())
    # robot interactive cards (interactiveCards/send) may route their button callback to a
    # different topic — register the candidates too so a click reveals/uses the right one.
    for extra in ("/v1.0/im/robots/interactiveCards", "/v1.0/im/bot/interactiveCard/callback",
                  "/v1.0/im/robot/interactiveCard/callback"):
        try:
            client.register_callback_handler(extra, CardHandler())
        except Exception as e:  # noqa: BLE001
            log.warn("extra card topic not registered", topic=extra, error=repr(e)[:80])

    # convenience (best-effort): message the bot once → your userId shows up in the log
    try:
        class ChatHandler(dingtalk_stream.ChatbotHandler):
            async def process(self, callback):  # noqa: ANN001
                uid = (getattr(callback, "data", None) or {}).get("senderStaffId")
                log.info("chat message — copy your userId for DINGTALK_USER_ID", userId=uid)
                return dingtalk_stream.AckMessage.STATUS_OK, "OK"
        client.register_callback_handler(dingtalk_stream.ChatbotMessage.TOPIC, ChatHandler())
    except Exception as e:  # noqa: BLE001 — userId capture is optional, never block startup
        log.warn("chat handler not registered (userId capture off)", error=repr(e)[:120])

    def _bye(*_):
        raise KeyboardInterrupt
    signal.signal(signal.SIGINT, _bye)
    signal.signal(signal.SIGTERM, _bye)

    log.info("serve started — Stream listening for card 👍/👎 (Ctrl-C to stop)",
             user_id=creds.get("user_id"))
    try:
        client.start_forever()
    except KeyboardInterrupt:
        pass
    finally:
        log.info("serve stopped")
        log.close()
    return 0
