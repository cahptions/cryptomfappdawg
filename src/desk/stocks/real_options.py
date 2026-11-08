"""Option strategies whose signal is option data, replayed on real past prices (Massive Options Basic, 2 years).

The modeled replay can't run **Calls when IV is cheap** or **Cheap earnings straddles**: their signal is how options
were priced, which prices rebuilt from the stock's own movement can't show. Inside Massive's window they replay on real
prices instead, beside the modeled replay and never mixed into it (their own accounts, labelled, never graded as
forward evidence):

- **Signals.** Each replayed session the strategies' own rules (``desk.stocks.paper.strategies``) read the replay's
  committee, momentum and results calendar for that day, with real option data in place of the model: IV30 from the IV
  history (``desk.stocks.iv_history``) and, before a report, the expected move of the straddle on the first expiry after
  its results window, from the real closing trades of the call and put downloaded for that report (``task``).
- **Contracts.** Calls when IV is cheap buys the monthly call downloaded for the IV history nearest 0.50 delta
  (0.35-0.65) with 30-60 days left; Cheap earnings straddles buys that report's call and put. **Random calls** (real
  prices), the baseline both are judged against, buys the same kind of call on a random stock that has one that day.
- **Accounting** (as the lottos on real prices): $1,000 a trade (2% of a $50,000 account), at most 5 open and one per
  stock, bought at the next session's real opening trade plus 4% (half the model's typical spread) and $0.65 a contract
  a leg; each later close (less 4% and fees) is checked against the rule's exits (take-profit, stop, days to expiry,
  the second session after the results window); a position still open at expiry settles at intrinsic value on the
  stock's close. A day a leg did not trade is skipped: no price is made up.
"""

from __future__ import annotations

import json
import math
import random
import sqlite3
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from statistics import mean, median
from typing import Any

from desk.stocks import calendar as cal
from desk.stocks import options as opt
from desk.stocks.analysis import Day
from desk.stocks.iv_history import lists, save_list, series
from desk.stocks.paper.fills import OPTION_FEE_PER_CONTRACT as FEE
from desk.stocks.paper.strategies import BY_STRATEGY, candidates
from desk.stocks.repo import StocksMarket, date_of

STRATEGY_IDS = ("cheap_iv_calls", "earnings_straddles")
BASELINE = "random_calls"
ARCHIVE_DAYS = 725
STAKE, SLIP, MAX_OPEN = 1000.0, 0.04, 5
CALL_DTE, DELTA, DELTA_BAND = (30, 60), 0.50, 0.15
NEAR = 0.05                         # a straddle's strike within 5% of the price counts as at the money
PURPOSE = "earnings"
REPORT = "stocks-backtest-report.json"
METHOD = ("Each replayed session inside Massive's 2-year window, the strategy's own rule reads that day's committee, "
          "momentum and results calendar from the replay, with real option data: IV30 solved from real closing trades "
          "(the IV history) and the expected move of the straddle on the first expiry after the results window, from "
          "the real closing trades of its call and put. Calls buy the monthly call nearest 0.50 delta (30-60 days); "
          "straddles buy that report's call and put. $1,000 a trade, at most 5 open, one per stock, bought at the next "
          "session's real open plus 4% and $0.65 a contract a leg; exits on later closes less 4% and fees by the rule's "
          "take-profit, stop and dates; settled at intrinsic value at expiry. Random calls (the baseline) buy the same "
          "kind of call on a random stock that has one that day.")


def _call_contracts(conn: sqlite3.Connection) -> dict[str, list[tuple[date, str, float]]]:
    """Per stock, the IV history's monthly calls: (expiry, OCC code, strike)."""
    out: dict[str, list[tuple[date, str, float]]] = defaultdict(list)
    for (symbol, expiry), item in lists(conn).items():
        for c in item["contracts"]:
            if c["kind"] == "call":
                out[symbol].append((expiry, c["occ"], float(c["strike"])))
    return out


