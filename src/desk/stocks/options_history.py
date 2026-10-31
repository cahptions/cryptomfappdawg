"""Real historical option prices from Massive's free Options Basic plan (2 years of daily option bars, 5 requests a
minute).

``check`` is a small read-only probe (two requests: one past SPY expiry's calls near the money, then one of those
contracts' daily bars). ``download`` runs with each collection pass: it fetches the real daily prices of the contracts
the latest historical replay traded, a few at a time, so the next replay uses them in place of modeled prices."""

from __future__ import annotations

import json
import math
from datetime import date, datetime, timedelta
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


def wanted(paths: DeskPaths) -> list[dict[str, str]]:
    """The contracts the latest replay traded inside Massive's window (empty without a replay)."""
    try:
        value = json.loads((paths.home / REPORT).read_text())
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


async def download(massive: Any, market: StocksMarket, paths: DeskPaths, now: datetime) -> dict[str, Any]:
    """Fetch up to ``PER_PASS`` contracts' real daily prices; a contract still held at the replay's end is asked
    again once its replay window reaches past what was fetched."""
    items = wanted(paths)
    fetched = market.option_bar_fetches()
    todo = [w for w in items if fetched.get(w["occ"], date.min) < date.fromisoformat(w["last"])]
    if not todo:
        return {"ok": True, **progress(market, items)}
    done, problem = 0, None
    for w in todo[:PER_PASS]:
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


__all__ = ["PER_PASS", "check", "describe", "download", "progress", "wanted"]
