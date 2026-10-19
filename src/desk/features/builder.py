"""Build features from append-only observations known at ``as_of``.

Mutable token/pool display rows are deliberately never queried. A momentum series belongs to one pool and
one provider. Missing history, zero denominators and unsupported observations stay explicit missing values.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from itertools import pairwise
from typing import Any

from desk.contracts.discovery import SecurityAssessment
from desk.contracts.features import FeatureSnapshot, FeatureValue
from desk.contracts.market import WindowMetrics
from desk.contracts.observed import DataStatus
from desk.contracts.tokens import TokenClaims
from desk.core.clock import ensure_utc, to_iso
from desk.features.registry import FEATURE_REGISTRY, FEATURE_SET_VERSION
from desk.security.policy import roundtrip_cost

ZERO = Decimal(0)
ONE = Decimal(1)
HOUR = Decimal(3600)
MAX_HISTORY_GAP_SECONDS = 180


@dataclass(frozen=True)
class _Sample:
    row_id: int
    at: datetime
    source: str
    price: Decimal | None
    price_status: DataStatus
    liquidity: Decimal | None
    liquidity_status: DataStatus
    fdv: Decimal | None
    fdv_status: DataStatus
    market_cap: Decimal | None
    market_cap_status: DataStatus
    windows: dict[str, WindowMetrics]
    raw_ref: str | None


def _decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    result = Decimal(str(value))
    return result if result.is_finite() else None


def _status(*statuses: DataStatus) -> DataStatus:
    for value in (DataStatus.MISSING, DataStatus.CONFLICT, DataStatus.STALE,
                  DataStatus.UNVERIFIED, DataStatus.ESTIMATED):
        if value in statuses:
            return value
    return DataStatus.OK


def _ratio(numerator: Decimal | None, denominator: Decimal | None) -> Decimal | None:
    if numerator is None or denominator is None or denominator <= 0:
        return None
    return numerator / denominator


class FeatureBuilder:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def _rows(self, sql: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
        cursor = self.conn.execute(sql, params)
        names = [column[0] for column in cursor.description]
        return [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]

    @staticmethod
    def _sample(row: dict[str, Any]) -> _Sample:
        return _Sample(
            row_id=int(row["id"]), at=ensure_utc(datetime.fromisoformat(row["observed_at"])),
            source=str(row["source"]), price=_decimal(row["price_usd"]),
            price_status=DataStatus(row["price_usd_status"]), liquidity=_decimal(row["liquidity_usd"]),
            liquidity_status=DataStatus(row["liquidity_status"]), fdv=_decimal(row["fdv_usd"]),
            fdv_status=DataStatus(row["fdv_status"]), market_cap=_decimal(row["market_cap_usd"]),
            market_cap_status=DataStatus(row["market_cap_status"]),
            windows={name: WindowMetrics.model_validate(value) for name, value in json.loads(row["windows_json"]).items()},
            raw_ref=row["raw_sha256"],
        )

    def build(self, mint: str, pool_address: str, as_of: datetime) -> FeatureSnapshot:
        as_of = ensure_utc(as_of)
        values: dict[str, FeatureValue] = {}
        refs: set[str] = set()

        def put(name: str, value: Decimal | None, at: datetime | None = None, source: str | None = None,
                status: DataStatus = DataStatus.OK, reason: str | None = None) -> None:
            definition = FEATURE_REGISTRY[name]
            age = None if at is None else (as_of - at).total_seconds()
            if value is None or not value.is_finite() or (age is not None and age < 0):
                value, status = None, DataStatus.MISSING
                reason = reason or "No finite, point-in-time observation with required coverage and nonzero denominator"
            elif age is not None and age > definition.max_age_seconds:
                status, reason = DataStatus.STALE, "Observation exceeds feature maximum age"
            values[name] = FeatureValue(value=value, status=status, unit=definition.unit,
                                        source=source or "+".join(definition.sources), observed_at=at,
                                        age_seconds=age, reason=reason)

        # Define every registered feature, including unavailable context. No placeholder numeric zeros.
        for name in FEATURE_REGISTRY:
            put(name, None, reason="Required observation is not recorded by this data source")

        latest = self._rows(
            "SELECT * FROM market_snapshots WHERE base_mint=? AND pool_address=? AND observed_at<=? "
            "ORDER BY observed_at DESC,id DESC LIMIT 1", (mint, pool_address, to_iso(as_of)))
        samples: list[_Sample] = []
        if latest:
            source = str(latest[0]["source"])
            rows = self._rows(
                "SELECT * FROM market_snapshots WHERE base_mint=? AND pool_address=? AND source=? "
                "AND observed_at<=? AND observed_at>=? ORDER BY observed_at,id",
                (mint, pool_address, source, to_iso(as_of), to_iso(as_of - timedelta(hours=6, seconds=120))))
            # Equal timestamps use the largest row id consistently, without zero-length intervals.
            by_time = {sample.at: sample for sample in (self._sample(row) for row in rows)}
            samples = list(by_time.values())
            if not samples:
                # Preserve a stale latest observation even when it is older than the bounded history window.
                samples = [self._sample(latest[0])]
            refs.update(s.raw_ref for s in samples if s.raw_ref)
            self._market_features(samples, as_of, put)

        claims = self._rows(
            "SELECT * FROM token_claims WHERE mint=? AND observed_at<=? ORDER BY observed_at DESC,id DESC LIMIT 1",
            (mint, to_iso(as_of)))
        claim: TokenClaims | None = None
        if claims:
            row = claims[0]
            claim = TokenClaims.model_validate({**json.loads(row["claims_json"]), "mint": mint,
                                               "source": row["source"], "observed_at": row["observed_at"],
                                               "raw_sha256": row["raw_sha256"]})
            if claim.raw_sha256:
                refs.add(claim.raw_sha256)
            self._claim_features(claim, as_of, put)

        # Earliest reception is a lower-bound age, never substituted for token creation.
        first_rows = self._rows(
            "SELECT MIN(observed_at) AS at FROM (SELECT observed_at FROM market_snapshots WHERE base_mint=? "
            "AND observed_at<=? UNION ALL SELECT observed_at FROM token_claims WHERE mint=? AND observed_at<=?)",
            (mint, to_iso(as_of), mint, to_iso(as_of)))
        if first_rows and first_rows[0]["at"] is not None:
            first_at = ensure_utc(datetime.fromisoformat(first_rows[0]["at"]))
            put("first_observed_age_seconds", Decimal(str((as_of - first_at).total_seconds())), first_at,
                "local_observation_receipt", reason="Lower bound on token age; not creation time")

        creation_rows = self._rows(
            "SELECT o.* FROM discovery_observations o JOIN discoveries d ON d.id=o.candidate_id "
            "WHERE d.mint=? AND d.pool_address=? AND o.observed_at<=? "
            "AND o.pool_created_at IS NOT NULL ORDER BY o.observed_at DESC,o.id DESC LIMIT 1",
            (mint, pool_address, to_iso(as_of)))
        pool_created = None
        pool_observed = None
        pool_source = "discovery_observations"
        if creation_rows:
            row = creation_rows[0]
            pool_created = ensure_utc(datetime.fromisoformat(row["pool_created_at"]))
            pool_observed = ensure_utc(datetime.fromisoformat(row["observed_at"]))
            pool_source = str(row["source"])
            if row["raw_sha256"]:
                refs.add(row["raw_sha256"])
        elif claim is not None and claim.first_pool == pool_address:
            pool_created, pool_observed, pool_source = claim.first_pool_created_at, claim.observed_at, claim.source
        if pool_created is not None and pool_observed is not None and pool_created <= as_of:
            put("pool_age_seconds", Decimal(str((as_of - pool_created).total_seconds())), pool_observed,
                pool_source, DataStatus.UNVERIFIED, "Provider creation-time claim; mutable pool cache is ignored")

        if values["token_age_seconds"].value is None:
            token_creation_rows = self._rows(
                "SELECT o.* FROM discovery_observations o JOIN discoveries d ON d.id=o.candidate_id "
                "WHERE d.mint=? AND o.observed_at<=? AND o.token_created_at IS NOT NULL "
                "ORDER BY o.observed_at DESC,o.id DESC LIMIT 1", (mint, to_iso(as_of)))
            if token_creation_rows:
                row = token_creation_rows[0]
                token_created = ensure_utc(datetime.fromisoformat(row["token_created_at"]))
                if token_created <= as_of:
                    put("token_age_seconds", Decimal(str((as_of - token_created).total_seconds())),
                        ensure_utc(datetime.fromisoformat(row["observed_at"])), str(row["source"]),
                        DataStatus.UNVERIFIED, "Provider creation-time claim retained in append-only discovery observations")
                    if row["raw_sha256"]:
                        refs.add(row["raw_sha256"])

        age_feature = values["token_age_seconds"]
        if samples and age_feature.value is not None and age_feature.observed_at is not None:
            current = samples[-1]
            h6 = current.windows.get("h6", WindowMetrics()).volume_usd
            h24 = current.windows.get("h24", WindowMetrics()).volume_usd
            age_hours = age_feature.value / HOUR
            clamped_age = max(Decimal(6), min(Decimal(24), age_hours))
            adjusted = _ratio(h6, None if h24 is None else h24 * Decimal(6) / clamped_age)
            put("volume_decay_adj", adjusted, min(current.at, age_feature.observed_at),
                current.source + "+" + age_feature.source, DataStatus.UNVERIFIED,
                "Adjusted using unverified token creation time; six-hour formula from PLAN §11")

        refs.update(self._holder_growth(mint, claim, as_of, put))
        refs.update(self._security_features(mint, pool_address, as_of, put))
        return FeatureSnapshot(mint=mint, pool_address=pool_address, as_of=as_of,
                               feature_set_version=FEATURE_SET_VERSION, values=values, raw_refs=tuple(sorted(refs)))

    @staticmethod
    def _market_features(samples: list[_Sample], as_of: datetime, put: Any) -> None:
        current = samples[-1]

        def anchor(seconds: int) -> _Sample | None:
            target = as_of - timedelta(seconds=seconds)
            eligible = [sample for sample in samples if sample.at <= target]
            found = eligible[-1] if eligible else None
            return found if found is not None and (target - found.at).total_seconds() <= 120 else None

        def series(seconds: int) -> list[_Sample]:
            first = anchor(seconds)
            if first is None:
                return []
            found = [sample for sample in samples if sample.at >= first.at]
            if (any(s.price is None or s.price <= 0 or s.price_status is not DataStatus.OK for s in found)
                    or any((b.at - a.at).total_seconds() > MAX_HISTORY_GAP_SECONDS
                           for a, b in pairwise(found))):
                return []
            return found

        for name, value, status in (
            ("price_usd", current.price, current.price_status),
            ("reported_liquidity_usd", current.liquidity, current.liquidity_status),
            ("fdv_usd", current.fdv, current.fdv_status),
            ("market_cap_usd", current.market_cap, current.market_cap_status),
        ):
            put(name, value, current.at, current.source, status)
        for label, window in (("5m", "m5"), ("1h", "h1"), ("6h", "h6"), ("24h", "h24")):
            put(f"volume_usd_{label}", current.windows.get(window, WindowMetrics()).volume_usd, current.at, current.source)

        returns: dict[str, Decimal | None] = {}
        for label, seconds in (("5m", 300), ("15m", 900), ("1h", 3600), ("6h", 21600)):
            previous = anchor(seconds)
            ratio = _ratio(current.price, None if previous is None else previous.price)
            result = None if ratio is None or ratio <= 0 else ratio.ln()
            status = _status(current.price_status, DataStatus.MISSING if previous is None else previous.price_status)
            put(f"log_return_{label}", result, current.at, current.source, status)
            returns[label] = result if status is DataStatus.OK else None

        five, ten = anchor(300), anchor(600)
        preceding_ratio = _ratio(None if five is None else five.price, None if ten is None else ten.price)
        acceleration = None
        if (returns["5m"] is not None and preceding_ratio is not None and preceding_ratio > 0
                and five is not None and ten is not None and five.price_status is DataStatus.OK
                and ten.price_status is DataStatus.OK):
            acceleration = returns["5m"] - preceding_ratio.ln()
        put("return_acceleration", acceleration, current.at, current.source)

        hour_series, six_series = series(3600), series(21600)
        stddev = None
        if len(hour_series) > 1:
            prices = [sample.price for sample in hour_series]
            logs = [(b / a).ln() for a, b in pairwise(prices) if a is not None and b is not None]
            mean = sum(logs, ZERO) / Decimal(len(logs))
            stddev = (sum(((value - mean) ** 2 for value in logs), ZERO) / Decimal(len(logs))).sqrt()
            valid_prices = [p for p in prices if p is not None]
            put("consolidation_range_1h", (max(valid_prices) - min(valid_prices)) / min(valid_prices),
                current.at, current.source)
        put("realized_volatility_1h", stddev, current.at, current.source)
        put("volatility_adjusted_momentum", _ratio(returns["1h"], stddev), current.at, current.source)
        high = max((s.price for s in six_series if s.price is not None), default=None)
        distance = _ratio(current.price, high)
        put("distance_from_high_6h", None if distance is None else distance - ONE, current.at, current.source)

        hour, short = current.windows.get("h1", WindowMetrics()), current.windows.get("m5", WindowMetrics())
        put("buy_sell_ratio", _ratio(_decimal(hour.buys), _decimal(hour.sells)), current.at, current.source)
        for name, count in (("buy_transactions_1h", hour.buys), ("sell_transactions_1h", hour.sells),
                            ("distinct_buyers_1h", hour.buyers), ("distinct_sellers_1h", hour.sellers)):
            put(name, _decimal(count), current.at, current.source)
        hour_count = None if hour.buys is None or hour.sells is None else Decimal(hour.buys + hour.sells)
        short_count = None if short.buys is None or short.sells is None else Decimal(short.buys + short.sells)
        put("transaction_frequency_acceleration", _ratio(short_count, None if hour_count is None else hour_count / 12),
            current.at, current.source)
        put("mean_transaction_size_usd", _ratio(hour.volume_usd, hour_count), current.at, current.source)
        put("volume_acceleration", _ratio(short.volume_usd, None if hour.volume_usd is None else hour.volume_usd / 12),
            current.at, current.source)
        day_volume = current.windows.get("h24", WindowMetrics()).volume_usd
        put("volume_decay_orig", _ratio(current.windows.get("h6", WindowMetrics()).volume_usd,
                                       None if day_volume is None else day_volume / 4), current.at, current.source)
        put("liquidity_to_market_cap", _ratio(current.liquidity, current.market_cap), current.at,
            current.source, _status(current.liquidity_status, current.market_cap_status))
        hour_anchor = anchor(3600)
        liquidity_ratio = _ratio(current.liquidity, None if hour_anchor is None else hour_anchor.liquidity)
        put("liquidity_change_1h", None if liquidity_ratio is None else liquidity_ratio - ONE, current.at,
            current.source, _status(current.liquidity_status,
                                    DataStatus.MISSING if hour_anchor is None else hour_anchor.liquidity_status))

    @staticmethod
    def _claim_features(claim: TokenClaims, as_of: datetime, put: Any) -> None:
        status = DataStatus.UNVERIFIED
        if claim.created_at is not None and claim.created_at <= as_of:
            put("token_age_seconds", Decimal(str((as_of - claim.created_at).total_seconds())),
                claim.observed_at, claim.source, status, "Provider token creation-time claim")
        put("holder_count", _decimal(claim.holder_count), claim.observed_at, claim.source, status)
        audit = claim.audit
        put("dev_mints", None if audit is None else _decimal(audit.dev_mints), claim.observed_at, claim.source, status)
        put("dev_migrations", None if audit is None else _decimal(audit.dev_migrations), claim.observed_at, claim.source, status)
        hour = claim.stats.get("1h")
        organic = volume = None
        if hour is not None:
            if hour.buy_organic_volume_usd is not None and hour.sell_organic_volume_usd is not None:
                organic = hour.buy_organic_volume_usd + hour.sell_organic_volume_usd
            if hour.buy_volume_usd is not None and hour.sell_volume_usd is not None:
                volume = hour.buy_volume_usd + hour.sell_volume_usd
        share = _ratio(organic, volume)
        if share is not None and not ZERO <= share <= ONE:
            put("organic_volume_share", share, claim.observed_at, claim.source, DataStatus.CONFLICT,
                "Claimed organic volume exceeds total volume")
        else:
            put("organic_volume_share", share, claim.observed_at, claim.source, status,
                "Provider organic-volume attribution has not been independently verified")

    def _holder_growth(self, mint: str, claim: TokenClaims | None, as_of: datetime, put: Any) -> tuple[str, ...]:
        if claim is None or claim.holder_count is None:
            return ()
        target = as_of - timedelta(hours=1)
        previous = self._rows(
            "SELECT * FROM token_claims WHERE mint=? AND source=? AND observed_at<=? "
            "AND observed_at>=? ORDER BY observed_at DESC,id DESC LIMIT 1",
            (mint, claim.source, to_iso(target), to_iso(target - timedelta(seconds=120))))
        if previous:
            previous_count = _decimal(json.loads(previous[0]["claims_json"]).get("holder_count"))
            ratio = _ratio(Decimal(claim.holder_count), previous_count)
            put("holder_count_growth_1h", None if ratio is None else ratio - ONE,
                claim.observed_at, claim.source, DataStatus.UNVERIFIED)
            if previous[0]["raw_sha256"]:
                return (str(previous[0]["raw_sha256"]),)
        return ()

    def _security_features(self, mint: str, pool_address: str, as_of: datetime, put: Any) -> tuple[str, ...]:
        rows = self._rows(
            "SELECT a.* FROM security_assessments a JOIN discoveries d ON d.id=a.candidate_id "
            "WHERE d.mint=? AND d.pool_address=? AND a.assessed_at<=? ORDER BY a.assessed_at DESC,a.id DESC LIMIT 1",
            (mint, pool_address, to_iso(as_of)))
        if not rows:
            return ()
        assessment = SecurityAssessment.model_validate_json(rows[0]["payload_json"])
        if assessment.mint != mint or assessment.assessed_at > as_of:
            return ()
        if assessment.holders is not None:
            for name, value in (("top1_pct", assessment.holders.adjusted_top1_pct),
                                ("top10_pct", assessment.holders.adjusted_top10_pct),
                                ("dev_holdings_pct", assessment.holders.dev_pct)):
                put(name, value, assessment.assessed_at, "solana.rpc:top20_owner_lower_bound",
                    reason="Coverage is limited to observed top20 token accounts, not full beneficial ownership")
        refs: list[str] = []
        pool = assessment.pool_verification
        if (pool is not None and pool.base_mint == mint and pool.pool_address == pool_address and pool.canonical
                and pool.lp_burned and not pool.unsupported_features and pool.observed_at <= as_of):
            put("verified_liquidity_usd", pool.liquidity_usd, pool.observed_at, "solana.rpc:verified_pool_reserves",
                reason=pool.valuation_basis)
            refs.extend(pool.raw_refs)
        buy, sell = assessment.buy_quote, assessment.sell_quote
        if buy is not None and sell is not None:
            try:
                # Validate chronology and identity here; ``put`` labels old evidence STALE using the registry's10s cap.
                cost = roundtrip_cost(mint, buy, sell, as_of, 2147483647)
            except ValueError:
                put("roundtrip_cost_fraction", None,
                    reason="Linked quotes are future-dated or do not form an exact ticket pair")
            else:
                put("roundtrip_cost_fraction", cost, min(buy.received_at, sell.received_at),
                    buy.source + "+" + sell.source,
                    reason=f"Exact recorded buy ticket {buy.in_amount_raw} lamports; quote-implied cost plus "
                    "reported fixed fees, not complete execution costs")
                refs.extend(ref for ref in (buy.raw_sha256, sell.raw_sha256) if ref)
        else:
            put("roundtrip_cost_fraction", None,
                reason="Assessment lacks linked exact-ticket quotes and their receipt timestamps")
        return tuple(refs)
