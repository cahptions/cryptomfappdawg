"""Every option structure priced from KESTREL's two forecasts against the chain's own prices
(docs/STOCKS_PLAYS.md → What a play is worth).

**The distribution of the stock at expiry.** Built on a fixed grid of probabilities (``GRID`` cells, equal steps in
normal score from −6 to +6), as the log return's quantile function Q(τ):

1. **Today's KESTREL forecast** (unchanged): the chain's own smile shape where ``chain_odds`` fitted one (its skew and
   fat tails, rescaled to KESTREL's volatility and drift), else the lognormal at KESTREL's volatility and drift.
2. **× the volatility surprise** (``vol_surprise``): its spread around its centre is multiplied by the predicted
   volatility multiplier where that horizon is live (1 elsewhere), re-centred so the expected price keeps its drift.
   This is "scale = forecast volatility (HAR-IV × surprise)".
3. **+ the direction model's deviation from no skill** (``direction``), where its horizon is live: the predicted
   standardized quantiles and the baseline's are each turned into a full quantile function (monotone cubic, PCHIP, in
   normal-score space, straight tails beyond the 5th and 95th percentiles). Their medians' difference, × the stock's
   reference scale × √h, shifts the distribution (the expected direction); their shapes, each centred on its median
   and divided by its own standard deviation, differ by a dimensionless amount that is multiplied by the forecast's
   own standard deviation (so the volatility model alone sets the spread). That difference is added to Q(τ), which is
   then sorted. With no skill (the deviation is zero) the chain's skew shape is kept exactly; with skill the chain's
   shape is bent by what the model learned. Where the direction model is not live this step adds nothing.

**Value.** A structure's fair value is its expected payoff at expiry under that distribution, discounted at the
risk-free rate, per share (on a lognormal at volatility σ and drift r this is the Black–Scholes price). Its cost is
the realistic fill (``learner.fills``: the chosen limit's price when the fill model has observations as of the
decision, else the far side: paying the ask, taking the bid) plus fees. Edge = fair value − cost; edge per dollar at
risk; chance of profit = the chance its payoff at expiry beats its cost.

**Direction or volatility?** The structure is re-priced with the direction removed: drift at the risk-free rate (no
committee tilt) and no direction deviation, the volatility (and its surprise) unchanged. That value − cost is the
**volatility part** (options cheap or expensive against how much the stock should move); the rest is the **direction
part**. The two add up to the edge exactly.

**Shadow first** (``gates``): the edge is recorded with every play. It changes a section's ranking only when, on that
section's graded plays, it ranked them better than the current ranking out of sample: the mean over sessions of the
rank correlation between the recorded edge per dollar at risk and the realized net return beats the current ranking's
by a Newey–West t of at least ``GATE_T`` over at least ``GATE_SESSIONS`` sessions (each with at least
``MIN_PER_SESSION`` graded plays). Then the section ranks by the average of the two percentile ranks. The calibrated
chance of profit shown stays ``odds``' unless this distribution proves better calibrated (``calibration`` reports both
Brier scores; switching needs a new ``odds.MODEL_VERSION``).
"""

from __future__ import annotations

import math
import sqlite3
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import numpy as np

from desk.core.hashing import canonical_json, sha256_hex
from desk.stocks.forecast import direction as dr
from desk.stocks.forecast import vol_surprise as vs

Array = np.ndarray
NAME = "stocks_structure_pricing"
VERSION = 1
GRID = 2400
SPAN = 6.0
CHECKPOINTS = (5, 21)                # sessions after the decision for the price ranges along the way
RANGE = (0.05, 0.5, 0.95)
DIRECTION_SHARE = 0.6                # a part with at least this share of |direction| + |volatility| "mostly" drives it
GATE_SESSIONS = 20
MIN_PER_SESSION = 3
GATE_T = 2.0


