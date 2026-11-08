"""Implied-volatility history from Massive's real daily option closes (Options Basic: the last 2 years).

IV rank compares today's implied volatility with the year's range. KESTREL's own chains give one IV30 a day only since
collection began, so a rank from them alone rests on a short history. This builds the year from real trades:

- **Download** (``task``, a share of the Massive queue): for the ``NAMES`` most traded optioned stocks and every
  standard monthly expiry already past inside Massive's window (newest first), the call and the put at the strike
  nearest the price nine weeks before expiry (one listing request), then each one's daily bars (one request each):
  3 requests a name-month.
- **Series** (``rebuild``): on each day, each such contract within 7-63 days of expiry and within 10% of the money gives
  an implied volatility from its closing trade (Black-Scholes with the day's 3-month Treasury yield and the stock's
  trailing dividend yield, against that day's close); the call's and put's are averaged and the expiries either side
  of 30 days are interpolated in total variance to a constant 30 days. One row per stock and day, ``source``
  ``massive_close`` (``stk_iv_daily``).
- **Rank** (``year_of_iv``): the collected chain's IV30 on the days it exists, Massive's on the others, labelled with how
  many days came from each (``basis_text``).

Closing trades are last trades, not end-of-day quotes: a quiet contract's last trade can be hours older than the stock's
close, so a single day's value is noisier than a quote's. The rank reads the year's range, which a few noisy days move
little; the label says where the days came from.
"""

from __future__ import annotations

import json
import math
import sqlite3
from collections import defaultdict
from collections.abc import Iterable
from datetime import date, datetime, timedelta
from pathlib import Path
from statistics import mean
from typing import Any

from desk.core.clock import to_iso
from desk.stocks import calendar as cal
from desk.stocks import options as opt
from desk.stocks.repo import StocksMarket, date_of, day_of

NAMES = 60                   # the most traded optioned stocks (SPY, QQQ and IWM first)
CORE = ("SPY", "QQQ", "IWM")
ARCHIVE_DAYS = 725           # Massive Options Basic: the last 2 years
LEAD_DAYS = 63               # each monthly expiry is listed nine weeks before it
DTE = (7, 63)                # days to expiry a contract counts
NEAR = 0.10                  # within 10% of the money (|ln K/S|)
TARGET = 30                  # constant maturity, days
SOURCE = "massive_close"
PURPOSE = "iv"
RANK_DAYS = 400              # the year IV rank reads (as the collected history always has)


# ── what to download ─────────────────────────────────────────────────────────────────────────────
def expiries(today: date) -> list[date]:
    """Every standard monthly expiry already past whose listing date is inside Massive's window, newest first."""
    from desk.stocks.option_model import third_friday

    out: list[date] = []
    first = today - timedelta(days=ARCHIVE_DAYS - LEAD_DAYS)
    year, month = today.year, today.month
    while True:
        e = third_friday(year, month)
        while not cal.is_trading_day(e):
            e -= timedelta(days=1)
        if e < first:
            return out
        if e < today:
            out.append(e)
        year, month = (year - 1, 12) if month == 1 else (year, month - 1)


def names(market: StocksMarket, today: date, n: int = NAMES) -> list[str]:
    """The optioned stocks with the most dollar volume over the last six weeks, the core ETFs first."""
    optioned = market.optioned_symbols()
    if not optioned:
        return []
    volume = {r[0]: r[1] or 0.0 for r in market.conn.execute(
        "SELECT symbol, SUM(close*volume) FROM stk_bars WHERE day>=? GROUP BY symbol",
        (day_of(today - timedelta(days=42)),)) if r[0] in optioned}
    ranked = sorted(volume, key=lambda s: (-volume[s], s))
    return list(dict.fromkeys([*(s for s in CORE if s in optioned), *ranked]))[:n]


