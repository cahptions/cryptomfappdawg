"""The production hand-off from discovery to PAPER, with only the network mocked.

A PAPER ticket is capped by pool liquidity converted at a SOL/USD price observed at most 120 s earlier. Nothing in
production records that price on a schedule: each security assessment fetches it before verifying the pool, and
PAPER is invoked seconds later from the same assessment (``discovery.on_pass = paper.consider`` in the collector).
Here the book's own start-up quote is an hour old, so the entry can only be sized if that hand-off works; a broken
one would reject every entry as LIQUIDITY_CAP_UNKNOWN while every unit test with a seeded quote stayed green.
"""

from __future__ import annotations

import json
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from desk.contracts.discovery import ShieldObservation
from desk.contracts.quotes import QuoteObservation, QuoteUnavailable
from desk.core.config import load_config
from desk.core.paths import REPO_ROOT, DeskPaths
from desk.core.secrets import Secrets
from desk.discovery import engine as discovery_module
from desk.discovery.engine import DiscoveryEngine
from desk.paper.engine import PaperEngine
from desk.paper.guardian import Guardian
from desk.paper.ledger import PaperLedger
from desk.providers.factory import build_suite
from desk.regime.context import INPUTS, ContextRecord, save_context
from desk.security.policy import USDC_MINT
from desk.storage.databases import Databases
from tests.integration.test_paper_lifecycle import Clock, FakeJupiter, no_sleep
from tests.unit.test_features import seed_claim, seed_series
from tests.unit.test_security import MINT, NOW, holder_metrics, mint_account, pool_verification, quotes


class StampedJupiter(FakeJupiter):
    """FakeJupiter's prices, received at the test clock's time instead of a fixed one."""

    def __init__(self, clock: Clock) -> None:
        super().__init__()
        self.clock = clock

    async def order(self, input_mint: str, output_mint: str, amount: int) -> QuoteObservation | QuoteUnavailable:
        quote = await super().order(input_mint, output_mint, amount)
        stamp = {"received_at": self.clock()}
        if isinstance(quote, QuoteObservation):
            stamp["requested_at"] = self.clock()
        return quote.model_copy(update=stamp)


async def test_a_security_pass_is_sized_with_the_sol_usd_price_discovery_just_recorded(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    paths = DeskPaths(home=tmp_path / "data", config_dir=REPO_ROOT / "config", secrets_file=tmp_path / "none.env")
    dbs = Databases.open(paths)
    loaded = load_config(paths.config_dir)
    suite = build_suite(paths, loaded, Secrets(), dbs)
    clock = Clock()
    try:
        # the PAPER book opened an hour ago at $100/SOL; its start-up quote is long expired
        clock.now = NOW - timedelta(hours=1)
        paper = PaperEngine(SimpleNamespace(jupiter=StampedJupiter(clock)), dbs, loaded, clock=clock, sleep=no_sleep)
        await paper.ensure_book()
        clock.now = NOW
        Guardian(SimpleNamespace(jupiter=StampedJupiter(clock)), dbs, loaded, clock=clock, sleep=no_sleep).heartbeat()
        save_context(dbs.market, ContextRecord(observed_at=NOW, reasons=dict.fromkeys(INPUTS, "TEST")))
        # what the collector stored: the pool's minute series and the token's claims
        seed_series(dbs.market)
        seed_claim(dbs.market, 24)
        # the network as discovery sees it now: SOL has moved to $150
        buy, sell = quotes()
        spot = buy.model_copy(update={"output_mint": USDC_MINT, "out_amount_raw": 15_000_000})
        monkeypatch.setattr(discovery_module, "utc_now", lambda: NOW)
        monkeypatch.setattr(suite.rpc, "get_mint_account", AsyncMock(return_value=mint_account()))
        monkeypatch.setattr(discovery_module, "verify_pool", AsyncMock(
            return_value=pool_verification().model_copy(update={"liquidity_usd": Decimal(140000)})))
        monkeypatch.setattr(discovery_module, "verify_holders", AsyncMock(return_value=(holder_metrics(), ())))
        monkeypatch.setattr(suite.jupiter, "order", AsyncMock(side_effect=[spot, buy, sell]))
        monkeypatch.setattr(suite.jupiter, "shield", AsyncMock(
            return_value=ShieldObservation(mint=MINT, warnings=(), observed_at=NOW)))

        discovery = DiscoveryEngine(suite, dbs, loaded)
        discovery.on_pass = paper.consider              # as the collector wires them
        assert await discovery.evaluate_cycle() == 1

        kinds = [r[0] for r in dbs.paper_ledger.execute("SELECT kind FROM paper_events ORDER BY id")]
        assert "ENTRY_REJECTED_SIZE" not in kinds and kinds[-1] == "OPENED", kinds
        position = PaperLedger(dbs.paper_ledger).open_positions()[0]
        assert position["mint"] == MINT
        assert Decimal(json.loads(position["entry_json"])["sol_usd"]) == 150   # discovery's quote, not the book's
    finally:
        await suite.aclose()
        dbs.close()
