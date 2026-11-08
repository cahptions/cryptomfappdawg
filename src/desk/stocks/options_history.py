"""Real historical option prices from Massive's free Options Basic plan (2 years of daily option bars, 5 requests a
minute).

``check`` is a small read-only probe (two requests: one past SPY expiry's calls near the money, then one of those
contracts' daily bars). ``download`` runs with each collection pass: it fetches the real daily prices of the contracts
the latest historical replay traded, a few at a time, so the next replay uses them in place of modeled prices."""

from __future__ import annotations

import json
import math
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from desk.core.errors import DeskError, ProviderError
from desk.core.paths import DeskPaths
from desk.stocks import calendar as cal
from desk.stocks.repo import StocksMarket, day_of

PER_PASS = 12                   # about two and a half minutes at 5 requests a minute
REPORT = "stocks-backtest-report.json"


async def check(massive: Any, market: StocksMarket, today: date) -> dict[str, Any]:
    if not getattr(massive, "configured", False):
        return {"ok": False, "text": "No Massive key: add MASSIVE_API_KEY in KESTREL → Settings → Sources."}
    as_of = today - timedelta(days=365)
    while not cal.is_trading_day(as_of):
        as_of -= timedelta(days=1)
    row = market.conn.execute("SELECT close FROM stk_bars WHERE symbol='SPY' AND day<=? ORDER BY day DESC LIMIT 1",
                              (day_of(as_of),)).fetchone()
    spot = float(row[0]) if row else None
    target = as_of + timedelta(days=30)
    first, last = target - timedelta(days=3), target + timedelta(days=3)
    try:
        listed = await massive.option_contracts("SPY", as_of, first, last, kind="call",
                                                strikes=(spot * 0.95, spot * 1.05) if spot else None)
        if not listed and spot:                         # a stored price on another scale: ask without the strikes
            listed = await massive.option_contracts("SPY", as_of, first, last, kind="call")
        if not listed:
            return {"ok": False, "text": (
                f"Massive answered but listed no SPY calls as of {as_of} expiring {first} to {last}"
                f"{f' (stored SPY close ${spot:,.2f})' if spot else ''}. The key is accepted (a key without option "
                "access is refused outright), so your plan is fine; the request itself needs fixing.")}
        expiry = min({c["expiration"] for c in listed}, key=lambda e: abs((e - target).days))
        calls = [c for c in listed if c["expiration"] == expiry]
        middle = spot if spot else sorted(c["strike"] for c in calls)[len(calls) // 2]
        pick = min(calls, key=lambda c: abs(c["strike"] - middle))
        bars = await massive.option_bars(pick["ticker"], as_of, expiry)
    except ProviderError as exc:
        if exc.status_code in (401, 403):
            return {"ok": False, "text": (
                f"Your Massive key works for stock prices but not for option history (HTTP {exc.status_code}). Sign in "
                "at massive.com and add the free Options Basic plan to the same account, then run this again.")}
        return {"ok": False, "text": f"Massive could not answer: {str(exc)[:160]}"}
    except DeskError as exc:
        return {"ok": False, "text": f"Massive's answer could not be read: {str(exc)[:160]}"}
    if not bars:
        return {"ok": False, "text": f"Massive listed {pick['ticker']} but returned no daily bars for it."}
    first, last = bars[0], bars[-1]
    return {"ok": True, "contract": pick, "bars": len(bars), "as_of": as_of.isoformat(), "text": (
        f"Yes: your Massive key reads historical option prices. SPY ${pick['strike']:g} call expiring {expiry}: "
        f"{len(bars)} daily bars from {first['day']} to {last['day']} (closed ${first['close']:.2f} → "
        f"${last['close']:.2f}). Historical option replays can use real prices (daily last trades, 2 years back).")}


def wanted(paths: DeskPaths | Path) -> list[dict[str, str]]:
    """The contracts the latest replay traded inside Massive's window (empty without a replay)."""
    home = paths if isinstance(paths, Path) else paths.home
    try:
        value = json.loads((home / REPORT).read_text())
    except (OSError, ValueError):
        return []
    rows = value.get("option_contracts") if isinstance(value, dict) else None
    return [r for r in rows if isinstance(r, dict) and {"occ", "first", "last"} <= r.keys()] if isinstance(rows, list) else []


def progress(market: StocksMarket, items: list[dict[str, str]]) -> dict[str, Any]:
    fetched = market.option_bar_fetches()
    left = sum(1 for w in items if fetched.get(w["occ"], date.min) < date.fromisoformat(w["last"]))
    asked, priced = market.option_bar_counts()
    return {"wanted": len(items), "left": left, "asked": asked, "with_prices": priced,
            "minutes_left": math.ceil(left / 5)}


async def download(massive: Any, market: StocksMarket, paths: DeskPaths | Path, now: datetime, *,
                   per_pass: int = PER_PASS) -> dict[str, Any]:
    """Fetch up to ``per_pass`` contracts' real daily prices; a contract still held at the replay's end is asked
    again once its replay window reaches past what was fetched."""
    items = wanted(paths)
    fetched = market.option_bar_fetches()
    todo = [w for w in items if fetched.get(w["occ"], date.min) < date.fromisoformat(w["last"])]
    if not todo:
        return {"ok": True, "requests": 0, **progress(market, items)}
    done, problem = 0, None
    for w in todo[:per_pass]:
        first, last = date.fromisoformat(w["first"]), date.fromisoformat(w["last"])
        try:
            bars = await massive.option_bars(w["occ"], first, last)
        except ProviderError as exc:
            if exc.status_code == 404:
                bars = []
            elif exc.status_code in (401, 403):
                problem = (f"Massive refused option prices (HTTP {exc.status_code}). Add the free Options Basic plan to the "
                           "account that owns MASSIVE_API_KEY.")
                break
            else:
                problem = f"Massive could not answer: {str(exc)[:160]}"
                break
        except DeskError as exc:
            problem = f"Massive's answer could not be read: {str(exc)[:160]}"
            break
        market.save_option_bars(w["occ"], first, last, bars, now)
        done += 1
    detail = {"fetched_now": done, **progress(market, items)}
    market.job("option_bars", now, error=problem, detail=detail)
    return {"ok": problem is None, "requests": done + (problem is not None), **detail,
            **({"error": problem} if problem else {})}


async def task(massive: Any, market: StocksMarket, home: Path, now: datetime, budget: int) -> dict[str, Any]:
    """The Massive queue's share for the replay's contracts (``desk.stocks.massive_options``)."""
    return await download(massive, market, home, now, per_pass=budget)


# ── real monthly listings for the stocks and months the replay trades ────────────────────────────
LISTING_PURPOSE = "replay"
LISTING_DAYS = (28, 95)          # expiries the rules' 30-60 days can reach from any day of the month
LISTING_STRIKES = 0.20           # strikes within 20% of the price on the month's first session


def listing_months(home: Path) -> list[tuple[str, date]]:
    """Each (stock, first session of the month) in which the latest replay traded its options, newest first."""
    from desk.stocks.option_model import parse_occ

    months = set()
    for w in wanted(home):
        parsed = parse_occ(w["occ"])
        if parsed is None:
            continue
        decided = date.fromisoformat(w["first"]) + timedelta(days=10)   # ``first`` is ten days before the entry
        start = decided.replace(day=1)
        months.add((parsed[0], start if cal.is_trading_day(start) else cal.next_trading_day(start)))
    return sorted(months, key=lambda m: (m[1], m[0]), reverse=True)


def listings(conn: Any) -> dict[tuple[str, int, int], dict[str, Any]]:
    """The downloaded month listings: (stock, year, month) -> {as_of, expiries: {date: strikes}}."""
    out = {}
    for r in conn.execute("SELECT symbol, as_of, contracts FROM stk_option_lists WHERE purpose=?", (LISTING_PURPOSE,)):
        as_of = date.fromordinal(r[1])
        out[(r[0], as_of.year, as_of.month)] = {"as_of": as_of, "expiries": {
            date.fromisoformat(e): strikes for e, strikes in json.loads(r[2]).items()}}
    return out


async def listings_task(massive: Any, market: StocksMarket, home: Path, now: datetime, budget: int) -> dict[str, Any]:
    """The Massive queue's share for real listings: one listing request per stock-month the replay traded."""
    from desk.stocks.iv_history import save_list
    from desk.stocks.massive_options import ask

    have = {(s, y, m) for s, y, m in listings(market.conn)}
    todo = [(s, d) for s, d in listing_months(home) if (s, d.year, d.month) not in have]
    used, problem = 0, None
    for symbol, as_of in todo[:budget]:
        row = market.conn.execute("SELECT close FROM stk_bars WHERE symbol=? AND day<=? ORDER BY day DESC LIMIT 1",
                                  (symbol, as_of.toordinal())).fetchone()
        if row is None:
            continue
        spot = float(row[0])
        listed, problem = await ask(massive.option_contracts(
            symbol, as_of, as_of + timedelta(days=LISTING_DAYS[0]), as_of + timedelta(days=LISTING_DAYS[1]),
            strikes=(spot * (1 - LISTING_STRIKES), spot * (1 + LISTING_STRIKES))))
        used += 1
        if problem:
            break
        expiries: dict[str, set[float]] = {}
        for c in listed:
            expiries.setdefault(c["expiration"].isoformat(), set()).add(float(c["strike"]))
        save_list(market.conn, LISTING_PURPOSE, symbol, None, as_of, len(listed),
                  {e: sorted(k) for e, k in sorted(expiries.items())}, None if listed else "nothing listed", now)
    detail = {"requests": used, "months_wanted": len(have) + len(todo), "months_left": max(0, len(todo) - used)}
    market.job("replay_listings", now, error=problem, detail=detail)
    return {"ok": problem is None, **detail, **({"error": problem} if problem else {})}


def describe(market: StocksMarket, paths: DeskPaths) -> str:
    """One line on the real option prices the replay has, for ``kestrel options-history``."""
    items = wanted(paths)
    if not items:
        return "Replay option prices: no replay has traded options yet; the next historical replay will."
    p = progress(market, items)
    text = (f"Replay option prices: {p['wanted'] - p['left']} of {p['wanted']} traded contracts downloaded from Massive "
            f"({p['with_prices']} had real trades)")
    minutes = p["minutes_left"]
    return text + (f"; about {minutes} minute{'' if minutes == 1 else 's'} of downloading left." if p["left"]
                   else "; all downloaded.")


__all__ = ["PER_PASS", "check", "describe", "download", "listing_months", "listings", "listings_task", "progress", "task",
           "wanted"]
