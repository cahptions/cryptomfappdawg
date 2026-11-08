"""Lottos on real past prices.

The PAPER lotto accounts trade forward from the day they were added. This replays the same idea over the last two
years on real prices and no pricing model: Massive's Options Basic archive keeps two years of daily bars for every
contract, weeklies included. A lotto's value lives in the far, short-dated tails a model misprices, so only real
trades count here.

Download, a few requests each collection pass and more while the market is closed (Massive allows 5 a minute): for
each of the most traded optioned names and every weekly expiry, the out-of-the-money contracts listed two weeks before
it (one request a side), then each candidate's daily bars over those two weeks (one request each). Candidates are the
strikes a rough price estimate puts near the lotto range; the real prices decide what counts.

Replay, on every downloaded day: each contract whose real close was $0.05-$0.50, 2-14 days from expiry, at least 3%
out of the money and traded at least 50 times that day. KESTREL's Lottos rule ranks them (the payoff on a
one-standard-deviation move per dollar, times the chance in the money, from the contract's implied and the stock's
realized volatility; the replay has no committee tilt), the best per stock, the top 12 a day. Random lottos take a
random qualifying contract per stock. Each pick is $300, bought at the next session's real open plus 5% (about half a
lotto's spread) and $0.65 a contract; sold at triple on a later close less 5%, or held and settled at expiry.
"""

from __future__ import annotations

import json
import math
import random
import sqlite3
from collections import defaultdict
from datetime import date, datetime, timedelta
from itertools import pairwise
from pathlib import Path
from statistics import mean, median
from typing import Any

from desk.core.clock import to_iso
from desk.core.errors import DeskError, ProviderError
from desk.stocks import calendar as cal
from desk.stocks import options as opt
from desk.stocks.odds import IV_WEIGHT
from desk.stocks.paper.fills import OPTION_FEE_PER_CONTRACT as FEE
from desk.stocks.repo import StocksMarket, date_of, day_of

NAMES = ("SPY", "QQQ", "IWM", "AAPL", "NVDA", "TSLA", "AMD", "AMZN", "META", "MSFT", "GOOGL", "NFLX")
ARCHIVE_DAYS = 725                 # Massive Options Basic: the last 2 years
LEAD_DAYS = 14                     # a weekly is a lotto over its last two weeks
PER_SIDE = 8                       # candidate strikes downloaded per expiry, calls and puts each
PER_PASS_OPEN, PER_PASS_CLOSED = 8, 40
ASK, OTM, MIN_VOLUME, DTE = (0.05, 0.50), 0.03, 50, (2, 14)
STAKE, SLIP, TRIPLE, PICKS, RATE = 300.0, 0.05, 3.0, 12, 0.04
REPORT = "stocks-lotto-history.json"
METHOD = ("Each session, every downloaded contract that closed at $0.05-$0.50, 2-14 days from expiry, at least 3% out "
          "of the money and traded 50+ times that day qualifies. KESTREL's Lottos rule ranks them (payoff on a "
          "one-standard-deviation move per dollar x chance in the money; no committee tilt), the best per stock, top 12 "
          "a day; Random lottos take a random qualifying contract per stock. $300 a pick, bought at the next session's "
          "real open plus 5% and $0.65 a contract; sold at triple on a later close less 5%, or settled at expiry.")
VARIANTS = (("kestrel_triple", "KESTREL's picks · sold at triple"), ("kestrel_run", "KESTREL's picks · let them run"),
            ("random_triple", "Random lottos · sold at triple"), ("random_run", "Random lottos · let them run"))


# ── what to download ─────────────────────────────────────────────────────────────────────────────
def expiries(today: date) -> list[date]:
    """Every weekly expiry (Friday, or the trading day before a Friday holiday) already past, newest first."""
    out = []
    d = today - timedelta(days=1)
    first = today - timedelta(days=ARCHIVE_DAYS - LEAD_DAYS)
    while d.weekday() != 4:
        d -= timedelta(days=1)
    while d >= first:
        e = d if cal.is_trading_day(d) else cal.previous_trading_day(d)
        if e < today:
            out.append(e)
        d -= timedelta(days=7)
    return out


