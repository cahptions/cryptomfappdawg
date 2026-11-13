"""The Learner's features: every one documented with its source and when it becomes known (docs/STOCKS_LEARNER.md →
Features). Point in time only.

**The rule.** A candidate is decided on session S from S's own option quotes (the chain stored for S: the contract's
bid, ask, volume, open interest and the stock price quoted with them). Everything else is read as it stood at the
**previous session's close** (strictly before S): the stock's daily closes, the desks' scores, rates, short interest
(and only settlements at least ``SHORT_INTEREST_LAG`` days old, FINRA's publication delay), insider filings, announced
earnings dates first seen before S. ``Reader`` is the only way this module reads a database, and every query it runs
carries ``day < S`` (or the chain's own day S); tests/unit/test_stocks_learner.py writes wild values after S into every
source and proves the features do not change.

Desk scores come from the compact daily history (``stk_score_history``): replayed decisions on days the research did not
score (and every ETF) have none, and the model sees them as missing, never as zero. Sectors are a company attribute
(the SEC's SIC code), not a daily observation.
"""

from __future__ import annotations

import math
import sqlite3
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import TYPE_CHECKING, Any

from desk.core.hashing import canonical_json, sha256_hex
from desk.stocks import options as opt
from desk.stocks.repo import day_of

if TYPE_CHECKING:
    from desk.stocks.plays import Table, Underlying

NAME = "stocks_learner_features"
VERSION = 1
SHORT_INTEREST_LAG = 14             # days after settlement before short interest counts as published
INSIDER_DAYS = 90
DESKS = ("valuation", "quality", "growth", "momentum", "earnings", "insiders", "shorts", "crowd", "risk", "macro",
         "options", "news")
PREVIOUS = "the previous session's close (data dated before the decision session)"
QUOTE = "the decision session's own option quotes (the chain stored for that session)"


@dataclass(frozen=True)
class Feature:
    name: str
    group: str          # stock | market | contract
    source: str
    known: str          # when it becomes known
    about: str


def _f(name: str, group: str, source: str, known: str, about: str) -> Feature:
    return Feature(name, group, source, known, about)


