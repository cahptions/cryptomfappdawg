"""Whole-portfolio risk: every open PAPER options position and every one of My tracked plays, added up.

For each option leg the greeks come from, in order:

1. **quotes**: a tracked play's latest stored session row (``stk_track_days``: KESTREL's greeks solved from the
   quoted mid, per share), when all four are there;
2. **modelled**: the app's option model (Black-Scholes, ``desk.stocks.options``) at the stock's latest stored close,
   with the leg's own implied volatility (its latest mark's, else its entry's, else the tracked quote's), a 4% rate
   and no dividends;
3. otherwise **unknown**: counted and shown as unknown, never as zero.

Totals: delta in shares and in dollars, beta-weighted to SPY (dollars per 1% SPY move, and SPY-equivalent shares),
vega (dollars per 1 volatility point), theta (dollars a day), the largest single name and sector by gross dollar
delta, expiries by week, and three modelled shocks: SPY −5% and +5% (each stock moved by its beta) and implied
volatility +5 points, by full Black-Scholes revaluation of every leg whose volatility is known. Betas come from up to a
year of stored daily closes against SPY; a stock with fewer than 60 shared days is taken as 1 and says so.

Read-only. No provider call, no order, no estimate fills a gap: research and PAPER only.
"""

from __future__ import annotations

import json
import math
import sqlite3
from collections import defaultdict
from collections.abc import Iterable
from datetime import date, datetime, timedelta
from itertools import pairwise
from typing import Any

import numpy as np

from desk.stocks import calendar as cal
from desk.stocks import options as opt
from desk.stocks.repo import date_of

RATE = 0.04
SHOCK, VOL_SHOCK = 0.05, 0.05
CONTRACT = 100
BETA_DAYS, BETA_MIN = 252, 60
GREEKS = ("delta", "gamma", "theta", "vega")


def _finite(*values: Any) -> bool:
    return all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in values)


def _close(market: sqlite3.Connection, symbol: str) -> tuple[float, date] | None:
    row = market.execute("SELECT close, day FROM stk_bars WHERE symbol=? ORDER BY day DESC LIMIT 1",
                         (symbol,)).fetchone()
    return (float(row[0]), date_of(row[1])) if row and _finite(row[0]) and row[0] > 0 else None


def beta(market: sqlite3.Connection, symbol: str) -> float | None:
    """Slope of the stock's daily returns on SPY's over up to a year of shared stored closes; None under 60 days.
    A day-to-day move over 40% (an unadjusted split) is left out rather than adjusted."""
    if symbol == "SPY":
        return 1.0
    rows = market.execute("SELECT s.day, s.close, b.close FROM stk_bars s JOIN stk_bars b ON b.day=s.day AND "
                          "b.symbol='SPY' WHERE s.symbol=? ORDER BY s.day DESC LIMIT ?",
                          (symbol, BETA_DAYS + 1)).fetchall()[::-1]
    pairs = []
    for (d0, s0, b0), (d1, s1, b1) in pairwise(rows):
        if d1 - d0 > 7 or not (s0 > 0 and b0 > 0):
            continue
        rs, rb = s1 / s0 - 1, b1 / b0 - 1
        if abs(rs) < 0.4 and abs(rb) < 0.4:
            pairs.append((rs, rb))
    if len(pairs) < BETA_MIN:
        return None
    x = np.array([b for _, b in pairs])
    y = np.array([s for s, _ in pairs])
    var = float(np.var(x))
    return None if var <= 0 else round(float(np.cov(x, y, bias=True)[0, 1] / var), 3)


def _sector(market: sqlite3.Connection, symbol: str) -> str:
    from desk.stocks.sectors import LABELS, sector

    row = market.execute("SELECT c.sector, c.sic FROM stk_listings l JOIN stk_companies c ON c.cik=l.cik "
                         "WHERE l.symbol=?", (symbol,)).fetchone()
    if row is None:
        return "Fund or unknown"
    return str(row[0]) if row[0] else LABELS.get(sector(row[1]), "Other")


def _paper_legs(ledger: sqlite3.Connection) -> Iterable[dict[str, Any]]:
    from desk.stocks.paper.strategies import STRATEGIES

    titles = {s.id: s.title for s in STRATEGIES}
    for row in ledger.execute("SELECT id, account, symbol, structure, legs_json, marks_json, last_value_micros "
                              "FROM stk_positions WHERE state='OPEN' AND asset='option' ORDER BY opened_at, id"):
        marks = (json.loads(row["marks_json"]) or {}).get("legs", {}) if row["marks_json"] else {}
        legs = []
        for leg in json.loads(row["legs_json"]):
            mark = marks.get(leg.get("occ"), {}) or {}
            legs.append({"occ": leg.get("occ"), "kind": leg.get("kind"), "strike": leg.get("strike"),
                         "expiration": leg.get("expiration"), "contracts": leg.get("quantity"),
                         "iv": mark.get("iv") or leg.get("last_iv") or leg.get("entry_iv"), "greeks": None})
        yield {"id": row["id"], "group": "PAPER", "book": titles.get(row["account"], row["account"]),
               "symbol": row["symbol"], "label": f"{row['symbol']} {row['structure'].replace('_', ' ')}",
               "value": None if row["last_value_micros"] is None else row["last_value_micros"] / 1e6, "legs": legs}


