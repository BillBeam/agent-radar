"""Web-vote poller — the Mac-side half of the reading-page 👍/👎 loop.

A daemon thread inside `radar --mode serve`: every `INTERVAL_S` it asks the site's
same-origin `GET /votes?since=<cursor>` (bearer = HMAC(secret, "vote-read")[:32] — derived,
never the secret itself) and funnels each vote through the SAME `record_feedback` +
`item_snapshot` path as `radar mark` and the DingTalk card — byte-identical structure by
construction, last-write-wins. The cursor persists in data/state/web_votes_cursor.json so
a serve restart never replays or drops votes.

Network note: serve strips proxies for the domestic DingTalk stream; pages.dev may need
one — run-serve.sh preserves the ambient proxy as AGENT_RADAR_WEB_PROXY before stripping,
and only this poller uses it.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import threading
from typing import Any, Optional

import requests

from ..core.config import Paths
from ..core.feedback import record_feedback
from ..core.io import atomic_write_json, read_json

INTERVAL_S = 60.0
_CURSOR = Paths.state / "web_votes_cursor.json"


def read_token(secret: str) -> str:
    return hmac.new(secret.encode(), b"vote-read", hashlib.sha256).hexdigest()[:32]


def poll_once(api_base: str, token: str, *, log: Any = None,
              session: Optional[requests.Session] = None) -> int:
    """One poll: fetch votes newer than the cursor, record each, advance the cursor.
    Returns the number of votes recorded (0 on quiet or any network failure)."""
    from .listener import item_snapshot   # same snapshot source as the card path
    s = session or _session()
    cursor = int((read_json(_CURSOR, {}) or {}).get("ts", 0))
    try:
        r = s.get(f"{api_base}/votes", params={"since": cursor},
                  headers={"Authorization": f"Bearer {token}"}, timeout=20)
        r.raise_for_status()
        votes = (r.json() or {}).get("votes") or []
    except Exception as e:  # noqa: BLE001 — a bad poll must never kill serve
        if log:
            log.warn("web-vote poll failed", error=repr(e)[:120])
        return 0
    n = 0
    for v in votes:
        try:
            date, item_id, vote = str(v["date"]), str(v["item_id"]), str(v["vote"])
            if vote not in ("up", "down"):
                continue
            record_feedback(date, item_snapshot(date, item_id), vote)
            cursor = max(cursor, int(v.get("ts") or 0))
            n += 1
            if log:
                log.info("feedback via web page", date=date, item_id=item_id, vote=vote)
        except Exception as e:  # noqa: BLE001
            if log:
                log.warn("web vote skipped (malformed)", error=repr(e)[:120])
    if n:
        atomic_write_json(_CURSOR, {"ts": cursor})
    return n


def _session() -> requests.Session:
    s = requests.Session()
    proxy = os.environ.get("AGENT_RADAR_WEB_PROXY")   # preserved by run-serve.sh pre-strip
    if proxy:
        s.proxies = {"http": proxy, "https": proxy}
        s.trust_env = False
    return s


def start_poller(*, base_url: str, log: Any = None) -> Optional[threading.Thread]:
    """Spawn the daemon poll thread. Needs AGENT_RADAR_WEB_SECRET in env (for the derived
    read token only); returns None (poller off) when secret or base_url is missing."""
    secret = os.environ.get("AGENT_RADAR_WEB_SECRET")
    if not secret or not base_url:
        if log:
            log.info("web-vote poller off (no base_url or web secret)")
        return None
    token = read_token(secret)
    del secret
    api = base_url.rstrip("/")
    session = _session()

    def _loop() -> None:
        while True:
            poll_once(api, token, log=log, session=session)
            threading.Event().wait(INTERVAL_S)

    t = threading.Thread(target=_loop, name="web-vote-poller", daemon=True)
    t.start()
    if log:
        log.info("web-vote poller started", api=f"{api}/votes", interval_s=INTERVAL_S)
    return t
