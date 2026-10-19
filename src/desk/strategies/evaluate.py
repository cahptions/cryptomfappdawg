"""Deterministic pure SHADOW ranking. No clock reads, random state or I/O.

H7 is evaluated before trade hypotheses and can only remove eligibility. Scores
are versioned research priorities and never represent profitability or sizing.
"""

from __future__ import annotations

from decimal import Decimal, localcontext

from desk.contracts.discovery import Outcome
from desk.contracts.observed import DataStatus
from desk.contracts.strategy import Decision, DecisionContext, FeatureCondition, RankedStrategy, StrategyKind, StrategySpec
from desk.core.hashing import fingerprint
from desk.strategies.specs import builtin_specs

EVALUATOR_VERSION = "shadow-rules-v3"
# v3 (2026-10-19): an eligible hypothesis outranks an eligible baseline for the same candidate, whatever their
# score scales; baselines take only candidates no hypothesis wants. NO_TRADE still wins ties and beats any
# non-positive score.
# v2 (operator decision 2026-10-19): research and PAPER decisions do not wait for a calibrated regime; an
# uncalibrated regime is recorded and only a calibrated label can exclude a strategy. LIVE (M8) separately
# requires a calibrated regime. PLAN §13 catastrophic-market halt applies regardless of calibration.
# v1: every strategy needed a calibrated, eligible regime; no market-crash rule.
# Replay reproduces a recorded run with the version that recorded it, so every version stays available.
EVALUATORS = ("shadow-rules-v1", "shadow-rules-v2", EVALUATOR_VERSION)
SOL_CRASH_LOG_RETURN_1H = Decimal("0.92").ln()   # SOL -8% in one hour


def _matches(value: Decimal, condition: FeatureCondition) -> bool:
    match condition.operator:
        case "gte":
            return value >= condition.threshold
        case "gt":
            return value > condition.threshold
        case "lte":
            return value <= condition.threshold
        case "lt":
            return value < condition.threshold
        case "eq":
            return value == condition.threshold


def _global_reasons(context: DecisionContext, version: str) -> tuple[str, ...]:
    reasons: list[str] = []
    if context.security_outcome is not Outcome.PASS:
        reasons.append(f"SECURITY_{context.security_outcome.value}")
    if context.security_assessed_at is None:
        reasons.append("SECURITY_TIMESTAMP_MISSING")
    else:
        age = (context.features.as_of - context.security_assessed_at).total_seconds()
        if age < 0:
            reasons.append("SECURITY_OBSERVED_AFTER_DECISION")
        elif age > context.security_max_age_seconds:
            reasons.append("SECURITY_STALE")
    if version == "shadow-rules-v1":
        if context.regime_status is not DataStatus.OK or context.regime_label is None:
            reasons.append("REGIME_INSUFFICIENT_OR_UNVERIFIED")
        return tuple(reasons)
    sol = context.features.usable("sol_return_1h")
    if sol is not None and sol <= SOL_CRASH_LOG_RETURN_1H:
        reasons.append("MARKET_SOL_CRASH_1H")
    return tuple(reasons)


