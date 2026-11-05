"""The replay's open-interest floor reads Massive's traded volume where it can, and says what it assumes elsewhere."""

import math
from datetime import date, timedelta

import numpy as np

from desk.core.config import load_config
from desk.stocks import calendar as cal
from desk.stocks import option_model as om
from desk.stocks.backtest import replay
from desk.stocks.paper.fills import index_chain
from desk.stocks.paper.strategies import MIN_OPEN_INTEREST, contract
from desk.stocks.prices import Panel
from tests.conftest import REPO
from tests.stocks_world import make_world

DAY = date(2026, 6, 1)


def test_massive_volume_decides_liquidity_only_for_days_it_was_asked_about():
    code = "AAA260717C00100000"
    d = DAY.toordinal()
    quiet = om.RealBars([(code, d - 3, 2.0, 4)], {code: (d - 20, d + 30)})
    assert quiet.liquid(code, DAY) is False                                  # 4 contracts in ten days
    busy = om.RealBars([(code, d - 3, 2.0, 4), (code, d, 2.1, 30)], {code: (d - 20, d + 30)})
    assert busy.liquid(code, DAY) is True
    assert busy.liquid(code, DAY + timedelta(days=40)) is None               # never asked about that day
    assert om.RealBars().liquid(code, DAY) is None
    assert om.RealBars([(code, d, 2.0)]).liquid(code, DAY) is None           # volume-less rows still load


def test_a_contract_massive_saw_untraded_is_quoted_without_open_interest_and_passed_over():
    days = cal.trading_days(DAY - timedelta(days=200), DAY)[-80:]
    closes = np.array([[100 * math.exp(0.015 * (1 if i % 2 else -1)) for i in range(80)]])
    panel = Panel(["AAA"], days, closes, closes, closes, closes, np.ones_like(closes) * 1e6)
    s = float(closes[0, -1])
    e = om.monthly_expiries(DAY)[1]
    atm = om.occ("AAA", e, "call", min(om.strike_grid(s), key=lambda k: abs(k - s)))
    real = om.RealBars([], {atm: (DAY.toordinal() - 15, DAY.toordinal() + 40)})   # asked: no trades at all
    model = om.ModeledOptions(om.Calibration({}, 1.1, 0.1, {}, 0.1), real, {"AAA"})
    model.session(DAY, panel, 0.04)
    decision = index_chain(model.chain("AAA", real=False))
    assert decision[atm].open_interest == 0 < MIN_OPEN_INTEREST
    assert all(q.open_interest == om.OPEN_INTEREST for c, q in decision.items() if c != atm)
    pick = contract(model.chain("AAA", real=False), s, DAY, 0.04, "call", 0.5, 30, 60, expiration=e)
    assert pick is not None and pick.quote.occ != atm
    assert model.liquidity == {"liquid": 0, "illiquid": 1}


def test_replay_discloses_its_optioned_universe_and_liquidity_assumption(tmp_path):
    w = make_world(tmp_path, n=12)
    try:
        result = replay(w.paths, load_config(REPO / "config").settings.stocks, 8)
        notes = " ".join(result["limitations"])
        assert f"trade only the {result['option_pricing']['optioned_stocks']} stocks whose option chains" in notes
        assert "100-contract open-interest floor" in notes and "open interest is not modeled" not in notes
        assert result["option_pricing"]["liquidity_checks"] == {"liquid": 0, "illiquid": 0}
    finally:
        w.close()
