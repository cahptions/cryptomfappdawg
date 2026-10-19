from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from desk.contracts.discovery import DiscoveryReason, Outcome
from desk.contracts.features import FeatureSnapshot, FeatureValue
from desk.contracts.observed import DataStatus
from desk.contracts.strategy import DecisionContext, FeatureCondition, SignalScorePolicy, StrategySpec
from desk.strategies import builtin_specs, evaluate_decision, revise_spec

NOW = datetime(2026, 10, 19, 6, tzinfo=UTC)
MINT = "So11111111111111111111111111111111111111112"
POOL = "11111111111111111111111111111111"


def _feature(value: str, *, status: DataStatus = DataStatus.OK, age: int = 0) -> FeatureValue:
    return FeatureValue(
        value=Decimal(value), status=status, unit="fixture_unit", source="test.fixture",
        observed_at=NOW - timedelta(seconds=age), age_seconds=float(age),
    )


def _context(**changes: object) -> DecisionContext:
    values = {
        "verified_liquidity_usd": _feature("100000"),
        "roundtrip_cost_fraction": _feature("0.02"),
        "log_return_5m": _feature("0.01"),
        "log_return_15m": _feature("0.05"),
        "log_return_1h": _feature("0.20"),
        "volume_acceleration": _feature("2"),
        "distinct_buyers_1h": _feature("150"),
        "organic_volume_share": _feature("0.7"),
        "liquidity_change_1h": _feature("0.2"),
        "distance_from_high_6h": _feature("0"),
        "consolidation_range_1h": _feature("0.05"),
        "token_age_seconds": _feature("90000"),
        "volume_decay_orig": _feature("1"),
        "volume_decay_adj": _feature("1"),
        "initial_spike_return": _feature("0.5"),
        "spike_retracement_fraction": _feature("0.3"),
        "breakout_retest_confirmed": _feature("1"),
    }
    data: dict[str, object] = {
        "features": FeatureSnapshot(
            mint=MINT, pool_address=POOL, as_of=NOW, feature_set_version="fixture-v1", values=values,
        ),
        "security_outcome": Outcome.PASS,
        "security_assessed_at": NOW,
        "discovery_reason": DiscoveryReason.MIGRATION,
        "discovery_observed_at": NOW - timedelta(seconds=1200),
        "migration_occurred_at": NOW - timedelta(seconds=1200),
        "regime_label": "RISK_ON",
        "regime_status": DataStatus.OK,
        "regime_version": "fixture-measured-regime-v1",
    }
    data.update(changes)
    return DecisionContext.model_validate(data)


def _replace_feature(context: DecisionContext, name: str, feature: FeatureValue) -> DecisionContext:
    values = {**context.features.values, name: feature}
    snapshot = FeatureSnapshot.model_validate({**context.features.model_dump(mode="python"), "values": values})
    return DecisionContext.model_validate({**context.model_dump(mode="python"), "features": snapshot})


@pytest.mark.parametrize("section", [
    "eligibility", "features", "entry_conditions", "rejection_conditions", "sizing", "initial_stop", "trailing",
    "max_hold_seconds", "liquidity_exits", "execution_requirements", "invalidation", "signal_score",
])
def test_each_required_strategy_section_is_validated(section: str) -> None:
    data = builtin_specs()[3].model_dump(mode="python")
    del data[section]
    with pytest.raises(ValidationError):
        StrategySpec.model_validate(data)


def test_specs_are_deeply_frozen_and_revision_records_parent() -> None:
    initial = builtin_specs()[3]
    original_hash = initial.spec_hash
    with pytest.raises(ValidationError, match="frozen"):
        initial.version = 2
    with pytest.raises(ValidationError, match="frozen"):
        initial.entry_conditions[0].threshold = Decimal(1)
    revision = revise_spec(
        initial, version=2,
        entry_conditions=(FeatureCondition(feature="distinct_buyers_1h", operator="gte", threshold=Decimal(200)),),
    )
    assert initial.spec_hash == original_hash
    assert revision.spec_hash != original_hash
    assert revision.parent_version == initial.version_id
    with pytest.raises(ValueError, match="increase"):
        revise_spec(initial, version=1)
    changed = initial.model_dump(mode="python")
    changed["version"] = 2
    with pytest.raises(ValidationError, match="parent_version"):
        StrategySpec.model_validate(changed)


