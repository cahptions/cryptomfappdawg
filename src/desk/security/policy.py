"""Pure security rules. Scores are deliberately absent from the input contract."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from desk.contracts.discovery import HolderMetrics, Outcome, PoolVerification, SecurityAssessment, ShieldObservation
from desk.contracts.quotes import QuoteObservation, QuoteUnavailable
from desk.contracts.tokens import MintAccount, TokenClaims
from desk.core.config import DiscoverySection
from desk.core.units import sol_to_lamports
from desk.providers.solana_rpc import TOKEN_2022_PROGRAM, TOKEN_PROGRAM

ALLOWED_MINT_EXTENSIONS = frozenset({"metadataPointer", "tokenMetadata"})
SOL_MINT = "So11111111111111111111111111111111111111112"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"


def mint_rejections(mint: MintAccount) -> list[str]:
    reasons = []
    expected = TOKEN_PROGRAM if mint.token_program == "spl-token" else TOKEN_2022_PROGRAM  # noqa: S105
    if mint.owner_program != expected:
        reasons.append("TOKEN_PROGRAM_CONFLICT")
    if not mint.is_initialized:
        reasons.append("MINT_UNINITIALIZED")
    if mint.mint_authority is not None:
        reasons.append("MINT_AUTHORITY")
    if mint.freeze_authority is not None:
        reasons.append("FREEZE_AUTHORITY")
    for ext in mint.extensions:
        if ext.name not in ALLOWED_MINT_EXTENSIONS:
            reasons.append("EXTENSION_" + ext.name)
    if mint.token_program == "spl-token" and mint.extensions:  # noqa: S105 - public program label
        reasons.append("CLASSIC_MINT_WITH_EXTENSIONS")
    return reasons


def roundtrip_cost(mint: str, buy: QuoteObservation, sell: QuoteObservation, now: datetime,
                   max_age: int) -> Decimal:
    if (buy.input_mint != SOL_MINT or buy.output_mint != mint or sell.input_mint != mint
            or sell.output_mint != SOL_MINT or buy.out_amount_raw != sell.in_amount_raw
            or buy.in_amount_raw <= 0 or buy.out_amount_raw <= 0 or sell.out_amount_raw <= 0
            or buy.swap_mode != "ExactIn" or sell.swap_mode != "ExactIn"
            or buy.error_code is not None or sell.error_code is not None):
        raise ValueError("quote identity, quantities or execution mode mismatch")
    for quote in (buy, sell):
        if not 0 <= (now - quote.received_at).total_seconds() <= max_age:
            raise ValueError("stale or future quote")
        if not quote.requested_at <= quote.received_at or quote.taker is not None or quote.transaction_included:
            raise ValueError("read-only quote evidence required")
    if sell.requested_at < buy.received_at:
        raise ValueError("sell must quote the exact quantity after the buy quote arrived")
    # Fees included in outAmount are not added again. Fixed SOL execution costs are added when supplied.
    fixed = sum((q.signature_fee_lamports or 0) + (q.prioritization_fee_lamports or 0) for q in (buy, sell))
    return Decimal(buy.in_amount_raw - sell.out_amount_raw + fixed) / Decimal(buy.in_amount_raw)


def assess(*, mint_address: str, mint: MintAccount | None, holders: HolderMetrics | None,
           pool: PoolVerification | None, claim: TokenClaims | None,
           buy: QuoteObservation | QuoteUnavailable | None, sell: QuoteObservation | QuoteUnavailable | None,
           now: datetime, settings: DiscoverySection, shield: ShieldObservation | None = None) -> SecurityAssessment:
    reasons: list[str] = []
    missing: list[str] = []
    cost: Decimal | None = None
    if mint is None:
        missing.append("INSUFFICIENT_DATA:AUTHORITY")
    elif mint.mint != mint_address:
        reasons.append("MINT_IDENTITY_CONFLICT")
    else:
        reasons.extend(mint_rejections(mint))
        if not 0 <= (now - mint.observed_at).total_seconds() <= settings.max_snapshot_age_seconds:
            missing.append("INSUFFICIENT_DATA:STALE_MINT")
    if holders is None:
        missing.append("INSUFFICIENT_DATA:HOLDERS")
    else:
        if holders.adjusted_top1_pct > settings.max_top1_pct:
            reasons.append("HOLDER_TOP1")
        if holders.adjusted_top10_pct > settings.max_top10_pct:
            reasons.append("HOLDER_TOP10")
        if holders.dev_pct is not None and holders.dev_pct > settings.max_dev_pct:
            reasons.append("DEV_HOLDINGS")
    if pool is None:
        missing.append("INSUFFICIENT_DATA:LIQUIDITY")
    else:
        if pool.base_mint != mint_address:
            reasons.append("POOL_IDENTITY_CONFLICT")
        if not pool.canonical or not pool.lp_burned:
            reasons.append("LP_UNVERIFIED")
        if pool.unsupported_features:
            reasons.append("UNSUPPORTED_POOL_FEATURE")
        if pool.liquidity_usd < settings.min_liquidity_usd:
            reasons.append("LIQUIDITY_TOO_LOW")
        if not 0 <= (now - pool.observed_at).total_seconds() <= settings.max_snapshot_age_seconds:
            missing.append("INSUFFICIENT_DATA:STALE_LIQUIDITY")
    provider_pct = None
    if shield is None or shield.mint != mint_address:
        missing.append("INSUFFICIENT_DATA:SHIELD")
    elif not 0 <= (now - shield.observed_at).total_seconds() <= settings.max_snapshot_age_seconds:
        missing.append("INSUFFICIENT_DATA:STALE_SHIELD")
    else:
        # Only the two informational types observed in the M0 fixture are understood. All warnings/critical
        # findings and any unfamiliar type fail closed and remain auditable, including provider wording changes.
        for warning in shield.warnings:
            if warning.severity != "info" or warning.type not in {"NOT_VERIFIED", "NEW_LISTING"}:
                reasons.append("SHIELD:" + warning.type)
    if claim is not None:
        if claim.mint != mint_address:
            reasons.append("PROVIDER_IDENTITY_CONFLICT")
        audit = claim.audit
        if audit:
            provider_pct = audit.top_holders_pct
            if (holders is not None and provider_pct is not None
                    and abs(provider_pct - holders.raw_top10_pct) > settings.max_holder_conflict_pp):
                reasons.append("CONFLICT:HOLDERS")
            if audit.is_sus:
                reasons.append("PROVIDER_SUSPICIOUS")
            if (audit.dev_mints is not None and audit.dev_migrations is not None
                    and audit.dev_mints > settings.extreme_dev_mints
                    and Decimal(audit.dev_migrations) / Decimal(audit.dev_mints) < settings.min_dev_migration_fraction):
                reasons.append("EXTREME_DEV_HISTORY")
    if isinstance(sell, QuoteUnavailable):
        reasons.append("NO_SELL_ROUTE")
    elif isinstance(buy, QuoteUnavailable):
        reasons.append("NO_BUY_ROUTE")
    elif not isinstance(buy, QuoteObservation) or not isinstance(sell, QuoteObservation):
        missing.append("INSUFFICIENT_DATA:QUOTES")
    else:
        try:
            if buy.in_amount_raw != sol_to_lamports(settings.ticket_sol):
                raise ValueError("quote ticket differs from the intended M2 assessment ticket")
            cost = roundtrip_cost(mint_address, buy, sell, now, settings.max_quote_age_seconds)
            if cost > settings.max_roundtrip_cost_fraction:
                reasons.append("COST_TOO_HIGH")
        except ValueError:
            missing.append("INSUFFICIENT_DATA:INVALID_OR_STALE_QUOTES")
    # Known violations dominate missing fields; neither state can become PASS via a score.
    outcome = Outcome.REJECT if reasons or missing else Outcome.PASS
    return SecurityAssessment(mint=mint_address, assessed_at=now, outcome=outcome,
                              reasons=tuple(reasons + missing), holders=holders,
                              provider_top_holders_pct=provider_pct,
                              provider_claim_source=None if claim is None else claim.source,
                              roundtrip_cost_fraction=cost,
                              pool_verification=pool,
                              buy_quote=buy if isinstance(buy, QuoteObservation) else None,
                              sell_quote=sell if isinstance(sell, QuoteObservation) else None,
                              fixed_fees_complete=all(isinstance(q, QuoteObservation)
                                  and q.signature_fee_lamports is not None and q.prioritization_fee_lamports is not None
                                  for q in (buy, sell)))
