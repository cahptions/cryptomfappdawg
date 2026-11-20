"""Decision-time evidence and one-unit expiry scenarios for the Plays shortlist."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from desk.core.clock import to_iso
from desk.stocks.paper.fills import CONTRACT, OPTION_FEE_PER_CONTRACT
from desk.stocks.play_marks import profit, settlement
from desk.stocks.plays import breakeven_roots


def comparison(play: dict[str, Any], cutoff: datetime) -> dict[str, Any]:
    """Use the recorded entry and the same intrinsic settlement and fee convention as Plays grading."""
    odds = (play.get("exit_plan") or {}).get("odds") or {}
    legs = play.get("legs") or []
    spot, risk, entry = play.get("underlying_price"), play.get("max_loss"), play.get("entry")
    result = {"symbol": play["symbol"], "type": play.get("type"), "action": play.get("action"),
              "strikes": sorted({leg["strike"] for leg in legs}), "spot": spot, "expiration": play.get("expiration"),
              "entry": entry, "identified_at": to_iso(cutoff), "source": play.get("source"),
              "model_take_profit": odds.get("take_profit"), "prior_outcomes": odds.get("history"),
              "max_loss": risk, "scenario": None}
    if (not legs or len({leg["expiration"] for leg in legs}) != 1 or not spot or spot <= 0
            or risk is None or risk <= 0 or entry is None or play.get("action") not in ("BUY", "SELL")):
        return result
    action = play["action"]
    fee = OPTION_FEE_PER_CONTRACT * len(legs)
    effective_entry = entry + (fee if action == "BUY" else -fee) / CONTRACT
    values = [round(profit(action, entry, risk, settlement(legs, spot * (1 + move / 100)),
                           len(legs), settled=True)[0], 2) for move in range(-20, 21)]
    return {**result, "scenario": {"min_move": -20, "pnl": values, "entry_fees": round(fee, 2),
                                   "breakevens": breakeven_roots(action, effective_entry, legs)}}
