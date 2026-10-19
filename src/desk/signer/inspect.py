"""Transaction inspection and simulation checks (PLAN §16.2, M8-04/05). A problem list; empty means acceptable.

Rules are deliberately narrow and match the measured Jupiter transaction shape: compute-budget instructions,
wrapping SOL into the wallet's own wSOL account (transfer + SyncNative), creating the wallet's own token
accounts, one Jupiter route, and closing the wallet's own wSOL account back to the wallet. Anything else —
another program at top level, a SOL or token transfer to a foreign account, a different fee payer, an expired
blockhash, output not to the wallet's own account — is refused. Simulation then has to confirm the balance
changes. Simulation cannot prove landing, MEV exposure or future state (§16.4).
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Any

from solders.pubkey import Pubkey
from solders.transaction import VersionedTransaction

from desk.signer import programs as p

MAX_BLOCKHASH_HORIZON = 300            # lastValidBlockHeight must be within this many blocks ahead
RENT_ALLOWANCE_LAMPORTS = 2 * 2_039_280 + 100_000


def associated_token_address(owner: str, mint: str, token_program: str = p.TOKEN) -> str:
    seeds = [bytes(Pubkey.from_string(owner)), bytes(Pubkey.from_string(token_program)), bytes(Pubkey.from_string(mint))]
    return str(Pubkey.find_program_address(seeds, Pubkey.from_string(p.ASSOCIATED_TOKEN))[0])


def own_accounts(wallet: str, mints: tuple[str, ...]) -> frozenset[str]:
    return frozenset({wallet} | {associated_token_address(wallet, mint, program)
                                 for mint in (*mints, p.WSOL) for program in p.TOKEN_PROGRAMS})


@dataclass(frozen=True)
class SwapExpectation:
    wallet: str
    side: str                       # BUY: SOL -> token ; SELL: token -> SOL
    input_mint: str
    output_mint: str
    amount_raw: int
    min_out_raw: int
    max_priority_fee_lamports: int
    allowed_fee_destinations: frozenset[str] = frozenset()


def _u32(data: bytes, at: int = 0) -> int:
    return int.from_bytes(data[at:at + 4], "little")


def _u64(data: bytes, at: int) -> int:
    return int.from_bytes(data[at:at + 8], "little")


def inspect_swap(transaction_b64: str, order: dict[str, Any], expected: SwapExpectation, block_height: int) -> list[str]:
    problems: list[str] = []
    try:
        tx = VersionedTransaction.from_bytes(base64.b64decode(transaction_b64))
    except Exception:  # any undecodable payload is refused, never interpreted
        return ["TRANSACTION_UNDECODABLE"]
    message = tx.message
    keys = [str(k) for k in message.account_keys]
    wallet = expected.wallet
    if message.header.num_required_signatures != 1:
        problems.append("MULTIPLE_SIGNERS")
    if not keys or keys[0] != wallet:
        problems.append("FEE_PAYER_NOT_WALLET")
    # the order must describe exactly the intent
    if order.get("taker") != wallet:
        problems.append("ORDER_TAKER_NOT_WALLET")
    if order.get("inputMint") != expected.input_mint or order.get("outputMint") != expected.output_mint:
        problems.append("ORDER_MINTS_DIFFER")
    if str(order.get("inAmount")) != str(expected.amount_raw):
        problems.append("ORDER_AMOUNT_DIFFERS")
    try:
        threshold = int(order.get("otherAmountThreshold") or 0)
    except (TypeError, ValueError):
        threshold = 0
    if threshold < expected.min_out_raw:
        problems.append("MIN_OUT_BELOW_INTENT")
    try:
        horizon = int(order["lastValidBlockHeight"]) - block_height
    except (KeyError, TypeError, ValueError):
        horizon = -1
    if horizon <= 0:
        problems.append("BLOCKHASH_EXPIRED")
    elif horizon > MAX_BLOCKHASH_HORIZON:
        problems.append("BLOCKHASH_IMPLAUSIBLE")

    mine = own_accounts(wallet, (expected.input_mint, expected.output_mint))
    wsol = {associated_token_address(wallet, p.WSOL, program) for program in p.TOKEN_PROGRAMS}
    synced: set[str] = set()
    wrapped = 0
    jupiter_routes = 0
    compute_limit, compute_price = 0, 0

    def account(ix: Any, position: int) -> str:
        index = list(ix.accounts)[position]
        return keys[index] if index < len(keys) else f"LOOKUP:{index}"

    for ix in message.instructions:
        if ix.program_id_index >= len(keys):
            problems.append("PROGRAM_FROM_LOOKUP_TABLE")
            continue
        program = keys[ix.program_id_index]
        data = bytes(ix.data)
        if program not in p.TOP_LEVEL_ALLOWED:
            problems.append(f"PROGRAM_NOT_ALLOWED:{program}")
        elif program == p.SYSTEM:
            if len(data) < 12 or _u32(data) != p.SYSTEM_TRANSFER or len(ix.accounts) < 2:
                problems.append("SYSTEM_INSTRUCTION_NOT_ALLOWED")
                continue
            source, destination, lamports = account(ix, 0), account(ix, 1), _u64(data, 4)
            if source != wallet:
                problems.append("SOL_TRANSFER_NOT_FROM_WALLET")
            elif destination in mine:
                wrapped += lamports
            elif destination in expected.allowed_fee_destinations and lamports * 100 <= expected.amount_raw:
                continue
            else:
                problems.append(f"SOL_TRANSFER_TO_FOREIGN:{destination}")
        elif program in p.TOKEN_PROGRAMS:
            tag = data[0] if data else -1
            if tag == p.TOKEN_SYNC_NATIVE and ix.accounts and account(ix, 0) in mine:
                synced.add(account(ix, 0))
            elif tag == p.TOKEN_CLOSE_ACCOUNT and len(ix.accounts) >= 3:
                if not (account(ix, 0) in mine and account(ix, 1) == wallet and account(ix, 2) == wallet):
                    problems.append("CLOSE_ACCOUNT_TO_FOREIGN")
            else:
                problems.append(f"TOKEN_INSTRUCTION_NOT_ALLOWED:{tag}")
        elif program == p.ASSOCIATED_TOKEN:
            if data not in (b"", bytes([p.ATA_CREATE]), bytes([p.ATA_CREATE_IDEMPOTENT])) or len(ix.accounts) < 6:
                problems.append("ATA_INSTRUCTION_NOT_ALLOWED")
                continue
            payer, ata, owner, mint, token_program = (account(ix, i) for i in (0, 1, 2, 3, 5))
            if payer != wallet or owner != wallet or mint.startswith("LOOKUP") or token_program not in p.TOKEN_PROGRAMS \
                    or ata != associated_token_address(wallet, mint, token_program):
                problems.append("ATA_NOT_WALLETS_OWN")
        elif program == p.COMPUTE_BUDGET:
            tag = data[0] if data else -1
            if tag == p.COMPUTE_SET_LIMIT and len(data) >= 5:
                compute_limit = _u32(data, 1)
            elif tag == p.COMPUTE_SET_PRICE and len(data) >= 9:
                compute_price = _u64(data, 1)
            elif tag != 1:                                    # 1 = RequestHeapFrame
                problems.append("COMPUTE_INSTRUCTION_NOT_ALLOWED")
        elif program == p.JUPITER_V6:
            jupiter_routes += 1
            resolved = {account(ix, i) for i in range(len(ix.accounts))}
            if wallet not in resolved:
                problems.append("ROUTE_NOT_FOR_WALLET")
    if jupiter_routes != 1:
        problems.append("EXPECTED_ONE_JUPITER_ROUTE")
    destination_accounts = {associated_token_address(wallet, expected.output_mint, program) for program in p.TOKEN_PROGRAMS}
    if expected.output_mint == p.WSOL:
        destination_accounts |= {wallet}
    if not destination_accounts & set(keys):
        problems.append("OUTPUT_NOT_TO_OWN_ACCOUNT")
    if wrapped and not synced & wsol:
        problems.append("SOL_WRAPPED_WITHOUT_SYNC")
    limit = expected.amount_raw + RENT_ALLOWANCE_LAMPORTS if expected.side == "BUY" else RENT_ALLOWANCE_LAMPORTS
    if wrapped > limit:
        problems.append("SOL_WRAPPED_EXCEEDS_INTENT")
    if compute_limit * compute_price // 1_000_000 > expected.max_priority_fee_lamports:
        problems.append("PRIORITY_FEE_TOO_HIGH")
    return problems


def token_amount(account: dict[str, Any] | None) -> int:
    """SPL token amount (u64 at offset 64, same base layout for Token and Token-2022); 0 for a missing account."""
    if not account:
        return 0
    raw = base64.b64decode(account["data"][0])
    return _u64(raw, 64) if len(raw) >= 72 else 0


def check_simulation(simulation: dict[str, Any], pre: list[dict[str, Any] | None], expected: SwapExpectation,
                     fee_budget_lamports: int) -> list[str]:
    """``pre``/post accounts are [wallet, input token account, output token account] (token accounts may be None)."""
    if simulation.get("err") is not None:
        return [f"SIMULATION_FAILED:{simulation.get('err')}"]
    post = simulation.get("accounts")
    if not isinstance(post, list) or len(post) != 3 or post[0] is None or pre[0] is None:
        return ["SIMULATION_RESULT_INCOMPLETE"]
    sol_delta = int(post[0]["lamports"]) - int(pre[0]["lamports"])
    problems = []
    if expected.side == "BUY":
        received = token_amount(post[2]) - token_amount(pre[2])
        if -sol_delta > expected.amount_raw + fee_budget_lamports:
            problems.append("SIMULATED_SOL_SPEND_EXCEEDS_INTENT")
        if received < expected.min_out_raw:
            problems.append("SIMULATED_OUTPUT_BELOW_MIN_OUT")
    else:
        spent = token_amount(pre[1]) - token_amount(post[1])
        if spent > expected.amount_raw:
            problems.append("SIMULATED_TOKEN_SPEND_EXCEEDS_INTENT")
        if sol_delta < expected.min_out_raw - fee_budget_lamports:
            problems.append("SIMULATED_OUTPUT_BELOW_MIN_OUT")
    return problems
