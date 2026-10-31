"""Option prices for the historical replay: listings, the model at its calibrated volatility, real Massive prices in
its place (a quiet day keeps the last trade's volatility), calibration from stored data, the replay using real prices
without being pulled toward them, and the rate-limited download of the contracts a replay traded."""

import asyncio
import json
import math
from datetime import date, timedelta

import numpy as np
import pytest

from desk.core.config import load_config
from desk.core.errors import ProviderError, ProviderErrorKind
from desk.stocks import calendar as cal
from desk.stocks import option_model as om
from desk.stocks import options as opt
from desk.stocks import options_history as oh
from desk.stocks.backtest import historical_panel, replay
from desk.stocks.prices import Panel, load_panel
from tests.conftest import REPO
from tests.stocks_world import NOW, add_chains, make_world

R = 0.04


def panel(closes: list[float], end: date, symbol: str = "AAA") -> Panel:
    days = cal.trading_days(end - timedelta(days=200), end)[-len(closes):]
    arr = np.array([closes], dtype=float)
    return Panel([symbol], days, arr, arr, arr, arr, np.ones_like(arr) * 1e6)


def wavy(n: int = 80, start: float = 100.0, move: float = 0.015) -> list[float]:
    return [start * math.exp(move * (1 if i % 2 else -1) * (i % 3 + 1) / 2) for i in range(n)]


def calibration(**kw):
    base = {"ratios": {"AAA": 1.2}, "market_ratio": 1.1, "skew": 0.1, "spreads": {}, "market_spread": 0.1}
    return om.Calibration(**{**base, **kw})


def test_listings_are_monthly_third_fridays_moved_off_holidays_with_a_listing_grid():
    assert om.monthly_expiries(date(2026, 5, 1), 60) == [date(2026, 5, 15), date(2026, 6, 18)]   # Juneteenth Friday
    assert om.monthly_expiries(date(2025, 4, 1), 20) == [date(2025, 4, 17)]                      # Good Friday
    grid = om.strike_grid(100.0)
    assert grid[0] == 65.0 and grid[-1] == 135.0 and all(k / 2.5 == round(k / 2.5) for k in grid)
    assert om.strike_grid(8.0)[:2] == [5.5, 6.0] and om.strike_step(700) == 10.0
    code = om.occ("SPY", date(2026, 11, 20), "put", 512.5)
    assert code == "SPY261120P00512500" and om.parse_occ(code) == ("SPY", date(2026, 11, 20), "put", 512.5)
    assert om.parse_occ("not an option") is None


def test_a_rebuilt_chain_is_black_scholes_at_the_calibrated_volatility_with_skew_and_spread():
    day = date(2026, 6, 1)
    closes = wavy()
    p = panel(closes, day)
    model = om.ModeledOptions(calibration(), om.RealBars(), {"AAA"})
    model.session(day, p, R)
    atm = om.realized(np.array(closes)) * 1.2
    assert model.atm("AAA") == pytest.approx(atm) and model.summary("AAA") == {"iv30": atm, "modeled": True}
    assert model.summary("ZZZ") is None                                    # never seen with options: not optioned
    chain = model.chain("AAA")
    assert list(chain) == om.monthly_expiries(day)
    s = closes[-1]
    e = list(chain)[1]
    t = opt.year_fraction(day, e)
    for q in chain[e]:
        iv = model.calibration.iv(atm, s, q.strike, t)
        fair = opt.bs_price(q.kind, s, q.strike, t, iv, R)
        assert q.mid == pytest.approx(fair, abs=0.011) and q.quoted_at == cal.session_close(day) and q.last is None
        assert q.ask - q.bid == pytest.approx(max(0.1 * fair, 0.05), abs=0.011)
    assert model.calibration.iv(atm, s, 0.9 * s, t) > atm > model.calibration.iv(atm, s, 1.1 * s, t)   # put skew


