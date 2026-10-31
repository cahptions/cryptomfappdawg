"""What to do with a play: how far to trust it, when to get out, and how much to put on for a bankroll.

Research guidance for PAPER plays, never an order. Three parts, each in plain terms:

- **Confidence**: five checks a play passes or fails (better-than-even odds, KESTREL's expected value, the research
  agreeing with its direction, an easy market, a category with a winning record). The meter is the count, so it is
  never a disguised probability, and every check says why.
- **Exit plan**: take-profit, stop and time exit in the same units Learning grades (return on the money at risk). A
  category's learned rule replaces the standard rule once it beat holding on unseen sessions. Targets are also given
  as stock prices for a move today, and live status follows a tracked play.
- **Bankroll**: the day's plays chosen and sized so no play can lose more than a set share of the amount and the
  total at risk stays capped, spread across stocks, categories and directions.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import date, timedelta
from typing import Any

import numpy as np

from desk.stocks import calendar as cal
from desk.stocks import options as opt
from desk.stocks.paper.fills import OPTION_FEE_PER_CONTRACT as FEE
from desk.stocks.play_targets import chance

DIRECTION = {"long_call": "bullish", "bull_call_debit": "bullish", "bull_put_credit": "bullish",
             "cash_secured_put": "bullish", "long_put": "bearish", "bear_put_debit": "bearish",
             "bear_call_credit": "bearish", "iron_condor": "neutral", "iron_butterfly": "neutral",
             "long_straddle": "volatile", "long_strangle": "volatile"}
PROVEN = 20                       # resolved plays before a category's record counts
LABELS = ("Very weak", "Very weak", "Weak", "Moderate", "Strong", "Very strong")


# ── confidence ───────────────────────────────────────────────────────────────────────────────────
def _research(play: Mapping[str, Any]) -> tuple[bool, str]:
    direction = DIRECTION.get(play["structure"], "")
    c, iv, rv = play.get("committee"), play.get("iv30"), play.get("rv")
    if direction == "volatile":
        if iv and rv:
            return rv >= iv, f"The stock has moved {rv:.0%} a year against {iv:.0%} priced into its options"
        return False, "No realized-against-implied volatility to compare"
    if c is None:
        return False, "No committee view of this stock"
    if direction == "bullish":
        return c >= 58, f"Committee {c:.0f}: {'a bullish lean' if c >= 58 else 'not bullish'}"
    if direction == "bearish":
        return c < 42, f"Committee {c:.0f}: {'a bearish lean' if c < 42 else 'not bearish'}"
    return 42 <= c < 58, f"Committee {c:.0f}: {'no strong lean either way' if 42 <= c < 58 else 'a strong lean'}"


def confidence(play: Mapping[str, Any], record: Mapping[str, Any] | None) -> dict[str, Any]:
    """Five plain checks; the meter is how many pass."""
    p, ev = play.get("profit_kestrel"), play.get("expected_value")
    agrees, why = _research(play)
    n = int((record or {}).get("resolved") or 0)
    mean = (record or {}).get("mean_return")
    checks = [
        ("odds", "Better than even odds", p is not None and p >= 0.5,
         "No chance of profit computed" if p is None else f"{p:.0%} chance of a profit at expiry"),
        ("math", "KESTREL's math favours it", ev is not None and ev > 0,
         "No expected value computed" if ev is None else
         f"Expects {'+' if ev >= 0 else '−'}${abs(ev):,.0f} per position after fees"),
        ("research", "The research agrees", agrees, why),
        ("market", "Easy to trade", play.get("liquidity") in ("A", "B"),
         f"Liquidity {play.get('liquidity')}: widest leg quoted {float(play.get('spread_pct') or 0):.0%} wide"),
        ("record", "Proven category", n >= PROVEN and mean is not None and mean > 0,
         f"{n} resolved, averaging {mean:+.0%} on the money at risk" if n >= PROVEN and mean is not None else
         f"Not proven yet: {n} of {PROVEN} plays resolved"),
    ]
    score = sum(ok for _, _, ok, _ in checks)
    return {"score": score, "of": len(checks), "label": LABELS[score],
            "checks": [{"id": i, "title": t, "ok": bool(ok), "detail": d} for i, t, ok, d in checks]}


# ── exit plan ────────────────────────────────────────────────────────────────────────────────────
def _standard(play: Mapping[str, Any], category: str) -> tuple[float | None, float | None, int | None, str]:
    """(take profit, stop, days left to leave by, why) as return on the money at risk."""
    s, dte = play["structure"], int(play["dte"])
    late = 21 if dte >= 35 else max(3, dte // 3)
    gain = (play.get("max_gain") or 0.0) / play["max_loss"] if play.get("max_gain") else None
    if category == "lottos":
        return 2.0, None, None, "Lottos: sell when it triples; otherwise let it run, sized so losing it all is fine"
    if category == "leaps":
        return 0.5, 0.3, 60, "LEAPS: bank +50%, cut at −30%, and leave before the last two months of fast decay"
    if s in ("long_call", "long_put"):
        return 1.0, 0.5, late, "Bought options: sell at double, cut at half, and leave before time decay speeds up"
    if s.endswith("debit") and gain:
        return 0.5 * gain, 0.5, late, "Debit spreads: take half the maximum gain, cut at half the debit"
    if s in ("long_straddle", "long_strangle"):
        return 0.5, 0.4, late, "Bought volatility: take +50%, cut at −40%, and leave before the last weeks' decay"
    if s == "iron_butterfly" and gain:
        return 0.25 * gain, 0.5, late, "Iron butterflies: take a quarter of the credit, cut at half the worst case"
    if s == "cash_secured_put" and gain:
        return 0.5 * gain, min(1.0, 2 * gain), 7, ("Cash-secured puts: buy back at half the premium, or when the "
                                                   "loss reaches twice the premium; or keep the shares if assigned")
    if gain:
        return 0.5 * gain, min(1.0, 2 * gain), late, ("Credit trades: buy back at half the credit, or when the loss "
                                                      "reaches twice the credit")
    return None, None, None, "Hold to expiry"


def _held(legs: Sequence[Mapping[str, Any]], s: float, years: float, rate: float) -> float:
    return sum((1 if leg["side"] == "buy" else -1) * opt.bs_price(leg["kind"], s, leg["strike"], max(years, 1e-6),
                                                                   float(leg.get("iv") or 0.3), rate) for leg in legs)


def target_value(play: Mapping[str, Any], r: float) -> float:
    """The position's value per share (as held) at a return ``r`` on the money at risk, round-trip fees included."""
    fees, entry = FEE * len(play["legs"]), float(play["entry"])
    if play["action"] == "BUY":
        return entry + (r * (entry * 100 + fees) + 2 * fees) / 100
    return (r * float(play["max_loss"]) + 2 * fees) / 100 - entry