def content() -> dict[str, Any]:
    return {"version": VERSION, "grid": GRID, "span": SPAN, "checkpoints": list(CHECKPOINTS), "range": list(RANGE),
            "direction_share": DIRECTION_SHARE, "direction": [dr.VERSION, dr.version_hash()],
            "vol_surprise": [vs.VERSION, vs.version_hash()],
            "distribution": "today's KESTREL quantile function (chain smile else lognormal), spread x surprise "
                            "multiplier re-centred to its drift, + direction deviation (median shift x reference "
                            "scale x sqrt(h) + standardized shape difference x the forecast's sd), sorted",
            "interpolation": "PCHIP in normal score between the 5th and 95th percentiles, straight tails",
            "value": "discounted expected payoff at expiry", "cost": "learner.fills chosen limit, else far side; fees",
            "decomposition": "volatility part = value at drift r without direction deviation - cost",
            "gate": {"sessions": GATE_SESSIONS, "min_per_session": MIN_PER_SESSION, "t": GATE_T,
                     "statistic": "per-session Spearman(edge per risk, net return) - Spearman(-rank, net return)",
                     "ranking": "mean of the two percentile ranks"}}


def version_hash() -> str:
    return sha256_hex(canonical_json(content()))[:16]


def _phi(x: Array) -> Array:
    from scipy.special import ndtr

    out: Array = ndtr(x)
    return out


_EDGES = np.linspace(-SPAN, SPAN, GRID + 1)
Z = 0.5 * (_EDGES[:-1] + _EDGES[1:])                    # normal scores of the cells' centres
W = np.diff(_phi(_EDGES))
W[0] += float(_phi(np.array([-SPAN]))[0])               # the far tails sit on the outermost cells
W[-1] += float(_phi(np.array([-SPAN]))[0])
TAU = _phi(Z)


# ── a forecast distribution ──────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Forecast:
    """The log return U = ln(S_T / S) as sorted values ``q`` on the fixed cells (masses ``W``)."""
    q: Array

    @classmethod
    def lognormal(cls, sigma: float, mu: float, t: float) -> Forecast:
        return cls((mu - 0.5 * sigma * sigma) * t + sigma * math.sqrt(t) * Z)

    @classmethod
    def from_table(cls, u: Array, survival: Array) -> Forecast:
        """From ``chain_odds.Distribution``'s table (points and chance of finishing above each)."""
        cdf = 1.0 - np.asarray(survival, dtype=float)
        return cls(np.interp(TAU, cdf, np.asarray(u, dtype=float)))

    def mean(self) -> float:
        return float(W @ self.q)

    def sd(self) -> float:
        m = self.mean()
        return float(math.sqrt(max(float(W @ (self.q - m) ** 2), 0.0)))

    def growth(self) -> float:
        """E[S_T / S]."""
        return float(W @ np.exp(self.q))

    def recentred(self, mu: float, t: float) -> Forecast:
        """Shifted so E[S_T] = S·e^{μt}."""
        return Forecast(self.q + mu * t - math.log(self.growth()))

    def scaled(self, m: float, mu: float, t: float) -> Forecast:
        """Its spread around its centre × ``m``, re-centred to drift ``mu``."""
        c = self.mean()
        return Forecast(c + m * (self.q - c)).recentred(mu, t)

    def expected(self, values: Callable[[Array], Array], s: float) -> float:
        return float(W @ values(s * np.exp(self.q)))

    def chance(self, event: Callable[[Array], Array], s: float) -> float:
        return float(W @ event(s * np.exp(self.q)).astype(float))

    def quantiles(self, probabilities: Sequence[float]) -> list[float]:
        return [float(v) for v in np.interp(np.asarray(probabilities), TAU, self.q)]


def full_quantiles(knots: Sequence[float]) -> Array:
    """A whole quantile function on ``Z`` from values at ``direction.QUANTILES``: PCHIP (monotone) in normal score
    between them, straight lines beyond with the outer segments' slopes."""
    from scipy.interpolate import PchipInterpolator
    from scipy.special import ndtri

    x = ndtri(np.asarray(dr.QUANTILES))
    y = np.maximum.accumulate(np.asarray(knots, dtype=float))      # sorted already; guarded
    inner = PchipInterpolator(x, y)(np.clip(Z, x[0], x[-1]))
    lo = (y[1] - y[0]) / (x[1] - x[0])
    hi = (y[-1] - y[-2]) / (x[-1] - x[-2])
    out: Array = np.where(x[0] > Z, y[0] + lo * (Z - x[0]), np.where(x[-1] < Z, y[-1] + hi * (Z - x[-1]), inner))
    return out


