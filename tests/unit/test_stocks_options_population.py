"""Fault regressions: actual response shape, independent clocks, raw preservation and bounded requests.

All prices, responses and success scenarios are synthetic; no live entitlement/profitability claim.
"""

import json
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

from desk.core.config import load_config
from desk.core.errors import DeskError, ProviderError, ProviderErrorKind
from desk.stocks.activity import chain_activity
from desk.stocks.collect import option_chains, prices
from desk.stocks.contracts import Bar, OptionQuote
from desk.stocks.hub import home, option_table
from desk.stocks.repo import StocksDatabases, StocksMarket
from desk.stocks.runner import research_preview, saved
from desk.stocks.sources.schwab import parse_chain
from tests.conftest import REPO
from tests.stocks_world import make_world

CAPTURE = datetime(2026, 10, 22, 23, tzinfo=UTC)
QUOTE = datetime(2026, 10, 22, 20, tzinfo=UTC)
SESSION = QUOTE.date()


class StandardSchwab:
    async def expirations(self, symbol):
        return [date(2026, 10, 30), date(2026, 11, 20)]

    async def chain(self, symbol, expiration):
        maps = {}
        for kind in ("call", "put"):
            strikes = {}
            for k in (98, 100, 102):
                strikes[str(k)] = [{
                    "symbol": f"{symbol:<6}{expiration:%y%m%d}{kind[0].upper()}{k*1000:08}",
                    "strikePrice": k, "multiplier": 100, "nonStandard": False,
                    "optionDeliverablesList": [{"symbol": symbol, "assetType": "STOCK", "deliverableUnits": 100}],
                    "bid": 4, "ask": 4.2, "last": 4.1, "totalVolume": 1000, "openInterest": 200,
                    "quoteTimeInLong": int(QUOTE.timestamp() * 1000)}]
            maps[f"{kind}ExpDateMap"] = {f"{expiration}:8": strikes}
        return parse_chain(json.dumps({"symbol": symbol, **maps}).encode(), symbol, expiration)

    async def quotes(self, symbols):
        return [SimpleNamespace(last=100)]


async def test_schwab_standard_chains_populate_etfs_and_companies_despite_stale_equity_history(tmp_path):
    w = make_world(tmp_path, n=12)
    ledger, frozen = list(w.dbs.ledger.iterdump()), list(w.dbs.research.iterdump())
    try:
        suite = SimpleNamespace(tradier=StandardSchwab(), option_source="schwab")
        result = await option_chains(suite, w.market, CAPTURE, ["SPY", "QQQ", "CHEAP"],
                                     rate=.04, last_prices={}, clock=lambda: CAPTURE)
        assert result["ok"] and result["contracts"] == 36
        assert all(v["liquid_contracts"] == 12 for v in result["coverage"].values())
        research_preview(w.paths, load_config(REPO / "config"), w.dbs, CAPTURE)
        assert saved(w.paths, "research")["asof"] == "2026-10-21"  # old prices remain honestly dated
        options = saved(w.paths, "options")
        assert options["asof"] == "2026-10-22" and options["coverage"]["quality_pass"] == 3
        assert {r["symbol"] for r in options["rows"]["options_activity"]} == {"SPY", "QQQ", "CHEAP"}
        assert options["rows"]["big_moves"] and options["rows"]["unusual_options"]
        assert not options["rows"]["cheap_iv"]  # compare volatility only with same-session prices
        value = home(w.dbs.market, w.dbs.research, preview=saved(w.paths, "research"), options_view=options)
        assert {"SPY", "QQQ", "CHEAP"} <= value["stocks"].keys()
        for symbol in ("SPY", "QQQ", "CHEAP"):
            table = option_table(w.dbs.market, symbol)
            assert table["contracts"] and table["source"] == "schwab"
            assert {q["kind"] for q in table["contracts"]} == {"call", "put"}
        assert list(w.dbs.ledger.iterdump()) == ledger
        # Research preview is mutable; frozen list/evaluation records must not be added.
        assert not w.dbs.research.execute("SELECT 1 FROM stk_list_days LIMIT 1").fetchone()
        assert frozen != list(w.dbs.research.iterdump())  # only derived latest research was produced
    finally:
        w.close()


async def test_options_publish_without_any_equity_history_and_pause_does_not_place_orders(tmp_path):
    from desk.core.paths import DeskPaths

    paths = DeskPaths(tmp_path / "home", REPO / "config", tmp_path / "none.env")
    dbs = StocksDatabases.open(paths)
    try:
        market = StocksMarket(dbs.market)
        suite = SimpleNamespace(tradier=StandardSchwab(), option_source="schwab")
        await option_chains(suite, market, CAPTURE, ["SPY"], rate=.04, last_prices={}, clock=lambda: CAPTURE)
        ledger = list(dbs.ledger.iterdump())
        result = research_preview(paths, load_config(REPO / "config"), dbs, CAPTURE)
        assert result["waiting"] and saved(paths, "options")["rows"]["options_activity"]
        assert list(dbs.ledger.iterdump()) == ledger
    finally:
        dbs.close()


