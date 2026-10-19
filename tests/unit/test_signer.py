"""M8: key isolation, keystore, intent-only signer, transaction inspection, simulation, no blind resubmit,
idempotency/crash recovery, gate matrix, zero-cost validation, emergency liquidation, restricted withdrawal."""

from __future__ import annotations

import ast
import asyncio
import base64
import logging
import os
import stat
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from solders.hash import Hash
from solders.instruction import AccountMeta, Instruction
from solders.keypair import Keypair
from solders.message import MessageV0, to_bytes_versioned
from solders.pubkey import Pubkey
from solders.signature import Signature
from solders.system_program import TransferParams, transfer
from solders.transaction import VersionedTransaction

from desk.contracts.quotes import QuoteObservation
from desk.core.config import LiveSection, load_config
from desk.core.paths import REPO_ROOT
from desk.live.gates import LiveContext, evaluate_gates, live_allowed
from desk.live.liquidate import liquidate_all
from desk.live.validate import validate_path
from desk.paper.ledger import PaperLedger
from desk.signer import keystore
from desk.signer import programs as p
from desk.signer.inspect import SwapExpectation, associated_token_address, inspect_swap
from desk.signer.intents import Intent
from desk.signer.journal import Journal
from desk.signer.server import request, serve
from desk.signer.service import BuyAuthorizer, Signer
from desk.storage.db import DbKind, connect, migrate

NOW = datetime(2026, 11, 3, 12, tzinfo=UTC)
LOADED = load_config(REPO_ROOT / "config")
LIVE = LiveSection(max_capital_sol=Decimal(1), max_priority_fee_lamports=1_000_000, intent_max_age_seconds=30,
                   withdraw_address="")
HEIGHT = 1_000
AMOUNT = 10 ** 8                       # 0.1 SOL  (cap = 1 SOL x 15% = 0.15 SOL)
MIN_OUT = 900_000


def pk(text: str) -> Pubkey:
    return Pubkey.from_string(text)


def meta(key: Pubkey | str, signer: bool = False, writable: bool = True) -> AccountMeta:
    return AccountMeta(pk(key) if isinstance(key, str) else key, signer, writable)


def compute(limit: int = 200_000, price: int = 500) -> list[Instruction]:
    return [Instruction(pk(p.COMPUTE_BUDGET), bytes([2]) + limit.to_bytes(4, "little"), []),
            Instruction(pk(p.COMPUTE_BUDGET), bytes([3]) + price.to_bytes(8, "little"), [])]


def jupiter_buy(wallet: Pubkey, mint: str, *, amount: int = AMOUNT, out_owner: Pubkey | None = None,
                extra: list[Instruction] | None = None, payer: Pubkey | None = None, price: int = 500,
                routes: int = 1, close_to: Pubkey | None = None) -> str:
    wsol = associated_token_address(str(wallet), p.WSOL)
    out = associated_token_address(str(out_owner or wallet), mint)
    ixs = [*compute(price=price),
           transfer(TransferParams(from_pubkey=wallet, to_pubkey=pk(wsol), lamports=amount)),
           Instruction(pk(p.TOKEN), bytes([p.TOKEN_SYNC_NATIVE]), [meta(wsol)])]
    ixs += [Instruction(pk(p.JUPITER_V6), b"\xbb\x64\xfa\xcc" + bytes(8), [meta(wallet, True), meta(wsol), meta(out),
                                                                          meta(p.TOKEN, writable=False)])] * routes
    ixs += [Instruction(pk(p.TOKEN), bytes([p.TOKEN_CLOSE_ACCOUNT]),
                        [meta(wsol), meta(close_to or wallet), meta(wallet, True)])]
    ixs += extra or []
    message = MessageV0.try_compile(payer or wallet, ixs, [], Hash.default())
    return base64.b64encode(bytes(VersionedTransaction.populate(message, [Signature.default()] *
                                                                message.header.num_required_signatures))).decode()


def order_for(wallet: Pubkey, mint: str, transaction: str, *, amount: int = AMOUNT, valid: int = HEIGHT + 150) -> dict[str, Any]:
    return {"transaction": transaction, "taker": str(wallet), "inputMint": p.WSOL, "outputMint": mint,
            "inAmount": str(amount), "outAmount": str(MIN_OUT + 10_000), "otherAmountThreshold": str(MIN_OUT),
            "lastValidBlockHeight": str(valid), "requestId": "req-1"}


def token_account(amount: int) -> dict[str, Any]:
    data = bytes(64) + amount.to_bytes(8, "little") + bytes(93)
    return {"lamports": 2_039_280, "data": [base64.b64encode(data).decode(), "base64"], "owner": p.TOKEN}


class FakeSwap:
    def __init__(self, order: dict[str, Any], execute: str = "ok") -> None:
        self.next_order, self.mode = order, execute
        self.orders: list[tuple[Any, ...]] = []
        self.executions: list[str] = []

    async def order(self, input_mint: str, output_mint: str, amount: int, taker: str, slippage_bps: int) -> dict[str, Any]:
        self.orders.append((input_mint, output_mint, amount, taker))
        return self.next_order

    async def execute(self, signed: str, request_id: str) -> dict[str, Any]:
        self.executions.append(signed)
        if self.mode == "timeout":
            raise TimeoutError("execute timed out")
        return {"status": "Success"}


