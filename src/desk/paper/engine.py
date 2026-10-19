"""PAPER entry engine: decides right after a fresh security PASS and opens quote-filled learning-book positions.

It runs inside ``desk paper`` (collector + discovery). It never exits positions — the independent guardian does —
and it refuses every entry while the guardian heartbeat is stale, the kill switch is set or a halt is active.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from collections.abc import Callable
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from desk.contracts.quotes import QuoteObservation, QuoteUnavailable
from desk.core.clock import parse_iso8601, to_epoch_millis, to_iso, utc_now
from desk.core.config import LoadedConfig
from desk.core.ids import new_id
from desk.core.units import sol_to_lamports
from desk.ops.shadow import decision_context
from desk.paper.executor import Execution, enter
from desk.paper.ledger import PaperLedger
from desk.paper.model import FeeModel, learning_ticket, usd_to_lamports
from desk.providers.factory import ProviderSuite
from desk.regime.context import ReferenceCache, latest_context
from desk.security.policy import SOL_MINT, USDC_MINT
from desk.storage.databases import Databases
from desk.storage.market_repo import MarketRepository
from desk.storage.shadow_repo import ShadowRepository
from desk.strategies import builtin_specs, evaluate_decision
from desk.strategies.specs import paper_specs

log = logging.getLogger(__name__)
FAILURE_PAUSE = timedelta(minutes=15)
SUSPEND_RECOVERY = timedelta(minutes=5)


def fee_model(loaded: LoadedConfig) -> FeeModel:
    paper = loaded.settings.paper
    return FeeModel(paper.signature_fee_lamports, paper.priority_fee_lamports, paper.token_account_rent_lamports)


def execution_model(loaded: LoadedConfig) -> Execution:
    paper = loaded.settings.paper
    return Execution(paper.conservative_wait_seconds, paper.simulated_failure_rate, paper.stress_failure_rate,
                     fee_model(loaded))


def heartbeat_age(runtime: sqlite3.Connection, process: str, now: datetime) -> float | None:
    row = runtime.execute("SELECT at FROM heartbeats WHERE process = ?", (process,)).fetchone()
    return None if row is None else (now - parse_iso8601(row["at"])).total_seconds()


def stop_file_set(dbs: Databases) -> bool:
    return (dbs.paths.home / "STOP").exists()


class PaperEngine:
    def __init__(self, suite: ProviderSuite, dbs: Databases, loaded: LoadedConfig, *, seed: int = 0,
                 clock: Callable[[], datetime] = utc_now, sleep: Callable[[float], Any] = asyncio.sleep,
                 suspended_recently: Callable[[], bool] = lambda: False) -> None:
        self.suite, self.dbs, self.loaded, self.seed = suite, dbs, loaded, seed
        self.clock, self.sleep, self.suspended_recently = clock, sleep, suspended_recently
        self.ledger = PaperLedger(dbs.paper_ledger)
        self.market = MarketRepository(dbs.market)
        self.references = ReferenceCache(dbs.market)
        self.specs = paper_specs()
        self.by_id = {spec.strategy_id: spec for spec in self.specs}
        self.execution = execution_model(loaded)
        self._lock = asyncio.Lock()

    async def _quote(self, input_mint: str, output_mint: str, amount: int) -> QuoteObservation | QuoteUnavailable:
        quote = await self.suite.jupiter.order(input_mint, output_mint, amount)
        self.market.save_quote(quote)
        return quote

    async def ensure_book(self) -> None:
        """Create the learning book once, at one recorded SOL/USD rate from an exact 1 SOL quote."""
        # PAPER versions are children of the SHADOW v1 specs: register parents first (idempotent)
        repo = ShadowRepository(self.dbs.market)
        repo.register_specs(builtin_specs(), self.clock())
        repo.register_specs(self.specs, self.clock())
        if self.ledger.initialized():
            return
        one_sol = sol_to_lamports(Decimal(1))
        spot = await self._quote(SOL_MINT, USDC_MINT, one_sol)
        if not isinstance(spot, QuoteObservation) or spot.in_amount_raw != one_sol or spot.out_amount_raw <= 0:
            raise RuntimeError("SOL/USD reference quote unavailable; the PAPER book was not created")
        paper = self.loaded.settings.paper
        self.ledger.init_book(self.clock(), Decimal(spot.out_amount_raw) / Decimal(10 ** 6),
                              paper.starting_capital_usd, paper.learning_cash_usd)

    # ── gates ─────────────────────────────────────────────────────────────────────────────────────
    def blocked(self, mint: str, now: datetime) -> list[str]:
        paper, limits = self.loaded.settings.paper, self.loaded.settings.limits
        reasons = []
        if stop_file_set(self.dbs):
            reasons.append("KILL_SWITCH")
        halted = self.ledger.halted()
        if halted:
            reasons.append(f"HALTED:{halted}")
        age = heartbeat_age(self.dbs.runtime, "guardian", now)
        if age is None or not 0 <= age <= paper.guardian_heartbeat_max_age_seconds:
            reasons.append("GUARDIAN_NOT_SUPERVISING")
        if self.suspended_recently():
            reasons.append("SUSPEND_RECOVERY")
        context = latest_context(self.dbs.market, now)
        if context is None or (now - parse_iso8601(context["observed_at"])).total_seconds() > limits.stale_data_halt_seconds:
            reasons.append("STALE_MARKET_CONTEXT")
        open_rows = self.ledger.open_positions()
        if len(open_rows) >= paper.max_learning_positions:
            reasons.append("POSITION_LIMIT")
        if any(row["mint"] == mint for row in open_rows):
            reasons.append("MINT_ALREADY_OPEN")
        recent = self.dbs.paper_ledger.execute(
            "SELECT COUNT(*) FROM paper_events WHERE kind = 'ENTRY_ATTEMPT' AND at > ?",
            (to_iso(now - timedelta(minutes=1)),)).fetchone()[0]
        if recent >= limits.max_order_submissions_per_min:
            reasons.append("ORDER_RATE")
        outcomes = self.dbs.paper_ledger.execute(
            "SELECT kind, at FROM paper_events WHERE kind IN ('OPENED', 'ENTRY_FAILED') ORDER BY id DESC LIMIT ?",
            (limits.max_consecutive_failed_tx,)).fetchall()
        if (len(outcomes) == limits.max_consecutive_failed_tx and all(r["kind"] == "ENTRY_FAILED" for r in outcomes)
                and now - parse_iso8601(outcomes[0]["at"]) < FAILURE_PAUSE):
            reasons.append("CONSECUTIVE_FAILURES_PAUSE")
        loss_limit = usd_to_lamports(paper.learning_max_cumulative_loss_usd, Decimal(self.ledger.meta()["sol_usd_at_start"]))
        if self.ledger.realized_pnl_lamports() <= -loss_limit and not halted:
            self.ledger.halt("LEARNING_LOSS_LIMIT", now)
            reasons.append("HALTED:LEARNING_LOSS_LIMIT")
        return reasons

    def _sol_usd(self, now: datetime) -> Decimal | None:
        row = self.dbs.market.execute(
            "SELECT in_amount_raw, out_amount_raw FROM quotes WHERE input_mint = ? AND output_mint = ? AND outcome = 'quote'"
            " AND received_at_ms <= ? AND received_at_ms >= ? ORDER BY received_at_ms DESC, id DESC LIMIT 1",
            (SOL_MINT, USDC_MINT, to_epoch_millis(now), to_epoch_millis(now - timedelta(seconds=120)))).fetchone()
        if row is None or int(row["in_amount_raw"]) <= 0:
            return None
        return (Decimal(row["out_amount_raw"]) / Decimal(10 ** 6)) / (Decimal(row["in_amount_raw"]) / Decimal(10 ** 9))

    # ── decision → entry ──────────────────────────────────────────────────────────────────────────
    async def consider(self, candidate_id: str) -> str:
        """Called right after a security PASS, while its exact-ticket quotes are still fresh."""
        async with self._lock:
            return await self._consider(candidate_id)

    async def _consider(self, candidate_id: str) -> str:
        now = self.clock()
        context = decision_context(self.dbs.market, candidate_id, now, self.references)
        decision = evaluate_decision(candidate_id, context, self.specs, seed=self.seed)
        mint, pool = context.features.mint, context.features.pool_address
        self.ledger.event(now, "DECISION", {"mode": "PAPER", "decision_hash": decision.decision_hash,
                                            "inputs_hash": decision.inputs_hash, "chosen": decision.chosen,
                                            "regime_label": context.regime_label, "regime_status": context.regime_status.value,
                                            "ranking": [{"id": r.strategy_id, "eligible": r.eligible, "score": str(r.score),
                                                         "reasons": list(r.reasons)} for r in decision.ranking],
                                            "context": context.model_dump(mode="json")}, candidate_id=candidate_id)
        if decision.chosen == "NO_TRADE":
            return "NO_TRADE"
        spec = self.by_id[decision.chosen]
        blocked = self.blocked(mint, now)
        if blocked:
            self.ledger.event(now, "ENTRY_BLOCKED", {"reasons": blocked, "strategy": spec.strategy_id},
                              candidate_id=candidate_id)
            return "BLOCKED"
        sizing = self.loaded.settings.sizing
        fees = self.execution.fees
        sol_usd = self._sol_usd(now)
        liquidity = context.features.usable("verified_liquidity_usd")
        cap = None if sol_usd is None or liquidity is None else usd_to_lamports(
            liquidity * sizing.max_ticket_fraction_pool_liquidity, sol_usd)
        ticket = learning_ticket(
            base_lamports=self.ledger.base_lamports(), realized_pnl_lamports=self.ledger.realized_pnl_lamports(),
            fixed_fraction=sizing.fixed_fraction, max_fraction=sizing.max_ticket_fraction_equity,
            min_ticket_lamports=sol_to_lamports(sizing.min_ticket_sol), max_fixed_cost_fraction=sizing.max_fixed_cost_fraction,
            fixed_costs_lamports=2 * fees.per_transaction, rent_lamports=fees.token_account_rent_lamports,
            cash_lamports=self.ledger.cash_lamports(), liquidity_cap_lamports=cap)
        if not ticket.lamports:
            self.ledger.event(now, "ENTRY_REJECTED_SIZE", {"reasons": list(ticket.reasons), "strategy": spec.strategy_id},
                              candidate_id=candidate_id)
            return "REJECTED_SIZE"
        intent_id = "intent_" + decision.decision_hash[:32]
        self.ledger.event(now, "ENTRY_ATTEMPT", {"intent_id": intent_id, "ticket_lamports": ticket.lamports,
                                                 "sizing_capital_lamports": ticket.sizing_capital_lamports},
                          candidate_id=candidate_id)
        result = await enter(self.execution, self._quote, self.sleep, intent_id=intent_id, mint=mint,
                             ticket_lamports=ticket.lamports,
                             max_roundtrip_cost_fraction=spec.execution_requirements.max_roundtrip_cost_fraction)
        done = self.clock()
        if result.status != "FILLED" or result.profiles is None:
            kind = "ENTRY_FAILED" if result.status == "ENTRY_FAILED" else "ENTRY_REJECTED_COST"
            self.ledger.event(done, kind, {"intent_id": intent_id, "reason": result.reason, "quotes": result.quotes,
                                           "failed_fees_lamports": result.failed_fees_lamports,
                                           "roundtrip_cost_fraction": str(result.roundtrip_cost_fraction)},
                              candidate_id=candidate_id)
            return kind
        position_id = new_id("pos")
        entry = {"decision_hash": decision.decision_hash, "inputs_hash": decision.inputs_hash,
                 "spec_version_id": spec.version_id, "sizing_capital_lamports": ticket.sizing_capital_lamports,
                 "sol_usd": str(sol_usd), "entry_liquidity_usd": str(liquidity),
                 # the guardian compares like with like: the same provider-reported series at entry and later
                 "entry_reported_liquidity_usd": str(context.features.usable("reported_liquidity_usd")),
                 "quotes": result.quotes,
                 "roundtrip_cost_fraction": str(result.roundtrip_cost_fraction),
                 "profiles": {"observed": result.profiles.observed, "conservative": result.profiles.conservative,
                              "stress": result.profiles.stress},
                 "stress_extra_fees_lamports": result.stress_extra_fees_lamports}
        try:
            self.ledger.open_position(
                position_id=position_id, intent_id=intent_id, candidate_id=candidate_id, mint=mint, pool_address=pool,
                strategy_id=spec.strategy_id, strategy_version=spec.version_id, decision_hash=decision.decision_hash,
                regime_label=context.regime_label, at=done, ticket_lamports=ticket.lamports,
                tokens_raw=result.profiles.conservative, entry_fees_lamports=result.fee_lamports,
                failed_fees_lamports=result.failed_fees_lamports, rent_lamports=fees.token_account_rent_lamports,
                entry=entry)
        except sqlite3.IntegrityError as exc:   # duplicate intent or a second open position on the same mint
            self.ledger.event(done, "ENTRY_DUPLICATE_REFUSED", {"intent_id": intent_id, "error": str(exc)},
                              candidate_id=candidate_id)
            return "DUPLICATE_REFUSED"
        log.info("paper position opened", extra={"position_id": position_id, "strategy": spec.strategy_id,
                                                  "ticket_lamports": ticket.lamports})
        return "OPENED"
