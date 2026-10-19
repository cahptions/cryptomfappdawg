"""The signer's decision procedure (PLAN §16.2). Order → inspect → simulate → sign → submit → settle, journaled.

Refusals are final: a refused or expired intent is never retried by the signer. After a submission it never
resubmits blindly; it polls the signature until ``lastValidBlockHeight`` has passed. While any earlier intent is
unresolved, new intents are refused. Withdrawals go only to the single pre-registered address.
A BUY needs a per-intent authorizer (``desk.live.authorize.LiveAuthorizer`` in production) that re-establishes
all seven LIVE gates for that exact intent before every check that may lead to signing. Without one, every BUY
is refused. An unlocked key, positive capital setting, or displayed gate report alone cannot authorize a BUY.
"""

from __future__ import annotations

import asyncio
import base64
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Protocol

from pydantic import ValidationError
from solders.hash import Hash
from solders.instruction import AccountMeta, Instruction
from solders.keypair import Keypair
from solders.message import MessageV0
from solders.pubkey import Pubkey
from solders.signature import Signature
from solders.system_program import TransferParams, transfer
from solders.transaction import VersionedTransaction

from desk.core.clock import utc_now
from desk.core.config import LimitsSection, LiveSection, SizingSection
from desk.core.units import sol_to_lamports
from desk.signer import programs as p
from desk.signer.inspect import (
    SwapExpectation,
    associated_token_address,
    check_simulation,
    inspect_swap,
    token_amount,
)
from desk.signer.intents import CloseAccount, Intent, Withdrawal
from desk.signer.journal import Journal

SIGNATURE_FEE = 5000
POLL_SECONDS = 2.0
LIVE_ENTRY_AUTHORIZATION_UNAVAILABLE = "LIVE_ENTRY_AUTHORIZATION_UNAVAILABLE"

# This is an internal dependency, never a request field, environment switch, or UI capability.
# The production implementation (desk.live.authorize) independently establishes all seven LIVE gates
# for this exact intent from fresh evidence. None refuses every BUY.
# Synchronous evaluation keeps the final check and signing in one event-loop turn.
BuyAuthorizer = Callable[[Intent, datetime], list[str]]


class SwapApi(Protocol):
    async def order(self, input_mint: str, output_mint: str, amount: int, taker: str, slippage_bps: int) -> dict[str, Any]: ...
    async def execute(self, signed_transaction_b64: str, request_id: str) -> dict[str, Any]: ...


class RpcApi(Protocol):
    async def block_height(self) -> int: ...
    async def accounts(self, addresses: list[str]) -> list[dict[str, Any] | None]: ...
    async def simulate(self, transaction_b64: str, addresses: list[str]) -> dict[str, Any]: ...
    async def signature_status(self, signature: str) -> dict[str, Any] | None: ...
    async def latest_blockhash(self) -> tuple[str, int]: ...
    async def send(self, transaction_b64: str) -> str: ...


def refusal(error: str, **detail: Any) -> dict[str, Any]:
    return {"ok": False, "error": error, **detail}


