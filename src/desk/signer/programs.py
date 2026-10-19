"""On-chain identities the signer trusts. Every value is verified: the core programs on-chain in M1 (M1-19), the
Jupiter router and transaction shape from a live order on 2026-10-19 (m8_jupiter_order_taker_simulation.json)."""

from __future__ import annotations

SYSTEM = "11111111111111111111111111111111"
COMPUTE_BUDGET = "ComputeBudget111111111111111111111111111111"
TOKEN = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"  # noqa: S105 - program id, not a secret
TOKEN_2022 = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"  # noqa: S105
ASSOCIATED_TOKEN = "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL"  # noqa: S105
JUPITER_V6 = "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4"
WSOL = "So11111111111111111111111111111111111111112"

TOP_LEVEL_ALLOWED = frozenset({SYSTEM, COMPUTE_BUDGET, TOKEN, TOKEN_2022, ASSOCIATED_TOKEN, JUPITER_V6})
TOKEN_PROGRAMS = frozenset({TOKEN, TOKEN_2022})

# instruction tags
SYSTEM_TRANSFER = 2
TOKEN_CLOSE_ACCOUNT = 9
TOKEN_SYNC_NATIVE = 17
ATA_CREATE, ATA_CREATE_IDEMPOTENT = 0, 1
COMPUTE_SET_LIMIT, COMPUTE_SET_PRICE = 2, 3
