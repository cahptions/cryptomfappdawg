"""PAPER fees: measured network-fee estimates, conservative fallback, close-account fee, no double platform fee."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest

from desk.contracts.quotes import QuoteObservation
from desk.core.config import load_config
from desk.core.paths import REPO_ROOT
from desk.ops.collector import Collector, TaskStats
from desk.paper import fees
from desk.paper.ledger import PaperLedger
from desk.security.policy import SOL_MINT, USDC_MINT
from tests.integration.test_paper_lifecycle import MINT, _setup, dbs  # noqa: F401  (fixture)
from tests.unit.test_strategies import NOW

LOADED = load_config(REPO_ROOT / "config")
TAKER = "5tzFkiKscXHK5ZXCGbXZxdw7gTjjD1mBwuoFbhUvuAi9"


def probe_quote(priority: int, at: Any = NOW, *, taker: str | None = TAKER, output: str = USDC_MINT) -> QuoteObservation:
    return QuoteObservation(source="jupiter.order_v2", input_mint=SOL_MINT, output_mint=output, in_amount_raw=10 ** 8,
                            out_amount_raw=10 ** 7, swap_mode="ExactIn", fee_bps=10, router="metis",
                            signature_fee_lamports=None if taker is None else 5000,
                            prioritization_fee_lamports=None if taker is None else priority, rent_fee_lamports=0,
                            taker=taker, requested_at=at, received_at=at, latency_ms=Decimal(1))


def test_fallback_model_until_six_probes_exist(dbs: Any) -> None:  # noqa: F811
    estimate = fees.estimate(dbs.market, LOADED, NOW)
    assert estimate.source == "FALLBACK_MODEL" and estimate.per_transaction == 105_000
    assert estimate.round_trip == 3 * 105_000          # buy, sell, close the token account
    for priority in (332, 461, 557, 800, 1200):
        assert fees.record(dbs.market, probe_quote(priority), "SOL->USDC")
    assert fees.estimate(dbs.market, LOADED, NOW).source == "FALLBACK_MODEL"


def test_measured_estimate_is_p90_with_margin_over_24_hours(dbs: Any) -> None:  # noqa: F811
    for priority in (332, 461, 557, 800, 1200, 5000, 12_113):
        fees.record(dbs.market, probe_quote(priority, NOW - timedelta(hours=1)), "SOL->USDC")
    fees.record(dbs.market, probe_quote(900_000, NOW - timedelta(hours=25)), "SOL->USDC")   # outside the window
    fees.record(dbs.market, probe_quote(900_000, NOW + timedelta(seconds=1)), "SOL->USDC")  # not yet observable
    estimate = fees.estimate(dbs.market, LOADED, NOW)
    assert estimate.source == "MEASURED" and estimate.observations == 7
    assert estimate.priority_p90 == 12_113 and estimate.priority_p50 == 800
    assert estimate.priority_lamports == 18_170 and estimate.per_transaction == 23_170   # ceil(12,113 x 1.5) + 5,000
    assert estimate.as_dict()["transactions_per_round_trip"] == 3


def test_only_orders_built_for_a_taker_count_and_records_are_append_only(dbs: Any) -> None:  # noqa: F811
    assert not fees.record(dbs.market, probe_quote(500, taker=None), "SOL->USDC")
    assert fees.record(dbs.market, probe_quote(500), "SOL->USDC")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        dbs.market.execute("DELETE FROM fee_observations")


def test_engine_and_guardian_charge_measured_fees_and_the_close_transaction(dbs: Any,  # noqa: F811
                                                                           monkeypatch: pytest.MonkeyPatch) -> None:
    engine, guardian, jupiter, clock, candidate = _setup(dbs, monkeypatch)
    for priority in (500, 600, 700, 800, 900, 1000):
        fees.record(dbs.market, probe_quote(priority, NOW - timedelta(minutes=30)), "SOL->USDC")
    per_tx = 5000 + 1500                                                   # ceil(1,000 x 1.5)
    assert asyncio.run(engine.consider(candidate)) == "OPENED"
    ledger = PaperLedger(dbs.paper_ledger)
    position = ledger.open_positions()[0]
    entry = json.loads(position["entry_json"])["fees"]
    assert entry["source"] == "MEASURED" and entry["per_transaction_lamports"] == per_tx
    assert entry["platform_fee_included_in_quote"] is True
    assert position["entry_fees_lamports"] in (per_tx, 2 * per_tx, 3 * per_tx)      # plus simulated failures
    jupiter.price = Decimal("0.8")                                         # hard stop
    clock.now += timedelta(seconds=30)
    asyncio.run(guardian.tick())
    closed = dict(dbs.paper_ledger.execute("SELECT * FROM positions").fetchone())
    assert closed["state"] == "CLOSED" and closed["exit_fees_lamports"] == 2 * per_tx   # sell + close account
    breakdown = json.loads(closed["profiles_json"])["fee_breakdown"]
    assert breakdown["close_account_tx_lamports"] == per_tx and breakdown["exit_fee_source"] == "MEASURED"
    assert breakdown["rent_locked_and_refunded_lamports"] == LOADED.settings.paper.token_account_rent_lamports
    assert ledger.reconcile()["difference_lamports"] == 0


def test_collector_probe_records_real_fee_reports_for_a_public_taker(dbs: Any,  # noqa: F811
                                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    _setup(dbs, monkeypatch)
    calls: list[tuple[str, str, str | None]] = []

    async def order(input_mint: str, output_mint: str, amount: int, *, taker: str | None = None) -> QuoteObservation:
        calls.append((input_mint, output_mint, taker))
        return probe_quote(700, output=output_mint)

    secrets = SimpleNamespace(all_secret_values=lambda: [])
    suite: Any = SimpleNamespace(jupiter=SimpleNamespace(order=order),
                                 rpc=SimpleNamespace(http=SimpleNamespace(secrets=secrets)))
    collector = Collector(suite, dbs, LOADED, discovery=True)
    collector.stats.tasks["fee_probe"] = TaskStats()
    ledger = PaperLedger(dbs.paper_ledger)
    ledger.open_position(position_id="p1", intent_id="i1", candidate_id="c", mint=MINT, pool_address="pool",
                         strategy_id="H3", strategy_version="v", decision_hash="d", regime_label=None, at=NOW,
                         ticket_lamports=10 ** 8, tokens_raw=10 ** 9, entry_fees_lamports=0, failed_fees_lamports=0,
                         rent_lamports=0, entry={})
    asyncio.run(collector.fee_probe())
    assert calls == [(SOL_MINT, USDC_MINT, TAKER), (SOL_MINT, MINT, TAKER)]
    assert dbs.market.execute("SELECT COUNT(*) FROM fee_observations").fetchone()[0] == 2
    assert collector.stats.tasks["fee_probe"].items == 2


def test_jupiter_platform_fee_is_already_inside_ultra_quotes() -> None:
    """Recorded live comparison: on one route, Ultra output = fee-free output x (1 - feeBps). Never deduct it twice."""
    evidence = json.loads((REPO_ROOT / "research/evidence/2026-10-19/m5_jupiter_fee_embedding.json").read_text())
    result = evidence["result"]
    expected = Decimal(result["legacy_outAmount"]) * (1 - Decimal(result["feeBps"]) / 10_000)
    assert abs(Decimal(result["ultra_taker_outAmount"]) - expected) <= 1
