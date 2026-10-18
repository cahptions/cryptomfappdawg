"""The ``Observed`` wrapper: a value plus where it came from, when, and how trustworthy it is.

Rule: ``value is None`` **if and only if** ``status is MISSING``. Reading a missing value with ``require()``
raises :class:`MissingDataError` — there is no code path that turns "unknown" into ``0``.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Generic, Self, TypeVar

from pydantic import AfterValidator, BaseModel, ConfigDict, model_validator

from desk.core.base58 import is_solana_address
from desk.core.clock import TimestampError, ensure_utc
from desk.core.errors import MissingDataError


def _utc(value: datetime) -> datetime:
    try:
        return ensure_utc(value)
    except TimestampError as exc:
        raise ValueError(str(exc)) from exc


def _address(value: str) -> str:
    if not is_solana_address(value):
        raise ValueError(f"not a valid Solana address: {value!r}")
    return value


UtcDatetime = Annotated[datetime, AfterValidator(_utc)]
SolAddress = Annotated[str, AfterValidator(_address)]


class DataStatus(StrEnum):
    OK = "ok"                  # observed from the named source
    MISSING = "missing"        # the source did not provide it (or the request failed)
    STALE = "stale"            # observed, but older than its permitted age
    CONFLICT = "conflict"      # sources disagree beyond tolerance
    UNVERIFIED = "unverified"  # a provider claim we cannot (yet) confirm on-chain
    ESTIMATED = "estimated"    # model-derived; never a real quote or observation


T = TypeVar("T")


class Observed(BaseModel, Generic[T]):
    model_config = ConfigDict(frozen=True)

    value: T | None
    status: DataStatus
    source: str
    observed_at: UtcDatetime
    provider_as_of: UtcDatetime | None = None
    flags: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _missing_iff_none(self) -> Self:
        if (self.value is None) != (self.status is DataStatus.MISSING):
            raise ValueError("value must be None exactly when status is MISSING")
        return self

    @property
    def is_missing(self) -> bool:
        return self.status is DataStatus.MISSING

    def require(self) -> T:
        if self.value is None:
            raise MissingDataError(f"required value from {self.source} is missing")
        return self.value
