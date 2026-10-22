"""The new research rank never depends on social/committee scores or stale/wide option quotes."""
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

from desk.core.config import load_config
from desk.stocks.activity import candidates, chain_activity, market_activity, options_activity, quote_session
from desk.stocks.contracts import OptionQuote
from tests.stocks_world import make_world


def test_quote_session_and_quality_gates() -> None:
    close = datetime(2026, 10, 21, 20, tzinfo=UTC)
    overnight = close + timedelta(hours=10)
    assert quote_session(overnight) == date(2026, 10, 21)
    q = OptionQuote("SPY261120C00500000", "SPY", "call", 500, date(2026, 11, 20), 4, 4.2, 4.1, 1000, 200, None, close)
    assert chain_activity([q, q], overnight)["volume"] == 1000  # no duplicate OCC inflation
    assert chain_activity([replace(q, quoted_at=close-timedelta(hours=1))], overnight)["volume"] == 0
    assert chain_activity([replace(q, ask=8)], overnight)["volume"] == 0
    assert chain_activity([replace(q, quoted_at=None)], overnight)["volume"] == 0


def test_rank_independent_of_social_and_options_sort_not_limited_to_first_25(tmp_path) -> None:
    from desk.stocks.analysis import Analyzer
    from desk.stocks.prices import load_panel
    w = make_world(tmp_path, n=60)
    try:
        day = Analyzer(w.market, w.research, load_config(w.paths.config_dir).settings.stocks).build(
            load_panel(w.market, w.days[0], w.days[-1]), w.days[-1])
        for i in range(35):
            s = f"T{i}"
            day.market_price[s] = {"close": 100, "rel_volume": 1+i, "ret_1d": .01,
                                   "vol_20d": .2, "rs_63d": .1}
            day.ctx.options[s] = {"day": day.asof.isoformat(), "activity": {
                "session": day.asof.isoformat(), "liquid_contracts": 10, "volume": 1000,
                "open_interest": 1000, "premium_proxy": 10000+i, "median_spread": .05,
                "volume_oi": 1}}
        before = market_activity(day)
        day.ctx.crowd.clear()
        day.committee.clear()
        assert market_activity(day) == before
        assert options_activity(day)[0]["symbol"] == "T34"
        day.ctx.options["T34"]["day"] = "2020-01-01"
        assert not any(r["symbol"] == "T34" for r in candidates(day)[0])
    finally:
        w.close()


def test_etf_profile_uses_prices_without_company_committee(tmp_path) -> None:
    from desk.stocks.hub import adjusted_bars, etf_profile
    w = make_world(tmp_path, n=10)
    try:
        rows = [dict(r) for r in w.dbs.market.execute(
            "SELECT day,open,high,low,close,volume FROM stk_bars WHERE symbol='SPY' ORDER BY day DESC LIMIT 400")]
        rows.reverse()
        profile = etf_profile(w.dbs.market, "SPY", adjusted_bars(w.dbs.market, "SPY", rows), "S&P ETF")
        assert profile["price"]["close"] > 0
        assert profile["committee"] is None and profile["fundamentals"] == {}
        assert set(profile["desks"]) == {"momentum", "risk", "options"}
        assert w.research.latest("SPY") is None  # On-demand view never writes scoring records.
    finally:
        w.close()


async def test_option_provider_failure_is_not_reported_as_success_and_other_symbols_continue(tmp_path) -> None:
    from types import SimpleNamespace

    from desk.core.errors import DeskError
    from desk.stocks.collect import option_chains
    from tests.stocks_world import NOW
    w = make_world(tmp_path, n=10)
    class Provider:
        async def expirations(self, symbol):
            if symbol == "BAD":
                raise DeskError("Synthetic provider denial")
            return [date(2026, 11, 20)]

        async def chain(self, symbol, expiration):
            return [OptionQuote("GOOD261120C00100000", symbol, "call", 100, expiration,
                                4, 4.2, 4.1, 1000, 200, None, NOW)]
    suite = SimpleNamespace(tradier=Provider(), option_source="synthetic")
    try:
        failed = await option_chains(suite, w.market, NOW, ["BAD"], rate=.04,
                                     last_prices={}, clock=lambda: NOW)
        assert not failed["ok"]
        assert w.market.jobs()["options"]["last_ok_at"] is None
        mixed = await option_chains(suite, w.market, NOW, ["BAD", "GOOD"], rate=.04,
                                    last_prices={"GOOD": 100}, clock=lambda: NOW)
        assert mixed["ok"] and mixed["partial"] and mixed["symbols"] == 1
        assert mixed["errors"] == {"BAD": "Synthetic provider denial"}
        assert w.market.option_daily("GOOD")
    finally:
        w.close()
