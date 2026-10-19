"""Regime labels require actual trailing reference history and fresh measured context."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from desk.contracts.features import FeatureSnapshot, FeatureValue, RegimeLabel
from desk.contracts.observed import DataStatus
from desk.regime import REGIME_VERSION, ContextObservation, RegimeReference, classify_regime
from tests.unit.test_security import MINT, NOW, POOL


def reference() -> RegimeReference:
    return RegimeReference(as_of=NOW, observations=tuple(
        ContextObservation(observed_at=NOW - timedelta(days=30 - i), sol_return_1h=Decimal(i) / 1000,
                           sol_volatility_1h=Decimal(i + 1) / 1000, watchlist_volume_usd_1h=Decimal(100 + i),
                           migrations_per_hour=Decimal(1 + i), hazard_rate_24h=Decimal(i) / 1000)
        for i in range(31)))


def snapshot(**overrides: str) -> FeatureSnapshot:
    inputs = {"sol_return_1h": "0.015", "sol_volatility_1h": "0.015", "watchlist_volume_usd_1h": "115",
              "migrations_per_hour": "16", "hazard_rate_24h": "0.015"}
    inputs.update(overrides)
    values = {name: FeatureValue(value=Decimal(value), status=DataStatus.OK, unit="ratio", source="measured-context",
                                 observed_at=NOW, age_seconds=0) for name, value in inputs.items()}
    return FeatureSnapshot(mint=MINT, pool_address=POOL, as_of=NOW, feature_set_version="test-v1", values=values)


def test_cold_start_is_insufficient_history_not_neutral() -> None:
    result = classify_regime(snapshot())
    assert result.label is None and result.status is DataStatus.MISSING
    assert "INSUFFICIENT_HISTORY" in result.reasons[0]


@pytest.mark.parametrize("overrides,expected", [
    ({}, RegimeLabel.NEUTRAL),
    ({"sol_return_1h": "0.04", "watchlist_volume_usd_1h": "150", "migrations_per_hour": "50",
      "hazard_rate_24h": "0.005"}, RegimeLabel.RISK_ON),
    ({"hazard_rate_24h": "0.1"}, RegimeLabel.RISK_OFF),
    ({"sol_return_1h": "-0.1", "watchlist_volume_usd_1h": "0", "migrations_per_hour": "0"}, RegimeLabel.RISK_OFF),
    ({"sol_volatility_1h": "1"}, RegimeLabel.CHOPPY),
])
def test_reference_calibrated_labels_are_deterministic(overrides: dict[str, str], expected: RegimeLabel) -> None:
    ref, current = reference(), snapshot(**overrides)
    result = classify_regime(current, ref)
    assert result.label is expected and result.status is DataStatus.OK
    assert result.version == REGIME_VERSION
    assert all(classify_regime(current, ref).model_dump_json() == result.model_dump_json() for _ in range(100))
    assert ref.reference_hash in result.reasons[-1]


def test_short_fake_reference_is_rejected() -> None:
    ref = reference()
    with pytest.raises(ValidationError, match="31"):
        RegimeReference(as_of=NOW, observations=ref.observations[-7:])
    duplicated = (ref.observations[-1],) * 31
    with pytest.raises(ValidationError, match="unique"):
        RegimeReference(as_of=NOW, observations=duplicated)


def test_future_reference_is_not_used_for_past_decision() -> None:
    ref = reference()
    future_ref = RegimeReference(as_of=NOW + timedelta(days=1), observations=tuple(
        point.model_copy(update={"observed_at": point.observed_at + timedelta(days=1)}) for point in ref.observations))
    result = classify_regime(snapshot(), future_ref)
    assert result.label is None and "FUTURE_REFERENCE" in result.reasons[0]


def test_stale_reference_is_not_used() -> None:
    current = snapshot().model_copy(update={"as_of": NOW + timedelta(days=2)})
    result = classify_regime(current, reference())
    assert result.label is None and result.status is DataStatus.STALE


def test_missing_unverified_or_stale_context_prevents_a_label() -> None:
    current, ref = snapshot(), reference()
    for status in (DataStatus.UNVERIFIED, DataStatus.ESTIMATED, DataStatus.CONFLICT, DataStatus.STALE):
        values = {**current.values, "hazard_rate_24h": current.values["hazard_rate_24h"].model_copy(update={"status": status})}
        result = classify_regime(current.model_copy(update={"values": values}), ref)
        assert result.label is None and result.status is DataStatus.MISSING
    values = {name: value for name, value in current.values.items() if name != "hazard_rate_24h"}
    assert classify_regime(current.model_copy(update={"values": values}), ref).label is None
    stale_values = {**current.values, "sol_return_1h": current.values["sol_return_1h"].model_copy(
        update={"observed_at": NOW - timedelta(seconds=121), "age_seconds": 121})}
    assert classify_regime(current.model_copy(update={"values": stale_values}), ref).label is None


def test_constant_measured_reference_ties_are_middle_tercile() -> None:
    current = snapshot()
    observation = ContextObservation(observed_at=NOW, **{name: value.value for name, value in current.values.items()})
    ref = RegimeReference(as_of=NOW, observations=tuple(
        observation.model_copy(update={"observed_at": NOW - timedelta(days=30 - i)}) for i in range(31)))
    assert classify_regime(current, ref).label is RegimeLabel.NEUTRAL


def test_feature_snapshot_rejects_future_evidence() -> None:
    current = snapshot()
    values = {**current.values, "sol_return_1h": current.values["sol_return_1h"].model_copy(
        update={"observed_at": NOW + timedelta(microseconds=1)})}
    with pytest.raises(ValidationError, match="future"):
        FeatureSnapshot(mint=MINT, pool_address=POOL, as_of=NOW, feature_set_version="test-v1", values=values)


@pytest.mark.parametrize("overrides", [{"hazard_rate_24h": "-1"}, {"hazard_rate_24h": "2"},
                                     {"migrations_per_hour": "-1"}, {"sol_volatility_1h": "-1"}])
def test_invalid_current_measurement_bounds_cannot_produce_label(overrides: dict[str, str]) -> None:
    result = classify_regime(snapshot(**overrides), reference())
    assert result.status is DataStatus.CONFLICT and result.label is None
