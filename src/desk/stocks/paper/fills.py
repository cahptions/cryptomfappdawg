"""How a PAPER order fills, and what a position is worth, using only data a real account would have had.

Stocks: an order decided after a close fills at the next session's open, plus slippage (5 bp for names trading at
least $50M a day, 15 bp otherwise); a stop resting below the price fills at the stop, or at the open if the stock
gapped through it. Commissions are zero (Robinhood, Schwab); sales pay the regulatory fees (SEC Section 31 and FINRA
TAF, approximated by their 2025 rates). Fractional shares to 1/10,000 are allowed, as both brokers allow them.

Options: an order decided on a close fills at the next stored chain snapshot, buying at the ask and selling at the
bid, and only if the spread is fair (no more than 35% of the mid) and the ask hasn't run more than 10% (or 5 cents)
past what the decision saw. Each contract pays $0.65 per leg (Schwab's commission; Robinhood's is lower), each way. A position is
marked at the mid; a contract missing from the day's chain is priced with Black-Scholes at its last implied
volatility. At expiration a position settles at intrinsic value on the underlying's close.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

from desk.stocks import options as opt
from desk.stocks.contracts import Bar, OptionQuote

LIQUID_DOLLAR_VOLUME = 50_000_000
SLIPPAGE_LIQUID = 0.0005
SLIPPAGE_OTHER = 0.0015
SEC_FEE_RATE = 27.80 / 1_000_000        # of sale proceeds
FINRA_TAF_PER_SHARE = 0.000166
FINRA_TAF_MAX = 8.30
OPTION_FEE_PER_CONTRACT = 0.65
MAX_SPREAD_OF_MID = 0.35
MAX_ASK_RUN = 0.10
MIN_ASK_RUN = 0.05                       # dollars: a 10% limit on a $0.10 contract is one tick of noise
CONTRACT = 100
SHARE_STEP = 10_000                      # fractional shares to 1/10,000


def slippage(dollar_volume: float | None) -> float:
    return SLIPPAGE_LIQUID if (dollar_volume or 0) >= LIQUID_DOLLAR_VOLUME else SLIPPAGE_OTHER


def shares_for(notional: float, price: float) -> float:
    return math.floor(notional / price * SHARE_STEP) / SHARE_STEP if price > 0 else 0.0


def sale_fees(shares: float, proceeds: float) -> float:
    return round(proceeds * SEC_FEE_RATE + min(shares * FINRA_TAF_PER_SHARE, FINRA_TAF_MAX), 2)


@dataclass(frozen=True)
class StockFill:
    price: float
    shares: float
    cost: float          # what changed hands (positive), before fees
    fees: float
    note: str


def buy_at_open(bar: Bar, notional: float, dollar_volume: float | None) -> StockFill | None:
    price = bar.open * (1 + slippage(dollar_volume))
    shares = shares_for(notional, price)
    if shares <= 0:
        return None
    return StockFill(price, shares, shares * price, 0.0, "next open")


def sell_at(price: float, shares: float, dollar_volume: float | None, note: str) -> StockFill:
    px = price * (1 - slippage(dollar_volume))
    proceeds = shares * px
    return StockFill(px, shares, proceeds, sale_fees(shares, proceeds), note)


def stop_hit(bar: Bar, stop: float | None) -> float | None:
    """The price a resting stop sells at on this bar (the open if it gapped through), or None."""
    if stop is None:
        return None
    if bar.open <= stop:
        return bar.open
    if bar.low <= stop:
        return stop
    return None


# ── options ──────────────────────────────────────────────────────────────────────────────────────
def index_chain(chains: dict[date, list[OptionQuote]]) -> dict[str, OptionQuote]:
    return {q.occ: q for quotes in chains.values() for q in quotes}


def fair_spread(q: OptionQuote) -> bool:
    mid = q.mid
    return mid is not None and q.bid is not None and q.bid > 0 and (q.ask or 0) - q.bid <= MAX_SPREAD_OF_MID * mid


@dataclass(frozen=True)
class OptionFill:
    legs: list[dict[str, Any]]           # each leg with its fill price per share
    debit: float                         # paid (+) or received (-), before fees
    fees: float
    problem: str | None = None
    modeled: bool = False


def limit(seen: float) -> float:
    """The most a buy order decided at ``seen`` will pay: 10% more, or 5 cents for cheap contracts."""
    return seen + max(seen * MAX_ASK_RUN, MIN_ASK_RUN)


def open_legs(legs: Sequence[dict[str, Any]], quotes: dict[str, OptionQuote]) -> OptionFill:
    """Buy long legs at the ask and sell short legs at the bid; refuse an unfair or runaway fill."""
    filled: list[dict[str, Any]] = []
    debit = 0.0
    contracts = 0
    for leg in legs:
        q = quotes.get(leg["occ"])
        if q is None:
            return OptionFill([], 0.0, 0.0, f"{leg['occ']} is not in the chain")
        if not fair_spread(q):
            return OptionFill([], 0.0, 0.0, f"{leg['occ']} spread too wide to fill fairly")
        long = leg["quantity"] > 0
        price = float(q.ask if long else q.bid)            # type: ignore[arg-type]
        seen = leg.get("decision_price")
        if long and seen and price > limit(seen):
            return OptionFill([], 0.0, 0.0, f"{leg['occ']} ask {price:.2f} ran past the {limit(seen):.2f} limit")
        filled.append({**leg, "entry_price": price})
        debit += leg["quantity"] * price * CONTRACT
        contracts += abs(leg["quantity"])
    return OptionFill(filled, debit, round(contracts * OPTION_FEE_PER_CONTRACT, 2))


def close_legs(legs: Sequence[dict[str, Any]], quotes: dict[str, OptionQuote], s: float | None, today: date,
               r: float) -> OptionFill:
    """Sell long legs at the bid and buy back short legs at the ask (model prices when a leg has no quote)."""
    credit = 0.0
    modeled = False
    contracts = 0
    out: list[dict[str, Any]] = []
    for leg in legs:
        q = quotes.get(leg["occ"])
        long = leg["quantity"] > 0
        if q is not None and q.mid is not None:
            assert q.bid is not None and q.ask is not None
            price = float(q.bid if long else q.ask)
        else:
            modeled = True
            model = model_price(leg, s, today, r)
            if model is None:
                return OptionFill([], 0.0, 0.0, f"no price for {leg['occ']}")
            price = model * (0.95 if long else 1.05)
        out.append({**leg, "exit_price": price})
        credit += leg["quantity"] * price * CONTRACT
        contracts += abs(leg["quantity"])
    return OptionFill(out, -credit, round(contracts * OPTION_FEE_PER_CONTRACT, 2), modeled=modeled)


def model_price(leg: dict[str, Any], s: float | None, today: date, r: float) -> float | None:
    if s is None or s <= 0:
        return None
    expiration = date.fromisoformat(leg["expiration"])
    vol = leg.get("last_iv") or leg.get("entry_iv")
    if expiration <= today:
        return intrinsic(leg, s)
    if not vol or not math.isfinite(float(vol)) or float(vol) <= 0:
        return None
    return opt.bs_price(leg["kind"], s, leg["strike"], opt.year_fraction(today, expiration), float(vol), r)


def intrinsic(leg: dict[str, Any], s: float) -> float:
    k = float(leg["strike"])
    return max(0.0, s - k) if leg["kind"] == "call" else max(0.0, k - s)


def mark_legs(legs: Sequence[dict[str, Any]], quotes: dict[str, OptionQuote], s: float | None, today: date,
              r: float) -> tuple[float | None, list[dict[str, Any]], bool]:
    """Position value at the mids (model where a quote is missing); returns (value, legs with marks, modelled?)."""
    value = 0.0
    modelled = False
    out: list[dict[str, Any]] = []
    for leg in legs:
        q = quotes.get(leg["occ"])
        mid = None if q is None else q.mid
        iv = leg.get("last_iv")
        if mid is not None and s:
            t = opt.year_fraction(today, date.fromisoformat(leg["expiration"]))
            iv = opt.implied_vol(leg["kind"], mid, s, leg["strike"], t, r) or iv
        else:
            mid = model_price({**leg, "last_iv": iv}, s, today, r)
            modelled = True
        if mid is None:
            return None, list(legs), True
        out.append({**leg, "mark": mid, "last_iv": iv})
        value += leg["quantity"] * mid * CONTRACT
    return value, out, modelled


def settle(legs: Sequence[dict[str, Any]], s: float) -> float:
    """Cash value at expiration: intrinsic on the underlying's close."""
    return float(sum(int(leg["quantity"]) * intrinsic(leg, s) * CONTRACT for leg in legs))


__all__ = ["CONTRACT", "OPTION_FEE_PER_CONTRACT", "OptionFill", "StockFill", "buy_at_open", "close_legs", "fair_spread",
           "index_chain", "intrinsic", "mark_legs", "model_price", "open_legs", "sale_fees", "sell_at", "settle",
           "shares_for", "slippage", "stop_hit"]
