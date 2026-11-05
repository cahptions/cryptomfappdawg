"""Option prices for the historical replay, as far back as stock prices go.

A past day's option chain is rebuilt from the stock's own history: monthly expiries (the third Friday, listed for every
optioned stock), a listing-style strike grid, and Black-Scholes at the stock's realized volatility times how options
on that stock have actually been priced against its realized volatility. That ratio, the put skew and the quoted spread
are calibrated from the chains this app collected and from Massive's real daily option prices.

Where Massive has a contract's real daily price (Options Basic covers the last 2 years), it replaces the model. On a
day the contract traded, the price is its closing trade. On a quiet day, it is the volatility implied by its last trade
within a week. Contracts are chosen from the model's prices, so one contract's real price never pulls a rule toward
that contract; real prices change what a contract cost and was worth (and, through calibration, the model itself).
Every day a traded contract is held records which kind of price it got.
"""

from __future__ import annotations

import math
import re
import sqlite3
from bisect import bisect_left
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from statistics import median
from typing import Any

import numpy as np

from desk.stocks import calendar as cal
from desk.stocks import options as opt
from desk.stocks.contracts import OptionQuote
from desk.stocks.prices import Panel
from desk.stocks.repo import StocksMarket

# Strategies a stock-price replay can run with rebuilt option prices, and why the others can't.
REPLAYED = ("random_calls", "momentum_call_spreads", "bear_put_spreads")
# Squeeze rules replay once the stored short-interest history covers the window (desk.stocks.backtest.short_history).
SHORT_REPLAYED = ("squeeze_runner", "squeeze_calls")
SHORT_WAIT = ("Replays once the stored FINRA short-interest history covers the replay window: 24 or more twice-monthly "
              "settlements, the earliest published before the first replayed day. Until then it is left out.")
NOT_REPLAYED = {
    "cheap_iv_calls": "Its signal is how options were priced against the stock's own movement. Prices rebuilt from "
                      "that movement can't show it, so this one is tested on collected chains only.",
    "earnings_straddles": "Needs the move options priced before each report, which only real chains show.",
    "squeeze_calls": SHORT_WAIT,
    "squeeze_runner": SHORT_WAIT,
    "unusual_follow": "Needs each day's real option volume across the whole chain.",
    "random_lottos": "Replayed on real past prices instead (Massive's weekly option history), not on modeled ones.",
    "lotto_picks": "Replayed on real past prices instead (Massive's weekly option history), not on modeled ones.",
    "lotto_runners": "Replayed on real past prices instead (Massive's weekly option history), not on modeled ones.",
}

MASSIVE_DAYS = 725            # Options Basic reaches 2 years back; a few days' margin
DEFAULT_RATIO = 1.15          # options usually price a little more movement than stocks deliver
DEFAULT_SKEW = 0.08           # IV rises 8% of the at-the-money level per standard move down in strike
DEFAULT_SPREAD = 0.08         # bid-ask spread as a share of the mid
MIN_HALF_SPREAD = 0.025       # dollars: the smallest half-spread quoted
STICKY_DAYS = 7               # a real trade's implied volatility prices a quiet contract for up to a week
HORIZON_DAYS = 130            # expiries offered (the replayed rules hold 30 to 60 days)
STRIKE_RANGE = 0.35           # strikes from 35% below to 35% above the price
OPEN_INTEREST = 1000          # not modeled; above the rules' 100-contract floor
_OCC = re.compile(r"^([A-Z][A-Z0-9.]*)(\d{6})([CP])(\d{8})$")


# ── listings ─────────────────────────────────────────────────────────────────────────────────────
def third_friday(year: int, month: int) -> date:
    first = date(year, month, 1)
    return first + timedelta(days=(4 - first.weekday()) % 7 + 14)


def monthly_expiries(asof: date, horizon: int = HORIZON_DAYS) -> list[date]:
    """Standard monthly expiries from ``asof`` to ``horizon`` days out (a holiday Friday moves to the day before)."""
    out: list[date] = []
    year, month = asof.year, asof.month
    while True:
        e = third_friday(year, month)
        while not cal.is_trading_day(e):
            e -= timedelta(days=1)
        if (e - asof).days > horizon:
            return out
        if e >= asof:
            out.append(e)
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)


def strike_step(s: float) -> float:
    return 0.5 if s < 10 else 1.0 if s < 50 else 2.5 if s < 150 else 5.0 if s < 500 else 10.0


def strike_grid(s: float) -> list[float]:
    step = strike_step(s)
    return [round(k * step, 2) for k in range(math.ceil(s * (1 - STRIKE_RANGE) / step),
                                              math.floor(s * (1 + STRIKE_RANGE) / step) + 1) if k > 0]


