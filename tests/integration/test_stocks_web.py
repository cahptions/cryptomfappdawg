"""The local dashboard reads stored records, rejects foreign origins, and keeps credentials out of responses."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from threading import Event
from typing import Any
from unittest.mock import Mock

from fastapi.testclient import TestClient

from desk.core.config import load_config
from desk.core.paths import DeskPaths
from desk.core.worker_lock import WorkerLock
from desk.stocks.research_worker import ResearchWorker
from desk.stocks.runner import process, saved
from desk.stocks.web import create_app
from tests.conftest import REPO
from tests.stocks_world import NOW, make_world


def test_zero_revenue_company_does_not_abort_research_or_write_paper(tmp_path: Path) -> None:
    world = make_world(tmp_path, n=12)
    try:
        cik = world.dbs.market.execute("SELECT cik FROM stk_listings WHERE symbol='CHEAP'").fetchone()[0]
        facts = world.market.facts(cik)
        for concept in ("us-gaap:Revenues", "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax"):
            if concept in facts:
                facts[concept] = [replace(fact, value=0.0) for fact in facts[concept]]
        world.market.save_facts(cik, facts, "synthetic-zero-revenue", NOW)
        ledger = list(world.dbs.ledger.iterdump())
        result = process(world.paths, load_config(REPO / "config"), world.dbs, NOW, research_only=True)
        assert result["universe"] > 1 and result["paper"] is None
        assert saved(world.paths, "research")["lists"]
        detail = world.research.latest("CHEAP")
        assert detail is not None and detail["fundamentals"]["piotroski"] is None
        assert list(world.dbs.ledger.iterdump()) == ledger
    finally:
        world.close()


def test_corrupt_optional_settings_and_reports_keep_dashboard_repairable(tmp_path: Path) -> None:
    paths = DeskPaths(tmp_path / 'home', REPO / 'config', tmp_path / 'private.env')
    paths.ensure()
    (paths.home / 'stocks-connection.json').write_text('invalid json')
    (paths.home / 'stocks-research.json').write_text('{"lists":"wrong shape"}')
    (paths.home / 'stocks-paper.json').write_text('broken')
    with TestClient(create_app(paths, auto_start=False), base_url='http://127.0.0.1:8767') as client:
        value = client.get('/api/stocks/snapshot')
        assert value.status_code == 200
        assert value.json()['connection']['configuration_error']
        assert set(value.json()['report_errors']) == {'paper', 'research'}
        assert client.get('/api/stocks/home').status_code == 200
        assert client.get('/api/stocks/paper').status_code == 200


def test_empty_dashboard_and_secret_form_boundary(tmp_path: Path) -> None:
    paths = DeskPaths(tmp_path / "home", REPO / "config", tmp_path / "private" / "secrets.env")
    with TestClient(create_app(paths, auto_start=False), base_url="http://127.0.0.1:8767") as client:
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
        stale = client.post("/api/stocks/connection", json=payload, headers={"X-CSRF-Token": "old-session"})
        assert stale.status_code == 403 and stale.json()["detail"] == "valid dashboard session required"
        assert not paths.secrets_file.exists()
        csrf = client.get("/api/stocks/session").json()["csrf_token"]
        headers = {"X-CSRF-Token": csrf, "Origin": "http://127.0.0.1:8767"}
        with WorkerLock(paths, "stocks-worker"):
            busy = client.post("/api/stocks/connection", json=payload, headers=headers)
            assert busy.status_code == 409 and "Keep desk stocks dashboard running" in busy.json()["detail"]
        response = client.post("/api/stocks/connection", json=payload, headers=headers)
        assert response.status_code == 200 and "DO-NOT-RETURN-THIS" not in response.text
        assert response.json()["schwab"]["app_key_set"] and response.json()["schwab"]["app_secret_set"]
        assert "test-public-key" not in client.get("/api/stocks/snapshot").text
        assert client.post("/api/stocks/connection", content="x" * 9000, headers=headers).status_code == 413
        assert client.get("/api/stocks/paper").json()["report"] is None
        assert client.get("/api/stocks/proof").json()["validation"] is None
        home = client.get("/api/stocks/home").json()
        assert home["breadth"]["researched"] == 0 and home["stocks"] == {}
        assert all(b["price"] is None and b["change"] is None for b in home["benchmarks"])
        # Auxiliary source setup does not require stopping a worker or invalidate Schwab credentials.
        payload = {"massive_key": "PRIVATE-STOCK-KEY", "sec_contact": "private-contact@example.org"}
        assert client.post("/api/stocks/sources", json=payload).status_code == 403
        with WorkerLock(paths, "stocks-worker"):
            response = client.post("/api/stocks/sources", json=payload, headers=headers)
        assert response.status_code == 200 and response.json()["source_keys"]["MASSIVE_API_KEY"]
        assert "PRIVATE-STOCK-KEY" not in response.text and "private-contact" not in response.text
        assert "PRIVATE-STOCK-KEY" not in client.get("/api/stocks/snapshot").text
        assert client.post("/api/stocks/sources", json={"app_secret": "wrong-route"}, headers=headers).status_code == 400
        assert client.post("/api/stocks/sources", content="x" * 9000, headers=headers).status_code == 413
        assert client.post("/api/stocks/research/start").status_code == 403
        assert client.post("/api/stocks/research/pause").status_code == 403
        with WorkerLock(paths, "stocks-worker"):
            assert client.post("/api/stocks/research/start", json={}, headers=headers).status_code == 400
            assert client.post("/api/stocks/research/pause", json={}, headers=headers).json()["owned"] is False


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
        with TestClient(create_app(world.paths, auto_start=False), base_url="http://127.0.0.1:8767") as client:
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


def test_research_backfill_refreshes_same_session_without_paper_or_frozen_history_writes(tmp_path: Path) -> None:
    world = make_world(tmp_path, n=12)
    loaded = load_config(REPO / "config")
    try:
        process(world.paths, loaded, world.dbs, NOW)
        world.paths.stop_file.touch()
        ledger = list(world.dbs.ledger.iterdump())
        history = world.dbs.research.execute("SELECT COUNT(*) FROM stk_score_history").fetchone()[0]
        frozen = list(world.dbs.research.execute("SELECT * FROM stk_list_snapshots"))
        # An already processed PAPER session must not prevent a research preview as backfill arrives.
        result = process(world.paths, loaded, world.dbs, NOW + timedelta(seconds=5), research_only=True)
        assert result["universe"] > 0 and result["paper"] is None
        assert saved(world.paths, "research")["lists"]
        assert list(world.dbs.ledger.iterdump()) == ledger
        assert world.dbs.research.execute("SELECT COUNT(*) FROM stk_score_history").fetchone()[0] == history
        assert list(world.dbs.research.execute("SELECT * FROM stk_list_snapshots")) == frozen
        assert "current" in process(world.paths, loaded, world.dbs, NOW, research_only=True)["waiting"]
        with TestClient(create_app(world.paths, auto_start=False), base_url="http://127.0.0.1:8767") as client:
            snapshot = client.get("/api/stocks/snapshot").json()
            assert any(v["preview"] and v["rows"] for v in snapshot["lists"])
            assert client.get("/api/stocks/home").json()["stocks"]
            revision = snapshot["revision"]
            world.dbs.market.execute("UPDATE stk_bar_days SET received_at=? WHERE day=(SELECT MAX(day) FROM stk_bar_days)",
                                     ((NOW + timedelta(minutes=1)).isoformat(),))
            assert client.get("/api/stocks/snapshot").json()["revision"] != revision
        assert process(world.paths, loaded, world.dbs, NOW + timedelta(minutes=1), research_only=True)["research_only"]
        assert list(world.dbs.ledger.iterdump()) == ledger
    finally:
        world.close()


def test_research_warmup_waits_without_freezing_empty_lists(tmp_path: Path) -> None:
    world = make_world(tmp_path, n=12)
    try:
        days = world.market.bar_days()
        world.dbs.market.execute("DELETE FROM stk_bar_days WHERE day<?", (days[-5].toordinal(),))
        result = process(world.paths, load_config(REPO / "config"), world.dbs, NOW, research_only=True)
        assert "5/60" in result["waiting"]
        assert world.dbs.research.execute("SELECT COUNT(*) FROM stk_list_days").fetchone()[0] == 0
        assert world.dbs.ledger.execute("SELECT COUNT(*) FROM stk_decisions").fetchone()[0] == 0
        assert saved(world.paths, "research") is None
    finally:
        world.close()


def test_explicit_start_and_pause_routes_never_start_on_get(tmp_path: Path) -> None:
    paths = DeskPaths(tmp_path / "home", REPO / "config", tmp_path / "secrets.env")
    worker = Mock(spec=ResearchWorker)
    worker.status.return_value = {"owned": False, "running": False, "stopping": False, "exit_code": None}
    worker.start.return_value = {"owned": True, "running": True, "stopping": False, "exit_code": None}
    worker.stop.return_value = {"owned": True, "running": True, "stopping": True, "exit_code": None}
    with TestClient(create_app(paths, worker=worker, auto_start=False), base_url="http://127.0.0.1:8767") as client:
        client.get("/")
        client.get("/api/stocks/home")
        client.get("/api/stocks/snapshot")
        worker.start.assert_not_called()
        csrf = client.get("/api/stocks/session").json()["csrf_token"]
        headers = {"X-CSRF-Token": csrf}
        assert client.post("/api/stocks/research/start", json={}, headers=headers).json()["owned"]
        worker.start.assert_called_once()
        assert client.post("/api/stocks/research/pause", json={}, headers=headers).json()["stopping"]
        worker.stop.assert_called_once()


def test_dashboard_lifespan_starts_paper_once_and_shutdown_does_not_persist_pause(tmp_path: Path, monkeypatch: Any) -> None:
    paths = DeskPaths(tmp_path / "home", REPO / "config", tmp_path / "secrets.env")
    child = Mock()
    child.poll.return_value = None
    spawn = Mock(return_value=child)
    monkeypatch.setattr("desk.stocks.research_worker.subprocess.Popen", spawn)
    with TestClient(create_app(paths), base_url="http://127.0.0.1:8767") as client:
        assert spawn.call_count == 1
        for _ in range(3):
            status = client.get("/api/stocks/snapshot").json()["collection"]
            assert status["owned"] and status["mode"] == "PAPER"
        assert spawn.call_count == 1
        assert "paper" in spawn.call_args.args[0] and "--continuous" in spawn.call_args.args[0]
    child.send_signal.assert_called_once()
    assert json.loads((paths.home / "stocks-automation.json").read_text())["enabled"]


def test_paper_waits_for_history_without_consuming_session_then_runs_automatically(tmp_path: Path) -> None:
    world = make_world(tmp_path, n=12)
    try:
        receipts = [tuple(r) for r in world.dbs.market.execute("SELECT * FROM stk_bar_days ORDER BY day")]
        keep = set(world.days[-20:])
        world.dbs.market.execute("DELETE FROM stk_bar_days")
        for row in receipts:
            if any(value.toordinal() == row[0] for value in keep):
                world.dbs.market.execute("INSERT INTO stk_bar_days VALUES (?,?,?,?)", row)
        loaded = load_config(REPO / "config")
        assert "downloading history" in process(world.paths, loaded, world.dbs, NOW)["waiting"]
        assert world.dbs.ledger.execute("SELECT COUNT(*) FROM stk_decisions").fetchone()[0] == 0
        for row in receipts:
            world.dbs.market.execute("INSERT OR IGNORE INTO stk_bar_days VALUES (?,?,?,?)", row)
        result = process(world.paths, loaded, world.dbs, NOW)
        assert result["paper"]["skipped"] is None
        assert "already processed" in process(world.paths, loaded, world.dbs, NOW)["waiting"]
    finally:
        world.close()


def test_failed_authorization_can_be_cancelled_before_correcting_credentials(tmp_path: Path, monkeypatch: Any) -> None:
    paths = DeskPaths(tmp_path / "home", REPO / "config", tmp_path / "secrets.env")
    attempt = Mock()
    attempt.complete = Event()
    attempt.error = None
    attempt.thread.is_alive.return_value = False
    attempt.stop.side_effect = attempt.complete.set
    attempt.start.return_value = "https://api.schwabapi.com/v1/oauth/authorize?client_id=fixture"
    login = Mock(return_value=attempt)
    monkeypatch.setattr("desk.stocks.web.new_login", login)
    with TestClient(create_app(paths, auto_start=False), base_url="http://127.0.0.1:8767") as client:
        headers = {"X-CSRF-Token": client.get("/api/stocks/session").json()["csrf_token"]}
        assert client.post("/api/stocks/connect", json={}, headers=headers).status_code == 400
        login.assert_not_called()
        payload = {"app_key": "fixture-id", "app_secret": "fixture-secret", "options_provider": "schwab",
                   "callback_url": "https://127.0.0.1:8182/oauth/callback"}
        assert client.post("/api/stocks/connection", json=payload, headers=headers).status_code == 200
        assert client.post("/api/stocks/connect", json={}, headers=headers).status_code == 200
        assert client.get("/api/stocks/connection").json()["login"]["pending"]
        assert client.post("/api/stocks/connection", json=payload, headers=headers).status_code == 409
        assert client.post("/api/stocks/connect/cancel", json={}).status_code == 403
        # Closing the listener must finish before credentials can be replaced.
        attempt.thread.is_alive.return_value = True
        assert client.post("/api/stocks/connect/cancel", json={}, headers=headers).status_code == 409
        assert client.post("/api/stocks/connection", json=payload, headers=headers).status_code == 409
        attempt.thread.is_alive.return_value = False
        assert client.post("/api/stocks/connect/cancel", json={}, headers=headers).status_code == 200
        assert not client.get("/api/stocks/connection").json()["login"]["pending"]
        payload["app_secret"] = "corrected-fixture-secret"
        result = client.post("/api/stocks/connection", json=payload, headers=headers)
        assert result.status_code == 200 and "corrected-fixture-secret" not in result.text
