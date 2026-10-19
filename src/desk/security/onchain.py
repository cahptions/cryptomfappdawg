"""Conservative PumpSwap verifier using the official pinned account layout.

Only canonical Pump migrations with an entirely burned LP mint are eligible. Other venues remain
LP_UNVERIFIED. Public keys and PDA derivation use solders; there is no signer or wallet here.
"""

from __future__ import annotations

import base64
import struct
from decimal import Decimal
from typing import Any

from solders.pubkey import Pubkey

from desk.contracts.discovery import HolderAccount, HolderMetrics, PoolVerification
from desk.contracts.market import PoolSnapshot
from desk.contracts.tokens import MintAccount
from desk.core.base58 import b58encode
from desk.core.errors import SchemaError
from desk.providers.factory import ProviderSuite
from desk.providers.parsing import expect_dict, expect_list, strict_int
from desk.providers.solana_rpc import TOKEN_2022_PROGRAM, TOKEN_PROGRAM, parse_mint_account
from desk.providers.venues import VENUE_PROGRAMS
from desk.security.holders import concentration
from desk.security.policy import SOL_MINT, USDC_MINT

POOL_DISCRIMINATOR = bytes([241, 154, 109, 4, 17, 177, 109, 188])
CURVE_DISCRIMINATOR = bytes([23, 183, 248, 55, 96, 216, 172, 96])
BURN_OWNER = "1nc1nerator11111111111111111111111111111111"


def _pda(seeds: list[bytes], program: str) -> tuple[str, int]:
    key, bump = Pubkey.find_program_address(seeds, Pubkey.from_string(program))
    return str(key), bump


def _bytes(account: dict[str, Any]) -> bytes:
    data = expect_list(account.get("data"), "base64 account")
    if len(data) != 2 or data[1] != "base64" or account.get("executable") is not False:
        raise SchemaError("non-executable base64 account required")
    return base64.b64decode(data[0], validate=True)


def decode_pool(address: str, account: dict[str, Any]) -> dict[str, Any]:
    if account.get("owner") != VENUE_PROGRAMS["pumpswap"]:
        raise SchemaError("LP_UNVERIFIED: pool owner is not PumpSwap")
    data = _bytes(account)
    if len(data) < 243 or data[:8] != POOL_DISCRIMINATOR:
        raise SchemaError("LP_UNVERIFIED: unknown or truncated PumpSwap pool layout")
    bump, index = data[8], struct.unpack_from("<H", data, 9)[0]
    keys = [b58encode(data[offset:offset + 32]) for offset in range(11, 203, 32)]
    creator, base, quote, lp_mint, base_vault, quote_vault = keys
    expected, expected_bump = _pda(
        [b"pool", index.to_bytes(2, "little"), bytes(Pubkey.from_string(creator)),
         bytes(Pubkey.from_string(base)), bytes(Pubkey.from_string(quote))], VENUE_PROGRAMS["pumpswap"])
    if expected != address or bump != expected_bump:
        raise SchemaError("pool PDA or bump mismatch")
    authority, _ = _pda([b"pool-authority", bytes(Pubkey.from_string(base))], VENUE_PROGRAMS["pumpfun_bonding_curve"])
    expected_lp, _ = _pda([b"pool_lp_mint", bytes(Pubkey.from_string(address))], VENUE_PROGRAMS["pumpswap"])
    if lp_mint != expected_lp:
        raise SchemaError("LP mint PDA mismatch")
    unsupported = []
    # Supported legacy lengths end at complete fields. Reallocated zero padding is harmless.
    boundaries = {243, 244, 245, 261, 269, 270, 271, 279, 287}
    if len(data) < 287 and len(data) not in boundaries:
        raise SchemaError("truncated Pool extension fields")
    for offset, name in ((243, "mayhem"), (244, "cashback"), (269, "editable_creator_fee"), (270, "holder_rewards")):
        if len(data) > offset:
            if data[offset] not in (0, 1):
                raise SchemaError("invalid Pool boolean")
            if data[offset]:
                unsupported.append(name)
    if len(data) >= 261 and int.from_bytes(data[245:261], "little", signed=True) != 0:
        unsupported.append("virtual_quote_reserves")
    if len(data) > 287 and any(data[287:]):
        unsupported.append("unknown_pool_tail")
    return {"base": base, "quote": quote, "lp_mint": lp_mint, "base_vault": base_vault,
            "quote_vault": quote_vault, "canonical": index == 0 and creator == authority,
            "coin_creator": None if data[211:243] == bytes(32) else b58encode(data[211:243]),
            "unsupported": tuple(unsupported)}


