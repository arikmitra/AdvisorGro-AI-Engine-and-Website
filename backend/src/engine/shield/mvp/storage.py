"""Append-only run history (SQLite). There is deliberately no update or delete."""
from __future__ import annotations

import json
import sqlite3
import threading
from typing import Optional


class RunStore:
    def __init__(self, path: str = ":memory:"):
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._lock = threading.Lock()
        self._db.execute("""CREATE TABLE IF NOT EXISTS runs(
            run_id TEXT PRIMARY KEY, domain TEXT NOT NULL,
            created_at REAL NOT NULL, record TEXT NOT NULL)""")
        self._db.execute("CREATE INDEX IF NOT EXISTS ix_dom ON runs(domain, created_at)")

    def append(self, record: dict) -> None:
        with self._lock:
            self._db.execute("INSERT INTO runs VALUES (?,?,?,?)",
                (record["run_id"], record["domain"], record["created_at"],
                 json.dumps(record, default=str)))
            self._db.commit()

    def history(self, domain: str, limit: int = 10) -> list[dict]:
        with self._lock:
            rows = self._db.execute(
                "SELECT record FROM runs WHERE domain=? ORDER BY created_at DESC LIMIT ?",
                (domain, limit)).fetchall()
        return [json.loads(r[0]) for r in rows]

    def latest(self, domain: str) -> Optional[dict]:
        h = self.history(domain, 1)
        return h[0] if h else None
