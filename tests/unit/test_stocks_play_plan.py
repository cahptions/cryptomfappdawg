"""How far to trust a play, when to leave it, and how much to put on: plain checks, exits in Learning's own units,
stock prices that really reach the target, and bankroll sizing that never breaks its caps."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pytest

from desk.stocks import play_plan as pp
from desk.stocks import plays as pl
from desk.stocks.paper.fills import OPTION_FEE_PER_CONTRACT as FEE
from desk.stocks.play_marks import profit
from tests.unit.test_stocks_play_budgets import AT, chain_table, context

SESSION = AT.date()


def built(category: str, **underlying: Any) -> dict[str, Any]:
    tb = chain_table(40.)
    for k, v in underlying.items():
        setattr(tb.u, k, v)
    [candidate] = pl.SELECTORS[category](tb, context())
    return pp.attach(pl.finish(candidate, context()), category, None, None, 0.04, SESSION)


def test_confidence_is_five_plain_checks_counted() -> None:
    play = {"structure": "bull_call_debit", "profit_kestrel": 0.55, "expected_value": 12.0, "committee": 64.0,
            "liquidity": "A", "spread_pct": 0.03}
    c = pp.confidence(play, {"resolved": 40, "mean_return": 0.08})
    assert c["score"] == 5 and c["label"] == "Very strong" and c["of"] == 5
    assert [x["id"] for x in c["checks"]] == ["odds", "math", "research", "market", "record"]
    weak = pp.confidence({**play, "profit_kestrel": 0.3, "expected_value": -4.0, "committee": 45.0, "liquidity": "C"},
                         {"resolved": 7, "mean_return": 0.5})
    assert weak["score"] == 0 and "Not proven yet: 7 of 20" in weak["checks"][4]["detail"]
    assert "not bullish" in weak["checks"][2]["detail"]
    condor = pp.confidence({**play, "structure": "iron_condor", "committee": 50.0}, None)
    assert condor["checks"][2]["ok"] and condor["score"] == 4                     # neutral stock, no record yet
    vol = pp.confidence({**play, "structure": "long_strangle", "iv30": 0.30, "rv": 0.42}, None)
    assert vol["checks"][2]["ok"] and "42% a year against 30%" in vol["checks"][2]["detail"]


@pytest.mark.parametrize("category,extra", [("debit_spreads", {}), ("iron_condors", {"committee": 50., "iv_rank": 80.}),
                                            ("strangles", {"committee": 50., "iv_rank": 10.}),
                                            ("likely_itm", {})])
def test_exit_targets_are_learnings_returns_and_their_stock_prices_reach_them(category: str,
                                                                              extra: dict[str, Any]) -> None:
    play = built(category, **extra)
    plan = play["exit_plan"]
    assert plan["source"] == "standard" and plan["leave_by"] and plan["take_profit"] and plan["stop"]
    for level in (plan["take_profit"], plan["stop"]):
        # the value at the target gives back exactly that return through the app's own P/L
        pnl, pct = profit(play["action"], play["entry"], play["max_loss"], level["value"], len(play["legs"]),
                          settled=False)
        assert pct == pytest.approx(level["return"], abs=2e-3) and pnl == pytest.approx(level["pnl"], abs=0.1)
        for side, price in level["stock"].items():
            if price is not None:
                assert (price > play["underlying_price"]) == (side == "above")
                worth = pp._held(play["legs"], price, play["dte"] / 365, 0.04)
                assert worth == pytest.approx(level["value"], abs=0.02)
    assert date.fromisoformat(plan["leave_by"]) < date.fromisoformat(play["expiration"])


def test_a_learned_category_rule_replaces_the_standard_one() -> None:
    play = built("debit_spreads")
    learned = {"suggested": True, "test_sessions": 30, "mean_improvement": 0.04,
               "best_in_sample": {"rule": "take profit at +25%, stop at −50%, leave after 5 sessions",
                                  "params": [0.25, 0.5, 5]}}
    plan = pp.exit_plan(play, "debit_spreads", learned, 0.04, SESSION)
    assert plan["source"] == "learned" and plan["take_profit"]["return"] == 0.25 and plan["stop"]["return"] == -0.5
    assert plan["leave_by"] == pl.cal.trading_days(SESSION + timedelta(days=1), date(2100, 1, 1))[4].isoformat()
    assert "30 unseen sessions" in plan["why"]
    assert pp.exit_plan(play, "debit_spreads", {**learned, "suggested": False}, 0.04, SESSION)["source"] == "standard"


def test_sold_plays_reach_their_take_profit_with_time_and_bought_plays_their_stop() -> None:
    condor = built("iron_condors", committee=50., iv_rank=80.)
    assert condor["exit_plan"]["take_profit"]["by_time"] is not None
    strangle = built("strangles", committee=50., iv_rank=10.)
    assert strangle["exit_plan"]["stop"]["by_time"] is not None
    assert strangle["exit_plan"]["take_profit"]["by_time"] is None       # time alone never pays a bought play


def board(*plays: dict[str, Any]) -> dict[str, Any]:
    cats: dict[str, list[dict[str, Any]]] = {}
    for p in plays:
        cats.setdefault(p["cat"], []).append(p)
    return {"asof": "2026-10-28", "categories": [{"id": c, "title": c, "emoji": "•", "plays": ps, "budget_plays": []}
                                                 for c, ps in cats.items()]}


def fake(symbol: str, cat: str, structure: str, risk: float, score: int, ev: float = 5.0) -> dict[str, Any]:
    action = "SELL" if structure in ("bull_put_credit", "iron_condor") else "BUY"
    return {"symbol": symbol, "cat": cat, "structure": structure, "action": action, "max_loss": risk,
            "cost": risk if action == "BUY" else None, "expected_value": ev,
            "legs": [{"occ": f"{symbol}{cat}{structure}", "side": "buy"}],
            "confidence": {"score": score}, "exit_plan": {"take_profit": {"pnl": risk / 2}, "stop": {"pnl": -risk / 2}}}


def test_bankroll_sizing_keeps_every_cap_and_spreads_the_risk() -> None:
    plays = [fake("A", "iron_condors", "iron_condor", 60, 5),
             fake("D", "iron_condors", "iron_condor", 150, 5),                     # worst case above $100: skipped
             fake("C", "debit_spreads", "bull_call_debit", 30, 4), fake("B", "debit_spreads", "bull_call_debit", 40, 4),
             fake("G", "debit_spreads", "bull_call_debit", 45, 4),                 # third of a category: skipped
             fake("A", "debit_spreads", "bull_call_debit", 90, 4),                 # second play on A: skipped
             fake("E", "lottos", "long_call", 20, 1),                              # 1 of 5: never picked
             fake("F", "debit_spreads2", "bear_put_debit", 50, 3)]
    out = pp.bankroll(board(*plays), 1000, "balanced")                             # $100 a play, $350 in all
    picked = {p["play"]["symbol"]: p for p in out["picks"]}
    assert set(picked) == {"A", "C", "B", "F"} and picked["A"]["play"]["structure"] == "iron_condor"
    assert [p["play"]["symbol"] for p in out["picks"]] == ["A", "C", "B", "F"]    # most confident, best value first
    assert all(p["risk"] <= out["per_play_cap"] for p in out["picks"])
    assert out["summary"]["risk"] <= out["risk_cap"] == 350
    assert picked["B"]["quantity"] == 2 and picked["B"]["risk"] == 80              # as many as the per-play cap allows
    bullish = sum(p["risk"] for p in out["picks"] if pp.DIRECTION[p["play"]["structure"]] == "bullish")
    assert bullish <= 0.6 * 350
    assert out["summary"]["at_stop"] == pytest.approx(sum(-p["risk"] / 2 for p in out["picks"]))
    tiny = pp.bankroll(board(*plays), 100, "careful")                              # $5 a play: nothing fits
    assert not tiny["picks"] and "cheapest play with at least 2 of 5 checks risks $30" in tiny["message"]


def test_tracked_status_says_take_profit_stop_time_add_or_hold() -> None:
    play = built("debit_spreads")
    plan = play["exit_plan"]
    tp, sl = plan["take_profit"]["return"], plan["stop"]["return"]

    def at(pct: float, s: float | None = None, today: date = SESSION) -> str:
        point = {"close": {"pct": pct}, "underlying": s or play["underlying_price"]}
        got = pp.status(play, plan, play["entry"], point, [leg["iv"] for leg in play["legs"]], today)
        assert got is not None
        return str(got["state"])

    assert at(tp + 0.01) == "take_profit" and at(sl - 0.01) == "stop"
    assert at(0.0, today=date.fromisoformat(plan["leave_by"])) == "time_exit"
    assert at(0.0) == "hold"
    assert at(0.05, s=play["underlying_price"] * 1.04) == "add"                    # odds up 10+ points, time left
    unknown = {k: v for k, v in play.items() if k != "profit_kestrel"}           # no baseline: never "add"
    point = {"close": {"pct": 0.05}, "underlying": play["underlying_price"] * 1.04}
    assert pp.status(unknown, plan, play["entry"], point, [], SESSION)["state"] == "hold"
    entered = (play["underlying_price"] * 1.04, SESSION)                          # entered after the rise: no gain
    assert pp.status(play, plan, play["entry"], point, [], SESSION, entered)["state"] == "hold"
    assert pp.status(play, plan, play["entry"], None, [], SESSION) is None


def test_every_built_play_carries_its_confidence_and_plan_and_fees_are_counted() -> None:
    play = built("iron_condors", committee=50., iv_rank=80.)
    assert play["confidence"]["of"] == 5 and play["exit_plan"]["take_profit"]["pnl"] > 0
    value = pp.target_value(play, 0.0)                                              # break-even after both fees
    assert (play["entry"] + value) * 100 == pytest.approx(2 * FEE * 4, abs=1e-6)