def earnings_lists(conn: sqlite3.Connection) -> dict[tuple[str, date], dict[str, Any]]:
    """Each downloaded report's straddle, keyed by (stock, the day it was listed as of)."""
    return {(r["symbol"], date_of(r["as_of"])): {"expiry": date_of(r["expiry"]) if r["expiry"] else None,
                                                  "contracts": json.loads(r["contracts"]), "note": r["note"]}
            for r in conn.execute("SELECT * FROM stk_option_lists WHERE purpose=?", (PURPOSE,))}


class RealOptions:
    """Collects, session by session inside the stock replay, what the rules would have done on real option data."""

    def __init__(self, conn: sqlite3.Connection, last: date, optioned: set[str]) -> None:
        self.first = last - timedelta(days=ARCHIVE_DAYS)
        self.optioned = optioned
        self.iv = series(conn, optioned, self.first, last)
        self.calls = _call_contracts(conn)
        self.straddles: dict[str, list[tuple[date, dict[str, Any]]]] = defaultdict(list)
        for (symbol, as_of), item in earnings_lists(conn).items():
            if item["expiry"] and len(item["contracts"]) == 2:
                self.straddles[symbol].append((as_of, item))
        codes = {occ for items in self.calls.values() for _, occ, _ in items}
        codes |= {c["occ"] for items in self.straddles.values() for _, item in items for c in item["contracts"]}
        self.bars: dict[str, dict[date, tuple[float | None, float]]] = defaultdict(dict)
        for r in conn.execute("SELECT occ, day, open, close FROM stk_option_bars WHERE volume>0"):
            if r[0] in codes:
                self.bars[r[0]][date_of(r[1])] = (r[2], float(r[3]))
        self.signals: dict[str, list[dict[str, Any]]] = {s: [] for s in (*STRATEGY_IDS, BASELINE)}
        self.wanted: dict[tuple[str, str], dict[str, Any]] = {}
        self.spots: dict[tuple[str, date], float] = {}
        self.days = 0

    # ── what each rule reads on a day ────────────────────────────────────────────────────────────
    def _straddle(self, symbol: str, d: date, spot: float) -> tuple[dict[str, Any], list[dict[str, Any]]] | None:
        for as_of, item in self.straddles.get(symbol, []):
            if not as_of <= d < item["expiry"]:
                continue
            closes = [self.bars[c["occ"]].get(d) for c in item["contracts"]]
            strike = float(item["contracts"][0]["strike"])
            if all(closes) and abs(math.log(strike / spot)) <= NEAR:
                move = sum(c[1] for c in closes if c) / spot
                return ({"expiration": item["expiry"].isoformat(), "days": (item["expiry"] - d).days,
                         "expected_move": move, "source": "massive_close"}, item["contracts"])
        return None

    def _call(self, symbol: str, d: date, spot: float, rate: float) -> dict[str, Any] | None:
        best = None
        for expiry, code, strike in self.calls.get(symbol, []):
            bar = self.bars[code].get(d)
            if bar is None or not CALL_DTE[0] <= (expiry - d).days <= CALL_DTE[1]:
                continue
            t = opt.year_fraction(d, expiry)
            iv = opt.implied_vol("call", bar[1], spot, strike, t, rate)
            if iv is None:
                continue
            gap = abs(opt.greeks("call", spot, strike, t, iv, rate).delta - DELTA)
            if gap <= DELTA_BAND and (best is None or gap < best[0]):
                best = (gap, {"occ": code, "kind": "call", "strike": strike, "expiration": expiry.isoformat()})
        return None if best is None else best[1]

    def observe(self, day: Day, rate: float) -> None:
        """Run both rules on ``day`` with real option data (the day's modeled options are put back after)."""
        d = day.asof
        if d < self.first:
            return
        self.days += 1
        spots = {s: float(px) for s in self.optioned & set(day.ctx.symbols)
                 if (px := (day.ctx.price.get(s) or {}).get("close")) and math.isfinite(float(px)) and px > 0}
        real: dict[str, dict[str, Any]] = {}
        legs: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for s, spot in spots.items():
            summary: dict[str, Any] = {"real": True}
            if (iv := self.iv.get(s, {}).get(d.toordinal())) is not None:
                call = self._call(s, d, spot, rate)
                if call:
                    summary["iv30"] = iv
                    legs[("call", s)] = [call]
            straddle = self._straddle(s, d, spot)
            if straddle:
                summary["expiries"] = [straddle[0]]
                legs[("straddle", s)] = straddle[1]
            if len(summary) > 1:
                real[s] = summary
            self._want(day, s, spot)
        modeled, day.ctx.options = day.ctx.options, real
        try:
            for sid, kind in (("cheap_iv_calls", "call"), ("earnings_straddles", "straddle")):
                for c in candidates(BY_STRATEGY[sid], day):
                    if (kind, c.symbol) in legs:
                        self.signals[sid].append({"day": d, "symbol": c.symbol, "score": c.score, "legs": legs[(kind, c.symbol)],
                                                  "window": c.evidence.get("window"), "reasons": c.reasons})
        finally:
            day.ctx.options = modeled
        pool = sorted(s for kind, s in legs if kind == "call")
        if pool:
            s = random.Random(f"real_random_calls:{d.isoformat()}").choice(pool)  # noqa: S311 - a reproducible draw
            self.signals[BASELINE].append({"day": d, "symbol": s, "score": 0.0, "legs": legs[("call", s)],
                                           "window": None, "reasons": ["Random call (baseline)"]})

    def _want(self, day: Day, symbol: str, spot: float) -> None:
        """A results window the straddle rule would look at (centre 2-10 days ahead, 4+ past reports), to download."""
        p = BY_STRATEGY["earnings_straddles"].p
        ev = day.ctx.events.get(symbol) or {}
        window = ev.get("next_window")
        past = [e for e in ev.get("earnings", []) if e.get("reaction") is not None]
        if not window or len(past) < p["min_reports"]:
            return
        start, end = date.fromisoformat(window[0]), date.fromisoformat(window[1])
        centre = start + (end - start) / 2
        if p["min_days"] <= (centre - day.asof).days <= p["max_days"] and (symbol, window[1]) not in self.wanted:
            self.wanted[(symbol, window[1])] = {"symbol": symbol, "as_of": day.asof.isoformat(), "end": window[1],
                                                "spot": round(spot, 4)}

    # ── the trades ───────────────────────────────────────────────────────────────────────────────
    def _trade(self, signal: dict[str, Any], p: dict[str, Any], closes: dict[date, float]) -> dict[str, Any] | None:
        exit_on = None
        if signal.get("window") and p.get("exit_after_window") is not None:
            exit_on = cal.next_trading_day(date.fromisoformat(signal["window"][1]))
            for _ in range(int(p["exit_after_window"])):
                exit_on = cal.next_trading_day(exit_on)
        return hold_long(self.bars, signal["legs"], signal["day"], p, closes, exit_on)

    def replay(self, market: StocksMarket) -> dict[str, Any]:
        """Each strategy's trades, at most ``MAX_OPEN`` open at once and one per stock, best signal first."""
        closes: dict[str, dict[date, float]] = {}
        out: dict[str, Any] = {}
        trades_by: dict[str, list[dict[str, Any]]] = {}
        for sid in (*STRATEGY_IDS, BASELINE):
            p = BY_STRATEGY[sid].p
            by_day: dict[date, list[dict[str, Any]]] = defaultdict(list)
            for signal in self.signals[sid]:
                by_day[signal["day"]].append(signal)
            held: list[tuple[date, str]] = []
            trades = []
            for d in sorted(by_day):
                held = [(end, s) for end, s in held if end >= cal.next_trading_day(d)]
                for signal in sorted(by_day[d], key=lambda x: (-x["score"], x["symbol"])):
                    if len(held) >= MAX_OPEN or any(s == signal["symbol"] for _, s in held):
                        continue
                    symbol = signal["symbol"]
                    if symbol not in closes:
                        closes[symbol] = {b.day: b.close for b in market.bars(symbol, self.first)}
                    done = self._trade(signal, p, closes[symbol])
                    if done is None:
                        continue
                    held.append((date.fromisoformat(done["exit_day"]), symbol))
                    trades.append({**done, "symbol": symbol, "decided": d.isoformat(),
                                   "legs": [leg["occ"] for leg in signal["legs"]],
                                   "multiple": done["value"] / done["cost"], "reason": signal["reasons"][0]})
            trades_by[sid] = trades
            out[sid] = {"title": BY_STRATEGY[sid].title + " · real prices", "signals": len(self.signals[sid]),
                        **summary(trades), "recent": trades[-12:]}
        from desk.stocks.lotto_history import versus

        for sid in STRATEGY_IDS:
            out[sid]["versus_random_calls"] = versus(trades_by[sid], trades_by[BASELINE])
        return {"version": 1, "first": self.first.isoformat(), "sessions": self.days, "strategies": out,
                "coverage": {"stocks_with_iv": sum(1 for v in self.iv.values() if v),
                             "stocks_with_calls": len(self.calls),
                             "reports_downloaded": sum(map(len, self.straddles.values())),
                             "reports_wanted": len(self.wanted)},
                "wanted_reports": sorted(self.wanted.values(), key=lambda w: (w["as_of"], w["symbol"])),
                "method": METHOD}


