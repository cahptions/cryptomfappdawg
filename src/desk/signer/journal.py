"""Crash-safe signer journal (synchronous=FULL). An intent id is processed at most once (M8-08)."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from desk.core.clock import to_iso

SCHEMA = """
CREATE TABLE IF NOT EXISTS intents (
    intent_id   TEXT PRIMARY KEY,
    kind        TEXT NOT NULL,
    received_at TEXT NOT NULL,
    request     TEXT NOT NULL,
    state       TEXT NOT NULL CHECK (state IN ('RECEIVED', 'REFUSED', 'SIGNED', 'SUBMITTED', 'CONFIRMED',
                                                'FAILED', 'EXPIRED')),
    signature   TEXT,
    last_valid_block_height INTEGER,
    detail      TEXT NOT NULL DEFAULT '{}',
    updated_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS journal_events (
    id INTEGER PRIMARY KEY, at TEXT NOT NULL, intent_id TEXT NOT NULL, state TEXT NOT NULL, detail TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS journal_events_append_only BEFORE UPDATE ON journal_events BEGIN
    SELECT RAISE(ABORT, 'signer journal events are append-only');
END;
CREATE TRIGGER IF NOT EXISTS journal_events_no_delete BEFORE DELETE ON journal_events BEGIN
    SELECT RAISE(ABORT, 'signer journal events are append-only');
END;
"""
OPEN_STATES = ("SIGNED", "SUBMITTED")
FINAL_STATES = ("REFUSED", "CONFIRMED", "FAILED", "EXPIRED")


class Journal:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=FULL")
        self.conn.executescript(SCHEMA)

    def close(self) -> None:
        self.conn.close()

    def get(self, intent_id: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM intents WHERE intent_id = ?", (intent_id,)).fetchone()
        return None if row is None else {**dict(row), "detail": json.loads(row["detail"])}

    def claim(self, intent_id: str, kind: str, request: dict[str, Any], at: datetime) -> bool:
        """True only for the first caller; a duplicate intent id is never processed again."""
        try:
            self.conn.execute("INSERT INTO intents(intent_id, kind, received_at, request, state, updated_at)"
                              " VALUES (?, ?, ?, ?, 'RECEIVED', ?)",
                              (intent_id, kind, to_iso(at), json.dumps(request, sort_keys=True, default=str), to_iso(at)))
        except sqlite3.IntegrityError:
            return False
        self._event(intent_id, "RECEIVED", {}, at)
        return True

    def update(self, intent_id: str, state: str, at: datetime, detail: dict[str, Any], *, signature: str | None = None,
               last_valid_block_height: int | None = None) -> None:
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            self.conn.execute(
                "UPDATE intents SET state = ?, detail = ?, updated_at = ?, signature = COALESCE(?, signature),"
                " last_valid_block_height = COALESCE(?, last_valid_block_height) WHERE intent_id = ?",
                (state, json.dumps(detail, sort_keys=True, default=str), to_iso(at), signature, last_valid_block_height,
                 intent_id))
            self._event(intent_id, state, detail, at)
            self.conn.execute("COMMIT")
        except BaseException:
            self.conn.execute("ROLLBACK")
            raise

    def _event(self, intent_id: str, state: str, detail: dict[str, Any], at: datetime) -> None:
        self.conn.execute("INSERT INTO journal_events(at, intent_id, state, detail) VALUES (?, ?, ?, ?)",
                          (to_iso(at), intent_id, state, json.dumps(detail, sort_keys=True, default=str)))

    def signed_detail(self, intent_id: str) -> dict[str, Any]:
        """What was journaled when the intent was signed (empty if it never was)."""
        row = self.conn.execute("SELECT detail FROM journal_events WHERE intent_id = ? AND state = 'SIGNED'"
                                " ORDER BY id DESC LIMIT 1", (intent_id,)).fetchone()
        detail: dict[str, Any] = {} if row is None else json.loads(row["detail"])
        return detail

    def unresolved(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM intents WHERE state IN ('RECEIVED', 'SIGNED', 'SUBMITTED') ORDER BY received_at")]
