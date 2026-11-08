"""One Schwab chain request per stock covering every expiry it needs (a few for names with daily expiries), with the
underlying's price from the same answer, so the same request budget covers far more of the focus list."""

import json
from datetime import UTC, date, datetime, timedelta
from typing import Any

from desk.stocks import collect
from desk.stocks.sources.schwab import CHAIN_STRIKES, parse_chains
from tests.stocks_world import make_world

QUOTE = datetime(2026, 10, 22, 20, tzinfo=UTC)
CAPTURE = datetime(2026, 10, 22, 21, tzinfo=UTC)
TODAY = date(2026, 10, 22)


def body(symbol: str, expirations: list[date], price: float = 100.0) -> bytes:
    maps: dict[str, Any] = {"callExpDateMap": {}, "putExpDateMap": {}}
    for e in expirations:
        for kind in ("call", "put"):
            maps[f"{kind}ExpDateMap"][f"{e}:{(e - TODAY).days}"] = {str(k): [{
                "symbol": f"{symbol:<6}{e:%y%m%d}{kind[0].upper()}{k * 1000:08}", "strikePrice": k, "multiplier": 100,
                "bid": 4, "ask": 4.2, "last": 4.1, "totalVolume": 1000, "openInterest": 200,
                "quoteTimeInLong": int(QUOTE.timestamp() * 1000)}] for k in (98, 100, 102)}
    return json.dumps({"symbol": symbol, "underlyingPrice": price, **maps}).encode()


def test_one_answer_holds_every_wanted_expiry_and_nothing_else() -> None:
    days = [TODAY + timedelta(days=d) for d in (8, 15, 29)]
    got = parse_chains(body("AAA", days), "AAA", {days[0], days[2]})
    assert sorted(got) == [days[0], days[2]] and all(len(v) == 6 for v in got.values())


def test_chosen_expiries_group_into_requests_that_span_few_listed_ones() -> None:
    weekly_monthly = [TODAY + timedelta(days=7 * w) for w in range(1, 9)] + [TODAY + timedelta(days=d)
                                                                            for d in (91, 182, 364)]
    chosen = collect.pick_expirations(weekly_monthly, TODAY)
    assert collect.spans(weekly_monthly, chosen) == [chosen]                   # one request
    daily = [TODAY + timedelta(days=d) for d in range(2, 400)]
    chosen = collect.pick_expirations(daily, TODAY)
    groups = collect.spans(daily, chosen)
    assert 1 < len(groups) <= len(chosen) and sorted(e for g in groups for e in g) == chosen


class RangedSchwab:
    def __init__(self) -> None:
        self.requests: list[str] = []

    async def expirations(self, symbol: str) -> list[date]:
        self.requests.append("expirations")
        return [TODAY + timedelta(days=7 * w) for w in range(1, 9)] + [TODAY + timedelta(days=91)]

    async def chain_range(self, symbol: str, expirations: list[date]) -> tuple[dict[date, Any], float]:
        self.requests.append("chains")
        return parse_chains(body(symbol, expirations), symbol, set(expirations)), 100.0

    async def chain(self, symbol: str, expiration: date) -> Any:
        raise AssertionError("one request a stock, not one an expiry")

    async def quotes(self, symbols: list[str]) -> Any:
        raise AssertionError("the price comes with the chain")


async def test_collection_asks_one_chain_request_a_stock_and_takes_the_price_from_it(tmp_path: Any) -> None:
    from types import SimpleNamespace

    w = make_world(tmp_path, n=12)
    try:
        schwab = RangedSchwab()
        suite = SimpleNamespace(tradier=schwab, option_source="schwab")
        result = await collect.option_chains(suite, w.market, CAPTURE, ["SPY", "CHEAP"], rate=0.04, last_prices={},
                                             clock=lambda: CAPTURE)
        assert result["ok"] and result["symbols"] == 2
        assert schwab.requests == ["expirations", "chains"] * 2
        assert result["coverage"]["SPY"]["status"] == "collected" and CHAIN_STRIKES >= 40
    finally:
        w.close()