def stock_levels(play: Mapping[str, Any], value: float, rate: float) -> dict[str, float | None]:
    """Stock prices nearest today's where the position would be worth ``value`` after a move today."""
    s0, years = float(play["underlying_price"]), max(int(play["dte"]), 1) / 365
    grid = s0 * np.exp(np.linspace(math.log(0.4), math.log(1.8), 721))
    gap = np.array([_held(play["legs"], s, years, rate) for s in grid]) - value
    at = int(np.argmin(np.abs(grid - s0)))
    out: dict[str, float | None] = {"above": None, "below": None}
    for side, steps in (("above", range(at, len(grid) - 1)), ("below", range(at, 0, -1))):
        for i in steps:
            j = i + 1 if side == "above" else i - 1
            if gap[i] == 0 or np.sign(gap[i]) != np.sign(gap[j]):
                w = gap[i] / (gap[i] - gap[j]) if gap[i] != gap[j] else 0.0
                out[side] = round(float(grid[i] + w * (grid[j] - grid[i])), 2)
                break
    return out


def _by_time(play: Mapping[str, Any], value: float, rate: float, session: date) -> str | None:
    """The day a level arrives with time alone if the stock stays where it is: a sold play's take-profit as its
    options decay, a bought play's stop as its own do. None when time alone never gets there before expiry."""
    s0, dte = float(play["underlying_price"]), int(play["dte"])
    start = _held(play["legs"], s0, max(dte, 1) / 365, rate) - value
    for d in range(1, dte):
        if np.sign(_held(play["legs"], s0, (dte - d) / 365, rate) - value) != np.sign(start):
            return (session + timedelta(days=d)).isoformat()
    return None


