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


# ---------------- outbound: createAndDeliver body ----------------
def test_build_card_param_map():
    from radar.channels.dingtalk_card import build_card_param_map
    m = build_card_param_map(_item(id="abc", title="Hi", reason="一句话理由"), 3, "🆕")
    assert m == {"markdown": "[3] 🆕 Hi — 一句话理由"}   # plain-text compact line: [N] marker title — reason
    assert all(isinstance(v, str) for v in m.values())        # cardParamMap requires string values
    # reason is clipped so the card stays a one/two-line scan
    long = build_card_param_map(_item(title="T", reason="理" * 200), 1, "📚")["markdown"]
    assert long.startswith("[1] 📚 T — ") and long.endswith("…") and len(long) < 80


def test_build_send_request():
    from radar.channels.dingtalk_card import build_send_request
    body = build_send_request("2026-06-28", _item(id="abc", title="Hi", reason="理由"), 2, "🆕",
                              {"card_template_id": "tpl-uuid.schema", "user_id": "U123", "robot_code": "RC"})
    assert body["cardTemplateId"] == "tpl-uuid.schema"        # app-bound template — the only Stream path
    assert body["outTrackId"] == "2026-06-28:abc"             # ties the click back to the item
    assert body["callbackType"] == "STREAM"                   # → callback reaches /v1.0/card/instances/callback
    assert body["cardData"]["cardParamMap"]["markdown"] == "[2] 🆕 Hi — 理由"   # [N]+marker+title+reason
    assert body["imRobotOpenDeliverModel"] == {"spaceType": "IM_ROBOT", "robotCode": "RC"}  # uppercase
    assert body["openSpaceId"] == "dtv1.card//im_robot.U123"  # LOWERCASE im_robot (per DingTalk codegen)
    assert body["userId"] == "U123" and body["userIdType"] == 1


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