def test_real_prices_replace_the_model_and_a_quiet_day_keeps_the_last_trades_volatility():
    days = cal.trading_days(date(2026, 5, 1), date(2026, 6, 30))
    closes = wavy(len(days) + 60)
    p = panel(closes, days[-1])
    e = date(2026, 7, 17)
    code = om.occ("AAA", e, "call", 100.0)
    d1, d2 = days[-15], days[-14]
    s1 = closes[p.day_index(d1)]
    deep_put = om.occ("AAA", e, "put", 150.0)                              # off the grid: listed once traded
    real = om.RealBars([(code, d1.toordinal(), 6.4), (deep_put, days[-3].toordinal(), 3.0)])
    model = om.ModeledOptions(calibration(), real, {"AAA"})
    model.remember("AAA", [{"occ": code, "expiration": e.isoformat(), "strike": 100.0},
                           {"occ": deep_put, "expiration": e.isoformat(), "strike": 150.0}])

    def quote(day):
        model.session(day, p, R)
        return next(q for q in model.chain("AAA")[e] if q.occ == code)

    q = quote(d1)
    assert q.last == 6.4 and q.mid == pytest.approx(6.4, abs=0.011)
    iv = opt.implied_vol("call", 6.4, s1, 100.0, opt.year_fraction(d1, e), R)
    s2 = closes[p.day_index(d2)]
    assert quote(d2).mid == pytest.approx(opt.bs_price("call", s2, 100.0, opt.year_fraction(d2, e), iv, R), abs=0.011)
    later = days[-6]                                                       # more than a week after the trade: model
    got = quote(later)
    s3, t3 = closes[p.day_index(later)], opt.year_fraction(later, e)
    modeled = opt.bs_price("call", s3, 100.0, t3, model.calibration.iv(model.atm("AAA"), s3, 100.0, t3), R)
    assert got.mid == pytest.approx(modeled, abs=0.011)
    model.session(days[-3], p, R)                                          # a stale trade below intrinsic: ignored
    deep = next(q for q in model.chain("AAA")[e] if q.occ == deep_put)
    assert deep.last is None and deep.mid > 40                              # modeled near intrinsic, not the stale $3
    assert model.priced[(code, d1.toordinal())] == "real" and model.priced[(code, d2.toordinal())] == "real_iv"
    assert model.priced[(code, later.toordinal())] == "model"


def test_calibration_learns_each_stocks_ratio_skew_and_spread_from_stored_chains_and_massive(tmp_path):
    world = make_world(tmp_path, n=12)
    try:
        market, last = world.market, world.days[-1]
        p = historical_panel(load_panel(market, world.days[0], last, adjust=False), last, market.splits())
        a, b = [s for s in p.symbols if s != "SPY"][:2]
        market.conn.execute("DELETE FROM stk_option_daily")
        none = om.calibrate(market, p, om.RealBars(), R)                     # nothing stored: stated defaults
        assert (none.market_ratio, none.skew, none.market_spread) == (om.DEFAULT_RATIO, om.DEFAULT_SKEW,
                                                                      om.DEFAULT_SPREAD) and not none.ratios
        for d in world.days[-20:]:                                           # collected chains: IV 1.5x realized
            rv = om.realized(p.close[p.index[a], :p.day_index(d) + 1])
            market.save_option_daily(a, d, NOW, {"iv30": 1.5 * rv, "expiries": [
                {"days": 30, "atm_iv": 0.4, "skew_25d": 0.12 * 0.4 * 1.349 * 100}]})
        rows = []
        for d in world.days[-10:]:                                           # Massive's trades: IV 2x realized
            t = p.day_index(d)
            s, rv = float(p.close[p.index[b], t]), om.realized(p.close[p.index[b], :t + 1])
            e, k = om.monthly_expiries(d, 120)[-1], round(s, 2)
            rows.append((om.occ(b, e, "call", k), d.toordinal(),
                         opt.bs_price("call", s, k, opt.year_fraction(d, e), 2.0 * rv, R)))
        add_chains(world, [a], world.days[-3:], spread=0.06)
        got = om.calibrate(market, p, om.RealBars(rows), R)
        assert got.ratios[a] == pytest.approx(1.5) and got.skew == pytest.approx(0.12)
        assert got.ratios[b] == pytest.approx(2.0, rel=0.01)
        assert got.spreads[a] == pytest.approx(0.06, abs=0.01)
        assert got.observations["collected_days"] == 20 and got.observations["massive_prices"] == 10
    finally:
        world.close()


