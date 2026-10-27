"""Plays: the options of the day, by category (docs/STOCKS_PLAYS.md). Research only — no order can be placed here.

Built from the latest stored option chains (each underlying's latest pass of the session), the desks' latest analysis,
the committee's measured information coefficient, and how earlier plays turned out. Every play is a quoted,
defined-risk structure with its cost or credit, worst case, break-even, greeks and three chances of finishing in the
money (``desk.stocks.odds``): what the market prices, what the stock's own history says, and KESTREL's calibrated
forecast. Nothing sells an uncovered option.

The quotes carry no greeks, so implied volatility and greeks are solved here from the bid/ask mid (vectorized, so a
whole session scores in well under a second). Unusual activity is scored from quote snapshots — volume, open interest
and quotes, compared with the contract's own and the underlying's earlier sessions — not from exchange trade prints,
so trade direction is shown only as a labelled inference.

A session's plays are recorded once, at the first build after that session closes, and graded once at expiration on
the underlying's close. Graded plays calibrate the KESTREL chance of later plays.
"""

from __future__ import annotations

import json
import math
import random
import sqlite3
import statistics
from collections import Counter, defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any

import numpy as np

from desk.core.clock import to_iso
from desk.core.hashing import canonical_json, sha256_hex
from desk.stocks import calendar as cal
from desk.stocks import odds as od
from desk.stocks import options as opt
from desk.stocks.activity import quote_session
from desk.stocks.contracts import OptionQuote
from desk.stocks.hub import adjusted_bars
from desk.stocks.paper.book import PaperBook
from desk.stocks.paper.fills import OPTION_FEE_PER_CONTRACT as FEE
from desk.stocks.paper.report import trade_stats
from desk.stocks.paper.strategies import BY_STRATEGY
from desk.stocks.repo import StocksMarket, StocksResearch, day_of
from desk.storage.db import transaction

Array = np.ndarray
VERSION = 1
PER_CATEGORY = 12
HISTORY_SESSIONS = 756              # three years of closes for the history view
PRIOR_SESSIONS = 10                 # earlier sessions read for each contract's own volume, open interest and IV
DEFAULT_RATE = 0.04
CLOSE_GRACE = timedelta(minutes=15)
ETFS = frozenset({"SPY", "QQQ", "IWM", "DIA"})
MIN_PAPER_TRADES = 20


# ── categories ───────────────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Category:
    id: str
    title: str
    emoji: str
    blurb: str
    basis: str
    empty: str
    paper: str | None = None        # the PAPER strategy that trades this idea, when there is one

    def content(self) -> dict[str, Any]:
        return {"id": self.id, "basis": self.basis, "paper": self.paper}


CATEGORIES: tuple[Category, ...] = (
    Category("likely_itm", "Most likely to finish ITM", "🎯",
             "Calls on stocks the desks like and puts on stocks they don't, ranked by KESTREL's chance of finishing in "
             "the money.",
             "Out-of-the-money or at-the-money (|delta| 0.25–0.55), 7–60 days, spread ≤ 15%, open interest ≥ 100, ask ≥ "
             "$0.10. Direction from the committee's own label: calls at a bullish lean (≥ 58), puts at a bearish "
             "lean (< 42). One contract per stock.",
             "No stock with a committee view has a liquid contract in range today."),
    Category("edge", "Biggest model edge", "⚖️",
             "Contracts KESTREL values above their ask: its forecast expects more back than they cost.",
             "Expected payoff under KESTREL's forecast minus the ask, per dollar paid; at least +5%. 7–60 days, |delta| "
             "0.15–0.70, spread ≤ 12%, open interest ≥ 100.",
             "No contract is priced below KESTREL's forecast today.", paper="cheap_iv_calls"),
    Category("unusual", "Unusual activity", "🐋",
             "Contracts trading far beyond their open interest and their own usual volume, scored on eight signals and "
             "confirmed the next day by the change in open interest.",
             "Volume ≥ 250 and ≥ $25k at the mid, and volume ≥ open interest or ≥ 4× the contract's own average. "
             "Scored on volume/OI, volume vs its average, premium, the underlying's whole-chain surge, where the last "
             "trade printed in the quote, short-dated out-of-the-money aggression, IV rising with the volume, and "
             "clusters across strikes. From quote snapshots, not exchange prints.",
             "No contract in the collected chains clears the unusual-activity gates today.", paper="unusual_follow"),
    Category("cheap_vol", "Cheap volatility", "🏷️",
             "At-the-money straddles where options price less movement than the stock has actually been making.",
             "IV30 ≤ 0.9× 20-session realized volatility, or IV rank ≤ 20. Straddle at the expiry nearest 30 days "
             "(14–60). The chance shown is of a move past either break-even.",
             "No collected underlying has IV well below its realized volatility today."),
    Category("earnings", "Earnings plays", "📅",
             "Straddles over the next results date, comparing the move options price with the moves this stock "
             "has made on its last results days.",
             "Results window estimated from past report dates, starting within 21 days; first expiry after it (≤ 45 "
             "days); at least four past results-day moves. Ranked by usual move ÷ priced move.",
             "No collected underlying has results due within three weeks.", paper="earnings_straddles"),
    Category("trend_calls", "Trend calls", "🚀",
             "Calls on the strongest trends, bought with time to work.",
             "Momentum desk ≥ 75 and above the 200-day average; call nearest 0.60 delta, 30–75 days.",
             "No strong-trend stock has a liquid call in range today.", paper="momentum_call_spreads"),
    Category("bear_puts", "Bearish puts", "🐻",
             "Puts on expensive stocks in downtrends, or stocks with a red-flag filing.",
             "Valuation ≤ 30 and momentum ≤ 35 below the 200-day average, or a red flag with momentum ≤ 50; put nearest "
             "0.45 delta, 30–75 days.", "No bearish setup has a liquid put in range today.", paper="bear_put_spreads"),
    Category("squeeze", "Squeeze calls", "🔥",
             "Out-of-the-money calls on heavily shorted stocks already moving against the shorts.",
             "Squeeze score ≥ 60; call nearest 0.30 delta, 14–45 days.",
             "No collected underlying has a squeeze score of 60 or more today.", paper="squeeze_calls"),
    Category("sell_premium", "Sell rich premium", "💰",
             "Credit spreads (defined risk) where options are expensive: collect premium, keep it if the stock stays "
             "on the right side of the short strike.",
             "IV rank ≥ 60 or IV30 ≥ 1.3× realized. Bull put spread (committee ≥ 50 or none) or bear call spread "
             "(committee ≤ 45): short leg nearest 0.25 delta, long leg about 5% further out, 21–60 days, credit ≥ 10% "
             "of the width. Never an uncovered short.",
             "No collected underlying has rich enough premium to sell today."),
    Category("lottos", "Lottos", "🎰",
             "Cheap, short-dated, out-of-the-money contracts with the biggest payoff if the stock makes a normal-sized "
             "move. Most expire worthless — the chance is shown.",
             "Ask $0.05–$0.50, 1–14 days, at least 3% out of the money, volume and open interest ≥ 50. Ranked by "
             "payoff at a one-standard-deviation move per dollar, times KESTREL's chance in the money.",
             "No cheap short-dated contract is liquid enough today."),
    Category("leaps", "LEAPS", "🏛️",
             "Long-dated in-the-money calls on high-conviction names: stock-like exposure for less money down.",
             "180+ days, call nearest 0.75 delta, spread ≤ 10%; committee ≥ 60 or a broad ETF. Ranked by conviction "
             "less the time value paid.",
             "No long-dated chain is stored yet (collection adds the expiry nearest a year out)."),
    Category("watchlist", "Your watchlist", "⭐",
             "The best-valued contract for each stock on your watchlist, in the committee's direction.",
             "14–60 days, |delta| 0.35–0.60, spread ≤ 15%; highest KESTREL value per dollar.",
             "Add stocks to your watchlist (star them on a stock page) to see their plays here."),
)
BY_CATEGORY = {c.id: c for c in CATEGORIES}


def method() -> str:
    return sha256_hex(canonical_json({"version": VERSION, "categories": [c.content() for c in CATEGORIES],
                                      "iv_weight": od.IV_WEIGHT, "ic_shrink": od.IC_SHRINK_MONTHS,
                                      "calibration_prior": od.CALIBRATION_PRIOR}))[:16]


