"""Pure, inexpensive stages zero through two. No provider claims are silently upgraded to verified facts."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from desk.contracts.discovery import Outcome, StageResult
from desk.contracts.market import PoolSnapshot
from desk.contracts.observed import DataStatus
from desk.contracts.tokens import TokenClaims
from desk.core.config import LoadedConfig
from desk.security.policy import SOL_MINT, USDC_MINT


def stage_result(candidate_id: str, stage: int, outcome: Outcome, rule: str, observed: dict[str, Any],
                 thresholds: dict[str, Any], now: datetime, loaded: LoadedConfig) -> StageResult:
    return StageResult(candidate_id=candidate_id, stage=stage, outcome=outcome, rule_id=rule,
                       observed_values=observed, thresholds=thresholds, config_hash=loaded.snapshot_hash, decided_at=now)


def prefilter(candidate_id: str, snap: PoolSnapshot | None, claim: TokenClaims | None,
              now: datetime, loaded: LoadedConfig) -> list[StageResult]:
    s = loaded.settings.discovery
    results = [stage_result(candidate_id, 0, Outcome.PASS, "APPROVED_DISCOVERY_SOURCE", {}, {}, now, loaded)]

    def check(stage: int, rule: str, observed: dict[str, Any], thresholds: dict[str, Any],
              ok: bool, failure: Outcome = Outcome.REJECT) -> bool:
        results.append(stage_result(candidate_id, stage, Outcome.PASS if ok else failure, rule,
                                    observed, thresholds, now, loaded))
        return ok

    if snap is None:
        check(1, "AWAITING_POOL", {}, {}, False, Outcome.DEFER)
        return results
    age = (now - snap.observed_at).total_seconds()
    if not check(1, "SNAPSHOT_FRESHNESS", {"age_seconds": age}, {"max": s.max_snapshot_age_seconds},
                 0 <= age <= s.max_snapshot_age_seconds, Outcome.DEFER):
        return results
    if not check(1, "QUOTE_TOKEN", {"mint": snap.quote_mint}, {"allowed": [SOL_MINT, USDC_MINT]},
                 snap.quote_mint in (SOL_MINT, USDC_MINT)):
        return results
    if not check(1, "BONDING_CURVE", {"venue": snap.venue}, {"allowed": False}, not snap.is_bonding_curve):
        return results
    pool_age = None if snap.pool_created_at is None else (now - snap.pool_created_at).total_seconds()
    if not check(1, "POOL_AGE", {"age_seconds": pool_age}, {"min": s.min_pool_age_seconds},
                 pool_age is not None and pool_age >= s.min_pool_age_seconds, Outcome.DEFER):
        return results
    for name, value, low, high in (
        ("LIQUIDITY", snap.liquidity_usd, s.min_liquidity_usd, None),
        ("FDV", snap.fdv_usd, s.min_fdv_usd, s.max_fdv_usd),
    ):
        if value.status is not DataStatus.OK or value.value is None:
            check(1, "INSUFFICIENT_DATA:" + name, {"status": value.status.value}, {"required": "ok"}, False)
            return results
        if not check(1, name + "_RANGE", {"value": value.value, "source": value.source,
                                          "verification": "provider_claim_pending_on_chain_checks"},
                     {"min": low, "max": high}, value.value >= low and (high is None or value.value <= high)):
            return results
    h1 = snap.windows.get("h1")
    volume = None if h1 is None else h1.volume_usd
    if not check(1, "VOLUME_H1", {"volume_usd": volume}, {"min": s.min_volume_h1_usd},
                 volume is not None and volume >= s.min_volume_h1_usd):
        return results
    # buyers+sellers is NOT a distinct-trader count (a wallet may be in both sets). Their max is a lower bound.
    traders: int | None = None
    source: str | None = None
    if claim is not None and 0 <= (now - claim.observed_at).total_seconds() <= s.max_snapshot_age_seconds:
        stats = claim.stats.get("1h")
        if stats is not None and stats.num_traders is not None:
            traders, source = stats.num_traders, claim.source
    if traders is None and h1 is not None:
        reported = [x for x in (h1.buyers, h1.sellers) if x is not None]
        if reported:
            traders, source = max(reported), snap.source + ":distinct_traders_lower_bound"
    check(2, "DISTINCT_TRADERS_H1", {"count_or_lower_bound": traders, "source": source},
          {"min": s.min_distinct_traders_h1}, traders is not None and traders >= s.min_distinct_traders_h1,
          Outcome.DEFER if traders is None or (source is not None and "lower_bound" in source) else Outcome.REJECT)
    return results


def source_conflict(left: Decimal | None, right: Decimal | None, cap: Decimal) -> bool:
    if left is None or right is None:
        return False
    if left <= 0 or right <= 0:
        return left != right
    return abs(left - right) / max(left, right) > cap
