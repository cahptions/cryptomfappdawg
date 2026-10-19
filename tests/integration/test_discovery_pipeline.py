"""Offline M2 integration: real SQLite, mocked network boundaries, and a real loopback HTTP request."""

from __future__ import annotations

import json
import threading
from datetime import timedelta
from http.server import HTTPServer
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest

from desk.contracts.discovery import ShieldObservation
from desk.contracts.tokens import TokenClaims, TokenStatsWindow
from desk.core.base58 import b58encode
from desk.core.config import load_config
from desk.core.paths import REPO_ROOT, DeskPaths
from desk.core.secrets import Secrets
from desk.discovery.engine import DiscoveryEngine
from desk.ops.console import make_handler, status_payload
from desk.providers.factory import build_suite
from desk.storage.databases import Databases
from desk.storage.market_repo import MarketRepository
from tests.unit.test_discovery import pool_snapshot
from tests.unit.test_security import MINT, NOW, holder_metrics, mint_account, pool_verification, quotes


async def test_persisted_observation_to_security_pass_and_status(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from desk.discovery import engine as engine_module

    paths = DeskPaths(home=tmp_path / "home", config_dir=REPO_ROOT / "config", secrets_file=tmp_path / "none.env")
    dbs = Databases.open(paths)
    loaded = load_config(paths.config_dir)
    suite = build_suite(paths, loaded, Secrets(), dbs)
    ledger_before = list(dbs.paper_ledger.iterdump())
    market = MarketRepository(dbs.market)
    market.save_pool_snapshots([pool_snapshot()])
    market.save_token_claims([TokenClaims(source="jupiter.tokens_search", mint=MINT, observed_at=NOW,
                                         stats={"1h": TokenStatsWindow(num_traders=200)})])
    buy, sell = quotes()
    from desk.security.policy import SOL_MINT, USDC_MINT

    spot = buy.model_copy(update={"input_mint": SOL_MINT, "output_mint": USDC_MINT, "out_amount_raw": 10_000_000})
    monkeypatch.setattr(engine_module, "utc_now", lambda: NOW)
    monkeypatch.setattr(suite.rpc, "get_mint_account", AsyncMock(return_value=mint_account()))
    monkeypatch.setattr(engine_module, "verify_pool", AsyncMock(return_value=pool_verification()))
    monkeypatch.setattr(engine_module, "verify_holders", AsyncMock(return_value=(holder_metrics(), ())))
    monkeypatch.setattr(suite.jupiter, "order", AsyncMock(side_effect=[spot, buy, sell]))
    monkeypatch.setattr(suite.jupiter, "shield", AsyncMock(return_value=ShieldObservation(
        mint=MINT, warnings=(), observed_at=NOW)))
    try:
        engine = DiscoveryEngine(suite, dbs, loaded)
        assert await engine.evaluate_cycle() == 1
        row = dbs.market.execute("SELECT state, stage FROM discoveries WHERE pool_address != ''").fetchone()
        assert (row["state"], row["stage"]) == ("PASS", 4)
        assert dbs.market.execute("SELECT COUNT(*) FROM security_assessments").fetchone()[0] == 1
        assert dbs.market.execute("SELECT COUNT(*) FROM quotes").fetchone()[0] == 3
        assert engine.repo.report()["orphans"] == 0
        assert list(dbs.paper_ledger.iterdump()) == ledger_before
        assert status_payload(paths)["report"]["by_state"]["PASS"] == 1
        # Repeating a cycle without elapsed time makes no additional expensive calls.
        assert await engine.evaluate_cycle() == 0
        assert suite.jupiter.order.await_count == 3
    finally:
        await suite.aclose()
        dbs.close()


async def test_provider_failure_is_recorded_pending_and_not_eligible(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from desk.core.errors import ProviderError, ProviderErrorKind
    from desk.discovery import engine as engine_module

    paths = DeskPaths(home=tmp_path / "home", config_dir=REPO_ROOT / "config", secrets_file=tmp_path / "none.env")
    dbs = Databases.open(paths)
    loaded = load_config(paths.config_dir)
    suite = build_suite(paths, loaded, Secrets(), dbs)
    MarketRepository(dbs.market).save_pool_snapshots([pool_snapshot()])
    monkeypatch.setattr(engine_module, "utc_now", lambda: NOW)
    monkeypatch.setattr(suite.rpc, "get_mint_account", AsyncMock(side_effect=ProviderError(
        "solana_rpc", ProviderErrorKind.RATE_LIMITED, "temporarily unavailable")))
    try:
        engine = DiscoveryEngine(suite, dbs, loaded)
        assert await engine.evaluate_cycle() == 1
        row = dbs.market.execute("SELECT state, last_rule_id FROM discoveries").fetchone()
        assert row["state"] == "DEFER" and row["last_rule_id"] == "INSUFFICIENT_DATA:SECURITY_DOSSIER"
        assert engine.repo.report()["orphans"] == 0
    finally:
        await suite.aclose()
        dbs.close()


async def test_budget_queue_is_fair_and_known_exclusions_skip_expensive_requests(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from desk.discovery import engine as engine_module

    paths = DeskPaths(home=tmp_path / "home", config_dir=REPO_ROOT / "config", secrets_file=tmp_path / "none.env")
    dbs = Databases.open(paths)
    loaded = load_config(paths.config_dir)
    suite = build_suite(paths, loaded, Secrets(), dbs)
    MarketRepository(dbs.market).save_pool_snapshots([
        pool_snapshot().model_copy(update={"pool_address": b58encode(bytes([i]) * 32)}) for i in range(10, 15)])
    buy, _ = quotes()
    from desk.security.policy import USDC_MINT

    spot = buy.model_copy(update={"output_mint": USDC_MINT, "out_amount_raw": 10_000_000})
    clock = [NOW]
    monkeypatch.setattr(engine_module, "utc_now", lambda: clock[0])
    monkeypatch.setattr(suite.rpc, "get_mint_account", AsyncMock(return_value=mint_account()))
    monkeypatch.setattr(engine_module, "verify_pool", AsyncMock(return_value=pool_verification().model_copy(
        update={"lp_burned": False})))
    expensive = AsyncMock(side_effect=AssertionError("known LP exclusion must not request holders"))
    monkeypatch.setattr(engine_module, "verify_holders", expensive)
    monkeypatch.setattr(suite.jupiter, "order", AsyncMock(return_value=spot))
    try:
        engine = DiscoveryEngine(suite, dbs, loaded)
        assert await engine.evaluate_cycle() == 2
        assert engine.repo.report()["by_state"] == {"DEFER": 3, "REJECT": 2}
        clock[0] += timedelta(seconds=1)
        assert await engine.evaluate_cycle() == 2
        clock[0] += timedelta(seconds=1)
        assert await engine.evaluate_cycle() == 1
        assert engine.repo.report()["by_state"] == {"REJECT": 5}
        assert expensive.await_count == 0
        assert suite.jupiter.order.await_count == 5  # reference quotes only, no entry/sell quotes
    finally:
        await suite.aclose()
        dbs.close()


def test_console_is_read_only_host_checked_and_renders_token_strings_as_text(tmp_path: Path) -> None:
    paths = DeskPaths(home=tmp_path / "home", config_dir=REPO_ROOT / "config", secrets_file=tmp_path / "none.env")
    dbs = Databases.open(paths)
    dbs.close()
    server = HTTPServer(("127.0.0.1", 0), make_handler(paths))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    address = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        with httpx.Client(trust_env=False) as client:
            response = client.get(address + "/api/status")
            assert response.status_code == 200 and response.json()["mode"] == "SHADOW"
            assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
            assert client.post(address + "/api/arm-live").status_code in (404, 501)
            assert client.get(address + "/api/status", headers={"Host": "attacker.invalid"}).status_code == 403
            script = client.get(address + "/app.js").text
            assert "textContent" in script and "innerHTML" not in script
            assert "no recorded run" in script
            assert json.loads(response.text)["report"]["orphans"] == 0
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
