"""Untrusted parsed accounts must not masquerade as verified vaults or burned holdings."""

from __future__ import annotations

import pytest

from desk.core.errors import SchemaError
from desk.providers.solana_rpc import TOKEN_PROGRAM
from desk.security.onchain import parse_token_account
from tests.unit.test_security import MINT, POOL, VAULT


def token_account():
    return {"owner": TOKEN_PROGRAM, "executable": False, "data": {"program": "spl-token", "parsed": {
        "type": "account", "info": {"mint": MINT, "owner": POOL, "state": "initialized",
                                      "tokenAmount": {"amount": "6000", "decimals": 6}}}}}


def test_verified_vault_checks_mint_authority_state_and_delegation() -> None:
    account = token_account()
    assert parse_token_account(account, MINT, POOL) == (POOL, 6000, 6)
    with pytest.raises(SchemaError, match="mint mismatch"):
        parse_token_account(account, VAULT, POOL)
    with pytest.raises(SchemaError, match="authority mismatch"):
        parse_token_account(account, MINT, VAULT)
    account["data"]["parsed"]["info"]["delegate"] = VAULT
    with pytest.raises(SchemaError, match="delegated"):
        parse_token_account(account, MINT, POOL)


def test_frozen_and_executable_accounts_are_not_accepted_as_vaults() -> None:
    account = token_account()
    account["data"]["parsed"]["info"]["state"] = "frozen"
    with pytest.raises(SchemaError, match="frozen"):
        parse_token_account(account, MINT, POOL)
    account["executable"] = True
    with pytest.raises(SchemaError, match="executable"):
        parse_token_account(account, MINT, POOL)