def _closes(market: StocksMarket, symbol: str, since: date) -> dict[date, float]:
    return {b.day: b.close for b in market.bars(symbol, since)}


def _on_or_before(closes: dict[date, float], day: date) -> float | None:
    for back in range(8):
        value = closes.get(day - timedelta(days=back))
        if value is not None:
            return value
    return None


def _realized(closes: dict[date, float], day: date, n: int = 20) -> float | None:
    series = [v for d, v in sorted(closes.items()) if d <= day][-(n + 1):]
    if len(series) < n // 2:
        return None
    rets = [math.log(b / a) for a, b in pairwise(series) if a > 0 and b > 0]
    if len(rets) < 2:
        return None
    m = mean(rets)
    return math.sqrt(sum((r - m) ** 2 for r in rets) / (len(rets) - 1) * 252)


def choose(listed: list[dict[str, Any]], spot: float, vol: float, as_of: date, expiry: date) -> list[dict[str, Any]]:
    """The out-of-the-money strikes a rough estimate puts near the lotto range at some point in the last two weeks,
    nearest the money first, ``PER_SIDE`` a side. Generous on purpose: the real prices decide what qualifies."""
    picks: list[dict[str, Any]] = []
    for kind in ("call", "put"):
        side = []
        for c in listed:
            if c["kind"] != kind:
                continue
            otm = c["strike"] / spot - 1 if kind == "call" else 1 - c["strike"] / spot
            if not OTM <= otm <= 0.40:
                continue
            prices = [opt.bs_price(kind, spot, c["strike"], max(days, 1) / 365, max(vol * 1.2, 0.12), RATE)
                      for days in ((expiry - as_of).days, 5)]
            if any(0.02 <= p <= 1.0 for p in prices):
                side.append((otm, c))
        picks += [{"occ": c["ticker"], "kind": c["kind"], "strike": c["strike"]}
                  for _, c in sorted(side, key=lambda x: x[0])[:PER_SIDE]]
    return picks


def _listings(conn: sqlite3.Connection) -> dict[tuple[str, date], dict[str, Any]]:
    return {(r["symbol"], date_of(r["expiry"])): {"as_of": date_of(r["as_of"]), "contracts": json.loads(r["contracts"]),
                                                  "listed": r["listed"], "note": r["note"]}
            for r in conn.execute("SELECT * FROM stk_lotto_lists")}