# ── vectorized pricing (Abramowitz–Stegun 7.1.26 erf: |error| < 1.5e-7) ─────────────────────────
def _ncdf(x: Array) -> Array:
    z = np.abs(x) / math.sqrt(2.0)
    t = 1.0 / (1.0 + 0.3275911 * z)
    poly = ((((1.061405429 * t - 1.453152027) * t + 1.421413741) * t - 0.284496736) * t + 0.254829592) * t
    out: Array = 0.5 * (1.0 + np.sign(x) * (1.0 - poly * np.exp(-z * z)))
    return out


def _bs(call: Array, s: Array, k: Array, t: Array, v: Array, r: float) -> Array:
    sq = v * np.sqrt(t)
    d1 = (np.log(s / k) + (r + 0.5 * v * v) * t) / sq
    d2 = d1 - sq
    disc = k * np.exp(-r * t)
    out: Array = np.where(call, s * _ncdf(d1) - disc * _ncdf(d2), disc * _ncdf(-d2) - s * _ncdf(-d1))
    return out


def implied_vols(call: Array, s: Array, k: Array, t: Array, price: Array, r: float) -> Array:
    """Bisection on [0.5%, 600%] for every contract at once; NaN where the price is at or below intrinsic value."""
    disc = k * np.exp(-r * t)
    intrinsic = np.where(call, np.maximum(s - disc, 0.0), np.maximum(disc - s, 0.0))
    lo, hi = np.full(price.shape, 0.005), np.full(price.shape, 6.0)
    for _ in range(48):
        mid = 0.5 * (lo + hi)
        high = _bs(call, s, k, t, mid, r) > price
        hi, lo = np.where(high, mid, hi), np.where(high, lo, mid)
    iv = 0.5 * (lo + hi)
    bad = (price <= intrinsic + 1e-6) | (_bs(call, s, k, t, np.full(price.shape, 6.0), r) < price) | (iv >= 5.99)
    out: Array = np.where(bad, np.nan, iv)
    return out


def _above(s: Array, k: Array, t: Array, sigma: Array, mu: Array) -> Array:
    out: Array = _ncdf((np.log(s / k) + (mu - 0.5 * sigma * sigma) * t) / (sigma * np.sqrt(t)))
    return out


def _payoff(call: Array, s: Array, k: Array, t: Array, sigma: Array, mu: Array) -> Array:
    sq = sigma * np.sqrt(t)
    d1 = (np.log(s / k) + (mu + 0.5 * sigma * sigma) * t) / sq
    d2 = d1 - sq
    forward = s * np.exp(mu * t)
    out: Array = np.where(call, forward * _ncdf(d1) - k * _ncdf(d2), k * _ncdf(-d2) - forward * _ncdf(-d1))
    return out


# ── what is known about each underlying ──────────────────────────────────────────────────────────
@dataclass
class Underlying:
    symbol: str
    price: float
    observed_at: str | None
    source: str
    chains: dict[date, list[OptionQuote]]
    detail: dict[str, Any] = field(default_factory=dict)
    committee: float | None = None
    z: float | None = None
    rv: float | None = None
    closes: list[float] = field(default_factory=list)
    iv30: float | None = None
    iv_rank: float | None = None
    earnings: tuple[date, date] | None = None
    past_moves: list[float] = field(default_factory=list)
    # occ → (volume, open interest, mid, underlying price, day) on the previous stored session
    prior: dict[str, tuple[int, int, float | None, float | None, date]] = field(default_factory=dict)
    average_volume: dict[str, float] = field(default_factory=dict)
    option_volume_avg: float | None = None

    def desk(self, name: str) -> float | None:
        d = (self.detail.get("desks") or {}).get(name) or {}
        return d.get("score")

    def feature(self, name: str) -> float | None:
        value = (self.detail.get("price") or {}).get(name)
        return None if value is None else float(value)

    @property
    def name(self) -> str | None:
        return self.detail.get("name")


@dataclass
class Context:
    session: date
    now: datetime
    rate: float
    ic: float | None
    ic_months: float
    ic_source: str | None
    calibration: dict[str, od.Calibration]
    watchlist: list[str]
    flagged: dict[str, dict[str, Any]] = field(default_factory=dict)   # occ → the last recorded unusual play of it


def _rate(market: StocksMarket, session: date) -> float:
    points = [p for p in market.rates("UST3M", session - timedelta(days=30)) if p.day <= session]
    return points[-1].value / 100 if points else DEFAULT_RATE


def committee_ic(research: StocksResearch) -> tuple[float | None, float, str | None]:
    """The committee's information coefficient: the live record once it has 12 independent months, else the rebuilt."""
    report = research.latest_validation() or {}
    committee = (report.get("desks") or {}).get("committee") or {}
    for source in ("live", "rebuilt"):
        ic = ((committee.get(source) or {}).get("ic")) or {}
        if ic.get("mean") is not None and (ic.get("independent") or 0) >= (12 if source == "live" else 1):
            return float(ic["mean"]), float(ic.get("independent") or 0), source
    return None, 0.0, None


def _realized(closes: Sequence[float]) -> float | None:
    if len(closes) < 21:
        return None
    rets = [math.log(closes[i] / closes[i - 1]) for i in range(len(closes) - 20, len(closes))]
    vol = statistics.pstdev(rets) * math.sqrt(252)
    return vol if vol > 0 and math.isfinite(vol) else None


def load(market: StocksMarket, research: StocksResearch, session: date) -> list[Underlying]:
    chains = market.chains_on(session)
    if not chains:
        return []
    symbols = sorted(chains)
    marks = ",".join("?" * len(symbols))   # placeholders only; values are bound
    details = {r["symbol"]: json.loads(r["detail_json"]) for r in research.conn.execute(
        f"SELECT symbol, detail_json FROM stk_scores_latest WHERE symbol IN ({marks})", symbols)}  # noqa: S608
    scores = [r[0] for r in research.conn.execute(
        "SELECT json_extract(detail_json, '$.committee.score') FROM stk_scores_latest") if r[0] is not None]
    mean = statistics.fmean(scores) if scores else 50.0
    sd = statistics.pstdev(scores) if len(scores) >= 10 else 0.0
    iv_rows: dict[str, list[tuple[int, float | None, float | None]]] = defaultdict(list)
    sql = ("SELECT underlying, day, json_extract(summary_json, '$.iv30') iv, json_extract(summary_json, "  # noqa: S608
           "'$.activity.volume') vol FROM stk_option_daily WHERE day >= ? AND day <= ? AND underlying IN ("
           + marks + ") ORDER BY day")                                  # placeholders only; values are bound
    for r in market.conn.execute(sql, (day_of(session - timedelta(days=400)), day_of(session), *symbols)):
        iv_rows[r["underlying"]].append((r["day"], r["iv"], r["vol"]))
    prior_days = market.option_days(session, PRIOR_SESSIONS)
    prior_chains = [market.chains_on(d, symbols) for d in prior_days]
    out = []
    for symbol in symbols:
        c = chains[symbol]
        price = c["price"]
        if not price or price <= 0:
            continue
        u = Underlying(symbol, float(price), c["observed_at"], c["source"], c["expirations"])
        u.detail = details.get(symbol, {})
        u.committee = (u.detail.get("committee") or {}).get("score")
        u.z = None if u.committee is None or sd <= 0 else (u.committee - mean) / sd
        rows = [dict(r) for r in market.conn.execute(
            "SELECT day, close FROM stk_bars WHERE symbol = ? AND day <= ? ORDER BY day DESC LIMIT ?",
            (symbol, day_of(session), HISTORY_SESSIONS))]
        rows.reverse()
        u.closes = [r["close"] for r in adjusted_bars(market.conn, symbol, rows)]
        u.rv = u.feature("vol_20d") or _realized(u.closes)
        history = iv_rows.get(symbol, [])
        current = [iv for day, iv, _ in history if day == day_of(session) and iv]
        u.iv30 = current[-1] if current else None
        if u.iv30:
            u.iv_rank = opt.iv_rank([iv for _, iv, _ in history if iv], u.iv30)[0]
        volumes = [v for day, _, v in history if day < day_of(session) and v]
        u.option_volume_avg = statistics.fmean(volumes[-20:]) if len(volumes) >= 3 else None
        events = u.detail.get("events") or {}
        window = events.get("next_window")
        if window:
            u.earnings = (date.fromisoformat(window[0]), date.fromisoformat(window[1]))
        u.past_moves = [abs(e["reaction"]) for e in events.get("earnings", []) if e.get("reaction") is not None][-8:]
        seen: dict[str, list[int]] = defaultdict(list)
        for k, (day, prior) in enumerate(zip(prior_days, prior_chains, strict=True)):
            item = prior.get(symbol) or {}
            for quotes in item.get("expirations", {}).values():
                for q in quotes:
                    seen[q.occ].append(q.volume)
                    if k == 0:
                        u.prior[q.occ] = (q.volume, q.open_interest, q.mid, item.get("price"), day)
        u.average_volume = {occ: statistics.fmean(v) for occ, v in seen.items() if len(v) >= 3}
        out.append(u)
    return out


