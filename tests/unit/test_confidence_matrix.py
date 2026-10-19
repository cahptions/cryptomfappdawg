"""M6-03/06/07/09/10: leakage audit, no-edge never promoted, real edge detected, matrix cells and readiness."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import numpy as np
import pytest

from desk.core.paths import DeskPaths
from desk.features.audit import leaking_features
from desk.features.builder import FeatureBuilder
from desk.stats.matrix import INSUFFICIENT, NOT_APPLICABLE, PASS, Evidence, Trade, build_matrix
from desk.storage.databases import Databases
from desk.storage.market_repo import MarketRepository
from tests.unit.test_market_context import address, ds_snapshot

START = datetime(2026, 11, 1, tzinfo=UTC)
GOOD = Evidence(entry_attempts=1000, entries_without_route=0, replay_checked=300, replay_identical=300)


def trades(returns: np.ndarray, *, strategy: str = "H3", days: float = 90, regimes: tuple[str, ...] = ("RISK_ON", "NEUTRAL"),
           stress_shift: float = 0.02, gap: float = 0.002) -> list[Trade]:
    n = len(returns)
    step = days * 86400 / n
    out = []
    for i, r in enumerate(returns):
        opened = START + timedelta(seconds=i * step)
        out.append(Trade(strategy_id=strategy, strategy_version=f"{strategy}-v2", opened_at=opened,
                         closed_at=opened + timedelta(hours=2), ret=float(r), observed_ret=float(r) + gap,
                         stress_ret=float(r) - stress_shift, regime_label=regimes[i % len(regimes)],
                         exit_problem=False, pnl_lamports=int(r * 10 ** 8), max_hold_seconds=6 * 3600))
    return out


def heavy_zero_mean(rng: np.random.Generator, n: int) -> np.ndarray:
    """Symmetric Student-t(3) memecoin-like returns, clipped at ±95%: true mean exactly 0 after costs."""
    return np.clip(rng.standard_t(3, size=n) * 0.2, -0.95, 0.95)


# ── M6-06 ─────────────────────────────────────────────────────────────────────────────────────────────
@pytest.mark.slow
def test_m6_06_no_edge_data_is_never_promoted() -> None:
    rng = np.random.default_rng(606)
    baseline = Evidence(**{**GOOD.__dict__, "baseline_returns": {"B1": list(heavy_zero_mean(rng, 300))}})
    passes, eligible, full_checked = 0, 0, 0
    runs = 1000
    for run in range(runs):
        data = trades(heavy_zero_mean(rng, 300))
        cell = build_matrix(data, baseline, seed=run, resamples=1000, only=frozenset({"net_expectancy"}))["cells"][0]
        if cell["status"] == PASS:
            passes += 1
        # ELIGIBLE needs every dimension PASS, so only datasets passing expectancy (plus a control sample) need the
        # full matrix; the control sample confirms failing expectancy never yields ELIGIBLE.
        if cell["status"] == PASS or run < 20:
            full_checked += 1
            eligible += build_matrix(data, baseline, seed=run, resamples=1000)["readiness"] == \
                "ELIGIBLE FOR MANUAL LIVE REVIEW"
    rate = passes / runs
    print(f"M6-06 expectancy pass rate on zero-mean data: {rate:.3f}; ELIGIBLE {eligible}/{full_checked}")
    assert rate <= 0.07
    assert eligible == 0


# ── M6-07 ─────────────────────────────────────────────────────────────────────────────────────────────
@pytest.mark.slow
def test_m6_07_real_edge_with_enough_data_passes_statistical_dimensions() -> None:
    rng = np.random.default_rng(607)
    passed = 0
    runs = 200
    dims = frozenset({"net_expectancy", "statistical_reliability"})
    for run in range(runs):
        returns = 0.04 + rng.standard_t(5, size=600) * 0.25 / np.sqrt(5 / 3)   # σ = 25%
        cells = build_matrix(trades(returns), GOOD, seed=run, resamples=1000, only=dims)["cells"]
        passed += all(c["status"] == PASS for c in cells)
    print(f"M6-07 pass rate: {passed / runs:.3f}")
    assert passed / runs >= 0.80


# ── M6-09 cells and readiness ─────────────────────────────────────────────────────────────────────────
def test_m6_09_every_cell_has_estimate_ci_count_and_status() -> None:
    rng = np.random.default_rng(9)
    matrix = build_matrix(trades(0.05 + rng.normal(0, 0.1, 400)), GOOD, resamples=500)
    dims = {c["dimension"] for c in matrix["cells"]}
    assert dims == {"net_expectancy", "predictive_value", "statistical_reliability", "drawdown", "downside_tail",
                    "market_robustness", "execution_reliability", "exit_reliability", "stress", "calibration",
                    "generalization", "stability", "cost_efficiency", "data_integrity"}
    for cell in matrix["cells"]:
        assert set(cell) == {"dimension", "status", "estimate", "lower", "upper", "evidence", "detail"}
        assert cell["status"] in (PASS, "FAIL", INSUFFICIENT, NOT_APPLICABLE)


def test_readiness_states_follow_the_rules_only() -> None:
    rng = np.random.default_rng(10)
    assert build_matrix([], GOOD)["readiness"] == "INSUFFICIENT EVIDENCE"
    few = build_matrix(trades(0.05 + rng.normal(0, 0.1, 100)), GOOD, resamples=500)
    assert few["readiness"] == "INSUFFICIENT EVIDENCE"
    losing = build_matrix(trades(-0.08 + rng.normal(0, 0.1, 400)), GOOD, resamples=500)
    assert losing["readiness"] == "FAILED VALIDATION"
    baseline = Evidence(**{**GOOD.__dict__, "baseline_returns": {"B1": list(rng.normal(0, 0.1, 300))}})
    strong = build_matrix(trades(0.05 + rng.normal(0, 0.1, 400)), baseline, resamples=500)
    statuses = {c["dimension"]: c["status"] for c in strong["cells"]}
    assert all(s in (PASS, NOT_APPLICABLE) for s in statuses.values()), statuses
    assert strong["readiness"] == "ELIGIBLE FOR MANUAL LIVE REVIEW"
    # same strong data with no calibrated regime ⇒ cannot be eligible (market robustness insufficient)
    uncalibrated = build_matrix(trades(0.05 + rng.normal(0, 0.1, 400), regimes=("",)), baseline, resamples=500)
    assert uncalibrated["readiness"] == "CONTINUE PAPER TESTING"
    assert build_matrix(trades(rng.normal(0.05, 0.1, 400), strategy="B1"), GOOD, resamples=500)["readiness"].startswith(
        "BASELINE")


def test_integrity_failures_block_eligibility() -> None:
    rng = np.random.default_rng(11)
    data = trades(0.05 + rng.normal(0, 0.1, 400))
    broken = Evidence(**{**GOOD.__dict__, "unreconciled_lamports": 1})
    assert build_matrix(data, broken, resamples=500)["readiness"] == "FAILED VALIDATION"
    mismatch = Evidence(**{**GOOD.__dict__, "replay_identical": 299})
    cells = {c["dimension"]: c for c in build_matrix(data, mismatch, resamples=500)["cells"]}
    assert cells["data_integrity"]["status"] == "FAIL"


# ── M6-03 leakage detector ────────────────────────────────────────────────────────────────────────────
@pytest.fixture
def dbs(tmp_path: Path):
    databases = Databases.open(DeskPaths(home=tmp_path / "home", config_dir=tmp_path, secrets_file=tmp_path / "s.env"))
    yield databases
    databases.close()


def test_m6_03_leaky_feature_is_caught_and_the_real_builder_is_clean(dbs: Databases) -> None:
    pool, mint = address(), address()
    repo = MarketRepository(dbs.market)
    as_of = START + timedelta(hours=1)
    repo.save_pool_snapshots([ds_snapshot(pool, mint, as_of - timedelta(minutes=m), "50000", "1000") for m in (1, 30, 61)])

    later = iter(range(5, 100, 5))

    def add_future(at: datetime) -> None:   # each audit inserts its own, newer future observation
        minutes = next(later)
        repo.save_pool_snapshots([ds_snapshot(pool, mint, at + timedelta(minutes=minutes), str(minutes), "1")])

    def leaky(at: datetime) -> dict[str, object]:   # "latest price" without a time bound: uses the future
        row = dbs.market.execute("SELECT liquidity_usd FROM market_snapshots WHERE pool_address = ? "
                                 "ORDER BY observed_at_ms DESC LIMIT 1", (pool,)).fetchone()
        return {"leaky_liquidity": row[0]}

    def real(at: datetime) -> dict[str, object]:
        snapshot = FeatureBuilder(dbs.market).build(mint, pool, at)
        return {name: (v.value, v.status) for name, v in snapshot.values.items()}

    assert leaking_features(real, add_future, as_of) == []
    assert leaking_features(leaky, add_future, as_of) == ["leaky_liquidity"]
    assert Decimal(1) and sqlite3.sqlite_version