STOCK: tuple[Feature, ...] = (
    _f("committee", "stock", "stk_score_history (the committee)", PREVIOUS, "The committee's score, 0-100"),
    _f("committee_band", "stock", "stk_score_history (the committee)", PREVIOUS,
       "The committee's label as a band: −2 bearish … +2 bullish"),
    *(_f(f"desk_{d}", "stock", f"stk_score_history ({d} desk)", PREVIOUS, f"The {d} desk's score, 0-100") for d in DESKS),
    _f("ret_5d", "stock", "stk_bars (split-adjusted closes)", PREVIOUS, "Return over 5 sessions"),
    _f("ret_21d", "stock", "stk_bars", PREVIOUS, "Return over 21 sessions"),
    _f("ret_63d", "stock", "stk_bars", PREVIOUS, "Return over 63 sessions"),
    _f("ret_126d", "stock", "stk_bars", PREVIOUS, "Return over 126 sessions"),
    _f("above_sma50", "stock", "stk_bars", PREVIOUS, "Close against its 50-session average"),
    _f("above_sma200", "stock", "stk_bars", PREVIOUS, "Close against its 200-session average (trend)"),
    _f("dist_high_52w", "stock", "stk_bars", PREVIOUS, "Close against its 52-week high"),
    _f("rv20", "stock", "stk_bars", PREVIOUS, "Realized volatility, 20 sessions, annualized"),
    _f("rv60", "stock", "stk_bars", PREVIOUS, "Realized volatility, 60 sessions, annualized"),
    _f("dollar_volume", "stock", "stk_bars", PREVIOUS, "Median daily dollar volume, 20 sessions (log10)"),
    _f("iv30", "stock", "the decision chain (options.daily_summary)", QUOTE, "30-day at-the-money implied volatility"),
    _f("iv_rank", "stock", "stk_option_daily + stk_iv_daily before S, and today's IV30", QUOTE,
       "Where today's IV30 sits in its past year, 0-100"),
    _f("iv_rank_days", "stock", "stk_option_daily + stk_iv_daily", PREVIOUS, "Days of IV history behind the rank"),
    _f("iv_rank_massive_share", "stock", "stk_iv_daily", PREVIOUS,
       "Share of the rank's year from Massive's closing trades (the rest collected chains)"),
    _f("iv_rv", "stock", "decision chain and stk_bars", QUOTE, "IV30 divided by 20-session realized volatility"),
    _f("skew_25d", "stock", "the decision chain", QUOTE, "25-delta put minus call implied volatility near 30 days (points)"),
    _f("term_slope", "stock", "the decision chain", QUOTE, "90-day minus 30-day at-the-money IV (points)"),
    _f("earnings_days", "stock", "stk_earnings_dates (announced) else SEC 8-K item 2.02 (+91 days, estimated)",
       PREVIOUS, "Calendar days to the next results"),
    _f("earnings_announced", "stock", "stk_earnings_dates", PREVIOUS, "1 when that date was announced, 0 estimated"),
    _f("sector", "stock", "stk_companies SIC (Fama-French 12)", "a company attribute", "Sector code"),
    _f("short_change", "stock", "stk_short_interest", f"settlements at least {SHORT_INTEREST_LAG} days before S",
       "Change in shares short since the settlement before"),
    _f("days_to_cover", "stock", "stk_short_interest", f"settlements at least {SHORT_INTEREST_LAG} days before S",
       "Days to cover"),
    _f("insider_net", "stock", "stk_insider_trades (by filing day)", PREVIOUS,
       f"Open-market insider purchases minus sales, last {INSIDER_DAYS} days, count"),
    _f("price", "stock", "the decision chain", QUOTE, "Stock price quoted with the chain (log10)"),
    _f("etf", "stock", "stk_listings", "a listing attribute", "1 for an ETF"),
)
MARKET: tuple[Feature, ...] = (
    _f("spy_vs_200d", "market", "stk_bars SPY", PREVIOUS, "SPY against its 200-session average"),
    _f("spy_ret_21d", "market", "stk_bars SPY", PREVIOUS, "SPY's 21-session return"),
    _f("spy_rv20", "market", "stk_bars SPY", PREVIOUS, "SPY's 20-session realized volatility"),
    _f("vix", "market", "stk_rates VIXCLS (FRED / Schwab $VIX)", PREVIOUS, "VIX level"),
    _f("vix_change_5d", "market", "stk_rates VIXCLS", PREVIOUS, "VIX change over 5 observations"),
    _f("spy_term_slope", "market", "the decision chain of SPY", QUOTE,
       "SPY's 90-day minus 30-day IV (points): the VIX term structure's stand-in"),
    _f("hy_spread", "market", "stk_rates BAMLH0A0HYM2 (FRED)", PREVIOUS, "High-yield credit spread"),
    _f("hy_change_21d", "market", "stk_rates BAMLH0A0HYM2", PREVIOUS, "Its change over 21 observations"),
    _f("ust10y", "market", "stk_rates UST10Y", PREVIOUS, "10-year Treasury yield"),
    _f("ust3m", "market", "stk_rates UST3M", PREVIOUS, "3-month Treasury yield"),
)
CONTRACT: tuple[Feature, ...] = (
    _f("structure", "contract", "actions", "the action", "Structure code (actions.STRUCTURES order)"),
    _f("buy", "contract", "actions", "the action", "1 bought (debit), 0 sold (credit)"),
    _f("target_delta", "contract", "actions", "the action", "The |delta| dial"),
    _f("target_width", "contract", "actions", "the action", "The width dial (share of the price)"),
    _f("bucket", "contract", "actions", "the action", "Days-to-expiry bucket code"),
    _f("dte", "contract", "decision chain", QUOTE, "Calendar days to expiry"),
    _f("moneyness", "contract", "decision chain", QUOTE, "Anchor strike / price − 1"),
    _f("anchor_delta", "contract", "decision chain (Black-Scholes at the mid's IV)", QUOTE, "Anchor leg's delta"),
    _f("net_delta", "contract", "plays.finish", QUOTE, "Position delta per share"),
    _f("net_theta", "contract", "plays.finish", QUOTE, "Position theta per share per day"),
    _f("net_vega", "contract", "plays.finish", QUOTE, "Position vega per share per vol point"),
    _f("anchor_iv", "contract", "decision chain", QUOTE, "Anchor leg's implied volatility"),
    _f("spread_pct", "contract", "decision chain", QUOTE, "Widest leg's (ask − bid) / mid"),
    _f("min_oi", "contract", "decision chain", QUOTE, "Smallest leg open interest (log10 1+x)"),
    _f("min_volume", "contract", "decision chain", QUOTE, "Smallest leg volume (log10 1+x)"),
    _f("entry_pct", "contract", "decision chain", QUOTE, "Entry per share / price"),
    _f("risk", "contract", "plays.finish", QUOTE, "Money at risk per contract, fees included (log10)"),
    _f("reward_risk", "contract", "plays.finish", QUOTE, "Maximum gain / money at risk (missing when unlimited)"),
    _f("breakeven_low", "contract", "plays.finish", QUOTE, "Lower break-even against the price (missing if none)"),
    _f("breakeven_high", "contract", "plays.finish", QUOTE, "Upper break-even against the price (missing if none)"),
    _f("itm_market", "contract", "chain_odds (market view)", QUOTE, "The market's chance of the event"),
    _f("itm_kestrel", "contract", "chain_odds (KESTREL raw, uncalibrated)", QUOTE, "KESTREL's raw chance"),
    _f("profit_kestrel", "contract", "chain_odds / play_targets", QUOTE, "KESTREL's chance of a profit at expiry"),
    _f("ev_risk", "contract", "plays.finish", QUOTE, "KESTREL's expected value / money at risk"),
    _f("expected_move", "contract", "plays.finish", QUOTE, "KESTREL's one-sigma move to expiry"),
    _f("chain_basis", "contract", "chain_odds", QUOTE, "1 when the chances came from the chain's own smile"),
)
ALL: tuple[Feature, ...] = (*STOCK, *MARKET, *CONTRACT)
STOCK_NAMES = tuple(f.name for f in STOCK)
MARKET_NAMES = tuple(f.name for f in MARKET)
CONTRACT_NAMES = tuple(f.name for f in CONTRACT)
SECTORS = ("NoDur", "Durbl", "Manuf", "Enrgy", "Chems", "BusEq", "Telcm", "Utils", "Shops", "Hlth", "Money", "Other")