# ── every quoted contract of one underlying, priced at once ──────────────────────────────────────
@dataclass
class Table:
    u: Underlying
    quotes: list[OptionQuote]
    call: Array
    k: Array
    dte: Array
    t: Array
    bid: Array
    ask: Array
    mid: Array
    spread: Array
    volume: Array
    oi: Array
    iv: Array
    delta: Array
    sigma: Array
    mu: Array
    itm_market: Array
    itm_kestrel: Array
    payoff: Array        # KESTREL expected payoff per share at expiration

    def __len__(self) -> int:
        return len(self.quotes)

    def ev_long(self) -> Array:
        out: Array = self.payoff - self.ask
        return out


def table(u: Underlying, ctx: Context) -> Table | None:
    quotes = []
    for expiration, qs in u.chains.items():
        if (expiration - ctx.session).days < 1:
            continue
        for q in qs:
            mid = q.mid
            if (mid is None or mid < 0.05 or not q.bid or q.bid <= 0 or (q.ask or 0) <= q.bid
                    or ((q.ask or 0) - q.bid) / mid > 0.5 or not 0.4 <= q.strike / u.price <= 2.5):
                continue
            quotes.append(q)
    if not quotes:
        return None
    call = np.array([q.kind == "call" for q in quotes])
    k = np.array([q.strike for q in quotes], dtype=float)
    dte = np.array([(q.expiration - ctx.session).days for q in quotes], dtype=float)
    t = np.maximum(dte, 0.25) / opt.YEAR_DAYS
    bid = np.array([q.bid for q in quotes], dtype=float)
    ask = np.array([q.ask for q in quotes], dtype=float)
    mid = (bid + ask) / 2
    s = np.full(len(quotes), u.price)
    iv = implied_vols(call, s, k, t, mid, ctx.rate)
    good = ~np.isnan(iv)
    safe_iv = np.where(good, iv, 0.3)
    sq = safe_iv * np.sqrt(t)
    d1 = (np.log(s / k) + (ctx.rate + 0.5 * safe_iv ** 2) * t) / sq
    delta = np.where(call, _ncdf(d1), _ncdf(d1) - 1.0)
    rv = u.rv or 0.0
    sigma = np.sqrt(od.IV_WEIGHT * safe_iv ** 2 + (1 - od.IV_WEIGHT) * rv ** 2) if rv else safe_iv
    used = 0.0 if not ctx.ic or ctx.ic <= 0 or u.z is None else (
        ctx.ic * ctx.ic_months / (ctx.ic_months + od.IC_SHRINK_MONTHS) * max(-3.0, min(3.0, u.z)))
    mu = ctx.rate + used * sigma * np.sqrt(np.minimum(t, od.IC_HORIZON_YEARS)) / t
    above_m = _above(s, k, t, safe_iv, np.full(len(quotes), ctx.rate))
    above_k = _above(s, k, t, sigma, mu)
    keep = np.where(good)[0]
    pick = [quotes[i] for i in keep]
    return Table(u, pick, call[keep], k[keep], dte[keep], t[keep], bid[keep], ask[keep], mid[keep],
                 ((ask - bid) / mid)[keep], np.array([q.volume for q in pick], dtype=float),
                 np.array([q.open_interest for q in pick], dtype=float), iv[keep], delta[keep], sigma[keep], mu[keep],
                 np.where(call, above_m, 1 - above_m)[keep], np.where(call, above_k, 1 - above_k)[keep],
                 _payoff(call, s, k, t, sigma, mu)[keep])


# ── candidates ───────────────────────────────────────────────────────────────────────────────────
@dataclass
class Candidate:
    category: str
    table: Table
    structure: str
    legs: list[tuple[int, str]]          # (row in the table, "buy" | "sell")
    score: float
    reasons: list[str]
    flags: list[str] = field(default_factory=list)
    unusual: dict[str, Any] | None = None


def _direction(u: Underlying) -> str | None:
    """Calls when the committee leans bullish (≥ 58), puts when it leans bearish (< 42): its own label bands."""
    if u.committee is not None:
        return "call" if u.committee >= 58 else "put" if u.committee < 42 else None
    return None


def _view(u: Underlying) -> str:
    c = u.detail.get("committee") or {}
    if c.get("score") is None:
        return "no committee view (not in the stock research universe)"
    return f"committee {c['score']:.0f} ({c.get('label', '').lower()})"


def _nearest(tb: Table, mask: Array, target_delta: float, lo: int, hi: int) -> int | None:
    m = mask & (tb.dte >= lo) & (tb.dte <= hi)
    rows = np.where(m)[0]
    if not len(rows):
        return None
    centre = (lo + hi) / 2
    expiries = sorted({float(tb.dte[i]) for i in rows}, key=lambda d: abs(d - centre))
    chosen = [int(i) for i in rows if tb.dte[i] == expiries[0]]
    return min(chosen, key=lambda i: (abs(abs(tb.delta[i]) - target_delta), tb.spread[i]))


def _liquid(tb: Table, spread: float, oi: int) -> Array:
    out: Array = (tb.spread <= spread) & (tb.oi >= oi)
    return out


def likely_itm(tb: Table, ctx: Context) -> list[Candidate]:
    d = _direction(tb.u)
    if d is None:
        return []
    m = (tb.call == (d == "call")) & _liquid(tb, 0.15, 100) & (tb.ask >= 0.10) & (tb.dte >= 7) & (tb.dte <= 60) & (
        np.abs(tb.delta) >= 0.25) & (np.abs(tb.delta) <= 0.55)
    rows = np.where(m)[0]
    if not len(rows):
        return []
    i = max(rows, key=lambda j: (tb.itm_kestrel[j], -tb.ask[j]))
    return [Candidate("likely_itm", tb, f"long_{d}", [(int(i), "buy")], float(tb.itm_kestrel[i]),
                      [f"{tb.u.symbol}: {_view(tb.u)}",
                       f"KESTREL {tb.itm_kestrel[i]:.0%} vs market {tb.itm_market[i]:.0%} chance in the money"])]


def edge(tb: Table, ctx: Context) -> list[Candidate]:
    m = _liquid(tb, 0.12, 100) & (tb.ask >= 0.10) & (tb.dte >= 7) & (tb.dte <= 60) & (np.abs(tb.delta) >= 0.15) & (
        np.abs(tb.delta) <= 0.70)
    rows = np.where(m)[0]
    if not len(rows):
        return []
    per_dollar = tb.ev_long() / tb.ask
    i = max(rows, key=lambda j: per_dollar[j])
    if per_dollar[i] < 0.05:
        return []
    kind = "call" if tb.call[i] else "put"
    why = [f"KESTREL expects ${tb.payoff[i]:.2f} a share back for ${tb.ask[i]:.2f} paid ({per_dollar[i]:+.0%})",
           f"Forecast volatility {tb.sigma[i]:.0%} vs implied {tb.iv[i]:.0%}"]
    if abs(tb.mu[i] - ctx.rate) > 1e-9:
        why.append(f"Committee tilt: {_view(tb.u)}")
    return [Candidate("edge", tb, f"long_{kind}", [(int(i), "buy")], float(per_dollar[i]), why)]


