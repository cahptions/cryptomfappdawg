"""The odds of an option, three ways (docs/STOCKS_PLAYS.md).

* **Market**: what the option's own price implies (lognormal at its implied volatility, drift = the risk-free rate).
  This is a price, not a forecast.
* **History**: how often this stock really moved that far over that many sessions, from its own daily closes,
  rescaled to today's realized volatility (keeps its fat tails and skew, drops its old trend).
* **KESTREL**: a forecast. Volatility blends implied and realized variance (implied volatility usually overstates the
  move that follows); the drift is tilted by the committee's view only as far as the committee's measured
  information coefficient allows (Grinold: expected move = IC × z-score × volatility), never beyond the horizon the IC
  was measured on. Then it is calibrated against how past plays actually finished (``Calibration``).

Every probability is the chance of the stated event at expiration unless named otherwise ("touch" = the price reaches
the level at any time before expiration).
"""

from __future__ import annotations

import math
from bisect import bisect_left
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import pairwise

from desk.stocks.options import norm_cdf

MODEL_VERSION = 1                # bump when the probability model changes: calibration restarts for it
TRADING_YEAR = 252
IV_WEIGHT = 0.7                  # forecast variance = 0.7 × implied² + 0.3 × realized²
IC_HORIZON_YEARS = 21 / 252      # the committee's information coefficient is measured over one month
IC_SHRINK_MONTHS = 24            # an IC from n independent months counts n / (n + 24) of itself
MIN_HISTORY_WINDOWS = 120
CALIBRATION_PRIOR = 20           # pseudo-observations pulling each bucket toward its own prediction
MIN_CALIBRATION = 30             # resolved plays before any calibration is applied


def above(s: float, level: float, t: float, sigma: float, mu: float) -> float:
    """P(S_T > level) for a lognormal price with annual drift ``mu`` and volatility ``sigma``."""
    if level <= 0:
        return 1.0
    if t <= 0 or sigma <= 0:
        return 1.0 if s > level else 0.0
    return norm_cdf((math.log(s / level) + (mu - 0.5 * sigma * sigma) * t) / (sigma * math.sqrt(t)))


def finishes_beyond(kind: str, s: float, level: float, t: float, sigma: float, mu: float) -> float:
    """P(a call's underlying ends above ``level``) or P(a put's ends below it)."""
    p = above(s, level, t, sigma, mu)
    return p if kind == "call" else 1.0 - p


def touch(s: float, level: float, t: float, sigma: float, mu: float) -> float:
    """P(the price reaches ``level`` at any time before ``t``) for geometric Brownian motion (reflection formula)."""
    if math.isclose(level, s):
        return 1.0
    if t <= 0 or sigma <= 0 or level <= 0:
        return 0.0
    a = math.log(level / s)
    nu = mu - 0.5 * sigma * sigma
    sq = sigma * math.sqrt(t)
    if a > 0:      # up-and-touch
        p = norm_cdf((-a + nu * t) / sq) + math.exp(min(2 * nu * a / sigma ** 2, 700)) * norm_cdf((-a - nu * t) / sq)
    else:          # down-and-touch
        p = norm_cdf((a - nu * t) / sq) + math.exp(min(2 * nu * a / sigma ** 2, 700)) * norm_cdf((a + nu * t) / sq)
    return max(0.0, min(1.0, p))


def expected_payoff(kind: str, s: float, k: float, t: float, sigma: float, mu: float) -> float:
    """E[payoff at expiration] per share under the lognormal (undiscounted)."""
    if t <= 0 or sigma <= 0:
        return max(0.0, s - k) if kind == "call" else max(0.0, k - s)
    sq = sigma * math.sqrt(t)
    d1 = (math.log(s / k) + (mu + 0.5 * sigma * sigma) * t) / sq
    d2 = d1 - sq
    forward = s * math.exp(mu * t)
    if kind == "call":
        return forward * norm_cdf(d1) - k * norm_cdf(d2)
    return k * norm_cdf(-d2) - forward * norm_cdf(-d1)


# ── history ──────────────────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class History:
    """A stock's standardized n-session log returns (mean removed, unit variance)."""
    z: tuple[float, ...]
    sessions: int

    @classmethod
    def build(cls, closes: Sequence[float], sessions: int) -> History | None:
        pts = [math.log(c) for c in closes if c and c > 0 and not math.isnan(c)]
        if sessions < 1 or len(pts) - sessions < MIN_HISTORY_WINDOWS:
            return None
        moves = [pts[i + sessions] - pts[i] for i in range(len(pts) - sessions)]
        mean = sum(moves) / len(moves)
        sd = math.sqrt(sum((m - mean) ** 2 for m in moves) / (len(moves) - 1))
        if sd <= 0:
            return None
        return cls(tuple(sorted((m - mean) / sd for m in moves)), sessions)

    def beyond(self, kind: str, s: float, level: float, sigma_annual: float) -> float:
        """Share of history's moves, at today's volatility, that would end beyond ``level``."""
        if level <= 0:
            return 1.0 if kind == "call" else 0.0
        scale = sigma_annual * math.sqrt(self.sessions / TRADING_YEAR)
        if scale <= 0:
            return 1.0 if (s > level) == (kind == "call") else 0.0
        threshold = math.log(level / s) / scale
        below = bisect_left(self.z, threshold) / len(self.z)
        return 1.0 - below if kind == "call" else below


