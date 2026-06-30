"""DingTalk interactive-card channel + Stream callback — pure logic (no network, no SDK)."""
from __future__ import annotations

import json

from radar.core.io import atomic_write_json
from radar.core.models import Digest, Item


def _item(**kw):
    base = dict(id="abc", source_id="s", source_name="S", category="harness",
                title="T", url="http://u", reason="一句话理由", explain_zh="详解正文…")
    base.update(kw)
    return Item(**base)


# ---------------- outbound: ONE list card (Loop rows) ----------------
def test_build_items():
    from datetime import datetime, timezone
    from radar.channels.dingtalk_card import build_items
    dt = datetime(2026, 6, 26, tzinfo=timezone.utc)
    a = _item(id="a", published_at=dt, title="Hi", reason="一句话理由", explain_zh="详解")   # 🆕 deep-read
    b = _item(id="b", published_at=dt, reason="r-b", explain_zh=None)                          # 🆕 not deep-read — STILL a row
    c = _item(id="c", published_at=None, title="T2", reason="r2", explain_zh="详解")          # 📚 deep-read
    rows = build_items(Digest(date="2026-06-26", items=[a, b, c]))
    assert [r["num"] for r in rows] == ["1", "2", "3"]                  # ALL items, contiguous [N] == the brief
    assert [r["marker"] for r in rows] == ["🆕", "🆕", "📚"]
    # Chinese-first row (no English title); vote tokens carry vote+item_id back via the actionId
    assert rows[0] == {"num": "1", "marker": "🆕", "reason": "一句话理由",
                       "up_token": "up_a", "down_token": "down_a"}
    assert all(isinstance(v, str) for r in rows for v in r.values())    # cardParamMap rows are all strings


def test_build_list_request(monkeypatch):
    from radar.channels.dingtalk_card import build_list_request
    monkeypatch.delenv("DINGTALK_OUTTRACK_NONCE", raising=False)        # default: stable outTrackId
    rows = [{"num": "1", "marker": "🆕", "title": "Hi", "reason": "r", "up_token": "up_a", "down_token": "down_a"}]
    body = build_list_request("2026-06-28", rows,
                              {"card_template_id": "tpl.schema", "user_id": "U123", "robot_code": "RC"})
    assert body["cardTemplateId"] == "tpl.schema"
    assert body["outTrackId"] == "2026-06-28:list"                      # ONE card for the whole digest
    assert body["callbackType"] == "STREAM"
    assert json.loads(body["cardData"]["cardParamMap"]["items"]) == rows   # items is a JSON STRING (loopArray)
    assert body["imRobotOpenDeliverModel"] == {"spaceType": "IM_ROBOT", "robotCode": "RC"}
    assert body["openSpaceId"] == "dtv1.card//im_robot.U123"            # LOWERCASE im_robot
    assert body["userId"] == "U123" and body["userIdType"] == 1


def test_outtrack_nonce_forces_fresh_instance(monkeypatch):
    from radar.channels.dingtalk_card import build_list_request
    monkeypatch.setenv("DINGTALK_OUTTRACK_NONCE", "demo1")             # opt-in: a new card instead of reusing
    body = build_list_request("2026-06-28", [], {"card_template_id": "t", "user_id": "U", "robot_code": "R"})
    assert body["outTrackId"] == "2026-06-28:list:demo1"


def test_item_numbering_matches_brief():
    """[N] + 🆕/📚 derive from the canonical display order (fresh→backfill) over the FULL list, so a
    card's number equals the brief's — even though deep-read items (which get cards) are a
    non-contiguous subset."""
    from datetime import datetime, timezone
    from radar.channels.dingtalk_card import _canonical_order, deep_read_items, item_numbering
    dt = datetime(2026, 6, 26, tzinfo=timezone.utc)
    a = _item(id="a", published_at=dt, explain_zh="详解")        # fresh, deep-read
    b = _item(id="b", published_at=dt, explain_zh=None)          # fresh, NOT deep-read
    c = _item(id="c", published_at=None, explain_zh="详解")      # backfill, deep-read
    items = [a, b, c]
    assert [it.id for it in _canonical_order(items)] == ["a", "b", "c"]   # fresh then backfill
    num = item_numbering(items)
    assert num == {"a": (1, "🆕"), "b": (2, "🆕"), "c": (3, "📚")}
    digest = Digest(date="2026-06-26", items=items)
    assert [num[it.id] for it in deep_read_items(digest)] == [(1, "🆕"), (3, "📚")]   # non-contiguous [N]