def unusual(tb: Table, ctx: Context) -> list[Candidate]:
    u = tb.u
    premium = tb.volume * tb.mid * 100
    avg = np.array([u.average_volume.get(q.occ) or (q.average_volume or 0) for q in tb.quotes], dtype=float)
    vol_avg = np.where(avg > 0, tb.volume / np.maximum(avg, 1), np.nan)
    vol_oi = tb.volume / np.maximum(tb.oi, 1)
    gate = (tb.volume >= 250) & (premium >= 25_000) & (tb.spread <= 0.35) & (
        (vol_oi >= 1.0) | (np.nan_to_num(vol_avg) >= 4))
    rows = np.where(gate)[0]
    if not len(rows):
        return []
    today_total = float(np.sum([q.volume for qs in u.chains.values() for q in qs]))
    surge = None if not u.option_volume_avg else today_total / u.option_volume_avg
    side_counts = Counter(bool(tb.call[i]) for i in rows)
    out = []
    for i in rows:
        q = tb.quotes[i]
        factors, score = [], 0.0
        score += 22 * min(1.0, math.log2(vol_oi[i] + 1) / math.log2(11))
        factors.append(f"volume {int(tb.volume[i]):,} = {vol_oi[i]:.1f}× open interest")
        if not math.isnan(vol_avg[i]):
            score += 18 * min(1.0, math.log2(vol_avg[i] + 1) / math.log2(21))
            factors.append(f"{vol_avg[i]:.1f}× its own average")
        score += 20 * max(0.0, min(1.0, (math.log10(premium[i]) - 4.4) / 2.6))
        factors.append(f"${premium[i]:,.0f} at the mid")
        if surge:
            score += 10 * max(0.0, min(1.0, (surge - 1) / 4))
            if surge >= 1.5:
                factors.append(f"whole chain {surge:.1f}× its usual volume")
        lean = "direction unknown"
        if q.last and tb.ask[i] > tb.bid[i]:
            where = (q.last - tb.bid[i]) / (tb.ask[i] - tb.bid[i])
            score += 8 * min(1.0, abs(where - 0.5) * 2)
            if where >= 0.75:
                lean = "bullish lean" if tb.call[i] else "bearish lean"
                factors.append("last trade at or above the ask")
            elif where <= 0.25:
                lean = "bearish lean" if tb.call[i] else "bullish lean"
                factors.append("last trade at or below the bid")
        otm = (q.strike / u.price - 1) if tb.call[i] else (1 - q.strike / u.price)
        if tb.dte[i] <= 21 and otm >= 0.03:
            score += 8
            factors.append(f"{otm:.0%} out of the money, {int(tb.dte[i])} days")
        prior = u.prior.get(q.occ)
        if prior and prior[2] and prior[3]:
            before = opt.implied_vol(q.kind, prior[2], prior[3], q.strike, opt.year_fraction(prior[4], q.expiration),
                                     ctx.rate)
            if before:
                change = float(tb.iv[i]) - before
                score += 7 * max(0.0, min(1.0, change / 0.05))
                if change >= 0.02:
                    factors.append(f"IV up {change * 100:.1f} points on the day")
        cluster = side_counts[bool(tb.call[i])]
        if cluster >= 3:
            score += 7 * min(1.0, (cluster - 2) / 3)
            factors.append(f"one of {cluster} unusual {q.kind}s on {u.symbol}")
        spans = u.earnings is not None and ctx.session <= u.earnings[0] <= q.expiration
        tier = ("🐋 Whale" if premium[i] >= 1_000_000 else "🧹 Sweep-like cluster" if cluster >= 3 and tb.dte[i] <= 30
                else "📅 Event bet" if spans else "🆕 Opening" if vol_oi[i] >= 2 else "⚡ Unusual")
        earlier = ctx.flagged.get(q.occ)
        if earlier:
            opened = int(tb.oi[i]) - earlier["open_interest"]
            confirmation = (f"Also flagged {earlier['session']}: open interest {opened:+,} since, "
                            f"{opened / max(earlier['volume'], 1):.0%} of that day's volume")
        else:
            confirmation = "Whether today's volume opened new positions shows in the next session's open interest"
        final = round(min(100.0, score), 1)
        info: dict[str, Any] = {"score": final, "tier": tier, "lean": lean, "factors": factors,
                "volume": int(tb.volume[i]), "premium": round(float(premium[i])), "vol_oi": round(float(vol_oi[i]), 2),
                "vol_avg": None if math.isnan(vol_avg[i]) else round(float(vol_avg[i]), 2),
                "chain_surge": None if not surge else round(surge, 2), "spans_earnings": spans,
                "confirmation": confirmation}
        out.append(Candidate("unusual", tb, f"long_{q.kind}", [(int(i), "buy")], final,
                             [f"{tier}: {lean} (inferred from the last trade in the quote, not an exchange print)",
                              f"{u.symbol}: {_view(u)}"], unusual=info))
    return sorted(out, key=lambda c: -c.score)[:2]


def _straddle(tb: Table, lo: int, hi: int, after: date | None = None) -> tuple[int, int] | None:
    rows = np.where(_liquid(tb, 0.15, 50) & (tb.dte >= lo) & (tb.dte <= hi))[0]
    if after is not None:
        rows = np.array([i for i in rows if tb.quotes[i].expiration > after], dtype=int)
    if not len(rows):
        return None
    centre = lo if after is not None else (lo + hi) / 2
    expiry = min({float(tb.dte[i]) for i in rows}, key=lambda d: abs(d - centre) if after is None else d)
    by_strike: dict[float, dict[bool, int]] = defaultdict(dict)
    for i in rows:
        if tb.dte[i] == expiry:
            by_strike[float(tb.k[i])][bool(tb.call[i])] = int(i)
    pairs = [(k, legs) for k, legs in by_strike.items() if True in legs and False in legs]
    if not pairs:
        return None
    _, legs = min(pairs, key=lambda kv: abs(kv[0] - tb.u.price))
    return legs[True], legs[False]


def cheap_vol(tb: Table, ctx: Context) -> list[Candidate]:
    u = tb.u
    ratio = None if not (u.iv30 and u.rv) else u.iv30 / u.rv
    if not ((ratio is not None and ratio <= 0.9) or (u.iv_rank is not None and u.iv_rank <= 20)):
        return []
    legs = _straddle(tb, 14, 60)
    if legs is None:
        return []
    why = [f"IV30 {u.iv30:.0%} vs realized {u.rv:.0%} ({ratio:.2f}×)" if ratio else "IV rank low",
           f"IV rank {u.iv_rank:.0f} of 100" if u.iv_rank is not None else "IV rank needs 20 sessions of history"]
    return [Candidate("cheap_vol", tb, "long_straddle", [(legs[0], "buy"), (legs[1], "buy")],
                      (1 / ratio) if ratio else 1.0 - (u.iv_rank or 0) / 100, why)]


def earnings(tb: Table, ctx: Context) -> list[Candidate]:
    u = tb.u
    if u.earnings is None or len(u.past_moves) < 4 or not 1 <= (u.earnings[0] - ctx.session).days <= 21:
        return []
    legs = _straddle(tb, 1, 45, after=u.earnings[1])
    if legs is None:
        return []
    priced = (tb.ask[legs[0]] + tb.ask[legs[1]]) / u.price
    typical = statistics.median(u.past_moves)
    flag = ("Priced below its usual results-day move" if typical >= priced
            else "Priced above its usual results-day move: selling premium may fit better")
    return [Candidate("earnings", tb, "long_straddle", [(legs[0], "buy"), (legs[1], "buy")], typical / priced,
                      [f"Results expected {u.earnings[0]:%b %-d}–{u.earnings[1]:%b %-d} (estimated from past reports)",
                       f"Options price ±{priced:.1%}; its last {len(u.past_moves)} results days moved a median "
                       f"{typical:.1%}", flag])]


def trend_calls(tb: Table, ctx: Context) -> list[Candidate]:
    u = tb.u
    m, above = u.desk("momentum"), u.feature("above_sma200")
    if m is None or m < 75 or above is None or above <= 0:
        return []
    i = _nearest(tb, tb.call & _liquid(tb, 0.15, 100), 0.60, 30, 75)
    if i is None:
        return []
    return [Candidate("trend_calls", tb, "long_call", [(i, "buy")], float(m),
                      [f"Momentum {m:.0f}, {above:.0%} above its 200-day average", f"{u.symbol}: {_view(u)}"])]


def bear_puts(tb: Table, ctx: Context) -> list[Candidate]:
    u = tb.u
    v, m, above = u.desk("valuation"), u.desk("momentum"), u.feature("above_sma200")
    flags = (u.detail.get("events") or {}).get("red_flags") or []
    weak = v is not None and m is not None and above is not None and v <= 30 and m <= 35 and above < 0
    flagged = bool(flags) and (m is None or m <= 50)
    if not (weak or flagged):
        return []
    i = _nearest(tb, ~tb.call & _liquid(tb, 0.15, 100), 0.45, 30, 75)
    if i is None:
        return []
    why = [f"Valuation {v:.0f}, momentum {m:.0f}, {above:.0%} vs its 200-day" if weak else
           f"Red flag: {flags[0].get('text')}"]
    return [Candidate("bear_puts", tb, "long_put", [(i, "buy")], (100 - (v or 50)) + (100 - (m or 50)) + 20 * flagged,
                      [*why, f"{u.symbol}: {_view(u)}"])]