async def download(massive: Any, market: StocksMarket, now: datetime, budget: int | None = None) -> dict[str, Any]:
    """One pass of the background download: listings first, then each listed candidate's real daily bars."""
    if not getattr(massive, "configured", False):
        return {"ok": False, "note": "No Massive key: the lotto replay needs MASSIVE_API_KEY (free Options Basic)."}
    today = cal.new_york(now).date()
    if budget is None:
        budget = PER_PASS_OPEN if cal.market_session(now)["state"] in ("pre", "open") else PER_PASS_CLOSED
    listed, fetched = _listings(market.conn), market.option_bar_fetches()
    used, problem = 0, None
    for expiry in expiries(today):
        for symbol in NAMES:
            if used >= budget or problem:
                break
            item = listed.get((symbol, expiry))
            if item is None:
                as_of = expiry - timedelta(days=LEAD_DAYS)
                as_of = as_of if cal.is_trading_day(as_of) else cal.previous_trading_day(as_of)
                closes = _closes(market, symbol, as_of - timedelta(days=45))
                spot, vol = _on_or_before(closes, as_of), _realized(closes, as_of)
                contracts: list[dict[str, Any]] = []
                note = None
                if spot is None:
                    note = f"no stored {symbol} price on {as_of}"
                else:
                    try:
                        for kind, band in (("call", (spot * (1 + OTM), spot * 1.40)),
                                           ("put", (spot * 0.60, spot * (1 - OTM)))):
                            contracts += await massive.option_contracts(symbol, as_of, expiry, expiry, kind=kind,
                                                                        strikes=band)
                            used += 1
                    except ProviderError as exc:
                        problem = (f"Massive refused option listings (HTTP {exc.status_code}). The lotto replay needs "
                                   "the free Options Basic plan." if exc.status_code in (401, 403)
                                   else f"Massive could not answer: {str(exc)[:160]}")
                        break
                    except DeskError as exc:
                        problem = f"Massive's answer could not be read: {str(exc)[:160]}"
                        break
                chosen = choose(contracts, spot, vol or 0.3, as_of, expiry) if spot else []
                market.conn.execute("INSERT OR REPLACE INTO stk_lotto_lists VALUES (?,?,?,?,?,?,?)",
                                    (symbol, day_of(expiry), day_of(as_of), len(contracts), json.dumps(chosen), note,
                                     to_iso(now)))
                item = listed[(symbol, expiry)] = {"as_of": as_of, "contracts": chosen, "listed": len(contracts)}
            for c in item["contracts"]:
                if used >= budget:
                    break
                if c["occ"] in fetched:
                    continue
                try:
                    bars = await massive.option_bars(c["occ"], item["as_of"], expiry)
                except ProviderError as exc:
                    if exc.status_code != 404:
                        problem = f"Massive could not answer: {str(exc)[:160]}"
                        break
                    bars = []
                except DeskError as exc:
                    problem = f"Massive's answer could not be read: {str(exc)[:160]}"
                    break
                used += 1
                market.save_option_bars(c["occ"], item["as_of"], expiry, bars, now)
                fetched[c["occ"]] = expiry
        if used >= budget or problem:
            break
    detail = {"requests": used, **coverage(market, today)}
    market.job("lotto_history", now, error=problem, detail=detail)
    return {"ok": problem is None, **detail, **({"error": problem} if problem else {})}


def coverage(market: StocksMarket, today: date) -> dict[str, Any]:
    listed = _listings(market.conn)
    fetched = market.option_bar_fetches()
    planned = len(expiries(today)) * len(NAMES)
    wanted = [c["occ"] for item in listed.values() for c in item["contracts"]]
    left = (planned - len(listed)) * (2 + 2 * PER_SIDE) + sum(1 for o in wanted if o not in fetched)
    return {"names": len(NAMES), "weeks": len(expiries(today)),
            "expiries_planned": planned, "expiries_listed": len(listed), "contracts_wanted": len(wanted),
            "contracts_fetched": sum(1 for o in wanted if o in fetched), "requests_left": left,
            "hours_left_at_full_speed": round(left / 5 / 60, 1)}


# ── the replay ───────────────────────────────────────────────────────────────────────────────────
def _ncdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def score(kind: str, price: float, spot: float, strike: float, years: float, rv: float | None) -> float | None:
    """KESTREL's Lottos ranking: the payoff on a one-standard-deviation move per dollar × the chance in the money."""
    iv = opt.implied_vol(kind, price, spot, strike, years, RATE)
    if iv is None:
        return None
    sigma = math.sqrt(IV_WEIGHT * iv * iv + (1 - IV_WEIGHT) * rv * rv) if rv else iv
    move = math.exp(sigma * math.sqrt(years))
    target = spot * move if kind == "call" else spot / move
    payoff = max(target - strike, 0.0) if kind == "call" else max(strike - target, 0.0)
    d2 = (math.log(spot / strike) + (RATE - 0.5 * sigma * sigma) * years) / (sigma * math.sqrt(years))
    chance = _ncdf(d2) if kind == "call" else _ncdf(-d2)
    value = payoff / price * chance
    return value if value > 0 else None


