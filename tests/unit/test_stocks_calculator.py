"""Mathematical payoff boundaries, fee signs, quote absence and mixed-expiry honesty."""

from datetime import UTC, date, datetime, timedelta

import pytest
from pydantic import ValidationError

from desk.core.errors import DeskError
from desk.stocks.calculator import ScenarioInput, ScenarioLeg, calculate, expiry_payoff, expiry_risk
from desk.stocks.contracts import OptionQuote

EXP = date(2026, 11, 20)


def leg(kind="call", strike=100, quantity=1, premium=5, expiration=EXP):
    return ScenarioLeg(kind, strike, expiration, quantity, premium, .3)


@pytest.mark.parametrize("legs,profit,loss,roots,up,down", [
    ([leg()], None, 501.3, [105.013], True, False),
    ([leg("put")], 9498.7, 501.3, [94.987], False, False),
    ([leg(), leg(strike=110, quantity=-1, premium=2)], 697.4, 302.6, [103.026], False, False),
    ([leg(quantity=-1)], 498.7, None, [104.987], False, True),
    ([leg("put", quantity=-1)], 498.7, 9501.3, [95.013], False, False),
    ([leg(), leg("put")], None, 1002.6, [89.974, 110.026], True, False),
    ([leg(), leg(strike=110, quantity=-2, premium=2)], 896.1, None, [101.039, 118.961], False, True),
])
def test_exact_piecewise_extrema_do_not_mistake_a_finite_plot_for_a_risk_bound(legs, profit, loss, roots, up, down):
    risk = expiry_risk(legs, .65)
    assert risk["exact"]
    assert risk["max_profit"] == (None if profit is None else pytest.approx(profit))
    assert risk["max_loss"] == (None if loss is None else pytest.approx(loss))
    assert risk["breakevens"] == pytest.approx(roots)
    assert (risk["unlimited_profit"], risk["unlimited_loss"]) == (up, down)
    for root in risk["breakevens"]:
        assert expiry_payoff(legs, root, .65) == pytest.approx(0, abs=.0001)


def quote(kind="call", strike=100, expiration=EXP, bid=4.8, ask=5):
    occ = f"SPY{expiration:%y%m%d}{kind[0].upper()}{strike*1000:08}"
    return OptionQuote(occ, "SPY", kind, strike, expiration, bid, ask, 4.9, 0, 400, None,
                       datetime(2026, 10, 23, 16, tzinfo=UTC))


def test_scenarios_use_saved_ask_bid_and_iv_without_inventing_execution_or_volume():
    q = quote()
    inputs = ScenarioInput(legs=[{"occ": q.occ}], valuation_date=EXP, underlying_price=110)
    result = calculate(inputs, {q.occ: q}, 100, date(2026, 10, 23), date(2026, 10, 23))
    assert result["entry_cost"] == pytest.approx(500.65)
    assert result["scenario_pl"] == pytest.approx(498.7)
    assert result["legs"][0]["iv"] > 0 and result["legs"][0]["quoted_at"]
    assert result["greeks"] is None and not result["archived"]
    assert all(p["scenario"] == p["expiration"] for p in result["curve"])
    inputs.valuation_date = date(2026, 10, 25)
    future = calculate(inputs, {q.occ: q}, 100, date(2026, 10, 23), date(2026, 10, 24))
    assert future["greeks"]["delta"] > 0 and future["archived"]


def test_mixed_expirations_do_not_claim_a_known_terminal_risk_or_exact_payoff_curve():
    a, b = quote(), quote(expiration=EXP + timedelta(days=28))
    inputs = ScenarioInput(legs=[{"occ": a.occ, "action": "sell"}, {"occ": b.occ}],
                           valuation_date=EXP, underlying_price=110)
    result = calculate(inputs, {a.occ: a, b.occ: b}, 100, date(2026, 10, 23), date(2026, 10, 23))
    assert not result["risk"]["exact"] and result["risk"]["max_loss"] is None
    assert all(p["expiration"] is None for p in result["curve"])
    assert result["scenario_pl"] is not None


def test_missing_iv_or_quotes_cannot_silently_become_a_zero_premium_trade():
    q = quote(bid=None, ask=None)
    inputs = ScenarioInput(legs=[{"occ": q.occ}], valuation_date=EXP, underlying_price=110)
    with pytest.raises(DeskError, match="entry quote"):
        calculate(inputs, {q.occ: q}, 100, date(2026, 10, 23), date(2026, 10, 23))
    inputs.legs[0].entry_price = 5
    result = calculate(inputs, {q.occ: q}, 100, date(2026, 10, 23), date(2026, 10, 23))
    assert result["scenario_pl"] == pytest.approx(498.7)
    inputs.valuation_date = date(2026, 10, 25)
    assert calculate(inputs, {q.occ: q}, 100, date(2026, 10, 23), date(2026, 10, 23))["scenario_pl"] is None
    with pytest.raises(DeskError, match="no longer"):
        calculate(inputs, {}, 100, date(2026, 10, 23), date(2026, 10, 23))


@pytest.mark.parametrize("change", [{"legs": []}, {"legs": [{"occ": quote().occ}] * 9},
                                    {"underlying_price": float("nan")}, {"iv_change": 4},
                                    {"legs": [{"occ": quote().occ, "quantity": -1}]}])
def test_invalid_or_unbounded_inputs_are_rejected(change):
    with pytest.raises(ValidationError):
        ScenarioInput(**{"legs": [{"occ": quote().occ}], "valuation_date": EXP, "underlying_price": 100, **change})


def test_price_time_grid_agrees_with_scenario_and_expiration_payoff_after_fees():
    q = quote()
    inputs = ScenarioInput(legs=[{"occ": q.occ, "quantity": 3}], valuation_date=EXP, underlying_price=107)
    result = calculate(inputs, {q.occ: q}, 100, date(2026, 10, 23), date(2026, 10, 23))
    grid = result["heatmap"]
    i, j = grid["prices"].index(107), grid["dates"].index(EXP.isoformat())
    assert grid["values"][i][j] == result["scenario_pl"] == pytest.approx((700 - 500 - 1.3) * 3)
    assert grid["dates"] == sorted(set(grid["dates"]))
    assert grid["dates"][0] == "2026-10-23" and grid["dates"][-1] == EXP.isoformat()
    assert all(len(row) == len(grid["dates"]) for row in grid["values"])
    assert len(grid["prices"]) <= 12 and len(grid["dates"]) <= 8
    inputs.legs[0].action = "sell"
    sold = calculate(inputs, {q.occ: q}, 100, date(2026, 10, 23), date(2026, 10, 23))
    assert sold["risk"]["unlimited_loss"] and sold["heatmap"]["values"][i][j] < 0
