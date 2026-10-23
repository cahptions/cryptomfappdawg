"""Read-only multi-leg scenario analytics. Exact common-expiry payoff; explicitly modeled earlier marks.

No strategy selection, order entry, probability-of-profit claim, or mutation of PAPER occurs here.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from desk.core.errors import DeskError
from desk.stocks.contracts import OptionQuote
from desk.stocks.options import bs_price, greeks, implied_vol


class LegInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    occ: str = Field(min_length=16, max_length=32, pattern=r"^[A-Z0-9.]{1,12}[0-9]{6}[CP][0-9]{8}$")
    action: Literal["buy", "sell"] = "buy"
    quantity: int = Field(default=1, ge=1, le=10000, strict=True)
    entry_price: float | None = Field(default=None, ge=0, le=1000000, allow_inf_nan=False)


class ScenarioInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    legs: list[LegInput] = Field(min_length=1, max_length=8)
    valuation_date: date
    underlying_price: float = Field(gt=0, le=10000000, allow_inf_nan=False)
    iv_change: float = Field(default=0, ge=-.99, le=3, allow_inf_nan=False)  # additive volatility, .01 = one point
    rate: float = Field(default=.04, ge=-.05, le=.5, allow_inf_nan=False)
    dividend_yield: float = Field(default=0, ge=0, le=.5, allow_inf_nan=False)
    fee_per_contract: float = Field(default=.65, ge=0, le=100, allow_inf_nan=False)


@dataclass(frozen=True)
class ScenarioLeg:
    kind: str
    strike: float
    expiration: date
    quantity: int  # signed
    premium: float  # per share; total includes 100-share multiplier
    iv: float | None


def intrinsic(kind: str, strike: float, spot: float) -> float:
    return max(0., spot - strike) if kind == "call" else max(0., strike - spot)


def entry_cost(legs: Sequence[ScenarioLeg], fee: float) -> float:
    return sum(leg.quantity * leg.premium * 100 + abs(leg.quantity) * fee for leg in legs)


def expiry_payoff(legs: Sequence[ScenarioLeg], spot: float, fee: float) -> float:
    return sum(leg.quantity * intrinsic(leg.kind, leg.strike, spot) * 100 - abs(leg.quantity) * fee for leg in legs) - entry_cost(
        legs, fee)


def expiry_risk(legs: Sequence[ScenarioLeg], fee: float) -> dict[str, Any]:
    if len({leg.expiration for leg in legs}) != 1:
        return {"max_profit": None, "max_loss": None, "unlimited_profit": False, "unlimited_loss": False,
                "breakevens": [], "exact": False,
                "message": "Mixed expirations: maximum risk and break-evens need an execution/assignment plan. "
                "The chart assumes the same scenario price at each earlier expiry; it is not a path simulation."}
    knots = sorted({0., *(leg.strike for leg in legs)})
    values = [expiry_payoff(legs, s, fee) for s in knots]
    slope = sum(leg.quantity * 100 for leg in legs if leg.kind == "call")
    roots = [s for s, v in zip(knots, values, strict=True) if abs(v) < 1e-8]
    for i in range(1, len(knots)):
        a, b = values[i - 1], values[i]
        if a * b < 0:
            roots.append(knots[i - 1] - a * (knots[i] - knots[i - 1]) / (b - a))
    if slope and (root := knots[-1] - values[-1] / slope) > knots[-1]:
        roots.append(root)
    return {"max_profit": None if slope > 0 else max(0., max(values)),
            "max_loss": None if slope < 0 else max(0., -min(values)),
            "unlimited_profit": slope > 0, "unlimited_loss": slope < 0, "exact": True,
            "breakevens": sorted({round(r, 6) for r in roots}),
            "message": "Expiration payoff after the selected entry and closing fees. Standard 100-share contracts only."}


def mark(legs: Sequence[ScenarioLeg], spot: float, day: date, fee: float, rate: float, dividend: float,
         iv_change: float) -> float | None:
    result = -entry_cost(legs, fee)
    for leg in legs:
        t = max(0., (leg.expiration - day).days / 365.)
        if not t:
            value = intrinsic(leg.kind, leg.strike, spot)
        elif leg.iv is None:
            return None
        elif spot == 0:
            value = leg.strike * math.exp(-rate * t) if leg.kind == "put" else 0.
        else:
            value = bs_price(leg.kind, spot, leg.strike, t, max(.005, leg.iv + iv_change), rate, dividend)
        result += leg.quantity * value * 100 - abs(leg.quantity) * fee
    return result


def calculate(inputs: ScenarioInput, quotes: dict[str, OptionQuote], spot: float, captured: date,
              today: date) -> dict[str, Any]:
    if not math.isfinite(spot) or spot <= 0:
        raise DeskError("A saved underlying price is needed to infer volatility")
    legs = []
    info = []
    for item in inputs.legs:
        quote = quotes.get(item.occ)
        if quote is None:
            raise DeskError("A selected contract is no longer in the saved snapshot. Reopen its chain.")
        premium = item.entry_price if item.entry_price is not None else (quote.ask if item.action == "buy" else quote.bid)
        if premium is None or (item.entry_price is None and (quote.mid is None or premium <= 0)):
            raise DeskError("No usable entry quote for this leg. Choose another strike or enter an assumed premium.")
        t = max(.25, (quote.expiration - captured).days) / 365
        iv = implied_vol(quote.kind, quote.mid, spot, quote.strike, t, inputs.rate,
                         inputs.dividend_yield) if quote.mid is not None else None
        leg = ScenarioLeg(quote.kind, quote.strike, quote.expiration, item.quantity * (1 if item.action == "buy" else -1),
                          premium, iv)
        legs.append(leg)
        info.append({"occ": quote.occ, "premium": premium, "iv": iv, "custom_price": item.entry_price is not None,
                     "quoted_at": quote.quoted_at.isoformat() if quote.quoted_at else None})
    first, last = min(leg.expiration for leg in legs), max(leg.expiration for leg in legs)
    if not captured <= inputs.valuation_date <= last:
        raise DeskError("Choose a scenario date between the saved snapshot and the last expiration")
    strikes = [leg.strike for leg in legs]
    lo, hi = 0., max(spot, inputs.underlying_price, *strikes) * 2
    xs = sorted({*(lo + (hi - lo) * i / 120 for i in range(121)), *strikes, inputs.underlying_price, spot})
    common = first == last
    terminal = [expiry_payoff(legs, s, inputs.fee_per_contract) if common else None for s in xs]
    curve = [{"price": s, "scenario": mark(legs, s, inputs.valuation_date, inputs.fee_per_contract, inputs.rate,
                                          inputs.dividend_yield, inputs.iv_change), "expiration": terminal[i]}
             for i, s in enumerate(xs)]
    combined = {"delta": 0., "gamma": 0., "theta_per_day": 0., "vega_per_point": 0.}
    known = True
    for leg in legs:
        t = (leg.expiration - inputs.valuation_date).days / 365
        if t <= 0:
            known = False  # expiry kink has no single differentiable Greek
        elif leg.iv is None:
            known = False
        else:
            g = greeks(leg.kind, inputs.underlying_price, leg.strike, t, max(.005, leg.iv + inputs.iv_change),
                       inputs.rate, inputs.dividend_yield)
            for key in combined:
                combined[key] += getattr(g, key) * leg.quantity * 100
    return {"entry_cost": entry_cost(legs, inputs.fee_per_contract), "risk": expiry_risk(legs, inputs.fee_per_contract),
            "scenario_pl": mark(legs, inputs.underlying_price, inputs.valuation_date, inputs.fee_per_contract,
                                inputs.rate, inputs.dividend_yield, inputs.iv_change),
            "greeks": combined if known else None, "curve": curve, "legs": info,
            "captured": captured.isoformat(), "archived": captured < today, "first_expiration": first.isoformat(),
            "last_expiration": last.isoformat(), "model": "European Black-Scholes-Merton approximation before expiry. "
            "American early exercise, discrete dividends, assignment, borrow, changing spreads and fill slippage are "
            "not modeled. IV is inferred from the saved midpoint; it is not a forecast. No orders are submitted."}
