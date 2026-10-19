"""Signer settlement for the LIVE executor: exact on-chain balance changes, rent reclaim and journal lookup.

Fakes only: throwaway keys, no network, nothing leaves the process.
"""

from __future__ import annotations

import asyncio
import base64
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from solders.keypair import Keypair
from solders.transaction import VersionedTransaction

from desk.signer import programs as p
from desk.signer.inspect import associated_token_address
from tests.unit.test_signer import (
    AMOUNT,
    MIN_OUT,
    MINT,
    NOW,
    FakeRpc,
    FakeSwap,
    intent,
    jupiter_buy,
    make_signer,
    order_for,
    token_account,
)

RENT = 2_039_280


class LandingRpc(FakeRpc):
    """Accounts change only once the signature is reported confirmed (optionally a few reads later)."""

    def __init__(self, wallet: Any, *, lag_reads: int = 0, created: bool = True, **kwargs: Any) -> None:
        super().__init__(wallet, **kwargs)
        self.landed, self.lag_reads, self.created = False, lag_reads, created

    async def signature_status(self, signature: str) -> dict[str, Any] | None:
        status = await super().signature_status(signature)
        if status and status.get("confirmationStatus") == "confirmed":
            self.landed = True
        return status

    async def accounts(self, addresses: list[str]) -> list[dict[str, Any] | None]:
        self.account_requests.append(addresses)
        settled = self.landed and self.lag_reads <= 0
        if self.landed:
            self.lag_reads -= 1
        out: list[dict[str, Any] | None] = []
        for i, address in enumerate(addresses):
            if address == self.wallet:
                out.append({"lamports": self.balance - (self.spend if settled else 0)})
            elif i == len(addresses) - 1:
                out.append(token_account(self.received) if settled else (None if self.created else token_account(0)))
            else:
                out.append(token_account(0))
        return out


def _buy(tmp_path: Path, rpc_kwargs: dict[str, Any] | None = None) -> tuple[dict[str, Any], Any]:
    wallet = Keypair()
    swap = FakeSwap(order_for(wallet.pubkey(), MINT, jupiter_buy(wallet.pubkey(), MINT)))
    rpc = LandingRpc(wallet.pubkey(), **(rpc_kwargs or {}))
    signer = make_signer(tmp_path, swap, rpc, wallet)
    return asyncio.run(signer.handle({"op": "intent", "intent": intent()})), signer


def test_confirmed_swap_reports_what_changed_on_chain(tmp_path: Path) -> None:
    result, signer = _buy(tmp_path)
    assert result["state"] == "CONFIRMED"
    balances = result["balances"]
    assert balances["sol_lamports"] == -(AMOUNT + 20_000) and balances["output_token_raw"] == MIN_OUT + 5_000
    assert balances["output_account_created"] is True and balances["output_account_lamports"] == RENT
    assert signer.journal.get("intent-0001")["detail"]["balances"] == balances


def test_a_lagging_node_is_reread_and_an_unreadable_one_is_reported_unknown(tmp_path: Path) -> None:
    result, _ = _buy(tmp_path / "lag", {"lag_reads": 2})
    assert result["balances"]["output_token_raw"] == MIN_OUT + 5_000
    result, _ = _buy(tmp_path / "never", {"lag_reads": 99})
    assert result["state"] == "CONFIRMED" and result["balances"] is None


def test_existing_token_account_is_not_counted_as_new_rent(tmp_path: Path) -> None:
    result, _ = _buy(tmp_path, {"created": False})
    assert result["balances"]["output_account_created"] is False and result["balances"]["output_account_lamports"] == 0