def _close_on_or_before(market: StocksMarket, symbol: str, day: date) -> float | None:
    row = market.conn.execute("SELECT close FROM stk_bars WHERE symbol=? AND day<=? AND day>=? ORDER BY day DESC LIMIT 1",
                              (symbol, day_of(day), day_of(day - timedelta(days=7)))).fetchone()
    return float(row[0]) if row else None


def choose(listed: Iterable[dict[str, Any]], spot: float, expiry: date) -> list[dict[str, Any]]:
    """The call and put at the listed strike nearest ``spot`` that has both."""
    pairs: dict[float, dict[str, str]] = defaultdict(dict)
    for c in listed:
        if c["expiration"] == expiry:
            pairs[float(c["strike"])][c["kind"]] = c["ticker"]
    both = [k for k, v in pairs.items() if {"call", "put"} <= v.keys()]
    if not both:
        return []
    k = min(both, key=lambda x: (abs(x - spot), x))
    return [{"occ": pairs[k][kind], "kind": kind, "strike": k} for kind in ("call", "put")]


def lists(conn: sqlite3.Connection, purpose: str = PURPOSE, symbol: str | None = None) -> dict[tuple[str, date], dict[str, Any]]:
    sql, args = "SELECT * FROM stk_option_lists WHERE purpose=?", [purpose]
    if symbol is not None:
        sql, args = sql + " AND symbol=?", [*args, symbol]
    return {(r["symbol"], date_of(r["expiry"])): {"as_of": date_of(r["as_of"]), "contracts": json.loads(r["contracts"]),
                                                  "listed": r["listed"], "note": r["note"]}
            for r in conn.execute(sql, args)}


def save_list(conn: sqlite3.Connection, purpose: str, symbol: str, expiry: date | None, as_of: date, listed: int,
              contracts: Any, note: str | None, now: datetime) -> None:
    conn.execute("INSERT OR REPLACE INTO stk_option_lists VALUES (?,?,?,?,?,?,?,?)",
                 (purpose, symbol, day_of(expiry) if expiry else 0, day_of(as_of), listed, json.dumps(contracts), note,
                  to_iso(now)))


async def task(massive: Any, market: StocksMarket, home: Path, now: datetime, budget: int) -> dict[str, Any]:
    """Up to ``budget`` requests: the next name-months' listings and bars (newest months first), then the touched
    stocks' series rebuilt."""
    from desk.stocks.massive_options import ask

    today = cal.new_york(now).date()
    wanted, have, fetched = names(market, today), lists(market.conn), market.option_bar_fetches()
    used, problem, touched = 0, None, set()
    for expiry in expiries(today):
        for symbol in wanted:
            if used >= budget or problem:
                break
            item = have.get((symbol, expiry))
            if item is None:
                as_of = expiry - timedelta(days=LEAD_DAYS)
                as_of = as_of if cal.is_trading_day(as_of) else cal.previous_trading_day(as_of)
                spot = _close_on_or_before(market, symbol, as_of)
                chosen: list[dict[str, Any]] = []
                listed: list[dict[str, Any]] = []
                if spot is not None:
                    listed, problem = await ask(massive.option_contracts(symbol, as_of, expiry, expiry,
                                                                         strikes=(spot * 0.9, spot * 1.1)))
                    used += 1
                    if problem:
                        break
                    chosen = choose(listed, spot, expiry)
                note = None if chosen else f"no stored {symbol} price on {as_of}" if spot is None else "no listed pair"
                save_list(market.conn, PURPOSE, symbol, expiry, as_of, len(listed), chosen, note, now)
                item = have[(symbol, expiry)] = {"as_of": as_of, "contracts": chosen, "listed": len(listed)}
            for c in item["contracts"]:
                if used >= budget or c["occ"] in fetched:
                    continue
                bars, problem = await ask(massive.option_bars(c["occ"], item["as_of"], expiry))
                used += 1
                if problem:
                    break
                market.save_option_bars(c["occ"], item["as_of"], expiry, bars, now)
                fetched[c["occ"]] = expiry
                touched.add(symbol)
        if used >= budget or problem:
            break
    for symbol in sorted(touched):
        rebuild(market, symbol, now)
    from desk.stocks import option_research  # the preregistered strategies read these same contracts

    if option_research.due(home, now):
        try:
            option_research.save(home, market, now)
        except (ArithmeticError, KeyError, ValueError, OSError) as exc:     # never stops the download
            problem = problem or f"Option research could not be computed: {type(exc).__name__}"
    detail = {"requests": used, **coverage(market, today)}
    market.job("iv_history", now, error=problem, detail=detail)
    return {"ok": problem is None, **detail, **({"error": problem} if problem else {})}


