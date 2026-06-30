"""Observability: structured logging + per-run trace.

A robust harness must be debuggable when it runs unattended. Every run gets a
run_id; logs are structured JSON lines; every stage and external call is traced
to data/trace/{run_id}.jsonl with timing, so a failed 3am run is fully
reconstructable.
"""
from __future__ import annotations

import json
import sys
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional


def _ts() -> str:
    return datetime.now(timezone.utc).isoformat()


class Logger:
    """Structured logger: human line to stderr + JSON line to a run log file."""

    def __init__(self, run_id: str, log_path: Optional[Path] = None, echo: bool = True):
        self.run_id = run_id
        self.echo = echo
        self._fh = None
        if log_path is not None:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            self._fh = log_path.open("a", encoding="utf-8")

    def _emit(self, level: str, msg: str, **fields: Any) -> None:
        rec = {"ts": _ts(), "run_id": self.run_id, "level": level, "msg": msg, **fields}
        if self._fh is not None:
            self._fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            self._fh.flush()
        if self.echo:
            extra = " ".join(f"{k}={v}" for k, v in fields.items())
            print(f"[{level:5}] {msg}{(' · ' + extra) if extra else ''}", file=sys.stderr)

    def info(self, msg: str, **f: Any) -> None:
        self._emit("INFO", msg, **f)

    def warn(self, msg: str, **f: Any) -> None:
        self._emit("WARN", msg, **f)

    def error(self, msg: str, **f: Any) -> None:
        self._emit("ERROR", msg, **f)

    def close(self) -> None:
        if self._fh is not None:
            self._fh.close()
            self._fh = None


class Tracer:
    """Append-only span/event trace for a run."""

    def __init__(self, run_id: str, trace_path: Optional[Path] = None):
        self.run_id = run_id
        self._fh = None
        self._lock = threading.Lock()   # event() is called from deepread's worker threads
        if trace_path is not None:
            trace_path.parent.mkdir(parents=True, exist_ok=True)
            self._fh = trace_path.open("a", encoding="utf-8")

    def event(self, kind: str, **fields: Any) -> None:
        if self._fh is None:
            return
        rec = {"ts": _ts(), "run_id": self.run_id, "kind": kind, **fields}
        line = json.dumps(rec, ensure_ascii=False, default=str) + "\n"
        with self._lock:                # thread-safe: deepread fans calls across workers
            self._fh.write(line)
            self._fh.flush()

    @contextmanager
    def span(self, name: str, **fields: Any) -> Iterator[dict[str, Any]]:
        start = time.monotonic()
        self.event("span_start", name=name, **fields)
        info: dict[str, Any] = {}
        try:
            yield info
        except Exception as e:  # noqa: BLE001 — record then re-raise
            self.event("span_error", name=name, error=repr(e),
                       ms=round((time.monotonic() - start) * 1000, 1))
            raise
        else:
            self.event("span_end", name=name,
                       ms=round((time.monotonic() - start) * 1000, 1), **info)

    def close(self) -> None:
        if self._fh is not None:
            self._fh.close()
            self._fh = None