def squeeze(tb: Table, ctx: Context) -> list[Candidate]:
    u = tb.u
    facts = ((u.detail.get("desks") or {}).get("shorts") or {}).get("facts") or {}
    sq = facts.get("squeeze")
    if sq is None or sq < 60:
        return []
    i = _nearest(tb, tb.call & _liquid(tb, 0.20, 50), 0.30, 14, 45)
    if i is None:
        return []
    shorts = u.detail.get("shorts") or {}
    return [Candidate("squeeze", tb, "long_call", [(i, "buy")], float(sq),
                      [f"Squeeze score {sq:.0f}" + (f": {shorts['short_pct']:.0%} of shares short"
                                                    if shorts.get("short_pct") else ""),
                       f"{u.symbol}: {_view(u)}"])]


def sell_premium(tb: Table, ctx: Context) -> list[Candidate]:
    u = tb.u
    rich = (u.iv_rank is not None and u.iv_rank >= 60) or (u.iv30 and u.rv and u.iv30 / u.rv >= 1.3)
    if not rich:
        return []
    kind = "put" if u.committee is None or u.committee >= 50 else "call" if u.committee <= 45 else None
    if kind is None:
        return []
    is_call = kind == "call"
    short = _nearest(tb, (tb.call == is_call) & _liquid(tb, 0.15, 100), 0.25, 21, 60)
    if short is None:
        return []
    target = tb.k[short] * (1.05 if is_call else 0.95)
    same = [i for i in range(len(tb)) if tb.dte[i] == tb.dte[short] and bool(tb.call[i]) == is_call
            and (tb.k[i] > tb.k[short] if is_call else tb.k[i] < tb.k[short]) and tb.spread[i] <= 0.25]
    if not same:
        return []
    long = min(same, key=lambda i: abs(tb.k[i] - target))
    credit = tb.bid[short] - tb.ask[long]
    width = abs(tb.k[long] - tb.k[short])
    if credit < 0.10 * width:
        return []
    expected = tb.payoff[short] - tb.payoff[long]
    max_loss = (width - credit) * 100 + 2 * FEE
    ev = (credit - expected) * 100 - 2 * FEE
    why = [f"IV rank {u.iv_rank:.0f}" if u.iv_rank is not None else f"IV30 {u.iv30:.0%} vs realized {u.rv:.0%}",
           f"{u.symbol}: {_view(u)}",
           f"Collect ${credit * 100:,.0f}, risk ${max_loss:,.0f}; KESTREL expects {'+' if ev >= 0 else '−'}${abs(ev):,.0f} "
           "per spread"]
    structure = "bear_call_credit" if is_call else "bull_put_credit"
    return [Candidate("sell_premium", tb, structure, [(short, "sell"), (long, "buy")], ev / max_loss, why)]


def lottos(tb: Table, ctx: Context) -> list[Candidate]:
    u = tb.u
    otm = np.where(tb.call, tb.k / u.price - 1, 1 - tb.k / u.price)
    m = (tb.ask >= 0.05) & (tb.ask <= 0.50) & (tb.dte >= 1) & (tb.dte <= 14) & (otm >= 0.03) & (tb.oi >= 50) & (
        tb.volume >= 50) & (tb.spread <= 0.35)
    rows = np.where(m)[0]
    if not len(rows):
        return []
    move = np.exp(tb.sigma * np.sqrt(tb.t))
    target = np.where(tb.call, u.price * move, u.price / move)
    multiple = np.where(tb.call, np.maximum(target - tb.k, 0), np.maximum(tb.k - target, 0)) / tb.ask
    i = max(rows, key=lambda j: multiple[j] * tb.itm_kestrel[j])
    if multiple[i] <= 0:
        return []
    return [Candidate("lottos", tb, f"long_{'call' if tb.call[i] else 'put'}", [(int(i), "buy")],
                      float(multiple[i] * tb.itm_kestrel[i]),
                      [f"Pays {multiple[i]:.1f}× its cost if {u.symbol} moves one standard deviation "
                       f"({(move[i] - 1):.1%}) {'up' if tb.call[i] else 'down'} by expiry",
                       f"{int(tb.dte[i])} days, {otm[i]:.0%} out of the money"],
                      flags=["Most lottos expire worthless: size it as money you can lose entirely"])]


def leaps(tb: Table, ctx: Context) -> list[Candidate]:
    u = tb.u
    conviction = u.committee if u.committee is not None else (60.0 if u.symbol in ETFS else None)
    if conviction is None or conviction < 60:
        return []
    i = _nearest(tb, tb.call & _liquid(tb, 0.10, 20), 0.75, 180, 800)
    if i is None or abs(tb.delta[i]) < 0.6:
        return []
    extrinsic = (tb.ask[i] - max(0.0, u.price - tb.k[i])) / u.price
    return [Candidate("leaps", tb, "long_call", [(i, "buy")], conviction - 100 * extrinsic,
                      [f"{u.symbol}: {_view(u)}" if u.committee is not None else f"{u.symbol}: broad-market ETF",
                       f"Time value paid: {extrinsic:.1%} of the stock price for {int(tb.dte[i])} days"])]


def watchlist(tb: Table, ctx: Context) -> list[Candidate]:
    u = tb.u
    if u.symbol not in ctx.watchlist:
        return []
    d = "call" if u.committee is None or u.committee >= 50 else "put"
    m = (tb.call == (d == "call")) & _liquid(tb, 0.15, 50) & (tb.dte >= 14) & (tb.dte <= 60) & (
        np.abs(tb.delta) >= 0.35) & (np.abs(tb.delta) <= 0.60)
    rows = np.where(m)[0]
    if not len(rows):
        return []
    per_dollar = tb.ev_long() / tb.ask
    i = max(rows, key=lambda j: per_dollar[j])
    return [Candidate("watchlist", tb, f"long_{d}", [(int(i), "buy")], float(per_dollar[i]),
                      [f"{u.symbol}: {_view(u)}", f"KESTREL value per dollar {per_dollar[i]:+.0%}"])]


SELECTORS: dict[str, Callable[[Table, Context], list[Candidate]]] = {
    "likely_itm": likely_itm, "edge": edge, "unusual": unusual, "cheap_vol": cheap_vol, "earnings": earnings,
    "trend_calls": trend_calls, "bear_puts": bear_puts, "squeeze": squeeze, "sell_premium": sell_premium,
    "lottos": lottos, "leaps": leaps, "watchlist": watchlist}


# ── a candidate becomes a play ───────────────────────────────────────────────────────────────────
TYPES = {"long_call": ("BUY", "CALL"), "long_put": ("BUY", "PUT"), "long_straddle": ("BUY", "STRADDLE"),
         "bull_put_credit": ("SELL", "PUT CREDIT SPREAD"), "bear_call_credit": ("SELL", "CALL CREDIT SPREAD")}


def _sessions_to(session: date, expiration: date) -> int:
    return max(1, len(cal.trading_days(session + timedelta(days=1), expiration)))