def hold_long(bars: dict[str, dict[date, tuple[float | None, float]]], legs: list[dict[str, Any]], decided: date,
              p: dict[str, Any], closes: dict[date, float], exit_on: date | None = None) -> dict[str, Any] | None:
    """One $1,000 long position on real prices: bought at the next session's real open plus ``SLIP`` and fees (None
    when a leg did not trade then), checked on each later close where every leg traded (less ``SLIP`` and fees)
    against the take-profit, stop, ``exit_on`` and days to expiry, else settled at intrinsic value at expiry."""
    entry = cal.next_trading_day(decided)
    expiry = date.fromisoformat(legs[0]["expiration"])
    opens = [bars[leg["occ"]].get(entry) for leg in legs]
    if entry >= expiry or not all(b and b[0] for b in opens):
        return None
    price = sum((b[0] or 0.0) for b in opens if b) * (1 + SLIP)
    units = int(STAKE // (price * 100 + FEE * len(legs)))
    if units < 1:
        return None
    cost = units * (price * 100 + FEE * len(legs))
    for d in sorted({d for leg in legs for d in bars[leg["occ"]] if entry <= d < expiry}):
        marks = [bars[leg["occ"]].get(d) for leg in legs]
        if not all(marks):
            continue                                    # a leg did not trade that day: nothing is made up
        value = units * (sum(m[1] for m in marks if m) * (1 - SLIP) * 100 - FEE * len(legs))
        change = value / cost - 1
        reason = ("take-profit" if change >= p["take_profit"] else "stop" if change <= p["stop"]
                  else "results are out" if exit_on and d >= exit_on
                  else "days to expiry" if (expiry - d).days <= p["exit_dte"] else None)
        if reason:
            return {"entry_day": entry.isoformat(), "exit_day": d.isoformat(), "units": units, "cost": cost,
                    "value": max(value, 0.0), "exit": f"{reason} on {d.isoformat()}"}
    settle = closes.get(expiry)
    if settle is None:
        return None
    value = units * sum(max(0.0, (max(settle - leg["strike"], 0.0) if leg["kind"] == "call"
                                  else max(leg["strike"] - settle, 0.0)) * 100 - FEE) for leg in legs)
    return {"entry_day": entry.isoformat(), "exit_day": expiry.isoformat(), "units": units, "cost": cost,
            "value": value, "exit": "settled at expiry"}


def summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"trades": 0}
    cost, value = sum(r["cost"] for r in rows), sum(r["value"] for r in rows)
    returns = [r["multiple"] - 1 for r in rows]
    best, worst = max(rows, key=lambda r: r["multiple"]), min(rows, key=lambda r: r["multiple"])
    return {"trades": len(rows), "cost": round(cost, 2), "pnl": round(value - cost, 2), "return": (value - cost) / cost,
            "win_rate": sum(r > 0 for r in returns) / len(rows), "median_return": median(returns),
            "mean_return": mean(returns), "first": rows[0]["decided"], "last": rows[-1]["decided"],
            "best": {k: best[k] for k in ("symbol", "decided", "multiple", "exit")},
            "worst": {k: worst[k] for k in ("symbol", "decided", "multiple", "exit")}}