def occ(symbol: str, expiration: date, kind: str, strike: float) -> str:
    return f"{symbol}{expiration:%y%m%d}{'C' if kind == 'call' else 'P'}{round(strike * 1000):08d}"


def parse_occ(code: str) -> tuple[str, date, str, float] | None:
    m = _OCC.match(code)
    if m is None:
        return None
    try:
        expiration = date(2000 + int(m[2][:2]), int(m[2][2:4]), int(m[2][4:]))
    except ValueError:
        return None
    return m[1], expiration, "call" if m[3] == "C" else "put", int(m[4]) / 1000


def realized(closes: np.ndarray) -> float | None:
    """Annualized realized volatility: half the last 20 sessions, half the last 60 (steadier than either)."""
    c = closes[np.isfinite(closes) & (closes > 0)][-61:]
    if len(c) < 21:
        return None
    r = np.diff(np.log(c))
    short = float(np.std(r[-20:]))
    long = float(np.std(r)) if len(r) >= 40 else short
    value = (short + long) / 2 * math.sqrt(252)
    return min(max(value, 0.05), 3.0) if math.isfinite(value) and value > 0 else None


def _clamp(x: float, lo: float, hi: float) -> float:
    return min(max(x, lo), hi)


# ── real prices ──────────────────────────────────────────────────────────────────────────────────
class RealBars:
    """Massive's daily closing trades per contract (OCC code -> sorted (day ordinal, close))."""

    def __init__(self, rows: Iterable[tuple[str, int, float]] = ()) -> None:
        series: dict[str, list[tuple[int, float]]] = defaultdict(list)
        for code, day, close in rows:
            series[code].append((day, close))
        self.series = {code: sorted(points) for code, points in series.items()}
        self.days = {code: [d for d, _ in points] for code, points in self.series.items()}

    @classmethod
    def load(cls, conn: sqlite3.Connection) -> RealBars:
        try:
            return cls((r[0], r[1], r[2]) for r in conn.execute("SELECT occ, day, close FROM stk_option_bars"))
        except sqlite3.OperationalError:          # a database from before real option prices
            return cls()

    def __len__(self) -> int:
        return len(self.series)

    def on(self, code: str, day: date) -> float | None:
        days = self.days.get(code)
        if not days:
            return None
        i = bisect_left(days, day.toordinal())
        return self.series[code][i][1] if i < len(days) and days[i] == day.toordinal() else None

    def before(self, code: str, day: date, within: int) -> tuple[date, float] | None:
        days = self.days.get(code)
        if not days:
            return None
        i = bisect_left(days, day.toordinal()) - 1
        if i < 0 or day.toordinal() - days[i] > within:
            return None
        d, close = self.series[code][i]
        return date.fromordinal(d), close


# ── calibration ──────────────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Calibration:
    ratios: dict[str, float]
    market_ratio: float
    skew: float
    spreads: dict[str, float]
    market_spread: float
    observations: dict[str, int] = field(default_factory=dict)

    def ratio(self, symbol: str) -> float:
        return self.ratios.get(symbol, self.market_ratio)

    def spread(self, symbol: str) -> float:
        return self.spreads.get(symbol, self.market_spread)

    def iv(self, atm: float, s: float, k: float, t: float) -> float:
        """Volatility at strike ``k``: higher below the price (the put skew), in standard moves of the stock."""
        m = math.log(k / s) / (atm * math.sqrt(t))
        return atm * _clamp(1 - self.skew * m, 0.6, 2.0)

    def as_json(self) -> dict[str, Any]:
        return {"market_ratio": round(self.market_ratio, 3), "symbols_with_own_ratio": len(self.ratios),
                "skew": round(self.skew, 3), "market_spread": round(self.market_spread, 3),
                "symbols_with_own_spread": len(self.spreads), "observations": dict(self.observations)}


def _rv_at(panel: Panel, symbol: str, day: date) -> float | None:
    i = panel.index.get(symbol)
    t = panel.day_index(day)
    if i is None or t < 0 or panel.days[t] != day:
        return None
    return realized(panel.close[i, :t + 1])