def finish(c: Candidate, ctx: Context) -> dict[str, Any]:
    tb, u = c.table, c.table.u
    action, kind_label = TYPES[c.structure]
    legs: list[dict[str, Any]] = []
    net_delta = net_gamma = net_theta = net_vega = 0.0
    for i, side in c.legs:
        q = tb.quotes[i]
        g = opt.greeks(q.kind, u.price, q.strike, float(tb.t[i]), float(tb.iv[i]), ctx.rate)
        sign = 1 if side == "buy" else -1
        net_delta += sign * g.delta
        net_gamma += sign * g.gamma
        net_theta += sign * g.theta_per_day
        net_vega += sign * g.vega_per_point
        legs.append({"occ": q.occ, "kind": q.kind, "side": side, "strike": q.strike, "expiration": q.expiration.isoformat(),
                     "bid": q.bid, "ask": q.ask, "mid": round(float(tb.mid[i]), 4), "last": q.last,
                     "iv": round(float(tb.iv[i]), 4), "delta": round(g.delta, 4), "gamma": round(g.gamma, 5),
                     "theta": round(g.theta_per_day, 4), "vega": round(g.vega_per_point, 4), "volume": q.volume,
                     "open_interest": q.open_interest,
                     "quoted_at": q.quoted_at.isoformat() if q.quoted_at else None})
    first = tb.quotes[c.legs[0][0]]
    i0 = c.legs[0][0]
    expiration = first.expiration
    t = float(tb.t[i0])
    fees = FEE * len(legs)
    width = 0.0
    if action == "BUY":
        entry = sum(float(tb.ask[i]) if side == "buy" else -float(tb.bid[i]) for i, side in c.legs)
    else:
        entry = sum(float(tb.bid[i]) if side == "sell" else -float(tb.ask[i]) for i, side in c.legs)
    history = od.History.build(u.closes, _sessions_to(ctx.session, expiration))

    def view(kind: str, buyer: bool, k: float, iv: float, premium: float, cal: od.Calibration | None = None) -> od.Odds:
        return od.odds(kind=kind, action="buy" if buyer else "sell", s=u.price, k=k, t=t, iv=iv, rv=u.rv, r=ctx.rate,
                       premium=premium, history=history, ic=ctx.ic, ic_months=ctx.ic_months, z=u.z, calibration=cal)
    if c.structure == "long_straddle":
        k, iv = first.strike, float((tb.iv[c.legs[0][0]] + tb.iv[c.legs[1][0]]) / 2)
        up = view("call", True, k + entry, iv, 0.0)
        down = view("put", True, k - entry, iv, 0.0)
        raw = up.itm_kestrel_raw + down.itm_kestrel_raw
        chance = {"itm_market": up.itm_market + down.itm_market,
                  "itm_history": None if up.itm_history is None or down.itm_history is None
                  else up.itm_history + down.itm_history,
                  "itm_kestrel_raw": raw, "itm_kestrel": ctx.calibration["move"].apply(raw),
                  "touch_kestrel": min(1.0, od.touch(u.price, k + entry, t, up.sigma, up.drift)
                                       + od.touch(u.price, k - entry, t, up.sigma, up.drift))}
        chance["profit_kestrel"] = chance["itm_kestrel"]
        payoff = (od.expected_payoff("call", u.price, k, t, up.sigma, up.drift)
                  + od.expected_payoff("put", u.price, k, t, up.sigma, up.drift))
        breakeven, breakeven_move = k + entry, entry / u.price
        event, event_kind, event_strike, event_width, label = "move", "straddle", k, entry, "Chance of a move past break-even"
        sigma = up.sigma
        max_gain = None
    else:
        short_leg = c.structure.endswith("credit")
        ref = c.legs[0][0]                       # the long leg of a purchase; the short leg of a credit spread
        q = tb.quotes[ref]
        o = view(q.kind, action == "BUY", q.strike, float(tb.iv[ref]), entry, ctx.calibration["itm"])
        already = u.price > q.strike if q.kind == "call" else u.price < q.strike
        chance = {"itm_market": o.itm_market, "itm_history": o.itm_history, "itm_kestrel_raw": o.itm_kestrel_raw,
                  "itm_kestrel": o.itm_kestrel, "profit_kestrel": o.profit_kestrel,
                  "touch_kestrel": None if already else o.touch_kestrel}
        payoff = sum((1 if side == "buy" else -1) * od.expected_payoff(tb.quotes[i].kind, u.price, tb.quotes[i].strike, t,
                                                                         o.sigma, o.drift) for i, side in c.legs)
        breakeven = q.strike + entry if q.kind == "call" else q.strike - entry
        breakeven_move = breakeven / u.price - 1
        event, event_kind, event_strike, event_width = "itm", q.kind, q.strike, 0.0
        label = "Chance it expires worthless" if short_leg else "Chance in the money"
        sigma = o.sigma
        if short_leg:
            width = abs(tb.quotes[c.legs[1][0]].strike - q.strike)
            max_gain = entry * 100 - fees
        else:
            max_gain = None if q.kind == "call" else (q.strike - entry) * 100 - fees
    if action == "BUY":
        cost = entry * 100 + fees
        max_loss, credit = cost, None
        ev = (payoff - entry) * 100 - fees
    else:
        credit = entry * 100 - fees
        max_loss = (width - entry) * 100 + fees
        cost = None
        ev = (entry + payoff) * 100 - fees          # payoff is the spread's expected (negative) value to its seller
    spreads = [float(tb.spread[i]) for i, _ in c.legs]
    ois = [int(tb.oi[i]) for i, _ in c.legs]
    grade = "A" if max(spreads) <= 0.05 and min(ois) >= 1000 else "B" if max(spreads) <= 0.12 and min(ois) >= 250 else "C"
    quoted = [leg["quoted_at"] for leg in legs if leg["quoted_at"]]
    earnings = u.earnings[0].isoformat() if u.earnings and ctx.session <= u.earnings[0] <= expiration else None
    flags = list(c.flags)
    if earnings:
        flags.append(f"Results expected before expiry (window starts {earnings}): moves can gap past stops")
    if grade == "C":
        flags.append("Thin or wide market: use a limit order near the mid, not the ask")
    play = {
        "symbol": u.symbol, "name": u.name, "category": c.category, "action": action, "type": kind_label,
        "structure": c.structure, "legs": legs, "strike": first.strike, "expiration": expiration.isoformat(),
        "dte": int(tb.dte[i0]), "underlying_price": u.price, "bid": legs[0]["bid"], "ask": legs[0]["ask"],
        "mid": legs[0]["mid"], "spread_pct": round(max(spreads), 4), "volume": legs[0]["volume"],
        "open_interest": legs[0]["open_interest"],
        "vol_oi": round(legs[0]["volume"] / max(legs[0]["open_interest"], 1), 2) if legs[0]["volume"] else None,
        "iv": legs[0]["iv"], "iv_rank": None if u.iv_rank is None else round(u.iv_rank, 1),
        "delta": round(net_delta, 4), "gamma": round(net_gamma, 5), "theta": round(net_theta, 4),
        "vega": round(net_vega, 4), "entry": round(entry, 4), "cost": None if cost is None else round(cost, 2),
        "credit": None if credit is None else round(credit, 2), "max_loss": round(max_loss, 2),
        "max_gain": None if max_gain is None else round(max_gain, 2), "breakeven": round(breakeven, 2),
        "breakeven_move": round(breakeven_move, 4), "expected_value": round(ev, 2),
        "expected_move": round(sigma * math.sqrt(t), 4), "chance_label": label, "event": event,
        "event_kind": event_kind, "event_strike": event_strike, "event_width": round(event_width, 4),
        **{k: (None if v is None else round(float(v), 4)) for k, v in chance.items()},
        "in_the_money": c.structure != "long_straddle" and (
            u.price > event_strike if event_kind == "call" else u.price < event_strike),
        "earnings": earnings, "liquidity": grade, "score": round(c.score, 4), "reasons": c.reasons, "flags": flags,
        "unusual": c.unusual, "quoted_at": min(quoted) if quoted else u.observed_at, "source": u.source}
    return play


# ── the track record ─────────────────────────────────────────────────────────────────────────────
def calibration(research: StocksResearch) -> dict[str, od.Calibration]:
    rows: dict[str, tuple[list[float], list[bool]]] = {"itm": ([], []), "move": ([], [])}
    for r in research.conn.execute(
            "SELECT p.event, p.itm_kestrel_raw, o.hit FROM stk_plays p JOIN stk_play_outcomes o USING (session, category, rank)"
            " WHERE p.itm_kestrel_raw IS NOT NULL"):
        rows[r["event"]][0].append(float(r["itm_kestrel_raw"]))
        rows[r["event"]][1].append(bool(r["hit"]))
    return {event: od.Calibration.learn(p, h) for event, (p, h) in rows.items()}


def records(research: StocksResearch) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    grouped: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for r in research.conn.execute(
            "SELECT p.category, p.itm_kestrel, p.itm_kestrel_raw, p.action, o.hit, o.net_return FROM stk_plays p "
            "JOIN stk_play_outcomes o USING (session, category, rank)"):
        grouped[r["category"]].append(r)
    for category, rows in grouped.items():
        predicted = [r["itm_kestrel"] for r in rows if r["itm_kestrel"] is not None]
        out[category] = {"resolved": len(rows), "itm_rate": sum(r["hit"] for r in rows) / len(rows),
                         "predicted_itm": statistics.fmean(predicted) if predicted else None,
                         "median_return": statistics.median(r["net_return"] for r in rows),
                         "brier": od.brier([r["itm_kestrel_raw"] for r in rows if r["itm_kestrel_raw"] is not None],
                                           [bool(r["hit"]) for r in rows if r["itm_kestrel_raw"] is not None])}
    return out