def _spec_reasons(spec: StrategySpec, context: DecisionContext, version: str) -> tuple[str, ...]:
    reasons: list[str] = []
    eligibility = spec.eligibility
    calibrated = context.regime_status is DataStatus.OK and context.regime_label is not None
    if (calibrated or version == "shadow-rules-v1") and context.regime_label not in eligibility.regimes:
        reasons.append("REGIME_NOT_ELIGIBLE")
    if eligibility.discovery_reasons and context.discovery_reason not in eligibility.discovery_reasons:
        reasons.append("DISCOVERY_REASON_NOT_ELIGIBLE")
    if eligibility.min_discovery_age_seconds is not None or eligibility.max_discovery_age_seconds is not None:
        # H1 means time since graduation occurred, not time since a scanner first
        # noticed an already graduated pool. Missing occurrence stays missing.
        event_at = context.migration_occurred_at if spec.strategy_id == "H1" else context.discovery_observed_at
        event_name = "MIGRATION_OCCURRENCE" if spec.strategy_id == "H1" else "DISCOVERY_EVENT"
        if event_at is None:
            reasons.append(f"{event_name}_TIMESTAMP_MISSING")
        else:
            discovery_age = (context.features.as_of - event_at).total_seconds()
            if discovery_age < 0:
                reasons.append(f"{event_name}_AFTER_DECISION")
            elif eligibility.min_discovery_age_seconds is not None and discovery_age < eligibility.min_discovery_age_seconds:
                reasons.append(f"{event_name}_TOO_RECENT")
            elif eligibility.max_discovery_age_seconds is not None and discovery_age > eligibility.max_discovery_age_seconds:
                reasons.append(f"{event_name}_TOO_OLD")

    usable: dict[str, Decimal] = {}
    for requirement in spec.features:
        feature = context.features.values.get(requirement.name)
        if feature is None or feature.value is None:
            reasons.append(f"FEATURE_MISSING:{requirement.name}")
            continue
        if feature.status is not DataStatus.OK:
            reasons.append(f"FEATURE_{feature.status.value.upper()}:{requirement.name}")
            continue
        if feature.observed_at is None:
            reasons.append(f"FEATURE_TIMESTAMP_MISSING:{requirement.name}")
            continue
        age = (context.features.as_of - feature.observed_at).total_seconds()
        max_age = requirement.max_age_seconds
        if requirement.name == "roundtrip_cost_fraction":
            max_age = min(max_age, spec.execution_requirements.max_quote_age_seconds)
        if age < 0:
            reasons.append(f"FEATURE_OBSERVED_AFTER_DECISION:{requirement.name}")
        elif age > max_age:
            reasons.append(f"FEATURE_STALE:{requirement.name}")
        elif not feature.value.is_finite():
            reasons.append(f"FEATURE_NONFINITE:{requirement.name}")
        else:
            usable[requirement.name] = feature.value

    for condition in spec.entry_conditions:
        value = usable.get(condition.feature)
        if value is not None and not _matches(value, condition):
            reasons.append(f"ENTRY_CONDITION_FALSE:{condition.feature}:{condition.operator}:{condition.threshold}")
    for condition in spec.rejection_conditions:
        value = usable.get(condition.feature)
        if value is not None and _matches(value, condition):
            reasons.append(f"REJECTION_CONDITION_TRUE:{condition.feature}:{condition.operator}:{condition.threshold}")
    liquidity = usable.get("verified_liquidity_usd")
    if liquidity is not None and liquidity < spec.liquidity_exits.minimum_verified_liquidity_usd:
        reasons.append("VERIFIED_LIQUIDITY_BELOW_MINIMUM")
    cost = usable.get("roundtrip_cost_fraction")
    if cost is not None and (cost < 0 or cost > spec.execution_requirements.max_roundtrip_cost_fraction):
        reasons.append("ROUNDTRIP_COST_OUTSIDE_LIMIT")
    return tuple(reasons)


def _signal_score(spec: StrategySpec, context: DecisionContext, candidate_id: str, seed: int) -> Decimal:
    policy = spec.signal_score
    with localcontext() as decimal_context:
        decimal_context.prec = 50
        if policy.method == "zero":
            return Decimal(0)
        if policy.method == "constant":
            return policy.offset
        if policy.method == "seeded_priority":
            # Stable random priority, comparable across the eligible cohort. It
            # never consumes global RNG state or pretends to estimate return.
            digest = fingerprint({
                "seed": seed,
                "candidate_id": candidate_id,
                "mint": context.features.mint,
                "as_of": context.features.as_of,
                "spec_hash": spec.spec_hash,
            })
            return Decimal(int(digest, 16) % 1_000_000) / Decimal(1_000_000)
        assert policy.feature is not None
        value = context.features.usable(policy.feature)
        assert value is not None  # prerequisite validation completed before scoring
        return value * policy.multiplier + policy.offset