def test_channel_order_card_after_markdown():
    from radar.stages.deliver import CHANNEL_ORDER
    assert "dingtalk_card" in CHANNEL_ORDER                                  # wired into daily delivery
    assert CHANNEL_ORDER.index("dingtalk") < CHANNEL_ORDER.index("dingtalk_card")  # read layer before vote layer


def test_deep_read_items_filters():
    from radar.channels.dingtalk_card import deep_read_items
    a = _item(id="a", explain_zh="真详解")
    b = _item(id="b", explain_zh="（原文正文未能获取，仅标题+链接可读）")   # degrade marker → skip
    c = _item(id="c", explain_zh=None)                                      # never deep-read → skip
    digest = Digest(date="2026-06-28", items=[a, b, c])
    assert [it.id for it in deep_read_items(digest)] == ["a"]


def test_channel_disabled_without_config():
    from radar.channels.dingtalk_card import DingtalkCardChannel
    from radar.core.config import RadarConfig
    assert DingtalkCardChannel().is_enabled(RadarConfig()) is False   # no [channels.dingtalk_card]


# ---------------- inbound: card callback parsing ----------------
def test_parse_card_callback_value_shapes():
    from radar.serve.listener import parse_card_callback
    # REAL shape: content is a JSON STRING, vote in cardPrivateData.params.value
    # (template button uses actionType:request + value="up"/"down")
    s = json.dumps({"cardPrivateData": {"actionIds": ["1"], "params": {"value": "up"}}})
    assert parse_card_callback({"outTrackId": "2026-06-28:abc", "userId": "U9", "content": s}) == \
        {"date": "2026-06-28", "item_id": "abc", "vote": "up", "user_id": "U9"}
    # value passed straight through as plain-string content
    assert parse_card_callback({"outTrackId": "d:i", "content": "down"})["vote"] == "down"
    # outTrackId may carry an optional trailing nonce (re-delivery): date:item:nonce → item_id=item
    assert parse_card_callback({"outTrackId": "2026-06-28:abc:demo1", "content": "up"}) == \
        {"date": "2026-06-28", "item_id": "abc", "vote": "up", "user_id": None}
    # back-compat: custom params.vote
    assert parse_card_callback(
        {"outTrackId": "d:i", "content": {"cardPrivateData": {"params": {"vote": "up"}}}})["vote"] == "up"
    # the actionId itself is "up"/"down"
    assert parse_card_callback(
        {"outTrackId": "d:i", "content": {"cardPrivateData": {"actionIds": ["down"]}}})["vote"] == "down"
    # cardPrivateData.value
    assert parse_card_callback(
        {"outTrackId": "d:i", "content": {"cardPrivateData": {"value": "up"}}})["vote"] == "up"
    # malformed / no vote → None (never crashes)
    assert parse_card_callback({"outTrackId": "no-colon"}) is None
    assert parse_card_callback({"outTrackId": "d:i", "content": {"cardPrivateData": {"params": {}}}}) is None
    assert parse_card_callback({"outTrackId": "d:i", "content": "garbage"}) is None
    assert parse_card_callback({}) is None
    assert parse_card_callback(None) is None


def test_parse_list_card_actionid():
    """LIST card: the clicked row's button actionId is `up_<id>` / `down_<id>` — vote + item_id ride
    in the actionId (params don't resolve ${loop.x}); date comes from outTrackId's first segment."""
    from radar.serve.listener import parse_card_callback
    s = json.dumps({"cardPrivateData": {"actionIds": ["up_abc123"], "params": {}}})
    assert parse_card_callback({"outTrackId": "2026-06-26:list", "userId": "U9", "content": s}) == \
        {"date": "2026-06-26", "item_id": "abc123", "vote": "up", "user_id": "U9"}
    # 👎 + an outTrackId nonce; item_id keeps its own underscores intact
    assert parse_card_callback({"outTrackId": "2026-06-26:list:demo",
                                "content": {"cardPrivateData": {"actionIds": ["down_x_y"]}}}) == \
        {"date": "2026-06-26", "item_id": "x_y", "vote": "down", "user_id": None}
    # a bare "up" (old per-item actionId) must NOT be mistaken for the list shape
    assert parse_card_callback({"outTrackId": "2026-06-26:list",
                                "content": {"cardPrivateData": {"actionIds": ["up"]}}}) is None


