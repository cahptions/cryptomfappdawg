"""Four research-backed option strategies, preregistered, replayed on real past prices (Massive, last 2 years).

Each rule below was written down (``RULES``, version hash ``RULES_SHA256``, 2026-11-08) before any result existed, and
is replayed only on real data the IV history already downloads (``desk.stocks.iv_history``: each month's at-the-money
call and put per stock, their real daily trades, and IV30 solved from them). Nothing here trades, grades forward
PAPER, or changes a model: the results are a separate, labelled record, computed after each IV-history download.

Accounting is the real-price replays' (``desk.stocks.real_options``): $1,000 a long trade, bought at the next
session's real open plus 4% and $0.65 a contract a leg, exits on later real closes less 4% and fees, settled at
intrinsic value at expiry; a day a leg did not trade is skipped, never filled in.
"""

from __future__ import annotations

import json
import math
import random
from collections import defaultdict
from datetime import date, datetime, timedelta
from itertools import pairwise
from pathlib import Path
from statistics import mean
from typing import Any

from desk.core.clock import to_iso
from desk.core.hashing import canonical_json, sha256_hex
from desk.stocks import calendar as cal
from desk.stocks import options as opt
from desk.stocks.iv_history import lists, series
from desk.stocks.paper.fills import OPTION_FEE_PER_CONTRACT as FEE
from desk.stocks.real_options import SLIP, hold_long, summary
from desk.stocks.repo import StocksMarket, date_of

REPORT = "stocks-option-research.json"
ARCHIVE_DAYS, RATE = 725, 0.04
RULES: dict[str, dict[str, str]] = {
    "vol_gap_straddles": {
        "title": "Vol-gap straddles", "source": "Goyal & Saretto (2009), Journal of Financial Economics",
        "rule": "Each session, for every stock with an IV30 that day: if its 20-day realized volatility is at least "
                "1.25x its IV30, buy its monthly at-the-money straddle (the IV history's call and put, 30-60 days "
                "left, strike within 5% of the price). Best gap first, at most 5 open, one per stock. Exit at +50%, "
                "-40% or 21 days to expiry. Baseline: a random stock's straddle the same day, same exits."},
    "spy_put_write": {
        "title": "SPY put-write", "source": "Whaley (2002); the CBOE S&P 500 PutWrite index",
        "rule": "Each monthly expiry, on the first session with 45 days or fewer left on which SPY's at-the-money put "
                "traded: sell one put at the next session's open less 4% and $0.65, cash-secured (the strike x 100 "
                "set aside, earning nothing here), held to expiry and settled at intrinsic value. Compared with "
                "holding SPY over the same days."},
    "iv_spread": {
        "title": "IV spread", "source": "Cremers & Weinbaum (2010), Journal of Financial and Quantitative Analysis",
        "rule": "Each week's last session, for every stock whose IV-history call and put (same strike, 20-45 days "
                "left) both traded that day: spread = call IV - put IV. With 10 or more stocks, buy the 5 with the "
                "highest spread and sell the 5 lowest at the next open, closed at the open 5 sessions later; the "
                "long-minus-short return per week is the result."},
    "regime_chooser": {
        "title": "Regime chooser", "source": "Volatility risk premium by regime (Bakshi & Kapadia 2003; Coval & "
                                             "Shumway 2001)",
        "rule": "On each SPY put-write day: if SPY's IV30 sits in the upper half of its trailing year (IV rank >= "
                "50), sell the put as above; otherwise buy SPY's at-the-money straddle on the same expiry (exits "
                "+50%, -40%, 21 days to expiry). Compared with always writing the put."},
}
RULES_SHA256 = sha256_hex(canonical_json(RULES))