def test_no_trade_exists_with_no_specs_and_security_failure() -> None:
    context = _context(security_outcome=Outcome.REJECT)
    decision = evaluate_decision("candidate-1", context, ())
    assert decision.chosen == "NO_TRADE"
    assert len(decision.ranking) == 1
    assert decision.ranking[0].score == 0


def test_no_trade_wins_zero_signal_ties_and_b0_never_trades() -> None:
    context = _replace_feature(_context(), "log_return_1h", _feature("0"))
    b0, _, b2, *_ = builtin_specs()
    decision = evaluate_decision("candidate-1", context, (b0, b2))
    assert decision.chosen == "NO_TRADE"
    assert decision.ranking[0].strategy_id == "NO_TRADE"
    assert evaluate_decision("candidate-1", _context(), (b0,)).chosen == "NO_TRADE"


@pytest.mark.parametrize("context", [
    _context(security_outcome=Outcome.REJECT),
    _context(security_outcome=Outcome.DEFER),
    _context(security_assessed_at=None),
    _context(security_assessed_at=NOW - timedelta(seconds=301)),
    _context(security_assessed_at=NOW + timedelta(seconds=1)),
    _replace_feature(_context(), "sol_return_1h", _feature("-0.0834")),
    _replace_feature(_context(regime_status=DataStatus.MISSING, regime_label=None), "sol_return_1h", _feature("-0.2")),
    _context(regime_label="RISK_OFF"),
])
def test_higher_scores_never_override_security_market_crash_or_excluded_regime(context: DecisionContext) -> None:
    decision = evaluate_decision("candidate-1", context, builtin_specs())
    assert decision.chosen == "NO_TRADE"
    assert all(row.score == 0 for row in decision.ranking)
    assert all(not row.eligible for row in decision.ranking if row.strategy_id != "NO_TRADE")


def test_uncalibrated_regime_does_not_block_research_decisions() -> None:
    """Operator decision 2026-10-19: PAPER/research learning starts before 30 days of context exist."""
    warmup = _context(regime_status=DataStatus.MISSING, regime_label=None)
    decision = evaluate_decision("candidate-1", warmup, builtin_specs())
    assert decision.chosen != "NO_TRADE"
    assert all("REGIME" not in reason for row in decision.ranking for reason in row.reasons)
    assert evaluate_decision("candidate-1", warmup, builtin_specs()) == decision
    # a calibrated label still excludes; an uncalibrated SOL move just above -8% does not halt
    assert evaluate_decision("candidate-1", _context(regime_label="RISK_OFF"), builtin_specs()).chosen == "NO_TRADE"
    near = _replace_feature(warmup, "sol_return_1h", _feature("-0.0833"))
    assert evaluate_decision("candidate-1", near, builtin_specs()).chosen == decision.chosen


@pytest.mark.parametrize("status", [DataStatus.UNVERIFIED, DataStatus.CONFLICT, DataStatus.ESTIMATED, DataStatus.STALE])
def test_required_claims_and_estimates_cannot_pass(status: DataStatus) -> None:
    context = _replace_feature(_context(), "verified_liquidity_usd", _feature("1000000", status=status))
    decision = evaluate_decision("candidate-1", context, builtin_specs())
    assert decision.chosen == "NO_TRADE"


def test_quote_freshness_uses_timestamp_and_exact_boundary() -> None:
    b2 = builtin_specs()[2]
    context = _replace_feature(_context(), "roundtrip_cost_fraction", _feature("0.02", age=10))
    assert evaluate_decision("candidate-1", context, (b2,)).chosen == "B2"
    context = _replace_feature(context, "roundtrip_cost_fraction", _feature("0.02", age=11))
    decision = evaluate_decision("candidate-1", context, (b2,))
    assert decision.chosen == "NO_TRADE"
    assert "FEATURE_STALE:roundtrip_cost_fraction" in decision.ranking[1].reasons


def test_b1_is_seeded_and_does_not_consume_global_random_state() -> None:
    baseline = builtin_specs()[1]
    state = random.getstate()
    first = evaluate_decision("candidate-1", _context(), (baseline,), seed=7)
    second = evaluate_decision("candidate-1", _context(), (baseline,), seed=7)
    changed = evaluate_decision("candidate-1", _context(), (baseline,), seed=8)
    assert first.model_dump_json() == second.model_dump_json()
    assert first.decision_hash != changed.decision_hash
    assert first.ranking[0].score != changed.ranking[0].score
    assert state == random.getstate()