class FakeRpc:
    def __init__(self, wallet: Pubkey, *, spend: int = AMOUNT + 20_000, received: int = MIN_OUT + 5_000,
                 statuses: list[dict[str, Any] | None] | None = None, heights: list[int] | None = None,
                 simulate_error: Exception | None = None) -> None:
        self.wallet = str(wallet)
        self.spend, self.received = spend, received
        self.statuses = statuses if statuses is not None else [{"confirmationStatus": "confirmed", "err": None}]
        self.heights = heights or [HEIGHT]
        self.simulate_error = simulate_error
        self.sent: list[str] = []
        self.simulations: list[str] = []
        self.account_requests: list[list[str]] = []
        self.balance = 5 * 10 ** 9

    async def block_height(self) -> int:
        return self.heights.pop(0) if len(self.heights) > 1 else self.heights[0]

    async def accounts(self, addresses: list[str]) -> list[dict[str, Any] | None]:
        self.account_requests.append(addresses)
        return [{"lamports": self.balance} if a == self.wallet else token_account(0) for a in addresses]

    async def simulate(self, transaction_b64: str, addresses: list[str]) -> dict[str, Any]:
        self.simulations.append(transaction_b64)
        if self.simulate_error:
            raise self.simulate_error
        out = []
        for i, a in enumerate(addresses):
            if a == self.wallet and i == 0:
                out.append({"lamports": self.balance - self.spend})
            else:
                out.append(token_account(self.received if i == len(addresses) - 1 else 0))
        return {"err": None, "accounts": out, "unitsConsumed": 50_000}

    async def signature_status(self, signature: str) -> dict[str, Any] | None:
        return self.statuses.pop(0) if self.statuses else None

    async def latest_blockhash(self) -> tuple[str, int]:
        return str(Hash.default()), HEIGHT + 150

    async def send(self, transaction_b64: str) -> str:
        self.sent.append(transaction_b64)
        return "sig"


def allow_test_buy(_intent: Intent, _now: datetime) -> list[str]:
    """Offline fixture only: exercise inspection/signing using throwaway keys and fake transports."""
    return []


def make_signer(tmp_path: Path, swap: FakeSwap, rpc: FakeRpc, keypair: Keypair, live: LiveSection = LIVE,
                journal: Journal | None = None, buy_authorizer: BuyAuthorizer | None = allow_test_buy) -> Signer:
    async def no_sleep(_: float) -> None:
        return None
    return Signer(keypair, journal or Journal(tmp_path / "journal.db"), swap, rpc, live, LOADED.settings.limits,
                  LOADED.settings.sizing, clock=lambda: NOW, sleep=no_sleep, buy_authorizer=buy_authorizer)


def intent(**changes: Any) -> dict[str, Any]:
    base: dict[str, Any] = {"intent_id": "intent-0001", "side": "BUY", "mint": MINT, "amount_raw": AMOUNT,
                            "max_slippage_bps": 50, "min_out_raw": MIN_OUT, "quote_ref": "q",
                            "expires_at": (NOW + timedelta(seconds=20)).isoformat()}
    base.update(changes)
    return base


MINT = str(Pubkey.new_unique())


# ── M8-01 / M8-02 keystore ────────────────────────────────────────────────────────────────────────────
def test_m8_02_keystore_encrypts_refuses_wrong_passphrase_and_never_logs(tmp_path: Path,
                                                                        caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    path = tmp_path / "hot-wallet.json"
    public = keystore.create(path, "correct horse battery staple")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    keypair = keystore.unlock(path, "correct horse battery staple")
    assert str(keypair.pubkey()) == public == keystore.public_key(path)
    secret = bytes(keypair)
    text = path.read_text()
    assert secret.hex() not in text and str(keypair) not in text and base64.b64encode(secret).decode() not in text
    with pytest.raises(keystore.KeystoreError, match="wrong passphrase"):
        keystore.unlock(path, "wrong passphrase!!")
    with pytest.raises(FileExistsError):
        keystore.create(path, "another long passphrase")
    with pytest.raises(keystore.KeystoreError, match="12 characters"):
        keystore.create(tmp_path / "short.json", "short")
    os.chmod(path, 0o644)
    with pytest.raises(keystore.KeystoreError, match="0600"):
        keystore.unlock(path, "correct horse battery staple")
    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert secret.hex() not in logged and str(keypair) not in logged and "correct horse" not in logged


def test_m8_01_only_the_signer_imports_the_keystore() -> None:
    root = REPO_ROOT / "src" / "desk"
    offenders = []
    for path in root.rglob("*.py"):
        if path.is_relative_to(root / "signer"):
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else (
                [f"{node.module}.{a.name}" for a in node.names] if isinstance(node, ast.ImportFrom) and node.module else [])
            offenders += [f"{path.relative_to(root)}: {n}" for n in names if n.startswith("desk.signer.keystore")
                          or n == "desk.signer.keystore"]
    assert offenders == []


# ── M8-03 intent-only ─────────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("payload,error", [
    ({"op": "sign_transaction", "transaction": "AAAA"}, "UNKNOWN_FIELDS"),
    ({"op": "sign"}, "UNKNOWN_OP"),
    ({"op": "intent", "intent": intent(), "raw": "x"}, "UNKNOWN_FIELDS"),
    ({"op": "intent", "intent": {**intent(), "transaction": "AAAA"}}, "INVALID_INTENT"),
    ({"op": "intent", "intent": intent(amount_raw="100000000")}, "INVALID_INTENT"),
    ({"op": "intent", "intent": intent(amount_raw=1.5)}, "INVALID_INTENT"),
    ({"op": "intent", "intent": intent(side="TRANSFER")}, "INVALID_INTENT"),
    ("not a dict", "UNKNOWN_FIELDS"),
])
def test_m8_03_signer_accepts_only_structured_intents(tmp_path: Path, payload: Any, error: str) -> None:
    wallet = Keypair()
    swap = FakeSwap(order_for(wallet.pubkey(), MINT, jupiter_buy(wallet.pubkey(), MINT)))
    result = asyncio.run(make_signer(tmp_path, swap, FakeRpc(wallet.pubkey()), wallet).handle(payload))
    assert result["ok"] is False and result["error"] == error
    assert swap.executions == []


