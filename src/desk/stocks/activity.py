"""Descriptive market/options attention, independent of social scores and PAPER strategy decisions."""

from __future__ import annotations

import math
import statistics
from collections.abc import Sequence
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Any

from desk.stocks import calendar as cal
from desk.stocks.contracts import OptionQuote

if TYPE_CHECKING:
    from desk.stocks.analysis import Day

VERSION = 1
WEIGHTS = {"option_premium": .25, "option_volume": .20, "volume_oi": .10, "quote_quality": .10,
           "relative_volume": .15, "standardized_move": .10, "relative_strength": .10}


def quote_session(observed_at: datetime) -> date:
    local = cal.new_york(observed_at)
    if not cal.is_trading_day(local.date()) or (local.hour, local.minute) < (9, 30):
        return cal.last_closed_session(observed_at)
    return local.date()


def chain_activity(quotes: Sequence[OptionQuote], observed_at: datetime) -> dict[str, Any]:
    session = quote_session(observed_at)
    accepted: dict[str, OptionQuote] = {}
    rejected = {"stale_or_unknown_time": 0, "invalid_or_wide_quote": 0, "outside_horizon": 0}
    for q in quotes:
        if not 2 <= (q.expiration - session).days <= 90:
            rejected["outside_horizon"] += 1
            continue
        if (q.quoted_at is None or cal.new_york(q.quoted_at).date() != session
                or q.quoted_at > observed_at + timedelta(seconds=5)
                or min(observed_at, cal.session_close(session)) - q.quoted_at > timedelta(minutes=30)):
            rejected["stale_or_unknown_time"] += 1
            continue
        if (q.mid is None or q.bid is None or q.bid <= 0 or q.ask is None
                or (q.ask - q.bid) / q.mid > .25 or q.volume < 0 or q.open_interest < 0):
            rejected["invalid_or_wide_quote"] += 1
            continue
        accepted[q.occ] = q
    liquid = list(accepted.values())
    volume, oi = sum(q.volume for q in liquid), sum(q.open_interest for q in liquid)
    traded = [q for q in liquid if q.volume > 0]
    spreads = [(q.ask - q.bid) / q.mid for q in traded if q.ask is not None and q.bid is not None and q.mid]
    return {"specification": "market-options-activity-v1", "session": session.isoformat(),
            "observed_at": observed_at.isoformat(), "sampled_contracts": len(quotes), "liquid_contracts": len(liquid),
            "volume": volume, "open_interest": oi, "volume_oi": volume / oi if oi else None,
            "premium_proxy": sum(q.volume * (q.mid or 0) * 100 for q in traded),
            "median_spread": statistics.median(spreads) if spreads else None,
            "call_volume": sum(q.volume for q in liquid if q.kind == "call"),
            "put_volume": sum(q.volume for q in liquid if q.kind == "put"), "rejected": rejected,
            "basis": "Sampled standard contracts only. Volume × quote midpoint × 100 is a premium proxy, not actual "
                     "trade value, opening interest, buying pressure, smart money or a direction forecast."}


def candidates(day: Day) -> tuple[list[dict[str, Any]], dict[str, int]]:
    prices = day.market_price or day.ctx.price
    counts = {"price_universe": len(prices), "same_session_options": 0, "quality_pass": 0}
    rows = []
    for symbol, px in prices.items():
        opt = day.ctx.options.get(symbol, {})
        if opt.get("day") != day.asof.isoformat():
            continue
        counts["same_session_options"] += 1
        a = opt.get("activity") or {}
        if (a.get("session") != day.asof.isoformat() or a.get("liquid_contracts", 0) < 4
                or a.get("volume", 0) < 100 or a.get("open_interest", 0) < 100
                or a.get("premium_proxy", 0) < 10000 or a.get("median_spread") is None):
            continue
        required = [px.get(k) for k in ("rel_volume", "ret_1d", "vol_20d", "rs_63d")]
        if any(v is None or not math.isfinite(v) for v in required) or not px.get("vol_20d"):
            continue
        counts["quality_pass"] += 1
        rows.append({"symbol": symbol, "price": px.get("close"), "options": a,
                     "metrics": {"option_premium": a["premium_proxy"], "option_volume": a["volume"],
                                 "volume_oi": min(a["volume_oi"], 10), "quote_quality": 1 - a["median_spread"],
                                 "relative_volume": px["rel_volume"],
                                 "standardized_move": abs(float(px["ret_1d"] or 0)) /
                                 (float(px["vol_20d"] or 1) / math.sqrt(252)),
                                 "relative_strength": abs(float(px["rs_63d"] or 0))}})
    return rows, counts


def ranked_activity(day: Day) -> list[dict[str, Any]]:
    rows, _ = candidates(day)
    for row in rows:
        components = {}
        for key in WEIGHTS:
            population = [r["metrics"][key] for r in rows]
            value = row["metrics"][key]
            components[key] = 100 * (sum(v < value for v in population) + .5 * sum(v == value for v in population)) / len(rows)
        a = row["options"]
        row.update(value=round(sum(WEIGHTS[k] * v for k, v in components.items()), 4), components=components,
                   reason=f"{a['volume']:,} sampled option contracts; ${a['premium_proxy']:,.0f} midpoint premium proxy; "
                          f"volume/OI {a['volume_oi']:.2f}; median spread {a['median_spread']:.1%}; "
                          f"stock volume {row['metrics']['relative_volume']:.1f}× normal. No social input.")
    rows.sort(key=lambda r: (-r["value"], r["symbol"]))
    return rows


def market_activity(day: Day) -> list[dict[str, Any]]:
    return ranked_activity(day)[:25]


def options_activity(day: Day) -> list[dict[str, Any]]:
    rows = ranked_activity(day)
    rows.sort(key=lambda r: (-r["options"]["premium_proxy"], r["symbol"]))
    return rows[:25]
