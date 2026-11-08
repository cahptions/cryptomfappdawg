"""``desk stocks verify-schwab``: one request per Schwab endpoint the app reads, and for each field it reads whether it
came back with the expected type. Only names and types are reported, never a value or a credential."""

import asyncio
import json
from datetime import UTC, datetime
from typing import Any

from desk.stocks.schwab_fields import check, summary, verify

NOW = datetime(2026, 11, 9, 16, 0, tzinfo=UTC)
SECRET_PRICE = 612.34


class FakeSchwab:
    configured = True

    def __init__(self) -> None:
        self.asked: list[tuple[str, str | None]] = []

    async def _get(self, endpoint: str, params: dict[str, Any], path: str | None = None) -> bytes:
        self.asked.append((endpoint, path))
        if endpoint == "quotes" and "C" in params["symbols"]:
            occ = params["symbols"].split(",")[0]
            return json.dumps({occ: {"symbol": occ, "assetMainType": "OPTION",
                                     "quote": {"bidPrice": 1.0, "askPrice": 1.1, "lastPrice": 1.05, "mark": 1.05,
                                               "totalVolume": 10, "openInterest": 100, "volatility": 18.0,
                                               "delta": 0.5, "gamma": 0.01, "theta": -0.1, "vega": 0.2,
                                               "underlyingPrice": SECRET_PRICE, "quoteTime": 1, "bidSize": 3,
                                               "askSize": 4},
                                     "reference": {"strikePrice": 610, "contractType": "C", "multiplier": 100,
                                                   "underlying": "SPY", "expirationYear": 2026,
                                                   "expirationMonth": 12, "expirationDay": 18}}}).encode()
        if endpoint == "quotes":
            return json.dumps({"SPY": {"symbol": "SPY", "quote": {"lastPrice": SECRET_PRICE, "mark": "612"}}}).encode()
        if endpoint == "expirations":
            return json.dumps({"expirationList": [{"expirationDate": "2026-11-10"},
                                                  {"expirationDate": "2026-12-18"}]}).encode()
        if endpoint == "chains":
            row = {"symbol": "SPY   261218C00610000", "strikePrice": 610, "bid": 1, "ask": 1.1, "last": 1,
                   "totalVolume": 3, "openInterest": 9, "quoteTimeInLong": 1, "bidSize": 1, "askSize": 1,
                   "multiplier": 100, "nonStandard": False, "mini": False, "optionDeliverablesList": []}
            return json.dumps({"symbol": "SPY", "isDelayed": False, "underlyingPrice": SECRET_PRICE,
                               "underlying": {"last": SECRET_PRICE},
                               "callExpDateMap": {"2026-12-18:39": {"610.0": [row]}}, "putExpDateMap": {}}).encode()
        if endpoint == "pricehistory":
            return json.dumps({"symbol": "SPY", "candles": [{"datetime": 1, "open": 1, "high": 2, "low": 1,
                                                             "close": 2, "volume": 9}]}).encode()
        if endpoint == "movers":
            return json.dumps({"screeners": [{"symbol": "AAA", "lastPrice": 3.0, "netChange": 0.1,
                                              "netPercentChange": 0.03, "volume": 1, "totalVolume": 2}]}).encode()
        raise AssertionError(endpoint)


def test_every_read_endpoint_is_asked_once_and_fields_are_reported_by_name_and_type() -> None:
    fake = FakeSchwab()
    report = asyncio.run(verify(fake, NOW))
    assert [e for e, _ in fake.asked] == ["quotes", "expirations", "chains", "quotes", "pricehistory", "pricehistory",
                                          "movers"]
    stock = report["endpoints"]["quotes (stock)"]
    assert stock["fields"]["quote.lastPrice"] == "ok" and stock["fields"]["quote.mark"] == "type text"
    assert stock["fields"]["fundamental.peRatio"] == "absent" and stock["sections"] == ["quote"]
    assert report["endpoints"]["chains"]["absent"] == 0 and report["endpoints"]["quotes (options)"]["mistyped"] == 0
    assert not report["ok"]
    text = "\n".join(summary(report))
    assert "quote.mark: type text" in text and str(SECRET_PRICE) not in text and str(SECRET_PRICE) not in json.dumps(
        report)                                                       # values never leave the check


def test_a_field_is_ok_absent_or_another_type() -> None:
    got = check({"": {"a": 1, "b": "x"}, "q": {"c": True}}, {"a": "number", "b": "number", "q.c": "bool", "q.d": "text"})
    assert got["fields"] == {"a": "ok", "b": "type text", "q.c": "ok", "q.d": "absent"}
    assert (got["ok"], got["absent"], got["mistyped"]) == (2, 1, 1)


def test_without_schwab_it_says_how_to_connect() -> None:
    class Off:
        configured = False

    assert "Settings" in summary(asyncio.run(verify(Off(), NOW)))[0]


def test_fields_schwab_sends_only_sometimes_are_not_flagged_and_an_empty_movers_list_says_why() -> None:
    got = check({"reference": {}}, {"reference.htbQuantity": "number"})
    assert got["absent"] == 0 and got["ok"] == 1
    assert got["fields"]["reference.htbQuantity"] == "absent (sent only for hard-to-borrow stocks)"
    report = {"symbol": "SPY", "checked_at": "2026-11-08T16:33:00", "endpoints": {
        "movers (not read by the app yet)": {**check({"": {"screeners": []}}, {"screeners": "list"}),
                                             "note": "no movers listed right now (market closed)"}}}
    lines = summary(report)
    assert lines[1].startswith("✓ movers") and "market closed" in lines[2]