def exit_plan(play: Mapping[str, Any], category: str, learned: Mapping[str, Any] | None, rate: float,
              session: date) -> dict[str, Any]:
    expiry = date.fromisoformat(play["expiration"])
    play = {**play, "dte": play.get("dte") or max(1, (expiry - session).days)}      # older saved plays lack it
    tp, sl, days_left, why = _standard(play, category)
    source, leave = "standard", None
    learned = learned or {}
    best = learned.get("best_in_sample") or {}
    if learned.get("suggested") and best.get("params"):
        tp, sl, held = best["params"]
        source = "learned"
        why = (f"Learned for this category: {best['rule']}, chosen walk-forward over {learned['test_sessions']} unseen "
               f"sessions and better than holding by {learned['mean_improvement']:+.1%} a play")
        days = cal.trading_days(session + timedelta(days=1), expiry)[:held] if held else []
        if days:
            leave = days[-1].isoformat()
    if leave is None and days_left:
        leave = max(session + timedelta(days=1), expiry - timedelta(days=days_left)).isoformat()

    def level(r: float | None) -> dict[str, Any] | None:
        if r is None:
            return None
        value = target_value(play, r)
        pnl = r * float(play["max_loss"])
        return {"return": round(r, 4), "pnl": round(pnl, 2), "value": round(value, 4),
                "stock": stock_levels(play, value, rate), "by_time": _by_time(play, value, rate, session)}

    p = float(play.get("profit_kestrel") or 0.0)
    if category == "lottos":
        add = "Don't add to a lotto: size it so losing all of it is fine."
    else:
        add = (f"Add one more only if KESTREL's chance of a profit from here rises 10 points above the {p:.0%} at "
               f"entry, with at least 21 days left, before the take-profit, and with your total risk still inside "
               "your bankroll limits.")
    return {"source": source, "why": why, "take_profit": level(tp), "stop": level(-sl if sl else None),
            "leave_by": leave, "add": add,
            "note": "Stock prices are for a move today with the quotes' volatility; the dates are when the level "
                    "arrives with time alone if the stock stays where it is. As days pass, bought plays need a bigger "
                    "move and sold plays reach their take-profit with less."}


def status(play: Mapping[str, Any], plan: Mapping[str, Any] | None, price: float, latest: Mapping[str, Any] | None,
           ivs: Sequence[float], today: date, entered: tuple[float, date] | None = None) -> dict[str, Any] | None:
    """Where a tracked play stands against its exit plan: reached a target, past its time, worth adding, or hold.

    "Worth adding" compares the chance of a profit from here with the same chance at your own entry (``entered``:
    the stock price and day you entered at), on the same volatility, so only a real improvement counts."""
    if not plan or not latest or not latest.get("close"):
        return None
    pct = float(latest["close"]["pct"])
    expiry = date.fromisoformat(play["expiration"])
    left = (expiry - today).days
    sigma = float(np.mean(ivs)) if ivs else float(play.get("iv") or 0.3)

    def odds(stock: float, days: int) -> float | None:
        if days <= 0:
            return None
        return chance({**play, "entry": price, "underlying_price": float(stock)}, 0.0, sigma, 0.0, days / 365)

    from_here = odds(latest.get("underlying") or play["underlying_price"], left)
    at_entry = odds(entered[0], (expiry - entered[1]).days) if entered else play.get("profit_kestrel")
    tp, sl = plan.get("take_profit"), plan.get("stop")
    if tp and pct >= tp["return"]:
        state, text = "take_profit", f"Take-profit reached: {pct:+.0%} against a {tp['return']:+.0%} target"
    elif sl and pct <= sl["return"]:
        state, text = "stop", f"Stop reached: {pct:+.0%} against a {sl['return']:+.0%} limit"
    elif plan.get("leave_by") and today >= date.fromisoformat(plan["leave_by"]):
        state, text = "time_exit", f"Time exit: the plan leaves by {plan['leave_by']}"
    elif (from_here is not None and at_entry is not None and from_here >= at_entry + 0.10 and left >= 21
          and pct > -0.25 and "Don't add" not in plan.get("add", "")):
        state, text = "add", (f"Odds improved: {from_here:.0%} chance of a profit from here against {at_entry:.0%} "
                              "at entry. Adding one more fits the plan if your bankroll limits allow")
    else:
        state, text = "hold", "Hold: no exit or add condition is met"
    return {"state": state, "text": text, "return": round(pct, 4), "chance_from_here": None if from_here is None
            else round(from_here, 4), "chance_at_entry": None if at_entry is None else round(float(at_entry), 4),
            "days_left": left}


# ── bankroll ─────────────────────────────────────────────────────────────────────────────────────
# (worst case per play, all plays together) as shares of the amount set aside for options.
LEVELS = {"careful": (0.05, 0.20, "Careful"), "balanced": (0.10, 0.35, "Balanced"), "bold": (0.20, 0.60, "Bold")}
MAX_PICKS = 6