@pytest.mark.parametrize("changes,problem", [
    ({"expires_at": (NOW - timedelta(seconds=1)).isoformat()}, "INTENT_EXPIRED"),
    ({"expires_at": (NOW + timedelta(minutes=10)).isoformat()}, "INTENT_EXPIRY_TOO_FAR"),
    ({"amount_raw": 150_000_001}, "AMOUNT_ABOVE_CAP"),
])
def test_m8_03_expired_and_oversized_intents_are_refused(tmp_path: Path, changes: dict[str, Any], problem: str) -> None:
    wallet = Keypair()
    swap = FakeSwap(order_for(wallet.pubkey(), MINT, jupiter_buy(wallet.pubkey(), MINT)))
    result = asyncio.run(make_signer(tmp_path, swap, FakeRpc(wallet.pubkey()), wallet).handle(
        {"op": "intent", "intent": intent(**changes)}))
    assert result["error"] == "REFUSED" and problem in result["problems"] and swap.orders == []


def test_no_capital_allocation_means_no_signature(tmp_path: Path) -> None:
    wallet = Keypair()
    swap = FakeSwap(order_for(wallet.pubkey(), MINT, jupiter_buy(wallet.pubkey(), MINT)))
    no_capital = LIVE.model_copy(update={"max_capital_sol": Decimal(0)})
    signer = make_signer(tmp_path, swap, FakeRpc(wallet.pubkey()), wallet, no_capital)
    result = asyncio.run(signer.handle({"op": "intent", "intent": intent()}))
    assert "NO_CAPITAL_ALLOCATION" in result["problems"] and swap.executions == []


# ── M8-04 inspection ──────────────────────────────────────────────────────────────────────────────────
def _expected(wallet: Pubkey) -> SwapExpectation:
    return SwapExpectation(wallet=str(wallet), side="BUY", input_mint=p.WSOL, output_mint=MINT, amount_raw=AMOUNT,
                           min_out_raw=MIN_OUT, max_priority_fee_lamports=1_000_000)


def test_m8_04_the_measured_jupiter_shape_passes_inspection() -> None:
    wallet = Keypair().pubkey()
    tx = jupiter_buy(wallet, MINT)
    assert inspect_swap(tx, order_for(wallet, MINT, tx), _expected(wallet), HEIGHT) == []


def test_m8_04_the_real_recorded_jupiter_order_passes_inspection() -> None:
    import json

    evidence = json.loads((REPO_ROOT / "research/evidence/2026-10-19/m8_jupiter_order_taker_simulation.json").read_text())
    order = evidence["order"]
    expected = SwapExpectation(wallet=order["taker"], side="BUY", input_mint=p.WSOL, output_mint=order["outputMint"],
                               amount_raw=int(order["inAmount"]), min_out_raw=int(order["otherAmountThreshold"]),
                               max_priority_fee_lamports=1_000_000)
    assert inspect_swap(order["transaction"], order, expected, int(order["lastValidBlockHeight"]) - 100) == []


def _crafted() -> list[tuple[str, Any]]:
    stranger = Pubkey.new_unique()
    return [
        ("PROGRAM_NOT_ALLOWED", lambda w: jupiter_buy(w, MINT, extra=[
            Instruction(Pubkey.new_unique(), b"\x01", [meta(w, True)])])),
        ("SOL_TRANSFER_TO_FOREIGN", lambda w: jupiter_buy(w, MINT, extra=[
            transfer(TransferParams(from_pubkey=w, to_pubkey=stranger, lamports=1_000))])),
        ("FEE_PAYER_NOT_WALLET", lambda w: jupiter_buy(w, MINT, payer=stranger)),
        ("OUTPUT_NOT_TO_OWN_ACCOUNT", lambda w: jupiter_buy(w, MINT, out_owner=stranger)),
        ("TOKEN_INSTRUCTION_NOT_ALLOWED:3", lambda w: jupiter_buy(w, MINT, extra=[Instruction(
            pk(p.TOKEN), bytes([3]) + bytes(8), [
            meta(associated_token_address(str(w), MINT)), meta(associated_token_address(str(stranger), MINT)), meta(w, True)])])),
        ("PRIORITY_FEE_TOO_HIGH", lambda w: jupiter_buy(w, MINT, price=10 ** 7)),
        ("EXPECTED_ONE_JUPITER_ROUTE", lambda w: jupiter_buy(w, MINT, routes=2)),
        ("CLOSE_ACCOUNT_TO_FOREIGN", lambda w: jupiter_buy(w, MINT, close_to=stranger)),
        ("SOL_WRAPPED_EXCEEDS_INTENT", lambda w: jupiter_buy(w, MINT, amount=AMOUNT * 2)),
    ]