def deviation(dq: dr.Quantiles, sd_target: float) -> Array:
    """The direction model's deviation from no skill, as a log-return shift per cell (see the module docstring)."""
    c, b = full_quantiles(dq.cond), full_quantiles(dq.base)
    mid = dr.QUANTILES.index(0.5)
    mc, mb = dq.cond[mid], dq.base[mid]

    def shape(v: Array, med: float) -> Array:
        sd = math.sqrt(max(float(W @ (v - float(W @ v)) ** 2), 1e-12))
        out: Array = (v - med) / sd
        return out

    out: Array = sd_target * (shape(c, mc) - shape(b, mb)) + dq.sigma_ref * math.sqrt(dq.sessions) * (mc - mb)
    return out


@dataclass(frozen=True)
class Pair:
    """The full forecast at one expiry and the same without direction (the volatility part's basis)."""
    full: Forecast
    neutral: Forecast
    direction_live: bool
    vol_multiplier: float
    vol_live: bool


def pair(base: Forecast, *, t: float, rate: float, drift: float, dq: dr.Quantiles | None,
         correction: vs.Correction | None) -> Pair:
    """Today's forecast ``base`` (drift ``drift``) → (full, neutral): × the surprise multiplier; + the direction
    deviation where live; neutral: drift ``rate`` and no deviation."""
    m = correction.multiplier if correction is not None else 1.0
    scaled = base.scaled(m, drift, t)
    neutral = base.scaled(m, rate, t)
    live = dq is not None and dq.live
    full = Forecast(np.sort(scaled.q + deviation(dq, scaled.sd()))) if live and dq is not None else scaled
    return Pair(full, neutral, live, m, bool(correction is not None and correction.live))


# ── a structure's value ──────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Leg:
    kind: str           # call | put
    strike: float
    sign: int           # +1 bought, −1 sold


def payoff(legs: Sequence[Leg]) -> Callable[[Array], Array]:
    def at(prices: Array) -> Array:
        out = np.zeros_like(prices)
        for leg in legs:
            intrinsic = np.maximum(prices - leg.strike, 0.0) if leg.kind == "call" else np.maximum(leg.strike - prices,
                                                                                                    0.0)
            out = out + leg.sign * intrinsic
        return out
    return at


def value(f: Forecast, s: float, legs: Sequence[Leg], rate: float, t: float) -> float:
    """Discounted expected payoff per share of the position as held (bought +, sold −)."""
    return math.exp(-rate * t) * f.expected(payoff(legs), s)


@dataclass(frozen=True)
class Cost:
    per_share: float            # what the position costs per share at the fill, fees included (a credit is negative)
    fraction: float             # the chosen limit, from the mid (0) to the far side (1)
    chance: float               # its chance of filling within the session
    basis: str                  # "fill model" | "far side"


def cost(action: str, entry: float, legs: int, fill: Any | None, fee_per_contract: float) -> Cost:
    """The realistic entry: ``entry`` is the far-side price per share (a debit for a purchase, a credit for a sale);
    the fill model's chosen limit saves ``fill.saved()`` per share when it has observations."""
    fees = fee_per_contract * legs / 100
    saved = fill.saved() if fill is not None and fill.fraction < 1 else 0.0
    basis = "fill model" if saved > 0 else "far side"
    fraction = fill.fraction if fill is not None else 1.0
    chance = fill.chance if fill is not None else 1.0
    if action == "BUY":
        return Cost(entry - saved + fees, fraction, chance, basis)
    return Cost(-(entry + saved) + fees, fraction, chance, basis)


