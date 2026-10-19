"""LIVE entry engine (M8): approved strategy versions only, every BUY signed by the armed signer after its seven gates.

It runs inside ``desk live run`` beside the PAPER engine and decides on the same security PASS. It holds no key and
signs nothing. Per candidate it decides with the approved versions (plus the overlays every decision uses), applies
the account limits of PLAN §13.2, sizes per §13.1 against the LIVE book, takes a fresh exact-size quote and its
sell-back, and checks the same per-intent authorization the signer will apply, so intents that would be refused are
not sent. The request is journaled before it is sent; the outcome is booked from the balance changes the signer
measured. An unanswered request blocks further entries until the signer's journal says what became of it.
Exits belong to the LIVE guardian.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from desk.contracts.quotes import QuoteObservation, QuoteUnavailable
from desk.contracts.strategy import StrategyKind, StrategySpec
from desk.core.clock import parse_iso8601, to_epoch_millis, utc_now
from desk.core.config import LoadedConfig
from desk.core.ids import new_id
from desk.core.units import sol_to_lamports
from desk.live import risk
from desk.live.book import LiveBook
from desk.live.gates import approved, live_halt
from desk.live.link import Outcome, SignerLink
from desk.ops.shadow import decision_context
from desk.paper import fees as paper_fees
from desk.paper.engine import heartbeat_age
from desk.paper.model import floor_int, usd_to_lamports
from desk.providers.factory import ProviderSuite
from desk.regime.context import ReferenceCache, latest_context
from desk.security.policy import SOL_MINT, USDC_MINT
from desk.signer.intents import Intent
from desk.storage.databases import Databases
from desk.storage.market_repo import MarketRepository
from desk.storage.shadow_repo import ShadowRepository
from desk.strategies import builtin_specs, evaluate_decision
from desk.strategies.evaluate import EVALUATOR_VERSION
from desk.strategies.specs import paper_specs, superseded_specs

log = logging.getLogger(__name__)
Authorize = Callable[[Intent, datetime], list[str]]
SOL_USD_MAX_AGE = timedelta(seconds=120)
INTENT_LIFETIME_SECONDS = 25


def _usable(quote: QuoteObservation | QuoteUnavailable) -> QuoteObservation | None:
    return quote if isinstance(quote, QuoteObservation) and quote.out_amount_raw > 0 and quote.raw_sha256 else None


def quote_summary(quote: QuoteObservation) -> dict[str, Any]:
    return {"input_mint": quote.input_mint, "output_mint": quote.output_mint, "in": quote.in_amount_raw,
            "out": quote.out_amount_raw, "sha256": quote.raw_sha256, "received_at": quote.received_at.isoformat(),
            "fee_bps": quote.fee_bps, "router": quote.router}


class LiveEngine:
    def __init__(self, suite: ProviderSuite, dbs: Databases, loaded: LoadedConfig, book: LiveBook, link: SignerLink,
                 authorize: Authorize, *, seed: int = 0, clock: Callable[[], datetime] = utc_now) -> None:
        self.suite, self.dbs, self.loaded, self.book, self.link = suite, dbs, loaded, book, link
        self.authorize, self.seed, self.clock = authorize, seed, clock
        self.market = MarketRepository(dbs.market)
        self.references = ReferenceCache(dbs.market)
        self.specs = paper_specs()
        self.overlays = tuple(spec for spec in self.specs if spec.kind is StrategyKind.OVERLAY)
        self._lock = asyncio.Lock()

    @property
    def allocation_lamports(self) -> int:
        return sol_to_lamports(self.loaded.settings.live.max_capital_sol)

    async def _quote(self, input_mint: str, output_mint: str, amount: int) -> QuoteObservation | QuoteUnavailable:
        quote = await self.suite.jupiter.order(input_mint, output_mint, amount)
        self.market.save_quote(quote)
        return quote

    async def start(self) -> None:
        """Register the specifications, create or top up the book at the configured allocation, settle old requests."""
        repo = ShadowRepository(self.dbs.market)
        for specs in (builtin_specs(), superseded_specs(), self.specs):
            repo.register_specs(specs, self.clock())
        one_sol = sol_to_lamports(Decimal(1))
        spot = _usable(await self._quote(SOL_MINT, USDC_MINT, one_sol))
        if spot is None or spot.in_amount_raw != one_sol:
            raise RuntimeError("SOL/USD reference quote unavailable; the LIVE book was not created")
        added = self.book.sync_allocation(self.clock(), self.allocation_lamports, Decimal(spot.out_amount_raw) / 10 ** 6)
        if added:
            log.info("LIVE allocation recorded", extra={"added_lamports": added})
        await self.recover()

    def approved_specs(self) -> tuple[StrategySpec, ...]:
        """Hypotheses with a typed ``desk live approve`` record for their exact version (baselines never trade LIVE)."""
        return tuple(spec for spec in self.specs if spec.kind is StrategyKind.HYPOTHESIS
                     and approved(self.dbs.research, spec.version_id))

    # ── requests whose answer was lost ────────────────────────────────────────────────────────────
    async def recover(self) -> None:
        for sent in self.book.pending(("BUY",)):
            outcome = await self.link.lookup(sent["intent_id"], sent["intent"]["expires_at"], self.clock())
            if outcome.final:
                self.book_buy(sent, outcome)

    # ── decision → BUY ────────────────────────────────────────────────────────────────────────────
    async def consider(self, candidate_id: str) -> str:
        async with self._lock:
            if self.book.pending(("BUY",)):
                await self.recover()
                if self.book.pending(("BUY",)):
                    return "INTENT_UNRESOLVED"
            return await self._consider(candidate_id)

    def _safety_blocks(self, now: datetime) -> list[str]:
        reasons = []
        halt = live_halt(self.dbs.paths)
        if halt is not None:
            reasons.append(f"HALTED:{halt}")
        age = heartbeat_age(self.dbs.runtime, "guardian-live", now)
        if age is None or not 0 <= age <= self.loaded.settings.paper.guardian_heartbeat_max_age_seconds:
            reasons.append("LIVE_GUARDIAN_NOT_SUPERVISING")
        context = latest_context(self.dbs.market, now)
        if context is None or (now - parse_iso8601(context["observed_at"])).total_seconds() \
                > self.loaded.settings.limits.stale_data_halt_seconds:
            reasons.append("STALE_MARKET_CONTEXT")
        return reasons

    def _sol_usd(self, now: datetime) -> Decimal | None:
        row = self.dbs.market.execute(
            "SELECT in_amount_raw, out_amount_raw FROM quotes WHERE input_mint = ? AND output_mint = ? AND outcome = 'quote'"
            " AND received_at_ms <= ? AND received_at_ms >= ? ORDER BY received_at_ms DESC, id DESC LIMIT 1",
            (SOL_MINT, USDC_MINT, to_epoch_millis(now), to_epoch_millis(now - SOL_USD_MAX_AGE))).fetchone()
        if row is None or int(row["in_amount_raw"]) <= 0:
            return None
        return (Decimal(row["out_amount_raw"]) / Decimal(10 ** 6)) / (Decimal(row["in_amount_raw"]) / Decimal(10 ** 9))

    def _refuse(self, now: datetime, kind: str, payload: dict[str, Any], candidate_id: str, result: str) -> str:
        self.book.event(now, kind, payload, candidate_id=candidate_id)
        return result

    async def _consider(self, candidate_id: str) -> str:
        specs = self.approved_specs()
        if not specs:
            return "NO_APPROVED_STRATEGY"
        now = self.clock()
        context = decision_context(self.dbs.market, candidate_id, now, self.references)
        decision = evaluate_decision(candidate_id, context, (*specs, *self.overlays), seed=self.seed)
        mint, pool = context.features.mint, context.features.pool_address
        self.book.event(now, "DECISION", {"mode": "LIVE", "evaluator_version": EVALUATOR_VERSION, "seed": self.seed,
                                          "decision_hash": decision.decision_hash, "inputs_hash": decision.inputs_hash,
                                          "chosen": decision.chosen, "regime_label": context.regime_label,
                                          "regime_status": context.regime_status.value,
                                          "ranking": [{"id": r.strategy_id, "eligible": r.eligible, "score": str(r.score),
                                                       "reasons": list(r.reasons)} for r in decision.ranking],
                                          "context": context.model_dump(mode="json")},
                        candidate_id=candidate_id)
        if decision.chosen == "NO_TRADE":
            return "NO_TRADE"
        spec = next(s for s in specs if s.strategy_id == decision.chosen)
        settings = self.loaded.settings
        acct = risk.account(self.book, self.allocation_lamports, now)
        blocked = list(dict.fromkeys(self._safety_blocks(now) + risk.entry_blocks(self.book, acct, settings.limits,
                                                                                   mint, now)))
        if blocked:
            return self._refuse(now, "ENTRY_BLOCKED", {"reasons": blocked, "strategy": spec.strategy_id},
                                candidate_id, "BLOCKED")
        estimate = paper_fees.estimate(self.dbs.market, self.loaded, now)
        sol_usd = self._sol_usd(now)
        liquidity = context.features.usable("verified_liquidity_usd")
        cap = None if sol_usd is None or liquidity is None else usd_to_lamports(
            liquidity * settings.sizing.max_ticket_fraction_pool_liquidity, sol_usd)
        ticket = risk.live_ticket(acct, settings.sizing, settings.limits, liquidity_cap_lamports=cap,
                                  fixed_costs_lamports=estimate.round_trip, rent_lamports=estimate.rent_lamports)
        if not ticket.lamports:
            return self._refuse(now, "ENTRY_REJECTED_SIZE", {"reasons": list(ticket.reasons), "strategy": spec.strategy_id},
                                candidate_id, "REJECTED_SIZE")
        # the exact ticket's own round trip: a quote for another size cannot justify this one
        buy = _usable(await self._quote(SOL_MINT, mint, ticket.lamports))
        back = None if buy is None else _usable(await self._quote(mint, SOL_MINT, buy.out_amount_raw))
        if buy is None or back is None:
            return self._refuse(self.clock(), "ENTRY_REJECTED_COST", {"reason": "NO_ROUTE", "strategy": spec.strategy_id},
                                candidate_id, "REJECTED_COST")
        cost = Decimal(ticket.lamports - back.out_amount_raw + estimate.round_trip) / Decimal(ticket.lamports)
        if cost > spec.execution_requirements.max_roundtrip_cost_fraction:
            return self._refuse(self.clock(), "ENTRY_REJECTED_COST",
                                {"reason": "ROUNDTRIP_COST", "roundtrip_cost_fraction": str(cost),
                                 "max": str(spec.execution_requirements.max_roundtrip_cost_fraction),
                                 "quotes": [quote_summary(buy), quote_summary(back)], "strategy": spec.strategy_id},
                                candidate_id, "REJECTED_COST")
        slippage = settings.live.entry_slippage_bps
        sent_at = self.clock()
        intent = Intent(intent_id="live_" + decision.decision_hash[:32], side="BUY", mint=mint,
                        amount_raw=ticket.lamports, max_slippage_bps=slippage,
                        min_out_raw=max(1, floor_int(Decimal(buy.out_amount_raw) * (10_000 - slippage) / 10_000)),
                        quote_ref=str(buy.raw_sha256),
                        expires_at=sent_at + timedelta(seconds=min(INTENT_LIFETIME_SECONDS,
                                                                   settings.live.intent_max_age_seconds - 1)),
                        strategy_version=spec.version_id, candidate_id=candidate_id)
        problems = self.authorize(intent, sent_at)
        if problems:
            return self._refuse(sent_at, "ENTRY_BLOCKED", {"reasons": problems, "stage": "authorization",
                                                           "strategy": spec.strategy_id}, candidate_id, "BLOCKED")
        entry = {"decision_hash": decision.decision_hash, "inputs_hash": decision.inputs_hash,
                 "spec_version_id": spec.version_id, "sizing_capital_lamports": ticket.sizing_capital_lamports,
                 "sol_usd": str(sol_usd), "entry_liquidity_usd": str(liquidity),
                 "entry_reported_liquidity_usd": str(context.features.usable("reported_liquidity_usd")),
                 "quotes": [quote_summary(buy), quote_summary(back)], "roundtrip_cost_fraction": str(cost),
                 "fee_estimate": estimate.as_dict()}
        sent = {"intent_id": intent.intent_id, "kind": "BUY", "intent": intent.model_dump(mode="json"),
                "pool_address": pool, "strategy_id": spec.strategy_id, "regime_label": context.regime_label,
                "entry": entry, "candidate_id": candidate_id, "position_id": None}
        self.book.sent(sent_at, intent.intent_id, "BUY", {k: v for k, v in sent.items()
                                                          if k not in ("intent_id", "kind", "candidate_id", "position_id")},
                       candidate_id=candidate_id)
        outcome = await self.link.submit({"op": "intent", "intent": sent["intent"]})
        if not outcome.final:
            log.warning("LIVE BUY answer lost; it is resolved from the signer journal before any new entry",
                        extra={"intent_id": intent.intent_id, "problems": list(outcome.problems)})
            return "INTENT_UNRESOLVED"
        return self.book_buy(sent, outcome)

    # ── booking ───────────────────────────────────────────────────────────────────────────────────
    def book_buy(self, sent: dict[str, Any], outcome: Outcome) -> str:
        """Book a final BUY outcome exactly once, from the wallet's measured balance changes when available."""
        now, intent = self.clock(), sent["intent"]
        detail = outcome.as_dict()
        estimate = paper_fees.estimate(self.dbs.market, self.loaded, now)
        if outcome.state == "FAILED":
            paid = outcome.balances.get("sol_lamports") if outcome.balances else None
            fee = -int(paid) if isinstance(paid, int) and paid < 0 else estimate.per_transaction
            self.book.book_failed_entry(now, sent, detail, fee)
            log.warning("LIVE BUY failed on-chain", extra={"intent_id": intent["intent_id"], "fee_lamports": fee})
            return "ENTRY_FAILED"
        if outcome.state != "CONFIRMED":
            kind = "ENTRY_EXPIRED" if outcome.state == "EXPIRED" else "ENTRY_REFUSED"
            self.book.resolve(now, sent, detail, (kind, {"intent_id": intent["intent_id"], **detail}))
            return kind
        amounts = booked_entry(int(intent["amount_raw"]), int(intent["min_out_raw"]), outcome.balances, estimate)
        position_id = new_id("live")
        self.book.book_entry(now, sent, detail, {
            "position_id": position_id, "intent_id": intent["intent_id"], "candidate_id": intent["candidate_id"],
            "mint": intent["mint"], "pool_address": sent["pool_address"], "strategy_id": sent["strategy_id"],
            "strategy_version": intent["strategy_version"], "decision_hash": sent["entry"]["decision_hash"],
            "regime_label": sent["regime_label"], "at": now, "ticket_lamports": amounts["ticket_lamports"],
            "tokens_raw": amounts["tokens_raw"], "entry_fees_lamports": amounts["fees_lamports"],
            "failed_fees_lamports": 0, "rent_lamports": amounts["rent_lamports"],
            "entry": {**sent["entry"], "signature": outcome.signature, "balances": outcome.balances, "booking": amounts,
                      # LIVE has one profile: what the chain reported
                      "profiles": {"observed": amounts["tokens_raw"], "conservative": amounts["tokens_raw"],
                                   "stress": amounts["tokens_raw"]}}})
        log.info("LIVE position opened", extra={"intent_id": intent["intent_id"], "signature": outcome.signature})
        return "OPENED"


