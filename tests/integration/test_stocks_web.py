"""The local dashboard reads stored records, rejects foreign origins, and keeps credentials out of responses."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from desk.core.config import load_config
from desk.core.paths import DeskPaths
from desk.core.worker_lock import WorkerLock
from desk.stocks.runner import process, saved
from desk.stocks.web import create_app
from tests.conftest import REPO
from tests.stocks_world import NOW, make_world


def test_empty_dashboard_and_secret_form_boundary(tmp_path: Path) -> None:
    paths = DeskPaths(tmp_path / "home", REPO / "config", tmp_path / "private" / "secrets.env")
    with TestClient(create_app(paths), base_url="http://127.0.0.1:8767") as client:
        assert client.get("/").status_code == 200
        response = client.get("/api/stocks/snapshot")
        assert response.status_code == 200 and response.json()["orders_enabled"] is False
        assert response.json()["asof"] is None
        assert "script-src 'self'" in response.headers["content-security-policy"]
        assert client.get("/api/stocks/snapshot", headers={"Host": "attacker.example"}).status_code == 403
        assert client.get("/api/stocks/session", headers={"Origin": "https://attacker.example"}).status_code == 403
        payload = {"app_key": "test-public-key", "app_secret": "DO-NOT-RETURN-THIS", "options_provider": "schwab",
                   "callback_url": "https://127.0.0.1:8182/oauth/callback"}
        assert client.post("/api/stocks/connection", json=payload).status_code == 403
        csrf = client.get("/api/stocks/session").json()["csrf_token"]
        headers = {"X-CSRF-Token": csrf, "Origin": "http://127.0.0.1:8767"}
        with WorkerLock(paths, "stocks-worker"):
            assert client.post("/api/stocks/connection", json=payload, headers=headers).status_code == 409
        response = client.post("/api/stocks/connection", json=payload, headers=headers)
        assert response.status_code == 200 and "DO-NOT-RETURN-THIS" not in response.text
        assert "test-public-key" not in client.get("/api/stocks/snapshot").text
        assert client.post("/api/stocks/connection", content="x" * 9000, headers=headers).status_code == 413
        assert client.get("/api/stocks/paper").json()["report"] is None
        assert client.get("/api/stocks/proof").json()["validation"] is None
        home = client.get("/api/stocks/home").json()
        assert home["breadth"]["researched"] == 0 and home["stocks"] == {}
        assert all(b["price"] is None and b["change"] is None for b in home["benchmarks"])


def test_forward_pipeline_and_populated_dashboard_are_idempotent(tmp_path: Path) -> None:
    world = make_world(tmp_path, n=12)
    loaded = load_config(REPO / "config")
    try:
        result = process(world.paths, loaded, world.dbs, NOW)
        assert result["refresh"]["universe"] > 0 and result["paper"]["skipped"] is None
        assert saved(world.paths, "paper")["mode"] == "PAPER"
        previous = world.dbs.ledger.execute("SELECT COUNT(*) FROM stk_decisions").fetchone()[0]
        assert "already processed" in process(world.paths, loaded, world.dbs, NOW)["waiting"]
        assert world.dbs.ledger.execute("SELECT COUNT(*) FROM stk_decisions").fetchone()[0] == previous
        with TestClient(create_app(world.paths), base_url="http://127.0.0.1:8767") as client:
            snapshot = client.get("/api/stocks/snapshot").json()
            assert snapshot["asof"] == NOW.date().isoformat() and snapshot["lists"]
            detail = client.get("/api/stocks/stock/CHEAP").json()
            assert len(detail["analysis"]["desks"]) == 11 and len(detail["bars"]) <= 400
            accounts = client.get("/api/stocks/paper").json()["report"]["accounts"]
            assert accounts and all(a["deposits"] in {10000, 50000} for a in accounts)
            assert client.get("/api/stocks/stock/TOO_LONG_SYMBOL").status_code == 400
            assert "DO-NOT-RETURN" not in json.dumps(snapshot)
            home = client.get("/api/stocks/home").json()
            assert home["breadth"]["researched"] > 0
            assert len(home["benchmarks"]) == 6 and len(home["stocks"]) <= 150
            assert all(len(card["history"]) <= 24 for card in home["stocks"].values())
            assert client.post("/api/stocks/watch/CHEAP?action=add").status_code == 403
            csrf = client.get("/api/stocks/session").json()["csrf_token"]
            watched = client.post("/api/stocks/watch/CHEAP?action=add", headers={"X-CSRF-Token": csrf})
            assert watched.status_code == 200 and watched.json()["watchlisted"] is True
            assert "CHEAP" in client.get("/api/stocks/home").json()["watchlist"]
            assert client.post("/api/stocks/watch/UNKNOWN?action=add", headers={"X-CSRF-Token": csrf}).status_code == 404
            assert client.get("/api/stocks/stock/CHEAP/options").json()["contracts"] == []
    finally:
        world.close()


def test_missing_latest_session_waits_and_does_not_fabricate_forward_trades(tmp_path: Path) -> None:
    world = make_world(tmp_path, n=12, through=NOW.date() - timedelta(days=1))
    try:
        result = process(world.paths, load_config(REPO / "config"), world.dbs, NOW)
        assert "Waiting for published" in result["waiting"]
        assert world.dbs.ledger.execute("SELECT COUNT(*) FROM stk_decisions").fetchone()[0] == 0
    finally:
        world.close()