@pytest.mark.parametrize("problem,build", _crafted(), ids=[c[0] for c in _crafted()])
def test_m8_04_crafted_transactions_are_refused(problem: str, build: Any, tmp_path: Path) -> None:
    wallet = Keypair()
    tx = build(wallet.pubkey())
    problems = inspect_swap(tx, order_for(wallet.pubkey(), MINT, tx), _expected(wallet.pubkey()), HEIGHT)
    assert problem in problems or any(x.startswith(problem) for x in problems), problems
    swap = FakeSwap(order_for(wallet.pubkey(), MINT, tx))
    result = asyncio.run(make_signer(tmp_path, swap, FakeRpc(wallet.pubkey()), wallet).handle(
        {"op": "intent", "intent": intent()}))
    assert result["error"] == "REFUSED" and swap.executions == []          # nothing signed or sent


def test_m8_04_order_must_describe_the_intent_and_blockhash_must_be_live() -> None:
    wallet = Keypair().pubkey()
    tx = jupiter_buy(wallet, MINT)
    assert "ORDER_AMOUNT_DIFFERS" in inspect_swap(tx, {**order_for(wallet, MINT, tx), "inAmount": "1"}, _expected(wallet), HEIGHT)
    assert "MIN_OUT_BELOW_INTENT" in inspect_swap(tx, {**order_for(wallet, MINT, tx), "otherAmountThreshold": "1"},
                                                  _expected(wallet), HEIGHT)
    assert "BLOCKHASH_EXPIRED" in inspect_swap(tx, order_for(wallet, MINT, tx, valid=HEIGHT), _expected(wallet), HEIGHT)
    assert inspect_swap("bm90IGEgdHg=", order_for(wallet, MINT, tx), _expected(wallet), HEIGHT) == ["TRANSACTION_UNDECODABLE"]


# ── M8-05 simulation ──────────────────────────────────────────────────────────────────────────────────
def test_m8_05_valid_intent_is_simulated_then_signed_once_and_confirmed(tmp_path: Path) -> None:
    wallet = Keypair()
    tx = jupiter_buy(wallet.pubkey(), MINT)
    swap, rpc = FakeSwap(order_for(wallet.pubkey(), MINT, tx)), FakeRpc(wallet.pubkey())
    signer = make_signer(tmp_path, swap, rpc, wallet)
    result = asyncio.run(signer.handle({"op": "intent", "intent": intent()}))
    assert result["ok"] and result["state"] == "CONFIRMED" and len(swap.executions) == 1
    signed = VersionedTransaction.from_bytes(base64.b64decode(swap.executions[0]))
    assert signed.signatures[0].verify(wallet.pubkey(), to_bytes_versioned(signed.message))
    assert signer.journal.get("intent-0001")["signature"] == result["signature"]


@pytest.mark.parametrize("rpc_changes,problem", [
    ({"simulate_error": RuntimeError("rpc down")}, "PRE_SIGNING_FAILURE:RuntimeError"),
    ({"spend": AMOUNT * 3}, "SIMULATED_SOL_SPEND_EXCEEDS_INTENT"),
    ({"received": MIN_OUT - 1}, "SIMULATED_OUTPUT_BELOW_MIN_OUT"),
])
def test_m8_05_no_simulation_or_bad_deltas_means_no_signature(tmp_path: Path, rpc_changes: dict[str, Any], problem: str) -> None:
    wallet = Keypair()
    swap = FakeSwap(order_for(wallet.pubkey(), MINT, jupiter_buy(wallet.pubkey(), MINT)))
    signer = make_signer(tmp_path, swap, FakeRpc(wallet.pubkey(), **rpc_changes), wallet)
    result = asyncio.run(signer.handle({"op": "intent", "intent": intent()}))
    assert problem in result["problems"] and swap.executions == []
    assert signer.journal.get("intent-0001")["signature"] is None


# ── M8-07 no blind resubmit ───────────────────────────────────────────────────────────────────────────
def test_m8_07_execute_timeout_polls_until_expiry_and_never_resubmits(tmp_path: Path) -> None:
    wallet = Keypair()
    swap = FakeSwap(order_for(wallet.pubkey(), MINT, jupiter_buy(wallet.pubkey(), MINT)), execute="timeout")
    rpc = FakeRpc(wallet.pubkey(), statuses=[None] * 20, heights=[HEIGHT, HEIGHT, HEIGHT + 50, HEIGHT + 120, HEIGHT + 151])
    result = asyncio.run(make_signer(tmp_path, swap, rpc, wallet).handle({"op": "intent", "intent": intent()}))
    assert result["state"] == "EXPIRED" and len(swap.executions) == 1 and len(swap.orders) == 1


def test_m8_07_a_timed_out_submission_that_lands_is_confirmed_without_resubmitting(tmp_path: Path) -> None:
    wallet = Keypair()
    swap = FakeSwap(order_for(wallet.pubkey(), MINT, jupiter_buy(wallet.pubkey(), MINT)), execute="timeout")
    rpc = FakeRpc(wallet.pubkey(), statuses=[None, None, {"confirmationStatus": "confirmed", "err": None}])
    result = asyncio.run(make_signer(tmp_path, swap, rpc, wallet).handle({"op": "intent", "intent": intent()}))
    assert result["state"] == "CONFIRMED" and len(swap.executions) == 1