def _tracked_legs(research: sqlite3.Connection, now: datetime) -> Iterable[dict[str, Any]]:
    from desk.stocks.play_history import label
    from desk.stocks.play_selections import ticket

    rows = research.execute("SELECT p.id FROM stk_play_selections p WHERE EXISTS (SELECT 1 FROM "
                            "stk_play_selection_events e WHERE e.selection_id=p.id AND e.kind='ENTRY') AND NOT EXISTS "
                            "(SELECT 1 FROM stk_play_selection_events e WHERE e.selection_id=p.id AND e.kind IN "
                            "('CLOSE','CANCEL','EXPIRED_NO_ENTRY')) ORDER BY p.confirmed_at").fetchall()
    for (ident,) in rows:
        current = ticket(research, ident, now)
        if current["expired"]:
            continue
        play, units = current["idea"]["play"], int(current["quantity"])
        legs = []
        for leg in play["legs"]:
            day = research.execute("SELECT iv, delta, gamma, theta, vega FROM stk_track_days WHERE occ=? "
                                   "ORDER BY session DESC LIMIT 1", (leg["occ"],)).fetchone()
            greeks = ({g: float(day[g]) for g in GREEKS} if day is not None and _finite(*(day[g] for g in GREEKS))
                      else None)
            sign = 1 if leg.get("side", "buy") == "buy" else -1
            legs.append({"occ": leg["occ"], "kind": leg.get("kind"), "strike": leg.get("strike"),
                         "expiration": leg.get("expiration") or play["expiration"],
                         "contracts": sign * int(leg.get("quantity", 1)) * units,
                         "iv": day["iv"] if day is not None else None, "greeks": greeks})
        yield {"id": ident, "group": "Tracked", "book": "My tracked plays", "symbol": play["symbol"],
               "label": label(play), "value": None, "legs": legs}


def _leg_risk(leg: dict[str, Any], spot: float | None, b: float, today: date) -> dict[str, Any]:
    """Per-contract-times-quantity dollar greeks and shocks for one leg; unknown parts stay None."""
    contracts = leg["contracts"]
    out: dict[str, Any] = {"basis": "unknown", **dict.fromkeys(("delta_shares", "theta", "vega", "gamma_shares",
                                                                 "down", "up", "vol_up"))}
    if not _finite(contracts) or not contracts or leg["kind"] not in ("call", "put") or not _finite(leg["strike"]):
        return out
    try:
        expiry = date.fromisoformat(str(leg["expiration"]))
    except ValueError:
        return out
    t = opt.year_fraction(today, expiry)
    size = contracts * CONTRACT
    iv = float(leg["iv"]) if _finite(leg["iv"]) and leg["iv"] > 0 else None
    greeks = leg["greeks"]
    if greeks is not None:
        out.update(basis="quotes", delta_shares=greeks["delta"] * size, gamma_shares=greeks["gamma"] * size,
                   theta=greeks["theta"] * size, vega=greeks["vega"] * size)
    elif spot is not None and iv is not None and t > 0:
        g = opt.greeks(leg["kind"], spot, float(leg["strike"]), t, iv, RATE)
        out.update(basis="modelled", delta_shares=g.delta * size, gamma_shares=g.gamma * size,
                   theta=g.theta_per_day * size, vega=g.vega_per_point * size)
    if spot is not None and iv is not None and t > 0:
        now = opt.bs_price(leg["kind"], spot, float(leg["strike"]), t, iv, RATE)

        def at(s: float, vol: float) -> float:
            return float((opt.bs_price(leg["kind"], s, float(leg["strike"]), t, vol, RATE) - now) * size)

        out.update(down=at(spot * (1 - SHOCK * b), iv), up=at(spot * (1 + SHOCK * b), iv),
                   vol_up=at(spot, iv + VOL_SHOCK))
    return out