def calibrate(market: StocksMarket, panel: Panel, real: RealBars, rate: float, *, spread_days: int = 5) -> Calibration:
    """How options were priced against each stock's realized volatility, the skew and the spread, from what's stored.

    ``panel`` must be split-adjusted through its last day so returns are continuous."""
    ratios: dict[str, list[float]] = defaultdict(list)
    skews: list[float] = []
    chains = massive = 0
    for symbol, day, summary in market.option_daily_all():
        rv = _rv_at(panel, symbol, day)
        iv30 = summary.get("iv30")
        if rv and iv30 and math.isfinite(float(iv30)) and iv30 > 0:
            ratios[symbol].append(float(iv30) / rv)
            chains += 1
        for e in summary.get("expiries") or []:
            if 14 <= (e.get("days") or 0) <= 90 and e.get("atm_iv") and e.get("skew_25d") is not None:
                # 25-delta strikes sit about 0.674 standard moves either side, 1.349 apart
                skews.append(float(e["skew_25d"]) / 100 / (float(e["atm_iv"]) * 1.349))
    skew = _clamp(median(skews), 0.0, 0.3) if len(skews) >= 5 else DEFAULT_SKEW
    for code, points in real.series.items():
        parsed = parse_occ(code)
        if parsed is None:
            continue
        symbol, expiration, kind, k = parsed
        i = panel.index.get(symbol)
        if i is None:
            continue
        for ordinal, close in points:
            day = date.fromordinal(ordinal)
            t_index = panel.day_index(day)
            if t_index < 0 or panel.days[t_index] != day or not 7 <= (expiration - day).days <= 120:
                continue
            s, rv = float(panel.close[i, t_index]), realized(panel.close[i, :t_index + 1])
            t = opt.year_fraction(day, expiration)
            iv = opt.implied_vol(kind, close, s, k, t, rate) if s > 0 and math.isfinite(s) else None
            if not iv or not rv:
                continue
            m = math.log(k / s) / (iv * math.sqrt(t))
            if abs(m) <= 1.5:
                ratios[symbol].append(iv / _clamp(1 - skew * m, 0.6, 2.0) / rv)
                massive += 1
    every = [x for xs in ratios.values() for x in xs]
    spreads: dict[str, list[float]] = defaultdict(list)
    for day in market.option_days(date.max, spread_days):
        for symbol, _, price, quotes in market.chain_rows(day, 14, 90):
            if not price:
                continue
            for q in quotes:
                mid = q.mid
                if mid is not None and mid >= 0.5 and (q.bid or 0) > 0 and abs(math.log(q.strike / price)) < 0.1:
                    spreads[symbol].append(((q.ask or 0) - (q.bid or 0)) / mid)
    all_spreads = [x for xs in spreads.values() for x in xs]
    return Calibration(
        ratios={s: _clamp(median(v), 0.6, 3.0) for s, v in ratios.items() if len(v) >= 8},
        market_ratio=_clamp(median(every), 0.6, 3.0) if len(every) >= 20 else DEFAULT_RATIO,
        skew=skew,
        spreads={s: _clamp(median(v), 0.01, 0.3) for s, v in spreads.items() if len(v) >= 10},
        market_spread=_clamp(median(all_spreads), 0.01, 0.3) if len(all_spreads) >= 30 else DEFAULT_SPREAD,
        observations={"collected_days": chains, "massive_prices": massive, "skew": len(skews), "spreads": len(all_spreads)})