def record(research: StocksResearch, session: date, categories: dict[str, list[dict[str, Any]]], now: datetime) -> list[str]:
    """Append each category's plays for a closed session once (an empty category too). Returns the categories added."""
    added = []
    m = method()
    for category, plays in categories.items():
        with transaction(research.conn):
            cur = research.conn.execute("INSERT OR IGNORE INTO stk_play_days(session, category, size, method, recorded_at)"
                                        " VALUES (?, ?, ?, ?, ?)", (day_of(session), category, len(plays), m, to_iso(now)))
            if cur.rowcount == 0:
                continue
            research.conn.executemany(
                "INSERT INTO stk_plays(session, category, rank, symbol, action, structure, expiration, event, event_kind,"
                " event_strike, event_width, entry, max_loss, legs_json, underlying, itm_market, itm_history, itm_kestrel,"
                " itm_kestrel_raw, profit_kestrel, play_json, method, recorded_at) VALUES"
                " (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [(day_of(session), category, p["rank"], p["symbol"], p["action"], p["structure"],
                  day_of(date.fromisoformat(p["expiration"])), p["event"], p["event_kind"], p["event_strike"],
                  p["event_width"], p["entry"], p["max_loss"], canonical_json(p["legs"]), p["underlying_price"],
                  p["itm_market"], p["itm_history"], p["itm_kestrel"], p["itm_kestrel_raw"], p["profit_kestrel"],
                  canonical_json(p), m, to_iso(now)) for p in plays])
            added.append(category)
    return added


# ── twins: the random baseline each play must beat ──────────────────────────────────────────────
TWIN_SEARCH = 6                     # other stocks tried before a play is left without a twin


def _twin_leg(tb: Table, i: int, side: str) -> dict[str, Any]:
    q = tb.quotes[i]
    return {"occ": q.occ, "kind": q.kind, "side": side, "strike": q.strike, "expiration": q.expiration.isoformat(),
            "bid": float(tb.bid[i]), "ask": float(tb.ask[i]), "mid": round(float(tb.mid[i]), 4),
            "iv": round(float(tb.iv[i]), 4), "delta": round(float(tb.delta[i]), 4), "underlying": tb.u.price}


def twin(play: dict[str, Any], tables: Sequence[Table], session: date) -> dict[str, Any] | None:
    """A random contract on another stock with the same structure, expiry (or the nearest within a week) and delta.
    Seeded by the play's identity, so the same record always draws the same twin."""
    rng = random.Random(f"{session.isoformat()}:{play['category']}:{play['rank']}")  # noqa: S311 - a reproducible draw, not a secret
    dte = (date.fromisoformat(play["expiration"]) - session).days
    pool = []
    for tb in tables:
        if tb.u.symbol == play["symbol"] or not len(tb):
            continue
        gaps = np.abs(tb.dte - dte)
        if gaps.min() <= 7:
            pool.append((tb, float(tb.dte[int(np.argmin(gaps))])))
    exact = [x for x in pool if x[1] == dte]
    pool = exact or pool
    rng.shuffle(pool)
    legs, structure = play["legs"], play["structure"]
    for tb, days in pool[:TWIN_SEARCH]:
        on = tb.dte == days
        chosen: list[tuple[int, str]] = []
        if structure in ("long_call", "long_put"):
            kind = legs[0]["kind"]
            rows = np.where(on & (tb.call == (kind == "call")))[0]
            if len(rows):
                i = int(min(rows, key=lambda r: abs(abs(tb.delta[r]) - abs(legs[0]["delta"]))))
                chosen = [(i, "buy")]
        elif structure == "long_straddle":
            calls = np.where(on & tb.call)[0]
            if len(calls):
                c = int(min(calls, key=lambda r: abs(tb.delta[r] - 0.5)))
                puts = np.where(on & ~tb.call & (tb.k == tb.k[c]))[0]
                if len(puts):
                    chosen = [(c, "buy"), (int(puts[0]), "buy")]
        else:                                                   # credit spreads: short leg first
            short, long_ = legs[0], legs[1]
            kind = short["kind"]
            rows = np.where(on & (tb.call == (kind == "call")))[0]
            if len(rows):
                i = int(min(rows, key=lambda r: abs(abs(tb.delta[r]) - abs(short["delta"]))))
                width = abs(long_["strike"] - short["strike"]) / max(play["underlying_price"], 1e-9) * tb.u.price
                target = tb.k[i] + width if kind == "call" else tb.k[i] - width
                further = rows[(tb.k[rows] > tb.k[i]) if kind == "call" else (tb.k[rows] < tb.k[i])]
                if len(further):
                    j = int(min(further, key=lambda r: abs(tb.k[r] - target)))
                    chosen = [(i, "sell"), (j, "buy")]
        if not chosen:
            continue
        twin_legs = [_twin_leg(tb, i, side) for i, side in chosen]
        if play["action"] == "BUY":
            entry = sum(leg["ask"] if leg["side"] == "buy" else -leg["bid"] for leg in twin_legs)
            max_loss = entry * 100 + FEE * len(twin_legs)
        else:
            entry = sum(leg["bid"] if leg["side"] == "sell" else -leg["ask"] for leg in twin_legs)
            width = abs(twin_legs[1]["strike"] - twin_legs[0]["strike"])
            max_loss = (width - entry) * 100 + FEE * len(twin_legs)
        if entry <= 0 or max_loss <= 0:
            continue
        return {"symbol": tb.u.symbol, "action": play["action"], "structure": structure,
                "expiration": twin_legs[0]["expiration"], "legs": twin_legs, "entry": round(entry, 4),
                "max_loss": round(max_loss, 2)}
    return None