def test_the_replay_fills_at_massives_real_price_and_is_not_pulled_toward_a_contract_that_has_one(tmp_path):
    world = make_world(tmp_path, n=12)
    settings = load_config(REPO / "config").settings.stocks
    try:
        first = replay(world.paths, settings, 63)
        trade = next(p for p in first["trades"] if p["account"] == "random_calls")
        leg = trade["legs"][0]
        filled = date.fromisoformat(trade["entry_day"])
        decided = cal.previous_trading_day(filled)
        assert any(c["occ"] == leg["occ"] for c in first["option_contracts"])
        symbol, expiration, kind, strike = om.parse_occ(leg["occ"])
        neighbour = om.occ(symbol, expiration, kind, strike + om.strike_step(strike))
        real = round(leg["entry_price"] / 1.04, 2)            # its real closing trade, near what the model asked
        world.market.save_option_bars(leg["occ"], filled, filled, [{"day": filled, "open": real, "high": real,
                                                                    "low": real, "close": real, "volume": 5}], NOW)
        world.market.save_option_bars(neighbour, decided, decided, [{"day": decided, "open": 50, "high": 50, "low": 50,
                                                                     "close": 50.0, "volume": 5}], NOW)
        second = replay(world.paths, settings, 63)
        again = next(p for p in second["trades"] if p["account"] == "random_calls" and p["entry_day"] == trade["entry_day"])
        assert again["legs"][0]["occ"] == leg["occ"]                     # the neighbour's real price changed nothing
        spread = second["option_pricing"]["calibration"]["market_spread"]
        assert again["legs"][0]["entry_price"] == round(real + max(real * spread / 2, om.MIN_HALF_SPREAD), 2)
        account = next(a for a in second["accounts"] if a["id"] == "random_calls")
        assert account["option_prices"]["real"] >= 1 and second["option_pricing"]["with_real_prices"] >= 1
        assert "Massive's real daily closing trades" in " ".join(second["limitations"])
    finally:
        world.close()


class Bars:
    def __init__(self, answers):
        self.answers, self.calls = answers, []

    async def option_bars(self, code, first, last):
        self.calls.append(code)
        answer = self.answers.get(code, [])
        if isinstance(answer, Exception):
            raise answer
        return answer


def http(status):
    return ProviderError("massive", ProviderErrorKind.HTTP_ERROR, f"option_bars: HTTP {status}", status)


def test_download_fetches_each_traded_contract_once_a_few_per_pass_and_stops_on_a_refusal(tmp_path):
    world = make_world(tmp_path, n=12)
    try:
        report = world.paths.home / oh.REPORT

        def want(items):
            report.write_text(json.dumps({"option_contracts": items}))

        assert "no replay has traded options yet" in oh.describe(world.market, world.paths)
        items = [{"occ": f"AAA261120C{50000 + i * 1000:08d}", "first": "2026-10-01", "last": "2026-10-20"}
                 for i in range(oh.PER_PASS + 2)]
        want(items)
        bar = {"day": date(2026, 10, 2), "open": 1.0, "high": 1.2, "low": 0.9, "close": 1.1, "volume": 30.0}
        massive = Bars({items[0]["occ"]: [bar], items[1]["occ"]: http(404)})     # 404: no such contract
        out = asyncio.run(oh.download(massive, world.market, world.paths, NOW))
        assert out["ok"] and out["fetched_now"] == oh.PER_PASS and out["left"] == 2 and out["with_prices"] == 1
        assert om.RealBars.load(world.market.conn).on(items[0]["occ"], date(2026, 10, 2)) == 1.1
        assert "about 1 minute of downloading" in oh.describe(world.market, world.paths)
        asyncio.run(oh.download(massive, world.market, world.paths, NOW))
        assert len(massive.calls) == len(items) and world.market.jobs()["option_bars"]["detail"]["left"] == 0
        asyncio.run(oh.download(massive, world.market, world.paths, NOW))                 # nothing is asked twice
        assert len(massive.calls) == len(items) and "all downloaded" in oh.describe(world.market, world.paths)
        items[0]["last"] = "2026-10-27"                                                    # still held: asked again
        want(items)
        asyncio.run(oh.download(massive, world.market, world.paths, NOW))
        assert massive.calls[-1] == items[0]["occ"] and len(massive.calls) == len(items) + 1
        want([*items, {"occ": "BBB261120P00040000", "first": "2026-10-01", "last": "2026-10-20"}])
        refused = asyncio.run(oh.download(Bars({"BBB261120P00040000": http(403)}), world.market, world.paths, NOW))
        assert not refused["ok"] and "Options Basic" in refused["error"]
        assert "Options Basic" in world.market.jobs()["option_bars"]["last_error"]
    finally:
        world.close()


def test_a_finished_batch_of_real_prices_refreshes_the_automatic_replay_once_not_mid_download(tmp_path):
    from desk.stocks.backtest_job import BacktestWorker

    world = make_world(tmp_path, n=12)
    try:
        worker = BacktestWorker(world.paths)
        assert worker.coverage()["option_prices"] == 0
        items = [{"occ": "AAA261120C00050000", "first": "2026-10-01", "last": "2026-10-20"}]
        (world.paths.home / oh.REPORT).write_text(json.dumps({"option_contracts": items}))
        downloading = worker.coverage()
        assert downloading["option_prices"] == "downloading"
        world.market.save_option_bars(items[0]["occ"], date(2026, 10, 1), date(2026, 10, 20), [], NOW)
        done = worker.coverage()
        assert done["option_prices"] == 1 and worker.input_key(0, done) != worker.input_key(0, downloading)
    finally:
        world.close()