# ── the forecast ─────────────────────────────────────────────────────────────────────────────────
def forecast_sigma(iv: float | None, rv: float | None) -> float | None:
    if iv and rv:
        return math.sqrt(IV_WEIGHT * iv * iv + (1 - IV_WEIGHT) * rv * rv)
    return iv or rv


def tilt(ic: float | None, independent_months: float, z: float | None, sigma: float, t: float) -> float:
    """Annual drift added by the committee's view: IC (shrunk by its evidence) × z × the move over at most the IC's
    horizon, spread over ``t``. Zero without a positive measured IC."""
    if not ic or ic <= 0 or z is None or t <= 0:
        return 0.0
    used = ic * independent_months / (independent_months + IC_SHRINK_MONTHS)
    z = max(-3.0, min(3.0, z))
    return used * z * sigma * math.sqrt(min(t, IC_HORIZON_YEARS)) / t


@dataclass(frozen=True)
class Calibration:
    """Predicted-probability buckets (tenths) → how often plays in them actually finished in the money, shrunk toward
    the prediction by ``CALIBRATION_PRIOR`` pseudo-observations. Empty until ``MIN_CALIBRATION`` plays resolve."""
    buckets: tuple[tuple[float, int, float], ...] = ()      # (mean predicted, resolved, observed hit rate)

    @classmethod
    def learn(cls, predicted: Sequence[float], outcomes: Sequence[bool]) -> Calibration:
        if len(predicted) < MIN_CALIBRATION:
            return cls()
        bins: dict[int, list[tuple[float, bool]]] = {}
        for p, hit in zip(predicted, outcomes, strict=True):
            bins.setdefault(min(int(p * 10), 9), []).append((p, hit))
        out = []
        for b in sorted(bins):
            rows = bins[b]
            mean_p = sum(p for p, _ in rows) / len(rows)
            out.append((mean_p, len(rows), sum(h for _, h in rows) / len(rows)))
        return cls(tuple(out))

    @property
    def resolved(self) -> int:
        return sum(n for _, n, _ in self.buckets)

    def apply(self, p: float) -> float:
        """Interpolate the shrunk observed rate between bucket centres; identity where there is no evidence."""
        if not self.buckets:
            return p
        points = [(mean_p, (n * rate + CALIBRATION_PRIOR * mean_p) / (n + CALIBRATION_PRIOR))
                  for mean_p, n, rate in self.buckets]
        if p <= points[0][0]:
            return max(0.0, min(1.0, p + points[0][1] - points[0][0]))
        if p >= points[-1][0]:
            return max(0.0, min(1.0, p + points[-1][1] - points[-1][0]))
        for (x0, y0), (x1, y1) in pairwise(points):
            if x0 <= p <= x1:
                w = 0.0 if x1 == x0 else (p - x0) / (x1 - x0)
                return max(0.0, min(1.0, y0 + w * (y1 - y0)))
        return p


@dataclass(frozen=True)
class Odds:
    itm_market: float
    itm_history: float | None
    itm_kestrel: float
    itm_kestrel_raw: float
    profit_kestrel: float
    touch_kestrel: float
    expected_value: float           # per contract, after the entry cost (long) or credit (short), before fees
    sigma: float
    drift: float

    def as_json(self) -> dict[str, float | None]:
        return {k: (None if v is None else round(v, 4)) for k, v in self.__dict__.items()}


def odds(*, kind: str, action: str, s: float, k: float, t: float, iv: float, rv: float | None, r: float, premium: float,
         history: History | None, ic: float | None, ic_months: float, z: float | None,
         calibration: Calibration | None = None) -> Odds:
    """All three views of one contract. ``premium`` is per share: paid (``action`` "buy") or received ("sell")."""
    sigma = forecast_sigma(iv, rv) or iv
    mu = r + tilt(ic, ic_months, z, sigma, t)
    itm_market = finishes_beyond(kind, s, k, t, iv, r)
    raw = finishes_beyond(kind, s, k, t, sigma, mu)
    cal = (calibration or Calibration()).apply(raw)
    breakeven = k + premium if kind == "call" else k - premium
    reach = finishes_beyond(kind, s, breakeven, t, sigma, mu)
    payoff = expected_payoff(kind, s, k, t, sigma, mu)
    buy = action == "buy"
    return Odds(itm_market, None if history is None else history.beyond(kind, s, k, rv or iv),
                cal, raw, reach if buy else 1.0 - reach, touch(s, k, t, sigma, mu),
                ((payoff - premium) if buy else (premium - payoff)) * 100, sigma, mu)


def brier(predicted: Sequence[float], outcomes: Sequence[bool]) -> float | None:
    if not predicted:
        return None
    return sum((p - float(o)) ** 2 for p, o in zip(predicted, outcomes, strict=True)) / len(predicted)


__all__ = ["Calibration", "History", "Odds", "above", "brier", "expected_payoff", "finishes_beyond", "forecast_sigma",
           "odds", "tilt", "touch"]
