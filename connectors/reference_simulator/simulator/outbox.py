from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from pathlib import Path
from datetime import datetime, timezone


DEFAULT_BUFFER_MAX_BYTES = 256 * 1024 * 1024


class OutboxCorruptError(RuntimeError):
    pass


class BufferFullError(RuntimeError):
    pass


class Outbox:
    """Durable SQLite queue; queued request bytes are immutable."""

    def __init__(self, path: Path, max_bytes: int = DEFAULT_BUFFER_MAX_BYTES):
        self.path = path
        self.max_bytes = max_bytes
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.path.parent.stat().st_mode & 0o077:
            raise ValueError("simulator state directory must be private (mode 700)")
        if self.path.exists():
            try:
                readonly_uri = self.path.resolve().as_uri() + "?mode=ro"
                check = sqlite3.connect(readonly_uri, uri=True)
                try:
                    integrity = [row[0] for row in check.execute("PRAGMA integrity_check")]
                    has_batches = check.execute(
                        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='batches'").fetchone()
                    if has_batches:
                        for payload, expected in check.execute("SELECT payload,payload_sha256 FROM batches"):
                            if hashlib.sha256(bytes(payload)).hexdigest() != expected:
                                raise OutboxCorruptError(
                                    "outbox payload checksum mismatch; state file was left untouched")
                finally:
                    check.close()
            except OutboxCorruptError:
                raise
            except sqlite3.Error as exc:
                raise OutboxCorruptError("outbox integrity check failed; damaged state was left untouched") from None
            if integrity != ["ok"]:
                raise OutboxCorruptError("outbox integrity check failed; damaged state was left untouched")
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
        self.db.execute("CREATE TABLE IF NOT EXISTS outbox_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        self.db.execute("INSERT OR IGNORE INTO outbox_meta(key,value) VALUES('dropped_records','0')")
        self.db.commit()
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def close(self) -> None:
        self.db.close()

    def add(self, batch_id: str, source_code: str, scenario: str, payload: bytes,
            created_at: str | None = None):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            if self.buffered_bytes() + len(payload) > self.max_bytes:
                try:
                    lost_records = len(json.loads(payload.decode("utf-8")).get("records", []))
                except (UnicodeDecodeError, json.JSONDecodeError, AttributeError, TypeError):
                    lost_records = 1
                self.db.execute("UPDATE outbox_meta SET value=CAST(value AS INTEGER)+? WHERE key='dropped_records'",
                                (lost_records,))
                self.db.commit()
                raise BufferFullError("outbox byte cap reached; generated batch was not queued")
            digest = hashlib.sha256(payload).hexdigest()
            self.db.execute("""INSERT INTO batches(batch_id,source_code,scenario,payload,payload_sha256,status)
                              VALUES(?,?,?,?,?,'pending')""", (batch_id, source_code, scenario, payload, digest))
            stamp = created_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            self.db.execute("INSERT OR REPLACE INTO outbox_meta(key,value) VALUES('last_poll_at',?)", (stamp,))
            if scenario == "keepalive":
                self.db.execute("INSERT OR REPLACE INTO outbox_meta(key,value) VALUES('last_keepalive_at',?)", (stamp,))
            self.db.commit()
        except Exception:
            if self.db.in_transaction:
                self.db.rollback()
            raise
        return self.get(batch_id)

    def get(self, batch_id: str):
        return self._verify(self.db.execute("SELECT * FROM batches WHERE batch_id=?", (batch_id,)).fetchone())

    def pending(self, source_code: str):
        rows = self.db.execute("SELECT * FROM batches WHERE source_code=? AND status='pending' ORDER BY sequence", (source_code,)).fetchall()
        return [self._verify(row) for row in rows]

    def latest_done(self, source_code: str):
        return self._verify(self.db.execute("SELECT * FROM batches WHERE source_code=? AND status='done' ORDER BY sequence DESC LIMIT 1", (source_code,)).fetchone())

    @staticmethod
    def _verify(row):
        if row is not None and hashlib.sha256(bytes(row["payload"])).hexdigest() != row["payload_sha256"]:
            raise OutboxCorruptError("outbox payload checksum mismatch; state file was left untouched")
        return row

    def mark(self, batch_id: str, status: str, http_status: int, response_json: str,
             completed_at: str | None = None) -> None:
        if status not in {"done", "failed"}:
            raise ValueError("terminal state must be done or failed")
        self.db.execute("UPDATE batches SET status=?,http_status=?,response_json=? WHERE batch_id=?",
                        (status, http_status, response_json, batch_id))
        if status == "done":
            stamp = completed_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            self.db.execute("INSERT OR REPLACE INTO outbox_meta(key,value) VALUES('last_success_at',?)", (stamp,))
        self.db.commit()

    def pending_count(self, source_code: str) -> int:
        rows = self.db.execute("SELECT payload FROM batches WHERE source_code=? AND status='pending'", (source_code,)).fetchall()
        return sum(len(json.loads(bytes(row["payload"]).decode("utf-8")).get("records", [])) for row in rows)

    def buffered_bytes(self) -> int:
        row = self.db.execute("SELECT COALESCE(sum(length(payload)),0) FROM batches WHERE status='pending'").fetchone()
        return int(row[0])

    def dropped_records(self) -> int:
        row = self.db.execute("SELECT value FROM outbox_meta WHERE key='dropped_records'").fetchone()
        return int(row[0]) if row else 0

    def record_dropped(self, count: int) -> None:
        if count <= 0:
            return
        self.db.execute("UPDATE outbox_meta SET value=CAST(value AS INTEGER)+? WHERE key='dropped_records'",
                        (count,))
        self.db.commit()

    def keepalive_pending(self, source_code: str) -> bool:
        return self.db.execute("SELECT 1 FROM batches WHERE source_code=? AND scenario='keepalive' AND status='pending' LIMIT 1",
                               (source_code,)).fetchone() is not None

    def keepalive_due(self, now: datetime, interval_seconds: int) -> tuple[bool, int]:
        row = self.db.execute("SELECT value FROM outbox_meta WHERE key='last_success_at'").fetchone()
        if row is None:
            row = self.db.execute("SELECT value FROM outbox_meta WHERE key='last_keepalive_at'").fetchone()
        if row is None:
            row = self.db.execute("SELECT value FROM outbox_meta WHERE key='last_poll_at'").fetchone()
        if not row:
            return True, 0
        previous = datetime.fromisoformat(row[0].replace("Z", "+00:00"))
        remaining = interval_seconds - max(0, int((now - previous).total_seconds()))
        return remaining <= 0, max(0, remaining)

    def can_generate(self) -> bool:
        return self.buffered_bytes() < self.max_bytes
