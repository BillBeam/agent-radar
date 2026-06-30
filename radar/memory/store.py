"""Content memory — what we've pushed before, so rerank can down-weight topics the
reader has already seen recently (P2).

Local SQLite. The rerank down-weight signal is driven by **exact tag-overlap**
(`push_tags`), NOT FTS5 text-match: the trigram tokenizer only fires for queries of
≥3 codepoints, so short topics like "Go"/"解耦" would match nothing. FTS5 (trigram) is
kept only as a content substrate (near-dup / future "延续上周 X" narrative) and is
guarded — if the local sqlite build lacks FTS5, we degrade to the relational tables and
B's signal is unaffected. No vectors (SPEC §8), no LLM, no extra API cost.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from ..core.config import Paths


class MemoryStore:
    """SQLite-backed push history. Lazily opened — importing this module has ZERO side
    effects (no DB open), so `registry.load_adapters` can import it freely."""

    def __init__(self, db_path: Optional[Path] = None) -> None:
        self._db_path = Path(db_path) if db_path else Paths.memory_db
        self._conn: Optional[sqlite3.Connection] = None
        self.fts_enabled = False

    # ---------------- connection / schema (lazy) ----------------
    def _connect(self) -> sqlite3.Connection:
        if self._conn is None:
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(self._db_path))
            conn.row_factory = sqlite3.Row
            try:
                conn.execute("PRAGMA journal_mode=WAL")
            except sqlite3.Error:
                pass
            self._ensure_schema(conn)
            self._conn = conn
        return self._conn

    def _ensure_schema(self, conn: sqlite3.Connection) -> None:
        # Relational tables (always) — these carry B's signal.
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS pushes (
                item_id    TEXT PRIMARY KEY,
                date       TEXT,
                title      TEXT,
                category   TEXT,
                tags_json  TEXT,
                topic_text TEXT,
                explain_zh TEXT,
                url        TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            );
            CREATE INDEX IF NOT EXISTS idx_pushes_date ON pushes(date);
            CREATE TABLE IF NOT EXISTS push_tags (
                item_id TEXT,
                tag     TEXT,
                PRIMARY KEY (item_id, tag)
            );
            CREATE INDEX IF NOT EXISTS idx_push_tags_tag ON push_tags(tag);
            """
        )
        # FTS5 content substrate (NOT the rerank signal) — guard: degrade if unavailable.
        try:
            conn.executescript(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS pushes_fts USING fts5(
                    topic_text, explain_zh, tokenize='trigram'
                );
                CREATE TRIGGER IF NOT EXISTS pushes_fts_ins AFTER INSERT ON pushes BEGIN
                    INSERT INTO pushes_fts(rowid, topic_text, explain_zh)
                    VALUES (new.rowid, COALESCE(new.topic_text,''), COALESCE(new.explain_zh,''));
                END;
                CREATE TRIGGER IF NOT EXISTS pushes_fts_del AFTER DELETE ON pushes BEGIN
                    DELETE FROM pushes_fts WHERE rowid = old.rowid;
                END;
                CREATE TRIGGER IF NOT EXISTS pushes_fts_upd AFTER UPDATE ON pushes BEGIN
                    DELETE FROM pushes_fts WHERE rowid = old.rowid;
                    INSERT INTO pushes_fts(rowid, topic_text, explain_zh)
                    VALUES (new.rowid, COALESCE(new.topic_text,''), COALESCE(new.explain_zh,''));
                END;
                """
            )
            self.fts_enabled = True
        except sqlite3.Error:
            self.fts_enabled = False
        conn.commit()

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    # ---------------- write ----------------
    def remember_digest(self, date: str, items: list) -> int:
        """Record the delivered items, idempotent on item.id (= sha1(url), cross-day stable).
        Returns the number of items written."""
        conn = self._connect()
        n = 0
        for it in items:
            iid = getattr(it, "id", None)
            if not iid:
                continue
            tags = [t for t in (getattr(it, "tags", None) or []) if t]
            title = getattr(it, "title", "") or ""
            topic_text = " ".join([title, *tags]).strip()
            conn.execute(
                """INSERT OR REPLACE INTO pushes
                   (item_id, date, title, category, tags_json, topic_text, explain_zh, url)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (iid, date, title, getattr(it, "category", "") or "",
                 json.dumps(tags, ensure_ascii=False), topic_text,
                 getattr(it, "explain_zh", None), getattr(it, "url", "") or ""),
            )
            conn.execute("DELETE FROM push_tags WHERE item_id = ?", (iid,))
            conn.executemany(
                "INSERT OR IGNORE INTO push_tags(item_id, tag) VALUES (?,?)",
                [(iid, t) for t in tags],
            )
            n += 1
        conn.commit()
        return n

    # ---------------- read (the rerank signal) ----------------
    def topic_history(self, item: Any, recent_days: int = 30, *,
                      today: Optional[str] = None) -> dict:
        """Distinct EARLIER pushes that share a tag with this candidate, within the window.
        Drives rerank's '近 N 天同主题×N' down-weight. Tag-overlap (taxonomy-controlled,
        length-agnostic), not FTS text-match. `today` overridable for deterministic tests."""
        tags = [t for t in (getattr(item, "tags", None) or []) if t]
        iid = getattr(item, "id", None) or ""
        if not tags:
            return {"count": 0, "last_date": None, "sample_titles": []}
        conn = self._connect()
        ref = today or datetime.now().astimezone().strftime("%Y-%m-%d")
        cutoff = (datetime.strptime(ref, "%Y-%m-%d") - timedelta(days=recent_days)).strftime("%Y-%m-%d")
        placeholders = ",".join("?" for _ in tags)
        rows = conn.execute(
            f"""SELECT p.title AS title, MAX(p.date) AS date
                FROM push_tags pt JOIN pushes p ON p.item_id = pt.item_id
                WHERE pt.tag IN ({placeholders})
                  AND p.date >= ?
                  AND p.item_id != ?
                GROUP BY p.item_id
                ORDER BY date DESC""",
            (*tags, cutoff, iid),
        ).fetchall()
        return {
            "count": len(rows),
            "last_date": rows[0]["date"] if rows else None,
            "sample_titles": [r["title"] for r in rows[:3]],
        }
