"""M2 security boundaries, concentration arithmetic, and exact-quantity quote checks."""

from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError
from solders.pubkey import Pubkey

from desk.contracts.discovery import ClusterClaim, HolderAccount, Outcome, PoolVerification, ShieldObservation, ShieldWarning
from desk.contracts.quotes import QuoteObservation, QuoteUnavailable
from desk.contracts.tokens import MintAccount, MintExtension, TokenAudit, TokenClaims
from desk.core.base58 import b58encode
from desk.core.config import load_config
from desk.core.errors import SchemaError
from desk.core.paths import REPO_ROOT
from desk.providers.parsing import load_json
from desk.providers.solana_rpc import TOKEN_2022_PROGRAM, parse_mint_account, rpc_result
from desk.providers.venues import VENUE_PROGRAMS
from desk.security.holders import concentration
from desk.security.onchain import POOL_DISCRIMINATOR, decode_pool
from desk.security.policy import SOL_MINT, assess, mint_rejections, roundtrip_cost
from tests.conftest import load_fixture

NOW = datetime(2026, 10, 19, 2, tzinfo=UTC)
MINT = b58encode(bytes([3]) * 32)
POOL = b58encode(bytes([4]) * 32)
VAULT = b58encode(bytes([5]) * 32)
SETTINGS = load_config(REPO_ROOT / "config").settings.discovery


def mint_account() -> MintAccount:
    return MintAccount(mint=MINT, owner_program=TOKEN_2022_PROGRAM, token_program="spl-token-2022", decimals=6,
                       supply_raw=10000, mint_authority=None, freeze_authority=None, is_initialized=True,
                       extensions=(MintExtension(name="metadataPointer"), MintExtension(name="tokenMetadata")),
                       slot=123, observed_at=NOW)


def quotes() -> tuple[QuoteObservation, QuoteObservation]:
    buy = QuoteObservation(source="jupiter.order_v2", input_mint=SOL_MINT, output_mint=MINT,
                           in_amount_raw=100_000_000, out_amount_raw=1_000_000, swap_mode="ExactIn",
                           requested_at=NOW - timedelta(seconds=3), received_at=NOW - timedelta(seconds=2),
                           latency_ms=Decimal(100))
    sell = QuoteObservation(source="jupiter.order_v2", input_mint=MINT, output_mint=SOL_MINT,
                            in_amount_raw=1_000_000, out_amount_raw=98_000_000, swap_mode="ExactIn",
                            requested_at=NOW - timedelta(seconds=1.5), received_at=NOW - timedelta(seconds=1),
                            latency_ms=Decimal(100))
    return buy, sell


def holder_metrics():
    accounts = [HolderAccount(address=VAULT, owner=POOL, amount_raw=6000)] + [
        HolderAccount(address=b58encode(bytes([i]) * 32), owner=b58encode(bytes([i + 40]) * 32), amount_raw=100)
        for i in range(10, 29)]
    return concentration(accounts, 10000, {VAULT: "verified_pool_vault"})


def pool_verification() -> PoolVerification:
    return PoolVerification(pool_address=POOL, base_mint=MINT, quote_mint=SOL_MINT, base_vault=VAULT,
                            quote_vault=b58encode(bytes([6]) * 32), canonical=True, lp_burned=True,
                            liquidity_usd=Decimal(100000), observed_at=NOW, slot=123, raw_refs=())


def assessment(**changes):
    buy, sell = quotes()
    arguments = dict(mint_address=MINT, mint=mint_account(), holders=holder_metrics(), pool=pool_verification(),
                     claim=None, buy=buy, sell=sell, now=NOW, settings=SETTINGS,
                     shield=ShieldObservation(mint=MINT, warnings=(), observed_at=NOW))
    arguments.update(changes)
    return assess(**arguments)


def test_real_pump_token2022_metadata_only_fixture_passes_extension_policy() -> None:
    body = load_fixture("provider/solana_public_rpc_getAccountInfo_token2022_mint.json")
    # The fixture's mint identity is supplied by the RPC request, not inferred from metadata text.
    parsed = parse_mint_account(rpc_result(body), MINT, NOW)
    assert parsed is not None
    assert {x.name for x in parsed.extensions} == {"metadataPointer", "tokenMetadata"}
    assert mint_rejections(parsed) == []


@pytest.mark.parametrize("name", ["transferFeeConfig", "transferHook", "permanentDelegate", "defaultAccountState",
                                  "nonTransferable", "pausable", "confidentialTransferMint", "futureExtension"])
def test_unsafe_and_unknown_extensions_fail_closed(name: str) -> None:
    mint = mint_account().model_copy(update={"extensions": (MintExtension(name=name, state={"state": "frozen"}),)})
    assert "EXTENSION_" + name in mint_rejections(mint)