# ── the download: each wanted report's straddle ─────────────────────────────────────────────────
def wanted(home: Path) -> list[dict[str, Any]]:
    try:
        value = json.loads((home / REPORT).read_text())
    except (OSError, ValueError):
        return []
    rows = ((value.get("real_price") or {}).get("wanted_reports") if isinstance(value, dict) else None) or []
    return [r for r in rows if isinstance(r, dict) and {"symbol", "as_of", "end", "spot"} <= r.keys()]


def choose(listed: list[dict[str, Any]], spot: float, end: date) -> list[dict[str, Any]]:
    """The first listed expiry after the results window, and its call and put at the strike nearest ``spot``."""
    after = sorted({c["expiration"] for c in listed if c["expiration"] > end})
    for expiry in after[:1]:
        pairs: dict[float, dict[str, str]] = defaultdict(dict)
        for c in listed:
            if c["expiration"] == expiry:
                pairs[float(c["strike"])][c["kind"]] = c["ticker"]
        both = [k for k, v in pairs.items() if {"call", "put"} <= v.keys()]
        if both:
            k = min(both, key=lambda x: (abs(x - spot), x))
            return [{"occ": pairs[k][kind], "kind": kind, "strike": k, "expiration": expiry.isoformat()}
                    for kind in ("call", "put")]
    return []