def parse_token_account(value: Any, mint: str, authority: str | None = None) -> tuple[str, int, int]:
    account = expect_dict(value, "token account")
    if account.get("executable") is not False:
        raise SchemaError("executable account cannot be a token account")
    if account.get("owner") not in (TOKEN_PROGRAM, TOKEN_2022_PROGRAM):
        raise SchemaError("token account has an unexpected owner program")
    data = expect_dict(account.get("data"), "token data")
    parsed = expect_dict(data.get("parsed"), "parsed token account")
    if parsed.get("type") != "account":
        raise SchemaError("expected a parsed token account")
    info = expect_dict(parsed.get("info"), "token info")
    if info.get("mint") != mint or info.get("state") != "initialized":
        raise SchemaError("token account mint mismatch or frozen/uninitialized account")
    owner = str(info["owner"])
    if authority is not None and owner != authority:
        raise SchemaError("vault token authority mismatch")
    if authority is not None and info.get("delegate") is not None:
        raise SchemaError("pool vault has a delegated spending authority")
    amount = expect_dict(info.get("tokenAmount"), "token amount")
    raw, decimals = strict_int(amount["amount"]), strict_int(amount["decimals"])
    if raw < 0 or not 0 <= decimals <= 255:
        raise SchemaError("invalid token amount")
    return owner, raw, decimals


async def verify_pool(suite: ProviderSuite, snap: PoolSnapshot, mint: MintAccount,
                      sol_usd: Decimal) -> PoolVerification:
    result, resp = await suite.rpc.call("getAccountInfo", [snap.pool_address,
        {"encoding": "base64", "commitment": "confirmed", "minContextSlot": mint.slot}])
    doc = expect_dict(result, "pool result")
    slot = strict_int(expect_dict(doc["context"], "context")["slot"])
    if slot < mint.slot:
        raise SchemaError("pool response predates required mint context")
    fields = decode_pool(snap.pool_address, expect_dict(doc.get("value"), "pool account"))
    if fields["base"] != snap.base_mint or fields["quote"] != snap.quote_mint:
        raise SchemaError("pool mint identities disagree with provider")
    quote_mint = str(fields["quote"])
    if quote_mint not in (SOL_MINT, USDC_MINT):
        raise SchemaError("unsupported quote mint")
    addresses = [str(fields["base_vault"]), str(fields["quote_vault"]), str(fields["lp_mint"])]
    accounts, accounts_resp = await suite.rpc.get_multiple_accounts(addresses, min_context_slot=slot)
    accounts_doc = expect_dict(accounts, "vault result")
    if strict_int(expect_dict(accounts_doc["context"], "context")["slot"]) < slot:
        raise SchemaError("vault response predates required pool context")
    values = expect_list(accounts_doc["value"], "vaults")
    if len(values) != 3:
        raise SchemaError("incomplete vault/LP response")
    _, base_raw, base_decimals = parse_token_account(values[0], snap.base_mint, snap.pool_address)
    _, quote_raw, quote_decimals = parse_token_account(values[1], quote_mint, snap.pool_address)
    if base_decimals != mint.decimals or quote_decimals != (9 if quote_mint == SOL_MINT else 6):
        raise SchemaError("vault decimals disagree with mint")
    lp = parse_mint_account({"context": accounts_doc["context"], "value": values[2]},
                            str(fields["lp_mint"]), accounts_resp.received_at, accounts_resp.sha256)
    if lp is None or lp.mint_authority != snap.pool_address or lp.freeze_authority is not None:
        raise SchemaError("LP mint authority mismatch")
    unsupported = tuple(fields["unsupported"])
    if lp.owner_program != TOKEN_PROGRAM or lp.extensions:
        unsupported += ("unsupported_lp_token_program",)
    quote_value = Decimal(quote_raw).scaleb(-quote_decimals) * (sol_usd if quote_mint == SOL_MINT else Decimal(1))
    return PoolVerification(
        pool_address=snap.pool_address, base_mint=snap.base_mint, quote_mint=quote_mint,
        base_vault=str(fields["base_vault"]), quote_vault=str(fields["quote_vault"]),
        canonical=bool(fields["canonical"]), lp_burned=lp.supply_raw == 0,
        liquidity_usd=quote_value * 2 if base_raw > 0 else Decimal(0),
        observed_at=accounts_resp.received_at, slot=strict_int(accounts_doc["context"]["slot"]),
        raw_refs=(resp.sha256, accounts_resp.sha256), coin_creator=fields["coin_creator"],
        unsupported_features=unsupported,
        pool_price_usd=quote_value / Decimal(base_raw).scaleb(-base_decimals) if base_raw > 0 else None,
    )