def trade(bars: dict[date, dict[str, Any]], decided: date, expiry: date, kind: str, strike: float,
          settle_close: float | None, *, triple: bool) -> dict[str, Any] | None:
    """One $300 lotto bought at the next session's real open; None when it did not trade that session."""
    entry_day = cal.next_trading_day(decided)
    bar = bars.get(entry_day)
    if bar is None or not bar.get("open") or entry_day > expiry:
        return None
    price = bar["open"] * (1 + SLIP) + 0.005
    units = int(STAKE // (price * 100 + FEE))
    if units < 1:
        return None
    cost = units * (price * 100 + FEE)
    if triple:
        for day in sorted(d for d in bars if entry_day <= d <= expiry):
            value = units * (bars[day]["close"] * (1 - SLIP) * 100 - FEE)
            if value >= TRIPLE * cost:
                return {"entry_day": entry_day.isoformat(), "units": units, "cost": cost, "value": value,
                        "exit": f"sold at triple on {day.isoformat()}"}
    if settle_close is None:
        return None
    intrinsic = max(settle_close - strike, 0.0) if kind == "call" else max(strike - settle_close, 0.0)
    value = units * (intrinsic * 100 - FEE) if intrinsic * 100 > FEE else 0.0
    return {"entry_day": entry_day.isoformat(), "units": units, "cost": cost, "value": value,
            "exit": "expired in the money" if value > 0 else "expired worthless"}


def replay(market: StocksMarket, now: datetime) -> dict[str, Any]:
    today = cal.new_york(now).date()
    listed = _listings(market.conn)
    pools: dict[date, dict[str, list[dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    contract_bars: dict[str, dict[date, dict[str, Any]]] = {}
    settles: dict[tuple[str, date], float | None] = {}
    closes_by: dict[str, dict[date, float]] = {}
    for (symbol, expiry), item in listed.items():
        if not item["contracts"]:
            continue
        if symbol not in closes_by:
            closes_by[symbol] = _closes(market, symbol, today - timedelta(days=ARCHIVE_DAYS + 60))
        closes = closes_by[symbol]
        settles[(symbol, expiry)] = closes.get(expiry)
        for c in item["contracts"]:
            rows = market.conn.execute("SELECT day, open, high, low, close, volume FROM stk_option_bars WHERE occ=?",
                                       (c["occ"],)).fetchall()
            bars = {date_of(r["day"]): dict(r) for r in rows}
            contract_bars[c["occ"]] = bars
            for day, bar in bars.items():
                days_left = (expiry - day).days
                spot = closes.get(day)
                if spot is None or not DTE[0] <= days_left <= DTE[1]:
                    continue
                otm = c["strike"] / spot - 1 if c["kind"] == "call" else 1 - c["strike"] / spot
                if ASK[0] <= bar["close"] <= ASK[1] and otm >= OTM and (bar["volume"] or 0) >= MIN_VOLUME:
                    pools[day][symbol].append({**c, "symbol": symbol, "expiry": expiry, "close": bar["close"],
                                               "spot": spot, "otm": otm, "decided": day})
    trades: dict[str, list[dict[str, Any]]] = {v: [] for v, _ in VARIANTS}
    unfilled = 0
    for day in sorted(pools):
        kestrel, randoms = [], []
        for symbol, options in sorted(pools[day].items()):
            rv = _realized(closes_by[symbol], day)
            ranked = [(s, o) for o in options
                      if (s := score(o["kind"], o["close"], o["spot"], o["strike"],
                                     opt.year_fraction(day, o["expiry"]), rv)) is not None]
            if ranked:
                kestrel.append(max(ranked, key=lambda x: x[0]))
            randoms.append(random.Random(f"lotto:{day}:{symbol}").choice(sorted(options, key=lambda o: o["occ"])))  # noqa: S311
        chosen = {"kestrel": [o for _, o in sorted(kestrel, key=lambda x: -x[0])[:PICKS]],
                  "random": random.Random(f"lotto:{day}").sample(randoms, min(PICKS, len(randoms)))}  # noqa: S311
        for who, picks in chosen.items():
            for o in picks:
                settle = settles.get((o["symbol"], o["expiry"]))
                for triple in (True, False):
                    done = trade(contract_bars[o["occ"]], day, o["expiry"], o["kind"], o["strike"], settle, triple=triple)
                    if done is None:
                        if triple and who == "kestrel":
                            unfilled += 1          # it did not trade at the next session's open
                        continue
                    trades[f"{who}_{'triple' if triple else 'run'}"].append(
                        {**done, "occ": o["occ"], "symbol": o["symbol"], "decided": day.isoformat(),
                         "multiple": done["value"] / done["cost"]})
    summary = {v: summarize(trades[v]) for v, _ in VARIANTS}
    for v, title in VARIANTS:
        summary[v]["title"] = title
    days = sorted(pools)
    return {"version": 1, "generated_at": to_iso(now),
            "first": days[0].isoformat() if days else None, "last": days[-1].isoformat() if days else None,
            "sessions": len(days), "names": list(NAMES), "unfilled_kestrel": unfilled, "variants": summary,
            "kestrel_vs_random": {"run": versus(trades["kestrel_run"], trades["random_run"]),
                                  "triple": versus(trades["kestrel_triple"], trades["random_triple"])},
            "coverage": coverage(market, today), "method": METHOD}


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"picks": 0}
    cost, value = sum(r["cost"] for r in rows), sum(r["value"] for r in rows)
    multiples = [r["multiple"] for r in rows]
    best = max(rows, key=lambda r: r["multiple"])
    return {"picks": len(rows), "cost": round(cost, 2), "value": round(value, 2), "pnl": round(value - cost, 2),
            "return": (value - cost) / cost, "worthless": sum(m == 0 for m in multiples) / len(rows),
            "at_least_3x": sum(m >= 3 for m in multiples) / len(rows),
            "at_least_10x": sum(m >= 10 for m in multiples) / len(rows), "median_multiple": median(multiples),
            "best": {k: best[k] for k in ("symbol", "occ", "decided", "multiple", "exit")}}


def versus(mine: list[dict[str, Any]], baseline: list[dict[str, Any]], draws: int = 2000) -> dict[str, Any] | None:
    """KESTREL's average return per pick minus random's, day by day, with a bootstrap over days."""
    by: dict[str, dict[str, list[float]]] = defaultdict(lambda: {"k": [], "r": []})
    for r in mine:
        by[r["decided"]]["k"].append(r["multiple"] - 1)
    for r in baseline:
        by[r["decided"]]["r"].append(r["multiple"] - 1)
    gaps = [mean(v["k"]) - mean(v["r"]) for v in by.values() if v["k"] and v["r"]]
    if len(gaps) < 5:
        return None
    rng = random.Random(1)        # noqa: S311 - a reproducible resample
    boots = sorted(mean(rng.choices(gaps, k=len(gaps))) for _ in range(draws))
    return {"days": len(gaps), "mean_gap": mean(gaps), "low": boots[int(0.05 * draws)], "high": boots[int(0.95 * draws)],
            "p_not_better": sum(b <= 0 for b in boots) / draws}


def save(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fresh = path.with_suffix(".new")
    fresh.write_text(json.dumps(report, allow_nan=False), encoding="utf-8")
    fresh.replace(path)


def load(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


async def job(massive: Any, market: StocksMarket, home: Path, now: datetime, budget: int | None = None) -> dict[str, Any]:
    """Download a batch, then rebuild the report when enough is new (it reads every downloaded bar)."""
    got = await download(massive, market, now, budget)
    previous = load(home / REPORT)
    fetched = got.get("contracts_fetched", 0)
    stale = previous is None or fetched - (previous.get("coverage") or {}).get("contracts_fetched", 0) >= 150 or (
        got.get("requests_left") == 0 and (previous.get("coverage") or {}).get("requests_left") != 0)
    if stale and fetched:
        save(home / REPORT, replay(market, now))
        got["report"] = "rebuilt"
    return got


async def task(massive: Any, market: StocksMarket, home: Path, now: datetime, budget: int) -> dict[str, Any]:
    """The Massive queue's share for the lottos (``desk.stocks.massive_options``)."""
    return await job(massive, market, home, now, budget)


__all__ = ["NAMES", "REPORT", "VARIANTS", "choose", "coverage", "download", "expiries", "job", "load", "replay",
           "score", "summarize", "task", "trade", "versus"]
