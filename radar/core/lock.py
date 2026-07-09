"""Single-run file lock — so a manual run and the scheduled run don't collide
(double delivery, corrupted state). PID + timestamp; stale locks (dead process or
too old) are reclaimed automatically.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .io import atomic_write_json, read_json

# A lock older than this is presumed crashed. It is only a BACKSTOP for a recycled PID —
# a crashed run's PID is dead, so `_pid_alive` reclaims it immediately. The window must
# therefore exceed the longest run a healthy machine can produce, or a live run gets its
# lock stolen and the digest is delivered twice.
#   V5 deepread alone is ~75 min (10 × opus); 2026-07-08's sleep-sliced run took 4h33m of
#   wall clock. 3600s was under BOTH — the old value would have declared that live run stale
#   after one hour. Found when the manual-trigger poller (which probes this lock before
#   starting a run) would have launched a second concurrent daily on top of a running one.
STALE_AFTER_SECONDS = 6 * 3600


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists but not ours
    return True


def _age_seconds(ts: Optional[str]) -> float:
    if not ts:
        return 1e9
    try:
        when = datetime.fromisoformat(ts)
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - when).total_seconds()
    except ValueError:
        return 1e9


def _live(existing: object) -> bool:
    """True if this lock record belongs to a run that is still alive and not stale."""
    if not isinstance(existing, dict):
        return False
    pid = int(existing.get("pid", 0) or 0)
    return _pid_alive(pid) and _age_seconds(existing.get("ts")) < STALE_AFTER_SECONDS


def is_held(path: Path) -> bool:
    """Read-only probe: is a live run holding this lock right now? For callers that must
    decide whether to *start* a run without taking the lock themselves (the manual-trigger
    poller). A dead or stale lock reads as free — same predicate `acquire` reclaims on."""
    return _live(read_json(path, None))


class RunLock:
    def __init__(self, path: Path):
        self.path = path
        self.acquired = False

    def acquire(self) -> bool:
        """Return True if we got the lock; False if a live run already holds it.
        A stale lock (dead PID or too old) is reclaimed."""
        existing = read_json(self.path, None)
        if _live(existing):
            self.held_by = existing
            return False
        self.path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(self.path, {"pid": os.getpid(),
                                      "ts": datetime.now(timezone.utc).isoformat()})
        self.acquired = True
        return True

    def release(self) -> None:
        if self.acquired:
            try:
                self.path.unlink(missing_ok=True)
            except OSError:
                pass
            self.acquired = False

    def __enter__(self) -> "RunLock":
        return self

    def __exit__(self, *exc) -> None:
        self.release()
