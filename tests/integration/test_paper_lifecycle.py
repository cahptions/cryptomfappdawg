"""M5 end to end with a fake Jupiter: decision → sized entry → marks → exit → reconciliation → report.

Covers M5-01 (reconciliation), M5-04 (quote-only fills), M5-07 (failure handling), M5-10 (locks/duplicates),
M5-11 (restart recovery), M5-12 (entries refused without the guardian), M5-13 (kill switch), M5-17 (mode
separation) and M5-19 (audit trail of every step).
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from desk.contracts.discovery import DiscoveryReason
from desk.contracts.quotes import QuoteObservation, QuoteUnavailable
from desk.core.clock import to_iso
from desk.core.config import load_config
from desk.core.paths import REPO_ROOT, DeskPaths
from desk.paper import engine as engine_module
from desk.paper.engine import PaperEngine
from desk.paper.guardian import Guardian
from desk.paper.ledger import PaperLedger
from desk.paper.report import paper_report
from desk.regime.context import INPUTS, ContextRecord, save_context
from desk.security.policy import SOL_MINT, USDC_MINT
from desk.storage.databases import Databases
from desk.storage.discovery_repo import DiscoveryRepository
from tests.unit.test_strategies import NOW, _context

MINT = "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"
POOL = "58oQChx4yWmvKdwLLZzBi4ChoCc2fqCUWBkwMihLYQo2"
SOL_USD = Decimal(100)          # 1 SOL = $100 -> $100 base = 1 SOL, $100k cash = 1000 SOL


class FakeJupiter:
    """tokens per lamport on buys; lamports per token on sells, both scaled by ``price``."""

    def __init__(self) -> None:
        self.price = Decimal(1)          # value multiplier of the token
        self.route = True
        self.routes_left: int | None = None   # when set, only this many more token quotes find a route
        self.calls: list[tuple[str, str, int]] = []

    async def order(self, input_mint: str, output_mint: str, amount: int) -> QuoteObservation | QuoteUnavailable:
        self.calls.append((input_mint, output_mint, amount))
        if input_mint == SOL_MINT and output_mint == USDC_MINT:
            out = int(Decimal(amount) / 10 ** 9 * SOL_USD * 10 ** 6)
        elif not self.route or self.routes_left == 0:
            return QuoteUnavailable(source="fake", input_mint=input_mint, output_mint=output_mint, in_amount_raw=amount,
                                    error_code="NO_ROUTE", error_message=None, http_status=200, received_at=NOW)
        elif input_mint == SOL_MINT:
            out = self._count(int(Decimal(amount) * 10 / self.price))                     # 10 raw tokens per lamport at price 1
        else:
            out = self._count(int(Decimal(amount) / 10 * self.price * Decimal("0.99")))   # 1% round-trip cost
        return QuoteObservation(source="fake.order", input_mint=input_mint, output_mint=output_mint,
                                in_amount_raw=amount, out_amount_raw=out, swap_mode="ExactIn",
                                price_impact_pct=Decimal("0.2"), requested_at=NOW, received_at=NOW,
                                latency_ms=Decimal(50))


    def _count(self, out: int) -> int:
        if self.routes_left is not None:
            self.routes_left -= 1
        return out


class Clock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> datetime:
        return self.now


async def no_sleep(_: float) -> None:
    return None


@pytest.fixture
def dbs(tmp_path: Path):
    dbs = Databases.open(DeskPaths(home=tmp_path / "data", config_dir=REPO_ROOT / "config",
                                  secrets_file=tmp_path / "none.env"))
    try:
        yield dbs
    finally:
        dbs.close()


def _setup(dbs: Databases, monkeypatch: pytest.MonkeyPatch) -> tuple[PaperEngine, Guardian, FakeJupiter, Clock, str]:
    loaded = load_config(REPO_ROOT / "config")
    jupiter, clock = FakeJupiter(), Clock()
    suite: Any = SimpleNamespace(jupiter=jupiter)
    candidate = DiscoveryRepository(dbs.market, loaded.settings.discovery).observe(
        MINT, POOL, "fixture", NOW - timedelta(minutes=20), reason=DiscoveryReason.MIGRATION)
    base = _context()
    features = base.features.model_copy(update={"mint": MINT, "pool_address": POOL})
    context = base.model_copy(update={"features": features, "regime_label": None, "regime_status": base.regime_status})
    def shifted(*_: object, **__: object) -> object:
        delta = clock.now - NOW
        values = {name: value.model_copy(update={"observed_at": value.observed_at + delta})
                  for name, value in features.values.items()}
        return context.model_copy(update={"security_assessed_at": NOW + delta, "features": features.model_copy(
            update={"as_of": clock.now, "values": values})})

    monkeypatch.setattr(engine_module, "decision_context", shifted)
    engine = PaperEngine(suite, dbs, loaded, clock=clock, sleep=no_sleep)
    guardian = Guardian(suite, dbs, loaded, clock=clock, sleep=no_sleep)
    asyncio.run(engine.ensure_book())
    save_context(dbs.market, ContextRecord(observed_at=NOW, reasons=dict.fromkeys(INPUTS, "TEST")))
    guardian.heartbeat()
    return engine, guardian, jupiter, clock, candidate


def test_full_lifecycle_reconciles_and_every_step_is_audited(dbs: Databases, monkeypatch: pytest.MonkeyPatch) -> None:
    engine, guardian, jupiter, clock, candidate = _setup(dbs, monkeypatch)
    ledger = PaperLedger(dbs.paper_ledger)
    assert ledger.cash_lamports() == 1000 * 10 ** 9 and ledger.base_lamports() == 10 ** 9
    assert asyncio.run(engine.consider(candidate)) == "OPENED"
    position = ledger.open_positions()[0]
    assert position["ticket_lamports"] == 10 ** 8                       # 10% of the $100 base (1 SOL)
    # fills came only from exact-amount quotes: buy, sell-back cost check, second buy (worse is used)
    assert jupiter.calls[1:] == [(SOL_MINT, MINT, 10 ** 8), (MINT, SOL_MINT, 10 ** 9), (SOL_MINT, MINT, 10 ** 8)]
    assert position["tokens_raw"] == int(Decimal(10 ** 9) * (1 - Decimal("0.001")))
    assert ledger.reconcile()["difference_lamports"] == 0
    # second decision on the same mint is refused (lock) — by the gate and, underneath, by the database
    assert asyncio.run(engine.consider(candidate)) == "BLOCKED"
    with pytest.raises(sqlite3.IntegrityError):
        dbs.paper_ledger.execute("INSERT INTO positions SELECT 'dup', 'intent_dup', candidate_id, mint, pool_address,"
                                 " strategy_id, strategy_version, decision_hash, regime_label, 'OPEN', opened_at,"
                                 " ticket_lamports, tokens_raw, entry_fees_lamports, rent_lamports, entry_json,"
                                 " NULL, NULL, NULL, NULL, NULL, 0, 0, NULL, NULL, NULL, NULL, NULL, NULL FROM positions")
    # price rises 30%: marks track the peak; then falls 12% from the peak -> trailing stop
    jupiter.price = Decimal("1.3")
    clock.now += timedelta(seconds=30)
    asyncio.run(guardian.tick())
    assert ledger.open_positions()[0]["peak_value_lamports"] > 10 ** 8 * 1.25
    jupiter.price = Decimal("1.3") * Decimal("0.88")
    clock.now += timedelta(seconds=30)
    asyncio.run(guardian.tick())
    assert not ledger.open_positions()
    closed = dict(dbs.paper_ledger.execute("SELECT * FROM positions").fetchone())
    assert closed["exit_reason"] == "TRAILING_STOP" and closed["realized_pnl_lamports"] > 0
    # M5-15 by hand: both exit quotes return floor(tokens / 10 * price * 0.99); the conservative fill takes the
    # worse quote less half the 0.2% impact. Exit fees are two transactions at the conservative fallback rate
    # (no fee probes recorded): the sell and the close-account transaction that returns the rent.
    # realized = proceeds - exit fees - ticket - entry fees (rent refunded)
    quote_out = int(Decimal(closed["tokens_raw"]) / 10 * Decimal("1.3") * Decimal("0.88") * Decimal("0.99"))
    assert closed["exit_lamports"] == int(Decimal(quote_out) * Decimal("0.999"))
    assert closed["exit_fees_lamports"] == 105_000 + 105_000
    assert closed["realized_pnl_lamports"] == (closed["exit_lamports"] - 210_000 - 10 ** 8
                                              - closed["entry_fees_lamports"])
    breakdown = json.loads(closed["profiles_json"])["fee_breakdown"]
    assert breakdown["sell_tx_lamports"] == breakdown["close_account_tx_lamports"] == 105_000
    assert breakdown["exit_fee_source"] == "FALLBACK_MODEL" and breakdown["platform_fee_included_in_quote"]
    assert closed["entry_fees_lamports"] in (105_000, 210_000, 315_000)   # plus any simulated failed entry tx
    assert ledger.reconcile()["difference_lamports"] == 0
    kinds = [r["kind"] for r in dbs.paper_ledger.execute("SELECT kind FROM paper_events ORDER BY id")]
    for step in ("DECISION", "ENTRY_ATTEMPT", "OPENED", "EXITING", "CLOSED"):
        assert step in kinds
    report = paper_report(dbs, load_config(REPO_ROOT / "config"), clock.now)
    assert report["closed_trades"] == 1 and report["reconciliation"]["difference_lamports"] == 0
    pnl = report["realized_pnl_sol"]
    assert Decimal(pnl["observed"]) >= Decimal(pnl["conservative"]) >= Decimal(pnl["stress"])
    assert report["replica_100_usd_account"]["trades_taken"] == 1
    # M5-17: nothing was written to a LIVE ledger, SHADOW tables untouched
    assert not (dbs.paths.db_dir / "ledger-live.db").exists()
    assert dbs.market.execute("SELECT COUNT(*) FROM shadow_decisions").fetchone()[0] == 0


def test_entries_need_a_supervising_guardian_and_respect_the_kill_switch(dbs: Databases,
                                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    engine, guardian, _, clock, candidate = _setup(dbs, monkeypatch)
    clock.now += timedelta(seconds=21)                                  # guardian heartbeat now stale
    save_context(dbs.market, ContextRecord(observed_at=clock.now, reasons=dict.fromkeys(INPUTS, "TEST")))
    assert asyncio.run(engine.consider(candidate)) == "BLOCKED"
    guardian.heartbeat()
    (dbs.paths.home / "STOP").write_text("stop")
    assert asyncio.run(engine.consider(candidate)) == "BLOCKED"
    reasons = [r["payload_json"] for r in
               dbs.paper_ledger.execute("SELECT payload_json FROM paper_events WHERE kind = 'ENTRY_BLOCKED'")]
    assert "GUARDIAN_NOT_SUPERVISING" in reasons[0] and "KILL_SWITCH" in reasons[1]
    (dbs.paths.home / "STOP").unlink()
    assert asyncio.run(engine.consider(candidate)) == "OPENED"


def test_stale_market_context_and_halts_block_entries(dbs: Databases, monkeypatch: pytest.MonkeyPatch) -> None:
    engine, guardian, _, clock, _candidate = _setup(dbs, monkeypatch)
    clock.now += timedelta(seconds=121)
    guardian.heartbeat()
    assert "STALE_MARKET_CONTEXT" in engine.blocked(MINT, clock.now)
    PaperLedger(dbs.paper_ledger).halt("STOP", clock.now)
    assert "HALTED:STOP" in engine.blocked(MINT, clock.now)


def test_no_route_at_entry_fails_without_a_position(dbs: Databases, monkeypatch: pytest.MonkeyPatch) -> None:
    engine, _, jupiter, _, candidate = _setup(dbs, monkeypatch)
    jupiter.route = False
    assert asyncio.run(engine.consider(candidate)) == "ENTRY_FAILED"
    assert not PaperLedger(dbs.paper_ledger).open_positions()
    assert PaperLedger(dbs.paper_ledger).reconcile()["difference_lamports"] == 0


def test_failed_exits_block_then_restart_recovers_and_closes(dbs: Databases, monkeypatch: pytest.MonkeyPatch) -> None:
    engine, guardian, jupiter, clock, candidate = _setup(dbs, monkeypatch)
    assert asyncio.run(engine.consider(candidate)) == "OPENED"
    ledger = PaperLedger(dbs.paper_ledger)
    jupiter.price = Decimal("0.85")                                     # hard stop territory
    jupiter.routes_left = 1                                             # the mark succeeds, then every route vanishes
    for _ in range(3):
        clock.now += timedelta(seconds=30)
        asyncio.run(guardian.tick())
    row = ledger.open_positions()[0]
    assert row["state"] == "EXIT_BLOCKED" and row["exit_attempts"] >= 3   # never recorded as closed
    # restart: a brand-new guardian process resumes the pending exit from the ledger alone
    jupiter.routes_left = None
    fresh = Guardian(SimpleNamespace(jupiter=jupiter), dbs, load_config(REPO_ROOT / "config"), clock=clock,
                     sleep=no_sleep)
    clock.now += timedelta(seconds=61)
    asyncio.run(fresh.tick())
    closed = dict(dbs.paper_ledger.execute("SELECT * FROM positions").fetchone())
    assert closed["state"] == "CLOSED" and closed["exit_reason"] == "HARD_STOP" and closed["realized_pnl_lamports"] < 0
    assert ledger.reconcile()["difference_lamports"] == 0


def test_cash_journal_and_events_are_append_only(dbs: Databases, monkeypatch: pytest.MonkeyPatch) -> None:
    _setup(dbs, monkeypatch)
    PaperLedger(dbs.paper_ledger).event(NOW, "TEST", {})
    for table in ("cash_entries", "paper_events"):
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            dbs.paper_ledger.execute(f"DELETE FROM {table}")
    assert to_iso(NOW)


def test_guardian_waits_for_the_engine_heartbeat_before_idling(dbs: Databases, monkeypatch: pytest.MonkeyPatch) -> None:
    from desk.paper import guardian as guardian_module

    _, guardian, _, _, _ = _setup(dbs, monkeypatch)
    ticks = 0
    original = guardian.tick

    async def counting_tick() -> None:
        nonlocal ticks
        ticks += 1
        if ticks == 3:
            guardian.stop()
        await original()

    monkeypatch.setattr(guardian, "tick", counting_tick)
    monkeypatch.setattr(guardian_module, "IDLE_EXIT_SECONDS", 300)
    guardian.paper = guardian.paper.model_copy(update={"guardian_loop_seconds": 1})
    asyncio.run(guardian.run())
    assert ticks == 3       # no collector heartbeat yet, but it kept supervising through the grace period
