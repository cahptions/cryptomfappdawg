"""Content-addressed store of raw provider responses (gzip files named by SHA-256 of the body).

Every stored observation can point back to the exact bytes it was parsed from. Responses linked to a
decision are kept forever (``keep = 1``); others are purged after the retention period.
"""

from __future__ import annotations

import gzip
import os
import sqlite3
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

from desk.core.clock import to_iso, utc_now
from desk.core.hashing import sha256_hex


class RawStore:
    def __init__(self, raw_dir: Path, market_conn: sqlite3.Connection) -> None:
        self.raw_dir = raw_dir
        self.conn = market_conn

    def _rel_path(self, digest: str) -> str:
        return f"{digest[:2]}/{digest}.gz"

    def put(self, provider: str, endpoint: str, http_status: int, body: bytes, received_at: datetime | None = None) -> str:
        digest = sha256_hex(body)
        rel = self._rel_path(digest)
        target = self.raw_dir / rel
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_name = tempfile.mkstemp(dir=target.parent, suffix=".tmp")
            try:
                with os.fdopen(fd, "wb") as raw_fh, gzip.GzipFile(fileobj=raw_fh, mode="wb", mtime=0) as gz:
                    gz.write(body)
                os.replace(tmp_name, target)
            except BaseException:
                Path(tmp_name).unlink(missing_ok=True)
                raise
        self.conn.execute(
            "INSERT OR IGNORE INTO raw_responses(sha256, provider, endpoint, http_status, received_at, size_bytes,"
            " rel_path) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (digest, provider, endpoint, http_status, to_iso(received_at or utc_now()), len(body), rel),
        )
        return digest

    def get(self, digest: str) -> bytes:
        with gzip.open(self.raw_dir / self._rel_path(digest), "rb") as fh:
            data = fh.read()
        if sha256_hex(data) != digest:
            raise ValueError(f"raw response {digest} is corrupted")
        return data

    def mark_keep(self, digest: str) -> None:
        self.conn.execute("UPDATE raw_responses SET keep = 1 WHERE sha256 = ?", (digest,))

    def purge(self, retention_days: int, now: datetime | None = None) -> int:
        cutoff = to_iso((now or utc_now()) - timedelta(days=retention_days))
        rows = self.conn.execute(
            "SELECT sha256, rel_path FROM raw_responses WHERE keep = 0 AND received_at < ?", (cutoff,)
        ).fetchall()
        for row in rows:
            (self.raw_dir / row["rel_path"]).unlink(missing_ok=True)
            self.conn.execute("DELETE FROM raw_responses WHERE sha256 = ?", (row["sha256"],))
        return len(rows)