def bankroll(board: Mapping[str, Any], amount: float, level: str = "balanced") -> dict[str, Any]:
    """The day's plays for ``amount``: no play's worst case above the level's share of it, the total at risk capped,
    one play per stock, two per category at most, no direction above 60% of the cap; best confidence first."""
    per_play, total, title = LEVELS[level]
    cap, budget = amount * per_play, amount * total
    pool: dict[str, dict[str, Any]] = {}
    for cat in board.get("categories") or []:
        for p in [*(cat.get("plays") or []), *(cat.get("budget_plays") or [])]:
            key = "|".join(leg["occ"] for leg in p["legs"])
            if key not in pool and p.get("max_loss") and p.get("confidence"):
                pool[key] = {**p, "category": cat["id"], "category_title": f"{cat['emoji']} {cat['title']}"}
    ranked = sorted(pool.values(), key=lambda p: (-p["confidence"]["score"],
                                                  -(p.get("expected_value") or 0) / p["max_loss"], p["symbol"]))
    affordable = [p for p in ranked if p["max_loss"] <= cap]
    strong = [p for p in affordable if p["confidence"]["score"] >= 3]
    chosen = strong if len(strong) >= 3 else [p for p in affordable if p["confidence"]["score"] >= 2]
    picks: list[dict[str, Any]] = []
    symbols: set[str] = set()
    categories: Counter[str] = Counter()
    directions: Counter[str] = Counter()
    left = budget
    for p in chosen:
        direction = DIRECTION.get(p["structure"], "other")
        if p["symbol"] in symbols or categories[p["category"]] >= 2:
            continue
        qty = min(int(cap // p["max_loss"]), int(left // p["max_loss"]),
                  int((0.6 * budget - directions[direction]) // p["max_loss"]))
        if qty < 1:
            continue
        risk = qty * p["max_loss"]
        plan = p.get("exit_plan") or {}
        cash = qty * (p["cost"] if p["action"] == "BUY" else p["max_loss"])
        picks.append({"play": p, "quantity": qty, "risk": round(risk, 2), "cash": round(cash, 2),
                      "share": round(risk / amount, 4),
                      "expected_value": round(qty * (p.get("expected_value") or 0.0), 2),
                      "at_take_profit": None if not plan.get("take_profit") else round(qty * plan["take_profit"]["pnl"], 2),
                      "at_stop": None if not plan.get("stop") else round(qty * plan["stop"]["pnl"], 2)})
        symbols.add(p["symbol"])
        categories[p["category"]] += 1
        directions[direction] += risk
        left -= risk
        if len(picks) >= MAX_PICKS:
            break
    cheapest = min((p["max_loss"] for p in ranked if p["confidence"]["score"] >= 2), default=None)
    message = None
    if not picks:
        message = (f"Nothing qualifies at a {per_play:.0%} worst case per play (${cap:,.0f})."
                   + (f" The cheapest play with at least 2 of 5 checks risks ${cheapest:,.0f}: raise the amount or "
                      "choose a bolder level." if cheapest else " No play passes 2 of the 5 checks today."))
    elif len(strong) < 3:
        message = "Few plays pass 3 of 5 checks within this size today, so 2-of-5 plays are included; read their checks."
    risk = sum(p["risk"] for p in picks)
    return {"amount": amount, "level": level, "level_title": title, "per_play": per_play, "total": total,
            "per_play_cap": round(cap, 2), "risk_cap": round(budget, 2), "picks": picks, "message": message,
            "summary": {"plays": len(picks), "risk": round(risk, 2), "share": round(risk / amount, 4) if amount else 0,
                        "cash": round(sum(p["cash"] for p in picks), 2),
                        "expected_value": round(sum(p["expected_value"] for p in picks), 2),
                        "at_take_profit": round(sum(p["at_take_profit"] or 0 for p in picks), 2),
                        "at_stop": round(sum(p["at_stop"] or 0 for p in picks), 2)},
            "asof": board.get("asof")}


def attach(play: dict[str, Any], category: str, record: Mapping[str, Any] | None, learned: Mapping[str, Any] | None,
           rate: float, session: date) -> dict[str, Any]:
    play["confidence"] = confidence(play, record)
    play["exit_plan"] = exit_plan(play, category, learned, rate, session)
    return play


__all__ = ["DIRECTION", "LEVELS", "attach", "bankroll", "confidence", "exit_plan", "status", "stock_levels",
           "target_value"]