# ── M8-08 idempotency and crash recovery ──────────────────────────────────────────────────────────────
def test_m8_08_the_same_intent_is_never_signed_twice(tmp_path: Path) -> None:
    wallet = Keypair()
    swap = FakeSwap(order_for(wallet.pubkey(), MINT, jupiter_buy(wallet.pubkey(), MINT)))
    signer = make_signer(tmp_path, swap, FakeRpc(wallet.pubkey()), wallet)
    assert asyncio.run(signer.handle({"op": "intent", "intent": intent()}))["ok"]
    again = asyncio.run(signer.handle({"op": "intent", "intent": intent()}))
    assert again["error"] == "DUPLICATE_INTENT" and again["state"] == "CONFIRMED"
    assert len(swap.executions) == 1 and len(swap.orders) == 1


def test_m8_08_restart_resolves_signed_intents_from_the_journal_without_signing(tmp_path: Path) -> None:
    wallet = Keypair()
    journal = Journal(tmp_path / "journal.db")
    journal.claim("intent-crashed", "SWAP", {}, NOW)
    journal.update("intent-crashed", "SIGNED", NOW, {}, signature="5" * 64, last_valid_block_height=HEIGHT + 10)
    journal.claim("intent-early", "SWAP", {}, NOW)                         # crashed before signing
    swap = FakeSwap(order_for(wallet.pubkey(), MINT, jupiter_buy(wallet.pubkey(), MINT)))
    rpc = FakeRpc(wallet.pubkey(), statuses=[None, None], heights=[HEIGHT, HEIGHT + 11])
    blocked = make_signer(tmp_path, swap, rpc, wallet, journal=journal)
    pending = asyncio.run(blocked.handle({"op": "intent", "intent": intent(intent_id="intent-new-1")}))
    assert "PREVIOUS_INTENT_UNRESOLVED" in pending["problems"]              # nothing new while one is open
    results = {r["intent_id"]: r["state"] for r in asyncio.run(blocked.recover())}
    assert results == {"intent-crashed": "EXPIRED", "intent-early": "REFUSED"}
    assert swap.orders == [] and swap.executions == []
    assert journal.get("intent-early")["detail"]["problems"] == ["CRASHED_BEFORE_SIGNING"]


# ── M8-06 gate matrix ─────────────────────────────────────────────────────────────────────────────────
ALL_OK = LiveContext(config_mode="LIVE", env_confirm="yes", strategy_version="H3-v2", approved=True,
                     readiness="ELIGIBLE FOR MANUAL LIVE REVIEW", readiness_override=False,
                     max_capital_sol=Decimal("1"), capital_ceiling_sol=Decimal("2"), signer_unlocked=True,
                     fee_balance_ok=True, quote_fresh=True, token_program_ok=True, guardian_fresh=True, halted=None,
                     regime_status_ok=True, regime_eligible=True)
SINGLE_MISSING = [
    (1, {"config_mode": "PAPER"}), (1, {"env_confirm": None}), (2, {"approved": False}),
    (3, {"readiness": "CONTINUE PAPER TESTING"}), (4, {"max_capital_sol": Decimal(0)}),
    (4, {"max_capital_sol": Decimal("2.5")}), (5, {"signer_unlocked": False}), (6, {"fee_balance_ok": False}),
    (6, {"quote_fresh": False}), (6, {"token_program_ok": False}), (6, {"guardian_fresh": False}),
    (6, {"halted": "STOP"}), (7, {"regime_status_ok": False}), (7, {"regime_eligible": False}),
]


def test_m8_06_all_gates_together_allow_live_review_path() -> None:
    assert live_allowed(evaluate_gates(ALL_OK))
    override = replace(ALL_OK, readiness="CONTINUE PAPER TESTING", readiness_override=True)
    assert live_allowed(evaluate_gates(override))


@pytest.mark.parametrize("gate,changes", SINGLE_MISSING, ids=[f"gate{g}-{next(iter(c))}" for g, c in SINGLE_MISSING])
def test_m8_06_each_single_missing_gate_refuses_live(gate: int, changes: dict[str, Any]) -> None:
    gates = evaluate_gates(replace(ALL_OK, **changes))
    assert not live_allowed(gates)
    assert [g.number for g in gates if not g.ok] == [gate]


def test_production_default_refuses_buy_before_any_network_or_signature(tmp_path: Path) -> None:
    wallet = Keypair()
    swap, rpc = FakeSwap({}), FakeRpc(wallet.pubkey())
    # Deliberately omit the authorizer, exactly as the production CLI constructor does.
    signer = Signer(wallet, Journal(tmp_path / "journal.db"), swap, rpc, LIVE,
                    LOADED.settings.limits, LOADED.settings.sizing, clock=lambda: NOW)
    status = asyncio.run(signer.handle({"op": "status"}))
    assert not status["buy_enabled"] and status["reason"] == "LIVE_ENTRY_AUTHORIZATION_UNAVAILABLE"
    result = asyncio.run(signer.handle({"op": "intent", "intent": intent()}))
    assert result["problems"] == ["LIVE_ENTRY_AUTHORIZATION_UNAVAILABLE"]
    assert LIVE.max_capital_sol > 0
    assert not swap.orders and not swap.executions and not rpc.account_requests and not rpc.simulations and not rpc.sent
    row = signer.journal.get("intent-0001")
    assert row["state"] == "REFUSED" and row["signature"] is None


