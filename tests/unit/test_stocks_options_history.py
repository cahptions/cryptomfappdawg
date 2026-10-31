"""`kestrel options-history`: does the Massive key read historical option prices? Read-only, two requests."""

import asyncio
import json
from datetime import UTC, date, datetime

from desk.core.errors import ProviderError, ProviderErrorKind
from desk.stocks.options_history import check
from desk.stocks.sources.massive import parse_option_bars, parse_option_contracts
from tests.stocks_world import make_world

TODAY = date(2026, 10, 30)


def test_documented_contract_and_bar_shapes_parse():
    contracts, cursor = parse_option_contracts(json.dumps({"results": [
        {"ticker": "O:SPY251121C00580000", "underlying_ticker": "SPY", "contract_type": "call", "strike_price": 580,
         "expiration_date": "2025-11-21", "shares_per_contract": 100},
        {"ticker": "O:SPY251121C00580001", "underlying_ticker": "SPY", "contract_type": "call", "strike_price": 580,
         "expiration_date": "2025-11-21", "shares_per_contract": 10}],                       # adjusted: skipped
        "next_url": "https://api.massive.com/v3/reference/options/contracts?cursor=abc"}).encode())
    assert contracts == [{"ticker": "SPY251121C00580000", "underlying": "SPY", "kind": "call", "strike": 580.0,
                          "expiration": date(2025, 11, 21)}] and cursor == "abc"
    stamp = int(datetime(2025, 10, 30, 4, tzinfo=UTC).timestamp() * 1000)          # midnight New York, that day
    bars = parse_option_bars(json.dumps({"results": [{"o": 12, "h": 13, "l": 11.5, "c": 12.4, "v": 900,
                                                      "t": stamp}]}).encode())
    assert bars == [{"day": date(2025, 10, 30), "open": 12.0, "high": 13.0, "low": 11.5, "close": 12.4,
                     "volume": 900.0}]


class Massive:
    configured = True

    def __init__(self, status=None):
        self.status, self.calls = status, []

    async def option_contracts(self, underlying, as_of, first, last, *, kind=None, strikes=None):
        self.calls.append(("contracts", underlying, as_of, first, last, kind, strikes))
        if self.status:
            raise ProviderError("massive", ProviderErrorKind.HTTP_ERROR, f"option_contracts: HTTP {self.status}",
                                self.status)
        expiry = date(2025, 11, 28)
        return [{"ticker": f"SPY251128C00{k}000", "underlying": "SPY", "kind": "call", "strike": float(k),
                 "expiration": expiry} for k in (590, 600, 610)]

    async def option_bars(self, ticker, first, last):
        self.calls.append(("bars", ticker, first, last))
        return [{"day": first, "close": 9.5}, {"day": last, "close": 3.25}]


def test_a_key_with_options_basic_reads_a_past_contract_with_two_requests(tmp_path):
    world = make_world(tmp_path, n=12)
    try:
        massive = Massive()
        result = asyncio.run(check(massive, world.market, TODAY))
        assert result["ok"] and result["bars"] == 2
        assert "Yes: your Massive key reads historical option prices" in result["text"]
        assert [c[0] for c in massive.calls] == ["contracts", "bars"]
        kind, as_of = massive.calls[0][5], massive.calls[0][2]
        assert kind == "call" and (TODAY - as_of).days >= 365                  # a year back, a trading day
    finally:
        world.close()


def test_a_stocks_only_key_is_told_what_to_add(tmp_path):
    world = make_world(tmp_path, n=12)
    try:
        result = asyncio.run(check(Massive(status=403), world.market, TODAY))
        assert not result["ok"] and "add the free Options Basic plan" in result["text"]
        nokey = type("NoKey", (), {"configured": False})()
        assert "No Massive key" in asyncio.run(check(nokey, world.market, TODAY))["text"]
    finally:
        world.close()


def test_the_request_lists_contracts_trading_on_the_past_day_and_retries_without_strikes(tmp_path):
    from desk.stocks.sources.massive import MassiveClient

    sent = []

    class Http:
        has_key = True

    client = MassiveClient(Http())

    async def paged(endpoint, parse, params):
        sent.append(params)
        return []

    client._paged = paged
    asyncio.run(client.option_contracts("SPY", date(2025, 10, 31), date(2025, 11, 27), date(2025, 12, 3),
                                        kind="call", strikes=(650.0, 700.0)))
    assert "expired" not in sent[0]                     # contracts still trading that day, not already expired
    assert sent[0]["as_of"] == "2025-10-31" and sent[0]["strike_price.gte"] == 650.0

    class Empty(Massive):
        async def option_contracts(self, underlying, as_of, first, last, *, kind=None, strikes=None):
            self.calls.append(("contracts", strikes))
            return []

    world = make_world(tmp_path, n=12)
    try:
        close = world.market.conn.execute("SELECT close FROM stk_bars WHERE symbol='SPY' AND day<=? ORDER BY day "
                                          "DESC LIMIT 1", (date(2025, 10, 30).toordinal(),)).fetchone()[0]
        massive = Empty()
        result = asyncio.run(check(massive, world.market, TODAY))
        assert [c[1] is None for c in massive.calls] == [False, True]          # then without the strike filter
        assert not result["ok"] and f"stored SPY close ${close:,.2f}" in result["text"]
    finally:
        world.close()
