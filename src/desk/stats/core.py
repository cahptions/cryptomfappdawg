"""Statistical methods of PLAN §14.2. Pure functions over float64 arrays (estimates, never ledger money).

Every random procedure takes an explicit seed, so a confidence report is reproducible from its inputs.
"""

from __future__ import annotations

import math
from collections.abc import Iterator, Sequence
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

Array = NDArray[np.float64]


def as_array(values: Sequence[float] | Array) -> Array:
    out = np.asarray(values, dtype=np.float64)
    if out.ndim != 1 or not np.all(np.isfinite(out)):
        raise ValueError("a finite one-dimensional series is required")
    return out


# ── dependence ────────────────────────────────────────────────────────────────────────────────────────
def autocorrelation(x: Array, max_lag: int) -> Array:
    x = x - x.mean()
    denominator = float(np.dot(x, x))
    if denominator == 0:
        return np.zeros(max_lag + 1)
    return np.array([1.0] + [float(np.dot(x[:-k], x[k:])) / denominator for k in range(1, max_lag + 1)])


def block_length(x: Array) -> float:
    """Politis & White (2004, corrected by Patton et al. 2009) optimal mean block for the stationary bootstrap."""
    n = len(x)
    if n < 8:
        return 1.0
    max_lag = min(n - 1, math.ceil(math.sqrt(n)) + 5)
    rho = autocorrelation(x, max_lag)
    k_n = max(5, int(math.sqrt(math.log10(n))))
    threshold = 2 * math.sqrt(math.log10(n) / n)
    m_hat = max_lag - k_n
    for m in range(1, max_lag - k_n + 1):
        if all(abs(rho[m + k]) < threshold for k in range(1, k_n + 1)):
            m_hat = m
            break
    big_m = min(2 * m_hat, max_lag)
    centered = x - x.mean()
    cov = np.array([float(np.dot(centered[: n - k], centered[k:])) / n for k in range(big_m + 1)])

    def flat_top(t: float) -> float:
        t = abs(t)
        return 1.0 if t <= 0.5 else 2 * (1 - t) if t <= 1 else 0.0

    weights = [flat_top(k / big_m) if big_m else 1.0 for k in range(big_m + 1)]
    g = sum(2 * weights[k] * k * cov[k] for k in range(1, big_m + 1))
    long_run = cov[0] + 2 * sum(weights[k] * cov[k] for k in range(1, big_m + 1))
    if long_run <= 0:
        return 1.0
    b = (2 * g * g / (2 * long_run * long_run)) ** (1 / 3) * n ** (1 / 3)
    return float(min(max(b, 1.0), n / 3))


def effective_sample_size(x: Array) -> float:
    """n / (1 + 2 Σ ρ_k), summing Geyer's initial positive sequence of autocorrelation pairs."""
    n = len(x)
    if n < 3:
        return float(n)
    rho = autocorrelation(x, min(n - 1, 200))
    total = 0.0
    for k in range(1, len(rho) - 1, 2):
        pair = rho[k] + rho[k + 1]
        if pair <= 0:
            break
        total += pair
    return float(n / max(1.0, 1 + 2 * total))


# ── bootstrap ─────────────────────────────────────────────────────────────────────────────────────────
_MAX_CELLS = 2_000_000          # resample matrix elements per chunk (~16 MB of indices) — laptop-sized