def _monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def portfolio(ledger: sqlite3.Connection | None, research: sqlite3.Connection | None, market: sqlite3.Connection,
              now: datetime) -> dict[str, Any]:
    today = cal.new_york(now).date()
    positions = [*(_paper_legs(ledger) if ledger is not None else []),
                 *(_tracked_legs(research, now) if research is not None else [])]
    spy = _close(market, "SPY")
    prices: dict[str, tuple[float, date] | None] = {}
    betas: dict[str, float | None] = {}
    totals: dict[str, float] = defaultdict(float)
    unknown = {"greeks": 0, "shock": 0}
    names: dict[str, float] = defaultdict(float)
    sectors: dict[str, float] = defaultdict(float)
    weeks: dict[str, dict[str, Any]] = {}
    rows = []
    for p in positions:
        symbol = p["symbol"]
        if symbol not in prices:
            prices[symbol] = _close(market, symbol)
            betas[symbol] = beta(market, symbol)
        price = prices[symbol]
        spot = price[0] if price else None
        known_beta = betas[symbol]
        b = 1.0 if known_beta is None else known_beta
        row = {k: p[k] for k in ("id", "group", "book", "symbol", "label", "value")}
        row.update(price=spot, price_day=price[1].isoformat() if price else None, beta=betas[symbol],
                   beta_assumed=betas[symbol] is None)
        sums: dict[str, float | None] = dict.fromkeys(("delta_shares", "theta", "vega", "down", "up", "vol_up"), 0.0)
        bases = set()
        for leg in p["legs"]:
            r = _leg_risk(leg, spot, b, today)
            bases.add(r["basis"])
            for key in sums:
                value = sums[key]
                sums[key] = None if value is None or r[key] is None else value + r[key]
            try:
                expiry = date.fromisoformat(str(leg.get("expiration")))
            except ValueError:
                continue
            if _finite(leg.get("contracts")):
                with_week = _monday(expiry).isoformat()
                week = weeks.setdefault(with_week, {"week_of": with_week, "contracts": 0, "positions": set()})
                week["contracts"] += abs(int(leg["contracts"]))
                week["positions"].add(p["id"])
        row.update(sums)
        row["basis"] = "unknown" if "unknown" in bases else "modelled" if "modelled" in bases else "quotes"
        if row["delta_shares"] is not None and spot is not None:
            row["delta_dollars"] = row["delta_shares"] * spot
            row["spy_dollars_per_1pct"] = row["delta_dollars"] * b * 0.01
            names[symbol] += abs(row["delta_dollars"])
            sectors[_sector(market, symbol)] += abs(row["delta_dollars"])
            for key in ("delta_shares", "delta_dollars", "spy_dollars_per_1pct"):
                totals[key] += row[key]
        else:
            row["delta_dollars"] = row["spy_dollars_per_1pct"] = None
        for key in ("theta", "vega"):
            if row[key] is not None:
                totals[key] += row[key]
        if row["delta_shares"] is None or row["theta"] is None or row["vega"] is None:
            unknown["greeks"] += 1
        for key in ("down", "up", "vol_up"):
            if row[key] is not None:
                totals[key] += row[key]
        if row["down"] is None:
            unknown["shock"] += 1
        rows.append(row)
    gross = sum(names.values())

    def largest(found: dict[str, float]) -> dict[str, Any] | None:
        if not found or gross <= 0:
            return None
        name, value = max(found.items(), key=lambda kv: kv[1])
        return {"name": name, "gross_delta_dollars": round(value, 2), "share": round(value / gross, 4)}

    def money(key: str) -> float | None:
        return round(totals[key], 2) if any(r.get(key) is not None for r in rows) else None

    spy_price = spy[0] if spy else None
    per_pct = money("spy_dollars_per_1pct")
    return {
        "generated_at": now.isoformat(), "positions": len(rows),
        "paper_positions": sum(1 for r in rows if r["group"] == "PAPER"),
        "tracked_plays": sum(1 for r in rows if r["group"] == "Tracked"),
        "totals": {"delta_shares": money("delta_shares"), "delta_dollars": money("delta_dollars"),
                   "spy_dollars_per_1pct": per_pct,
                   "spy_equivalent_shares": None if per_pct is None or not spy_price else round(per_pct * 100 /
                                                                                                spy_price, 1),
                   "vega_per_point": money("vega"), "theta_per_day": money("theta")},
        "unknown": unknown,
        "largest_name": largest(names), "largest_sector": largest(sectors),
        "expiries": [{"week_of": w["week_of"], "contracts": w["contracts"], "positions": len(w["positions"])}
                     for _, w in sorted(weeks.items())],
        "scenarios": [{"name": "SPY −5%", "change": money("down")}, {"name": "SPY +5%", "change": money("up")},
                      {"name": "Volatility +5 points", "change": money("vol_up")}],
        "spy": None if spy is None else {"price": spy[0], "day": spy[1].isoformat()},
        "rows": [{k: (round(v, 2) if isinstance(v, float) and k not in ("beta", "price") else v)
                  for k, v in r.items()} for r in rows],
        "notes": ["Greeks per leg: from the tracked quotes where stored, otherwise modelled (Black-Scholes at the "
                  "latest stored close, the leg's own implied volatility, 4% rate, no dividends), otherwise unknown. "
                  "Unknown is left out of every total and counted, never taken as zero.",
                  "Scenarios are modelled: every leg with a known volatility repriced with each stock moved by its "
                  "beta to SPY (1 where under 60 days of shared history), or volatility raised 5 points. Time, "
                  "skew and liquidity are held still.",
                  "PAPER strategy books and My tracked plays only; the Learning research account is not included."],
        "orders_enabled": False, "mode": "PAPER",
    }


__all__ = ["beta", "portfolio"]
