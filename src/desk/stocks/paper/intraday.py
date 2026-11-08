"""Intraday take-profits for PAPER strategies that sell at a target (Lotto picks · sold at triple).

The daily engine checks exits on closing quotes, so a lotto that triples at 11:00 and falls back by 4:00 was never
sold. During the session, every five-minute Plays tracking pass also quotes each held contract of these strategies
through Schwab ``/quotes`` (100 contracts a request; at most 20 are held, so one request) and, when the bid is worth the
strategy's take-profit, sells it there: a decision, an order and its fill at that bid with the usual $0.65 a contract,
recorded with the quote's own time. A quote older than five minutes, without a bid, or before the position's own entry
is never used. PAPER only: nothing is sent to a broker.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from desk.core.errors import DeskError
from desk.stocks import calendar as cal
from desk.stocks.contracts import OptionQuote
from desk.stocks.paper import fills
from desk.stocks.paper.book import PaperBook
from desk.stocks.paper.strategies import BY_STRATEGY

INTRADAY = tuple(s.id for s in BY_STRATEGY.values() if s.p.get("intraday_take_profit"))
FRESH = timedelta(minutes=5)


def _quote(tick: Any) -> OptionQuote:
    occ = tick.occ
    return OptionQuote(occ, occ[:-15], "call" if occ[-9] == "C" else "put", int(occ[-8:]) / 1000,
                       datetime.strptime(occ[-15:-9], "%y%m%d").date(), tick.bid, tick.ask, tick.last,
                       tick.volume or 0, tick.open_interest or 0, None, tick.quoted_at)


async def take_profits(book: PaperBook, schwab: Any, now: datetime) -> dict[str, Any]:
    """Quote every held contract of the intraday strategies once and sell any whose bid reaches its take-profit."""
    session = cal.market_session(now)
    held = [p for sid in INTRADAY for p in book.positions(sid) if p.asset == "option"]
    if session["state"] != "open" or not held or schwab is None or not getattr(schwab, "configured", False):
        return {"held": len(held), "requests": 0, "sold": []}
    occs = sorted({leg["occ"] for p in held for leg in p.legs})[:100]
    try:
        ticks = await schwab.option_quotes(occs)
    except DeskError as exc:
        return {"held": len(held), "requests": 1, "sold": [], "error": str(exc)[:160]}
    quotes = {t.occ: _quote(t) for t in ticks if t.bid and t.ask and t.quoted_at and now - t.quoted_at <= FRESH}
    today = cal.new_york(now).date()
    sold = []
    for p in held:
        st = BY_STRATEGY[p.account]
        legs = [leg for leg in p.legs if leg["occ"] in quotes]
        entered = datetime.fromisoformat(p.opened_at)
        if len(legs) != len(p.legs) or any((quotes[leg["occ"]].quoted_at or entered) <= entered for leg in legs):
            continue
        f = fills.close_legs(p.legs, quotes, None, today, 0.0)
        change = -f.debit / p.entry_cost - 1 if p.entry_cost > 0 else 0.0
        if f.problem or f.modeled or change < st.p["take_profit"]:
            continue
        stamp = max(q.quoted_at for q in (quotes[leg["occ"]] for leg in legs) if q.quoted_at)
        reason = f"take profit intraday ({change:+.0%}) on Schwab's quote at {cal.new_york(stamp):%H:%M}"
        with book.atomic():
            did = book.decide(now, today, st.id, st.spec_sha256, p.symbol, "EXIT", [reason],
                              {"position_id": p.id, "change": change, "quoted_at": stamp.isoformat()})
            order = book.place(did, st.id, now, today, {"asset": "option", "action": "EXIT", "symbol": p.symbol,
                                                         "position_id": p.id, "reason": reason})
            book.close_position(p, at=now, day=today, credit=-f.debit, fees=f.fees, reason=reason,
                                evidence={"legs": f.legs, "order": order, "quoted": True, "intraday": True,
                                          "quoted_at": stamp.isoformat(), "source": "schwab /quotes"})
            book.resolve(order, "FILLED", now, "intraday take-profit on a fresh Schwab quote")
        sold.append({"account": st.id, "symbol": p.symbol, "change": change})
    return {"held": len(held), "requests": 1, "sold": sold}


__all__ = ["INTRADAY", "take_profits"]
