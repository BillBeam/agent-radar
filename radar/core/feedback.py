"""Shared feedback store — ONE writer for both `radar mark` and the DingTalk card callback.

Keeping the write in a single place means the two entry points (terminal `mark` and a 👍/👎
tap in DingTalk) produce a byte-identical structure *by construction*, not merely by a test.
P2 personalization later reads this single store, so the contract must never drift.
"""
from __future__ import annotations

from datetime import datetime

from .config import Paths
from .io import atomic_write_json, read_json


def record_feedback(date: str, item: dict, vote: str) -> dict:
    """Record a 👍/👎 on a digest item → `data/feedback/{date}.json`, keyed by item id, with a
    content snapshot (title/source/tags/url) so P2 is self-contained. last-write-wins on repeat.

    `item` is the item dict from `{date}.items.json` (both callers have it). Returns the
    snapshot just written.
    """
    path = Paths.feedback / f"{date}.json"
    feedback = read_json(path, {})
    if not isinstance(feedback, dict):
        feedback = {}
    snap = {
        "vote": vote,
        "ts": datetime.now().astimezone().isoformat(timespec="seconds"),
        "title": item.get("title"),
        "source": item.get("source_name"),
        "tags": item.get("tags", []),
        "url": item.get("url"),
    }
    feedback[item["id"]] = snap
    atomic_write_json(path, feedback)
    return snap