def content() -> dict[str, Any]:
    return {"version": VERSION, "features": [[f.name, f.group, f.source, f.known] for f in ALL],
            "short_interest_lag": SHORT_INTEREST_LAG, "insider_days": INSIDER_DAYS}


def version_hash() -> str:
    return sha256_hex(canonical_json(content()))[:16]


def clean(x: Any, digits: int = 5) -> float | None:
    """A finite float rounded to ``digits`` significant digits (compact storage), else None."""
    if x is None or isinstance(x, bool):
        return None if x is None else float(x)
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(v):
        return None
    return v if v == 0 else float(f"{v:.{digits}g}")


def _log10(x: float | None) -> float | None:
    return None if x is None or x <= 0 else math.log10(x)


# ── point-in-time reader ─────────────────────────────────────────────────────────────────────────
class Reader:
    """Reads the databases as they stood before session ``session`` (see the module docstring)."""

    def __init__(self, market: sqlite3.Connection, research: sqlite3.Connection | None, session: date) -> None:
        self.market, self.research, self.session = market, research, session
        self.cut = day_of(session)          # every query below: day < cut

    def closes(self, symbol: str, n: int = 260) -> list[tuple[int, float, float]]:
        """(day, split-adjusted close, volume) for up to ``n`` sessions before S, oldest first."""
        from desk.stocks.hub import adjusted_bars

        rows = [dict(r) for r in self.market.execute(
            "SELECT day, close, volume FROM stk_bars WHERE symbol=? AND day<? ORDER BY day DESC LIMIT ?",
            (symbol, self.cut, n))]
        rows.reverse()
        rows = adjusted_bars(self.market, symbol, rows)
        return [(int(r["day"]), float(r["close"]), float(r["volume"] or 0)) for r in rows if r["close"]]

    def rate(self, series: str, n: int = 25) -> list[float]:
        """The newest ``n`` values before S (FRED's or Schwab's), oldest first."""
        rows = self.market.execute("SELECT day, value FROM stk_rates WHERE series IN (?, ?) AND day<? "
                                   "ORDER BY day DESC LIMIT ?", (series, f"{series}@schwab", self.cut, 2 * n)).fetchall()
        by: dict[int, float] = {}
        for d, v in rows:
            by.setdefault(int(d), float(v))
        return [by[d] for d in sorted(by)][-n:]

    def scores(self, symbol: str) -> dict[str, Any]:
        if self.research is None:
            return {}
        row = self.research.execute("SELECT scores_json FROM stk_score_history WHERE symbol=? AND asof<? "
                                    "ORDER BY asof DESC LIMIT 1", (symbol, self.cut)).fetchone()
        import json

        return dict(json.loads(row[0])) if row else {}

    def iv_year(self, symbol: str) -> tuple[list[float], dict[str, int]]:
        """IV30s over the year before S: collected chains' summaries first, Massive's closing-trade series between."""
        import json

        first = day_of(self.session - timedelta(days=400))
        own: dict[int, float] = {}
        for d, s in self.market.execute("SELECT day, summary_json FROM stk_option_daily WHERE underlying=? AND day>=? "
                                        "AND day<?", (symbol, first, self.cut)):
            iv = json.loads(s).get("iv30")
            if iv:
                own[int(d)] = float(iv)
        massive: dict[int, float] = {}
        try:
            for d, iv in self.market.execute("SELECT day, iv30 FROM stk_iv_daily WHERE symbol=? AND day>=? AND day<?",
                                             (symbol, first, self.cut)):
                if int(d) not in own and iv:
                    massive[int(d)] = float(iv)
        except sqlite3.OperationalError:
            pass
        merged = {**massive, **own}
        return [merged[d] for d in sorted(merged)], {"days": len(merged), "massive": len(massive)}

    def earnings(self, symbol: str) -> tuple[int | None, int | None]:
        """(calendar days to the next results, 1 announced / 0 estimated)."""
        cutoff = f"{(self.session - timedelta(days=1)).isoformat()}T23:59:59"
        try:
            row = self.market.execute("SELECT MIN(report_date) FROM stk_earnings_dates WHERE symbol=? AND report_date>=? "
                                      "AND first_seen<=?", (symbol, self.cut, cutoff)).fetchone()
        except sqlite3.OperationalError:
            row = None
        if row and row[0] is not None:
            return int(row[0]) - self.cut, 1
        last = self.market.execute(
            "SELECT MAX(f.filed) FROM stk_filings f JOIN stk_listings l ON l.cik=f.cik WHERE l.symbol=? AND "
            "f.form IN ('8-K','8-K/A') AND f.items LIKE '%2.02%' AND f.filed<?", (symbol, self.cut)).fetchone()
        if not last or last[0] is None:
            return None, None
        days = int(last[0]) + 91 - self.cut
        while days < 0:
            days += 91
        return days, 0

    def sector(self, symbol: str) -> int | None:
        from desk.stocks.sectors import sector

        row = self.market.execute("SELECT c.sic FROM stk_companies c JOIN stk_listings l ON l.cik=c.cik WHERE l.symbol=?",
                                  (symbol,)).fetchone()
        return None if row is None else SECTORS.index(sector(row[0]))

    def etf(self, symbol: str) -> int | None:
        row = self.market.execute("SELECT etf FROM stk_listings WHERE symbol=?", (symbol,)).fetchone()
        return None if row is None else int(row[0])

    def short_interest(self, symbol: str) -> tuple[float | None, float | None]:
        """(change in shares short since the previous settlement, days to cover) as published before S."""
        row = self.market.execute("SELECT short_shares, previous, avg_volume, days_to_cover FROM stk_short_interest "
                                  "WHERE symbol=? AND settlement<? ORDER BY settlement DESC LIMIT 1",
                                  (symbol, self.cut - SHORT_INTEREST_LAG)).fetchone()
        if row is None:
            return None, None
        days = row[3] if row[3] is not None else (row[0] / row[2] if row[2] else None)
        return (None if not row[1] else row[0] / row[1] - 1), days

    def insider_net(self, symbol: str) -> int:
        rows = self.market.execute("SELECT code, COUNT(*) FROM stk_insider_trades WHERE symbol=? AND filed<? AND filed>=? "
                                   "AND code IN ('P','S') GROUP BY code", (symbol, self.cut, self.cut - INSIDER_DAYS))
        counts = {c: n for c, n in rows}
        return int(counts.get("P", 0)) - int(counts.get("S", 0))