def record_twins(research: StocksResearch, session: date, plays: dict[str, list[dict[str, Any]]],
                 tables: Sequence[Table], now: datetime) -> int:
    rows = []
    for category, items in plays.items():
        for p in items:
            t = twin(p, tables, session)
            if t is not None:
                rows.append((day_of(session), category, p["rank"], t["symbol"], t["action"], t["structure"],
                             day_of(date.fromisoformat(t["expiration"])), canonical_json(t["legs"]), t["entry"],
                             t["max_loss"], to_iso(now)))
    with transaction(research.conn):
        research.conn.executemany("INSERT OR IGNORE INTO stk_play_twins(session, category, rank, symbol, action, "
                                  "structure, expiration, legs_json, entry, max_loss, recorded_at) VALUES "
                                  "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
    return len(rows)


def settle_value(legs: Sequence[dict[str, Any]], close: float) -> float:
    """Per-share value at expiration of the position as held (long +, short −)."""
    total = 0.0
    for leg in legs:
        intrinsic = max(0.0, close - leg["strike"]) if leg["kind"] == "call" else max(0.0, leg["strike"] - close)
        total += intrinsic if leg["side"] == "buy" else -intrinsic
    return total


def grade(market: StocksMarket, research: StocksResearch, now: datetime) -> int:
    """Resolve every recorded play whose expiration has a stored close. A split in between leaves it unresolved."""
    latest = market.conn.execute("SELECT MAX(day) FROM stk_bar_days WHERE rows > 0").fetchone()[0]
    if latest is None:
        return 0
    pending = research.conn.execute(
        "SELECT p.* FROM stk_plays p LEFT JOIN stk_play_outcomes o USING (session, category, rank) "
        "WHERE o.session IS NULL AND p.expiration <= ?", (latest,)).fetchall()
    resolved = 0
    for p in pending:
        bar = market.conn.execute("SELECT day, close FROM stk_bars WHERE symbol = ? AND day <= ? AND day >= ? "
                                  "ORDER BY day DESC LIMIT 1",
                                  (p["symbol"], p["expiration"], p["expiration"] - 4)).fetchone()
        split = market.conn.execute("SELECT 1 FROM stk_splits WHERE symbol = ? AND execution_date > ? AND "
                                    "execution_date <= ?", (p["symbol"], p["session"], p["expiration"])).fetchone()
        if bar is None or split is not None:
            continue
        close = float(bar["close"])
        legs = json.loads(p["legs_json"])
        value = settle_value(legs, close)
        n_legs = len(legs)
        if p["event"] == "move":
            hit = abs(close - p["event_strike"]) > p["event_width"]
        else:
            hit = close > p["event_strike"] if p["event_kind"] == "call" else close < p["event_strike"]
        fees = FEE * n_legs / 100
        if p["action"] == "BUY":
            net = (value - p["entry"] - fees) / (p["entry"] + fees)
        else:
            net = (p["entry"] + value - fees) / (p["max_loss"] / 100)
        research.conn.execute("INSERT OR IGNORE INTO stk_play_outcomes(session, category, rank, resolved_at, "
                              "underlying_close, hit, payoff, net_return) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                              (p["session"], p["category"], p["rank"], to_iso(now), close, int(hit), value, net))
        resolved += 1
    return resolved


def flagged_before(research: StocksResearch, session: date) -> dict[str, dict[str, Any]]:
    """The unusual plays recorded on the latest earlier session, by contract."""
    row = research.conn.execute("SELECT MAX(session) FROM stk_play_days WHERE category = 'unusual' AND session < ?",
                                (day_of(session),)).fetchone()
    if row is None or row[0] is None:
        return {}
    out = {}
    for r in research.conn.execute("SELECT play_json FROM stk_plays WHERE category = 'unusual' AND session = ?", (row[0],)):
        p = json.loads(r["play_json"])
        leg = p["legs"][0]
        out[leg["occ"]] = {"session": date.fromordinal(row[0]).isoformat(), "symbol": p["symbol"], "kind": leg["kind"],
                           "strike": leg["strike"], "expiration": leg["expiration"], "volume": leg["volume"],
                           "open_interest": leg["open_interest"], "entry": p["entry"],
                           "tier": (p.get("unusual") or {}).get("tier")}
    return out


def followups(flagged: dict[str, dict[str, Any]], tables: Sequence[Table]) -> list[dict[str, Any]]:
    """Did the last session's flagged volume open positions? Today's open interest against that day's, per contract."""
    now = {q.occ: (int(tb.oi[i]), float(tb.mid[i])) for tb in tables for i, q in enumerate(tb.quotes)}
    out = []
    for occ, f in flagged.items():
        if occ not in now:
            out.append({**f, "occ": occ, "open_interest_now": None, "opened": None, "price_change": None,
                        "verdict": "Not in today's collected chains"})
            continue
        oi, mid = now[occ]
        opened = (oi - f["open_interest"]) / max(f["volume"], 1)
        verdict = ("Opened: new positions held overnight" if opened >= 0.5 else
                   "Mostly closing trades or same-day round trips" if opened <= 0.1 else "Partly opened")
        out.append({**f, "occ": occ, "open_interest_now": oi, "opened": round(opened, 3),
                    "price_change": round(mid / f["entry"] - 1, 4) if f["entry"] else None, "verdict": verdict})
    return sorted(out, key=lambda r: -(r["opened"] if r["opened"] is not None else -9))


def paper_records(ledger: PaperBook | None) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    if ledger is None:
        return out
    for c in CATEGORIES:
        if not c.paper or c.paper not in BY_STRATEGY:
            continue
        stats = trade_stats(ledger.positions(c.paper, state="CLOSED"))
        st = BY_STRATEGY[c.paper]
        out[c.id] = {"strategy": c.paper, "title": st.title, "trades": stats["trades"], "win_rate": stats["win_rate"],
                     "avg_return": stats["avg_return"],
                     "grade": (f"too early ({stats['trades']} of {MIN_PAPER_TRADES} closed trades)"
                               if stats["trades"] < MIN_PAPER_TRADES else "see the PAPER tab for its graded record")}
    return out


# ── the build ────────────────────────────────────────────────────────────────────────────────────
def session_closed(session: date, now: datetime) -> bool:
    return now >= datetime.combine(session, time(16, 0), cal.NEW_YORK) + CLOSE_GRACE


def signature(market: StocksMarket, research: StocksResearch, session: date, now: datetime) -> str:
    chains = market.conn.execute("SELECT COUNT(*), MAX(observed_at) FROM stk_option_chains WHERE day = ?",
                                 (day_of(session),)).fetchone()
    parts = {"session": session.isoformat(), "chains": list(chains), "version": VERSION, "method": method(),
             "closed": session_closed(session, now),
             "scores": research.conn.execute("SELECT MAX(computed_at) FROM stk_scores_latest").fetchone()[0],
             "validation": research.conn.execute("SELECT MAX(id) FROM stk_validation").fetchone()[0],
             "outcomes": research.conn.execute("SELECT COUNT(*) FROM stk_play_outcomes").fetchone()[0],
             "watchlist": research.watchlist(),
             "bars": market.conn.execute("SELECT MAX(day) FROM stk_bar_days WHERE rows > 0").fetchone()[0]}
    return sha256_hex(canonical_json(parts))


def build(market: StocksMarket, research: StocksResearch, now: datetime, *, ledger: PaperBook | None = None,
          previous: dict[str, Any] | None = None) -> dict[str, Any]:
    """Score the latest options session into plays; record them once the session has closed; grade expired ones."""
    graded = grade(market, research, now)
    expected = quote_session(now)
    newest = market.conn.execute("SELECT MAX(day) FROM stk_option_chains").fetchone()[0]
    latest = None if newest is None else date.fromordinal(newest)
    if latest is None:
        return {"version": VERSION, "asof": None, "generated_at": now.isoformat(), "categories": [],
                "message": "No option chains are stored yet. Connect Schwab or Tradier in Settings; chains are collected "
                           "every 15 minutes while the market is open.", "graded": graded}
    session = min(latest, expected)
    key = signature(market, research, session, now)
    if previous and previous.get("input_hash") == key and not graded:
        return previous
    ic, months, source = committee_ic(research)
    ctx = Context(session, now, _rate(market, session), ic, months, source, calibration(research), research.watchlist(),
                  flagged_before(research, session))
    tables = [tb for u in load(market, research, session) if (tb := table(u, ctx)) is not None]
    out_categories = []
    recorded: dict[str, list[dict[str, Any]]] = {}
    track = records(research)
    paper = paper_records(ledger)
    for cat in CATEGORIES:
        found = [c for tb in tables for c in SELECTORS[cat.id](tb, ctx)]
        found.sort(key=lambda c: (-c.score, c.table.u.symbol))
        per_symbol: Counter[str] = Counter()
        plays: list[dict[str, Any]] = []
        for c in found:
            if per_symbol[c.table.u.symbol] >= (2 if cat.id == "unusual" else 1):
                continue
            per_symbol[c.table.u.symbol] += 1
            plays.append({**finish(c, ctx), "rank": len(plays) + 1})
            if len(plays) >= PER_CATEGORY:
                break
        recorded[cat.id] = plays
        entry = {"id": cat.id, "title": cat.title, "emoji": cat.emoji, "blurb": cat.blurb, "basis": cat.basis,
                 "empty": cat.empty, "plays": plays, "record": track.get(cat.id), "paper": paper.get(cat.id)}
        if cat.id == "unusual":
            entry["followups"] = followups(ctx.flagged, tables)
        out_categories.append(entry)
    added = record(research, session, recorded, now) if session_closed(session, now) else []
    twins = record_twins(research, session, {c: recorded[c] for c in added}, tables, now) if added else 0
    cal_itm = ctx.calibration["itm"]
    sources = Counter(tb.u.source for tb in tables)
    return {
        "version": VERSION, "method": method(), "input_hash": key, "asof": session.isoformat(),
        "expected_asof": expected.isoformat(), "generated_at": now.isoformat(),
        "source": sources.most_common(1)[0][0] if sources else None,
        "coverage": {"underlyings": len(tables), "contracts_scored": int(sum(len(tb) for tb in tables)),
                     "with_analysis": sum(1 for tb in tables if tb.u.detail)},
        "calibration": {"applied": bool(cal_itm.buckets), "resolved": cal_itm.resolved, "needed": od.MIN_CALIBRATION,
                        "buckets": [{"predicted": round(p, 3), "resolved": n, "observed": round(o, 3)}
                                    for p, n, o in cal_itm.buckets]},
        "model": {"ic": ic, "ic_months": round(months, 1), "ic_source": source, "iv_weight": od.IV_WEIGHT,
                  "rate": round(ctx.rate, 4)},
        "recorded": added, "twins": twins, "graded": graded, "session_closed": session_closed(session, now),
        "categories": out_categories,
        "message": f"Plays from the {session.isoformat()} options session"
                   + ("" if session == expected else f"; waiting for {expected.isoformat()} chains"),
    }


__all__ = ["BY_CATEGORY", "CATEGORIES", "Candidate", "Category", "Context", "Table", "Underlying", "build",
           "calibration", "committee_ic", "finish", "flagged_before", "followups", "grade", "implied_vols", "load",
           "method", "record", "record_twins", "records",
           "session_closed", "settle_value", "signature", "table", "twin"]
