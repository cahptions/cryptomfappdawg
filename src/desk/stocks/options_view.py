"""Mutable options research on its own observation clock; never frozen evidence or PAPER input."""

from __future__ import annotations

import math
import statistics
from datetime import date, datetime
from typing import Any

from desk.stocks import calendar as cal
from desk.stocks.activity import activity_failure, quote_session
from desk.stocks.repo import StocksMarket
from desk.stocks.signals import options

OPTION_LISTS = frozenset({"options_activity", "unusual_options", "cheap_iv", "big_moves"})


def realized_volatility(market: StocksMarket, symbol: str, session: date) -> float | None:
    """Same 20 simple-return population volatility as price features; require every real session."""
    from desk.stocks.hub import adjusted_bars

    rows = [dict(r) for r in market.conn.execute(
        "SELECT day,close FROM stk_bars WHERE symbol=? AND day<=? ORDER BY day DESC LIMIT 21",
        (symbol, session.toordinal()))]
    rows.reverse()
    if (len(rows) != 21 or rows[-1]["day"] != session.toordinal()
            or len(cal.trading_days(date.fromordinal(rows[0]["day"]), session)) != 21):
        return None
    closes = [r["close"] for r in adjusted_bars(market.conn, symbol, rows)]
    vol = statistics.pstdev([closes[i] / closes[i-1] - 1 for i in range(1, 21)]) * math.sqrt(252)
    return vol if vol > 0 and math.isfinite(vol) else None


def build(market: StocksMarket, now: datetime) -> dict[str, Any]:
    expected = quote_session(now)
    latest = market.latest_option_day()
    session = min(latest, expected) if latest else expected
    summaries = {s: o for s, o in options(market, session).items() if o["day"] == session.isoformat()}
    rows: dict[str, list[dict[str, Any]]] = {key: [] for key in OPTION_LISTS}
    coverage: dict[str, Any] = {"same_session_options": len(summaries), "quality_pass": 0, "quote_quality_pass": 0,
                               "sampled_contracts": 0, "liquid_contracts": 0, "excluded": {}, "symbols": {}}
    for symbol, summary in summaries.items():
        activity = summary.get("activity") or {}
        failure = activity_failure(activity, session)
        coverage["sampled_contracts"] += activity.get("sampled_contracts", 0)
        coverage["liquid_contracts"] += activity.get("liquid_contracts", 0)
        coverage["symbols"][symbol] = {"reason": failure, "contracts": activity.get("sampled_contracts", 0),
                                        "liquid_contracts": activity.get("liquid_contracts", 0),
                                        "reported_volume": activity.get("volume"),
                                        "provider_diagnostics": summary.get("provider_diagnostics"),
                                        "rejected": activity.get("rejected", {})}
        if failure:
            excluded = coverage["excluded"]
            excluded[failure] = excluded.get(failure, 0) + 1
        else:
            coverage["quality_pass"] += 1
        usable_quotes = (activity.get("session") == session.isoformat() and activity.get("liquid_contracts", 0) >= 4
                         and activity.get("open_interest", 0) >= 100)
        if not usable_quotes:
            continue
        coverage["quote_quality_pass"] += 1

        def row(value: float, reason: str, s: str = symbol, o: dict[str, Any] = summary,
                a: dict[str, Any] = activity) -> dict[str, Any]:
            return {"symbol": s, "value": value, "reason": reason, "price": o.get("price"),
                    "observed_at": o.get("observed_at"), "options": a}

        if not failure:
            rows["options_activity"].append(row(activity["premium_proxy"],
                f"{activity['volume']:,} sampled contracts; ${activity['premium_proxy']:,.0f} midpoint premium proxy; "
                f"volume/OI {activity['volume_oi']:.2f}; median spread {activity['median_spread']:.1%}. "
                "Not trade direction or opening interest."))
        for unusual in ([] if failure else summary.get("unusual", [])[:1]):
            premium = unusual["volume"] * (unusual.get("mid") or 0) * 100
            rows["unusual_options"].append(row(premium,
                f"{unusual['volume']:,} {unusual['expiration']} ${unusual['strike']:g} {unusual['kind']} contracts; "
                f"volume / {unusual.get('ratio_basis', 'reference')} {unusual['ratio']:.1f}×; "
                f"${premium:,.0f} midpoint premium proxy. Not trade direction."))
        for expiry in summary.get("expiries", []):
            if 2 <= expiry.get("days", 999) <= 10 and expiry.get("expected_move"):
                rows["big_moves"].append(row(expiry["expected_move"],
                    f"ATM straddle prices ±{expiry['expected_move']:.1%} by {expiry['expiration']}; "
                    "an option-price measure, not a prediction."))
                break
        # A relative-IV comparison needs matching-session realized-volatility observations.
        # Missing/stale equities history must not block raw chains, activity or expected moves.
        rv = realized_volatility(market, symbol, session)
        iv = summary.get("iv30")
        if rv and iv:
            rows["cheap_iv"].append(row(iv / rv, f"IV {iv:.1%} vs 20-session realized volatility {rv:.1%}; "
                                                   f"{iv / rv:.2f}×, same-session history."))
    for key, values in rows.items():
        values.sort(key=lambda r: (r["value"] if key == "cheap_iv" else -r["value"], r["symbol"]))
        rows[key] = [{**r, "rank": i} for i, r in enumerate(values[:25], 1)]
    messages = {
        "options_activity": "No underlyings pass the measured-activity gates. If usable quotes exist but the provider "
                            "reports no daily volume, chains, contract screens and price-based option measures remain available.",
        "unusual_options": "Chains were checked; no sampled contract meets the high-volume ratio rule.",
        "cheap_iv": "Relative IV needs usable option quotes and 21 consecutive closing prices through the same session. "
                    "Chains and other option lists update while that history is unavailable.",
        "big_moves": "No qualifying at-the-money call/put pair with 2–10 days to expiration in the collected sample."}
    if not summaries:
        messages = {key: "No chain summary collected for this session. Open a stock to prioritize its chain; "
                          "see Settings for the options connection and System for source errors." for key in OPTION_LISTS}
    return {"asof": session.isoformat() if latest else None, "expected_asof": expected.isoformat(),
            "awaiting_newer_session": latest is None or session < expected, "computed_at": now.isoformat(),
            "coverage": coverage, "rows": rows, "empty_reasons": messages, "version": 2}


def overlay(lists: list[dict[str, Any]], view: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not view:
        return lists
    return [{**item, "asof": view["asof"], "rows": view["rows"].get(item["id"], []), "preview": True,
             "coverage": view["coverage"], "options_clock": True,
             "empty_reason": view.get("empty_reasons", {}).get(item["id"]),
             "awaiting_newer_session": view["awaiting_newer_session"]}
            if item["id"] in OPTION_LISTS else item for item in lists]