# ── the three groups ─────────────────────────────────────────────────────────────────────────────
def _realized(closes: Sequence[float], n: int) -> float | None:
    if len(closes) < n + 1:
        return None
    rets = [math.log(closes[i] / closes[i - 1]) for i in range(len(closes) - n, len(closes)) if closes[i - 1] > 0]
    return statistics.pstdev(rets) * math.sqrt(252) if len(rets) >= n - 1 else None


def _ret(closes: Sequence[float], n: int) -> float | None:
    return closes[-1] / closes[-1 - n] - 1 if len(closes) > n and closes[-1 - n] > 0 else None


def _band(score: float | None) -> int | None:
    if score is None:
        return None
    return 2 if score >= 70 else 1 if score >= 58 else 0 if score >= 42 else -1 if score >= 30 else -2


def price_features(rows: Sequence[tuple[int, float, float]]) -> dict[str, float | None]:
    closes = [c for _, c, _ in rows]
    out: dict[str, float | None] = {f"ret_{n}d": _ret(closes, n) for n in (5, 21, 63, 126)}
    out["above_sma50"] = closes[-1] / statistics.fmean(closes[-50:]) - 1 if len(closes) >= 50 else None
    out["above_sma200"] = closes[-1] / statistics.fmean(closes[-200:]) - 1 if len(closes) >= 200 else None
    out["dist_high_52w"] = closes[-1] / max(closes[-252:]) - 1 if len(closes) >= 60 else None
    out["rv20"], out["rv60"] = _realized(closes, 20), _realized(closes, 60)
    dollars = [c * v for _, c, v in rows[-20:] if v]
    out["dollar_volume"] = _log10(statistics.median(dollars)) if len(dollars) >= 10 else None
    return out


