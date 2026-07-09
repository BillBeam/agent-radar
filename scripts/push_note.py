#!/usr/bin/env python
"""Push one line of plain text to the DingTalk 1v1 chat. For operational notices that must
reach the phone when there is no digest to deliver — e.g. run-daily.sh declining to start.

    .venv/bin/python scripts/push_note.py "今天的定时跑跳过了：Mac 在电池上。"

Reuses the review publisher's OTO sender (same bot, same 1v1 conversation, same env-only
credentials). Best-effort by design: a failed notice must never change anyone's exit code.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from radar.self_improve.review import push_summary_dingtalk   # noqa: E402


def main() -> int:
    text = " ".join(sys.argv[1:]).strip()
    if not text:
        print("usage: push_note.py <text>", file=sys.stderr)
        return 2
    try:
        ok, detail = push_summary_dingtalk(text)
    except Exception as e:  # noqa: BLE001 — a notice is never worth an exit code
        print(f"push_note: failed ({e!r})", file=sys.stderr)
        return 0
    print(f"push_note: {'sent' if ok else 'failed'} ({detail})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