# ── a day's chain ────────────────────────────────────────────────────────────────────────────────
class ModeledOptions:
    """Chains for one replay session at a time (``session`` first), cached per symbol."""

    def __init__(self, calibration: Calibration, real: RealBars, optioned: Iterable[str]) -> None:
        self.calibration = calibration
        self.real = real
        self.optioned = frozenset(optioned)
        self.asof: date | None = None
        self.panel: Panel | None = None
        self.rate = 0.04
        self._cache: dict[tuple[str, bool], dict[date, list[OptionQuote]]] = {}
        self._known: dict[str, set[tuple[date, float]]] = defaultdict(set)    # strikes once traded stay listed
        self._traded: set[str] = set()
        self.priced: dict[tuple[str, int], str] = {}                           # traded contract, day -> price kind

    def session(self, asof: date, panel: Panel, rate: float) -> None:
        self.asof, self.panel, self.rate = asof, panel, rate
        self._cache = {}

    def _spot(self, symbol: str, day: date) -> float | None:
        assert self.panel is not None
        i, t = self.panel.index.get(symbol), self.panel.day_index(day)
        if i is None or t < 0 or self.panel.days[t] != day:
            return None
        s = float(self.panel.close[i, t])
        return s if math.isfinite(s) and s > 0 else None

    def atm(self, symbol: str) -> float | None:
        """Today's modeled at-the-money implied volatility."""
        if self.panel is None or self.asof is None or symbol not in self.optioned:
            return None
        rv = _rv_at(self.panel, symbol, self.asof)
        return None if rv is None else rv * self.calibration.ratio(symbol)

    def summary(self, symbol: str) -> dict[str, Any] | None:
        """What the option rules read for a stock (``iv30`` marks it optioned), labelled as modeled."""
        atm = self.atm(symbol)
        return None if atm is None else {"iv30": atm, "modeled": True}

    def remember(self, symbol: str, legs: Sequence[dict[str, Any]]) -> None:
        for leg in legs:
            self._known[symbol].add((date.fromisoformat(leg["expiration"]), float(leg["strike"])))
            self._traded.add(leg["occ"])
        self._cache = {key: v for key, v in self._cache.items() if key[0] != symbol}

    def chain(self, symbol: str, *, real: bool = True) -> dict[date, list[OptionQuote]]:
        key = (symbol, real)
        if key not in self._cache:
            self._cache[key] = self._build(symbol, real)
        return self._cache[key]

    def _build(self, symbol: str, real: bool) -> dict[date, list[OptionQuote]]:
        assert self.asof is not None
        s, atm = self._spot(symbol, self.asof), self.atm(symbol)
        if s is None or atm is None:
            return {}
        quoted = cal.session_close(self.asof)
        spread = self.calibration.spread(symbol)
        grid = strike_grid(s)
        out: dict[date, list[OptionQuote]] = {}
        for e in monthly_expiries(self.asof):
            t = opt.year_fraction(self.asof, e)
            strikes = sorted(set(grid) | {k for ke, k in self._known[symbol] if ke == e})
            quotes = []
            for kind in ("call", "put"):
                for k in strikes:
                    code = occ(symbol, e, kind, k)
                    mid, source = self._price(code, symbol, kind, s, k, e, t, atm, real)
                    if real and code in self._traded:
                        self.priced[(code, self.asof.toordinal())] = source
                    half = max(mid * spread / 2, MIN_HALF_SPREAD)
                    quotes.append(OptionQuote(code, symbol, kind, k, e, round(max(mid - half, 0.0), 2), round(mid + half, 2),
                                              mid if source == "real" else None, 0, OPEN_INTEREST, None, quoted))
            out[e] = quotes
        return out

    def _price(self, code: str, symbol: str, kind: str, s: float, k: float, e: date, t: float, atm: float,
               real: bool) -> tuple[float, str]:
        assert self.asof is not None
        floor = max(0.0, s - k) if kind == "call" else max(0.0, k - s)
        if real:
            close = self.real.on(code, self.asof)
            if close is not None and close > floor:
                return close, "real"
            prior = self.real.before(code, self.asof, STICKY_DAYS)
            s0 = None if prior is None else self._spot(symbol, prior[0])
            if prior is not None and s0 is not None:
                iv = opt.implied_vol(kind, prior[1], s0, k, opt.year_fraction(prior[0], e), self.rate)
                if iv:
                    return opt.bs_price(kind, s, k, t, iv, self.rate), "real_iv"
        return opt.bs_price(kind, s, k, t, self.calibration.iv(atm, s, k, t), self.rate), "model"

    # ── what the replay reports ────────────────────────────────────────────────────────────────
    def price_mix(self, positions: Iterable[Any], last: date) -> dict[str, dict[str, int]]:
        """Per account, how many held contract-days were priced by a real trade, a recent trade's volatility, or the
        model."""
        out: dict[str, Counter[str]] = defaultdict(Counter)
        for p in positions:
            if p.asset != "option":
                continue
            end = (p.exit_day or last).toordinal()
            for leg in p.legs:
                for d in range(p.entry_day.toordinal(), end + 1):
                    kind = self.priced.get((leg["occ"], d))
                    if kind:
                        out[p.account][kind] += 1
        return {account: {k: c.get(k, 0) for k in ("real", "real_iv", "model")} for account, c in out.items()}


def wanted(positions: Iterable[Any], last: date) -> list[dict[str, str]]:
    """The contracts a replay traded inside Massive's window, each with the days its real prices would cover."""
    start = last - timedelta(days=MASSIVE_DAYS)
    spans: dict[str, tuple[date, date]] = {}
    for p in positions:
        if p.asset != "option":
            continue
        for leg in p.legs:
            first = max(p.entry_day - timedelta(days=10), start)        # the decision day before the fill, too
            end = min(p.exit_day or last, date.fromisoformat(leg["expiration"]), last)
            if end < first:
                continue
            a, b = spans.get(leg["occ"], (first, end))
            spans[leg["occ"]] = (min(a, first), max(b, end))
    return [{"occ": code, "first": a.isoformat(), "last": b.isoformat()} for code, (a, b) in sorted(spans.items())]


__all__ = ["MASSIVE_DAYS", "NOT_REPLAYED", "REPLAYED", "SHORT_REPLAYED", "Calibration", "ModeledOptions", "RealBars", "calibrate",
           "monthly_expiries", "occ", "parse_occ", "realized", "strike_grid", "strike_step", "third_friday", "wanted"]