def test_b2_highest_one_hour_momentum_priority_is_deterministic() -> None:
    b2 = builtin_specs()[2]
    low = _replace_feature(_context(), "log_return_1h", _feature("0.1"))
    high = _replace_feature(_context(), "log_return_1h", _feature("0.3"))
    low_decision = evaluate_decision("candidate-low", low, (b2,))
    high_decision = evaluate_decision("candidate-high", high, (b2,))
    assert high_decision.ranking[0].score > low_decision.ranking[0].score > 0
    assert high_decision.chosen == low_decision.chosen == "B2"


def test_migration_requires_explicit_reason_and_verified_occurrence_timestamp() -> None:
    h1 = builtin_specs()[3]
    assert evaluate_decision("candidate-1", _context(), (h1,)).chosen == "H1"
    for context in (
        _context(discovery_reason=DiscoveryReason.NEW_POOL),
        _context(migration_occurred_at=None),
        _context(migration_occurred_at=NOW - timedelta(seconds=599)),
        _context(migration_occurred_at=NOW - timedelta(seconds=3601)),
        _context(migration_occurred_at=NOW + timedelta(seconds=1)),
    ):
        assert evaluate_decision("candidate-1", context, (h1,)).chosen == "NO_TRADE"
    for age in (600, 3600):
        context = _context(migration_occurred_at=NOW - timedelta(seconds=age))
        assert evaluate_decision("candidate-1", context, (h1,)).chosen == "H1"


def test_first_seen_migration_does_not_claim_graduation_occurred_then() -> None:
    h1 = builtin_specs()[3]
    first_seen_only = _context(
        discovery_observed_at=NOW - timedelta(seconds=1200), migration_occurred_at=None,
    )
    decision = evaluate_decision("candidate-1", first_seen_only, (h1,))
    assert decision.chosen == "NO_TRADE"
    assert "MIGRATION_OCCURRENCE_TIMESTAMP_MISSING" in decision.ranking[1].reasons
    # Knowing the actual migration occurrence does not require a first-seen time.
    assert evaluate_decision("candidate-1", _context(discovery_observed_at=None), (h1,)).chosen == "H1"


def test_missing_spike_and_retest_chronology_does_not_invent_a_signal() -> None:
    context = _context()
    data = context.features.model_dump(mode="python")
    for name in ("initial_spike_return", "spike_retracement_fraction", "breakout_retest_confirmed"):
        del data["values"][name]
    context = _context(features=FeatureSnapshot.model_validate(data))
    h2, h5 = builtin_specs()[4], builtin_specs()[7]
    decision = evaluate_decision("candidate-1", context, (h2, h5))
    assert decision.chosen == "NO_TRADE"
    assert any("FEATURE_MISSING:breakout_retest_confirmed" in row.reasons for row in decision.ranking)


@pytest.mark.parametrize("strategy_id", ["H1", "H2", "H3", "H4", "H5", "H6", "R1"])
def test_each_trade_hypothesis_can_emit_only_a_shadow_signal_with_complete_evidence(strategy_id: str) -> None:
    context = _context()
    if strategy_id == "H2":
        context = _replace_feature(context, "volume_acceleration", _feature("1"))
    elif strategy_id == "H4":
        context = _context(discovery_reason=DiscoveryReason.VOLUME_EXPANSION)
        context = _replace_feature(context, "distance_from_high_6h", _feature("-0.20"))
    elif strategy_id == "H6":
        context = _context(discovery_reason=DiscoveryReason.RENEWED_ACTIVITY)
    spec = next(spec for spec in builtin_specs() if spec.strategy_id == strategy_id)
    decision = evaluate_decision("candidate-1", context, (spec,))
    assert decision.chosen == strategy_id
    assert decision.mode == "SHADOW"
    assert decision.ranking[0].score_kind == "heuristic_signal"
    assert decision.ranking[0].reasons == ("UNVALIDATED_RESEARCH_SIGNAL",)


