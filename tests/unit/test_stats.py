"""M6 statistical methods: bootstrap coverage, ESS, risk measures, deterioration, Holm, calibration, folds."""

from __future__ import annotations

import math

import numpy as np
import pytest

from desk.stats.core import (
    as_array,
    block_length,
    brier_score,
    cusum_alarm,
    effective_sample_size,
    expected_calibration_error,
    expected_shortfall,
    holm,
    max_drawdown,
    mean_interval,
    page_hinkley_alarm,
    stationary_bootstrap_means,
    walk_forward_folds,
    wilson_interval,
)


def ar1_t3(rng: np.random.Generator, n: int, phi: float = 0.2, mean: float = 0.03, scale: float = 0.1) -> np.ndarray:
    noise = rng.standard_t(3, size=n + 50) * scale
    x = np.zeros(n + 50)
    for i in range(1, n + 50):
        x[i] = phi * x[i - 1] + noise[i]
    return x[50:] + mean


@pytest.mark.slow
def test_m6_01_stationary_bootstrap_interval_coverage_on_ar1_student_t() -> None:
    rng = np.random.default_rng(20261019)
    covered = 0
    runs = 500
    for run in range(runs):
        series = ar1_t3(rng, 300)
        interval = mean_interval(series, resamples=1000, seed=run)
        covered += interval.lower <= 0.03 <= interval.upper
    coverage = covered / runs
    print(f"M6-01 actual coverage: {coverage:.3f}")
    assert 0.90 <= coverage <= 0.98, f"actual coverage {coverage:.3f}"


def test_bootstrap_is_deterministic_and_resamples_blocks() -> None:
    x = as_array(np.arange(100, dtype=float))
    a = stationary_bootstrap_means(x, resamples=200, seed=7)
    assert np.array_equal(a, stationary_bootstrap_means(x, resamples=200, seed=7))
    assert len(a) == 200 and abs(a.mean() - 49.5) < 3
    # memory-bounded chunking gives the same answer shape for long series
    long = as_array(np.random.default_rng(1).normal(size=20_000))
    assert len(stationary_bootstrap_means(long, resamples=300, seed=1)) == 300


def test_block_length_grows_with_dependence() -> None:
    rng = np.random.default_rng(3)
    iid = rng.normal(size=600)
    dependent = ar1_t3(rng, 600, phi=0.8, mean=0.0)
    assert block_length(iid) < block_length(dependent)
    assert block_length(iid[:5]) == 1.0


def test_effective_sample_size_shrinks_under_autocorrelation() -> None:
    rng = np.random.default_rng(5)
    iid = rng.normal(size=1000)
    assert effective_sample_size(iid) > 800
    dependent = ar1_t3(rng, 1000, phi=0.6, mean=0.0)
    ess = effective_sample_size(dependent)
    assert ess < 600                       # theory: n (1 - φ) / (1 + φ) = 250 for Gaussian AR(1)


def test_risk_measures_on_hand_computed_series() -> None:
    returns = as_array([0.1, -0.5, 0.2, -0.1])
    # equity 1.1, 0.55, 0.66, 0.594 ; peak 1.1 ⇒ drawdown 1 - 0.55/1.1 = 0.5
    assert max_drawdown(returns) == pytest.approx(0.5)
    tail = as_array([-0.9, -0.5] + [0.0] * 38)            # worst 5% of 40 = 2 values
    assert expected_shortfall(tail) == pytest.approx(-0.7)
    w = wilson_interval(81, 263)                           # reference values (Wilson 1927 formula)
    assert w.estimate == pytest.approx(81 / 263)
    assert w.lower == pytest.approx(0.2553, abs=1e-4) and w.upper == pytest.approx(0.3662, abs=1e-4)


def test_m6_08_deterioration_from_plus_4_to_minus_2_percent_is_detected_within_documented_delay() -> None:
    """Documented: lower CUSUM with k = 0.12, h = 20 (σ units). Siegmund's approximation gives an in-control ARL
    of ~5,400 trades (≈5% false alarms per 300 healthy trades); a 6 pp drop at σ = 25% (0.24 σ) drifts the
    statistic by -0.12 σ per trade, so the expected detection delay is ≈ (h + 1.166) / 0.12 ≈ 176 trades.
    Acceptance: <= 10% false alarms in the first 300 trades, alarm within 300 trades of the change in >= 80%."""
    rng = np.random.default_rng(8)
    timely = early = 0
    runs = 200
    for _ in range(runs):
        series = np.concatenate([rng.normal(0.04, 0.25, 300), rng.normal(-0.02, 0.25, 300)])
        alarm = cusum_alarm(series, reference_mean=0.04, sigma=0.25, k=0.12, h=20.0)
        early += alarm is not None and alarm < 300
        timely += alarm is not None and 300 <= alarm < 600
    assert early / runs <= 0.10, early / runs
    assert timely / runs >= 0.80, timely / runs
    assert page_hinkley_alarm(np.concatenate([np.full(200, 0.04), np.full(200, -0.02)]), delta=0.005, threshold=0.5) \
        is not None


def test_m6_12_holm_adjustment_matches_reference() -> None:
    raw = [0.01, 0.04, 0.03, 0.005]
    assert holm(raw) == pytest.approx([0.03, 0.06, 0.06, 0.02])
    assert holm([]) == []


def test_m6_04_brier_and_ece_on_fixed_data() -> None:
    p = as_array([0.1, 0.4, 0.35, 0.8, 0.9, 0.95])
    y = as_array([0, 0, 1, 1, 1, 0])
    assert brier_score(p, y) == pytest.approx((0.01 + 0.16 + 0.4225 + 0.04 + 0.01 + 0.9025) / 6)
    # bins (10 equal): 0.1→b1; 0.35,0.4→b3,b4; 0.8→b8; 0.9,0.95→b9
    expected = (1 / 6) * 0.1 + (1 / 6) * abs(1 - 0.35) + (1 / 6) * 0.4 + (1 / 6) * 0.2 + (2 / 6) * abs(0.5 - 0.925)
    assert expected_calibration_error(p, y) == pytest.approx(expected)


def test_m6_02_walk_forward_purge_and_embargo() -> None:
    hold = 6 * 3600
    times = [i * 3600.0 for i in range(500)]                 # one trade per hour
    folds, holdout = walk_forward_folds(times, folds=4, embargo_seconds=hold, max_hold_seconds=hold)
    assert holdout == (400, 500)
    for fold in folds:
        train_last_label_end = times[fold.train[1] - 1] + hold
        assert train_last_label_end <= times[fold.test[0]] - hold   # label window ends an embargo before the test
        assert fold.test[1] <= holdout[0]
    with pytest.raises(ValueError, match="embargo"):
        walk_forward_folds(times, folds=4, embargo_seconds=hold - 1, max_hold_seconds=hold)


def test_inputs_must_be_finite() -> None:
    with pytest.raises(ValueError):
        as_array([1.0, math.nan])
