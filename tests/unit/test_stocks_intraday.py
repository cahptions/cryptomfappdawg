"""Real minute timestamps, latest-session selection and chart-only refresh isolation. Synthetic data only."""

import json
import zlib
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from desk.core.config import load_config
from desk.core.errors import DeskError, SchemaError
from desk.stocks.contracts import Bar, IntradayBar, StockQuote
from desk.stocks.sources.schwab import SchwabClient, parse_intraday
from desk.stocks.stock_views import read, refresh, request, save_history, save_intraday, save_quote
from desk.stocks.web import detail
from tests.conftest import REPO
from tests.stocks_world import NOW, make_world


def candle(at="2026-10-21T13:30:00+00:00", **overrides):
    return {"datetime": int(datetime.fromisoformat(at).timestamp() * 1000),
            "open": 100, "high": 102, "low": 99, "close": 101, "volume": 1000, **overrides}


def parsed(symbol="SPY"):
    return parse_intraday(json.dumps({"symbol": symbol, "candles": [
        candle("2026-10-20T13:30:00+00:00"), candle(), candle("2026-10-21T13:32:00+00:00", volume=0),
    ]}).encode(), symbol)


def test_minute_parser_preserves_distinct_times_and_handles_daylight_saving():
    rows = parse_intraday(json.dumps({"symbol": "SPY", "candles": [
        candle("2026-03-09T13:30:00+00:00"), candle("2026-03-06T14:30:00+00:00"),
    ]}).encode(), "SPY")
    from desk.stocks.calendar import new_york

    assert [new_york(b.at).strftime("%H:%M") for b in rows] == ["09:30", "09:30"]
    assert rows[0].at < rows[1].at and rows[0].close == 101


@pytest.mark.parametrize("candles", [
    [candle(), candle()], [candle(high=98)], [candle(volume=-1)], [candle(open=True)],
    [candle(close=float("nan"))], [candle(datetime=None)], [candle("2026-10-21T13:30:15+00:00")],
])
def test_malformed_minute_response_is_rejected_as_a_whole(candles):
    with pytest.raises(SchemaError, match="minute candles"):
        parse_intraday(json.dumps({"symbol": "SPY", "candles": candles}).encode(), "SPY")


async def test_client_requests_one_minute_regular_session_data_with_weekend_lookback():
    client = SchwabClient(SimpleNamespace(), SimpleNamespace(headers=None, configured=True))
    client._get = AsyncMock(return_value=json.dumps({"symbol": "SPY", "candles": [candle()]}).encode())
    assert (await client.intraday_history("SPY"))[0].at == datetime(2026, 10, 21, 13, 30, tzinfo=UTC)
    assert client._get.call_args.args == ("pricehistory", {"symbol": "SPY", "periodType": "day", "period": 5,
        "frequencyType": "minute", "frequency": 1, "needExtendedHoursData": "false", "needPreviousClose": "false"})


def test_latest_session_is_timestamped_preserves_gaps_and_never_becomes_a_bulk_day_or_paper_input(tmp_path):
    world = make_world(tmp_path, n=12)
    try:
        days = world.market.bar_days()
        ledger = list(world.dbs.ledger.iterdump())
        request(world.dbs.market, "SPY", NOW)
        save_history(world.dbs.market, "SPY", [Bar("SPY", date(2026, 10, 20), 98, 100, 97, 99, 10000)], NOW)
        save_intraday(world.dbs.market, "SPY", parsed(), NOW)
        minute = detail(world.paths, "SPY")["stock_data"]["intraday"]
        assert minute["session"] == "2026-10-21" and minute["previous_close"] == 99
        assert len(minute["bars"]) == 2 and minute["bars"][1]["volume"] == 0
        assert minute["bars"][1]["gap_before"] and minute["interval_minutes"] == 1
        assert "13:30:00" in minute["bars"][0]["at"]
        assert world.market.bar_days() == days and list(world.dbs.ledger.iterdump()) == ledger
        old = minute["bars"]
        for bad in ([], [replace(parsed()[0], symbol="QQQ")],
                    [replace(parsed()[0], at=NOW + timedelta(minutes=5))]):
            with pytest.raises(DeskError, match="preserved"):
                save_intraday(world.dbs.market, "SPY", bad, NOW)
        assert read(world.dbs.market, "SPY", NOW)["intraday"]["bars"] == old
        # An unreadable minute cache must not erase daily history or a valid stock quote.
        save_quote(world.dbs.market, StockQuote("SPY", 101, 100, 102, 99, 1000, NOW), NOW)
        world.dbs.market.execute("UPDATE stk_stock_views SET intraday_zlib=? WHERE symbol='SPY'",
                                (zlib.compress(b'{}'),))
        saved = read(world.dbs.market, "SPY", NOW)
        assert saved["bars"] and saved["quote"] and not saved["intraday"]["bars"]
        assert "unreadable" in saved["intraday"]["error"]
    finally:
        world.close()