class CloseRpc(FakeRpc):
    def __init__(self, wallet: Any, *, amount: int = 0, owner: str = p.TOKEN, exists: bool = True,
                 refund: int = RENT - 5_000, statuses: list[dict[str, Any] | None] | None = None) -> None:
        super().__init__(wallet, statuses=statuses)
        self.amount, self.owner, self.exists, self.refund = amount, owner, exists, refund
        self.closed = False

    async def accounts(self, addresses: list[str]) -> list[dict[str, Any] | None]:
        self.account_requests.append(addresses)
        out: list[dict[str, Any] | None] = []
        for address in addresses:
            if address == self.wallet:
                out.append({"lamports": self.balance + (self.refund if self.closed else 0)})
            elif self.exists and not self.closed and address == associated_token_address(self.wallet, MINT, self.owner):
                out.append({**token_account(self.amount), "owner": self.owner})
            else:
                out.append(None)
        return out

    async def simulate(self, transaction_b64: str, addresses: list[str]) -> dict[str, Any]:
        self.simulations.append(transaction_b64)
        return {"err": None, "accounts": [{"lamports": self.balance + self.refund}]}

    async def signature_status(self, signature: str) -> dict[str, Any] | None:
        status = await super().signature_status(signature)
        if status and status.get("confirmationStatus") == "confirmed":
            self.closed = True
        return status


def _close(tmp_path: Path, rpc: CloseRpc, wallet: Keypair, intent_id: str = "close-0001") -> tuple[dict[str, Any], Any]:
    signer = make_signer(tmp_path, FakeSwap({}), rpc, wallet)
    request = {"op": "close", "close": {"intent_id": intent_id, "mint": MINT,
                                        "expires_at": (NOW + timedelta(seconds=20)).isoformat()}}
    return asyncio.run(signer.handle(request)), signer


def test_close_reclaims_rent_from_the_wallets_own_empty_account_only(tmp_path: Path) -> None:
    wallet = Keypair()
    rpc = CloseRpc(wallet.pubkey())
    result, _ = _close(tmp_path, rpc, wallet)
    assert result["state"] == "CONFIRMED" and result["balances"] == {"sol_lamports": RENT - 5_000}
    assert len(rpc.sent) == 1
    tx = VersionedTransaction.from_bytes(base64.b64decode(rpc.sent[0]))
    keys = [str(k) for k in tx.message.account_keys]
    (ix,) = tx.message.instructions
    ata = associated_token_address(str(wallet.pubkey()), MINT, p.TOKEN)
    assert keys[ix.program_id_index] == p.TOKEN and bytes(ix.data) == bytes([p.TOKEN_CLOSE_ACCOUNT])
    assert [keys[i] for i in ix.accounts] == [ata, str(wallet.pubkey()), str(wallet.pubkey())]
    assert keys[0] == str(wallet.pubkey()) and tx.message.header.num_required_signatures == 1


def test_token_2022_accounts_close_through_their_own_program(tmp_path: Path) -> None:
    wallet = Keypair()
    rpc = CloseRpc(wallet.pubkey(), owner=p.TOKEN_2022)
    result, _ = _close(tmp_path, rpc, wallet)
    tx = VersionedTransaction.from_bytes(base64.b64decode(rpc.sent[0]))
    keys = [str(k) for k in tx.message.account_keys]
    assert result["state"] == "CONFIRMED" and keys[tx.message.instructions[0].program_id_index] == p.TOKEN_2022


def test_close_refuses_anything_but_an_empty_own_account(tmp_path: Path) -> None:
    for name, rpc_kwargs, problem in (("funded", {"amount": 5}, "TOKEN_BALANCE_NOT_ZERO"),
                                      ("missing", {"exists": False}, "NO_TOKEN_ACCOUNT"),
                                      ("odd-refund", {"refund": RENT + 10_000_000}, "SIMULATED_REFUND_UNEXPECTED")):
        wallet = Keypair()
        rpc = CloseRpc(wallet.pubkey(), **rpc_kwargs)
        result, _ = _close(tmp_path / name, rpc, wallet)
        assert result["error"] == "REFUSED" and problem in result["problems"], name
        assert rpc.sent == [], name