def booked_entry(amount: int, min_out: int, balances: dict[str, Any] | None, estimate: paper_fees.FeeEstimate) -> dict[str, Any]:
    """What a confirmed BUY cost, split so that ticket + fees + rent equals the SOL that left the wallet.

    Without measured balances (a lagging node) the intent's guaranteed bounds are booked and flagged; the guardian
    later sells the wallet's actual token balance, so the error is limited to the entry's fee estimate.
    """
    spent = -int(balances.get("sol_lamports", 0)) if balances else 0
    tokens = int(balances.get("output_token_raw", 0)) if balances else 0
    if spent <= 0 or tokens <= 0:
        return {"ticket_lamports": amount, "tokens_raw": min_out, "fees_lamports": estimate.per_transaction,
                "rent_lamports": estimate.rent_lamports, "source": "INTENT_BOUNDS_RECONCILE_ON_CHAIN"}
    assert balances is not None
    rent = int(balances.get("output_account_lamports", 0)) if balances.get("output_account_created") else 0
    ticket = min(amount, spent)
    rent = min(rent, spent - ticket)
    return {"ticket_lamports": ticket, "tokens_raw": tokens, "fees_lamports": spent - ticket - rent,
            "rent_lamports": rent, "source": "MEASURED_BALANCES"}


__all__ = ["LiveEngine", "booked_entry", "quote_summary"]
