"""Versioned tercile classifier. Cold starts return missing, never invented NEUTRAL history."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from itertools import pairwise
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from desk.contracts.features import FeatureSnapshot, RegimeLabel, RegimeResult
from desk.contracts.observed import DataStatus, UtcDatetime
from desk.core.hashing import canonical_json, sha256_hex

_INPUTS = ("sol_return_1h", "sol_volatility_1h", "watchlist_volume_usd_1h", "migrations_per_hour", "hazard_rate_24h")
_DEFINITION = {
    "reference_days": 30, "maximum_reference_gap_hours": 24,
    "quantiles": "nearest_rank_terciles",
    "activity": "high only when volume and migrations terciles both high; low when both low; otherwise middle",
    "risk_off": "hazard high OR trend low and activity low",
    "choppy": "volatility high and trend not high",
    "risk_on": "trend high and activity high and hazard below high",
    "otherwise": "neutral", "order": ["risk_off", "choppy", "risk_on", "neutral"],
}
REGIME_VERSION = "regime-v1-" + sha256_hex(canonical_json(_DEFINITION))[:16]


class ContextObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    observed_at: UtcDatetime
    sol_return_1h: Decimal
    sol_volatility_1h: Decimal = Field(ge=0)
    watchlist_volume_usd_1h: Decimal = Field(ge=0)
    migrations_per_hour: Decimal = Field(ge=0)
    hazard_rate_24h: Decimal = Field(ge=0, le=1)


class RegimeReference(BaseModel):
    """A full thirty days of observed context, with no holes longer than a day.

    Explicit observations make a hand-supplied zero-day/constant threshold masquerading as a calibrated
    thirty-day history invalid. Constant *actually observed* references remain valid, but equality is middle.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    as_of: UtcDatetime
    observations: tuple[ContextObservation, ...] = Field(min_length=31)

    @model_validator(mode="after")
    def _coverage(self) -> Self:
        points = self.observations
        times = [point.observed_at for point in points]
        if times != sorted(set(times)):
            raise ValueError("reference observations must be strictly chronological and unique")
        if times[-1] > self.as_of or times[0] != self.as_of - timedelta(days=30):
            raise ValueError("reference must begin exactly thirty days before as_of and contain no future observation")
        if self.as_of - times[-1] > timedelta(days=1):
            raise ValueError("reference does not cover the end of its thirty-day window")
        if any(b - a > timedelta(days=1) for a, b in pairwise(times)):
            raise ValueError("reference has a gap longer than one day")
        return self

    @property
    def reference_hash(self) -> str:
        return sha256_hex(self.model_dump_json())


def _tercile(value: Decimal, observations: list[Decimal]) -> int:
    ordered = sorted(observations)
    n = len(ordered)
    low, high = ordered[(n + 2) // 3 - 1], ordered[(2 * n + 2) // 3 - 1]
    # Equal-valued distributions convey no low/high evidence.
    if value < low:
        return 0
    if value > high:
        return 2
    return 1


def classify_regime(snapshot: FeatureSnapshot, reference: RegimeReference | None = None) -> RegimeResult:
    if reference is None:
        return RegimeResult(version=REGIME_VERSION, label=None, status=DataStatus.MISSING,
                            reasons=("INSUFFICIENT_HISTORY: a measured trailing thirty-day context reference is required",))
    if reference.as_of > snapshot.as_of:
        return RegimeResult(version=REGIME_VERSION, label=None, status=DataStatus.MISSING,
                            reasons=("FUTURE_REFERENCE: calibration was not known at this snapshot",))
    if snapshot.as_of - reference.as_of > timedelta(days=1):
        return RegimeResult(version=REGIME_VERSION, label=None, status=DataStatus.STALE,
                            reasons=("STALE_REFERENCE: calibration is older than one day",))
    usable: dict[str, Decimal] = {}
    for name in _INPUTS:
        value = snapshot.values.get(name)
        if (value is None or snapshot.usable(name) is None or value.observed_at is None
                or not 0 <= (snapshot.as_of - value.observed_at).total_seconds() <= 120):
            return RegimeResult(version=REGIME_VERSION, label=None, status=DataStatus.MISSING,
                                reasons=("INSUFFICIENT_CONTEXT:" + name,))
        assert value.value is not None
        usable[name] = value.value

    try:
        ContextObservation(observed_at=snapshot.as_of, **usable)
    except ValidationError:
        return RegimeResult(version=REGIME_VERSION, label=None, status=DataStatus.CONFLICT,
                            reasons=("INVALID_CONTEXT: rates, volatility and counts must satisfy their measurement bounds",))

    terciles = {name: _tercile(usable[name], [getattr(point, name) for point in reference.observations])
                for name in _INPUTS}
    trend, volatility, hazard = terciles["sol_return_1h"], terciles["sol_volatility_1h"], terciles["hazard_rate_24h"]
    volume, migrations = terciles["watchlist_volume_usd_1h"], terciles["migrations_per_hour"]
    activity = 2 if volume == migrations == 2 else 0 if volume == migrations == 0 else 1
    if hazard == 2 or trend == activity == 0:
        label = RegimeLabel.RISK_OFF
    elif volatility == 2 and trend != 2:
        label = RegimeLabel.CHOPPY
    elif trend == activity == 2:
        label = RegimeLabel.RISK_ON
    else:
        label = RegimeLabel.NEUTRAL
    return RegimeResult(version=REGIME_VERSION, label=label, status=DataStatus.OK,
                        reasons=(f"trend_tercile={trend}; volatility_tercile={volatility}; "
                                 f"activity_tercile={activity}; hazard_tercile={hazard}",
                                 "reference_sha256=" + reference.reference_hash))