def chain_features(u: Underlying, session: date, rate: float) -> dict[str, float | None]:
    """IV30, skew and term structure from the decision chain itself."""
    summary = opt.daily_summary(u.chains, u.price, session, rate)
    near = [e for e in summary["expiries"] if e.get("days") and e.get("skew_25d") is not None]
    skew = min(near, key=lambda e: abs(e["days"] - 30))["skew_25d"] if near else None
    return {"iv30": summary.get("iv30"), "skew_25d": skew, "term_slope": summary.get("term_slope")}


def stock_features(reader: Reader, u: Underlying, rate: float) -> dict[str, float | None]:
    """The stock group for ``u`` on ``reader.session`` (point in time; see the module docstring)."""
    symbol = u.symbol
    scores = reader.scores(symbol)
    out: dict[str, float | None] = {"committee": scores.get("c"), "committee_band": _band(scores.get("c"))}
    out.update({f"desk_{d}": scores.get(d) for d in DESKS})
    rows = reader.closes(symbol)
    out.update(price_features(rows) if rows else {})
    out.update(chain_features(u, reader.session, rate))
    year, basis = reader.iv_year(symbol)
    iv30 = out.get("iv30")
    out["iv_rank"] = opt.iv_rank(year, iv30)[0] if iv30 else None
    out["iv_rank_days"] = float(basis["days"])
    out["iv_rank_massive_share"] = basis["massive"] / basis["days"] if basis["days"] else None
    rv = out.get("rv20")
    out["iv_rv"] = iv30 / rv if iv30 and rv else None
    days, announced = reader.earnings(symbol)
    out["earnings_days"], out["earnings_announced"] = days, announced
    out["sector"] = reader.sector(symbol)
    out["short_change"], out["days_to_cover"] = reader.short_interest(symbol)
    out["insider_net"] = reader.insider_net(symbol)
    out["price"] = _log10(u.price)
    out["etf"] = reader.etf(symbol)
    return {k: clean(out.get(k)) for k in STOCK_NAMES}


