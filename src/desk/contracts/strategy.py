"""Immutable research hypotheses and deterministic, non-executing decisions.

A heuristic signal score is neither an expected return nor permission to trade.
Every decision in M3 is SHADOW and carries the complete input/config fingerprints.
"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from desk.contracts.discovery import DiscoveryReason, Outcome
from desk.contracts.features import FeatureSnapshot
from desk.contracts.observed import DataStatus, UtcDatetime
from desk.core.hashing import fingerprint


class StrategyKind(StrEnum):
    BASELINE = "BASELINE"
    HYPOTHESIS = "HYPOTHESIS"
    OVERLAY = "OVERLAY"


class StrategyEligibility(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    regimes: tuple[str, ...]
    discovery_reasons: tuple[DiscoveryReason, ...]
    min_discovery_age_seconds: int | None = Field(default=None, ge=0)
    max_discovery_age_seconds: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _age_range(self) -> Self:
        if (
            self.min_discovery_age_seconds is not None
            and self.max_discovery_age_seconds is not None
            and self.max_discovery_age_seconds < self.min_discovery_age_seconds
        ):
            raise ValueError("discovery age maximum must be at least its minimum")
        return self


class FeatureRequirement(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1)
    max_age_seconds: int = Field(gt=0)


class FeatureCondition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    feature: str = Field(min_length=1)
    operator: Literal["gte", "gt", "lte", "lt", "eq"]
    threshold: Decimal = Field(allow_inf_nan=False)


class SizingPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    method: Literal["no_trade", "deferred_to_independent_risk_engine"]
    explanation: str = Field(min_length=1)


class StopPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    loss_fraction: Decimal = Field(ge=0, lt=1, allow_inf_nan=False)


class TrailingPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    activation_gain_fraction: Decimal = Field(ge=0, allow_inf_nan=False)
    drawdown_fraction: Decimal = Field(ge=0, lt=1, allow_inf_nan=False)


class LiquidityExitPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    minimum_verified_liquidity_usd: Decimal = Field(ge=0, allow_inf_nan=False)
    drop_fraction: Decimal = Field(ge=0, le=1, allow_inf_nan=False)


class ExecutionRequirements(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    max_roundtrip_cost_fraction: Decimal = Field(ge=0, lt=1, allow_inf_nan=False)
    max_quote_age_seconds: int = Field(gt=0)
    require_security_pass: Literal[True]
    require_independent_guardian: Literal[True]
    allow_estimated_fills: Literal[False]


class SignalScorePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    method: Literal["zero", "seeded_priority", "feature", "constant"]
    feature: str | None
    multiplier: Decimal = Field(allow_inf_nan=False)
    offset: Decimal = Field(allow_inf_nan=False)

    @model_validator(mode="after")
    def _feature_required(self) -> Self:
        if (self.method == "feature") != (self.feature is not None):
            raise ValueError("only feature scoring requires a feature")
        return self


class StrategySpec(BaseModel):
    """All sections are required; tuple-only children make frozen deeply meaningful.

    Storage enforces the identity of used versions. ``parent_version`` refers to
    the full previous ``version_id`` when research changes any section.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    strategy_id: str = Field(pattern=r"^(B[0-2]|H[1-8]|R1)$")
    version: int = Field(ge=1)
    parent_version: str | None
    kind: StrategyKind
    hypothesis: str = Field(min_length=1)
    allowed_modes: tuple[Literal["SHADOW", "PAPER"], ...] = Field(min_length=1)
    eligibility: StrategyEligibility
    features: tuple[FeatureRequirement, ...]
    entry_conditions: tuple[FeatureCondition, ...]
    rejection_conditions: tuple[FeatureCondition, ...]
    sizing: SizingPolicy
    initial_stop: StopPolicy
    trailing: TrailingPolicy
    max_hold_seconds: int = Field(ge=0)
    liquidity_exits: LiquidityExitPolicy
    execution_requirements: ExecutionRequirements
    invalidation: tuple[str, ...] = Field(min_length=1)
    signal_score: SignalScorePolicy

    @model_validator(mode="after")
    def _complete_and_bounded(self) -> Self:
        if self.version > 1 and not self.parent_version:
            raise ValueError("a changed version requires parent_version")
        if self.version == 1 and self.parent_version is not None:
            raise ValueError("an initial version cannot have a parent")
        names = [requirement.name for requirement in self.features]
        if len(names) != len(set(names)):
            raise ValueError("required features must be unique")
        referenced = {condition.feature for condition in (*self.entry_conditions, *self.rejection_conditions)}
        if self.signal_score.feature is not None:
            referenced.add(self.signal_score.feature)
        if not referenced.issubset(names):
            raise ValueError("every condition must declare its feature and freshness requirement")
        if self.strategy_id != "B0" and not {"verified_liquidity_usd", "roundtrip_cost_fraction"}.issubset(names):
            raise ValueError("non-cash research requires fresh verified liquidity and ticket cost")
        if self.strategy_id == "R1" and self.allowed_modes != ("SHADOW",):
            raise ValueError("R1 is SHADOW research only")
        if self.strategy_id == "H7" and self.kind is not StrategyKind.OVERLAY:
            raise ValueError("H7 must remain an overlay")
        if self.strategy_id == "B0" and self.sizing.method != "no_trade":
            raise ValueError("B0 never trades")
        if self.strategy_id != "B0" and self.max_hold_seconds <= 0:
            raise ValueError("trade hypotheses need a positive maximum holding period")
        return self

    @property
    def spec_hash(self) -> str:
        return fingerprint(self.model_dump(mode="python"))

    @property
    def version_id(self) -> str:
        return f"{self.strategy_id}:{self.version}:{self.spec_hash}"


class DecisionContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    features: FeatureSnapshot
    security_outcome: Outcome
    security_assessed_at: UtcDatetime | None
    discovery_reason: DiscoveryReason
    regime_label: str | None
    regime_status: DataStatus
    regime_version: str = "unclassified"
    security_max_age_seconds: int = Field(default=300, gt=0)
    discovery_observed_at: UtcDatetime | None = None
    # Populate only from a verified migration occurrence. Local first-seen time
    # and a provider's assertion that a pool is already graduated do not qualify.
    migration_occurred_at: UtcDatetime | None = None


class RankedStrategy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    strategy_id: str
    version: int
    spec_hash: str
    eligible: bool
    score: Decimal = Field(allow_inf_nan=False)
    score_kind: Literal["heuristic_signal"] = "heuristic_signal"
    reasons: tuple[str, ...]


class Decision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    candidate_id: str
    as_of: UtcDatetime
    mode: Literal["SHADOW"] = "SHADOW"
    inputs_hash: str
    config_hash: str
    regime_version: str
    ranking: tuple[RankedStrategy, ...]
    chosen: str
    decision_hash: str