async def verify_holders(suite: ProviderSuite, mint: MintAccount, pool: PoolVerification
                         ) -> tuple[HolderMetrics, tuple[str, ...]]:
    result, largest_resp = await suite.rpc.call("getTokenLargestAccounts", [mint.mint, {"commitment": "confirmed"}])
    doc = expect_dict(result, "largest accounts")
    rows = [expect_dict(r, "holder") for r in expect_list(doc.get("value"), "holders")]
    if not 1 <= len(rows) <= 20:
        raise SchemaError("expected 1-20 holder accounts")
    slot = strict_int(expect_dict(doc["context"], "context")["slot"])
    result, owners_resp = await suite.rpc.get_multiple_accounts([str(r["address"]) for r in rows],
                                                               min_context_slot=max(slot, mint.slot, pool.slot))
    owners_doc = expect_dict(result, "holder accounts")
    if strict_int(expect_dict(owners_doc["context"], "context")["slot"]) < max(slot, mint.slot, pool.slot):
        raise SchemaError("holder owner response predates required context")
    values = expect_list(owners_doc["value"], "holder accounts")
    if len(values) != len(rows):
        raise SchemaError("incomplete holder owner response")
    accounts = []
    exclusions = {}
    curve, _ = _pda([b"bonding-curve", bytes(Pubkey.from_string(mint.mint))],
                     VENUE_PROGRAMS["pumpfun_bonding_curve"])
    curve_accounts = []
    for row, value in zip(rows, values, strict=True):
        owner, amount, decimals = parse_token_account(value, mint.mint)
        if amount != strict_int(row["amount"]) or decimals != mint.decimals:
            raise SchemaError("holder balances changed between observations; retry with fresh evidence")
        account = HolderAccount(address=str(row["address"]), owner=owner, amount_raw=amount)
        accounts.append(account)
        if account.address == pool.base_vault and owner == pool.pool_address:
            exclusions[account.address] = "verified_pool_vault"
        elif owner == BURN_OWNER:
            info = expect_dict(expect_dict(expect_dict(value, "account")["data"], "data")["parsed"], "parsed")["info"]
            if info.get("delegate") is None and info.get("delegatedAmount") is None:
                exclusions[account.address] = "verified_incinerator_owner_no_delegate"
        elif owner == curve:
            curve_accounts.append(account.address)
    refs = [largest_resp.sha256, owners_resp.sha256]
    if curve_accounts:
        result, curve_resp = await suite.rpc.call("getAccountInfo", [curve, {"encoding": "base64", "commitment": "confirmed"}])
        value = expect_dict(result, "curve result").get("value")
        if (isinstance(value, dict) and value.get("owner") == VENUE_PROGRAMS["pumpfun_bonding_curve"]
                and _bytes(value)[:8] == CURVE_DISCRIMINATOR):
            exclusions.update({a: "verified_bonding_curve" for a in curve_accounts})
        refs.append(curve_resp.sha256)
    return concentration(accounts, mint.supply_raw, exclusions, pool.coin_creator), tuple(refs)
