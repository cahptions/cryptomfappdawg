"""The option chain's own distribution of the stock at an expiry (docs/STOCKS_PLAYS.md, "The chain's own skew").

One expiry's quotes say more than one implied volatility: out-of-the-money puts usually cost more volatility than
calls (skew), and both wings more than the money (smile). A lognormal at one volatility ignores that, so it
understates crashes and, under the usual equity skew, misprices the chance of finishing past an out-of-the-money
strike. This module turns the chain into the terminal distribution its prices imply and uses it twice:

* **Market** view: the chance the chain itself prices (risk-neutral, skew and fat tails included).
* **KESTREL** forecast: the same shape, its spread rescaled by KESTREL's volatility blend (``odds.IV_WEIGHT`` of
  implied variance, the rest realized) and shifted to KESTREL's drift (the committee tilt, ``odds.tilt``).

**Method, and why.** Breeden–Litzenberger: the chance the stock finishes above K is minus the forward value of the
call price's slope in strike, P(S_T > K) = −e^{rT} ∂C/∂K. Differencing quoted prices directly needs dense, tight
strikes and amplifies every bid/ask error (raw second differences routinely give negative densities on a retail
chain). So the smile is fitted first and differentiated analytically: implied volatility as a quadratic in
log-moneyness x = ln(K/F), by weighted least squares (weights 1/spread, out-of-the-money quotes), which gives

    P(S_T > K) = N(d2) − φ(d2) · √T · dσ/dx      (the Black-Scholes digital plus its skew correction).

A quadratic has three linear parameters: no optimiser, no local minima, stable from five strikes, and it captures the
two features that matter for chances (the skew's slope and the wings' curvature). SVI or splines fit more shapes but
need many clean strikes and a non-linear fit that can fail silently. Outside the quoted strikes the smile continues
along its edge tangent (bounded to 0.3–3× the money's volatility), and the result is checked: the chance must fall as
the strike rises (no negative probabilities) beyond a small repaired tolerance, otherwise the expiry falls back.

**Fallback.** Too few usable strikes (five, two either side of the money, each quoted within 30% of its mid), a ragged
fit, or a fit implying negative probabilities: the expiry uses today's lognormal at each contract's own implied
volatility (``odds``), and every play says which basis it used and why.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np

from desk.stocks import odds as od

Array = np.ndarray
MIN_STRIKES = 5             # usable strikes in one expiry, ...
MIN_SIDE = 2                # ... at least this many either side of the forward
MAX_SPREAD = 0.30           # (ask − bid) / mid of a strike used in the fit
MAX_Z = 4.0                 # strikes further than this many standard deviations are left out of the fit
MAX_RAGGED = 0.15           # weighted RMS residual of the fit, as a share of the money's volatility
MIN_IV_ERROR = 0.002       # a quote pins its volatility no closer than 0.2 points
BANDWIDTH_SD = 0.5         # the smile's kernel: half a standard deviation of the move, ...
BANDWIDTH_STRIKES = 1.5    # ... and at least one and a half strike spacings
MONOTONE_TOLERANCE = 0.02   # largest repaired rise in the chance of finishing above as the strike rises
GRID = 1201                 # log-return points the distribution is tabulated on
SPAN_SD = 6.0               # ... spanning this many standard deviations either side (at least ±0.5, at most ±4)
# Probabilities at which the KESTREL distribution's quantiles are stored with a play (the plan's Monte Carlo samples
# its terminal price from them): dense in the tails, where lottos and far targets live.
QUANTILES = (0.0005, 0.001, 0.0025, 0.005, 0.01, 0.02, 0.035, 0.05, 0.075, 0.1, 0.15, 0.2, 0.25, 0.3, 0.35, 0.4, 0.45,
             0.5, 0.55, 0.6, 0.65, 0.7, 0.75, 0.8, 0.85, 0.9, 0.925, 0.95, 0.965, 0.98, 0.99, 0.995, 0.9975, 0.999,
             0.9995)


def ncdf(x: Array) -> Array:
    """Standard normal CDF, vectorized (Abramowitz–Stegun 7.1.26 erf: |error| < 1.5e-7)."""
    z = np.abs(x) / math.sqrt(2.0)
    t = 1.0 / (1.0 + 0.3275911 * z)
    poly = ((((1.061405429 * t - 1.453152027) * t + 1.421413741) * t - 0.284496736) * t + 0.254829592) * t
    out: Array = 0.5 * (1.0 + np.sign(x) * (1.0 - poly * np.exp(-z * z)))
    return out


def npdf(x: Array) -> Array:
    out: Array = np.exp(-0.5 * x * x) / math.sqrt(2 * math.pi)
    return out


# ── the smile ────────────────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Smile:
    """Implied volatility against log-moneyness x = ln(K/F) for one expiry, tabulated on ``xs`` across the quoted
    strikes (a kernel-weighted local quadratic fit, ``fit_smile``); beyond them it continues along the edge tangent,
    bounded to [floor, cap]."""
    xs: Array
    sig: Array
    slope: Array
    floor: float
    cap: float
    atm: float
    strikes: int
    rms: float

    def vol(self, x: Array) -> tuple[Array, Array]:
        """(σ(x), dσ/dx)."""
        inner = np.clip(x, self.xs[0], self.xs[-1])
        level = np.interp(inner, self.xs, self.sig)
        slope = np.interp(inner, self.xs, self.slope)
        sigma = level + slope * (x - inner)
        clipped = (sigma < self.floor) | (sigma > self.cap)
        return np.clip(sigma, self.floor, self.cap), np.where(clipped, 0.0, slope)


def _local_quadratic(x: Array, y: Array, w: Array, at: Array, h: float) -> Array:
    """Kernel-weighted (Gaussian, bandwidth ``h``) local quadratic regression of ``y`` on ``x``, evaluated at ``at``.
    The curvature carries a small ridge, so a neighbourhood with too few strikes falls back toward a local line."""
    d = x[None, :] - at[:, None]
    kern = w[None, :] * np.exp(-0.5 * (d / h) ** 2)
    m = [np.sum(kern * d ** p, axis=1) for p in range(5)]
    v = [np.sum(kern * d ** p * y[None, :], axis=1) for p in range(3)]
    a = np.stack([np.stack([m[0], m[1], m[2]], -1), np.stack([m[1], m[2], m[3]], -1),
                  np.stack([m[2], m[3], m[4] + 0.01 * m[0] * h ** 4], -1)], -2)
    out: Array = np.linalg.solve(a, np.stack(v, -1)[..., None])[..., 0, 0]
    return out


def fit_smile(k: Array, iv: Array, spread: Array, call: Array, mid: Array, s: float, t: float,
              r: float) -> tuple[Smile | None, str]:
    """Fit one expiry's smile from its contracts (strike, implied volatility, relative spread, call?, mid). Returns the
    smile, or None and the reason the expiry falls back to the lognormal.

    Out-of-the-money quotes only (puts below the forward, calls above): an in-the-money option's price is mostly
    intrinsic value, so its spread swamps the little volatility it carries. Each quote is weighted by how precisely it
    pins its volatility, 1 / (half its spread ÷ its vega), so a wide far-wing quote counts for less than a tight one
    near the money."""
    forward = s * math.exp(r * t)
    otm = np.where(call, k >= forward, k < forward)
    usable = otm & np.isfinite(iv) & (iv > 0.01) & (iv < 5.0) & (spread <= MAX_SPREAD) & (k > 0) & (mid > 0)
    chosen: dict[float, int] = {}
    for i in np.where(usable)[0]:                       # one quote per strike: the tighter
        j = chosen.get(float(k[i]))
        if j is None or spread[i] < spread[j]:
            chosen[float(k[i])] = int(i)
    rows = np.array(sorted(chosen.values(), key=lambda i: float(k[i])), dtype=int)
    if len(rows):
        x = np.log(k[rows] / forward)
        rows = rows[np.abs(x / (iv[rows] * math.sqrt(t))) <= MAX_Z]
    below = int(np.sum(k[rows] < forward)) if len(rows) else 0
    above = len(rows) - below
    if len(rows) < MIN_STRIKES or below < MIN_SIDE or above < MIN_SIDE:
        return None, (f"only {len(rows)} usable strikes ({below} below and {above} above the money; needs "
                      f"{MIN_STRIKES}, {MIN_SIDE} either side, out of the money and quoted within {MAX_SPREAD:.0%} "
                      "of the mid)")
    x, y = np.log(k[rows] / forward), iv[rows]
    sq = y * math.sqrt(t)
    vega = s * npdf((-x + 0.5 * sq * sq) / sq) * math.sqrt(t)
    error = np.maximum(0.5 * spread[rows] * mid[rows] / np.maximum(vega, 1e-9), MIN_IV_ERROR)
    w = 1.0 / error ** 2
    near = np.argsort(np.abs(x))[:3]
    h = max(BANDWIDTH_SD * float(np.median(y[near])) * math.sqrt(t), BANDWIDTH_STRIKES * float(np.median(np.diff(x))))
    xs = np.linspace(float(x[0]), float(x[-1]), 241)
    sig = _local_quadratic(x, y, w, xs, h)
    if not np.all(np.isfinite(sig)) or float(sig.min()) <= 0.01:
        return None, "the fitted smile has no positive volatility somewhere across the strikes"
    atm = float(np.interp(0.0, xs, sig))
    residual = y - np.interp(x, xs, sig)
    rms = float(np.sqrt(np.sum(w * residual * residual) / np.sum(w)))
    if rms > MAX_RAGGED * atm:
        return None, (f"the quotes are too ragged to fit a smile (residual {rms * 100:.1f} volatility points against "
                      f"{atm:.0%} at the money)")
    return Smile(xs, sig, np.gradient(sig, xs), 0.3 * atm, 3.0 * max(atm, float(y.max())), atm, len(rows),
                 rms), "fitted"


# ── the distribution ─────────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Distribution:
    """The stock's log-return U = ln(S_T / S) at one expiry, tabulated: ascending points ``u`` and the chance of
    finishing above each, ``survival`` (non-increasing). The density is uniform between points; whatever lies beyond
    the outermost points sits on them."""
    u: Array
    survival: Array
    _mass: Array = field(init=False, repr=False, compare=False)
    _mid: Array = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        inner = self.survival[:-1] - self.survival[1:]
        mass = np.concatenate(([1.0 - self.survival[0]], inner, [self.survival[-1]]))
        mid = np.concatenate(([self.u[0]], 0.5 * (self.u[:-1] + self.u[1:]), [self.u[-1]]))
        object.__setattr__(self, "_mass", mass)
        object.__setattr__(self, "_mid", mid)

    def above(self, s: float, level: Array | float) -> Array:
        """P(S_T > level), vectorized over ``level``."""
        level = np.asarray(level, dtype=float)
        x = np.log(np.maximum(level, 1e-12) / s)
        out: Array = np.where(level <= 0, 1.0, np.interp(x, self.u, self.survival, left=1.0, right=0.0))
        return out

    def beyond(self, kind: str, s: float, level: float) -> float:
        p = float(self.above(s, level))
        return p if kind == "call" else 1.0 - p

    def mean_log(self) -> float:
        return float(np.sum(self._mass * self._mid))

    def expected_payoff(self, call: Array | bool, s: float, k: Array | float) -> Array:
        """E[payoff at expiry] per share, undiscounted, vectorized over strikes: E[(S−K)+] = ∫_K^∞ P(S_T > y) dy and
        E[(K−S)+] = E[(S−K)+] − E[S_T] + K (put–call parity holds exactly on the table)."""
        k = np.asarray(k, dtype=float)
        y = s * np.exp(self.u)
        # ∫_{y_i}^{y_last} P dy, by trapezoid on the dense grid
        pieces = 0.5 * (self.survival[:-1] + self.survival[1:]) * np.diff(y)
        tail = np.concatenate((np.cumsum(pieces[::-1])[::-1], [0.0]))
        calls = np.where(k <= y[0], tail[0] + (y[0] - np.maximum(k, 0.0)), np.interp(k, y, tail, right=0.0))
        mean = tail[0] + y[0]
        out: Array = np.where(call, calls, calls - mean + k)
        return out

    def quantiles(self, probabilities: Sequence[float] = QUANTILES) -> list[float]:
        cdf = 1.0 - self.survival
        return [round(float(v), 5) for v in np.interp(np.asarray(probabilities), cdf, self.u)]

    def reshaped(self, scale: float, mu: float, t: float) -> Distribution:
        """The same shape with its spread multiplied by ``scale`` and centred so E[S_T] = S·e^{μT} (a shift of the
        log-return, as the committee tilt shifts the lognormal's drift)."""
        centre = self.mean_log()
        growth = float(np.sum(self._mass * np.exp(scale * (self._mid - centre))))
        shift = mu * t - math.log(growth)
        return Distribution(scale * (self.u - centre) + shift, self.survival)


def implied(smile: Smile, s: float, t: float, r: float) -> tuple[Distribution | None, str]:
    """The risk-neutral distribution the smile prices, or None and why it was rejected."""
    sd = smile.atm * math.sqrt(t)
    span = min(4.0, max(0.5, SPAN_SD * sd))
    u = np.linspace(-span, span, GRID)
    x = u - r * t
    sigma, slope = smile.vol(x)
    sq = sigma * math.sqrt(t)
    d2 = (-x - 0.5 * sigma * sigma * t) / sq
    raw = ncdf(d2) - npdf(d2) * math.sqrt(t) * slope
    clipped = np.clip(raw, 0.0, 1.0)
    repaired = np.minimum.accumulate(clipped)
    worst = float(np.max(clipped - repaired))
    if worst > MONOTONE_TOLERANCE:
        return None, (f"the fitted smile implies a negative probability (the chance of finishing above rises by "
                      f"{worst:.1%} somewhere)")
    return Distribution(u, repaired), "fitted"


def lognormal(sigma: float, mu: float, t: float) -> Distribution:
    """The lognormal tabulated like the chain's (used to compare and to test; plays fall back to ``odds``' formulas)."""
    sd = sigma * math.sqrt(t)
    span = min(4.0, max(0.5, SPAN_SD * sd))
    u = np.linspace(-span, span, GRID)
    return Distribution(u, 1.0 - ncdf((u - (mu - 0.5 * sigma * sigma) * t) / sd))


# ── one expiry's views ───────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Expiry:
    """One expiry's basis: the chain's own distribution (``market``) and KESTREL's (``kestrel``: the chain's shape at
    KESTREL's volatility and drift), or the lognormal fallback with its reason."""
    basis: str                      # "chain" | "lognormal"
    reason: str
    market: Distribution | None = None
    kestrel: Distribution | None = None
    atm_iv: float | None = None
    sigma: float | None = None      # KESTREL's volatility at this expiry (the money's IV blended with realized)
    drift: float | None = None      # KESTREL's annual drift (risk-free rate + committee tilt)
    strikes: int = 0

    def describe(self) -> dict[str, object]:
        out: dict[str, object] = {"basis": self.basis, "reason": self.reason}
        if self.basis == "chain":
            out.update({"strikes": self.strikes, "atm_iv": None if self.atm_iv is None else round(self.atm_iv, 4)})
        return out


def expiry(k: Array, iv: Array, spread: Array, call: Array, mid: Array, *, s: float, t: float, r: float, rv: float | None,
           ic: float | None, ic_months: float, z: float | None) -> Expiry:
    """The basis for one expiry of one stock: fit, build, check; else the lognormal fallback and why."""
    smile, why = fit_smile(k, iv, spread, call, mid, s, t, r)
    if smile is None:
        return Expiry("lognormal", why)
    market, why = implied(smile, s, t, r)
    if market is None:
        return Expiry("lognormal", why)
    sigma = od.forecast_sigma(smile.atm, rv) or smile.atm
    mu = r + od.tilt(ic, ic_months, z, sigma, t)
    kestrel = market.reshaped(sigma / smile.atm, mu, t)
    return Expiry("chain", f"the chain's own smile across {smile.strikes} strikes", market, kestrel, smile.atm, sigma,
                  mu, smile.strikes)


def chance_odds(e: Expiry, *, kind: str, action: str, s: float, k: float, t: float, premium: float,
                history: od.History | None, rv: float | None, iv: float,
                calibration: od.Calibration | None = None) -> od.Odds:
    """``odds.odds`` on the chain's distributions: market and KESTREL from the smile; history as before; touch stays
    the reflection formula at KESTREL's volatility and drift (a path quantity the terminal distribution does not
    give)."""
    assert e.market is not None and e.kestrel is not None and e.sigma is not None and e.drift is not None
    itm_market = e.market.beyond(kind, s, k)
    raw = e.kestrel.beyond(kind, s, k)
    cal = (calibration or od.Calibration()).apply(raw)
    breakeven = k + premium if kind == "call" else k - premium
    reach = e.kestrel.beyond(kind, s, breakeven)
    payoff = float(e.kestrel.expected_payoff(kind == "call", s, k))
    buy = action == "buy"
    return od.Odds(itm_market, None if history is None else history.beyond(kind, s, k, rv or iv), cal, raw,
                   reach if buy else 1.0 - reach, od.touch(s, k, t, e.sigma, e.drift),
                   ((payoff - premium) if buy else (premium - payoff)) * 100, e.sigma, e.drift)


def sample(quantiles: Sequence[float], uniforms: Array) -> Array:
    """Log-returns drawn from stored quantiles (``QUANTILES``) by inverse-CDF interpolation; draws beyond the outermost
    stored probabilities take the outermost quantile."""
    out: Array = np.interp(uniforms, np.asarray(QUANTILES), np.asarray(quantiles, dtype=float))
    return out


__all__ = ["GRID", "MIN_STRIKES", "QUANTILES", "Distribution", "Expiry", "Smile", "chance_odds", "expiry",
           "fit_smile", "implied", "lognormal", "ncdf", "sample"]