@pytest.mark.parametrize("gate,changes", SINGLE_MISSING, ids=[f"gate{g}-{next(iter(c))}" for g, c in SINGLE_MISSING])
def test_each_gate_refusal_reaches_signer_before_order(tmp_path: Path, gate: int, changes: dict[str, Any]) -> None:
    wallet = Keypair()
    swap, rpc = FakeSwap({}), FakeRpc(wallet.pubkey())

    def authorize(pending: Intent, at: datetime) -> list[str]:
        assert pending.intent_id == "intent-0001" and at == NOW
        return [f"LIVE_GATE_{g.number}_CLOSED" for g in evaluate_gates(replace(ALL_OK, **changes)) if not g.ok]

    signer = make_signer(tmp_path, swap, rpc, wallet, buy_authorizer=authorize)
    result = asyncio.run(signer.handle({"op": "intent", "intent": intent()}))
    assert result["problems"] == [f"LIVE_GATE_{gate}_CLOSED"]
    assert not swap.orders and not swap.executions and not rpc.simulations and not rpc.account_requests
    assert signer.journal.get("intent-0001")["signature"] is None


@pytest.mark.parametrize("change_during", ["balance", "simulation"])
def test_entry_policy_is_rechecked_after_await_before_order_and_sign(tmp_path: Path, change_during: str) -> None:
    wallet = Keypair()
    permitted = True
    checks = []

    def authorize(pending: Intent, at: datetime) -> list[str]:
        checks.append((pending.intent_id, at, permitted))
        return [] if permitted else ["LIVE_GATE_6_CLOSED:STOP"]

    class ChangingRpc(FakeRpc):
        async def accounts(self, addresses: list[str]) -> list[dict[str, Any] | None]:
            nonlocal permitted
            result = await super().accounts(addresses)
            if change_during == "balance":
                await asyncio.sleep(0)
                permitted = False
            return result

        async def simulate(self, transaction_b64: str, addresses: list[str]) -> dict[str, Any]:
            nonlocal permitted
            result = await super().simulate(transaction_b64, addresses)
            await asyncio.sleep(0)
            permitted = False
            return result

    swap = FakeSwap(order_for(wallet.pubkey(), MINT, jupiter_buy(wallet.pubkey(), MINT)))
    rpc = ChangingRpc(wallet.pubkey())
    signer = make_signer(tmp_path, swap, rpc, wallet, buy_authorizer=authorize)
    result = asyncio.run(signer.handle({"op": "intent", "intent": intent()}))
    assert result["problems"] == ["LIVE_GATE_6_CLOSED:STOP"]
    assert checks[0][2] and not checks[-1][2]
    assert len(swap.orders) == (0 if change_during == "balance" else 1)
    assert len(rpc.simulations) == (0 if change_during == "balance" else 1)
    assert not swap.executions and not rpc.sent
    assert signer.journal.get("intent-0001")["signature"] is None


@pytest.mark.parametrize("change,problem", [("expire", "INTENT_EXPIRED"), ("disarm", "DISARMED")])
def test_intent_expiry_and_disarm_are_rechecked_after_simulation(tmp_path: Path, change: str, problem: str) -> None:
    wallet = Keypair()
    clock_now = NOW

    class ChangingRpc(FakeRpc):
        async def simulate(self, transaction_b64: str, addresses: list[str]) -> dict[str, Any]:
            nonlocal clock_now
            result = await super().simulate(transaction_b64, addresses)
            await asyncio.sleep(0)
            if change == "expire":
                clock_now = NOW + timedelta(seconds=21)
            else:
                signer.disarmed = True
            return result

    swap = FakeSwap(order_for(wallet.pubkey(), MINT, jupiter_buy(wallet.pubkey(), MINT)))
    rpc = ChangingRpc(wallet.pubkey())
    signer = make_signer(tmp_path, swap, rpc, wallet)
    signer.clock = lambda: clock_now
    result = asyncio.run(signer.handle({"op": "intent", "intent": intent()}))
    assert result["problems"] == [problem] and rpc.simulations and not swap.executions
    assert signer.journal.get("intent-0001")["signature"] is None


@pytest.mark.parametrize("bad_result", [None, True, "approved", [None], [""]])
def test_invalid_authorizer_result_cannot_enable_buy(tmp_path: Path, bad_result: Any) -> None:
    wallet = Keypair()
    swap, rpc = FakeSwap({}), FakeRpc(wallet.pubkey())

    def invalid_authorizer(_pending: Intent, _at: datetime) -> Any:
        return bad_result

    signer = make_signer(tmp_path, swap, rpc, wallet, buy_authorizer=invalid_authorizer)
    result = asyncio.run(signer.handle({"op": "intent", "intent": intent()}))
    assert result["problems"] == ["LIVE_ENTRY_AUTHORIZATION_INVALID"] and not swap.orders
    assert signer.journal.get("intent-0001")["signature"] is None