class Signer:
    def __init__(self, keypair: Keypair, journal: Journal, swap: SwapApi, rpc: RpcApi, live: LiveSection,
                 limits: LimitsSection, sizing: SizingSection, *, clock: Callable[[], datetime] = utc_now,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
                 buy_authorizer: BuyAuthorizer | None = None) -> None:
        self._keypair = keypair
        self.wallet = str(keypair.pubkey())
        self.journal, self.swap, self.rpc = journal, swap, rpc
        self.live, self.limits, self.sizing = live, limits, sizing
        self.clock, self.sleep = clock, sleep
        self._buy_authorizer = buy_authorizer
        self.disarmed = False

    @property
    def ticket_cap_lamports(self) -> int:
        return sol_to_lamports(self.live.max_capital_sol * self.sizing.max_ticket_fraction_equity)

    @property
    def fee_budget_lamports(self) -> int:
        return 2 * SIGNATURE_FEE + self.live.max_priority_fee_lamports + 2 * 2_039_280

    async def handle(self, request: Any) -> dict[str, Any]:
        """The only entry point. Anything that is not one of six structured operations is refused."""
        if self.disarmed:
            return refusal("DISARMED")
        if not isinstance(request, dict) or not set(request) <= {"op", "intent", "withdrawal", "close", "intent_id"}:
            return refusal("UNKNOWN_FIELDS")
        op = request.get("op")
        if op == "status":
            return {"ok": True, "wallet": self.wallet, "unlocked": True, "unresolved": len(self.journal.unresolved()),
                    "ticket_cap_lamports": self.ticket_cap_lamports, "buy_enabled": self._buy_authorizer is not None,
                    "reason": LIVE_ENTRY_AUTHORIZATION_UNAVAILABLE if self._buy_authorizer is None
                    else "PER_INTENT_AUTHORIZATION_REQUIRED"}
        if op == "intent" and set(request) == {"op", "intent"}:
            return await self.intent(request["intent"])
        if op == "withdraw" and set(request) == {"op", "withdrawal"}:
            return await self.withdraw(request["withdrawal"])
        if op == "close" and set(request) == {"op", "close"}:
            return await self.close_account(request["close"])
        if op == "lookup" and set(request) == {"op", "intent_id"}:
            return self.lookup(request["intent_id"])
        if op == "disarm" and set(request) == {"op"}:
            self.disarmed = True
            return {"ok": True, "disarmed": True}
        return refusal("UNKNOWN_OP")              # e.g. "sign", "sign_transaction": raw signing does not exist

    # ── swaps ─────────────────────────────────────────────────────────────────────────────────────
    async def intent(self, raw: Any) -> dict[str, Any]:
        try:
            intent = Intent.model_validate(raw)
        except ValidationError as exc:
            return refusal("INVALID_INTENT", detail=[e["msg"] for e in exc.errors()][:5])
        now = self.clock()
        existing = self.journal.get(intent.intent_id)
        if existing is not None or not self.journal.claim(intent.intent_id, "SWAP", intent.model_dump(mode="json"), now):
            state = existing["state"] if existing else "RECEIVED"
            return refusal("DUPLICATE_INTENT", state=state, signature=existing["signature"] if existing else None)
        others = [r for r in self.journal.unresolved() if r["intent_id"] != intent.intent_id]
        problems = self._precheck(intent, now, others)
        if problems:
            return self._refuse(intent.intent_id, problems)
        buying = intent.side == "BUY"
        input_mint, output_mint = (p.WSOL, intent.mint) if buying else (intent.mint, p.WSOL)
        expected = SwapExpectation(wallet=self.wallet, side=intent.side, input_mint=input_mint, output_mint=output_mint,
                                   amount_raw=intent.amount_raw, min_out_raw=intent.min_out_raw,
                                   max_priority_fee_lamports=self.live.max_priority_fee_lamports,
                                   allowed_fee_destinations=frozenset(self.live.allowed_fee_destinations))
        try:
            if buying:
                balance = (await self.rpc.accounts([self.wallet]))[0]
                spendable = (0 if balance is None else int(balance["lamports"])) \
                    - sol_to_lamports(self.limits.live_sol_reserve) - self.fee_budget_lamports
                if intent.amount_raw > spendable:
                    return self._refuse(intent.intent_id, ["INSUFFICIENT_BALANCE_ABOVE_RESERVE"])
            # Account retrieval can await while a STOP, approval, or readiness condition changes.
            problems = self._precheck(intent, self.clock(), self._other_unresolved(intent.intent_id))
            if problems:
                return self._refuse(intent.intent_id, problems)
            order = await self.swap.order(input_mint, output_mint, intent.amount_raw, self.wallet, intent.max_slippage_bps)
            transaction = order.get("transaction")
            if not transaction or order.get("errorCode") or order.get("error"):
                return self._refuse(intent.intent_id, [f"NO_ORDER:{order.get('errorCode') or order.get('error')}"])
            problems = inspect_swap(transaction, order, expected, await self.rpc.block_height())
            if problems:
                return self._refuse(intent.intent_id, problems)
            tracked = [self.wallet, self._own_account(transaction, input_mint), self._own_account(transaction, output_mint)]
            pre = await self.rpc.accounts(tracked)
            simulation = await self.rpc.simulate(transaction, tracked)
            problems = check_simulation(simulation, pre, expected, self.fee_budget_lamports)
            if problems:
                return self._refuse(intent.intent_id, problems)
        except Exception as exc:  # any failure before signing is a refusal, never a signature
            return self._refuse(intent.intent_id, [f"PRE_SIGNING_FAILURE:{type(exc).__name__}"])
        unsigned = VersionedTransaction.from_bytes(base64.b64decode(transaction))
        # Re-establish permission after every network wait. No await may separate this check
        # from signing: an entry permission established before simulation is not sufficient.
        problems = self._precheck(intent, self.clock(), self._other_unresolved(intent.intent_id))
        if problems:
            return self._refuse(intent.intent_id, problems)
        signed = VersionedTransaction(unsigned.message, [self._keypair])
        signature = str(signed.signatures[0])
        last_valid = int(order["lastValidBlockHeight"])
        self.journal.update(intent.intent_id, "SIGNED", self.clock(), {"request_id": order.get("requestId")},
                            signature=signature, last_valid_block_height=last_valid)
        response: dict[str, Any] | None = None
        try:
            response = await self.swap.execute(base64.b64encode(bytes(signed)).decode(), str(order.get("requestId")))
        except Exception as exc:  # a timeout may still land: poll, never resubmit
            response = {"status": "UNKNOWN", "error": type(exc).__name__}
        self.journal.update(intent.intent_id, "SUBMITTED", self.clock(), {"execute": response})
        result = await self.settle(intent.intent_id, signature, last_valid)
        if result.get("state") == "CONFIRMED":
            # what actually changed on-chain: the LIVE ledger books these amounts, never the quote
            result["balances"] = await self._balance_changes(tracked, pre)
            self.journal.update(intent.intent_id, "CONFIRMED", self.clock(), {"balances": result["balances"]})
        return result

    async def _balance_changes(self, tracked: list[str], pre: list[dict[str, Any] | None]) -> dict[str, Any] | None:
        """Wallet SOL and own token-account changes of a landed swap; ``tracked`` is [wallet, input, output]."""
        def amount(account: dict[str, Any] | None) -> int:
            return token_amount(account) if account and "data" in account else 0

        for _ in range(3):
            try:
                post = await self.rpc.accounts(tracked)
            except Exception:  # unknown is reported as unknown; the caller reconciles
                post = []
            # a landed transaction always changes the fee payer's balance; an unchanged one is a lagging node
            if len(post) == 3 and post[0] is not None and pre and pre[0] is not None \
                    and int(post[0]["lamports"]) != int(pre[0]["lamports"]):
                created = pre[2] is None and post[2] is not None
                return {"sol_lamports": int(post[0]["lamports"]) - int(pre[0]["lamports"]),
                        "input_token_raw": amount(post[1]) - amount(pre[1]),
                        "output_token_raw": amount(post[2]) - amount(pre[2]),
                        "output_account_created": created,
                        "output_account_lamports": int(post[2]["lamports"]) if created and post[2] else 0}
            await self.sleep(POLL_SECONDS)
        return None

    def _precheck(self, intent: Intent, now: datetime, others: list[dict[str, Any]]) -> list[str]:
        problems = []
        if self.disarmed:
            problems.append("DISARMED")
        if others:
            problems.append("PREVIOUS_INTENT_UNRESOLVED")
        if intent.expires_at <= now:
            problems.append("INTENT_EXPIRED")
        elif intent.expires_at > now + timedelta(seconds=self.live.intent_max_age_seconds):
            problems.append("INTENT_EXPIRY_TOO_FAR")
        if intent.side == "BUY":
            if self.live.max_capital_sol <= 0:
                problems.append("NO_CAPITAL_ALLOCATION")
            if intent.amount_raw > self.ticket_cap_lamports:
                problems.append("AMOUNT_ABOVE_CAP")
            problems.extend(self._authorize_buy(intent, now))
        return problems

    def _authorize_buy(self, intent: Intent, now: datetime) -> list[str]:
        if self._buy_authorizer is None:
            return [LIVE_ENTRY_AUTHORIZATION_UNAVAILABLE]
        try:
            problems = self._buy_authorizer(intent, now)
        except Exception:
            return ["LIVE_ENTRY_AUTHORIZATION_FAILED"]
        if not isinstance(problems, list) or any(not isinstance(p, str) or not p for p in problems):
            return ["LIVE_ENTRY_AUTHORIZATION_INVALID"]
        return problems

    def _other_unresolved(self, intent_id: str) -> list[dict[str, Any]]:
        return [row for row in self.journal.unresolved() if row["intent_id"] != intent_id]

    def _own_account(self, transaction_b64: str, mint: str) -> str:
        if mint == p.WSOL:
            return self.wallet
        keys = {str(k) for k in VersionedTransaction.from_bytes(base64.b64decode(transaction_b64)).message.account_keys}
        for program in (p.TOKEN, p.TOKEN_2022):
            candidate = associated_token_address(self.wallet, mint, program)
            if candidate in keys:
                return candidate
        return associated_token_address(self.wallet, mint, p.TOKEN)

    def _refuse(self, intent_id: str, problems: list[str]) -> dict[str, Any]:
        self.journal.update(intent_id, "REFUSED", self.clock(), {"problems": problems})
        return refusal("REFUSED", problems=problems, intent_id=intent_id)

    async def settle(self, intent_id: str, signature: str, last_valid_block_height: int) -> dict[str, Any]:
        """Poll the signature until it confirms, fails, or its blockhash can no longer land."""
        while True:
            status = await self.rpc.signature_status(signature)
            if status is not None and status.get("confirmationStatus") in ("confirmed", "finalized"):
                state = "CONFIRMED" if status.get("err") is None else "FAILED"
                self.journal.update(intent_id, state, self.clock(), {"status": status})
                return {"ok": state == "CONFIRMED", "state": state, "signature": signature, "intent_id": intent_id}
            if await self.rpc.block_height() > last_valid_block_height:
                self.journal.update(intent_id, "EXPIRED", self.clock(), {"reason": "blockhash expired without landing"})
                return {"ok": False, "state": "EXPIRED", "signature": signature, "intent_id": intent_id}
            await self.sleep(POLL_SECONDS)

    async def recover(self) -> list[dict[str, Any]]:
        """After a crash: resolve signed/submitted intents by polling; never sign anything again."""
        results = []
        for row in self.journal.unresolved():
            if row["state"] == "RECEIVED" or not row["signature"]:
                self.journal.update(row["intent_id"], "REFUSED", self.clock(), {"problems": ["CRASHED_BEFORE_SIGNING"]})
                results.append({"intent_id": row["intent_id"], "state": "REFUSED"})
            else:
                results.append(await self.settle(row["intent_id"], row["signature"], int(row["last_valid_block_height"])))
        return results

    def lookup(self, intent_id: Any) -> dict[str, Any]:
        """Read-only: an intent's journaled outcome, so a restarted engine never repeats a trade that landed."""
        row = self.journal.get(intent_id) if isinstance(intent_id, str) else None
        if row is None:
            return {"ok": True, "found": False}
        return {"ok": True, "found": True, "state": row["state"], "signature": row["signature"],
                "kind": row["kind"], "detail": row["detail"]}

    # ── rent reclaim: close the wallet's own empty token account ───────────────────────────────────
    async def close_account(self, raw: Any) -> dict[str, Any]:
        try:
            request = CloseAccount.model_validate(raw)
        except ValidationError as exc:
            return refusal("INVALID_CLOSE", detail=[e["msg"] for e in exc.errors()][:5])
        now = self.clock()
        if not self.journal.claim(request.intent_id, "CLOSE", request.model_dump(mode="json"), now):
            return refusal("DUPLICATE_INTENT")
        problems = []
        if request.expires_at <= now:
            problems.append("INTENT_EXPIRED")
        if self._other_unresolved(request.intent_id):
            problems.append("PREVIOUS_INTENT_UNRESOLVED")
        if problems:
            return self._refuse(request.intent_id, problems)
        candidates = [associated_token_address(self.wallet, request.mint, program) for program in (p.TOKEN, p.TOKEN_2022)]
        try:
            accounts = await self.rpc.accounts([self.wallet, *candidates])
            wallet_before = accounts[0]
            found = [(address, account) for address, account in zip(candidates, accounts[1:], strict=True) if account]
            if not found:
                return self._refuse(request.intent_id, ["NO_TOKEN_ACCOUNT"])
            address, account = found[0]
            program = str(account.get("owner"))
            if program not in p.TOKEN_PROGRAMS or associated_token_address(self.wallet, request.mint, program) != address:
                return self._refuse(request.intent_id, ["NOT_WALLETS_OWN_TOKEN_ACCOUNT"])
            if token_amount(account) != 0:
                return self._refuse(request.intent_id, ["TOKEN_BALANCE_NOT_ZERO"])
            if wallet_before is None:
                return self._refuse(request.intent_id, ["WALLET_UNREADABLE"])
            rent = int(account["lamports"])
            blockhash, last_valid = await self.rpc.latest_blockhash()
            owner = self._keypair.pubkey()
            instruction = Instruction(Pubkey.from_string(program), bytes([p.TOKEN_CLOSE_ACCOUNT]),
                                      [AccountMeta(Pubkey.from_string(address), False, True),
                                       AccountMeta(owner, False, True), AccountMeta(owner, True, False)])
            message = MessageV0.try_compile(owner, [instruction], [], Hash.from_string(blockhash))
            unsigned = VersionedTransaction.populate(message, [Signature.default()])
            simulation = await self.rpc.simulate(base64.b64encode(bytes(unsigned)).decode(), [self.wallet])
            if simulation.get("err") is not None:
                return self._refuse(request.intent_id, [f"SIMULATION_FAILED:{simulation.get('err')}"])
            post = (simulation.get("accounts") or [None])[0]
            gained = None if post is None else int(post["lamports"]) - int(wallet_before["lamports"])
            if gained is None or not rent - SIGNATURE_FEE <= gained <= rent:
                return self._refuse(request.intent_id, ["SIMULATED_REFUND_UNEXPECTED"])
        except Exception as exc:
            return self._refuse(request.intent_id, [f"PRE_SIGNING_FAILURE:{type(exc).__name__}"])
        signed = VersionedTransaction(message, [self._keypair])
        signature = str(signed.signatures[0])
        self.journal.update(request.intent_id, "SIGNED", self.clock(), {"account": address, "rent_lamports": rent},
                            signature=signature, last_valid_block_height=last_valid)
        try:
            await self.rpc.send(base64.b64encode(bytes(signed)).decode())
        except Exception as exc:  # may still land; poll
            self.journal.update(request.intent_id, "SUBMITTED", self.clock(), {"send_error": type(exc).__name__})
        else:
            self.journal.update(request.intent_id, "SUBMITTED", self.clock(), {})
        result = await self.settle(request.intent_id, signature, last_valid)
        if result.get("state") == "CONFIRMED":
            try:
                after = (await self.rpc.accounts([self.wallet]))[0]
                result["balances"] = None if after is None else {
                    "sol_lamports": int(after["lamports"]) - int(wallet_before["lamports"])}
            except Exception:
                result["balances"] = None
            self.journal.update(request.intent_id, "CONFIRMED", self.clock(), {"balances": result["balances"]})
        return result

    # ── the single transfer capability ────────────────────────────────────────────────────────────
    async def withdraw(self, raw: Any) -> dict[str, Any]:
        try:
            request = Withdrawal.model_validate(raw)
        except ValidationError as exc:
            return refusal("INVALID_WITHDRAWAL", detail=[e["msg"] for e in exc.errors()][:5])
        now = self.clock()
        if not self.journal.claim(request.intent_id, "WITHDRAW", request.model_dump(mode="json"), now):
            return refusal("DUPLICATE_INTENT")
        problems = []
        if not self.live.withdraw_address or request.destination != self.live.withdraw_address:
            problems.append("DESTINATION_NOT_REGISTERED")
        if request.confirmation != f"WITHDRAW {request.lamports} LAMPORTS TO {request.destination}":
            problems.append("CONFIRMATION_MISMATCH")
        if request.expires_at <= now:
            problems.append("INTENT_EXPIRED")
        if [r for r in self.journal.unresolved() if r["intent_id"] != request.intent_id]:
            problems.append("PREVIOUS_INTENT_UNRESOLVED")
        if problems:
            return self._refuse(request.intent_id, problems)
        try:
            balance = (await self.rpc.accounts([self.wallet]))[0]
            available = (0 if balance is None else int(balance["lamports"])) - sol_to_lamports(
                self.limits.live_sol_reserve) - SIGNATURE_FEE
            if request.lamports > available:
                return self._refuse(request.intent_id, ["AMOUNT_ABOVE_BALANCE_MINUS_RESERVE"])
            blockhash, last_valid = await self.rpc.latest_blockhash()
            instruction = transfer(TransferParams(from_pubkey=self._keypair.pubkey(),
                                                  to_pubkey=Pubkey.from_string(request.destination),
                                                  lamports=request.lamports))
            message = MessageV0.try_compile(self._keypair.pubkey(), [instruction], [], Hash.from_string(blockhash))
            unsigned = VersionedTransaction.populate(message, [Signature.default()])
            simulation = await self.rpc.simulate(base64.b64encode(bytes(unsigned)).decode(), [self.wallet])
            if simulation.get("err") is not None:
                return self._refuse(request.intent_id, [f"SIMULATION_FAILED:{simulation.get('err')}"])
            post = (simulation.get("accounts") or [None])[0]
            spent = None if post is None or balance is None else int(balance["lamports"]) - int(post["lamports"])
            if spent is None or spent > request.lamports + SIGNATURE_FEE:
                return self._refuse(request.intent_id, ["SIMULATED_SPEND_EXCEEDS_REQUEST"])
        except Exception as exc:
            return self._refuse(request.intent_id, [f"PRE_SIGNING_FAILURE:{type(exc).__name__}"])
        signed = VersionedTransaction(message, [self._keypair])
        signature = str(signed.signatures[0])
        self.journal.update(request.intent_id, "SIGNED", self.clock(), {"lamports": request.lamports},
                            signature=signature, last_valid_block_height=last_valid)
        try:
            await self.rpc.send(base64.b64encode(bytes(signed)).decode())
        except Exception as exc:  # may still land; poll
            self.journal.update(request.intent_id, "SUBMITTED", self.clock(), {"send_error": type(exc).__name__})
        else:
            self.journal.update(request.intent_id, "SUBMITTED", self.clock(), {})
        return await self.settle(request.intent_id, signature, last_valid)


def lamports_text(lamports: int) -> str:
    return f"{Decimal(lamports) / Decimal(10 ** 9):.9f}"
