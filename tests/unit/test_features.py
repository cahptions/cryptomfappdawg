"""Hand-computed feature arithmetic, provenance, bounded history, freshness, and point-in-time isolation."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from desk.contracts.discovery import Outcome, SecurityAssessment
from desk.contracts.features import FeatureValue
from desk.contracts.market import PoolSnapshot, WindowMetrics
from desk.contracts.observed import DataStatus, Observed
from desk.contracts.tokens import TokenAudit, TokenClaims, TokenStatsWindow
from desk.core.clock import to_iso
from desk.core.config import load_config
from desk.core.paths import REPO_ROOT
from desk.features import FEATURE_REGISTRY, FEATURE_SET_VERSION, FeatureBuilder
from desk.features.registry import FeatureDefinition
from desk.storage.db import DbKind, connect, migrate
from desk.storage.discovery_repo import DiscoveryRepository
from desk.storage.market_repo import MarketRepository
from tests.unit.test_discovery import pool_snapshot
from tests.unit.test_security import MINT, NOW, POOL, holder_metrics, pool_verification, quotes


@pytest.fixture
def conn(tmp_path: Path):
    connection = connect(tmp_path / "market.db", DbKind.MARKET)
    migrate(connection, DbKind.MARKET)
    yield connection
    connection.close()


def sample(at: datetime, price: Decimal, *, source: str = "geckoterminal.pools") -> PoolSnapshot:
    def obs(value: Decimal) -> Observed[Decimal]:
        return Observed[Decimal](value=value, status=DataStatus.OK, source=source, observed_at=at)

    return pool_snapshot().model_copy(update={
        "source": source, "observed_at": at, "price_usd": obs(price),
        "liquidity_usd": obs(Decimal(100000) + (price - 100) * 100), "fdv_usd": obs(Decimal(2000000)),
        "market_cap_usd": obs(Decimal(1000000)), "raw_sha256": f"raw-{at.isoformat()}-{source}",
        "windows": {
            "m5": WindowMetrics(volume_usd=Decimal(24000), buys=20, sells=10),
            "h1": WindowMetrics(volume_usd=Decimal(120000), buys=120, sells=60, buyers=100, sellers=90),
            "h6": WindowMetrics(volume_usd=Decimal(600000)),
            "h24": WindowMetrics(volume_usd=Decimal(2400000)),
        },
    })


def seed_series(conn: sqlite3.Connection) -> None:
    MarketRepository(conn).save_pool_snapshots(
        sample(NOW - timedelta(minutes=420 - i), Decimal(100 + i)) for i in range(421))


def seed_claim(conn: sqlite3.Connection, age_hours: int = 7) -> TokenClaims:
    claim = TokenClaims(
        mint=MINT, source="jupiter.tokens", observed_at=NOW, created_at=NOW - timedelta(hours=age_hours),
        holder_count=1500, audit=TokenAudit(dev_mints=50, dev_migrations=25),
        stats={"1h": TokenStatsWindow(buy_volume_usd=Decimal(60000), sell_volume_usd=Decimal(40000),
                                    buy_organic_volume_usd=Decimal(45000), sell_organic_volume_usd=Decimal(30000))},
        raw_sha256="raw-claim",
    )
    MarketRepository(conn).save_token_claims([claim])
    return claim


def persist_assessment(conn: sqlite3.Connection, assessment: SecurityAssessment) -> None:
    settings = load_config(REPO_ROOT / "config").settings.discovery
    candidate = DiscoveryRepository(conn, settings).observe(MINT, POOL, "test", NOW - timedelta(hours=7))
    conn.execute("INSERT INTO security_assessments(candidate_id,assessed_at,config_hash,payload_json) VALUES(?,?,?,?)",
                 (candidate, to_iso(assessment.assessed_at), "cfg", assessment.model_dump_json()))


def test_registry_is_complete_and_incomplete_definitions_fail() -> None:
    assert len(FEATURE_REGISTRY) >= 40
    assert FEATURE_SET_VERSION.startswith("features-v1-")
    for name, definition in FEATURE_REGISTRY.items():
        assert name == definition.name
        assert definition.definition and definition.unit and definition.sources
        assert definition.max_age_seconds > 0 and definition.version and definition.missing_policy
    with pytest.raises(ValidationError):
        FeatureDefinition(name="incomplete")  # type: ignore[call-arg]


@pytest.mark.parametrize("label,old", [("5m", 515), ("15m", 505), ("1h", 460), ("6h", 160)])
def test_log_returns_match_fixed_prices(conn: sqlite3.Connection, label: str, old: int) -> None:
    seed_series(conn)
    snapshot = FeatureBuilder(conn).build(MINT, POOL, NOW)
    assert snapshot.usable(f"log_return_{label}") == (Decimal(520) / old).ln()


def test_activity_liquidity_and_continuous_momentum_arithmetic(conn: sqlite3.Connection) -> None:
    seed_series(conn)
    snapshot = FeatureBuilder(conn).build(MINT, POOL, NOW)
    expected = {
        "price_usd": Decimal(520), "reported_liquidity_usd": Decimal(142000),
        "fdv_usd": Decimal(2000000), "market_cap_usd": Decimal(1000000),
        "volume_usd_5m": Decimal(24000), "volume_usd_1h": Decimal(120000),
        "volume_usd_6h": Decimal(600000), "volume_usd_24h": Decimal(2400000),
        "buy_sell_ratio": Decimal(2), "buy_transactions_1h": Decimal(120), "sell_transactions_1h": Decimal(60),
        "distinct_buyers_1h": Decimal(100), "distinct_sellers_1h": Decimal(90),
        "volume_acceleration": Decimal("2.4"), "transaction_frequency_acceleration": Decimal(2),
        "mean_transaction_size_usd": Decimal(120000) / 180,
        "liquidity_change_1h": Decimal(142000) / 136000 - 1,
        "liquidity_to_market_cap": Decimal("0.142"), "distance_from_high_6h": Decimal(0),
        "consolidation_range_1h": Decimal(60) / 460, "volume_decay_orig": Decimal(1),
        "return_acceleration": (Decimal(520) / 515).ln() - (Decimal(515) / 510).ln(),
        "first_observed_age_seconds": Decimal(7 * 3600),
    }
    logs = [(Decimal(i) / (i - 1)).ln() for i in range(461, 521)]
    mean = sum(logs, Decimal(0)) / 60
    volatility = (sum(((value - mean) ** 2 for value in logs), Decimal(0)) / 60).sqrt()
    expected["realized_volatility_1h"] = volatility
    expected["volatility_adjusted_momentum"] = (Decimal(520) / 460).ln() / volatility
    for name, value in expected.items():
        assert snapshot.usable(name) == value, name


@pytest.mark.parametrize("hours,expected", [(7, Decimal(7) / 24), (24, Decimal(1)), (48, Decimal(1))])
def test_documented_six_hour_volume_decay_variants(conn: sqlite3.Connection, hours: int, expected: Decimal) -> None:
    seed_series(conn)
    seed_claim(conn, hours)
    snapshot = FeatureBuilder(conn).build(MINT, POOL, NOW)
    adjusted = snapshot.values["volume_decay_adj"]
    assert abs(adjusted.value - expected) < Decimal("1e-26")
    assert adjusted.status is DataStatus.UNVERIFIED
    assert snapshot.usable("volume_decay_adj") is None
    assert snapshot.usable("volume_decay_orig") == 1


def test_claims_remain_unverified_and_age_is_not_first_seen(conn: sqlite3.Connection) -> None:
    seed_series(conn)
    seed_claim(conn, 48)
    snapshot = FeatureBuilder(conn).build(MINT, POOL, NOW)
    for name, expected in (("token_age_seconds", Decimal(48 * 3600)), ("holder_count", Decimal(1500)),
                           ("dev_mints", Decimal(50)), ("dev_migrations", Decimal(25)),
                           ("organic_volume_share", Decimal("0.75"))):
        assert snapshot.values[name].value == expected
        assert snapshot.values[name].status is DataStatus.UNVERIFIED
        assert snapshot.usable(name) is None
    assert snapshot.usable("first_observed_age_seconds") == 7 * 3600


def test_holder_growth_uses_same_source_prior_claim_and_keeps_evidence(conn: sqlite3.Connection) -> None:
    seed_series(conn)
    latest = seed_claim(conn)
    MarketRepository(conn).save_token_claims([
        latest.model_copy(update={"observed_at": NOW - timedelta(hours=1), "holder_count": 1000,
                                  "raw_sha256": "raw-old-holder-count"}),
        latest.model_copy(update={"source": "other", "observed_at": NOW - timedelta(hours=1), "holder_count": 1}),
    ])
    snapshot = FeatureBuilder(conn).build(MINT, POOL, NOW)
    growth = snapshot.values["holder_count_growth_1h"]
    assert growth.value == Decimal("0.5") and growth.status is DataStatus.UNVERIFIED
    assert "raw-old-holder-count" in snapshot.raw_refs


def test_creation_ages_use_append_only_observations_and_ignore_future_claims(conn: sqlite3.Connection) -> None:
    seed_series(conn)
    repo = DiscoveryRepository(conn, load_config(REPO_ROOT / "config").settings.discovery)
    repo.observe(MINT, POOL, "provider.creation", NOW, token_created_at=NOW - timedelta(hours=48),
                 pool_created_at=NOW - timedelta(hours=12), raw_ref="raw-creation")
    before = FeatureBuilder(conn).build(MINT, POOL, NOW)
    assert before.values["token_age_seconds"].value == 48 * 3600
    assert before.values["pool_age_seconds"].value == 12 * 3600
    assert before.values["pool_age_seconds"].status is DataStatus.UNVERIFIED
    repo.observe(MINT, POOL, "future.creation", NOW + timedelta(seconds=1), token_created_at=NOW,
                 pool_created_at=NOW, raw_ref="raw-future-creation")
    assert FeatureBuilder(conn).build(MINT, POOL, NOW).model_dump_json() == before.model_dump_json()


def test_future_rows_and_mutable_pool_metadata_do_not_change_snapshot(conn: sqlite3.Connection) -> None:
    seed_series(conn)
    seed_claim(conn)
    builder = FeatureBuilder(conn)
    before = builder.build(MINT, POOL, NOW).model_dump_json()
    MarketRepository(conn).save_pool_snapshots([sample(NOW + timedelta(microseconds=1), Decimal(999999))])
    future_claim = seed_claim(conn).model_copy(update={"observed_at": NOW + timedelta(days=1), "holder_count": 999999})
    MarketRepository(conn).save_token_claims([future_claim])
    conn.execute("UPDATE pools SET pool_created_at=? WHERE address=?", (to_iso(NOW - timedelta(days=999)), POOL))
    conn.execute("UPDATE tokens SET first_seen_at=?,name='future' WHERE mint=?", (to_iso(NOW - timedelta(days=999)), MINT))
    # An otherwise identical claim at the same timestamp is harmless; future timestamps are excluded precisely.
    assert builder.build(MINT, POOL, NOW).model_dump_json() == before


def test_no_cross_provider_history_or_cross_pool_identity(conn: sqlite3.Connection) -> None:
    seed_series(conn)
    latest = sample(NOW, Decimal(10000), source="dexscreener.tokens")
    MarketRepository(conn).save_pool_snapshots([latest])
    snapshot = FeatureBuilder(conn).build(MINT, POOL, NOW)
    assert snapshot.usable("price_usd") == 10000
    assert snapshot.values["log_return_1h"].status is DataStatus.MISSING
    assert snapshot.values["log_return_6h"].status is DataStatus.MISSING


def test_equal_timestamp_tie_uses_larger_row_id(conn: sqlite3.Connection) -> None:
    MarketRepository(conn).save_pool_snapshots([sample(NOW, Decimal(10)), sample(NOW, Decimal(20))])
    assert FeatureBuilder(conn).build(MINT, POOL, NOW).usable("price_usd") == 20


def test_stale_features_are_not_usable(conn: sqlite3.Connection) -> None:
    seed_series(conn)
    snapshot = FeatureBuilder(conn).build(MINT, POOL, NOW + timedelta(seconds=121))
    assert snapshot.values["price_usd"].value == 520
    assert snapshot.values["price_usd"].status is DataStatus.STALE
    assert snapshot.usable("price_usd") is None


def test_ancient_latest_observation_is_stale_without_unbounded_history(conn: sqlite3.Connection) -> None:
    MarketRepository(conn).save_pool_snapshots([sample(NOW - timedelta(days=2), Decimal(100))])
    snapshot = FeatureBuilder(conn).build(MINT, POOL, NOW)
    assert snapshot.values["price_usd"].status is DataStatus.STALE
    assert snapshot.usable("price_usd") is None
    assert snapshot.values["log_return_1h"].status is DataStatus.MISSING


def test_zero_denominators_and_missing_wallet_counts_never_become_zero(conn: sqlite3.Connection) -> None:
    latest = sample(NOW, Decimal(520)).model_copy(update={"windows": {
        "h1": WindowMetrics(volume_usd=Decimal(0), buys=10, sells=0),
        "m5": WindowMetrics(volume_usd=Decimal(0)), "h24": WindowMetrics(volume_usd=Decimal(0)),
        "h6": WindowMetrics(volume_usd=Decimal(0)),
    }})
    MarketRepository(conn).save_pool_snapshots([latest])
    snapshot = FeatureBuilder(conn).build(MINT, POOL, NOW)
    for name in ("buy_sell_ratio", "volume_acceleration", "volume_decay_orig", "distinct_buyers_1h"):
        assert snapshot.values[name].status is DataStatus.MISSING
        assert snapshot.values[name].value is None
    assert snapshot.usable("sell_transactions_1h") == 0


def test_missing_history_does_not_manufacture_rolling_high(conn: sqlite3.Connection) -> None:
    MarketRepository(conn).save_pool_snapshots([sample(NOW, Decimal(520)),
                                              sample(NOW - timedelta(hours=6), Decimal(100))])
    snapshot = FeatureBuilder(conn).build(MINT, POOL, NOW)
    assert snapshot.values["distance_from_high_6h"].status is DataStatus.MISSING
    assert snapshot.values["consolidation_range_1h"].status is DataStatus.MISSING


def test_old_assessment_does_not_upgrade_unrecorded_pool_or_quote_evidence(conn: sqlite3.Connection) -> None:
    MarketRepository(conn).save_pool_snapshots([sample(NOW, Decimal(520))])
    persist_assessment(conn, SecurityAssessment(mint=MINT, assessed_at=NOW, outcome=Outcome.PASS,
                                               reasons=(), holders=holder_metrics(), roundtrip_cost_fraction=Decimal("0.02")))
    snapshot = FeatureBuilder(conn).build(MINT, POOL, NOW)
    assert snapshot.usable("top1_pct") == 1
    assert snapshot.usable("top10_pct") == 10
    assert snapshot.usable("dev_holdings_pct") is None
    assert snapshot.usable("roundtrip_cost_fraction") is None
    assert snapshot.usable("verified_liquidity_usd") is None


def test_explicit_pool_and_exact_quote_provenance_produce_fresh_features(conn: sqlite3.Connection) -> None:
    MarketRepository(conn).save_pool_snapshots([sample(NOW, Decimal(520))])
    buy, sell = quotes()
    buy = buy.model_copy(update={"raw_sha256": "raw-buy"})
    sell = sell.model_copy(update={"raw_sha256": "raw-sell"})
    pool = pool_verification().model_copy(update={"raw_refs": ("raw-pool",)})
    persist_assessment(conn, SecurityAssessment(mint=MINT, assessed_at=NOW, outcome=Outcome.PASS, reasons=(),
                                               pool_verification=pool, buy_quote=buy, sell_quote=sell))
    snapshot = FeatureBuilder(conn).build(MINT, POOL, NOW)
    assert snapshot.usable("verified_liquidity_usd") == 100000
    assert snapshot.usable("roundtrip_cost_fraction") == Decimal("0.02")
    assert {"raw-buy", "raw-sell", "raw-pool"}.issubset(snapshot.raw_refs)
    assert snapshot.values["roundtrip_cost_fraction"].observed_at == buy.received_at
    stale = FeatureBuilder(conn).build(MINT, POOL, NOW + timedelta(seconds=9))
    assert stale.values["roundtrip_cost_fraction"].status is DataStatus.STALE
    assert stale.usable("roundtrip_cost_fraction") is None


@pytest.mark.parametrize("value", [Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity")])
def test_nonfinite_feature_values_are_rejected(value: Decimal) -> None:
    with pytest.raises(ValidationError):
        FeatureValue(value=value, status=DataStatus.OK, unit="ratio", source="test", observed_at=NOW, age_seconds=0)


def test_all_unimplemented_context_is_explicitly_missing(conn: sqlite3.Connection) -> None:
    snapshot = FeatureBuilder(conn).build(MINT, POOL, NOW)
    assert set(snapshot.values) == set(FEATURE_REGISTRY)
    assert all(value.status is DataStatus.MISSING and value.value is None for value in snapshot.values.values())