def stationary_indices(n: int, *, resamples: int, block: float, rng: np.random.Generator) -> Iterator[NDArray[np.int64]]:
    """Yield (chunk, n) index matrices of Politis–Romano stationary-bootstrap resamples (geometric blocks, wrap)."""
    p = 1.0 / block
    per_chunk = max(1, _MAX_CELLS // max(n, 1))
    index = np.arange(n)
    done = 0
    while done < resamples:
        rows = min(per_chunk, resamples - done)
        starts = rng.integers(0, n, size=(rows, n))
        new_block = rng.random((rows, n)) < p
        new_block[:, 0] = True
        last_start = np.maximum.accumulate(np.where(new_block, index, 0), axis=1)
        origin = np.take_along_axis(starts, last_start, axis=1)
        yield (origin + index - last_start) % n
        done += rows


def stationary_bootstrap_means(x: Array, *, resamples: int, seed: int, mean_block: float | None = None) -> Array:
    """Means of stationary-bootstrap resamples of ``x``."""
    if len(x) == 0:
        raise ValueError("empty series")
    block = block_length(x) if mean_block is None else mean_block
    rng = np.random.default_rng(seed)
    return np.concatenate([x[idx].mean(axis=1) for idx in stationary_indices(len(x), resamples=resamples,
                                                                              block=block, rng=rng)])


@dataclass(frozen=True)
class Interval:
    estimate: float
    lower: float
    upper: float


def mean_interval(x: Array, *, confidence: float = 0.95, one_sided: bool = False, resamples: int = 2000,
                  seed: int = 0) -> Interval:
    """Percentile stationary-bootstrap interval for the mean; one-sided puts all of alpha in the lower tail."""
    means = stationary_bootstrap_means(x, resamples=resamples, seed=seed)
    alpha = 1 - confidence
    if one_sided:
        return Interval(float(x.mean()), float(np.quantile(means, alpha)), math.inf)
    return Interval(float(x.mean()), float(np.quantile(means, alpha / 2)), float(np.quantile(means, 1 - alpha / 2)))


# ── risk measures ─────────────────────────────────────────────────────────────────────────────────────
def max_drawdown(returns: Array) -> float:
    """Largest peak-to-trough decline of the compounded equity curve, as a fraction."""
    equity = np.cumprod(1 + returns)
    peaks = np.maximum.accumulate(np.concatenate([[1.0], equity]))[1:]
    return float(np.max(1 - equity / peaks)) if len(returns) else 0.0


def bootstrap_drawdown_quantile(returns: Array, *, quantile: float = 0.95, resamples: int = 1000, seed: int = 0) -> float:
    """Quantile of the maximum drawdown across stationary-bootstrap resamples of the return series."""
    n = len(returns)
    if n == 0:
        return 0.0
    rng = np.random.default_rng(seed)
    out = []
    for idx in stationary_indices(n, resamples=resamples, block=block_length(returns), rng=rng):
        equity = np.cumprod(1 + returns[idx], axis=1)
        peaks = np.maximum.accumulate(np.concatenate([np.ones((len(idx), 1)), equity], axis=1), axis=1)[:, 1:]
        out.append(np.max(1 - equity / peaks, axis=1))
    return float(np.quantile(np.concatenate(out), quantile))


def expected_shortfall(returns: Array, level: float = 0.95) -> float:
    """Mean of the worst (1 - level) share of returns (a negative number for losses)."""
    if len(returns) == 0:
        return 0.0
    cutoff = np.quantile(returns, 1 - level)
    tail = returns[returns <= cutoff]
    return float(tail.mean())


def wilson_interval(successes: int, n: int, z: float = 1.959963984540054) -> Interval:
    if n == 0:
        return Interval(0.0, 0.0, 1.0)
    p = successes / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return Interval(p, max(0.0, centre - half), min(1.0, centre + half))


# ── deterioration ─────────────────────────────────────────────────────────────────────────────────────
def cusum_alarm(returns: Array, *, reference_mean: float, sigma: float, k: float = 0.5, h: float = 5.0) -> int | None:
    """One-sided lower CUSUM (standardised): index of the first alarm, or None.

    Tuned for a downward shift of about 2k sigma. The matrix uses k = 0.12, h = 20: in-control ARL ≈ 5,400
    trades, and a 0.24 σ drop (e.g. +4% → -2% at σ = 25%) is flagged after ≈ 176 trades on average.
    """
    if sigma <= 0:
        raise ValueError("sigma must be positive")
    s = 0.0
    for i, value in enumerate(returns):
        s = min(0.0, s + (value - reference_mean) / sigma + k)
        if s < -h:
            return i
    return None


def page_hinkley_alarm(returns: Array, *, delta: float, threshold: float) -> int | None:
    """Page–Hinkley test for a decrease of the mean: index of the first alarm, or None."""
    mean, cumulative, maximum = 0.0, 0.0, 0.0
    for i, value in enumerate(returns, start=1):
        mean += (value - mean) / i
        cumulative += value - mean + delta
        maximum = max(maximum, cumulative)
        if maximum - cumulative > threshold:
            return i - 1
    return None


# ── multiple testing, calibration, folds ──────────────────────────────────────────────────────────────
def holm(p_values: Sequence[float]) -> list[float]:
    """Holm step-down adjusted p-values, in the input order."""
    m = len(p_values)
    order = sorted(range(m), key=lambda i: p_values[i])
    adjusted = [0.0] * m
    running = 0.0
    for rank, i in enumerate(order):
        running = max(running, min(1.0, (m - rank) * p_values[i]))
        adjusted[i] = running
    return adjusted


def brier_score(probabilities: Array, outcomes: Array) -> float:
    return float(np.mean((probabilities - outcomes) ** 2))


def expected_calibration_error(probabilities: Array, outcomes: Array, bins: int = 10) -> float:
    """Equal-width bins on [0, 1]; the last bin is closed. Weighted mean |accuracy - confidence|."""
    edges = np.linspace(0, 1, bins + 1)
    index = np.clip(np.digitize(probabilities, edges[1:-1], right=False), 0, bins - 1)
    total = 0.0
    for b in range(bins):
        mask = index == b
        if mask.any():
            total += mask.sum() / len(probabilities) * abs(outcomes[mask].mean() - probabilities[mask].mean())
    return float(total)


@dataclass(frozen=True)
class Fold:
    train: tuple[int, int]       # [start, end) indices into time-ordered samples
    test: tuple[int, int]


def walk_forward_folds(times: Sequence[float], *, folds: int, embargo_seconds: float,
                       max_hold_seconds: float, holdout_fraction: float = 0.2) -> tuple[list[Fold], tuple[int, int]]:
    """Expanding-window folds with purge (training labels may not reach the test start) and embargo ≥ max hold.

    ``times`` are entry times (seconds) of time-ordered samples, each labelled over at most ``max_hold_seconds``.
    The final ``holdout_fraction`` is returned separately as the untouched segment.
    """
    if embargo_seconds < max_hold_seconds:
        raise ValueError("embargo must be at least the maximum holding period")
    n = len(times)
    holdout_start = int(n * (1 - holdout_fraction))
    usable = holdout_start
    size = usable // (folds + 1)
    out = []
    for f in range(1, folds + 1):
        test_start, test_end = f * size, min((f + 1) * size, usable)
        cutoff = times[test_start] - max(embargo_seconds, max_hold_seconds)
        train_end = 0
        while train_end < test_start and times[train_end] + max_hold_seconds <= cutoff:
            train_end += 1
        if train_end > 0 and test_end > test_start:
            out.append(Fold((0, train_end), (test_start, test_end)))
    return out, (holdout_start, n)