def coverage(market: StocksMarket, today: date) -> dict[str, Any]:
    wanted, months = names(market, today), expiries(today)
    have = lists(market.conn)
    fetched = market.option_bar_fetches()
    done = sum(1 for s in wanted for e in months
               if (item := have.get((s, e))) is not None and all(c["occ"] in fetched for c in item["contracts"]))
    left = (len(wanted) * len(months) - done) * 3
    stocks = market.conn.execute("SELECT COUNT(DISTINCT symbol), COUNT(*) FROM stk_iv_daily WHERE source=?",
                                 (SOURCE,)).fetchone()
    return {"names": len(wanted), "months": len(months), "name_months": len(wanted) * len(months),
            "name_months_done": done, "requests_left": left, "hours_left_at_full_speed": round(left / 5 / 60, 1),
            "stocks_with_series": stocks[0], "series_days": stocks[1]}


# ── the series ───────────────────────────────────────────────────────────────────────────────────
def constant_maturity(points: list[tuple[int, float]], days: int = TARGET) -> float | None:
    """At-the-money IV at ``days``: interpolated in total variance between the expiries either side, else the nearest
    expiry within 12 days of it."""
    below = [p for p in points if p[0] <= days]
    above = [p for p in points if p[0] >= days]
    if below and above:
        near, far = max(below), min(above)
        return opt.interpolate_iv([{"days": near[0], "atm_iv": near[1]}, {"days": far[0], "atm_iv": far[1]}], days)
    nearest = min(points, key=lambda p: abs(p[0] - days))
    return nearest[1] if abs(nearest[0] - days) <= 12 else None


def rebuild(market: StocksMarket, symbol: str, now: datetime) -> int:
    """Recompute one stock's series from every stored listing and bar (a split inside a contract's life drops it)."""
    from desk.stocks.backtest import rate_series
    from desk.stocks.paper.fills import Dividends

    items = [(e, item) for (_, e), item in lists(market.conn, symbol=symbol).items() if item["contracts"]]
    if not items:
        return 0
    first = min(item["as_of"] for _, item in items)
    closes = {b.day: b.close for b in market.bars(symbol, first)}            # each day's own shares, like its strikes
    splits = [sp for sp in market.splits() if sp.symbol == symbol]
    rate_on = rate_series(market)
    dividends = Dividends([d for d in market.dividends() if d.symbol == symbol], splits)
    points: dict[date, list[tuple[int, float, int]]] = defaultdict(list)
    for expiry, item in items:
        if any(item["as_of"] < sp.execution_date <= expiry for sp in splits):
            continue
        bars = {c["occ"]: {date_of(r[0]): r[1] for r in market.conn.execute(
            "SELECT day, close FROM stk_option_bars WHERE occ=? AND volume>0", (c["occ"],))} for c in item["contracts"]}
        for day in sorted({d for b in bars.values() for d in b}):
            s, dte = closes.get(day), (expiry - day).days
            if not s or not DTE[0] <= dte <= DTE[1]:
                continue
            t, r, q = opt.year_fraction(day, expiry), rate_on(day), dividends.yield_on(symbol, day, s)
            ivs = [iv for c in item["contracts"] if abs(math.log(c["strike"] / s)) <= NEAR
                   and (close := bars[c["occ"]].get(day)) is not None
                   and (iv := opt.implied_vol(c["kind"], close, s, c["strike"], t, r, q)) is not None]
            if ivs:
                points[day].append((dte, mean(ivs), len(ivs)))
    rows = [(symbol, day_of(day), iv30, SOURCE, sum(n for *_, n in pts), to_iso(now)) for day, pts in sorted(points.items())
            if (iv30 := constant_maturity([(d, v) for d, v, _ in pts])) is not None and math.isfinite(iv30)]
    market.conn.execute("DELETE FROM stk_iv_daily WHERE symbol=? AND source=?", (symbol, SOURCE))
    market.conn.executemany("INSERT INTO stk_iv_daily VALUES (?,?,?,?,?,?)", rows)
    return len(rows)