async def test_empty_chain_fails_without_erasing_saved_data_and_missing_price_keeps_raw_chain(tmp_path):
    w = make_world(tmp_path, n=12)
    try:
        suite = SimpleNamespace(tradier=StandardSchwab(), option_source="schwab")
        await option_chains(suite, w.market, CAPTURE, ["SPY"], rate=.04, last_prices={}, clock=lambda: CAPTURE)
        before = option_table(w.dbs.market, "SPY")

        async def empty(*args):
            return []

        suite.tradier.chain = empty
        result = await option_chains(suite, w.market, CAPTURE, ["SPY"], rate=.04,
                                     last_prices={}, clock=lambda: CAPTURE)
        assert not result["ok"] and "No supported contracts" in result["error"]
        assert result["coverage"]["SPY"]["status"] == "empty_chain"
        assert option_table(w.dbs.market, "SPY") == before

        class PriceFailure(StandardSchwab):
            async def quotes(self, symbols):
                raise DeskError("synthetic quote failure")

        suite.tradier = PriceFailure()
        result = await option_chains(suite, w.market, CAPTURE, ["QQQ"], rate=.04,
                                     last_prices={}, clock=lambda: CAPTURE)
        assert not result["ok"] and option_table(w.dbs.market, "QQQ")["contracts"]
    finally:
        w.close()


def test_stale_wide_unknown_and_one_sided_data_are_visible_but_never_made_qualified(tmp_path):
    w = make_world(tmp_path, n=12)
    try:
        expiry = date(2026, 11, 20)
        quotes = [OptionQuote(f"SPY261120C{k*1000:08}", "SPY", "call", k, expiry,
                              1, 5, 3, 1000, 200, None, QUOTE - timedelta(days=1)) for k in range(80, 150)]
        quotes += [OptionQuote("SPY261120P00100000", "SPY", "put", 100, expiry, 4, 4.2, 4.1, 500, 200, None, QUOTE)]
        w.market.save_option_chain("SPY", SESSION, expiry, CAPTURE, 100, quotes, source="synthetic")
        table = option_table(w.dbs.market, "SPY")
        assert len(table["contracts"]) == 21 and {q["kind"] for q in table["contracts"]} == {"call", "put"}
        activity = chain_activity(quotes, CAPTURE)
        assert activity["liquid_contracts"] == 1 and activity["rejected"]["stale_or_unknown_time"] == 70
        w.dbs.market.execute("UPDATE stk_option_chains SET chain_zlib=? WHERE underlying='SPY'", (b"corrupt",))
        assert option_table(w.dbs.market, "SPY")["status"] == "stored_snapshot_unreadable"
    finally:
        w.close()


def test_option_request_queue_is_bounded_coalesced_and_does_not_change_watchlist(tmp_path):
    w = make_world(tmp_path, n=12)
    try:
        market = w.market
        market.request_options("SPY", CAPTURE)
        market.request_options("SPY", CAPTURE + timedelta(seconds=1))
        assert market.requested_options(CAPTURE) == ["SPY"]
        market.attempted_options(["SPY"], CAPTURE + timedelta(minutes=1))
        market.request_options("SPY", CAPTURE + timedelta(minutes=2))
        assert market.requested_options(CAPTURE + timedelta(minutes=2)) == []
        market.request_options("SPY", CAPTURE + timedelta(minutes=17))
        assert market.requested_options(CAPTURE + timedelta(minutes=17)) == ["SPY"]
        for i in range(150):
            market.request_options(f"T{i}", CAPTURE + timedelta(minutes=18, seconds=i))
        assert len(market.requested_options(CAPTURE + timedelta(hours=1))) == 100
        assert not w.research.watchlist()
    finally:
        w.close()


async def test_recent_massive_403_does_not_starve_older_backfill_or_mark_missing_close_complete(tmp_path):
    from desk.core.paths import DeskPaths

    paths = DeskPaths(tmp_path / "home", REPO / "config", tmp_path / "none.env")
    dbs = StocksDatabases.open(paths)
    try:
        market = StocksMarket(dbs.market)
        calls = []

        async def grouped(day):
            calls.append(day)
            if day == SESSION:
                raise ProviderError("massive", ProviderErrorKind.HTTP_ERROR, "grouped_daily HTTP 403", 403)
            return [Bar("SPY", day, 100, 101, 99, 100, 1000)]

        result = await prices(SimpleNamespace(massive=SimpleNamespace(grouped_daily=grouped)), market, CAPTURE,
                              history_days=10, after_close=timedelta(minutes=75), max_days=3)
        assert result["ok"] and result["deferred_403"] == [SESSION.isoformat()]
        assert len(calls) == 3 and len(market.bar_days()) == 2 and SESSION not in market.checked_days()
    finally:
        dbs.close()


async def test_relative_iv_etfs_need_consecutive_real_closes_and_do_not_need_company_scores(tmp_path):
    from desk.stocks.options_view import build, realized_volatility

    w = make_world(tmp_path, n=12)
    try:
        suite = SimpleNamespace(tradier=StandardSchwab(), option_source="schwab")
        await option_chains(suite, w.market, CAPTURE, ["SPY"], rate=.04, last_prices={}, clock=lambda: CAPTURE)
        assert realized_volatility(w.market, "SPY", SESSION) is None
        last = w.market.bars("SPY")[-1]
        w.market.save_bars(SESSION, [Bar("SPY", SESSION, last.close, last.close * 1.02,
                                      last.close, last.close * 1.01, 1000000)], "synthetic", CAPTURE)
        view = build(w.market, CAPTURE)
        assert view["rows"]["cheap_iv"][0]["symbol"] == "SPY"
        assert w.research.latest("SPY") is None
        w.dbs.market.execute("DELETE FROM stk_bars WHERE symbol='SPY' AND day=?", (w.days[-3].toordinal(),))
        assert realized_volatility(w.market, "SPY", SESSION) is None
        assert build(w.market, CAPTURE)["rows"]["options_activity"]  # independent inputs remain useful
        assert not build(w.market, CAPTURE)["rows"]["cheap_iv"]
    finally:
        w.close()