def market_features(reader: Reader, spy: Underlying | None, rate: float) -> dict[str, float | None]:
    rows = reader.closes("SPY")
    closes = [c for _, c, _ in rows]
    out: dict[str, float | None] = {
        "spy_vs_200d": closes[-1] / statistics.fmean(closes[-200:]) - 1 if len(closes) >= 200 else None,
        "spy_ret_21d": _ret(closes, 21), "spy_rv20": _realized(closes, 20)}
    vix = reader.rate("VIXCLS", 6)
    out["vix"] = vix[-1] if vix else None
    out["vix_change_5d"] = vix[-1] - vix[0] if len(vix) >= 6 else None
    out["spy_term_slope"] = chain_features(spy, reader.session, rate)["term_slope"] if spy is not None else None
    hy = reader.rate("BAMLH0A0HYM2", 22)
    out["hy_spread"] = hy[-1] if hy else None
    out["hy_change_21d"] = hy[-1] - hy[0] if len(hy) >= 22 else None
    for series, name in (("UST10Y", "ust10y"), ("UST3M", "ust3m")):
        v = reader.rate(series, 1)
        out[name] = v[-1] if v else None
    return {k: clean(out.get(k)) for k in MARKET_NAMES}


def contract_features(tb: Table, legs: Sequence[tuple[int, str]], play: Mapping[str, Any],
                      action: Any) -> dict[str, float | None]:
    """The contract group of one candidate: its legs in ``tb`` and ``plays.finish``'s numbers for them."""
    from desk.stocks.learner.actions import BUCKET_CODE, STRUCTURE_CODE

    price = tb.u.price
    anchor = legs[0][0]
    rows = [i for i, _ in legs]
    breakevens = play.get("breakevens") or ([play["breakeven"]] if play.get("breakeven") is not None else [])
    lows = [b for b in breakevens if b <= price]
    highs = [b for b in breakevens if b > price]
    if play["structure"] in ("long_call", "bull_call_debit", "bear_call_credit"):
        lows, highs = [], [b for b in breakevens]
    elif play["structure"] in ("long_put", "bear_put_debit", "bull_put_credit", "cash_secured_put"):
        lows, highs = [b for b in breakevens], []
    risk = float(play["max_loss"])
    out = {
        "structure": STRUCTURE_CODE.get(play["structure"]), "buy": 1.0 if play["action"] == "BUY" else 0.0,
        "target_delta": None if action is None else action.delta, "target_width": None if action is None else action.width,
        "bucket": None if action is None else BUCKET_CODE.get(action.bucket),
        "dte": float(tb.dte[anchor]), "moneyness": float(tb.k[anchor]) / price - 1,
        "anchor_delta": float(tb.delta[anchor]), "net_delta": play.get("delta"), "net_theta": play.get("theta"),
        "net_vega": play.get("vega"), "anchor_iv": float(tb.iv[anchor]),
        "spread_pct": max(float(tb.spread[i]) for i in rows),
        "min_oi": math.log10(1 + min(float(tb.oi[i]) for i in rows)),
        "min_volume": math.log10(1 + min(float(tb.volume[i]) for i in rows)),
        "entry_pct": float(play["entry"]) / price, "risk": _log10(risk),
        "reward_risk": None if play.get("max_gain") is None or risk <= 0 else float(play["max_gain"]) / risk,
        "breakeven_low": min(lows) / price - 1 if lows else None,
        "breakeven_high": max(highs) / price - 1 if highs else None,
        "itm_market": play.get("itm_market"), "itm_kestrel": play.get("itm_kestrel_raw"),
        "profit_kestrel": ((play.get("probability_targets") or {}).get("returns") or {}).get("0", play.get("profit_kestrel")),
        "ev_risk": float(play["expected_value"]) / risk if risk > 0 else None,
        "expected_move": play.get("expected_move"),
        "chain_basis": 1.0 if (play.get("odds_basis") or {}).get("basis") == "chain" else 0.0}
    return {k: clean(out.get(k)) for k in CONTRACT_NAMES}


__all__ = ["ALL", "CONTRACT", "CONTRACT_NAMES", "DESKS", "MARKET", "MARKET_NAMES", "NAME", "STOCK", "STOCK_NAMES",
           "VERSION", "Feature", "Reader", "chain_features", "clean", "content", "contract_features", "market_features",
           "price_features", "stock_features", "version_hash"]
