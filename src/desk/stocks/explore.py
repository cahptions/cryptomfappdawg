"""Bounded, read-only trader screens and contract research. Never orders, forecasts or model adoption."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Any

from desk.stocks.activity import qualified_quotes, quote_session
from desk.stocks.contracts import OptionQuote
from desk.stocks.hub import option_table
from desk.stocks.paper.fills import OPTION_FEE_PER_CONTRACT


def option_setups(conn: sqlite3.Connection, now: datetime) -> dict[str, Any]:
    session = quote_session(now)
    selected = conn.execute("SELECT underlying, MAX(observed_at) observed FROM stk_option_chains WHERE day=? "
                            "GROUP BY underlying ORDER BY observed DESC LIMIT 25", (session.toordinal(),)).fetchall()
    rows = []
    for record in selected:
        symbol = record["underlying"]
        expirations = [r[0] for r in conn.execute(
            "SELECT expiration FROM stk_option_chains WHERE underlying=? AND day=? AND expiration>=? AND expiration<=?",
            (symbol, session.toordinal(), session.toordinal()+14, session.toordinal()+60))]
        if not expirations:
            continue
        from datetime import date

        expiry = date.fromordinal(min(expirations, key=lambda e: abs(e - session.toordinal() - 30)))
        view = option_table(conn, symbol, expiration=expiry, now=now)
        price = view.get("underlying_price")
        if not price or price <= 0:
            continue
        quotes = [OptionQuote(q["occ"], symbol, q["kind"], q["strike"], expiry, q["bid"], q["ask"], q["last"],
                              q["volume"], q["open_interest"], None,
                              datetime.fromisoformat(q["quoted_at"]) if q["quoted_at"] else None)
                  for q in view["contracts"]]
        valid, _ = qualified_quotes(quotes, now)
        for kind in ("call", "put"):
            eligible = [q for q in valid if q.kind == kind and q.open_interest >= 100
                        and .85 <= q.strike / price <= 1.15 and q.mid and (q.ask or 0)-(q.bid or 0) <= q.mid * .15
                        and (q.ask or 0) < (price if kind == "call" else q.strike)]
            if not eligible:
                continue
            quote = min(eligible, key=lambda q: (abs(q.strike / price - 1), (q.ask or 0) - (q.bid or 0), -q.open_interest))
            ask = float(quote.ask or 0)
            cost = ask * 100 + OPTION_FEE_PER_CONTRACT
            rows.append({"symbol": symbol, "occ": quote.occ, "kind": kind, "strike": quote.strike,
                         "expiration": expiry.isoformat(), "bid": quote.bid, "ask": quote.ask,
                         "entry_cost": round(cost, 2), "max_premium_loss": round(cost, 2),
                         "breakeven": quote.strike + cost/100 if kind == "call" else quote.strike-cost/100,
                         "spread_fraction": (ask-float(quote.bid or 0))/float(quote.mid or 1),
                         "open_interest": quote.open_interest, "volume": quote.volume,
                         "quoted_at": quote.quoted_at.isoformat() if quote.quoted_at else None,
                         "source": view["source"], "asof": session.isoformat(),
                         "reason": "Near-the-money standard contract; screened for quoted spread and open interest. "
                                   "Reported daily volume is shown separately and may be zero after hours."})
    rows.sort(key=lambda r: (r["spread_fraction"], -r["open_interest"], -r["volume"], r["occ"]))
    balanced = [r for kind in ("call", "put") for r in [row for row in rows if row["kind"] == kind][:10]]
    balanced.sort(key=lambda r: (r["spread_fraction"], -r["open_interest"], -r["volume"], r["occ"]))
    return {"rows": balanced, "sampled_underlyings": len(selected), "asof": session.isoformat(),
            "calls": len([r for r in balanced if r["kind"] == "call"]),
            "puts": len([r for r in balanced if r["kind"] == "put"]),
            "basis": "Research shortlist, ranked by spread then open interest and volume; not expected profit. "
                     "One long contract at the saved ask plus $0.65 entry-fee estimate. "
                     "Expiry break-even excludes exit/exercise costs. No order is created."}


def price_volume(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Price/volume activity remains usable without social inputs or an options connection."""
    records = conn.execute("SELECT symbol,detail_json FROM stk_scores_latest "
                           "WHERE json_extract(detail_json,'$.price.rel_volume') IS NOT NULL "
                           "ORDER BY json_extract(detail_json,'$.price.rel_volume') DESC LIMIT 25").fetchall()
    rows = []
    for record in records:
        detail = json.loads(record["detail_json"])
        price = detail.get("price", {})
        rows.append({"symbol": record["symbol"], "price": price.get("close"), "value": price["rel_volume"],
                     "reason": f"Volume {price['rel_volume']:.2f}× its recent norm; "
                               f"daily price change {price['ret_1d']:+.1%}." if price.get("ret_1d") is not None
                               else f"Volume {price['rel_volume']:.2f}× its recent norm.",
                     "asof": detail.get("asof")})
    return rows