def describe(edge: float, direction_part: float, vol_part: float, net_vega: float | None) -> tuple[str, str]:
    """(driver, plain sentence) for the play card."""
    if edge <= 0:
        return "none", "No edge at the expected fill: KESTREL's forecast values it below what it costs."
    total = abs(direction_part) + abs(vol_part)
    share = abs(direction_part) / total if total > 0 else 0.0
    cheap = "cheap" if (net_vega or 0.0) >= 0 else "expensive"
    if share >= DIRECTION_SHARE:
        return "direction", "Edge mostly from the expected direction of the stock."
    if share <= 1 - DIRECTION_SHARE:
        return "volatility", f"Edge mostly from options looking {cheap} vs how much the stock should move."
    return "both", (f"Edge from both the expected direction and options looking {cheap} vs how much the stock should "
                    "move.")


def price(legs: Sequence[Leg], p: Pair, *, s: float, t: float, rate: float, action: str, c: Cost, risk: float,
          net_vega: float | None = None) -> dict[str, Any]:
    """The play's ``structure_value``: fair value, cost, edge (per share and per contract), edge per dollar at risk,
    chance of profit, and the direction / volatility split (dollars per contract; they sum to the edge)."""
    fair = value(p.full, s, legs, rate, t)
    fair_vol = value(p.neutral, s, legs, rate, t)
    pay = payoff(legs)
    growth = math.exp(rate * t)
    win = p.full.chance(lambda prices: pay(prices) > c.per_share * growth, s)
    edge = fair - c.per_share
    direction_part, vol_part = (fair - fair_vol) * 100, (fair_vol - c.per_share) * 100
    driver, text = describe(edge, direction_part, vol_part, net_vega)
    return {"version": VERSION, "fair": round(fair * 100, 2), "cost": round(c.per_share * 100, 2),
            "edge": round(edge * 100, 2), "edge_per_risk": round(edge * 100 / risk, 4) if risk > 0 else None,
            "chance_profit": round(win, 4), "direction_part": round(direction_part, 2),
            "vol_part": round(vol_part, 2), "driver": driver, "text": text,
            "fill": {"basis": c.basis, "fraction": c.fraction, "chance": round(c.chance, 4)},
            "direction_live": p.direction_live, "vol_live": p.vol_live, "vol_multiplier": round(p.vol_multiplier, 4),
            "range": [round(s * math.exp(v), 2) for v in p.full.quantiles(RANGE)]}


