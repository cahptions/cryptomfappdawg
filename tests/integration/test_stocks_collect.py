"""KESTREL Stocks collection jobs end to end against recorded responses (respx; no network)."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
import respx

from desk.core.config import load_config
from desk.core.paths import DeskPaths
from desk.core.secrets import Secrets
from desk.stocks import calendar as cal
from desk.stocks import collect
from desk.stocks.contracts import Listing
from desk.stocks.repo import StocksDatabases, StocksMarket
from desk.stocks.suite import StocksSuite, build_stocks_suite
from tests.conftest import REPO

F = REPO / "tests" / "fixtures" / "stocks"
NOW = datetime(2026, 10, 21, 22, 0, tzinfo=UTC)             # 18:00 New York, after the close
SEC_WWW, SEC_DATA = "https://www.sec.gov", "https://data.sec.gov"


class World:
    def __init__(self, tmp_path: Path, secrets: dict[str, str] | None = None) -> None:
        self.paths = DeskPaths(tmp_path / "home", REPO / "config", tmp_path / "secrets.env")
        self.dbs = StocksDatabases.open(self.paths)
        self.market = StocksMarket(self.dbs.market)
        self.suite: StocksSuite = build_stocks_suite(
            self.paths, load_config(REPO / "config"),
            Secrets(values=secrets if secrets is not None else {
                "MASSIVE_API_KEY": "k", "TRADIER_TOKEN": "t", "SEC_CONTACT_EMAIL": "me@example.org"}),
            self.dbs.runtime, client=httpx.AsyncClient())

        async def no_wait(*_: Any, **__: Any) -> float:      # the providers' real budgets (Massive: 5 a minute)
            return 0.0                                        # are tested in the rate limiter's own tests
        self.suite.limiter.acquire = no_wait                  # type: ignore[method-assign]

    async def close(self) -> None:
        await self.suite.aclose()
        self.dbs.close()


@pytest.fixture
async def world(tmp_path: Path) -> World:
    w = World(tmp_path)
    yield w
    await w.close()


def grouped(day: date, rows: list[tuple[str, float, float]]) -> dict[str, Any]:
    t = int(datetime(day.year, day.month, day.day, 20, 0, tzinfo=UTC).timestamp() * 1000)
    return {"adjusted": False, "queryCount": len(rows), "resultsCount": len(rows), "request_id": "x", "status": "OK",
            "results": [{"T": s, "o": c, "h": c, "l": c, "c": c, "v": v, "t": t} for s, c, v in rows]}


@respx.mock
async def test_listings_join_sec_ciks_and_keep_only_researchable_stock(world: World) -> None:
    respx.get("https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt").mock(
        return_value=httpx.Response(200, content=(F / "nasdaqlisted.txt").read_bytes()))
    respx.get("https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt").mock(
        return_value=httpx.Response(200, content=(F / "otherlisted.txt").read_bytes()))
    respx.get(f"{SEC_WWW}/files/company_tickers_exchange.json").mock(
        return_value=httpx.Response(200, content=(F / "sec_company_tickers_exchange.json").read_bytes()))
    report = await collect.listings(world.suite, world.market, NOW)
    assert report["ok"] and report["research"] >= 8 and report["with_cik"] >= 6
    by = {r["symbol"]: r for r in world.market.listings()}
    assert by["AAPL"]["cik"] == 320193 and by["BRK.B"]["cik"] == 1067983
    from desk.stocks.universe import research_listings
    research = {r["symbol"] for r in research_listings(world.market)}
    assert "QQQ" not in research and not any(by[s]["kind"] in ("warrant", "unit") for s in research)


@respx.mock
async def test_optional_sec_mapping_failure_preserves_public_listings_and_prior_ciks(world: World) -> None:
    world.market.save_listings([Listing("AAPL", "Apple Common Stock", "NASDAQ", False, False, "common")],
                               {"AAPL": 320193}, NOW)
    for filename in ("nasdaqlisted.txt", "otherlisted.txt"):
        respx.get(f"https://www.nasdaqtrader.com/dynamic/SymDir/{filename}").mock(
            return_value=httpx.Response(200, content=(F / filename).read_bytes()))
    respx.get(f"{SEC_WWW}/files/company_tickers_exchange.json").mock(return_value=httpx.Response(403))
    result = await collect.listings(world.suite, world.market, NOW)
    assert result["ok"] and result["warning"] and result["listings"] > 1
    assert next(r for r in world.market.listings() if r["symbol"] == "AAPL")["cik"] == 320193


async def test_no_signup_jobs_run_without_price_sec_or_options_credentials(tmp_path: Path, monkeypatch: Any) -> None:
    from desk.stocks import runner

    paths = DeskPaths(tmp_path / "home", REPO / "config", tmp_path / "secrets.env")
    calls = []

    def fake(name: str) -> Any:
        async def job(*args: Any, **kwargs: Any) -> dict[str, Any]:
            calls.append(name)
            return {"ok": True}
        return job

    for name in ("listings", "mentions", "rates", "short_interest", "short_volume", "fails", "prices", "companies",
                 "filings_feed", "option_chains", "corporate_actions"):
        monkeypatch.setattr(collect, name, fake(name))
    result = await runner.cycle(paths, load_config(REPO / "config"), research_only=True)
    assert set(calls) == {"listings", "mentions", "rates", "short_interest", "short_volume"}
    assert result["research_only"] and result["analysis"]["paper"] is None
    assert result["problems"]


async def test_paper_uses_ready_data_despite_unavailable_optional_sources(tmp_path: Path, monkeypatch: Any) -> None:
    from desk.stocks import runner
    from tests.stocks_world import make_world

    world = make_world(tmp_path, n=12)
    monkeypatch.setattr(runner, "utc_now", lambda: NOW)

    async def unavailable(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"ok": False, "error": "fixture source unavailable"}

    for name in ("listings", "mentions", "rates", "short_interest", "short_volume"):
        monkeypatch.setattr(collect, name, unavailable)
    try:
        result = await runner.cycle(world.paths, load_config(REPO / "config"))
        assert not result["research_only"] and all(not job["ok"] for job in result["jobs"].values())
        assert result["analysis"]["paper"]["skipped"] is None
        assert runner.saved(world.paths, "research")["lists"]
        ledger = list(world.dbs.ledger.iterdump())
        again = await runner.cycle(world.paths, load_config(REPO / "config"))
        assert "already processed" in again["analysis"]["waiting"]
        assert list(world.dbs.ledger.iterdump()) == ledger
    finally:
        world.close()


@respx.mock
async def test_prices_backfill_newest_first_and_reject_mislabelled_days(world: World) -> None:
    world.market.save_listings([Listing("AAA", "A Common Stock", "NYSE", False, False, "common")], {}, NOW)
    days = cal.trading_days(date(2026, 10, 13), date(2026, 10, 21))
    calls: list[str] = []

    def answer(request: httpx.Request) -> httpx.Response:
        d = date.fromisoformat(request.url.path.rsplit("/", 1)[-1])
        calls.append(d.isoformat())
        assert request.url.params["apiKey"] == "k"
        if d == date(2026, 10, 21):
            return httpx.Response(200, json={"status": "OK", "resultsCount": 0, "results": []})   # not published yet
        return httpx.Response(200, json=grouped(d, [("AAA", 10.0 + d.day, 1e6), ("NOTKEPT", 5.0, 10.0)]))

    respx.get(url__startswith="https://api.massive.com/v2/aggs/grouped/").mock(side_effect=answer)
    report = await collect.prices(world.suite, world.market, NOW, history_days=8, after_close=timedelta(minutes=75))
    assert report["ok"] and calls[0] == "2026-10-21" and report["empty"] == ["2026-10-21"]
    assert world.market.bar_days() == [d for d in days if d != date(2026, 10, 21)]
    assert date(2026, 10, 21) not in world.market.checked_days()       # retried next time, not marked a holiday
    assert {b.symbol for b in world.market.bars("NOTKEPT")} == set()
    respx.get(url__startswith="https://api.massive.com/v2/aggs/grouped/").mock(
        return_value=httpx.Response(200, json=grouped(date(2020, 10, 14), [("AAA", 1.0, 1.0)])))
    bad = await collect.prices(world.suite, world.market, NOW, history_days=8, after_close=timedelta(minutes=75))
    assert not bad["ok"] and "another day" in bad["error"]
    assert world.market.jobs()["prices"]["last_error"]


async def test_partial_price_download_resumes_completed_days_and_records_receipt_time(world: World) -> None:
    from desk.core.errors import DeskError
    from desk.stocks.contracts import Bar

    world.market.save_listings([Listing('AAA', 'A Common Stock', 'NYSE', False, False, 'common')], {}, NOW)
    after_close = timedelta(minutes=75)
    days = collect.missing_price_days(world.market, NOW, 8, after_close)
    first = Bar('AAA', days[0], 10, 10, 10, 10, 1000)
    world.suite.massive.grouped_daily = AsyncMock(side_effect=[ [first], DeskError('synthetic outage') ])
    receipt = NOW + timedelta(minutes=2)
    bad = await collect.prices(world.suite, world.market, NOW, history_days=8, after_close=after_close,
                               max_days=2, clock=lambda: receipt)
    assert not bad['ok']
    assert world.market.bar_days() == [days[0]]
    assert days[0] not in collect.missing_price_days(world.market, NOW, 8, after_close)
    recorded = world.dbs.market.execute('SELECT received_at FROM stk_bar_days').fetchone()[0]
    assert datetime.fromisoformat(recorded) == receipt
    second = Bar('AAA', days[1], 11, 11, 11, 11, 1000)
    world.suite.massive.grouped_daily = AsyncMock(return_value=[second])
    good = await collect.prices(world.suite, world.market, NOW, history_days=8, after_close=after_close,
                                max_days=1, clock=lambda: receipt + timedelta(seconds=1))
    assert good['ok'] and world.market.bar_days() == sorted(days[:2])
    assert len(world.market.bars('AAA')) == 2


@respx.mock
async def test_filings_feed_parses_universe_form4s_and_resumes(world: World) -> None:
    world.market.save_listings([Listing("AYI", "Acuity Inc. Common Stock", "NYSE", False, False, "common")],
                               {"AYI": 1144215}, NOW)
    respx.get(url__regex=r"https://www\.sec\.gov/Archives/edgar/daily-index/2026/QTR4/form\.2026101[6-9]\.idx").mock(
        return_value=httpx.Response(404))
    respx.get(f"{SEC_WWW}/Archives/edgar/daily-index/2026/QTR4/form.20261020.idx").mock(
        return_value=httpx.Response(200, content=(F / "sec_form.20261020.idx").read_bytes()))
    respx.get(f"{SEC_WWW}/Archives/edgar/data/1144215/0001813477-26-000008.txt").mock(
        return_value=httpx.Response(200, content=(F / "sec_form4_full_submission.txt").read_bytes()))
    report = await collect.filings_feed(world.suite, world.market, NOW, start=date(2026, 10, 20))
    assert report["ok"] and report["days"] == ["2026-10-20"] and report["insider_rows"] == 1
    [trade] = world.market.insider_trades(date(2026, 10, 1))
    assert (trade["symbol"], trade["code"], trade["filed"]) == ("AYI", "P", date(2026, 10, 20).toordinal())
    again = await collect.filings_feed(world.suite, world.market, NOW, start=date(2026, 10, 20))
    assert again["days"] == [] and again["through"] == "2026-10-20"                   # 10-21 not published yet


@respx.mock
async def test_insider_history_loads_each_quarter_once(world: World) -> None:
    world.market.save_listings([Listing("GPI", "Group 1 Automotive Common Stock", "NYSE", False, False, "common")],
                               {"GPI": 1031203}, NOW)
    page = ('<a href="/files/datastandardsinnovation/data/insider-transactions-data-sets/2026q3_form345.zip">Q3</a>')
    respx.get(f"{SEC_WWW}/data-research/sec-markets-data/insider-transactions-data-sets").mock(
        return_value=httpx.Response(200, text=page))
    dataset = respx.get(f"{SEC_WWW}/files/datastandardsinnovation/data/insider-transactions-data-sets/2026q3_form345.zip").mock(
        return_value=httpx.Response(200, content=(F / "sec_insider_2026q3_trimmed.zip").read_bytes()))
    report = await collect.insider_history(world.suite, world.market, NOW)
    assert report["ok"] and report["rows"] == 29 and report["newest_quarter"] == "2026q3"
    assert {r["symbol"] for r in world.market.insider_trades(date(2026, 1, 1))} == {"GPI"}    # universe issuers only
    await collect.insider_history(world.suite, world.market, NOW)
    assert dataset.call_count == 1
    assert collect.quarter_end("2026q3") == date(2026, 9, 30) and collect.quarter_end("2026q4") == date(2026, 12, 31)


@respx.mock
async def test_short_interest_finds_the_newest_published_settlement(world: World) -> None:
    world.market.save_listings([Listing("AAPL", "Apple Inc. Common Stock", "Nasdaq", False, False, "common")], {}, NOW)
    recorded = (F / "finra_short_interest_csv_variant.csv").read_bytes()

    def answer(request: httpx.Request) -> httpx.Response:
        assert request.headers["accept"] == "application/json"
        body = json.loads(request.content)
        settlement = body["compareFilters"][0]["fieldValue"]
        return httpx.Response(200, content=recorded) if settlement == "2026-09-30" else httpx.Response(204)

    respx.post("https://api.finra.org/data/group/otcMarket/name/consolidatedShortInterest").mock(side_effect=answer)
    report = await collect.short_interest(world.suite, world.market, NOW, history=1)
    assert report["ok"] and report["settlement"] == "2026-09-30"
    [row] = world.market.short_interest("AAPL")
    assert row["days_to_cover"] == 3.03 and row["short_shares"] == 120763539


@respx.mock
async def test_finra_paging_uses_received_records_not_filtered_rows(world: World) -> None:
    offsets = []
    sample = {"symbolCode": "AAA", "settlementDate": "2026-09-30", "currentShortPositionQuantity": 100}

    def answer(request: httpx.Request) -> httpx.Response:
        offset = json.loads(request.content)["offset"]
        offsets.append(offset)
        rows = [sample] if offset else [*([sample] * 4999), {**sample, "currentShortPositionQuantity": None}]
        return httpx.Response(200, json=rows)

    respx.post("https://api.finra.org/data/group/otcMarket/name/consolidatedShortInterest").mock(side_effect=answer)
    rows = await world.suite.feeds.short_interest(date(2026, 9, 30))
    assert offsets == [0, 5000] and len(rows) == 5000


@respx.mock
async def test_finra_refuses_an_unexpected_settlement_partition(world: World) -> None:
    from desk.core.errors import SchemaError

    respx.post("https://api.finra.org/data/group/otcMarket/name/consolidatedShortInterest").mock(
        return_value=httpx.Response(200, content=(F / "finra_short_interest_csv_variant.csv").read_bytes()))
    with pytest.raises(SchemaError, match="unexpected settlement"):
        await world.suite.feeds.short_interest(date(2026, 10, 15))


@respx.mock
async def test_fred_non_csv_remains_unavailable_without_blocking_treasury(world: World) -> None:
    respx.get(url__startswith="https://home.treasury.gov/").mock(
        return_value=httpx.Response(200, content=(F / "treasury_yield_curve_2026.csv").read_bytes()))

    def answer(request: httpx.Request) -> httpx.Response:
        assert request.headers["accept"] == "text/csv"
        return httpx.Response(200, text="<html>not a CSV download</html>", headers={"content-type": "text/html"})

    respx.get(url__startswith="https://fred.stlouisfed.org/").mock(side_effect=answer)
    result = await collect.rates(world.suite, world.market, NOW)
    assert result["ok"] and "HTML page" in result["fred_errors"]["VIXCLS"]
    assert world.market.rates("UST10Y") and world.market.rates("VIXCLS") == []


@respx.mock
async def test_crowd_rates_and_option_chains(world: World) -> None:
    world.market.save_listings([Listing("SPCX", "SpaceX Common Stock", "Nasdaq", False, False, "common")], {}, NOW)
    page = json.loads((F / "apewisdom_wallstreetbets_p1.json").read_bytes())
    page["pages"] = 1
    respx.get(url__regex=r"https://apewisdom\.io/api/v1\.0/filter/.+/page/1").mock(return_value=httpx.Response(200, json=page))
    report = await collect.mentions(world.suite, world.market, NOW)
    assert report["ok"] and report["rows"]["wallstreetbets"] == len(page["results"])
    hour, rows = world.market.latest_mentions("wallstreetbets")
    assert hour == int(NOW.timestamp() // 3600) and rows[0]["rank"] == 1

    respx.get(url__startswith="https://home.treasury.gov/").mock(
        return_value=httpx.Response(200, content=(F / "treasury_yield_curve_2026.csv").read_bytes()))
    respx.get(url__startswith="https://fred.stlouisfed.org/").mock(return_value=httpx.Response(404))
    rates = await collect.rates(world.suite, world.market, NOW)
    assert rates["ok"] and set(rates["fred_errors"]) == {"BAMLH0A0HYM2", "VIXCLS"}         # optional, not fatal
    assert world.market.rates("UST10Y")[-1].value == 5.12

    exps = [date(2026, 10, 23), date(2026, 11, 20), date(2026, 12, 18), date(2027, 1, 15)]
    far = date(2027, 3, 19)                    # listed, but not one the collector picks
    respx.get(url__startswith="https://sandbox.tradier.com/v1/markets/options/expirations").mock(
        return_value=httpx.Response(200, json={"expirations": {"date": [d.isoformat() for d in [*exps, far]]}}))

    def chain(request: httpx.Request) -> httpx.Response:
        exp = request.url.params["expiration"]
        options = []
        for k in (90, 100, 110):
            for kind in ("call", "put"):
                options.append({"symbol": f"SPCX{exp}{kind[0]}{k}", "underlying": "SPCX", "option_type": kind, "strike": k,
                                "expiration_date": exp, "bid": 4.9, "ask": 5.1, "last": 5.0, "volume": 120,
                                "open_interest": 900, "average_volume": 100})
        return httpx.Response(200, json={"options": {"option": options}})

    respx.get(url__startswith="https://sandbox.tradier.com/v1/markets/options/chains").mock(side_effect=chain)
    report = await collect.option_chains(world.suite, world.market, NOW, ["SPCX"], rate=0.04, last_prices={"SPCX": 100.0})
    assert report["ok"] and report["symbols"] == 1 and report["contracts"] == 24
    day, chains = world.market.option_chain("SPCX")
    assert day == date(2026, 10, 21) and sorted(chains) == exps
    [(_, summary)] = world.market.option_daily("SPCX")
    assert summary["price"] == 100.0 and len(summary["expiries"]) == 4
    assert collect.pick_expirations(exps, date(2026, 10, 21)) == exps
    assert collect.pick_expirations(exps, date(2026, 10, 21), earnings=date(2026, 11, 25)) == exps
    # a PAPER position holding the far expiration keeps it quoted
    report = await collect.option_chains(world.suite, world.market, NOW, [], rate=0.04, last_prices={"SPCX": 100.0},
                                         held={"SPCX": {far}})
    assert report["ok"] and report["symbols"] == 1
    assert sorted(world.market.option_chain("SPCX")[1]) == [*exps, far]


async def test_without_keys_jobs_report_what_is_missing(tmp_path: Path) -> None:
    w = World(tmp_path, secrets={})
    try:
        report = await collect.prices(w.suite, w.market, NOW, history_days=5, after_close=timedelta(minutes=75))
        assert not report["ok"] and "MASSIVE_API_KEY" in report["error"]
        assert "MASSIVE_API_KEY" in w.market.jobs()["prices"]["last_error"]
    finally:
        await w.close()