def evaluate_decision(
    candidate_id: str,
    context: DecisionContext,
    specs: tuple[StrategySpec, ...],
    *,
    seed: int = 0,
    evaluator_version: str = EVALUATOR_VERSION,
) -> Decision:
    """Evaluate one candidate at ``as_of``; always include the cash baseline.

    A baseline's seeded priority is useful for comparing selection across a
    cohort. Every row is SHADOW evidence; chosen never submits an order.
    ``evaluator_version`` exists for replay: new decisions always use the current version.
    """
    if evaluator_version not in EVALUATORS:
        raise ValueError(f"unknown evaluator version {evaluator_version!r}")
    strategy_ids = [spec.strategy_id for spec in specs]
    if len(strategy_ids) != len(set(strategy_ids)):
        raise ValueError("evaluate exactly one immutable version per strategy id")
    if "B0" not in strategy_ids:
        specs = (*specs, builtin_specs()[0])
    specs = tuple(sorted(specs, key=lambda spec: spec.strategy_id))
    global_reasons = _global_reasons(context, evaluator_version)
    overlay_reasons = tuple(
        f"OVERLAY_{spec.strategy_id}:{reason}"
        for spec in specs if spec.kind is StrategyKind.OVERLAY
        for reason in _spec_reasons(spec, context, evaluator_version)
    )
    ranking: list[RankedStrategy] = []
    for spec in specs:
        if spec.strategy_id == "B0":
            ranking.append(RankedStrategy(
                strategy_id="NO_TRADE", version=spec.version, spec_hash=spec.spec_hash,
                eligible=True, score=Decimal(0), reasons=("CASH_BASELINE_NO_ORDERS",),
            ))
            continue
        if spec.kind is StrategyKind.OVERLAY:
            reasons = (*global_reasons, *_spec_reasons(spec, context, evaluator_version), "OVERLAY_ONLY_NEVER_STANDALONE")
            ranking.append(RankedStrategy(
                strategy_id=spec.strategy_id, version=spec.version, spec_hash=spec.spec_hash,
                eligible=False, score=Decimal(0), reasons=reasons,
            ))
            continue
        reasons = (*global_reasons, *overlay_reasons, *_spec_reasons(spec, context, evaluator_version))
        eligible = not reasons
        ranking.append(RankedStrategy(
            strategy_id=spec.strategy_id, version=spec.version, spec_hash=spec.spec_hash,
            eligible=eligible, score=_signal_score(spec, context, candidate_id, seed) if eligible else Decimal(0),
            reasons=reasons if reasons else ("UNVALIDATED_RESEARCH_SIGNAL",),
        ))
    kinds = {spec.strategy_id: spec.kind for spec in specs}

    def tier(row: RankedStrategy) -> int:
        if evaluator_version != "shadow-rules-v3" or not row.eligible or row.score <= 0 or row.strategy_id == "NO_TRADE":
            return 0
        return 2 if kinds.get(row.strategy_id) is StrategyKind.HYPOTHESIS else 1

    ranking.sort(key=lambda row: (-tier(row), -row.score, 0 if row.strategy_id == "NO_TRADE" else 1, row.strategy_id))
    ranked = tuple(ranking)
    inputs_hash = fingerprint({"candidate_id": candidate_id, "context": context.model_dump(mode="python")})
    config_hash = fingerprint({
        "evaluator_version": evaluator_version,
        "specs": [spec.model_dump(mode="python") for spec in specs],
        "seed": seed,
        "security_max_age_seconds": context.security_max_age_seconds,
        "regime_version": context.regime_version,
    })
    payload = {
        "candidate_id": candidate_id,
        "as_of": context.features.as_of,
        "mode": "SHADOW",
        "inputs_hash": inputs_hash,
        "config_hash": config_hash,
        "regime_version": context.regime_version,
        "ranking": [row.model_dump(mode="python") for row in ranked],
        "chosen": ranked[0].strategy_id,
    }
    return Decision.model_validate({**payload, "decision_hash": fingerprint(payload)})