def series(conn: sqlite3.Connection, symbols: Iterable[str], first: date, last: date, *,
           observed_by: datetime | None = None) -> dict[str, dict[int, float]]:
    """Massive's IV30 per stock and day ordinal, ``first``..``last`` (only rows computed by ``observed_by``)."""
    wanted = set(symbols)
    out: dict[str, dict[int, float]] = defaultdict(dict)
    try:
        rows = conn.execute("SELECT symbol, day, iv30 FROM stk_iv_daily WHERE day>=? AND day<=? AND computed_at<=?",
                            (day_of(first), day_of(last), to_iso(observed_by) if observed_by else "9999"))
        for r in rows:
            if r[0] in wanted:
                out[r[0]][int(r[1])] = float(r[2])
    except sqlite3.OperationalError:          # a database from before the IV history
        return {}
    return dict(out)


def year_of_iv(conn: sqlite3.Connection, collected: dict[str, dict[int, float]], asof: date, *,
               observed_by: datetime | None = None, days: int = RANK_DAYS) -> dict[str, tuple[list[float], dict[str, Any]]]:
    """Each stock's IV30s over the ``days`` up to ``asof`` for its rank: its collected chain's on the days it has one,
    Massive's real closes on the others, oldest first, with how many came from each."""
    first = asof - timedelta(days=days)
    real = series(conn, collected, first, asof, observed_by=observed_by)
    out = {}
    for symbol, own in collected.items():
        own = {d: v for d, v in own.items() if day_of(first) <= d <= day_of(asof) and v}
        massive = {d: v for d, v in real.get(symbol, {}).items() if d not in own}
        merged = {**massive, **own}
        out[symbol] = ([merged[d] for d in sorted(merged)],
                       {"days": len(merged), "collected": len(own), "massive": len(massive),
                        "first": date_of(min(merged)).isoformat() if merged else None})
    return out


def describe(market: StocksMarket, today: date) -> str:
    """One line on the IV history, for ``kestrel options-history``."""
    c = coverage(market, today)
    if not c["names"]:
        return "IV history: no optioned stocks yet (a chain must be collected first)."
    text = (f"IV history: {c['name_months_done']} of {c['name_months']} name-months downloaded ({c['names']} stocks × "
            f"{c['months']} monthly expiries); {c['stocks_with_series']} stocks have a series ({c['series_days']} days)")
    return text + (f"; about {c['hours_left_at_full_speed']} hours of downloading left at Massive's full speed."
                   if c["requests_left"] else "; all downloaded.")


def basis_text(basis: dict[str, Any] | None) -> str:
    """Where a rank's days came from, in a few words."""
    if not basis or not basis.get("days"):
        return "no IV history yet"
    parts = [f"{basis['massive']} from Massive's closing trades" if basis.get("massive") else "",
             f"{basis['collected']} from collected chains" if basis.get("collected") else ""]
    return f"{basis['days']} days since {basis['first']}: " + ", ".join(p for p in parts if p)


__all__ = ["NAMES", "SOURCE", "basis_text", "choose", "constant_maturity", "coverage", "describe", "expiries", "lists",
           "names", "rebuild", "save_list", "series", "task", "year_of_iv"]