def test_authorizer_error_cannot_enable_buy(tmp_path: Path) -> None:
    wallet = Keypair()
    swap, rpc = FakeSwap({}), FakeRpc(wallet.pubkey())

    def broken_authorizer(_pending: Intent, _at: datetime) -> list[str]:
        raise RuntimeError("policy database unavailable")

    signer = make_signer(tmp_path, swap, rpc, wallet, buy_authorizer=broken_authorizer)
    result = asyncio.run(signer.handle({"op": "intent", "intent": intent()}))
    assert result["problems"] == ["LIVE_ENTRY_AUTHORIZATION_FAILED"] and not swap.orders
    assert signer.journal.get("intent-0001")["signature"] is None


@pytest.mark.parametrize("field", ["authorized", "buy_authorizer", "live_allowed", "gates"])
def test_intent_cannot_claim_its_own_live_authorization(tmp_path: Path, field: str) -> None:
    wallet = Keypair()
    swap, rpc = FakeSwap({}), FakeRpc(wallet.pubkey())
    signer = make_signer(tmp_path, swap, rpc, wallet, buy_authorizer=None)
    result = asyncio.run(signer.handle({"op": "intent", "intent": intent(**{field: True})}))
    assert result["error"] == "INVALID_INTENT" and not swap.orders and not swap.executions


def test_sell_recovery_does_not_require_buy_authorization_or_new_capital(tmp_path: Path) -> None:
    wallet = Keypair()
    route = Instruction(pk(p.JUPITER_V6), bytes(12), [meta(wallet.pubkey(), True),
                        meta(associated_token_address(str(wallet.pubkey()), MINT))])
    message = MessageV0.try_compile(wallet.pubkey(), [*compute(), route], [], Hash.default())
    transaction = base64.b64encode(bytes(VersionedTransaction.populate(message, [Signature.default()]))).decode()
    order = {**order_for(wallet.pubkey(), MINT, transaction), "inputMint": MINT, "outputMint": p.WSOL}

    class ExitRpc(FakeRpc):
        async def accounts(self, addresses: list[str]) -> list[dict[str, Any] | None]:
            result = await super().accounts(addresses)
            result[1] = token_account(AMOUNT)
            return result

        async def simulate(self, transaction_b64: str, addresses: list[str]) -> dict[str, Any]:
            self.simulations.append(transaction_b64)
            return {"err": None, "accounts": [{"lamports": self.balance + MIN_OUT}, token_account(0),
                                              {"lamports": self.balance + MIN_OUT}]}

    swap, rpc = FakeSwap(order), ExitRpc(wallet.pubkey())
    no_capital = LIVE.model_copy(update={"max_capital_sol": Decimal(0)})
    signer = make_signer(tmp_path, swap, rpc, wallet, no_capital, buy_authorizer=None)
    result = asyncio.run(signer.handle({"op": "intent", "intent": intent(side="SELL")}))
    assert result["ok"] and result["state"] == "CONFIRMED"
    assert len(swap.executions) == 1 and len(rpc.simulations) == 1


# ── M8-09 zero-cost validation (offline) ──────────────────────────────────────────────────────────────
def test_m8_09_zero_cost_validation_signs_and_sends_nothing() -> None:
    wallet = Keypair().pubkey()
    tx = jupiter_buy(wallet, MINT)
    swap, rpc = FakeSwap(order_for(wallet, MINT, tx)), FakeRpc(wallet)
    result = asyncio.run(validate_path(swap, rpc, taker=str(wallet), output_mint=MINT, amount_lamports=AMOUNT))
    assert result["ok"], result
    assert swap.executions == [] and rpc.sent == []


@pytest.mark.live
def test_m8_09_live_zero_cost_path_validation() -> None:
    import httpx

    from desk.signer.net import JupiterSwap, SolanaRpc

    async def run() -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=20) as client:
            return await validate_path(JupiterSwap(client, None), SolanaRpc(client, "https://api.mainnet-beta.solana.com"),
                                       taker="5tzFkiKscXHK5ZXCGbXZxdw7gTjjD1mBwuoFbhUvuAi9",
                                       output_mint="EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v", amount_lamports=10 ** 7)
    result = asyncio.run(run())
    assert result["inspection_problems"] == [] and result["simulation_error"] is None


