"""LIVE guardian (M8): PAPER's marks and exit rules on the LIVE book; every exit goes through the signer.

It runs as its own process (``desk live guardian``: heartbeat ``guardian-live``, its own lock and provider clients).
Marks, impairment and the eight exit policies are the PAPER guardian's. An exit sells the wallet's actual on-chain
token balance, read from the chain so nothing is left behind by an estimated booking, against a fresh quote with
the configured exit slippage. The sale is booked from the wallet's measured balance change; tokens still left are
sold again, and then a close-account request returns the token account's rent. A position closes in the book only
when its account is closed (or found already closed), at exactly what the wallet received. Each request is
journaled before it is sent, and a lost answer is looked up in the signer's journal before that position is
touched again. Exits never need a strategy's permission, and halts never stop them.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from desk.contracts.quotes import QuoteObservation, QuoteUnavailable
from desk.core.clock import parse_iso8601, utc_now
from desk.core.config import LoadedConfig
from desk.live.book import LiveBook
from desk.live.engine import INTENT_LIFETIME_SECONDS, quote_summary
from desk.live.gates import LIVE_GUARDIAN
from desk.live.link import Outcome, SignerLink
from desk.paper import fees as paper_fees
from desk.paper.executor import MAX_ATTEMPTS
from desk.paper.guardian import BLOCKED_RETRY_SECONDS, Guardian
from desk.paper.model import floor_int
from desk.providers.factory import ProviderSuite
from desk.security.policy import SOL_MINT
from desk.signer import programs
from desk.signer.inspect import associated_token_address, token_amount
from desk.signer.intents import CloseAccount, Intent
from desk.storage.databases import Databases
from desk.strategies.specs import paper_specs, superseded_specs

log = logging.getLogger(__name__)
NODE_LAG_GRACE_SECONDS = 120     # an empty read this soon after the purchase may be a node that is behind


class LiveGuardian(Guardian):
    heartbeat_name = LIVE_GUARDIAN
    lock_name = "live-guardian.lock"
    display_name = "independent LIVE guardian"

    def __init__(self, suite: ProviderSuite, dbs: Databases, loaded: LoadedConfig, book: LiveBook, link: SignerLink, *,
                 clock: Callable[[], datetime] = utc_now, sleep: Callable[[float], Any] = asyncio.sleep) -> None:
        super().__init__(suite, dbs, loaded, clock=clock, sleep=sleep)
        self.ledger = self.book = book
        self.link = link
        self.live = loaded.settings.live
        self.specs = {spec.version_id: spec for spec in (*superseded_specs(), *paper_specs())}
        self._wallet: str | None = None

    # ── chain and signer reads ────────────────────────────────────────────────────────────────────
    async def wallet(self) -> str | None:
        if self._wallet is None:
            self._wallet = await self.link.wallet()
        return self._wallet

    async def held(self, mint: str) -> int | None:
        """The wallet's on-chain balance of ``mint`` (0 without a token account; None when it cannot be read)."""
        wallet = await self.wallet()
        if wallet is None:
            return None
        addresses = [associated_token_address(wallet, mint, program) for program in (programs.TOKEN, programs.TOKEN_2022)]
        try:
            result, _ = await self.suite.rpc.get_multiple_accounts(addresses, encoding="base64")
            accounts = result["value"]
            return sum(token_amount(account) for account in accounts if account)
        except Exception:  # unreadable is unknown, never zero
            return None

    async def _quote(self, input_mint: str, output_mint: str, amount: int) -> QuoteObservation | QuoteUnavailable:
        quote = await self.suite.jupiter.order(input_mint, output_mint, amount)
        self.market.save_quote(quote)
        return quote

    # ── loop ──────────────────────────────────────────────────────────────────────────────────────
    async def tick(self) -> None:
        self.heartbeat()
        await self.recover()
        await super().tick()

    async def recover(self) -> None:
        """Book every request whose answer was lost, from the signer's journal, before anything else is sent."""
        for sent in self.book.pending(("SELL", "CLOSE")):
            request = sent["intent"] if sent["kind"] == "SELL" else sent["close"]
            outcome = await self.link.lookup(sent["intent_id"], request["expires_at"], self.clock())
            if outcome.final:
                await self.settle(sent, outcome)

    def _retry_due(self, row: Any, now: datetime) -> bool:
        if row["state"] != "EXIT_BLOCKED":
            return True
        last = self.book.conn.execute(
            "SELECT at FROM paper_events WHERE position_id = ? AND kind IN ('EXIT_FAILED', 'EXIT_BLOCKED')"
            " ORDER BY id DESC LIMIT 1", (row["id"],)).fetchone()
        return last is None or (now - parse_iso8601(last["at"])).total_seconds() >= BLOCKED_RETRY_SECONDS

    async def supervise(self, row: Any, now: datetime) -> None:
        if any(sent["position_id"] == row["id"] for sent in self.book.pending(("SELL", "CLOSE"))):
            return                      # an answer is outstanding: never send a second request for this position
        if self.book.sold(row["id"]) is None:
            await super().supervise(row, now)          # marks, impairment, exit rules, then try_exit
        elif self._retry_due(row, now):
            await self.finish(row, now)

    # ── exits ─────────────────────────────────────────────────────────────────────────────────────
    async def try_exit(self, row: Any, reason: str, now: datetime) -> None:
        if row["state"] not in ("EXITING", "EXIT_BLOCKED"):
            self.book.set_state(row["id"], "EXITING", now, {"reason": reason, "attempts": row["exit_attempts"]})
            row = self.book.position(row["id"])
        held = await self.held(row["mint"])
        if held == 0 and (now - parse_iso8601(row["opened_at"])).total_seconds() > NODE_LAG_GRACE_SECONDS:
            self._tokens_missing(row, reason, now)
            return
        # unknown, or a node that has not seen a fresh purchase yet: the booked amount (the signer checks the balance)
        await self.sell(row, reason, held or row["tokens_raw"])

    async def exit_now(self, row: Any, reason: str) -> None:
        """Liquidation: exit this position now, whatever its marks say (pending answers are looked up first)."""
        await self.recover()
        row = self.book.position(row["id"])
        if row["state"] == "CLOSED" or any(s["position_id"] == row["id"] for s in self.book.pending(("SELL", "CLOSE"))):
            return
        if self.book.sold(row["id"]) is None:
            await self.try_exit(row, reason, self.clock())
        else:
            await self.finish(row, self.clock())

    async def sell(self, row: Any, reason: str, amount: int) -> None:
        quote = await self._quote(row["mint"], SOL_MINT, amount)
        if not isinstance(quote, QuoteObservation) or quote.out_amount_raw <= 0 or not quote.raw_sha256:
            self._exit_problem(row, reason, "NO_SELL_ROUTE")
            return
        slippage, sent_at = self.live.exit_slippage_bps, self.clock()
        intent = Intent(intent_id=f"sell_{row['id']}_{self.book.sent_count(row['id'], 'SELL') + 1}", side="SELL",
                        mint=row["mint"], amount_raw=amount, max_slippage_bps=slippage,
                        min_out_raw=max(1, floor_int(Decimal(quote.out_amount_raw) * (10_000 - slippage) / 10_000)),
                        quote_ref=quote.raw_sha256, expires_at=sent_at + timedelta(seconds=min(
                            INTENT_LIFETIME_SECONDS, self.live.intent_max_age_seconds - 1)))
        payload = {"intent": intent.model_dump(mode="json"), "reason": reason, "quote": quote_summary(quote)}
        self.book.sent(sent_at, intent.intent_id, "SELL", payload, position_id=row["id"])
        outcome = await self.link.submit({"op": "intent", "intent": payload["intent"]})
        if outcome.final:
            await self.settle({"intent_id": intent.intent_id, "kind": "SELL", "position_id": row["id"],
                               "candidate_id": None, **payload}, outcome)

    async def finish(self, row: Any, now: datetime) -> None:
        """After a sale: sell anything left, else close the token account (its rent comes back to the wallet)."""
        held = await self.held(row["mint"])
        if held:
            await self.sell(row, "SELL_REMAINDER", held)
            return
        sent_at = self.clock()
        request = CloseAccount(intent_id=f"close_{row['id']}_{self.book.sent_count(row['id'], 'CLOSE') + 1}",
                               mint=row["mint"], expires_at=sent_at + timedelta(seconds=INTENT_LIFETIME_SECONDS))
        payload = {"close": request.model_dump(mode="json")}
        self.book.sent(sent_at, request.intent_id, "CLOSE", payload, position_id=row["id"])
        outcome = await self.link.submit({"op": "close", "close": payload["close"]})
        if outcome.final:
            await self.settle({"intent_id": request.intent_id, "kind": "CLOSE", "position_id": row["id"],
                               "candidate_id": None, **payload}, outcome)

    # ── booking ───────────────────────────────────────────────────────────────────────────────────
    async def settle(self, sent: dict[str, Any], outcome: Outcome) -> None:
        """Book a final SELL or CLOSE outcome exactly once; a confirmed sale continues straight to the close."""
        now, detail = self.clock(), outcome.as_dict()
        row = self.book.position(sent["position_id"])
        estimate = paper_fees.estimate(self.dbs.market, self.loaded, now)
        if sent["kind"] == "SELL" and outcome.state == "CONFIRMED":
            if self.book.book_sale(now, sent, detail, sale_from(sent, outcome, estimate.per_transaction)):
                self.stats["exits"] += 1
                log.info("LIVE tokens sold", extra={"position_id": row["id"], "signature": outcome.signature})
                await self.finish(self.book.position(row["id"]), now)
            return
        if sent["kind"] == "CLOSE" and (outcome.state == "CONFIRMED" or "NO_TOKEN_ACCOUNT" in outcome.problems):
            sale = self.book.sold(row["id"])
            if sale is None or row["state"] == "CLOSED":
                self.book.resolve(now, sent, detail)
                return
            refund = 0                                   # no account: the sale's own transaction already closed it
            if outcome.state == "CONFIRMED":
                measured = outcome.balances.get("sol_lamports") if outcome.balances else None
                refund = int(measured) if isinstance(measured, int) else row["rent_lamports"] - estimate.per_transaction
            # the book refunds the rent it locked; the wallet's actual changes are the sale and this refund
            exit_lamports = sale["sol_lamports"] + refund - row["rent_lamports"]
            self.book.book_close(now, sent, detail, reason=row["exit_reason"] or sale["reason"],
                                 exit_lamports=exit_lamports,
                                 profiles={"source": "MEASURED_BALANCES", "sales": sale["sales"],
                                           "close": {"intent_id": sent["intent_id"], "signature": outcome.signature,
                                                     "refund_lamports": refund, "state": outcome.state}})
            log.info("LIVE position closed", extra={"position_id": row["id"]})
            return
        # failed, expired or refused: retried on the bounded schedule; the position stays supervised
        fee = 0
        if outcome.state == "FAILED":
            paid = outcome.balances.get("sol_lamports") if outcome.balances else None
            fee = -int(paid) if isinstance(paid, int) and paid < 0 else estimate.per_transaction
        close_failures = self.book.conn.execute(
            "SELECT COUNT(*) FROM paper_events WHERE position_id = ? AND kind = 'EXIT_FAILED'"
            " AND json_extract(payload_json, '$.step') = 'CLOSE'", (row["id"],)).fetchone()[0]
        attempts = row["exit_attempts"] + 1 if sent["kind"] == "SELL" else row["exit_attempts"]
        blocked = attempts >= MAX_ATTEMPTS if sent["kind"] == "SELL" else close_failures + 1 >= MAX_ATTEMPTS
        state = "EXIT_BLOCKED" if blocked else "EXITING"
        self.stats["exit_failures"] += 1
        self.book.book_exit_failure(now, sent, detail, state=state, fee_lamports=fee, payload={
            "reason": sent.get("reason") or row["exit_reason"] or "EXIT", "attempts": attempts, "step": sent["kind"],
            "status": outcome.state, "problems": list(outcome.problems), "fee_lamports": fee})
        if blocked:
            log.critical("LIVE exit blocked; the position remains supervised and is retried",
                         extra={"position_id": row["id"], "step": sent["kind"], "problems": list(outcome.problems)})

    def _exit_problem(self, row: Any, reason: str, problem: str) -> None:
        """Nothing was sent (no route, no signer): count the attempt; the position stays supervised."""
        attempts = row["exit_attempts"] + 1
        state = "EXIT_BLOCKED" if attempts >= MAX_ATTEMPTS else "EXITING"
        now = self.clock()
        self.book.set_state(row["id"], state, now, {"reason": reason, "attempts": attempts, "status": problem})
        self.book.event(now, "EXIT_FAILED", {"reason": reason, "attempts": attempts, "status": problem},
                        position_id=row["id"])
        self.stats["exit_failures"] += 1
        if state == "EXIT_BLOCKED":
            log.critical("LIVE exit blocked; the position remains supervised", extra={"position_id": row["id"],
                                                                                    "problem": problem})

    def _tokens_missing(self, row: Any, reason: str, now: datetime) -> None:
        """The book holds an unsold position the wallet does not: stop new entries until the operator reconciles."""
        if row["state"] != "EXIT_BLOCKED":
            self.book.set_state(row["id"], "EXIT_BLOCKED", now, {"reason": reason, "attempts": row["exit_attempts"],
                                                                 "status": "TOKENS_NOT_IN_WALLET"})
        if not self.book.halted():
            self.book.halt("RECONCILE_REQUIRED", now)
        log.critical("LIVE position's tokens are not in the wallet; entries halted until reconciled",
                     extra={"position_id": row["id"], "mint": row["mint"]})


def sale_from(sent: dict[str, Any], outcome: Outcome, fee_estimate: int) -> dict[str, Any]:
    """What a confirmed SELL brought in: the wallet's measured change, else the guaranteed minimum less a fee."""
    intent = sent["intent"]
    received = outcome.balances.get("sol_lamports") if outcome.balances else None
    spent = outcome.balances.get("input_token_raw") if outcome.balances else None
    if isinstance(received, int) and isinstance(spent, int) and spent < 0:
        return {"intent_id": sent["intent_id"], "signature": outcome.signature, "reason": sent["reason"],
                "sol_lamports": received, "tokens_sold": -spent, "source": "MEASURED_BALANCES"}
    return {"intent_id": sent["intent_id"], "signature": outcome.signature, "reason": sent["reason"],
            "sol_lamports": int(intent["min_out_raw"]) - fee_estimate, "tokens_sold": int(intent["amount_raw"]),
            "source": "INTENT_BOUNDS_RECONCILE_ON_CHAIN"}


__all__ = ["LiveGuardian", "sale_from"]