def test_weekend_and_before_open_keep_latest_real_session_and_correct_previous_close(tmp_path):
    world = make_world(tmp_path, n=12)
    try:
        monday = datetime(2026, 10, 26, 12, tzinfo=UTC)
        friday = datetime(2026, 10, 23, 19, 59, tzinfo=UTC)
        request(world.dbs.market, "SPY", monday)
        save_history(world.dbs.market, "SPY", [Bar("SPY", date(2026, 10, 22), 98, 100, 97, 99, 10000)], monday)
        bars = [IntradayBar("SPY", friday - timedelta(minutes=1), 100, 102, 99, 101, 1000),
                IntradayBar("SPY", friday, 101, 103, 100, 102, 1000)]
        save_intraday(world.dbs.market, "SPY", bars, monday)
        # Monday's quote previous close belongs to Monday, not Friday's intraday session.
        save_quote(world.dbs.market, StockQuote("SPY", 103, 102, 104, 102, 1000, monday), monday)
        minute = read(world.dbs.market, "SPY", monday)["intraday"]
        assert minute["session"] == "2026-10-23" and minute["previous_close"] == 99
        assert minute["last_at"].startswith("2026-10-23")
    finally:
        world.close()


async def test_minute_refresh_is_bounded_paced_and_independent_of_other_source_failures(tmp_path, monkeypatch):
    world = make_world(tmp_path, n=12)
    clock = [NOW]
    client = SimpleNamespace(configured=True, quote_details=AsyncMock(side_effect=DeskError("quote unavailable")),
        price_history=AsyncMock(side_effect=DeskError("daily unavailable")), intraday_history=AsyncMock(side_effect=parsed))
    suite = SimpleNamespace(schwab=client, aclose=AsyncMock())
    monkeypatch.setattr("desk.stocks.stock_views.build_stocks_suite", lambda *_: suite)
    monkeypatch.setattr("desk.stocks.stock_views.utc_now", lambda: clock[0])
    try:
        days = world.market.bar_days()
        for symbol in ("SPY", "QQQ", "CHEAP"):
            request(world.dbs.market, symbol, NOW)
        await refresh(world.paths, load_config(REPO / "config"))
        assert client.intraday_history.await_count == 2
        await refresh(world.paths, load_config(REPO / "config"))
        assert client.intraday_history.await_count == 3  # next bounded pass reaches the remaining symbol
        await refresh(world.paths, load_config(REPO / "config"))
        assert client.intraday_history.await_count == 3
        previous = read(world.dbs.market, "SPY", NOW)["intraday"]["bars"]
        assert previous and read(world.dbs.market, "SPY", NOW)["history_error"]
        clock[0] += timedelta(seconds=31)
        client.intraday_history.side_effect = DeskError("minute temporarily unavailable")
        await refresh(world.paths, load_config(REPO / "config"))
        saved = read(world.dbs.market, "SPY", clock[0])["intraday"]
        assert saved["bars"] == previous and "unavailable" in saved["error"]
        assert world.market.bar_days() == days
    finally:
        world.close()