# ── M8-10 emergency liquidation ───────────────────────────────────────────────────────────────────────
def test_m8_10_liquidation_attempts_every_exit_and_reports_truthfully(tmp_path: Path) -> None:
    conn = connect(tmp_path / "ledger-live.db", DbKind.LEDGER)
    migrate(conn, DbKind.LEDGER)
    ledger = PaperLedger(conn)
    ledger.init_book(NOW, Decimal(100), Decimal(100), Decimal(100))
    mints = [str(Pubkey.new_unique()) for _ in range(3)]
    for i, mint in enumerate(mints):
        ledger.open_position(position_id=f"pos{i}", intent_id=f"buy{i}", candidate_id="c", mint=mint, pool_address="p",
                             strategy_id="H3", strategy_version="H3-v2", decision_hash=f"d{i}", regime_label="RISK_ON",
                             at=NOW, ticket_lamports=10 ** 7, tokens_raw=10 ** 6, entry_fees_lamports=5000,
                             failed_fees_lamports=0, rent_lamports=0, entry={})
    stopped = []

    async def quote(input_mint: str, output_mint: str, amount: int) -> QuoteObservation:
        out = 0 if input_mint == mints[2] else 9_000_000
        return QuoteObservation(source="fake", input_mint=input_mint, output_mint=output_mint, in_amount_raw=amount,
                                out_amount_raw=out, swap_mode="ExactIn", requested_at=NOW, received_at=NOW,
                                latency_ms=Decimal(1))

    async def send(payload: dict[str, Any]) -> dict[str, Any]:
        if payload["intent"]["mint"] == mints[1]:
            return {"ok": False, "error": "REFUSED", "problems": ["SIMULATION_FAILED:slippage"]}
        return {"ok": True, "state": "CONFIRMED", "signature": "sig0"}

    report = asyncio.run(liquidate_all(ledger, quote=quote, send_intent=send, stop_entries=lambda: stopped.append(1),
                                       clock=lambda: NOW))
    assert stopped == [1]
    assert [r["result"] for r in report] == ["CONFIRMED", "FAILED", "FAILED"]
    assert report[1]["reason"] == "REFUSED" and report[2]["reason"] == "NO_SELL_ROUTE"
    states = {r["id"]: r["state"] for r in conn.execute("SELECT id, state FROM positions")}
    assert states == {"pos0": "CLOSED", "pos1": "EXIT_BLOCKED", "pos2": "EXIT_BLOCKED"}   # failures stay supervised
    assert ledger.reconcile()["difference_lamports"] == 0
    conn.close()


# ── restricted withdrawal ─────────────────────────────────────────────────────────────────────────────
def test_withdrawal_only_to_the_registered_address_with_exact_confirmation(tmp_path: Path) -> None:
    wallet, cold = Keypair(), str(Pubkey.new_unique())
    live = LIVE.model_copy(update={"withdraw_address": cold})
    rpc = FakeRpc(wallet.pubkey(), spend=10 ** 8 + 5000)
    signer = make_signer(tmp_path, FakeSwap({}), rpc, wallet, live, buy_authorizer=None)

    def ask(intent_id: str, destination: str, lamports: int, confirmation: str | None = None) -> dict[str, Any]:
        return asyncio.run(signer.handle({"op": "withdraw", "withdrawal": {
            "intent_id": intent_id, "destination": destination, "lamports": lamports,
            "confirmation": confirmation or f"WITHDRAW {lamports} LAMPORTS TO {destination}",
            "expires_at": (NOW + timedelta(seconds=20)).isoformat()}}))

    assert "DESTINATION_NOT_REGISTERED" in ask("wd-foreign-1", str(Pubkey.new_unique()), 10 ** 8)["problems"]
    assert "CONFIRMATION_MISMATCH" in ask("wd-typo-0001", cold, 10 ** 8, "yes")["problems"]
    assert "AMOUNT_ABOVE_BALANCE_MINUS_RESERVE" in ask("wd-toobig-01", cold, 5 * 10 ** 9)["problems"]
    result = ask("wd-good-0001", cold, 10 ** 8)
    assert result["ok"] and len(rpc.sent) == 1
    sent = VersionedTransaction.from_bytes(base64.b64decode(rpc.sent[0]))
    keys = [str(k) for k in sent.message.account_keys]
    assert len(sent.message.instructions) == 1 and keys[sent.message.instructions[0].program_id_index] == p.SYSTEM
    assert keys[0] == str(wallet.pubkey()) and cold in keys
    assert int.from_bytes(bytes(sent.message.instructions[0].data)[4:12], "little") == 10 ** 8
    unregistered = make_signer(tmp_path, FakeSwap({}), FakeRpc(wallet.pubkey()), wallet,
                               journal=Journal(tmp_path / "other.db"))
    refused = asyncio.run(unregistered.handle({"op": "withdraw", "withdrawal": {
        "intent_id": "wd-none-0001", "destination": cold, "lamports": 1, "confirmation": f"WITHDRAW 1 LAMPORTS TO {cold}",
        "expires_at": (NOW + timedelta(seconds=20)).isoformat()}}))
    assert "DESTINATION_NOT_REGISTERED" in refused["problems"]            # no address configured: impossible


# ── socket boundary ───────────────────────────────────────────────────────────────────────────────────
def test_socket_is_private_and_speaks_only_the_signer_protocol(tmp_path: Path) -> None:
    wallet = Keypair()
    signer = make_signer(tmp_path, FakeSwap({}), FakeRpc(wallet.pubkey()), wallet)
    socket_path = tmp_path / "run" / "signer.sock"

    async def scenario() -> list[dict[str, Any]]:
        stop = asyncio.Event()
        server = asyncio.create_task(serve(signer, socket_path, stop))
        while not socket_path.exists():
            await asyncio.sleep(0.01)
        mode = stat.S_IMODE(socket_path.stat().st_mode)
        answers = [{"mode": mode}, await request(socket_path, {"op": "status"}),
                   await request(socket_path, {"op": "sign_transaction"}), await request(socket_path, {"op": "disarm"})]
        await asyncio.wait_for(server, 5)
        return answers

    answers = asyncio.run(scenario())
    assert answers[0]["mode"] == 0o600
    assert answers[1]["ok"] and answers[1]["wallet"] == str(wallet.pubkey())
    assert answers[2]["error"] == "UNKNOWN_OP" and answers[3]["disarmed"] is True
    assert not socket_path.exists()