def test_inbound_vote_contract():
    """The ONLY thing crossing platform→core is the InboundVote {date,item_id,vote,user_id}.
    record_feedback works off this (+ the snapshot), never a raw DingTalk frame."""
    from radar.serve.listener import _INBOUND_KEYS, parse_card_callback
    ev = parse_card_callback({"outTrackId": "2026-06-26:abc", "userId": "U9",
                              "content": {"cardPrivateData": {"params": {"vote": "up"}}}})
    assert set(ev) == set(_INBOUND_KEYS) == {"date", "item_id", "vote", "user_id"}
    assert ev == {"date": "2026-06-26", "item_id": "abc", "vote": "up", "user_id": "U9"}


def test_normalize_callback_raw_fallback():
    from radar.serve.listener import _normalize_callback, parse_card_callback
    # sdk=None → pure raw passthrough (the SDK path is exercised by the real A0 run)
    raw = {"outTrackId": "2026-06-28:abc", "content": "up", "userId": "U9"}
    assert _normalize_callback(raw, None) == {"outTrackId": "2026-06-28:abc", "content": "up", "userId": "U9"}
    # alt raw key cardInstanceId is honored
    assert _normalize_callback({"cardInstanceId": "d:i", "content": "down"}, None)["outTrackId"] == "d:i"
    # a normalized frame still parses end-to-end
    assert parse_card_callback(_normalize_callback(raw, None)) == \
        {"date": "2026-06-28", "item_id": "abc", "vote": "up", "user_id": "U9"}


def test_item_snapshot(tmp_path, monkeypatch):
    from radar.serve import listener as L
    monkeypatch.setattr(L.Paths, "digests", tmp_path)
    atomic_write_json(tmp_path / "2026-06-28.items.json",
                      [{"id": "abc", "title": "T", "source_name": "S", "tags": ["x"], "url": "http://u"}])
    assert L.item_snapshot("2026-06-28", "abc")["title"] == "T"
    assert L.item_snapshot("2026-06-28", "zzz") == {"id": "zzz"}   # missing → minimal fallback


def test_card_update_response():
    from radar.serve.listener import _card_update_response
    r = _card_update_response("up")
    assert r["userPrivateData"]["cardParamMap"]["status"].startswith("✅ 已记录")


def test_callback_writes_feedback_same_store(tmp_path, monkeypatch):
    """A 👍 tap (parse → snapshot → record_feedback — the handler body minus the SDK) lands in the
    SAME feedback store/shape as `radar mark`."""
    from radar.core import feedback as FB
    from radar.serve import listener as L
    monkeypatch.setattr(L.Paths, "digests", tmp_path)
    monkeypatch.setattr(FB.Paths, "feedback", tmp_path)
    atomic_write_json(tmp_path / "2026-06-28.items.json",
                      [{"id": "abc", "title": "T", "source_name": "S", "tags": ["x"], "url": "http://u"}])
    p = L.parse_card_callback({"outTrackId": "2026-06-28:abc",
                               "content": {"cardPrivateData": {"params": {"vote": "up"}}}})
    FB.record_feedback(p["date"], L.item_snapshot(p["date"], p["item_id"]), p["vote"])
    fb = json.loads((tmp_path / "2026-06-28.json").read_text())
    assert set(fb["abc"]) == {"vote", "ts", "title", "source", "tags", "url"}
    assert fb["abc"]["vote"] == "up" and fb["abc"]["title"] == "T" and fb["abc"]["url"] == "http://u"


def test_run_listener_missing_creds_returns_1(tmp_path, monkeypatch):
    from radar.core.config import RadarConfig
    from radar.serve import listener as L
    monkeypatch.setattr(L.Paths, "state", tmp_path)                  # don't touch real radar.log
    monkeypatch.delenv("DINGTALK_CLIENT_ID", raising=False)
    monkeypatch.delenv("DINGTALK_CLIENT_SECRET", raising=False)
    assert L.run_listener(RadarConfig()) == 1                        # friendly fail, no SDK needed
