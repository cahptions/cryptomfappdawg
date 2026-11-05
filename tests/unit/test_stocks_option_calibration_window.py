"""The replay's option model is calibrated on an expanding window: no session is priced with what options did later."""

from datetime import date

import pytest

from desk.core.config import load_config
from desk.stocks import option_model as om
from desk.stocks import options as opt
from desk.stocks.backtest import historical_panel, replay
from desk.stocks.prices import load_panel
from tests.conftest import REPO
from tests.stocks_world import make_world

R = 0.04


def test_a_fit_reads_only_observations_dated_before_its_day(tmp_path):
    w = make_world(tmp_path, n=12)
    try:
        market, last = w.market, w.days[-1]
        p = historical_panel(load_panel(market, w.days[0], last, adjust=False), last, market.splits())
        b = next(s for s in p.symbols if s not in ("SPY", "CHEAP", "SQZ", "S10", "S11", "S12"))
        rows = []
        for d in w.days[-16:]:                                               # Massive's trades: IV 2x realized
            t = p.day_index(d)
            s, rv = float(p.close[p.index[b], t]), om.realized(p.close[p.index[b], :t + 1])
            e, k = om.monthly_expiries(d, 120)[-1], round(s, 2)
            rows.append((om.occ(b, e, "call", k), d.toordinal(), opt.bs_price("call", s, k, opt.year_fraction(d, e), 2 * rv, R)))
        seen = om.observe(market, p, om.RealBars(rows), R)
        everything, early = seen.fit(), seen.fit(w.days[-8])
        assert everything.observations["massive_prices"] == 16 and early.observations["massive_prices"] == 8
        assert early.ratios[b] == pytest.approx(2.0, rel=0.01)
        summaries = sum(1 for sym, d, _ in market.option_daily_all() if d < w.days[-8] and sym in p.index)
        assert early.observations["collected_days"] == summaries < everything.observations["collected_days"]
        before = seen.fit(w.days[-40])                                       # nothing observed yet: stated defaults
        assert not before.ratios and before.market_ratio == om.DEFAULT_RATIO and before.skew == om.DEFAULT_SKEW
        assert om.calibrate(market, p, om.RealBars(rows), R) == everything
    finally:
        w.close()


def test_each_replayed_month_is_priced_from_observations_before_it(tmp_path, monkeypatch):
    w = make_world(tmp_path, n=12)
    used: list[tuple[date, om.Calibration]] = []
    original = om.ModeledOptions.session

    def session(self, asof, panel, rate):
        used.append((asof, self.calibration))
        return original(self, asof, panel, rate)

    monkeypatch.setattr(om.ModeledOptions, "session", session)
    try:
        dated = [d for _, d, _ in w.market.option_daily_all()]
        result = replay(w.paths, load_config(REPO / "config").settings.stocks, 63)
        assert used and all(c.observations["collected_days"] <= sum(1 for d in dated if d < asof) for asof, c in used)
        first_asof, first = used[0]
        assert first_asof < min(dated) and first.market_ratio == om.DEFAULT_RATIO and not first.ratios
        assert used[-1][1].observations["collected_days"] > 0
        months = len({(d.year, d.month) for d, _ in used})
        window = result["option_pricing"]["calibration_window"]
        assert window.startswith(f"Refit at the start of each replayed month ({months} fits)")
        assert any("refit at the start of each replayed month" in x for x in result["limitations"])
    finally:
        w.close()
