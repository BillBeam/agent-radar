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
def test_build_card_request_shape():
    from radar.channels.dingtalk_card import CARD_VARS, build_card_request
    creds = {"card_template_id": "tmpl", "user_id": "U123", "robot_code": "RC"}
    body = build_card_request("2026-06-28", _item(id="abc", title="Hi", url="http://x"), creds)
    assert body["cardTemplateId"] == "tmpl"
    assert body["outTrackId"] == "2026-06-28:abc"           # ties the click back to the item
    assert body["callbackType"] == "STREAM"
    assert body["openSpaceId"] == "dtv1.card//IM_ROBOT.U123"
    assert body["imRobotOpenDeliverModel"] == {"spaceType": "IM_ROBOT", "robotCode": "RC"}
    pm = body["cardData"]["cardParamMap"]
    assert pm["title"] == "Hi" and pm["url"] == "http://x"   # title+url → clickable link in template
    assert set(pm) == set(CARD_VARS)                         # keys must match template vars (命门)
    assert all(isinstance(v, str) for v in pm.values())      # cardParamMap values must be strings


def test_build_card_request_robot_code_optional():
    from radar.channels.dingtalk_card import build_card_request
    body = build_card_request("2026-06-28", _item(), {"card_template_id": "t", "user_id": "U"})
    assert body["imRobotOpenDeliverModel"] == {"spaceType": "IM_ROBOT"}   # no robotCode key


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