def test_close_is_idempotent(tmp_path: Path) -> None:
    wallet = Keypair()
    rpc = CloseRpc(wallet.pubkey())
    first, signer = _close(tmp_path, rpc, wallet)
    assert first["state"] == "CONFIRMED"
    request = {"op": "close", "close": {"intent_id": "close-0001", "mint": MINT,
                                        "expires_at": (NOW + timedelta(seconds=20)).isoformat()}}
    assert asyncio.run(signer.handle(request))["error"] == "DUPLICATE_INTENT"
    assert len(rpc.sent) == 1


def test_lookup_reads_the_journal_without_signing(tmp_path: Path) -> None:
    result, signer = _buy(tmp_path)
    found = asyncio.run(signer.handle({"op": "lookup", "intent_id": "intent-0001"}))
    assert found["found"] and found["state"] == "CONFIRMED" and found["signature"] == result["signature"]
    assert found["detail"]["balances"]["output_token_raw"] == MIN_OUT + 5_000
    assert asyncio.run(signer.handle({"op": "lookup", "intent_id": "never-seen"})) == {"ok": True, "found": False}
    assert asyncio.run(signer.handle({"op": "lookup", "intent_id": "x", "extra": 1}))["error"] == "UNKNOWN_FIELDS"


class FailingRpc(LandingRpc):
    """The transaction lands but fails on-chain: only its fee leaves the wallet; no token moves."""

    async def accounts(self, addresses: list[str]) -> list[dict[str, Any] | None]:
        self.account_requests.append(addresses)
        return [{"lamports": self.balance - (5_000 if self.landed else 0)} if address == self.wallet
                else (None if i == len(addresses) - 1 else token_account(0)) for i, address in enumerate(addresses)]


def test_a_swap_that_fails_on_chain_reports_the_fee_it_paid(tmp_path: Path) -> None:
    wallet = Keypair()
    swap = FakeSwap(order_for(wallet.pubkey(), MINT, jupiter_buy(wallet.pubkey(), MINT)))
    rpc = FailingRpc(wallet.pubkey(), statuses=[{"confirmationStatus": "confirmed",
                                                 "err": {"InstructionError": [2, {"Custom": 6001}]}}])
    signer = make_signer(tmp_path, swap, rpc, wallet)
    result = asyncio.run(signer.handle({"op": "intent", "intent": intent()}))
    assert result["state"] == "FAILED" and result["balances"]["sol_lamports"] == -5_000
    assert result["balances"]["output_token_raw"] == 0
    assert signer.journal.get("intent-0001")["detail"]["balances"] == result["balances"]


def test_recovery_measures_what_landed_against_the_balances_journaled_at_signing(tmp_path: Path) -> None:
    """A signer that dies after submitting still lets the desk book the exact amounts once it is re-armed."""
    wallet = Keypair()
    swap = FakeSwap(order_for(wallet.pubkey(), MINT, jupiter_buy(wallet.pubkey(), MINT)))
    rpc = LandingRpc(wallet.pubkey())
    crashed = make_signer(tmp_path, swap, rpc, wallet)

    async def die(*_: Any) -> dict[str, Any]:
        raise RuntimeError("signer process killed")

    crashed.settle = die  # type: ignore[method-assign]
    with pytest.raises(RuntimeError):
        asyncio.run(crashed.handle({"op": "intent", "intent": intent()}))
    assert crashed.journal.get("intent-0001")["state"] == "SUBMITTED"
    signed = crashed.journal.signed_detail("intent-0001")
    assert signed["tracked"][0] == str(wallet.pubkey()) and signed["pre"][0]["lamports"] == rpc.balance
    crashed.journal.close()
    restarted = make_signer(tmp_path, swap, rpc, wallet)
    (result,) = asyncio.run(restarted.recover())
    assert result["state"] == "CONFIRMED" and result["balances"]["sol_lamports"] == -(AMOUNT + 20_000)
    assert result["balances"]["output_token_raw"] == MIN_OUT + 5_000
    found = asyncio.run(restarted.handle({"op": "lookup", "intent_id": "intent-0001"}))
    assert found["state"] == "CONFIRMED" and found["detail"]["balances"] == result["balances"]
    assert found["detail"]["recovered"] is True and len(swap.executions) == 1      # never signed or sent again