def test_renewal_requires_verified_age_and_explicit_renewal_event() -> None:
    h6 = builtin_specs()[8]
    context = _context(discovery_reason=DiscoveryReason.RENEWED_ACTIVITY)
    for feature in (_feature("86400"), _feature("90000", status=DataStatus.UNVERIFIED)):
        blocked = _replace_feature(context, "token_age_seconds", feature)
        assert evaluate_decision("candidate-1", blocked, (h6,)).chosen == "NO_TRADE"
    assert evaluate_decision("candidate-1", _context(), (h6,)).chosen == "NO_TRADE"


def test_overlay_removes_signals_but_never_becomes_standalone() -> None:
    b2, h7 = builtin_specs()[2], builtin_specs()[9]
    healthy = evaluate_decision("candidate-1", _context(), (b2, h7))
    assert healthy.chosen == "B2"
    context = _replace_feature(_context(), "volume_decay_adj", _feature("0.49"))
    exhausted = evaluate_decision("candidate-1", context, (b2, h7))
    assert exhausted.chosen == "NO_TRADE"
    assert any("OVERLAY_H7:REJECTION_CONDITION_TRUE" in reason for row in exhausted.ranking for reason in row.reasons)
    assert evaluate_decision("candidate-1", _context(), (h7,)).chosen == "NO_TRADE"


def test_r1_rejects_paper_mode_and_records_shadow_only() -> None:
    r1 = builtin_specs()[-1]
    data = r1.model_dump(mode="python")
    data["allowed_modes"] = ("SHADOW", "PAPER")
    with pytest.raises(ValidationError, match="SHADOW research only"):
        StrategySpec.model_validate(data)
    decision = evaluate_decision("candidate-1", _context(), (r1,))
    assert decision.mode == "SHADOW"
    assert decision.chosen == "R1"


def test_threshold_change_affects_only_expected_hypothesis() -> None:
    b2, h3 = builtin_specs()[2], builtin_specs()[5]
    context = _context()
    original = evaluate_decision("candidate-1", context, (b2, h3))
    changed = revise_spec(h3, version=2, entry_conditions=(
        FeatureCondition(feature="log_return_1h", operator="gt", threshold=Decimal("0.5")),
    ))
    copied = evaluate_decision("candidate-1", context, (b2, changed))
    assert original.inputs_hash == copied.inputs_hash
    assert original.config_hash != copied.config_hash
    original_b2 = next(row for row in original.ranking if row.strategy_id == "B2")
    copied_b2 = next(row for row in copied.ranking if row.strategy_id == "B2")
    assert original_b2 == copied_b2
    assert not next(row for row in copied.ranking if row.strategy_id == "H3").eligible
    assert h3.version == 1


def test_non_cash_spec_cannot_remove_execution_evidence_requirements() -> None:
    data = builtin_specs()[2].model_dump(mode="python")
    data["features"] = [{"name": "log_return_1h", "max_age_seconds": 120}]
    with pytest.raises(ValidationError, match="fresh verified liquidity"):
        StrategySpec.model_validate(data)


def test_decisions_are_byte_identical_for_1000_seeded_randomized_inputs() -> None:
    rng = random.Random(326947)
    specs = (builtin_specs()[1], builtin_specs()[2], builtin_specs()[5])
    for index in range(1000):
        context = _replace_feature(_context(), "log_return_1h", _feature(str(rng.randint(-500, 500) / 1000)))
        context = _replace_feature(context, "roundtrip_cost_fraction", _feature(str(rng.randint(0, 40) / 1000)))
        seed = rng.randrange(1_000_000)
        first = evaluate_decision(f"candidate-{index}", context, specs, seed=seed)
        second = evaluate_decision(f"candidate-{index}", context, tuple(reversed(specs)), seed=seed)
        assert first.decision_hash == second.decision_hash
        assert first.model_dump_json() == second.model_dump_json()
        assert any(row.strategy_id == "NO_TRADE" for row in first.ranking)


def test_zero_priority_tie_goes_to_no_trade() -> None:
    baseline = revise_spec(
        builtin_specs()[1], version=2,
        signal_score=SignalScorePolicy(method="constant", feature=None, multiplier=Decimal(1), offset=Decimal(0)),
    )
    assert evaluate_decision("candidate-1", _context(), (baseline,)).chosen == "NO_TRADE"
