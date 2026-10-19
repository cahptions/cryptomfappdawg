"""Small, explicit research hypotheses. Thresholds are unvalidated starting points.

These specs do not assert an edge. Missing chronology (spike/retest evidence) is
declared as missing rather than inferred from an unrelated rolling high.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Literal

from desk.contracts.discovery import DiscoveryReason
from desk.contracts.strategy import (
    ExecutionRequirements,
    FeatureCondition,
    FeatureRequirement,
    LiquidityExitPolicy,
    SignalScorePolicy,
    SizingPolicy,
    StopPolicy,
    StrategyEligibility,
    StrategyKind,
    StrategySpec,
    TrailingPolicy,
)

_REGIMES = ("RISK_ON", "NEUTRAL", "CHOPPY")
_COMMON_FEATURES = ("verified_liquidity_usd", "roundtrip_cost_fraction")


def _condition(feature: str, operator: Literal["gte", "gt", "lte", "lt", "eq"], value: str) -> FeatureCondition:
    return FeatureCondition(feature=feature, operator=operator, threshold=Decimal(value))


def _score(
    method: Literal["zero", "seeded_priority", "feature", "constant"],
    *,
    feature: str | None = None,
    multiplier: str = "1",
    offset: str = "0",
) -> SignalScorePolicy:
    return SignalScorePolicy(method=method, feature=feature, multiplier=Decimal(multiplier), offset=Decimal(offset))


def _spec(
    strategy_id: str,
    hypothesis: str,
    *,
    conditions: tuple[FeatureCondition, ...] = (),
    rejections: tuple[FeatureCondition, ...] = (),
    extra_features: tuple[str, ...] = (),
    reasons: tuple[DiscoveryReason, ...] = (),
    discovery_age: tuple[int, int] | None = None,
    max_hold_seconds: int = 21600,
    signal_score: SignalScorePolicy | None = None,
) -> StrategySpec:
    no_trade = strategy_id == "B0"
    names = set() if no_trade else set(_COMMON_FEATURES)
    names.update(condition.feature for condition in (*conditions, *rejections))
    names.update(extra_features)
    score = signal_score or _score("constant", offset="1")
    if score.feature is not None:
        names.add(score.feature)
    return StrategySpec(
        strategy_id=strategy_id,
        version=1,
        parent_version=None,
        kind=(
            StrategyKind.OVERLAY if strategy_id == "H7"
            else StrategyKind.BASELINE if strategy_id.startswith("B")
            else StrategyKind.HYPOTHESIS
        ),
        hypothesis=hypothesis,
        allowed_modes=("SHADOW",),
        eligibility=StrategyEligibility(
            regimes=() if no_trade else _REGIMES,
            discovery_reasons=reasons,
            min_discovery_age_seconds=discovery_age[0] if discovery_age is not None else None,
            max_discovery_age_seconds=discovery_age[1] if discovery_age is not None else None,
        ),
        features=tuple(
            FeatureRequirement(name=name, max_age_seconds=10 if name == "roundtrip_cost_fraction" else 120)
            for name in sorted(names)
        ),
        entry_conditions=conditions,
        rejection_conditions=rejections,
        sizing=SizingPolicy(
            method="no_trade" if no_trade else "deferred_to_independent_risk_engine",
            explanation=(
                "Cash baseline; zero orders and zero positions."
                if no_trade else
                "Signal only in M3. Future sizing must pass independent fee-floor, capital, liquidity and risk caps."
            ),
        ),
        initial_stop=StopPolicy(loss_fraction=Decimal("0") if no_trade else Decimal("0.10")),
        trailing=TrailingPolicy(activation_gain_fraction=Decimal("0.15"), drawdown_fraction=Decimal("0.10")),
        max_hold_seconds=0 if no_trade else max_hold_seconds,
        liquidity_exits=LiquidityExitPolicy(minimum_verified_liquidity_usd=Decimal("40000"), drop_fraction=Decimal("0.20")),
        execution_requirements=ExecutionRequirements(
            max_roundtrip_cost_fraction=Decimal("0.03"),
            max_quote_age_seconds=10,
            require_security_pass=True,
            require_independent_guardian=True,
            allow_estimated_fills=False,
        ),
        invalidation=(
            "Missing, stale, unverified or conflicting required feature",
            "Security ceases to pass, quote route unavailable, or liquidity falls below the specified boundary",
            "Maximum holding period reached; independent guardian/risk authorization unavailable before any future execution",
        ),
        signal_score=score,
    )


def builtin_specs() -> tuple[StrategySpec, ...]:
    """Return fresh immutable versions; no trial is promoted or tuned automatically."""
    return (
        _spec("B0", "NO-TRADE: cash, zero return and zero execution cost.", signal_score=_score("zero")),
        _spec(
            "B1", "Seeded random priority among security-eligible candidates; selection baseline, not an edge.",
            signal_score=_score("seeded_priority"),
        ),
        _spec(
            "B2", "Naive momentum: prioritize eligible tokens by observed one-hour log return.",
            extra_features=("log_return_1h",), signal_score=_score("feature", feature="log_return_1h", multiplier="100"),
        ),
        _spec(
            "H1", "Post-migration continuation, 10–60 minutes after an evidenced migration.",
            reasons=(DiscoveryReason.MIGRATION,), discovery_age=(600, 3600),
            conditions=(
                _condition("distinct_buyers_1h", "gte", "100"),
                _condition("organic_volume_share", "gte", "0.5"),
                _condition("log_return_15m", "gte", "0"),
            ),
        ),
        _spec(
            "H2", "Initial-spike retracement of 20–60%, stabilizing activity and intact liquidity.",
            conditions=(
                _condition("initial_spike_return", "gte", "0.30"),
                _condition("spike_retracement_fraction", "gte", "0.20"),
                _condition("spike_retracement_fraction", "lte", "0.60"),
                _condition("volume_acceleration", "gte", "0.80"),
                _condition("volume_acceleration", "lte", "1.25"),
                _condition("liquidity_change_1h", "gte", "0"),
            ),
        ),
        _spec(
            "H3", "Rolling six-hour-high breakout supported by rising liquidity and distinct buyers.",
            max_hold_seconds=14400,
            conditions=(
                _condition("distance_from_high_6h", "gte", "-0.005"),
                _condition("log_return_1h", "gte", "0.10"),
                _condition("liquidity_change_1h", "gte", "0.10"),
                _condition("distinct_buyers_1h", "gte", "100"),
            ),
        ),
        _spec(
            "H4", "Pullback following an explicitly observed volume-expansion discovery event.",
            reasons=(DiscoveryReason.VOLUME_EXPANSION,),
            conditions=(
                _condition("volume_acceleration", "gte", "2"),
                _condition("distance_from_high_6h", "lte", "-0.10"),
                _condition("distance_from_high_6h", "gte", "-0.30"),
                _condition("liquidity_change_1h", "gte", "0"),
                _condition("log_return_5m", "gte", "0"),
            ),
        ),
        _spec(
            "H5", "Confirmed breakout and retest after a narrow one-hour consolidation.",
            max_hold_seconds=28800,
            conditions=(
                _condition("consolidation_range_1h", "lte", "0.10"),
                _condition("breakout_retest_confirmed", "eq", "1"),
                _condition("log_return_15m", "gte", "0"),
                _condition("liquidity_change_1h", "gte", "0"),
            ),
        ),
        _spec(
            "H6", "Evidenced renewed activity in a token whose verified creation age exceeds 24 hours.",
            reasons=(DiscoveryReason.RENEWED_ACTIVITY,), max_hold_seconds=86400,
            conditions=(
                _condition("token_age_seconds", "gt", "86400"),
                _condition("volume_acceleration", "gte", "1.5"),
                _condition("log_return_1h", "gt", "0"),
                _condition("distinct_buyers_1h", "gte", "100"),
            ),
        ),
        _spec(
            "H7", "Momentum-exhaustion overlay; both volume-decay hypotheses remain separately observable.",
            extra_features=("volume_decay_orig", "volume_decay_adj"),
            rejections=(
                _condition("volume_decay_adj", "lt", "0.5"),
                _condition("log_return_5m", "lt", "-0.10"),
            ),
            signal_score=_score("zero"),
        ),
        _spec(
            "R1", "Latency-sensitive short volume acceleration, SHADOW research only.",
            max_hold_seconds=3600,
            conditions=(
                _condition("volume_acceleration", "gte", "1.5"),
                _condition("log_return_5m", "gt", "0"),
                _condition("distinct_buyers_1h", "gte", "100"),
            ),
            signal_score=_score("feature", feature="volume_acceleration", offset="-1.5"),
        ),
    )


PAPER_STRATEGIES = ("B1", "B2", "H1", "H2", "H3", "H4", "H5", "H6")


def paper_specs() -> tuple[StrategySpec, ...]:
    """Version 2 of each tradeable spec, identical except that PAPER is allowed (operator decision 2026-10-19).

    B0 and the H7 overlay keep version 1 (they never open positions); R1 stays SHADOW research only.
    """
    out = []
    for spec in builtin_specs():
        if spec.strategy_id in PAPER_STRATEGIES:
            out.append(revise_spec(spec, version=2, allowed_modes=("SHADOW", "PAPER")))
        elif spec.strategy_id in ("B0", "H7"):
            out.append(spec)
    return tuple(out)


def revise_spec(spec: StrategySpec, *, version: int, **changes: Any) -> StrategySpec:
    """A threshold change is a new child version; the supplied parent is untouched."""
    if version <= spec.version:
        raise ValueError("a revision must increase the strategy version")
    if {"version", "parent_version", "strategy_id"}.intersection(changes):
        raise ValueError("a revision preserves strategy identity and sets its own parent")
    values = spec.model_dump(mode="python")
    values.update(changes)
    values.update(version=version, parent_version=spec.version_id)
    return StrategySpec.model_validate(values)