async def task(massive: Any, market: StocksMarket, home: Path, now: datetime, budget: int) -> dict[str, Any]:
    """The Massive queue's share: for each report the latest replay wanted, the listing as of the first day the rule
    looked at it (expiries up to 30 days out, strikes within 5%), then the chosen call's and put's daily bars."""
    from desk.stocks.massive_options import ask

    have, fetched = earnings_lists(market.conn), market.option_bar_fetches()
    used, problem = 0, None
    items = wanted(home)
    for w in items:
        if used >= budget or problem:
            break
        symbol, as_of, end, spot = w["symbol"], date.fromisoformat(w["as_of"]), date.fromisoformat(w["end"]), float(w["spot"])
        item = have.get((symbol, as_of))
        if item is None:
            listed, problem = await ask(massive.option_contracts(symbol, as_of, end + timedelta(days=1),
                                                                 as_of + timedelta(days=30),
                                                                 strikes=(spot * (1 - NEAR), spot * (1 + NEAR))))
            used += 1
            if problem:
                break
            chosen = choose(listed, spot, end)
            expiry = date.fromisoformat(chosen[0]["expiration"]) if chosen else None
            save_list(market.conn, PURPOSE, symbol, expiry, as_of, len(listed), chosen,
                      None if chosen else "no listed pair after the results window", now)
            item = have[(symbol, as_of)] = {"expiry": expiry, "contracts": chosen, "note": None}
        for c in item["contracts"]:
            if used >= budget or c["occ"] in fetched or item["expiry"] is None:
                continue
            bars, problem = await ask(massive.option_bars(c["occ"], as_of, item["expiry"]))
            used += 1
            if problem:
                break
            market.save_option_bars(c["occ"], as_of, item["expiry"], bars, now)
            fetched[c["occ"]] = item["expiry"]
    done = sum(1 for w in items if (it := have.get((w["symbol"], date.fromisoformat(w["as_of"])))) is not None
               and all(c["occ"] in fetched for c in it["contracts"]))
    detail = {"requests": used, "reports_wanted": len(items), "reports_done": done}
    market.job("earnings_history", now, error=problem, detail=detail)
    return {"ok": problem is None, **detail, **({"error": problem} if problem else {})}


__all__ = ["BASELINE", "METHOD", "STRATEGY_IDS", "RealOptions", "choose", "earnings_lists", "hold_long", "summary", "task",
           "wanted"]