@pytest.mark.parametrize("field,reason", [("mint_authority", "MINT_AUTHORITY"), ("freeze_authority", "FREEZE_AUTHORITY")])
def test_authorities_are_hard_rejections(field: str, reason: str) -> None:
    result = assessment(mint=mint_account().model_copy(update={field: POOL}))
    assert result.outcome is Outcome.REJECT and reason in result.reasons


def test_missing_authority_fields_cannot_parse_as_disabled_authorities() -> None:
    body = load_json(load_fixture("provider/solana_public_rpc_getAccountInfo_token2022_mint.json"))
    del body["result"]["value"]["data"]["parsed"]["info"]["mintAuthority"]
    with pytest.raises(SchemaError, match="fields missing"):
        parse_mint_account(body["result"], MINT, NOW)


def test_empty_authority_string_is_not_a_disabled_authority() -> None:
    body = load_json(load_fixture("provider/solana_public_rpc_getAccountInfo_token2022_mint.json"))
    body["result"]["value"]["data"]["parsed"]["info"]["mintAuthority"] = ""
    with pytest.raises(ValidationError):
        parse_mint_account(body["result"], MINT, NOW)


def test_saved_live_pool_passes_identity_checks_and_unsupported_features_remain_explicit() -> None:
    evidence = load_json((REPO_ROOT / "research/evidence/2026-10-19/m2_pumpswap_pool_account.json").read_bytes())
    decoded = decode_pool(evidence["pool_address"], evidence["rpc_response"]["result"]["value"])
    assert decoded["canonical"] is True
    assert decoded["base"] == evidence["decoded"]["base"]
    assert decoded["unsupported"]  # This observed pool's extra features are deliberately ineligible.


def test_concentration_excludes_only_verified_accounts_without_renormalizing_supply() -> None:
    metrics = holder_metrics()
    assert metrics.raw_top1_pct == 60
    assert metrics.raw_top10_pct == 69
    assert metrics.adjusted_top1_pct == 1
    assert metrics.adjusted_top10_pct == 10
    assert metrics.unclassified_pct == 19
    assert metrics.observed_supply_pct == 79
    assert metrics.coverage == "top20_accounts_lower_bound"


def test_verified_curve_and_burn_excluded_but_unverified_infrastructure_counts() -> None:
    accounts = [HolderAccount(address=b58encode(bytes([i]) * 32), owner=b58encode(bytes([i + 20]) * 32), amount_raw=amount)
                for i, amount in [(10, 4000), (11, 2000), (12, 1000), (13, 1200)]]
    proofs = {accounts[0].address: "verified_pool_vault", accounts[1].address: "verified_bonding_curve",
              accounts[2].address: "verified_incinerator_owner"}
    metrics = concentration(accounts, 10000, proofs)
    assert metrics.adjusted_top1_pct == 12
    assert metrics.unclassified_pct == 12
    assert accounts[3].address not in metrics.excluded


def test_multiple_token_accounts_with_same_verified_owner_are_aggregated() -> None:
    accounts = [HolderAccount(address=b58encode(bytes([i]) * 32), owner=POOL, amount_raw=600) for i in (10, 11)]
    assert concentration(accounts, 10000, {}).adjusted_top1_pct == 12


def test_duplicate_holder_account_is_not_double_counted() -> None:
    account = HolderAccount(address=VAULT, owner=POOL, amount_raw=100)
    with pytest.raises(ValueError, match="duplicate"):
        concentration([account, account], 10000, {})


def test_provider_claim_is_separate_and_disagreement_flagged() -> None:
    claim = TokenClaims(source="jupiter.tokens_search", mint=MINT, observed_at=NOW,
                        audit=TokenAudit(top_holders_pct=Decimal(10)))
    result = assessment(claim=claim)
    assert result.provider_top_holders_pct == 10
    assert result.holders.raw_top10_pct == 69
    assert "CONFLICT:HOLDERS" in result.reasons


def test_cluster_contract_cannot_claim_common_ownership() -> None:
    with pytest.raises(ValidationError):
        ClusterClaim(label="same_owner", wallets=(MINT, POOL), evidence=("shared funder",))


def test_exact_quote_roundtrip_and_fixed_fee_completeness() -> None:
    buy, sell = quotes()
    assert roundtrip_cost(MINT, buy, sell, NOW, 10) == Decimal("0.02")
    result = assessment()
    assert result.outcome is Outcome.PASS
    assert not result.fixed_fees_complete
    assert result.cost_scope == "quote_implied_plus_reported_fixed_fees"


