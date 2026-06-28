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


def parse_card_callback(data: dict) -> Optional[dict]:
    """A card actionCallback payload → {date, item_id, vote, user_id}, or None if not a 👍/👎.
    outTrackId is '{date}:{item_id}'; the vote rides in content.cardPrivateData.params.vote."""
    if not isinstance(data, dict):
        return None
    out_track_id = data.get("outTrackId") or ""
    if ":" not in out_track_id:
        return None
    date, item_id = out_track_id.split(":", 1)

    content = data.get("content")
    if isinstance(content, str):
        try:
            content = json.loads(content)
        except (ValueError, TypeError):
            content = {}
    params = ((content or {}).get("cardPrivateData") or {}).get("params") or {}
    vote = params.get("vote")
    if vote not in ("up", "down"):
        return None
    return {"date": date, "item_id": item_id, "vote": vote, "user_id": data.get("userId")}


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

    credential = dingtalk_stream.Credential(creds["client_id"], creds["client_secret"])
    client = dingtalk_stream.DingTalkStreamClient(credential)

    class CardHandler(dingtalk_stream.CallbackHandler):
        async def process(self, callback):  # noqa: ANN001
            try:
                parsed = parse_card_callback(getattr(callback, "data", None))
                if parsed:
                    record_feedback(parsed["date"], item_snapshot(parsed["date"], parsed["item_id"]),
                                    parsed["vote"])
                    log.info("feedback via card", date=parsed["date"],
                             item_id=parsed["item_id"], vote=parsed["vote"])
                    return dingtalk_stream.AckMessage.STATUS_OK, _card_update_response(parsed["vote"])
                log.warn("card callback unparseable",
                         keys=list((getattr(callback, "data", None) or {}).keys()))
            except Exception as e:  # noqa: BLE001 — one bad callback must not kill the service
                log.error("card callback handler error", error=repr(e)[:200])
            return dingtalk_stream.AckMessage.STATUS_OK, "OK"

    client.register_callback_handler(dingtalk_stream.CallbackHandler.TOPIC_CARD_CALLBACK, CardHandler())

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
