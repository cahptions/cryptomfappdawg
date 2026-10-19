"""Discovery labels describe observed evidence, not an inferred token creation time."""

from __future__ import annotations

from decimal import Decimal

from desk.contracts.discovery import DiscoveryReason


def classify(*, known_token: bool, known_pool: bool, migration_evidence: bool = False,
             renewed: bool = False, volume_ratio: Decimal | None = None,
             acceleration_ratio: Decimal | None = None, expansion_threshold: Decimal | None = None,
             acceleration_threshold: Decimal | None = None) -> DiscoveryReason | None:
    if not known_token:
        return DiscoveryReason.NEW_TOKEN
    if not known_pool:
        return DiscoveryReason.MIGRATION if migration_evidence else DiscoveryReason.NEW_POOL
    if renewed:
        return DiscoveryReason.RENEWED_ACTIVITY
    if volume_ratio is not None and expansion_threshold is None:
        raise ValueError("volume expansion threshold must come from configuration")
    if acceleration_ratio is not None and acceleration_threshold is None:
        raise ValueError("acceleration threshold must come from configuration")
    if volume_ratio is not None and expansion_threshold is not None and volume_ratio >= expansion_threshold:
        return DiscoveryReason.VOLUME_EXPANSION
    if acceleration_ratio is not None and acceleration_threshold is not None and acceleration_ratio >= acceleration_threshold:
        return DiscoveryReason.ACCELERATION
    return None
