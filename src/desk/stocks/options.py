"""Options analytics, computed by the desk itself from bid/ask (the free sources have no greeks).

Black-Scholes-Merton with a continuous dividend yield; implied volatility solved from the mid price. "Chance of
profit" is the probability the option market's own volatility implies (risk-neutral, lognormal): what prices imply,
not a forecast, and labelled that way wherever it is shown.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from itertools import pairwise
from typing import Any

from desk.stocks.contracts import OptionQuote

YEAR_DAYS = 365.0
SQRT_2 = math.sqrt(2.0)


def norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / SQRT_2))


def norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2 * math.pi)


def year_fraction(today: date, expiration: date) -> float:
    """Calendar time to the 16:00 expiry, with a floor of a quarter day so expiry-day options stay defined."""
    return max((expiration - today).days, 0.25) / YEAR_DAYS


def bs_price(kind: str, s: float, k: float, t: float, vol: float, r: float, q: float = 0.0) -> float:
    if t <= 0 or vol <= 0:
        return max(0.0, s - k) if kind == "call" else max(0.0, k - s)
    d1 = (math.log(s / k) + (r - q + 0.5 * vol * vol) * t) / (vol * math.sqrt(t))
    d2 = d1 - vol * math.sqrt(t)
    if kind == "call":
        return s * math.exp(-q * t) * norm_cdf(d1) - k * math.exp(-r * t) * norm_cdf(d2)
    return k * math.exp(-r * t) * norm_cdf(-d2) - s * math.exp(-q * t) * norm_cdf(-d1)


@dataclass(frozen=True)
class Greeks:
    delta: float
    gamma: float
    theta_per_day: float      # change in option value per calendar day (per share)
    vega_per_point: float     # change per 1 volatility point (per share)


def greeks(kind: str, s: float, k: float, t: float, vol: float, r: float, q: float = 0.0) -> Greeks:
    sq = math.sqrt(t)
    d1 = (math.log(s / k) + (r - q + 0.5 * vol * vol) * t) / (vol * sq)
    d2 = d1 - vol * sq
    disc_q, disc_r = math.exp(-q * t), math.exp(-r * t)
    gamma = disc_q * norm_pdf(d1) / (s * vol * sq)
    vega = s * disc_q * norm_pdf(d1) * sq / 100
    common = -s * disc_q * norm_pdf(d1) * vol / (2 * sq)
    if kind == "call":
        delta = disc_q * norm_cdf(d1)
        theta = common - r * k * disc_r * norm_cdf(d2) + q * s * disc_q * norm_cdf(d1)
    else:
        delta = -disc_q * norm_cdf(-d1)
        theta = common + r * k * disc_r * norm_cdf(-d2) - q * s * disc_q * norm_cdf(-d1)
    return Greeks(delta, gamma, theta / YEAR_DAYS, vega)


def implied_vol(kind: str, price: float, s: float, k: float, t: float, r: float, q: float = 0.0) -> float | None:
    """Volatility that reproduces ``price`` (bisection, 0.5% to 600%); None below intrinsic value or unpriceable."""
    intrinsic = max(0.0, s * math.exp(-q * t) - k * math.exp(-r * t)) if kind == "call" else max(
        0.0, k * math.exp(-r * t) - s * math.exp(-q * t))
    if price <= intrinsic + 1e-9 or price <= 0 or s <= 0 or k <= 0:
        return None
    lo, hi = 0.005, 6.0
    if bs_price(kind, s, k, t, hi, r, q) < price:
        return None
    for _ in range(100):
        mid = (lo + hi) / 2
        if bs_price(kind, s, k, t, mid, r, q) < price:
            lo = mid
        else:
            hi = mid
        if hi - lo < 1e-6:
            break
    return (lo + hi) / 2


def prob_above(s: float, level: float, t: float, vol: float, r: float, q: float = 0.0) -> float:
    """Risk-neutral probability the price ends above ``level`` at ``t``."""
    if level <= 0:
        return 1.0
    d2 = (math.log(s / level) + (r - q - 0.5 * vol * vol) * t) / (vol * math.sqrt(t))
    return norm_cdf(d2)


# ── a chain, analysed ────────────────────────────────────────────────────────────────────────────
@dataclass
class Contract:
    quote: OptionQuote
    iv: float | None
    greeks: Greeks | None
    mid: float | None
    spread_fraction: float | None

    def as_json(self, s: float, t: float, r: float) -> dict[str, Any]:
        q, mid = self.quote, self.mid
        breakeven = None if mid is None else (q.strike + mid if q.kind == "call" else q.strike - mid)
        pop = None
        if self.iv and breakeven and breakeven > 0:
            above = prob_above(s, breakeven, t, self.iv, r)
            pop = above if q.kind == "call" else 1 - above
        return {"occ": q.occ, "kind": q.kind, "strike": q.strike, "bid": q.bid, "ask": q.ask, "mid": mid, "last": q.last,
                "volume": q.volume, "open_interest": q.open_interest, "average_volume": q.average_volume,
                "iv": self.iv, "delta": None if self.greeks is None else self.greeks.delta,
                "gamma": None if self.greeks is None else self.greeks.gamma,
                "theta_per_day_usd": None if self.greeks is None else self.greeks.theta_per_day * 100,
                "vega_usd": None if self.greeks is None else self.greeks.vega_per_point * 100,
                "breakeven": breakeven, "breakeven_move": None if breakeven is None else breakeven / s - 1,
                "chance_of_profit": pop, "max_loss_usd": None if mid is None else mid * 100,
                "spread_fraction": self.spread_fraction, "unusual": unusual_ratio(q)}


def unusual_ratio(q: OptionQuote) -> float | None:
    """Volume against the contract's usual volume (or open interest when there is no average): above 1 means more
    traded today than usually trades or than is open."""
    base = q.average_volume if q.average_volume else q.open_interest
    if q.volume < 100 or not base:
        return None
    return q.volume / base


def analyse(quotes: Sequence[OptionQuote], s: float, today: date, r: float, q: float = 0.0) -> list[Contract]:
    out = []
    for quote in quotes:
        mid = quote.mid
        t = year_fraction(today, quote.expiration)
        iv = None if mid is None else implied_vol(quote.kind, mid, s, quote.strike, t, r, q)
        g = None if iv is None else greeks(quote.kind, s, quote.strike, t, iv, r, q)
        spread = None if mid is None or mid <= 0 or quote.bid is None or quote.ask is None else (quote.ask - quote.bid) / mid
        out.append(Contract(quote, iv, g, mid, spread))
    return out


@dataclass
class ExpirySummary:
    expiration: date
    days: int
    atm_strike: float | None
    atm_iv: float | None
    straddle: float | None
    expected_move: float | None            # straddle / price (fraction)
    skew_25d: float | None                 # IV of the 25-delta put minus the 25-delta call (points of vol)
    call_volume: int
    put_volume: int
    call_oi: int
    put_oi: int
    max_pain: float | None
    contracts: list[Contract] = field(default_factory=list)


def summarize_expiry(contracts: Sequence[Contract], s: float, today: date) -> ExpirySummary:
    exp = contracts[0].quote.expiration
    calls = {c.quote.strike: c for c in contracts if c.quote.kind == "call"}
    puts = {c.quote.strike: c for c in contracts if c.quote.kind == "put"}
    strikes = sorted(set(calls) & set(puts))
    atm = min(strikes, key=lambda k: abs(k - s)) if strikes else None
    straddle = atm_iv = None
    if atm is not None and calls[atm].mid is not None and puts[atm].mid is not None:
        straddle = (calls[atm].mid or 0.0) + (puts[atm].mid or 0.0)
        ivs = [x for x in (calls[atm].iv, puts[atm].iv) if x]
        atm_iv = sum(ivs) / len(ivs) if ivs else None

    def at_delta(side: dict[float, Contract], target: float) -> float | None:
        priced = [c for c in side.values() if c.greeks is not None and c.iv is not None]
        if not priced:
            return None
        best = min(priced, key=lambda c: abs(abs(c.greeks.delta) - target) if c.greeks else 9.0)
        return best.iv if best.greeks and abs(abs(best.greeks.delta) - target) < 0.12 else None

    p25, c25 = at_delta(puts, 0.25), at_delta(calls, 0.25)
    skew = None if p25 is None or c25 is None else (p25 - c25) * 100
    pain = None
    all_strikes = sorted({c.quote.strike for c in contracts})
    if all_strikes and any(c.quote.open_interest for c in contracts):
        def payout(price: float) -> float:
            return sum(c.quote.open_interest * (max(0.0, price - c.quote.strike) if c.quote.kind == "call"
                                                else max(0.0, c.quote.strike - price)) for c in contracts)
        pain = min(all_strikes, key=payout)
    return ExpirySummary(exp, (exp - today).days, atm, atm_iv, straddle, None if straddle is None else straddle / s, skew,
                         sum(c.quote.volume for c in calls.values()), sum(c.quote.volume for c in puts.values()),
                         sum(c.quote.open_interest for c in calls.values()), sum(c.quote.open_interest for c in puts.values()),
                         pain, list(contracts))


def daily_summary(chains: dict[date, list[OptionQuote]], s: float, today: date, r: float) -> dict[str, Any]:
    """What the options desk stores each day per underlying (kept forever, unlike raw chains)."""
    expiries: list[dict[str, Any]] = []
    unusual: list[dict[str, Any]] = []
    for exp, quotes in sorted(chains.items()):
        if not quotes:
            continue
        summary = summarize_expiry(analyse(quotes, s, today, r), s, today)
        expiries.append({"expiration": exp.isoformat(), "days": summary.days, "atm_iv": summary.atm_iv,
                         "expected_move": summary.expected_move, "straddle": summary.straddle, "skew_25d": summary.skew_25d,
                         "call_volume": summary.call_volume, "put_volume": summary.put_volume, "call_oi": summary.call_oi,
                         "put_oi": summary.put_oi, "max_pain": summary.max_pain})
        for c in summary.contracts:
            ratio = unusual_ratio(c.quote)
            if ratio is not None and ratio >= 3 and c.quote.volume >= 500:
                unusual.append({"occ": c.quote.occ, "kind": c.quote.kind, "strike": c.quote.strike, "expiration": exp.isoformat(),
                                "volume": c.quote.volume, "open_interest": c.quote.open_interest, "ratio": round(ratio, 2),
                                "ratio_basis": "average volume" if c.quote.average_volume else "open interest",
                                "mid": c.mid, "iv": c.iv})
    near = [e for e in expiries if e["atm_iv"] is not None]
    iv30 = interpolate_iv(near, 30)
    calls = sum(e["call_volume"] for e in expiries)
    puts = sum(e["put_volume"] for e in expiries)
    unusual.sort(key=lambda u: -float(u["volume"]) * float(u["mid"] or 0))
    return {"price": s, "iv30": iv30, "expiries": expiries, "call_volume": calls, "put_volume": puts,
            "put_call_volume": None if not calls else puts / calls,
            "call_oi": sum(e["call_oi"] for e in expiries), "put_oi": sum(e["put_oi"] for e in expiries),
            "term_slope": term_slope(near), "unusual": unusual[:12]}


def interpolate_iv(expiries: Sequence[dict[str, Any]], days: int) -> float | None:
    """ATM implied volatility at a constant maturity, interpolated in total variance."""
    pts: list[tuple[float, float]] = sorted((float(e["days"]), float(e["atm_iv"])) for e in expiries
                                            if e.get("atm_iv") and e["days"] > 0)
    if not pts:
        return None
    if days <= pts[0][0]:
        return pts[0][1]
    if days >= pts[-1][0]:
        return pts[-1][1]
    for (d0, v0), (d1, v1) in pairwise(pts):
        if d0 <= days <= d1:
            w0, w1 = v0 * v0 * d0, v1 * v1 * d1
            w = w0 + (w1 - w0) * (days - d0) / (d1 - d0)
            return math.sqrt(w / days)
    return None


def term_slope(expiries: Sequence[dict[str, Any]]) -> float | None:
    """Far IV minus near IV (points): negative = inverted, the market expects the near term to be rougher."""
    near = interpolate_iv(expiries, 30)
    far = interpolate_iv(expiries, 90)
    return None if near is None or far is None else (far - near) * 100


def iv_rank(history: Sequence[float], current: float) -> tuple[float | None, float | None]:
    """IV rank (where today sits between the year's low and high, 0-100) and IV percentile (share of days below)."""
    values = [v for v in history if v]
    if len(values) < 20:
        return None, None
    lo, hi = min(values), max(values)
    rank = None if hi == lo else (current - lo) / (hi - lo) * 100
    pct = sum(1 for v in values if v < current) / len(values) * 100
    return rank, pct


def realized_move_frequency(closes: Sequence[float], days: int, move: float) -> float | None:
    """How often the stock actually moved more than ``move`` (either way) over ``days`` trading days."""
    pts = [c for c in closes if c and not math.isnan(c)]
    windows = [(pts[i + days] / pts[i] - 1) for i in range(len(pts) - days)]
    if len(windows) < 20:
        return None
    return sum(1 for w in windows if abs(w) > move) / len(windows)


# ── strategies and payoffs ───────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Leg:
    kind: str                 # call | put | stock
    strike: float
    expiration: date | None
    quantity: int             # contracts (+ long, - short); shares for stock
    price: float              # per share


def payoff_at_expiry(legs: Sequence[Leg], price: float) -> float:
    """Dollars at expiration (100 shares per contract) including what was paid or received."""
    total = 0.0
    for leg in legs:
        if leg.kind == "stock":
            total += (price - leg.price) * leg.quantity
            continue
        intrinsic = max(0.0, price - leg.strike) if leg.kind == "call" else max(0.0, leg.strike - price)
        total += (intrinsic - leg.price) * leg.quantity * 100
    return total


def strategy_profile(legs: Sequence[Leg], s: float, t: float, vol: float, r: float) -> dict[str, Any]:
    """Max profit and loss, breakevens and the implied chance of profit for a strategy held to expiration."""
    strikes = sorted({leg.strike for leg in legs if leg.kind != "stock"})
    lo, hi = max(0.01, min([s * 0.3, *strikes]) * 0.5), max([s * 3, *strikes]) * 1.5
    grid = sorted({lo, hi, *strikes, *(lo + (hi - lo) * i / 2000 for i in range(2001))})
    values = [payoff_at_expiry(legs, x) for x in grid]
    breakevens = []
    for i in range(len(grid) - 1):
        x0, v0, x1, v1 = grid[i], values[i], grid[i + 1], values[i + 1]
        if v0 == 0:
            breakevens.append(x0)
        elif v0 * v1 < 0:
            breakevens.append(x0 + (x1 - x0) * (-v0) / (v1 - v0))
    net_calls = sum(leg.quantity for leg in legs if leg.kind == "call")
    unlimited_up = net_calls > 0 or sum(leg.quantity for leg in legs if leg.kind == "stock") > 0
    # chance of profit: integrate the implied lognormal over the profitable price ranges
    pop = 0.0
    for i in range(len(grid) - 1):
        mid = (grid[i] + grid[i + 1]) / 2
        if payoff_at_expiry(legs, mid) > 0:
            pop += prob_above(s, grid[i], t, vol, r) - prob_above(s, grid[i + 1], t, vol, r)
    if payoff_at_expiry(legs, hi * 2) > 0:
        pop += prob_above(s, hi, t, vol, r)
    return {"max_profit": None if unlimited_up else max(values), "max_loss": min(values), "breakevens": breakevens,
            "chance_of_profit": max(0.0, min(1.0, pop)), "net_debit": sum(
                leg.price * leg.quantity * (100 if leg.kind != "stock" else 1) for leg in legs)}


def payoff_curve(legs: Sequence[Leg], lo: float, hi: float, points: int = 60) -> list[tuple[float, float]]:
    return [(x, payoff_at_expiry(legs, x)) for x in (lo + (hi - lo) * i / (points - 1) for i in range(points))]


def degen_check(chance_of_profit: float | None, days: int, premium_usd: float | None) -> str | None:
    """Call a lottery ticket a lottery ticket."""
    if chance_of_profit is None:
        return None
    if chance_of_profit < 0.15 and days <= 10:
        return "lottery ticket: under a 15% implied chance of profit with little time left"
    if chance_of_profit < 0.2:
        return "long shot: the price has to move a lot, soon"
    if premium_usd is not None and premium_usd < 30 and days <= 5:
        return "cheap for a reason: almost no time left"
    return None


__all__ = ["Contract", "ExpirySummary", "Greeks", "Leg", "analyse", "bs_price", "daily_summary", "degen_check", "greeks",
           "implied_vol", "interpolate_iv", "iv_rank", "payoff_at_expiry", "payoff_curve", "prob_above",
           "realized_move_frequency", "strategy_profile", "summarize_expiry", "term_slope", "unusual_ratio",
           "year_fraction"]