class Data:
    """The IV history's real contracts, bars, IV30 and stock closes, loaded once."""

    def __init__(self, market: StocksMarket, today: date) -> None:
        self.first = today - timedelta(days=ARCHIVE_DAYS)
        self.pairs: dict[str, list[tuple[date, dict[str, Any], dict[str, Any]]]] = defaultdict(list)
        for (symbol, expiry), item in lists(market.conn).items():
            legs = {c["kind"]: {**c, "expiration": expiry.isoformat()} for c in item["contracts"]}
            if {"call", "put"} <= legs.keys():
                self.pairs[symbol].append((expiry, legs["call"], legs["put"]))
        codes = {leg["occ"] for items in self.pairs.values() for _, c, p in items for leg in (c, p)}
        self.bars: dict[str, dict[date, tuple[float | None, float]]] = defaultdict(dict)
        for r in market.conn.execute("SELECT occ, day, open, close FROM stk_option_bars WHERE volume>0"):
            if r[0] in codes:
                self.bars[r[0]][date_of(r[1])] = (r[2], float(r[3]))
        self.iv = series(market.conn, self.pairs, self.first, today)
        self.closes = {s: {b.day: b.close for b in market.bars(s, self.first - timedelta(days=45))} for s in self.pairs}
        self.opens = {s: {b.day: b.open for b in market.bars(s, self.first)} for s in self.pairs}
        self.days = sorted({d for c in self.closes.values() for d in c if d >= self.first})

    def pair(self, symbol: str, d: date, dte: tuple[int, int], near: float = 0.05) -> tuple[date, list[dict[str, Any]]] | None:
        spot = self.closes[symbol].get(d)
        for expiry, call, put in sorted(self.pairs.get(symbol, [])):
            if (spot and dte[0] <= (expiry - d).days <= dte[1] and abs(math.log(call["strike"] / spot)) <= near
                    and d in self.bars[call["occ"]] and d in self.bars[put["occ"]]):
                return expiry, [call, put]
        return None

    def realized(self, symbol: str, d: date) -> float | None:
        closes = [v for k, v in sorted(self.closes[symbol].items()) if k <= d][-21:]
        rets = [math.log(b / a) for a, b in pairwise(closes) if a > 0 and b > 0]
        if len(rets) < 15:
            return None
        m = mean(rets)
        return math.sqrt(sum((r - m) ** 2 for r in rets) / (len(rets) - 1) * 252)


def _book(signals: dict[date, list[tuple[float, str, list[dict[str, Any]]]]], data: Data, plan: dict[str, float]
          ) -> list[dict[str, Any]]:
    held: list[tuple[date, str]] = []
    trades = []
    for d in sorted(signals):
        held = [(end, s) for end, s in held if end >= cal.next_trading_day(d)]
        for _, symbol, legs in sorted(signals[d], key=lambda x: (-x[0], x[1])):
            if len(held) >= 5 or any(s == symbol for _, s in held):
                continue
            done = hold_long(data.bars, legs, d, plan, data.closes[symbol])
            if done:
                held.append((date.fromisoformat(done["exit_day"]), symbol))
                trades.append({**done, "symbol": symbol, "decided": d.isoformat(), "multiple": done["value"] / done["cost"]})
    return trades


PLAN = {"take_profit": 0.5, "stop": -0.4, "exit_dte": 21}


def vol_gap(data: Data) -> dict[str, Any]:
    mine: dict[date, list[tuple[float, str, list[dict[str, Any]]]]] = defaultdict(list)
    base: dict[date, list[tuple[float, str, list[dict[str, Any]]]]] = defaultdict(list)
    for d in data.days:
        pool = []
        for s in data.pairs:
            found = data.pair(s, d, (30, 60))
            if found is None:
                continue
            pool.append((s, found[1]))
            iv, rv = data.iv.get(s, {}).get(d.toordinal()), data.realized(s, d)
            if iv and rv and rv / iv >= 1.25:
                mine[d].append((rv / iv, s, found[1]))
        if pool:
            s, legs = random.Random(f"vol_gap:{d}").choice(sorted(pool, key=lambda x: x[0]))  # noqa: S311
            base[d].append((0.0, s, legs))
    from desk.stocks.lotto_history import versus

    trades, random_trades = _book(mine, data, PLAN), _book(base, data, PLAN)
    return {**summary(trades), "baseline": summary(random_trades), "versus_baseline": versus(trades, random_trades)}


def _write_days(data: Data) -> list[tuple[date, date, dict[str, Any], dict[str, Any]]]:
    """SPY's put-write days: (decision day, expiry, call, put), one per monthly expiry."""
    out = []
    for expiry, call, put in sorted(data.pairs.get("SPY", [])):
        for d in data.days:
            if 0 < (expiry - d).days <= 45 and d in data.bars[put["occ"]] and d < expiry:
                out.append((d, expiry, call, put))
                break
    return out


def _put_write(data: Data, d: date, expiry: date, put: dict[str, Any]) -> dict[str, Any] | None:
    entry = cal.next_trading_day(d)
    bar = data.bars[put["occ"]].get(entry)
    settle = data.closes["SPY"].get(expiry)
    if bar is None or not bar[0] or settle is None:
        return None
    premium = bar[0] * (1 - SLIP) * 100 - FEE
    owed = max(put["strike"] - settle, 0.0) * 100
    collateral = put["strike"] * 100
    return {"decided": d.isoformat(), "entry_day": entry.isoformat(), "exit_day": expiry.isoformat(), "symbol": "SPY",
            "cost": collateral, "value": collateral + premium - owed, "multiple": (collateral + premium - owed) / collateral,
            "exit": "settled at expiry"}


def put_write(data: Data) -> dict[str, Any]:
    trades = [t for d, e, _, put in _write_days(data) if (t := _put_write(data, d, e, put))]
    spy = data.closes.get("SPY", {})
    held = [spy[date.fromisoformat(t["exit_day"])] / spy[date.fromisoformat(t["entry_day"])] - 1
            for t in trades if date.fromisoformat(t["entry_day"]) in spy and date.fromisoformat(t["exit_day"]) in spy]
    return {**summary(trades), "spy_same_days": mean(held) if held else None}


