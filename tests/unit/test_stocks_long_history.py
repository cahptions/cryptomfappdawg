"""Schwab history is genuinely longer, source-separated, bounded and failure-preserving. Synthetic values only."""

import json
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from desk.core.config import load_config
from desk.core.errors import DeskError, SchemaError
from desk.stocks.contracts import Bar, StockQuote
from desk.stocks.sources.schwab import SchwabClient, parse_history
from desk.stocks.stock_views import read, refresh, request, save_history, save_quote
from desk.stocks.web import detail
from tests.conftest import REPO
from tests.stocks_world import NOW, make_world


def candle(**overrides):
    return {"datetime": int(datetime(2010, 1, 4, 5, tzinfo=UTC).timestamp() * 1000),
            "open": 10, "high": 12, "low": 9, "close": 11, "volume": 1000, **overrides}


def test_daily_history_parser_preserves_real_dates_and_does_not_apply_a_second_split_adjustment():
    rows = parse_history(json.dumps({"symbol": "SPY", "candles": [candle()]}).encode(), "SPY")
    assert rows == [Bar("SPY", date(2010, 1, 4), 10, 12, 9, 11, 1000)]


@pytest.mark.parametrize("rows", [[candle(high=8)], [candle(volume=-1)], [candle(close=float("nan"))],
                                  [candle(), candle()], [candle(datetime=None)], [candle(open=True)]])
def test_bad_daily_candles_are_not_committed_as_partial_history(rows):
    with pytest.raises(SchemaError):
        parse_history(json.dumps({"symbol": "SPY", "candles": rows}).encode(), "SPY")


async def test_client_requests_actual_twenty_year_daily_history_not_a_cosmetic_long_range():
    client = SchwabClient(SimpleNamespace(), SimpleNamespace(headers=None, configured=True))
    client._get = AsyncMock(return_value=json.dumps({"symbol": "SPY", "candles": [candle()]}).encode())
    assert (await client.price_history("SPY"))[0].day.year == 2010
    assert client._get.call_args.args == ("pricehistory", {"symbol": "SPY", "periodType": "year", "period": 20,
        "frequencyType": "daily", "frequency": 1, "needExtendedHoursData": "false", "needPreviousClose": "false"})


def test_chart_cache_does_not_mark_bulk_day_complete_or_mutate_paper_and_retains_data_on_failure(tmp_path):
    w = make_world(tmp_path, n=12)
    try:
        original_days = w.market.bar_days()
        ledger = list(w.dbs.ledger.iterdump())
        request(w.dbs.market, "SPY", NOW)
        bars = [Bar("SPY", date(2010, 1, 4), 10, 12, 9, 11, 1000),
                Bar("SPY", NOW.date(), 100, 120, 90, 110, 10000)]
        save_history(w.dbs.market, "SPY", bars, NOW)
        save_quote(w.dbs.market, StockQuote("SPY", 111, 110, 112, 108, 123, NOW), NOW)
        with pytest.raises(DeskError, match="no daily candles"):
            save_history(w.dbs.market, "SPY", [], NOW)
        stored = read(w.dbs.market, "SPY", NOW)
        assert stored["bars"][0]["close"] == 11 and not stored["quote"]["stale"]
        assert read(w.dbs.market, "SPY", NOW + timedelta(minutes=3))["quote"]["stale"]
        value = detail(w.paths, "SPY")
        assert value["chart_source"] == "Schwab" and value["coverage"]["first"] == "2010-01-04"
        assert value["bars"][0]["close"] == 11 and value["bars"][1]["gap_before"]
        assert w.market.bar_days() == original_days and list(w.dbs.ledger.iterdump()) == ledger
    finally:
        w.close()


async def test_view_collection_is_independent_of_massive_and_failures_preserve_cached_history(tmp_path, monkeypatch):
    w = make_world(tmp_path, n=12)
    client = SimpleNamespace(configured=True,
        quotes=AsyncMock(return_value=[StockQuote("SPY", 111, 110, 112, 108, 123, NOW)]),
        price_history=AsyncMock(return_value=[Bar("SPY", date(2010, 1, 4), 10, 12, 9, 11, 1000)]))
    suite = SimpleNamespace(schwab=client, aclose=AsyncMock())
    monkeypatch.setattr("desk.stocks.stock_views.build_stocks_suite", lambda *args: suite)
    monkeypatch.setattr("desk.stocks.stock_views.utc_now", lambda: NOW)
    try:
        request(w.dbs.market, "SPY", NOW)
        await refresh(w.paths, load_config(REPO / "config"))
        assert read(w.dbs.market, "SPY", NOW)["bars"]
        await refresh(w.paths, load_config(REPO / "config"))
        assert client.price_history.await_count == 1 and client.quotes.await_count == 1
        later = NOW + timedelta(minutes=16)
        monkeypatch.setattr("desk.stocks.stock_views.utc_now", lambda: later)
        request(w.dbs.market, "SPY", later)
        client.price_history.side_effect = DeskError("History temporarily unavailable")
        await refresh(w.paths, load_config(REPO / "config"))
        value = read(w.dbs.market, "SPY", later)
        assert value["bars"][0]["close"] == 11 and "unavailable" in value["history_error"]
    finally:
        w.close()