# ── the session's forecasts, for Plays and the Learner ───────────────────────────────────────────
@dataclass
class Outlook:
    """What the structure pricing reads on one session: the direction and surprise predictions per stock and the
    fill model as of the decision (any of them may be missing: then that part is the baseline / the far side)."""
    session: date
    direction: dr.Session | None = None
    surprise: vs.Session | None = None
    fills: Any | None = None
    _pairs: dict[tuple[str, int], Pair] = field(default_factory=dict)

    def quantiles(self, symbol: str, sessions: int) -> dr.Quantiles | None:
        return None if self.direction is None else self.direction.at(symbol, sessions)

    def correction(self, symbol: str, sessions: int) -> vs.Correction | None:
        return None if self.surprise is None else self.surprise.at(symbol, sessions)

    def pair(self, symbol: str, expiry: int, sessions: int, base: Callable[[], Forecast], *, t: float, rate: float,
             drift: float) -> Pair:
        key = (symbol, expiry)
        if key not in self._pairs:
            self._pairs[key] = pair(base(), t=t, rate=rate, drift=drift, dq=self.quantiles(symbol, sessions),
                                    correction=self.correction(symbol, sessions))
        return self._pairs[key]

    def fits(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for name, got in (("direction", self.direction), ("vol_surprise", self.surprise)):
            rec = None if got is None else got.record
            out[name] = None if rec is None else {"fit_day": rec.fit_day.isoformat(), "sha": rec.sha[:16],
                                                  "live": {str(h): v for h, v in rec.live.items()}}
        return out


    def ranges(self, symbol: str, s: float, sigma: float, drift: float, rate: float, sessions: int
               ) -> dict[str, list[float]]:
        """The stock's 5th, 50th and 95th percentile prices at the exit checkpoints before expiry (``CHECKPOINTS``
        sessions): the lognormal at KESTREL's volatility × the surprise, plus the direction deviation there."""
        out = {}
        for n in CHECKPOINTS:
            if n >= sessions:
                continue
            t = n / 252
            p = pair(Forecast.lognormal(sigma, drift, t), t=t, rate=rate, drift=drift, dq=self.quantiles(symbol, n),
                     correction=self.correction(symbol, n))
            out[str(n)] = [round(s * math.exp(v), 2) for v in p.full.quantiles(RANGE)]
        return out


_MEMO: dict[tuple[Any, ...], tuple[Any, Any, Any]] = {}


def outlook(market: sqlite3.Connection, research: sqlite3.Connection | None, session: date,
            symbols: Sequence[str]) -> Outlook:
    """The session's outlook for ``symbols``. The predictions and the fill table are remembered for the process while
    nothing they read has changed; each call gets its own distributions (the chains move during the day)."""
    from desk.stocks import calendar as cal
    from desk.stocks.learner import fills as fl

    key = (id(market), session, tuple(sorted(symbols)), _fit_key(market), _fills_key(research))
    if key in _MEMO:
        direction, surprise, fills = _MEMO[key]
        return Outlook(session, direction, surprise, fills)
    out = Outlook(session)
    try:
        out.direction = dr.for_session(market, research, symbols, session)
    except (sqlite3.Error, ValueError, ArithmeticError) as exc:
        out.direction = dr.Session(session, None, None, reason=f"{type(exc).__name__}: {str(exc)[:160]}")
    try:
        out.surprise = vs.for_session(market, symbols, session)
    except (sqlite3.Error, ValueError, ArithmeticError) as exc:
        out.surprise = vs.Session(session, None, None, reason=f"{type(exc).__name__}: {str(exc)[:160]}")
    if research is not None:
        try:
            table = fl.Fills.load(research).at(cal.previous_trading_day(session).toordinal())
            out.fills = table if table.n else None
        except sqlite3.Error:
            out.fills = None
    if len(_MEMO) >= 4:
        _MEMO.pop(next(iter(_MEMO)))
    _MEMO[key] = (out.direction, out.surprise, out.fills)
    return out


def _fit_key(market: sqlite3.Connection) -> Any:
    try:
        return market.execute("SELECT MAX(fitted_at), (SELECT MAX(day) FROM stk_bars) FROM stk_forecast_fits").fetchone()
    except sqlite3.OperationalError:
        return None


def _fills_key(research: sqlite3.Connection | None) -> Any:
    if research is None:
        return None
    try:
        return research.execute("SELECT MAX(session), COUNT(*) FROM stk_learn_fill_obs").fetchone()
    except sqlite3.OperationalError:
        return None


# ── the shadow gate per Plays section ────────────────────────────────────────────────────────────
def _spearman(a: Sequence[float], b: Sequence[float]) -> float | None:
    from scipy.stats import rankdata

    if len(a) < MIN_PER_SESSION or np.ptp(a) == 0 or np.ptp(b) == 0:
        return None
    return float(np.corrcoef(rankdata(a), rankdata(b))[0, 1])


def gates(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    """Per Plays section: on its graded plays recorded with a structure value, the mean per-session rank correlation
    of the edge per dollar at risk with the realized net return against the current ranking's, and whether the edge
    may rank (``live``)."""
    try:
        rows = conn.execute(
            "SELECT p.category, p.session, p.rank, json_extract(p.play_json, '$.structure_value.edge_per_risk'), "
            "o.net_return FROM stk_plays p JOIN stk_play_outcomes o USING (session, category, rank) WHERE "
            "instr(p.category, '@') = 0 AND json_extract(p.play_json, '$.structure_value.edge_per_risk') IS NOT NULL "
            "AND o.net_return IS NOT NULL").fetchall()
    except sqlite3.OperationalError:
        return {}
    by: dict[str, dict[int, list[tuple[int, float, float]]]] = defaultdict(lambda: defaultdict(list))
    for category, session, rank, edge, net in rows:
        by[category][int(session)].append((int(rank), float(edge), float(net)))
    out: dict[str, dict[str, Any]] = {}
    for category, sessions in by.items():
        edge_rho, rank_rho = [], []
        for session in sorted(sessions):
            items = sessions[session]
            a = _spearman([e for _, e, _ in items], [n for _, _, n in items])
            b = _spearman([-r for r, _, _ in items], [n for _, _, n in items])
            if a is not None and b is not None:
                edge_rho.append(a)
                rank_rho.append(b)
        diff = np.asarray(edge_rho) - np.asarray(rank_rho)
        t = dr.newey_west_t(diff, 1) if len(diff) >= 3 else None
        live = len(diff) >= GATE_SESSIONS and float(diff.mean()) > 0 and t is not None and t >= GATE_T
        out[category] = {"sessions": len(diff), "plays": sum(len(v) for v in sessions.values()),
                         "edge_rho": round(float(np.mean(edge_rho)), 4) if edge_rho else None,
                         "current_rho": round(float(np.mean(rank_rho)), 4) if rank_rho else None,
                         "t": None if t is None else round(t, 3), "live": bool(live)}
    return out


def rerank(items: Sequence[Any], score: Callable[[Any], float], edge: Callable[[Any], float | None]) -> list[Any]:
    """Order by the mean of the current score's and the edge's percentile ranks (an unknown edge ranks last)."""
    from scipy.stats import rankdata

    if not items:
        return []
    s = rankdata([score(x) for x in items]) / len(items)
    raw = [edge(x) for x in items]
    e = rankdata([-math.inf if v is None else v for v in raw]) / len(items)
    order = sorted(range(len(items)), key=lambda i: (-(s[i] + e[i]) / 2, i))
    return [items[i] for i in order]


def calibration(conn: sqlite3.Connection) -> dict[str, Any]:
    """Brier scores of this distribution's chance of profit and of the odds model's raw one, on the same graded
    plays (a profit: a positive net return at expiry)."""
    try:
        rows = conn.execute(
            "SELECT json_extract(p.play_json, '$.structure_value.chance_profit'), p.profit_kestrel, o.net_return FROM "
            "stk_plays p JOIN stk_play_outcomes o USING (session, category, rank) WHERE "
            "json_extract(p.play_json, '$.structure_value.chance_profit') IS NOT NULL AND p.profit_kestrel IS NOT NULL "
            "AND o.net_return IS NOT NULL").fetchall()
    except sqlite3.OperationalError:
        return {"plays": 0}
    if not rows:
        return {"plays": 0}
    a = np.asarray([float(r[0]) for r in rows])
    b = np.asarray([float(r[1]) for r in rows])
    y = np.asarray([1.0 if float(r[2]) > 0 else 0.0 for r in rows])
    return {"plays": len(rows), "brier_structure": round(float(np.mean((a - y) ** 2)), 5),
            "brier_odds": round(float(np.mean((b - y) ** 2)), 5), "observed": round(float(y.mean()), 4)}


def driver_counts(conn: sqlite3.Connection) -> dict[str, int]:
    """How often each part drove the recorded top-ranked plays (rank ≤ 3) that had an edge."""
    try:
        rows = conn.execute("SELECT json_extract(play_json, '$.structure_value.driver'), COUNT(*) FROM stk_plays WHERE "
                            "rank <= 3 AND instr(category, '@') = 0 AND "
                            "json_extract(play_json, '$.structure_value.driver') IS NOT NULL GROUP BY 1").fetchall()
    except sqlite3.OperationalError:
        return {}
    return {str(k): int(n) for k, n in rows}


def as_legs(rows: Sequence[Mapping[str, Any]]) -> list[Leg]:
    return [Leg(str(r["kind"]), float(r["strike"]), 1 if r["side"] == "buy" else -1) for r in rows]


__all__ = [
    "CHECKPOINTS",
    "GRID",
    "NAME",
    "TAU",
    "VERSION",
    "Cost",
    "Forecast",
    "Leg",
    "Outlook",
    "Pair",
    "W",
    "Z",
    "as_legs",
    "calibration",
    "content",
    "cost",
    "describe",
    "deviation",
    "driver_counts",
    "full_quantiles",
    "gates",
    "outlook",
    "pair",
    "payoff",
    "price",
    "rerank",
    "value",
    "version_hash",
]
