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

STALE_AFTER_SECONDS = 3600  # a lock older than this is presumed crashed


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


class RunLock:
    def __init__(self, path: Path):
        self.path = path
        self.acquired = False

    def acquire(self) -> bool:
        """Return True if we got the lock; False if a live run already holds it.
        A stale lock (dead PID or too old) is reclaimed."""
        existing = read_json(self.path, None)
        if isinstance(existing, dict):
            pid = int(existing.get("pid", 0) or 0)
            if _pid_alive(pid) and _age_seconds(existing.get("ts")) < STALE_AFTER_SECONDS:
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
