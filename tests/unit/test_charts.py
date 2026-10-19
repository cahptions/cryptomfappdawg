"""Historical chart boundaries and independently known technical-indicator vectors."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from desk.core.clock import to_epoch_millis, to_iso
from desk.core.paths import REPO_ROOT, DeskPaths
from desk.web.charts import SOURCE, chart_data

NOW = datetime(2026, 10, 19, 12, tzinfo=UTC)
MINT = "So11111111111111111111111111111111111111112"
POOL = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
OTHER_POOL = "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"


@pytest.fixture
def paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> DeskPaths:
    result = DeskPaths(tmp_path / "data", REPO_ROOT / "config", tmp_path / "none")
    result.ensure()
    with sqlite3.connect(result.db_dir / "market.db") as conn:
        # Permit duplicate input timestamps here to exercise the read boundary as well as the
        # production storage layer's separate unique constraint.
        conn.execute("CREATE TABLE ohlcv_bars(id INTEGER PRIMARY KEY, pool_address TEXT, mint TEXT, timeframe TEXT, "
                     "bar_at TEXT, bar_at_ms INTEGER, open TEXT, high TEXT, low TEXT, close TEXT, volume_usd TEXT, "
                     "observed_at TEXT, raw_sha256 TEXT, source TEXT)")
    monkeypatch.setattr("desk.web.charts.utc_now", lambda: NOW)
    return result


def store(paths: DeskPaths, closes: list[Any], *, start: datetime | None = None, pool: str = POOL,
          source: str = SOURCE, mint: str = MINT, observed: datetime = NOW) -> None:
    first = start or NOW - timedelta(minutes=5 * len(closes))
    with sqlite3.connect(paths.db_dir / "market.db") as conn:
        for index, close in enumerate(closes):
            at = first + timedelta(minutes=5 * index)
            conn.execute("INSERT INTO ohlcv_bars(pool_address,mint,timeframe,bar_at,bar_at_ms,open,high,low,close,"
                         "volume_usd,observed_at,source) VALUES (?,?,'5m',?,?,?,?,?,?,?, ?,?)",
                         (pool, mint, to_iso(at), to_epoch_millis(at), str(close), str(close), str(close), str(close),
                          "1000", to_iso(observed), source))


def test_ema_linear_vector_sma_seed_and_warmup(paths: DeskPaths) -> None:
    store(paths, list(range(1, 61)))
    points = chart_data(paths, MINT, POOL)["points"]
    assert len(points) == 60
    assert points[18]["ema20"] is None and Decimal(points[19]["ema20"]) == Decimal("10.5")
    assert Decimal(points[20]["ema20"]) == Decimal("11.5")
    assert points[48]["ema50"] is None and Decimal(points[49]["ema50"]) == Decimal("25.5")
    assert Decimal(points[-1]["ema20"]) == Decimal("50.5")
    assert Decimal(points[-1]["ema50"]) == Decimal("35.5")
    assert points[13]["rsi14"] is None and Decimal(points[14]["rsi14"]) == 100


def test_wilder_rsi_matches_published_worked_example(paths: DeskPaths) -> None:
    closes = ["44.34", "44.09", "44.15", "43.61", "44.33", "44.83", "45.10", "45.42", "45.84", "46.08",
              "45.89", "46.03", "45.61", "46.28", "46.28", "46.00", "46.03", "46.41", "46.22", "45.64"]
    store(paths, closes)
    points = chart_data(paths, MINT, POOL)["points"]
    assert float(points[14]["rsi14"]) == pytest.approx(70.46413502109705)
    assert float(points[15]["rsi14"]) == pytest.approx(66.24961855355516)


@pytest.mark.parametrize("closes,expected", [([5] * 16, 50), (list(range(20, 4, -1)), 0), (list(range(1, 17)), 100)])
def test_rsi_flat_and_one_direction_edges(paths: DeskPaths, closes: list[int], expected: int) -> None:
    store(paths, closes)
    assert Decimal(chart_data(paths, MINT, POOL)["points"][-1]["rsi14"]) == expected


def test_chart_deduplicates_and_never_combines_pools_sources_or_partial_bars(paths: DeskPaths) -> None:
    store(paths, [1, 2, 3])
    store(paths, [30], start=NOW - timedelta(minutes=5))  # same timestamp: one latest stored observation
    store(paths, [900], start=NOW - timedelta(minutes=5), pool=OTHER_POOL)
    store(paths, [800], start=NOW - timedelta(minutes=5), source="unverified")
    store(paths, [700], start=NOW)  # current, still-open bucket
    store(paths, [600], start=NOW + timedelta(minutes=5))
    result = chart_data(paths, MINT, POOL)
    assert [point["close"] for point in result["points"]] == ["1", "2", "30"]
    assert result["pool_address"] == POOL and result["source"] == SOURCE
    assert result["status"] == "ok" and result["stale"] is False


def test_future_observation_cannot_revise_the_past(paths: DeskPaths) -> None:
    store(paths, [1], start=NOW - timedelta(minutes=5))
    store(paths, [999], start=NOW - timedelta(minutes=5), observed=NOW + timedelta(minutes=1))
    assert chart_data(paths, MINT, POOL)["points"][0]["close"] == "1"


def test_gap_restarts_all_indicator_warmups(paths: DeskPaths) -> None:
    store(paths, list(range(1, 61)), start=NOW - timedelta(minutes=410))
    store(paths, [5] * 20, start=NOW - timedelta(minutes=100))
    points = chart_data(paths, MINT, POOL)["points"]
    assert points[59]["ema50"] is not None
    assert points[60]["ema20"] is None and points[60]["rsi14"] is None
    assert points[-1]["ema20"] == "5" and points[-1]["ema50"] is None and points[-1]["rsi14"] == "50"


def test_truncation_keeps_preceding_warmup_and_indicators_are_causal(paths: DeskPaths) -> None:
    store(paths, list(range(1, 81)), start=NOW - timedelta(minutes=500))
    before = chart_data(paths, MINT, POOL)
    limited = chart_data(paths, MINT, POOL, 5)
    assert limited["points"] == before["points"][-5:]
    store(paths, [999, 1], start=NOW - timedelta(minutes=100))
    after = chart_data(paths, MINT, POOL)
    assert after["points"][:80] == before["points"]


def test_chart_reports_missing_and_stale_without_fetching_or_creating_databases(paths: DeskPaths, tmp_path: Path) -> None:
    assert chart_data(paths, MINT, POOL)["status"] == "missing"
    absent = DeskPaths(tmp_path / "absent", REPO_ROOT / "config", tmp_path / "none")
    assert chart_data(absent, MINT)["points"] == [] and not absent.home.exists()
    store(paths, [1], start=NOW - timedelta(hours=1))
    assert chart_data(paths, MINT)["stale"] is True


@pytest.mark.parametrize("limit", [0, 501, -1, True, 1.5, "200"])
def test_chart_rejects_unbounded_or_noninteger_limits(paths: DeskPaths, limit: Any) -> None:
    with pytest.raises(ValueError, match="limit"):
        chart_data(paths, MINT, POOL, limit)


def test_invalid_addresses_are_rejected_before_sql(paths: DeskPaths) -> None:
    with pytest.raises(ValueError, match="mint"):
        chart_data(paths, "' OR 1=1 --")
