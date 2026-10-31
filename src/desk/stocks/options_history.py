"""Can this computer read historical option prices? A small read-only probe of Massive's free Options Basic plan
(2 years of daily option bars and the contracts listed on any past day, at 5 requests a minute), made before any
historical option replay depends on it. Two requests: one past SPY expiry's calls near the money, then one of those
contracts' daily bars over its life."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from desk.core.errors import DeskError, ProviderError
from desk.stocks import calendar as cal
from desk.stocks.repo import StocksMarket, day_of


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
    try:
        listed = await massive.option_contracts("SPY", as_of, target - timedelta(days=3), target + timedelta(days=3),
                                                kind="call", strikes=(spot * 0.97, spot * 1.03) if spot else None)
        if not listed:
            return {"ok": False, "text": f"Massive answered but listed no SPY calls expiring near {target}."}
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


__all__ = ["check"]
