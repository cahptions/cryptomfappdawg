"""M2 audit contracts. Provider assertions never become verified on-chain facts implicitly."""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from desk.contracts.observed import SolAddress, UtcDatetime


class DiscoveryReason(StrEnum):
    NEW_TOKEN = "NEW_TOKEN"  # noqa: S105 - discovery classification, not a credential
    NEW_POOL = "NEW_POOL"
    MIGRATION = "MIGRATION"
    RENEWED_ACTIVITY = "RENEWED_ACTIVITY"
    VOLUME_EXPANSION = "VOLUME_EXPANSION"
    ACCELERATION = "ACCELERATION"


class Outcome(StrEnum):
    PASS = "PASS"  # noqa: S105 - policy outcome, not a credential
    REJECT = "REJECT"
    DEFER = "DEFER"


class StageResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    candidate_id: str
    stage: int = Field(ge=0, le=4)
    outcome: Outcome
    rule_id: str
    observed_values: dict[str, Any]
    thresholds: dict[str, Any]
    config_hash: str
    decided_at: UtcDatetime


class HolderAccount(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    address: SolAddress
    owner: SolAddress
    amount_raw: int = Field(ge=0)


class HolderMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    raw_top1_pct: Decimal
    raw_top10_pct: Decimal
    adjusted_top1_pct: Decimal
    adjusted_top10_pct: Decimal
    dev_pct: Decimal | None
    unclassified_pct: Decimal
    observed_supply_pct: Decimal
    excluded: dict[str, str]
    attribution: Literal["on_chain_token_owners"] = "on_chain_token_owners"
    coverage: Literal["top20_accounts_lower_bound"] = "top20_accounts_lower_bound"


class ClusterClaim(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    label: Literal["suspected_cluster"] = "suspected_cluster"
    wallets: tuple[SolAddress, ...]
    evidence: tuple[str, ...] = Field(min_length=1)


class ShieldWarning(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    type: str
    severity: Literal["info", "warning", "critical"]


class ShieldObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    mint: SolAddress
    warnings: tuple[ShieldWarning, ...]
    observed_at: UtcDatetime
    source: str = "jupiter.ultra_shield"
    raw_sha256: str | None = None


class PoolVerification(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    pool_address: SolAddress
    base_mint: SolAddress
    quote_mint: SolAddress
    base_vault: SolAddress
    quote_vault: SolAddress
    canonical: bool
    lp_burned: bool
    liquidity_usd: Decimal = Field(ge=0)
    observed_at: UtcDatetime
    slot: int = Field(ge=0)
    raw_refs: tuple[str, ...]
    coin_creator: SolAddress | None = None
    unsupported_features: tuple[str, ...] = ()
    valuation_basis: str = "twice quote reserves at pool midpoint; USDC assumed USD 1; SOL valued using SOL/USDC quote"
    pool_price_usd: Decimal | None = Field(default=None, ge=0)


class SecurityAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    mint: SolAddress
    assessed_at: UtcDatetime
    outcome: Outcome
    reasons: tuple[str, ...]
    holders: HolderMetrics | None = None
    provider_top_holders_pct: Decimal | None = None
    provider_claim_source: str | None = None
    roundtrip_cost_fraction: Decimal | None = None
    cost_scope: Literal["quote_implied_plus_reported_fixed_fees"] = "quote_implied_plus_reported_fixed_fees"
    fixed_fees_complete: bool = False
    unprovable: tuple[str, ...] = (
        "Future liquidity and sell-route availability",
        "Unobserved holdings outside the top twenty token accounts",
        "AMM program upgrades and state changes after observation",
        "Actual fills or transaction landing from a quote alone",
    )
