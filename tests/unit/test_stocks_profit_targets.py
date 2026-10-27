"""Compare integrated payoff regions with independent analytical single-leg and spread boundaries."""

import pytest

from desk.stocks.odds import above
from desk.stocks.play_targets import build, chance


def play(kind="call", credit=False):
    return {"action": "SELL" if credit else "BUY", "structure": "put_credit" if credit else f"long_{kind}",
            "entry": 2., "max_loss": 301.3 if credit else 200.65, "max_gain": 198.7 if credit else None,
            "underlying_price": 100., "legs": ([{"kind":"put", "side":"sell", "strike":100.},
                                               {"kind":"put", "side":"buy", "strike":95.}] if credit else
                                              [{"kind":kind, "side":"buy", "strike":100.}])}


@pytest.mark.parametrize("kind", ["call", "put"])
def test_profit_and_return_targets_match_closed_form_breakevens(kind):
    p = play(kind)
    for target in [0., .25, .5, 1.]:
        distance = (200.65 + target*200.65)/100
        strike = 100 + (distance if kind == "call" else -distance)
        expected = above(100,strike,.1,.3,.02)
        if kind == "put":
            expected = 1-expected
        assert chance(p,target,.3,.02,.1) == pytest.approx(expected)
    data = build(p,.3,.02,.1)
    assert data["max_gain_probability"] is None
    assert list(data["returns"].values()) == sorted(data["returns"].values(), reverse=True)


def test_credit_spread_finite_maximum_is_distinct_from_profit_and_unreachable_targets_are_zero():
    p = play(credit=True)
    data = build(p,.3,.02,.1)
    assert data["max_gain_probability"] == pytest.approx(above(100,100,.1,.3,.02), abs=1e-4)
    assert data["returns"]["0"] > data["max_gain_probability"]
    assert data["returns"]["100"] == 0


def test_straddle_uses_both_profit_tails_instead_of_call_itm_probability():
    p = play()
    p["structure"] = "long_straddle"
    p["legs"].append({"kind":"put", "side":"buy", "strike":100.})
    p["entry"],p["max_loss"] = 4.,401.3
    expected = 1-above(100,95.987,.1,.3,.02)+above(100,104.013,.1,.3,.02)
    assert chance(p,0,.3,.02,.1) == pytest.approx(expected)