def _week_return(data: Data, symbol: str, start: date, end: date) -> float | None:
    a, b = data.opens[symbol].get(start), data.opens[symbol].get(end)
    return b / a - 1 if a and b else None


def iv_spread(data: Data) -> dict[str, Any]:
    weeks = [d for d, n in zip(data.days, [*data.days[1:], None], strict=True)
             if n is None or n.isocalendar()[1] != d.isocalendar()[1]]
    gaps: list[dict[str, Any]] = []
    for d in weeks:
        ranked: list[tuple[float, str]] = []
        for s in data.pairs:
            found = data.pair(s, d, (20, 45), near=0.10)
            spot = data.closes[s].get(d)
            if found is None or not spot:
                continue
            expiry, (call, put) = found
            t = opt.year_fraction(d, expiry)
            call_iv, put_iv = (opt.implied_vol(leg["kind"], data.bars[leg["occ"]][d][1], spot, leg["strike"], t, RATE)
                               for leg in (call, put))
            if call_iv and put_iv:
                ranked.append((call_iv - put_iv, s))
        if len(ranked) < 10:
            continue
        ranked.sort()
        start = cal.next_trading_day(d)
        end = start
        for _ in range(5):
            end = cal.next_trading_day(end)

        top = [r for _, s in ranked[-5:] if (r := _week_return(data, s, start, end)) is not None]
        bottom = [r for _, s in ranked[:5] if (r := _week_return(data, s, start, end)) is not None]
        if top and bottom:
            gaps.append({"week": d.isoformat(), "spread": mean(top) - mean(bottom)})
    values = [float(g["spread"]) for g in gaps]
    if len(values) < 5:
        return {"weeks": len(values)}
    rng = random.Random(1)                                   # noqa: S311 - a reproducible resample
    boots = sorted(mean(rng.choices(values, k=len(values))) for _ in range(2000))
    return {"weeks": len(values), "mean_weekly": mean(values), "low": boots[100], "high": boots[1900],
            "p_not_better": sum(b <= 0 for b in boots) / 2000, "first": gaps[0]["week"], "last": gaps[-1]["week"]}


def regime(data: Data) -> dict[str, Any]:
    chosen, always = [], []
    spy_iv = data.iv.get("SPY", {})
    for d, expiry, call, put in _write_days(data):
        year = [v for k, v in spy_iv.items() if d.toordinal() - 365 <= k <= d.toordinal()]
        today = spy_iv.get(d.toordinal())
        if (written := _put_write(data, d, expiry, put)) is not None:
            always.append(written)
        if today is None or len(year) < 60:
            continue
        rank = opt.iv_rank(year, today)[0]
        if rank is not None and rank >= 50:
            if written is not None:
                chosen.append({**written, "choice": "put-write"})
        elif (bought := hold_long(data.bars, [call, put], d, PLAN, data.closes["SPY"])) is not None:
            chosen.append({**bought, "symbol": "SPY", "decided": d.isoformat(),
                           "multiple": bought["value"] / bought["cost"], "choice": "straddle"})
    from desk.stocks.lotto_history import versus

    return {**summary(chosen), "always_put_write": summary(always), "versus_put_write": versus(chosen, always),
            "straddle_months": sum(t["choice"] == "straddle" for t in chosen)}


def run(market: StocksMarket, now: datetime) -> dict[str, Any]:
    data = Data(market, cal.new_york(now).date())
    results = {"vol_gap_straddles": vol_gap(data), "spy_put_write": put_write(data), "iv_spread": iv_spread(data),
               "regime_chooser": regime(data)}
    return {"version": 1, "generated_at": to_iso(now), "rules_sha256": RULES_SHA256, "preregistered": "2026-11-08",
            "stocks": len(data.pairs), "first": data.first.isoformat(),
            "strategies": {k: {**RULES[k], **v} for k, v in results.items()},
            "note": "Preregistered before any result; replayed on real prices only; never changes a forward grade."}


def due(home: Path, now: datetime, every: timedelta = timedelta(hours=6)) -> bool:
    """Recompute when there is no result yet, or the last one is older than ``every``."""
    found = load(home)
    try:
        return found is None or now - datetime.fromisoformat(str(found.get("generated_at"))) >= every
    except ValueError:
        return True


def save(home: Path, market: StocksMarket, now: datetime) -> None:
    path = home / REPORT
    fresh = path.with_suffix(".new")
    fresh.write_text(json.dumps(run(market, now), allow_nan=False), encoding="utf-8")
    fresh.replace(path)


def load(home: Path) -> dict[str, Any] | None:
    try:
        value = json.loads((home / REPORT).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


__all__ = ["REPORT", "RULES", "RULES_SHA256", "due", "load", "run", "save"]