@pytest.mark.parametrize("change", [
    {"in_amount_raw": 999999}, {"input_mint": POOL}, {"output_mint": MINT},
    {"received_at": NOW - timedelta(seconds=11)}, {"received_at": NOW + timedelta(seconds=1)},
    {"requested_at": NOW - timedelta(seconds=4)}, {"transaction_included": True},
])
def test_mismatched_or_stale_quotes_cannot_establish_sellability(change: dict) -> None:
    buy, sell = quotes()
    with pytest.raises(ValueError):
        roundtrip_cost(MINT, buy, sell.model_copy(update=change), NOW, 10)


def test_no_sell_route_and_high_cost_rejected() -> None:
    unavailable = QuoteUnavailable(source="jupiter", input_mint=MINT, output_mint=SOL_MINT,
                                   in_amount_raw=1_000_000, error_code="NO_ROUTE", error_message=None,
                                   http_status=200, received_at=NOW)
    assert "NO_SELL_ROUTE" in assessment(sell=unavailable).reasons
    _, sell = quotes()
    assert "COST_TOO_HIGH" in assessment(sell=sell.model_copy(update={"out_amount_raw": 90_000_000})).reasons


@pytest.mark.parametrize("field", ["mint", "holders", "pool"])
def test_missing_hard_rule_data_never_passes(field: str) -> None:
    result = assessment(**{field: None})
    assert result.outcome is Outcome.REJECT
    assert any(r.startswith("INSUFFICIENT_DATA:") for r in result.reasons)


def test_unverified_lp_and_unknown_pool_features_never_pass() -> None:
    assert "LP_UNVERIFIED" in assessment(pool=pool_verification().model_copy(update={"lp_burned": False})).reasons
    assert "UNSUPPORTED_POOL_FEATURE" in assessment(
        pool=pool_verification().model_copy(update={"unsupported_features": ("mayhem",)})).reasons


def test_maximal_model_score_is_not_an_accepted_policy_input() -> None:
    with pytest.raises(TypeError, match="model_score"):
        assessment(model_score=1)


def test_shield_warnings_unknown_types_and_missing_results_fail_closed() -> None:
    assert "INSUFFICIENT_DATA:SHIELD" in assessment(shield=None).reasons
    for kind, severity in (("MINT_AUTHORITY", "warning"), ("NEW_UNKNOWN_CHECK", "info")):
        shield = ShieldObservation(mint=MINT, observed_at=NOW, warnings=(ShieldWarning(type=kind, severity=severity),))
        assert "SHIELD:" + kind in assessment(shield=shield).reasons
    info = ShieldObservation(mint=MINT, observed_at=NOW,
                             warnings=(ShieldWarning(type="NEW_LISTING", severity="info"),))
    assert assessment(shield=info).outcome is Outcome.PASS


def canonical_pool_bytes() -> tuple[str, bytes]:
    base = Pubkey.from_string(MINT)
    creator, _ = Pubkey.find_program_address([b"pool-authority", bytes(base)],
                                             Pubkey.from_string(VENUE_PROGRAMS["pumpfun_bonding_curve"]))
    program = Pubkey.from_string(VENUE_PROGRAMS["pumpswap"])
    pool, bump = Pubkey.find_program_address([b"pool", bytes(2), bytes(creator), bytes(base),
                                             bytes(Pubkey.from_string(SOL_MINT))], program)
    lp, _ = Pubkey.find_program_address([b"pool_lp_mint", bytes(pool)], program)
    data = (POOL_DISCRIMINATOR + bytes([bump]) + bytes(2) + bytes(creator) + bytes(base)
            + bytes(Pubkey.from_string(SOL_MINT)) + bytes(lp) + bytes(Pubkey.from_string(VAULT))
            + bytes(Pubkey.from_string(POOL)) + (10000).to_bytes(8, "little") + bytes(32))
    return str(pool), data


def test_pumpswap_layout_checks_program_pdas_and_canonical_creator() -> None:
    address, data = canonical_pool_bytes()
    account = {"owner": VENUE_PROGRAMS["pumpswap"], "executable": False,
               "data": [base64.b64encode(data).decode(), "base64"]}
    parsed = decode_pool(address, account)
    assert parsed["canonical"] and parsed["base"] == MINT
    with pytest.raises(SchemaError, match="PDA"):
        decode_pool(POOL, account)
    with pytest.raises(SchemaError, match="owner"):
        decode_pool(address, {**account, "owner": TOKEN_2022_PROGRAM})
    with pytest.raises(SchemaError, match="truncated"):
        decode_pool(address, {**account, "data": [base64.b64encode(data[:100]).decode(), "base64"]})
