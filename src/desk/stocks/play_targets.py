"""Expiry profit-target probabilities, distinct from ITM and from touching a profit target before expiry.

Option payoffs are piecewise linear in the stock price. Integrate the favorable intervals under the already
recorded KESTREL forecast: the chain's own distribution reshaped to KESTREL's volatility and drift where the expiry's
smile could be fitted (``chain_odds``), otherwise the lognormal. These are the model's own numbers: each recorded
play's targets are graded at expiry against its real return (``plays.grade_events``) and, once 30 from 10 sessions
have resolved in a group, the chance shown is calibrated on them (``plays.calibrated``). They are not path
predictions, nor a claim that an option can be sold at its theoretical maximum.
"""

from __future__ import annotations

import math
from datetime import date
from itertools import pairwise
from typing import TYPE_CHECKING, Any

from desk.stocks.odds import above
from desk.stocks.paper.fills import OPTION_FEE_PER_CONTRACT

if TYPE_CHECKING:
    from desk.stocks.chain_odds import Distribution


def chance(play: dict[str, Any], target: float, sigma: float, drift: float, years: float,
           dist: Distribution | None = None) -> float:
    """The chance the play's return at expiry reaches ``target`` (of the maximum loss): under ``dist`` (a terminal
    distribution of the stock) when given, else the lognormal at ``sigma`` and ``drift``."""
    s0 = play["underlying_price"]

    def beyond(level: float) -> float:
        return float(dist.above(s0, level)) if dist is not None else above(s0, level, years, sigma, drift)

    legs = play["legs"]
    cost = play["entry"] * 100 * (-1 if play["action"] == "BUY" else 1)
    cost -= OPTION_FEE_PER_CONTRACT * len(legs)

    def profit(stock: float) -> float:
        return float(cost + 100 * sum((1 if leg["side"] == "buy" else -1) *
            max(0.0, stock - leg["strike"] if leg["kind"] == "call" else leg["strike"] - stock) for leg in legs)
        )

    wanted = target * play["max_loss"]
    knots = [0.0, *sorted({float(leg["strike"]) for leg in legs}), math.inf]
    result = 0.0
    for low, high in pairwise(knots):
        middle = (low + high) / 2 if math.isfinite(high) else low + max(1.0, low)
        slope = 100 * sum((1 if leg["side"] == "buy" else -1) *
            ((1 if middle > leg["strike"] else 0) if leg["kind"] == "call" else
             (-1 if middle < leg["strike"] else 0)) for leg in legs)
        intercept = profit(middle) - slope * middle
        if abs(slope) < 1e-9:
            if intercept + 1e-9 < wanted:
                continue
        else:
            crossing = (wanted - intercept) / slope
            if slope > 0:
                low = max(low, crossing)
            else:
                high = min(high, crossing)
        if low >= high:
            continue
        result += beyond(low) - (beyond(high) if math.isfinite(high) else 0.0)
    return max(0.0, min(1.0, result))


MAX_GAIN_NOTES = {
    "bull_put_credit": "Maximum expiry gain occurs when the spread expires worthless.",
    "bear_call_credit": "Maximum expiry gain occurs when the spread expires worthless.",
    "bull_call_debit": "Maximum expiry gain occurs when the stock finishes beyond the sold strike.",
    "bear_put_debit": "Maximum expiry gain occurs when the stock finishes beyond the sold strike.",
    "iron_condor": "Maximum expiry gain occurs when the stock finishes between the two sold strikes.",
    "iron_butterfly": "Maximum expiry gain needs the stock to finish exactly at the sold strike; profit is partial "
                      "anywhere between the break-evens.",
    "cash_secured_put": "Maximum expiry gain (the premium) occurs when the stock finishes above the strike.",
}


def build(play: dict[str, Any], sigma: float, drift: float, years: float,
          dist: Distribution | None = None) -> dict[str, Any]:
    targets = {str(int(target * 100)): round(chance(play, target, sigma, drift, years, dist), 4)
               for target in (0.0, 0.25, 0.5, 1.0)}
    structure = play["structure"]
    capped = (play["action"] == "SELL" or structure.endswith("debit")) and bool(play.get("max_gain"))
    maximum = chance(play, play["max_gain"] / play["max_loss"], sigma, drift, years, dist) if capped else None
    return {"version": 1, "horizon": "At expiry", "returns": targets,
            "max_gain_probability": None if maximum is None else round(maximum, 4),
            "max_gain_note": MAX_GAIN_NOTES.get(structure) or (
                "The theoretical maximum requires the underlying to reach $0; no useful maximum-gain probability "
                "is estimated." if structure == "long_put" else
                "The theoretical maximum gain is unlimited; use a practical profit target instead."),
            "note": "Model estimates of profit at expiration after opening fees, not the chance of hitting a "
                    "sale target earlier. Each recorded play's targets are graded at expiry against its real return; "
                    "once 30 from 10 separate sessions have resolved, the chance shown is calibrated on them."}


def computed_later(play: dict[str, Any], identified: date) -> dict[str, Any] | None:
    """Targets for a saved idea that predates them, computed when it is read: from the idea's own saved legs, entry
    and stock price, at its stored KESTREL forecast (else its stored one-standard-deviation move to expiry, with no
    drift), over the time it had left when identified. Labelled as computed later; None when the idea kept neither."""
    if play.get("probability_targets") or not play.get("legs") or not play.get("underlying_price") \
            or not play.get("max_loss") or play.get("entry") is None:
        return None
    dte = int(play.get("dte") or (date.fromisoformat(play["expiration"]) - identified).days)
    years = max(dte, 0.25) / 365
    forecast = play.get("forecast") or {}
    if forecast.get("sigma"):
        sigma, drift, basis = float(forecast["sigma"]), float(forecast.get("drift") or 0.0), "its saved KESTREL forecast"
    elif play.get("expected_move"):
        sigma, drift = float(play["expected_move"]) / math.sqrt(years), 0.0
        basis = "its saved one-standard-deviation move, with no drift"
    else:
        return None
    out = build(play, sigma, drift, years)
    return {**out, "computed": "later", "basis": basis,
            "note": f"Computed when this saved idea was opened, from {basis}; the idea predates these targets, so "
                    f"they were not shown when it was picked. {out['note']}"}
