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
    stop: str = "0.10",
    trail: tuple[str, str] = ("0.15", "0.10"),
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
        initial_stop=StopPolicy(loss_fraction=Decimal("0") if no_trade else Decimal(stop)),
        trailing=TrailingPolicy(activation_gain_fraction=Decimal(trail[0]), drawdown_fraction=Decimal(trail[1])),
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
            "H8", "Moonshot continuation: an early explosive run (+50% in 1 h, still rising, volume x3, heavy buying, "
                  "liquidity growing with price, healthy holder distribution) continues long enough to bank a "
                  "multiple of the stop before the typical dump. Rare by design.",
            max_hold_seconds=7200, stop="0.15", trail=("0.25", "0.12"),
            conditions=(
                _condition("log_return_1h", "gte", "0.405"),            # ln(1.5): +50% in the last hour
                _condition("log_return_15m", "gte", "0.05"),            # still climbing
                _condition("log_return_5m", "gte", "0"),                # not reversing right now
                _condition("return_acceleration", "gte", "0"),          # momentum not fading
                _condition("volume_acceleration", "gte", "3"),          # 5 m volume >= 3x the hourly pace
                _condition("buy_transactions_1h", "gte", "400"),        # transactions, not distinct wallets
                _condition("buy_sell_ratio", "gte", "1.2"),
                _condition("liquidity_change_1h", "gte", "0.25"),       # real liquidity growing with the price
                _condition("top10_pct", "lte", "25"),                   # stricter than the safety gate (35)
                _condition("dev_holdings_pct", "lte", "2"),
            ),
            signal_score=_score("constant", offset="2"),               # outranks the other hypotheses
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


PAPER_STRATEGIES = ("B1", "B2", "H1", "H2", "H3", "H4", "H5", "H6", "H8")


def _paper_revision(spec: StrategySpec) -> StrategySpec:
    return revise_spec(spec, version=2, allowed_modes=("SHADOW", "PAPER"))


def _h8_v3(v2: StrategySpec) -> StrategySpec:
    """H8 v3 (2026-10-19): v2 required developer holdings <= 2%, a figure the desk never produces (no verified
    developer address reaches the holder analysis), so it could never qualify. v3 requires the largest single
    holder (verified top-20 accounts, pool vaults excluded) to hold <= 10% instead; nothing else changes."""
    names = sorted({f.name for f in v2.features} - {"dev_holdings_pct"} | {"top1_pct"})
    features = tuple(FeatureRequirement(name=name, max_age_seconds=10 if name == "roundtrip_cost_fraction" else 120)
                     for name in names)
    conditions = (*(c for c in v2.entry_conditions if c.feature != "dev_holdings_pct"),
                  _condition("top1_pct", "lte", "10"))
    return revise_spec(v2, version=3, features=features, entry_conditions=conditions)


def superseded_specs() -> tuple[StrategySpec, ...]:
    """Earlier PAPER versions a current version descends from; registered first so its lineage is verifiable."""
    return tuple(_paper_revision(spec) for spec in builtin_specs() if spec.strategy_id == "H8")


def paper_specs() -> tuple[StrategySpec, ...]:
    """The PAPER version of each tradeable spec (operator decision 2026-10-19): version 2, identical except that
    PAPER is allowed, and H8 version 3 (see ``_h8_v3``).

    B0 and the H7 overlay keep version 1 (they never open positions); R1 stays SHADOW research only.
    """
    out = []
    for spec in builtin_specs():
        if spec.strategy_id in PAPER_STRATEGIES:
            revised = _paper_revision(spec)
            out.append(_h8_v3(revised) if spec.strategy_id == "H8" else revised)
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
