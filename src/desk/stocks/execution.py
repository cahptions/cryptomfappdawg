"""Read-only quote evidence for a whole option structure. A quote is an observation, never a broker fill."""
from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from desk.core.clock import to_iso
from desk.stocks import calendar as cal
from desk.stocks.paper.fills import CONTRACT, OPTION_FEE_PER_CONTRACT
from desk.stocks.repo import StocksMarket

MAX_AGE = timedelta(seconds=60)
MAX_SKEW = timedelta(seconds=15)


@dataclass(frozen=True)
class Quote:
    occ: str
    at: datetime | None
    bid: float | None
    ask: float | None
    bid_size: int | None
    ask_size: int | None

    @classmethod
    def of(cls, q: Any) -> Quote:
        return cls(q.occ, q.quoted_at, q.bid, q.ask, q.bid_size, q.ask_size)


def evidence(legs: Sequence[tuple[str, int]], quotes: Mapping[str, Quote], received: datetime,
             cutoff: datetime, *, units: int = 1, max_debit: float | None = None) -> dict[str, Any]:
    """Check one receipt. Prices are signed US dollars per unit, before fees.

    All legs must have fresh provider timestamps and complete size at the side traded. The caller supplies
    quotes from a single snapshot/request. A failed or missing leg carries no evidence of a joint crossing.
    """
    status: dict[str, Any] = {"status": "unknown", "units": units, "cutoff": to_iso(cutoff),
                             "received_at": to_iso(received), "legs": []}
    if not legs or units < 1 or received > cutoff:
        return {**status, "why": "No complete quote receipt at the cutoff."}
    debit, times, amounts = 0.0, [], []
    for occ, qty in legs:
        q = quotes.get(occ)
        if (q is None or q.at is None or qty == 0 or q.bid is None or q.ask is None
                or not math.isfinite(q.bid) or not math.isfinite(q.ask) or not 0 <= q.bid <= q.ask or q.ask <= 0
                or not timedelta(0) <= received - q.at <= MAX_AGE or not timedelta(0) <= cutoff - q.at <= MAX_AGE):
            return {**status, "why": "A leg has missing or stale prices or a missing provider timestamp."}
        size = q.ask_size if qty > 0 else q.bid_size
        price = q.ask if qty > 0 else q.bid
        debit += qty * price * CONTRACT
        times.append(q.at)
        if size is not None:
            amounts.append(max(0, size) // abs(qty))
        status["legs"].append({"occ": occ, "quantity": qty, "bid": q.bid, "ask": q.ask, "size": size,
                               "at": to_iso(q.at)})
    status.update(quoted_at=to_iso(min(times)), net_debit=round(debit, 2))
    if max(times) - min(times) > MAX_SKEW:
        return {**status, "why": "The legs were quoted at different times."}
    if len(amounts) < len(legs):
        return {**status, "why": "Displayed size is missing on at least one leg."}
    capacity = min(amounts)
    status["capacity"] = capacity
    if capacity < units or (max_debit is not None and debit > max_debit + 1e-6):
        return {**status, "status": "unsupported", "why": "This receipt does not support the requested price and size."}
    return {**status, "status": "supported", "why": "A synchronized quote supports this price and size."}


def positions(legs: Sequence[dict[str, Any]]) -> list[tuple[str, int]]:
    return [(str(x["occ"]), 1 if x["side"] == "buy" else -1) for x in legs]


def idea_check(market: StocksMarket, play: dict[str, Any], cutoff: datetime,
               accounts: Sequence[dict[str, Any]]) -> dict[str, Any]:
    legs = play.get("legs") or []
    risk = float(play["max_loss"])
    cost = float(play["cost"]) if play["action"] == "BUY" else risk
    if not 2 <= len(legs) <= 4 or len({leg["expiration"] for leg in legs}) != 1:
        return {"status": "unknown", "why": "This check needs a two- to four-leg position with one expiry."}
    receipts = {leg.get("chain_received_at") for leg in legs}
    if len(receipts) == 1 and None not in receipts:
        received = next(iter(receipts))
        quotes = {
            leg["occ"]: Quote(leg["occ"], datetime.fromisoformat(leg["quoted_at"]) if leg.get("quoted_at") else None,
                              leg["bid"], leg["ask"], leg.get("bid_size"), leg.get("ask_size"))
            for leg in legs
        }
    else:
        day = cal.new_york(cutoff).date()
        chains = market.chains_on(day, [play["symbol"]], observed_by=cutoff)
        chain = chains.get(play["symbol"], {})
        expiry = date.fromisoformat(legs[0]["expiration"])
        received = chain.get("receipt_times", {}).get(expiry)
        quotes = {q.occ: Quote.of(q) for q in chain.get("expirations", {}).get(expiry, [])}
    direction = 1 if play["action"] == "BUY" else -1
    base = evidence(positions(legs), quotes, datetime.fromisoformat(received), cutoff,
                    max_debit=direction * float(play["entry"]) * CONTRACT) if received else {
                        "status": "unknown", "why": "No retained snapshot for this idea's cutoff.", "cutoff": to_iso(cutoff)}
    target_checks = []
    for account in accounts:
        cash, equity = float(account["cash"]), float(account["equity"])
        target = min(max(0., cash), .25 * max(0., equity))
        units = max(1, math.floor(target / cost)) if cost > 0 and cash >= cost and not account["pending"] else 0
        supported = (min(units, base["capacity"]) if base["status"] == "supported"
                     else 0 if base["status"] == "unsupported" else None)
        target_checks.append({**account, "target": round(target, 2), "cash_units": units, "quoted_units": supported,
                              "over_target": units == 1 and cost > target})
    return {**base, "cost_or_collateral": round(cost, 2), "max_loss": round(risk, 2),
            "fees_per_unit": round(len(legs) * OPTION_FEE_PER_CONTRACT, 2), "accounts": target_checks,
            "basis": "Saved idea price and current PAPER cash. Displayed size is an upper bound."}
