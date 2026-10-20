"""M3 market context: measured regime inputs, the hazard cohort and the thirty-day reference."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from solders.pubkey import Pubkey

from desk.contracts.market import OhlcvBar, PoolSnapshot, WindowMetrics
from desk.contracts.observed import DataStatus
from desk.core.clock import to_iso
from desk.core.config import load_config
from desk.core.errors import ProviderError, ProviderErrorKind
from desk.core.paths import DeskPaths
from desk.features.builder import FeatureBuilder
from desk.ops import collector as collector_module
from desk.ops.collector import Collector, TaskStats
from desk.providers import dexscreener, geckoterminal
from desk.providers.parsing import Fetched, observed_decimal
from desk.regime import classify_regime
from desk.regime.context import (
    INPUTS,
    ContextRecord,
    ReferenceCache,
    build_reference,
    hazard_followup_mints,
    hazard_rate,
    latest_context,
    migrations_last_hour,
    save_context,
    sol_reference_metrics,
    watchlist_volume,
)
from desk.storage import db as dbmod
from desk.storage.databases import Databases
from desk.storage.db import DbKind, connect, migrate
from desk.storage.market_repo import MarketRepository
from tests.conftest import REPO, load_fixture

NOW = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)
SOL_POOL = "Czfq3xZZDmsdGdUyrNLtRhGc47cXcZtLG4crryfu44zE"
OHLCV_RECEIVED = datetime(2026, 10, 19, 3, 51, 59, tzinfo=UTC)   # Date header of the recorded response


@pytest.fixture
def dbs(tmp_path: Path) -> Databases:
    paths = DeskPaths(home=tmp_path / "home", config_dir=tmp_path, secrets_file=tmp_path / "s.env")
    databases = Databases.open(paths)
    yield databases
    databases.close()


def address() -> str:
    return str(Pubkey.new_unique())


_TEMPLATE: PoolSnapshot | None = None


def ds_snapshot(pool: str, mint: str, at: datetime, liquidity: str | None, volume_h1: str | None = None) -> PoolSnapshot:
    global _TEMPLATE
    if _TEMPLATE is None:
        _TEMPLATE = dexscreener.parse_pairs(load_fixture("provider/m1_dexscreener_tokens_v1_amm_pairs.json"),
                                            "dexscreener.tokens_v1", NOW).items[0]
    source = "dexscreener.tokens_v1"
    return _TEMPLATE.model_copy(update={
        "pool_address": pool, "base_mint": mint, "observed_at": at, "source": source,
        "liquidity_usd": observed_decimal(liquidity, source, at),
        "windows": {} if volume_h1 is None else {"h1": WindowMetrics(volume_usd=Decimal(volume_h1))},
    })


def sol_bars(received: datetime = OHLCV_RECEIVED) -> list[OhlcvBar]:
    return geckoterminal.parse_ohlcv(load_fixture("provider/m3_geckoterminal_ohlcv_sol_usdc_1m.json"),
                                     SOL_POOL, "minute:1", received).items


# ── SOL reference ─────────────────────────────────────────────────────────────────────────────────────
def test_sol_metrics_from_recorded_minute_bars_match_hand_computation() -> None:
    bars = sol_bars()
    closes = {bar.ts: bar.close for bar in bars}
    latest = max(closes)
    ret, vol, reason = sol_reference_metrics(bars, OHLCV_RECEIVED)
    assert reason is None and ret is not None and vol is not None
    expected = (closes[latest] / closes[latest - timedelta(hours=1)]).ln()
    assert abs(ret - expected) < Decimal("1e-20")
    minute_returns = [(closes[t] / closes[t - timedelta(minutes=1)]).ln() for t in sorted(closes)
                      if latest - timedelta(hours=1) < t <= latest]
    assert len(minute_returns) == 60
    assert abs(vol - sum(r * r for r in minute_returns).sqrt()) < Decimal("1e-20")
    assert Decimal("0") < vol < Decimal("0.05")   # SOL/USDC 1 h realized volatility is a small fraction


def test_sol_metrics_fail_closed_on_stale_future_missing_anchor_and_gaps() -> None:
    bars = sol_bars()
    latest = max(bar.ts for bar in bars)
    assert sol_reference_metrics(bars, latest + timedelta(seconds=301))[2] == "SOL_REFERENCE_STALE"
    assert sol_reference_metrics(bars, latest - timedelta(seconds=1))[2] == "SOL_REFERENCE_STALE"
    no_anchor = [b for b in bars if b.ts != latest - timedelta(hours=1)]
    assert sol_reference_metrics(no_anchor, OHLCV_RECEIVED)[:2] == (None, None)
    gappy = [b for b in bars if not (latest - timedelta(minutes=30) <= b.ts < latest - timedelta(minutes=25))]
    ret, vol, reason = sol_reference_metrics(gappy, OHLCV_RECEIVED)
    assert ret is not None and vol is None and reason == "SOL_MINUTE_COVERAGE_INSUFFICIENT"
    assert sol_reference_metrics([], OHLCV_RECEIVED)[2] == "SOL_REFERENCE_EMPTY"
    five_minute = [b.model_copy(update={"timeframe": "minute:5"}) for b in bars]
    assert sol_reference_metrics(five_minute, OHLCV_RECEIVED)[2] == "SOL_REFERENCE_EMPTY"


# ── watchlist volume and migrations ───────────────────────────────────────────────────────────────────
def test_watchlist_volume_uses_latest_row_per_pool_inside_window(dbs: Databases) -> None:
    repo = MarketRepository(dbs.market)
    pools = [(address(), address()) for _ in range(20)]
    repo.save_pool_snapshots([ds_snapshot(p, m, NOW - timedelta(seconds=100), "50000", "999") for p, m in pools])
    repo.save_pool_snapshots([ds_snapshot(p, m, NOW - timedelta(seconds=30), "50000", "100") for p, m in pools])
    repo.save_pool_snapshots([ds_snapshot(address(), address(), NOW - timedelta(seconds=181), "1", "1000000")])
    repo.save_pool_snapshots([ds_snapshot(address(), address(), NOW + timedelta(seconds=1), "1", "1000000")])
    repo.save_pool_snapshots([ds_snapshot(address(), address(), NOW - timedelta(seconds=10), "1", None)])
    assert watchlist_volume(dbs.market, NOW) == (Decimal(2000), 20, None)
    value, pools_seen, reason = watchlist_volume(dbs.market, NOW + timedelta(seconds=200))
    assert value is None and pools_seen == 0 and reason == "WATCHLIST_COVERAGE_INSUFFICIENT"


def test_migrations_count_distinct_mints_in_preceding_hour(dbs: Databases) -> None:
    conn = dbs.market

    def observe(mint: str, reason: str, at: datetime) -> None:
        conn.execute("INSERT OR IGNORE INTO tokens(mint, first_seen_at, last_seen_at) VALUES (?, 'a', 'b')", (mint,))
        candidate = "cand_" + mint[:8] + reason
        conn.execute("INSERT OR IGNORE INTO discoveries(id, mint, pool_address, reason, first_observed_at, "
                     "last_observed_at, state) VALUES (?, ?, ?, ?, ?, ?, 'PENDING')",
                     (candidate, mint, address(), reason, to_iso(at), to_iso(at)))
        conn.execute("INSERT INTO discovery_observations(candidate_id, source, reason, observed_at) VALUES (?, 's', ?, ?)",
                     (candidate, reason, to_iso(at)))

    a, b = address(), address()
    observe(a, "MIGRATION", NOW - timedelta(minutes=10))
    observe(a, "MIGRATION", NOW - timedelta(minutes=5))         # same mint counts once
    observe(b, "MIGRATION", NOW - timedelta(minutes=59))
    observe(address(), "MIGRATION", NOW - timedelta(minutes=61))  # outside the hour
    observe(address(), "MIGRATION", NOW + timedelta(seconds=1))   # future
    observe(address(), "NEW_POOL", NOW - timedelta(minutes=1))
    assert migrations_last_hour(conn, NOW) == 2


# ── hazard cohort ─────────────────────────────────────────────────────────────────────────────────────
def _cohort(dbs: Databases, n_survive: int, n_lost: int, n_drained: int = 0) -> list[tuple[str, str]]:
    repo = MarketRepository(dbs.market)
    entry = NOW - timedelta(hours=24, minutes=30)
    pools = [(address(), address()) for _ in range(n_survive + n_lost + n_drained)]
    repo.save_pool_snapshots([ds_snapshot(p, m, entry, "10000") for p, m in pools])
    # a later, larger observation never replaces the cohort entry
    repo.save_pool_snapshots([ds_snapshot(p, m, entry + timedelta(minutes=5), "99999") for p, m in pools])
    outcome_at = entry + timedelta(hours=24, minutes=2)
    liquidity = ["9000"] * n_survive + ["2000"] * n_lost + ["0"] * n_drained
    repo.save_pool_snapshots([ds_snapshot(p, m, outcome_at, liq) for (p, m), liq in zip(pools, liquidity, strict=True)])
    return pools


def test_hazard_rate_counts_losses_including_explicit_zero(dbs: Databases) -> None:
    _cohort(dbs, n_survive=19, n_lost=5, n_drained=1)
    repo = MarketRepository(dbs.market)
    entry = NOW - timedelta(hours=24, minutes=30)
    repo.save_pool_snapshots([ds_snapshot(address(), address(), entry, "1000")])   # below entry minimum: excluded
    rate, counts, reason = hazard_rate(dbs.market, NOW)
    assert reason is None and rate == Decimal(6) / Decimal(25)
    assert counts == {"hazard_cohort": 25, "hazard_resolved": 25, "hazard_events": 6}


def test_hazard_ignores_future_and_early_outcomes_and_requires_coverage(dbs: Databases) -> None:
    pools = _cohort(dbs, n_survive=25, n_lost=0)
    entry = NOW - timedelta(hours=24, minutes=30)
    # before the outcome time of the default cohort only early (<24 h) observations exist: unresolved, not survivors
    early = entry + timedelta(hours=24, minutes=1)
    rate, counts, reason = hazard_rate(dbs.market, early)
    assert rate is None and counts["hazard_resolved"] == 0 and reason == "HAZARD_COHORT_TOO_SMALL"
    # a cohort whose follow-ups are mostly missing is not a measured hazard rate
    repo = MarketRepository(dbs.market)
    unresolved = [(address(), address()) for _ in range(10)]
    repo.save_pool_snapshots([ds_snapshot(p, m, entry, "10000") for p, m in unresolved])
    assert hazard_rate(dbs.market, NOW)[2] == "HAZARD_COHORT_UNRESOLVED"
    assert len(pools) == 25


def test_hazard_followup_selects_unobserved_pools_after_24_hours(dbs: Databases) -> None:
    repo = MarketRepository(dbs.market)
    entry = NOW - timedelta(hours=24, minutes=5)
    due, done, small = (address(), address()), (address(), address()), (address(), address())
    repo.save_pool_snapshots([ds_snapshot(*due, entry, "10000"), ds_snapshot(*done, entry, "10000"),
                              ds_snapshot(*small, entry, "100")])
    repo.save_pool_snapshots([ds_snapshot(*done, entry + timedelta(hours=24, minutes=1), "8000")])
    assert hazard_followup_mints(dbs.market, NOW) == [due[1]]
    assert hazard_followup_mints(dbs.market, entry + timedelta(hours=23)) == []
    assert hazard_followup_mints(dbs.market, NOW + timedelta(minutes=30)) == []   # outside the follow-up window


def test_migration_backfills_cohort_from_existing_snapshots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    conn = connect(tmp_path / "market.db", DbKind.MARKET)
    original = dbmod.available_migrations
    monkeypatch.setattr(dbmod, "available_migrations", lambda kind: original(kind)[:4])
    migrate(conn, DbKind.MARKET)
    pool, mint = address(), address()
    for minute, liquidity, status in ((3, "0", "ok"), (5, None, "missing"), (7, "12000", "ok"), (9, "50000", "ok")):
        at = NOW + timedelta(minutes=minute)
        conn.execute(
            "INSERT INTO market_snapshots(pool_address, base_mint, source, observed_at, observed_at_ms, price_usd_status,"
            " price_native_status, liquidity_usd, liquidity_status, fdv_status, market_cap_status, windows_json, flags)"
            " VALUES (?, ?, 'dexscreener.tokens_v1', ?, ?, 'missing', 'missing', ?, ?, 'missing', 'missing', '{}', '')",
            (pool, mint, to_iso(at), int(at.timestamp() * 1000), liquidity, status))
    monkeypatch.setattr(dbmod, "available_migrations", original)
    assert migrate(conn, DbKind.MARKET) == [5, 6, 7, 8, 9]
    row = conn.execute("SELECT * FROM hazard_cohort").fetchone()
    assert (row["pool_address"], row["entry_liquidity_usd"]) == (pool, "12000")
    conn.close()


# ── storage, reference and features ───────────────────────────────────────────────────────────────────
def complete(at: datetime, i: int) -> ContextRecord:
    return ContextRecord(observed_at=at, sol_return_1h=Decimal(i % 7 - 3) / 1000, sol_volatility_1h=Decimal(i % 5) / 1000,
                         watchlist_volume_usd_1h=Decimal(1_000_000 + i), migrations_per_hour=Decimal(i % 9),
                         hazard_rate_24h=Decimal(i % 4) / 10)


def test_unknown_input_needs_a_reason_and_rows_are_append_only(dbs: Databases) -> None:
    with pytest.raises(ValueError, match="without a recorded reason"):
        save_context(dbs.market, ContextRecord(observed_at=NOW))
    save_context(dbs.market, ContextRecord(observed_at=NOW, reasons=dict.fromkeys(INPUTS, "TEST")))
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        dbs.market.execute("UPDATE market_context SET sol_return_1h = '0'")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        dbs.market.execute("DELETE FROM market_context")
    row = latest_context(dbs.market, NOW)
    assert row is not None and all(row[name] is None for name in INPUTS)


def _history(dbs: Databases, days: int, *, skip: tuple[int, int] | None = None) -> None:
    start = NOW - timedelta(days=days)
    for i in range(days * 24 * 4 + 1):   # every 15 minutes
        at = start + timedelta(minutes=15 * i)
        if skip and skip[0] <= i < skip[1]:
            continue
        save_context(dbs.market, complete(at, i))


def test_reference_needs_thirty_measured_days_without_day_long_gaps(dbs: Databases) -> None:
    _history(dbs, 29)
    assert build_reference(dbs.market, NOW) is None
    _history(dbs, 31)
    reference = build_reference(dbs.market, NOW)
    assert reference is not None and reference.as_of <= NOW
    assert NOW - reference.as_of <= timedelta(hours=1)
    assert 700 <= len(reference.observations) <= 722      # one sample per hour


def test_reference_rejects_a_gap_longer_than_a_day(dbs: Databases) -> None:
    _history(dbs, 31, skip=(4 * 24 * 10, 4 * 24 * 12))      # two missing days
    assert build_reference(dbs.market, NOW) is None


def test_reference_is_point_in_time_and_cached_per_hour(dbs: Databases) -> None:
    _history(dbs, 31)
    before = build_reference(dbs.market, NOW)
    assert before is not None
    for minutes in (1, 30, 59):     # rows written later in the same decision hour never change the reference
        save_context(dbs.market, complete(NOW + timedelta(minutes=minutes), 99))
    after = build_reference(dbs.market, NOW + timedelta(minutes=59))
    assert after is not None and after.reference_hash == before.reference_hash
    cache = ReferenceCache(dbs.market)
    assert cache.get(NOW) is cache.get(NOW + timedelta(minutes=59))


def test_features_read_latest_stored_context_and_regime_labels(dbs: Databases) -> None:
    _history(dbs, 31)
    pool, mint = address(), address()
    as_of = NOW + timedelta(seconds=30)
    save_context(dbs.market, complete(NOW + timedelta(minutes=5), 3))   # future: must be ignored
    features = FeatureBuilder(dbs.market).build(mint, pool, as_of)
    for name in INPUTS:
        value = features.values[name]
        assert value.status is DataStatus.OK and value.observed_at == NOW, name
    result = classify_regime(features, build_reference(dbs.market, as_of))
    assert result.status is DataStatus.OK and result.label is not None
    stale = FeatureBuilder(dbs.market).build(mint, pool, NOW + timedelta(seconds=121))
    assert all(stale.values[name].status is DataStatus.STALE for name in INPUTS)


def test_missing_context_input_keeps_its_reason_and_blocks_the_regime(dbs: Databases) -> None:
    _history(dbs, 31)
    save_context(dbs.market, ContextRecord(observed_at=NOW + timedelta(seconds=10), **{
        name: getattr(complete(NOW, 1), name) for name in INPUTS if name != "migrations_per_hour"},
        reasons={"migrations_per_hour": "DISCOVERY_NOT_RUNNING"}))
    features = FeatureBuilder(dbs.market).build(address(), address(), NOW + timedelta(seconds=20))
    value = features.values["migrations_per_hour"]
    assert value.status is DataStatus.MISSING and value.reason == "DISCOVERY_NOT_RUNNING" and value.value is None
    result = classify_regime(features, build_reference(dbs.market, NOW))
    assert result.label is None and result.reasons == ("INSUFFICIENT_CONTEXT:migrations_per_hour",)


# ── collector job ─────────────────────────────────────────────────────────────────────────────────────
class _Gecko:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail

    async def ohlcv(self, pool: str, timeframe: str, aggregate: int, limit: int) -> Fetched[OhlcvBar]:
        assert (pool, timeframe, aggregate) == (SOL_POOL, "minute", 1)
        if self.fail:
            raise ProviderError("geckoterminal", ProviderErrorKind.NETWORK, "offline")
        return Fetched(sol_bars(), [], "raw", 200, 1.0)


def test_collector_records_context_row_with_reasons(dbs: Databases, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(collector_module, "utc_now", lambda: OHLCV_RECEIVED)

    collector = Collector(SimpleNamespace(geckoterminal=_Gecko(), dexscreener=None, jupiter=None, rpc=None),  # type: ignore[arg-type]
                          dbs, load_config(REPO / "config"))
    collector.stats.tasks["market_context"] = TaskStats()
    asyncio.run(collector.market_context())
    row = latest_context(dbs.market, OHLCV_RECEIVED)
    assert row is not None and row["sol_return_1h"] is not None and row["sol_volatility_1h"] is not None
    assert row["raw_sha256"] == "raw"
    for name in ("watchlist_volume_usd_1h", "migrations_per_hour", "hazard_rate_24h"):
        assert row[name] is None
    reasons = json.loads(row["coverage_json"])["reasons"]
    assert reasons == {"watchlist_volume_usd_1h": "WATCHLIST_REFRESH_NOT_CURRENT",
                       "migrations_per_hour": "DISCOVERY_NOT_RUNNING", "hazard_rate_24h": "HAZARD_COHORT_TOO_SMALL"}
    failing = Collector(SimpleNamespace(geckoterminal=_Gecko(fail=True), dexscreener=None, jupiter=None, rpc=None),  # type: ignore[arg-type]
                        dbs, load_config(REPO / "config"))
    failing.stats.tasks["market_context"] = TaskStats()
    with pytest.raises(ProviderError):
        asyncio.run(failing.market_context())
    assert dbs.market.execute("SELECT COUNT(*) FROM market_context").fetchone()[0] == 2   # the failure is recorded too
