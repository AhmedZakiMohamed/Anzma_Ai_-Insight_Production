from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from pathlib import Path


class Outbox:
    """D2's minimal durable batch ledger; capacity and drain policy are D3."""

    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.path.parent.stat().st_mode & 0o077:
            raise ValueError("simulator state directory must be private (mode 700)")
        self.db = sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("""CREATE TABLE IF NOT EXISTS batches (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT,
            batch_id TEXT NOT NULL UNIQUE,
            source_code TEXT NOT NULL,
            scenario TEXT NOT NULL,
            payload BLOB NOT NULL,
            payload_sha256 TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('pending','done','failed')),
            http_status INTEGER,
            response_json TEXT
        )""")
        self.db.commit()
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def close(self) -> None:
        self.db.close()

    def add(self, batch_id: str, source_code: str, scenario: str, payload: bytes):
        digest = hashlib.sha256(payload).hexdigest()
        self.db.execute("""INSERT INTO batches(batch_id,source_code,scenario,payload,payload_sha256,status)
                          VALUES(?,?,?,?,?,'pending')""", (batch_id, source_code, scenario, payload, digest))
        self.db.commit()
        return self.get(batch_id)

    def get(self, batch_id: str):
        return self.db.execute("SELECT * FROM batches WHERE batch_id=?", (batch_id,)).fetchone()

    def pending(self, source_code: str):
        return self.db.execute("SELECT * FROM batches WHERE source_code=? AND status='pending' ORDER BY sequence", (source_code,)).fetchall()

    def latest_done(self, source_code: str):
        return self.db.execute("SELECT * FROM batches WHERE source_code=? AND status='done' ORDER BY sequence DESC LIMIT 1", (source_code,)).fetchone()

    def mark(self, batch_id: str, status: str, http_status: int, response_json: str) -> None:
        if status not in {"done", "failed"}:
            raise ValueError("terminal state must be done or failed")
        self.db.execute("UPDATE batches SET status=?,http_status=?,response_json=? WHERE batch_id=?",
                        (status, http_status, response_json, batch_id))
        self.db.commit()

    def pending_count(self, source_code: str) -> int:
        rows = self.db.execute("SELECT payload FROM batches WHERE source_code=? AND status='pending'", (source_code,)).fetchall()
        return sum(len(json.loads(bytes(row["payload"]).decode("utf-8")).get("records", [])) for row in rows)
