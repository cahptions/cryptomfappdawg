"""C1_BRC, benchmark-residual continuation: a pure calculation (research/council/STRATEGIES.md §3).

From 306 consecutive closed 15-minute prices: 288 training returns, then 16 signal returns, then 1 skipped return.
OLS with intercept on training only; the held-out residuals use the frozen coefficients and are scaled by the
training residuals' MAD scale. ``Z`` is a diagnostic, not a t statistic, p-value, probability or expected return.
Decimal arithmetic in a fixed context, so the same inputs give the same digits on every machine.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, localcontext
from itertools import pairwise
from typing import Any

from desk.council.specs import COMMON, decimal

PRECISION = 34
MAD_SCALE = Decimal("1.4826")


def log_returns(prices: list[Decimal]) -> list[Decimal]:
    with localcontext() as ctx:
        ctx.prec = PRECISION
        return [(b / a).ln() for a, b in pairwise(prices)]


def median(values: list[Decimal]) -> Decimal:
    ordered = sorted(values)
    mid = len(ordered) // 2
    return ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2


def mad_scale(values: list[Decimal]) -> Decimal:
    centre = median(values)
    return MAD_SCALE * median([abs(v - centre) for v in values])


def ols(x: list[Decimal], y: list[Decimal]) -> tuple[Decimal, Decimal, Decimal] | None:
    """(alpha, beta, sum of squared benchmark deviations); None when the benchmark does not vary."""
    n = Decimal(len(x))
    mean_x, mean_y = sum(x, Decimal(0)) / n, sum(y, Decimal(0)) / n
    sxx = sum(((a - mean_x) ** 2 for a in x), Decimal(0))
    if sxx == 0:
        return None
    beta = sum(((a - mean_x) * (b - mean_y) for a, b in zip(x, y, strict=True)), Decimal(0)) / sxx
    return mean_y - beta * mean_x, beta, sxx


@dataclass(frozen=True)
class BrcResult:
    reasons: tuple[str, ...]              # empty when the calculation is valid
    z: Decimal | None = None
    alpha: Decimal | None = None
    beta: Decimal | None = None
    scale: Decimal | None = None
    skipped_relative_return: Decimal | None = None   # latest return in SOL terms (token/SOL log return)

    @property
    def valid(self) -> bool:
        return not self.reasons


def brc(token_closes: list[Decimal], benchmark_closes: list[Decimal], spec: dict[str, Any]) -> BrcResult:
    n, k, s = int(spec["train_returns"]), int(spec["signal_returns"]), int(spec["skip_returns"])
    if len(token_closes) != n + k + s + 1 or len(benchmark_closes) != len(token_closes):
        raise ValueError("C1 needs exactly train + signal + skip + 1 aligned closes")
    with localcontext() as ctx:
        ctx.prec = PRECISION
        r_i, r_b = log_returns(token_closes), log_returns(benchmark_closes)
        train_i, train_b = r_i[:n], r_b[:n]
        signal_i, signal_b = r_i[n:n + k], r_b[n:n + k]
        skipped = r_i[n + k] - r_b[n + k]
        fit = ols(train_b, train_i)
        if fit is None:
            return BrcResult(("BENCHMARK_VARIANCE_LOW",), skipped_relative_return=skipped)
        alpha, beta, sxx = fit
        reasons = []
        if (sxx / (n - 1)).sqrt() < decimal(COMMON, "min_benchmark_stddev_per_bar"):
            reasons.append("BENCHMARK_VARIANCE_LOW")
        residuals = [b - alpha - beta * a for a, b in zip(train_b, train_i, strict=True)]
        scale = mad_scale(residuals)
        if scale < decimal(COMMON, "min_robust_residual_scale_per_bar"):
            reasons.append("RESIDUAL_SCALE_LOW")
        if not decimal(spec, "beta_min") <= beta <= decimal(spec, "beta_max"):
            reasons.append("BETA_UNSTABLE")
        half = n // 2
        first, second = ols(train_b[:half], train_i[:half]), ols(train_b[half:], train_i[half:])
        if first is None or second is None or abs(first[1] - second[1]) > decimal(spec, "max_half_beta_difference"):
            reasons.append("BETA_UNSTABLE")
        z = None
        if scale > 0:
            z = sum((b - alpha - beta * a for a, b in zip(signal_b, signal_i, strict=True)), Decimal(0)) / (
                scale * Decimal(k).sqrt())
        return BrcResult(tuple(dict.fromkeys(reasons)), z, alpha, beta, scale, skipped)


__all__ = ["BrcResult", "brc", "log_returns", "mad_scale", "median", "ols"]
