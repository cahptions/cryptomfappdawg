"""Expiry profit-target probabilities, distinct from ITM and from touching a profit target before expiry.

Option payoffs are piecewise linear in the stock price. Integrate the favorable intervals under the already
recorded KESTREL lognormal forecast. These target probabilities are model estimates, not empirically calibrated
target-hit rates, path predictions, or a claim that an option can be sold at its theoretical maximum.
"""

from __future__ import annotations

import math
from itertools import pairwise
from typing import Any

from desk.stocks.odds import above
from desk.stocks.paper.fills import OPTION_FEE_PER_CONTRACT


def chance(play: dict[str, Any], target: float, sigma: float, drift: float, years: float) -> float:
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
        result += (above(play["underlying_price"], low, years, sigma, drift) -
                   (above(play["underlying_price"], high, years, sigma, drift) if math.isfinite(high) else 0.0))
    return max(0.0, min(1.0, result))


def build(play: dict[str, Any], sigma: float, drift: float, years: float) -> dict[str, Any]:
    targets = {str(int(target * 100)): round(chance(play, target, sigma, drift, years), 4)
               for target in (0.0, 0.25, 0.5, 1.0)}
    credit = play["action"] == "SELL" and play["structure"].endswith("credit")
    maximum = (chance(play, play["max_gain"] / play["max_loss"], sigma, drift, years) if credit else None)
    return {"version": 1, "horizon": "At expiry", "returns": targets,
            "max_gain_probability": None if maximum is None else round(maximum, 4),
            "max_gain_note": "Maximum expiry gain occurs when the spread expires worthless." if credit else
                "The theoretical maximum requires the underlying to reach $0; no useful maximum-gain probability "
                "is estimated." if play["structure"] == "long_put" else
                "The theoretical maximum gain is unlimited; use a practical profit target instead.",
            "note": "Model estimates of profit at expiration after opening fees, not the chance of hitting a "
                    "sale target earlier. These target probabilities have not been empirically calibrated."}
