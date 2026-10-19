"""An older discovery writer can continue while a separate dashboard starts and reads new data."""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from desk.core.clock import to_iso, utc_now
from desk.core.paths import DeskPaths
from desk.storage import db
from desk.storage.databases import Databases
from desk.web.app import create_app


def test_dashboard_upgrades_and_reads_while_m2_writer_continues(config_dir: Path, tmp_path: Path) -> None:
    paths = DeskPaths(tmp_path / "data", config_dir, tmp_path / "no-secrets.env")
    original = db.available_migrations
    prefixes = {db.DbKind.MARKET: 3, db.DbKind.LEDGER: 1, db.DbKind.RESEARCH: 1, db.DbKind.RUNTIME: 1}
    with patch.object(db, "available_migrations", lambda kind: original(kind)[:prefixes[kind]]):
        Databases.open(paths).close()

    ready, write_later, written, stop = (threading.Event() for _ in range(4))
    errors: list[BaseException] = []

    def writer() -> None:
        try:
            conn = db.connect(paths.db_dir / "market.db", db.DbKind.MARKET)
            try:
                counter = 0
                while not stop.is_set():
                    counter += 1
                    mint = f"older-collector-token-{counter}"
                    at = to_iso(utc_now())
                    conn.execute("INSERT INTO tokens(mint,first_seen_at,last_seen_at) VALUES (?,?,?)", (mint, at, at))
                    ready.set()
                    if write_later.is_set() and not written.is_set():
                        conn.execute(
                            "INSERT INTO discoveries(id,mint,reason,first_observed_at,last_observed_at,state) "
                            "VALUES ('fresh-after-startup',?,'NEW_TOKEN',?,?,'PENDING')", (mint, at, at))
                        written.set()
                    stop.wait(0.01)
            finally:
                conn.close()
        except BaseException as exc:
            errors.append(exc)
            ready.set()
            written.set()

    thread = threading.Thread(target=writer, daemon=True)
    thread.start()
    try:
        assert ready.wait(5) and not errors
        with TestClient(create_app(paths), base_url="http://127.0.0.1") as client:
            assert client.get("/api/snapshot").status_code == 200
            write_later.set()
            assert written.wait(5) and not errors
            response = client.get("/api/candidates/fresh-after-startup")
            assert response.status_code == 200
            assert response.json()["id"] == "fresh-after-startup"
            assert client.get("/api/snapshot").json()["paper"]["initialized"] is False
        assert thread.is_alive() and not errors
    finally:
        stop.set()
        thread.join(timeout=5)
    assert not thread.is_alive() and not errors
    with sqlite3.connect(paths.db_dir / "market.db") as conn:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
