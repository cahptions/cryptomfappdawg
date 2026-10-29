"""The stock page's company context: source parsers, cached feeds, and sections built from stored records.
Documented examples and recorded keyless responses only; no provider is contacted."""

import json
from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from desk.core.config import load_config
from desk.core.errors import ProviderError, ProviderErrorKind, SchemaError
from desk.stocks import stock_page, symbol_feeds
from desk.stocks.contracts import Dividend, Fact
from desk.stocks.repo import StocksMarket
from desk.stocks.sources.finnhub import parse_earnings, parse_earnings_calendar, parse_recommendations
from desk.stocks.sources.massive import icon_path, icon_type, parse_news, parse_ticker_details
from desk.stocks.sources.schwab import parse_quote_details
from desk.stocks.sources.validation import web_url
from desk.stocks.sources.wikimedia import article_title, company_query, parse_company_facts, parse_summary
from desk.stocks.web import create_app
from tests.conftest import load_fixture
from tests.stocks_world import ASOF, NOW, make_world


def fixture(name: str) -> bytes:
    return load_fixture(f"stocks/{name}")


def test_massive_details_and_news_keep_only_safe_display_fields():
    details = parse_ticker_details(fixture("massive_ticker_details_doc_example.json"))
    assert details["employees"] == 154000 and details["headquarters"] == "Cupertino, CA"
    assert details["list_date"] == "1980-12-12" and details["homepage_url"] == "https://www.apple.com"
    assert details["icon_path"].startswith("/v1/reference/company-branding/") and "apiKey" not in json.dumps(details)
    [news] = parse_news(fixture("massive_news_doc_example.json"))
    assert news["source"] == "Investing.com" and news["published"] == "2024-06-24T18:33:53+00:00"
    assert news["insights"] == [{"ticker": "UBS", "sentiment": "positive",
                                 "reasoning": "UBS analysts are providing a bullish outlook on the extent of future "
                                              "Federal Reserve rate cuts."}]
    hostile = json.loads(fixture("massive_news_doc_example.json"))
    hostile["results"][0]["article_url"] = "javascript:alert(1)"
    assert parse_news(json.dumps(hostile).encode()) == []
    with pytest.raises(SchemaError):
        parse_ticker_details(b'{"status":"ERROR"}')


@pytest.mark.parametrize("url", ["https://evil.example/v1/reference/company-branding/x.png",
                                 "http://api.massive.com/v1/reference/company-branding/x.png",
                                 "https://api.massive.com/v2/aggs/x", "https://api.massive.com/v1/reference/company-"
                                 "branding/../../v2/x", "https://api.massive.com/v1/reference/company-branding/x?a=1"])
def test_icons_are_fetched_only_from_the_massive_branding_path(url):
    assert icon_path(url) is None


def test_icons_must_be_png_or_jpeg_never_svg_or_html():
    assert icon_type(b"\x89PNG\r\n\x1a\n...") == "image/png" and icon_type(b"\xff\xd8\xff\xe0") == "image/jpeg"
    for body in (b"<svg onload=alert(1)>", b"<html>", b"\x89PNG\r\n\x1a\n" + b"0" * 400_000):
        with pytest.raises(SchemaError):
            icon_type(body)


def test_links_are_http_only():
    assert web_url("https://example.org/a") and web_url("http://example.org")
    for bad in ("javascript:alert(1)", "data:text/html,x", "/relative", "https://exa mple.org", None, 5):
        assert web_url(bad) is None


def test_wikidata_facts_pick_latest_headcount_shortest_site_and_drop_unlabelled_values():
    facts = parse_company_facts(fixture("wikidata_company_facts_aapl.json"))
    assert facts == {"headquarters": "Cupertino", "article": "https://en.wikipedia.org/wiki/Apple_Inc.",
                     "founded": "1976", "employees": 164000, "employees_asof": "2022", "website": "https://apple.com/"}
    assert article_title(facts["article"]) == "Apple_Inc." and article_title("https://evil.org/wiki/X") is None
    assert '"0000320193"' in company_query(320193)
    summary = parse_summary(fixture("wikipedia_summary_apple_inc.json"))
    assert summary["extract"].startswith("Apple Inc. is") and summary["url"] == "https://en.wikipedia.org/wiki/Apple_Inc."
    with pytest.raises(SchemaError):
        parse_summary(b'{"type":"disambiguation","extract":"x"}')


def test_finnhub_documented_examples_parse_newest_ratings_and_ordered_quarters():
    ratings = parse_recommendations(fixture("finnhub_recommendation_spec_example.json"))
    assert ratings[0] == {"period": "2025-03-01", "strong_buy": 13, "buy": 24, "hold": 7, "sell": 0, "strong_sell": 0}
    earnings = parse_earnings(fixture("finnhub_earnings_spec_example.json"))
    assert [e["period"] for e in earnings] == ["2022-09-30", "2022-12-31", "2023-03-31"]
    assert earnings[-1]["actual"] == 1.88 and earnings[-1]["estimate"] == 1.9744
    calendar = parse_earnings_calendar(fixture("finnhub_earnings_calendar_spec_example.json"), "AAPL")
    assert calendar[-1]["date"] == "2020-01-28" and calendar[-1]["when"] == "after the close"
    assert parse_earnings_calendar(fixture("finnhub_earnings_calendar_spec_example.json"), "MSFT") == []


def test_schwab_extra_sections_are_optional_and_malformed_values_are_dropped():
    body = json.dumps({"AAPL": {"symbol": "AAPL", "quote": {"lastPrice": 190.5, "bidSize": 300, "askSize": "x",
                                                            "52WeekHigh": 199, "netChange": -1.25,
                                                            "postMarketChange": 0.4},
                                "fundamental": {"peRatio": 29.4, "avg10DaysVolume": 51000000.0,
                                                "divExDate": "2026-08-11 00:00:00.0", "nextDivExDate": "bad"},
                                "reference": {"isHardToBorrow": False, "isShortable": "yes", "htbRate": 0.0}},
                       "MSFT": {"symbol": "MSFT", "quote": {"lastPrice": 410}}}).encode()
    details = parse_quote_details(body)
    assert details["AAPL"]["sections"] == ["quote", "fundamental", "reference"]
    assert details["AAPL"]["bid_size"] == 300 and "ask_size" not in details["AAPL"]
    assert details["AAPL"]["net_change"] == -1.25 and details["AAPL"]["ex_dividend_date"] == "2026-08-11"
    assert "next_ex_dividend_date" not in details["AAPL"] and details["AAPL"]["hard_to_borrow"] is False
    assert "shortable" not in details["AAPL"] and details["MSFT"] == {"sections": ["quote"], "last": 410.0}


def fact(concept: str, start: date, end: date, value: float, filed: date) -> Fact:
    return Fact(concept, "USD", start, end, value, "10-K", filed, None, None, f"a-{end}-{filed}")


def test_fourth_quarter_is_the_year_minus_nine_months_and_reported_quarters_win():
    rows = [fact("r", date(2025, 1, 1), date(2025, 3, 31), 10, date(2025, 5, 1)),
            fact("r", date(2025, 1, 1), date(2025, 6, 30), 25, date(2025, 8, 1)),
            fact("r", date(2025, 4, 1), date(2025, 6, 30), 15, date(2025, 8, 1)),
            fact("r", date(2025, 1, 1), date(2025, 9, 30), 45, date(2025, 11, 1)),
            fact("r", date(2025, 7, 1), date(2025, 9, 30), 20, date(2025, 11, 1)),
            fact("r", date(2025, 1, 1), date(2025, 12, 31), 70, date(2026, 2, 1)),
            fact("r", date(2026, 1, 1), date(2026, 3, 31), 30, date(2026, 5, 1)),
            fact("r", date(2026, 1, 1), date(2026, 3, 31), 31, date(2026, 6, 1))]  # restated later
    quarters, years = stock_page.periods(rows)
    assert quarters[date(2025, 12, 31)] == (25, True) and quarters[date(2025, 6, 30)] == (15, False)
    assert quarters[date(2026, 3, 31)] == (31, False) and years == {date(2025, 12, 31): 70}


@pytest.mark.parametrize("filed,shown", [("COOK TIMOTHY D", "Timothy D. Cook"), ("MUSK ELON", "Elon Musk"),
                                         ("HUANG JEN HSUN", "Huang Jen Hsun"), ("Seller", "Seller"),
                                         ("DE LA CRUZ MARIA J", "De La Cruz Maria J")])
def test_form4_names_are_reordered_only_when_unambiguous(filed, shown):
    assert stock_page.person(filed) == shown


@pytest.fixture
def world(tmp_path):
    w = make_world(tmp_path, n=12)
    yield w
    w.close()


def test_page_sections_come_from_stored_records_with_sources(world):
    market = StocksMarket(world.dbs.market)
    market.save_dividends([Dividend("S10", ASOF - timedelta(days=d), 0.25, ASOF - timedelta(days=d - 14), 4, "CD")
                           for d in (20, 111, 202, 293, 384)] + [Dividend("S10", ASOF + timedelta(days=60), 0.26, None, 4,
                                                                          "CD")], NOW)
    page = stock_page.build(world.dbs.market, world.dbs.research, "S10", NOW, detail=None, quote={"last": 50.0},
                            profile={}, feeds={}, list_titles={}, finnhub_configured=False, has_logo=False)
    assert page["financials"]["quarterly"] and page["financials"]["source"].startswith("Company filings")
    assert page["short_interest"]["latest"]["share_of_outstanding"] == pytest.approx(0.02, rel=0.05)
    insiders = page["insiders"]
    assert insiders["sold"] == 10_000_000 and insiders["sellers"] == 1 and len(insiders["months"]) == 13
    assert all({"bought", "sold", "planned_sold"} <= set(m) for m in insiders["months"])
    assert insiders["recent"][0]["action"] == "Sold" and page["about"]["facts"][0]["label"] == "CEO"
    dividends = page["dividends"]
    assert dividends["trailing_12m"] == pytest.approx(1.0) and dividends["yield"] == pytest.approx(0.02)
    assert dividends["next"]["cash"] == 0.26
    assert page["earnings"] is None  # the synthetic filings carry no EPS and Finnhub is off: nothing is invented
    assert page["ratings"] is None and page["news"] == []


def test_description_and_facts_prefer_the_most_direct_source():
    feeds = {"details": {"value": {"description": "From Massive", "employees": 10, "headquarters": "Austin, TX",
                                   "homepage_url": "https://co.example"}},
             "wikidata": {"value": {"founded": "1999", "employees": 9, "employees_asof": "2020", "ceo": "Wiki Person"}},
             "wikipedia": {"value": {"extract": "From Wikipedia", "url": "https://en.wikipedia.org/wiki/Co"}}}
    about = stock_page.about(None, {"about": "From 10-K", "metadata": {"headquarters": "1 Main St, Austin, TX"}}, feeds,
                             {"name": "Pat Lee", "asof": "2026-10-01"})
    assert about["description"] == {"text": "From Massive", "source": "Massive", "url": None}
    values = {f["label"]: (f["value"], f["source"]) for f in about["facts"]}
    assert values["CEO"] == ("Pat Lee", "SEC Form 4") and values["Employees"] == (10, "Massive")
    assert values["Headquarters"] == ("Austin, TX", "Massive") and values["Founded"] == ("1999", "Wikidata")
    del feeds["details"]
    assert stock_page.about(None, {}, feeds, None)["description"]["source"] == "Wikipedia"


class Fake:
    def __init__(self, finnhub_key: bool = False) -> None:
        self.massive = SimpleNamespace(configured=True,
                                       ticker_details=AsyncMock(return_value={"description": "d", "icon_path":
                                                                              "/v1/reference/company-branding/a/i.png"}),
                                       news=AsyncMock(return_value=[{"title": "t"}]),
                                       icon=AsyncMock(return_value=("image/png", b"\x89PNG\r\n\x1a\nbody")))
        self.wikimedia = SimpleNamespace(company=AsyncMock(return_value={"article": "https://en.wikipedia.org/wiki/S"}),
                                         summary=AsyncMock(return_value={"extract": "x"}))
        self.finnhub = SimpleNamespace(configured=finnhub_key, recommendations=AsyncMock(return_value=[]),
                                       earnings=AsyncMock(return_value=[]), earnings_calendar=AsyncMock(return_value=[]))
        self.aclose = AsyncMock()


async def test_feeds_refresh_in_dependency_order_within_budgets_and_cache(world, monkeypatch):
    fake = Fake()
    monkeypatch.setattr(symbol_feeds, "build_stocks_suite", lambda *args: fake)
    clock = [NOW]
    monkeypatch.setattr(symbol_feeds, "utc_now", lambda: clock[0])
    symbol_feeds.request(world.dbs.market, "S10", NOW)
    config = load_config(world.paths.config_dir)
    await symbol_feeds.refresh(world.paths, config)
    assert fake.massive.ticker_details.await_count == 1 and fake.massive.news.await_count == 1
    assert fake.massive.icon.await_count == 0 and fake.wikimedia.company.await_count == 1
    await symbol_feeds.refresh(world.paths, config)
    assert fake.massive.icon.await_count == 1 and fake.wikimedia.summary.await_count == 1
    assert symbol_feeds.logo(world.dbs.market, "S10") == ("image/png", b"\x89PNG\r\n\x1a\nbody")
    feeds = symbol_feeds.read(world.dbs.market, "S10")
    assert feeds["ratings"]["error"] == "not configured" and fake.finnhub.recommendations.await_count == 0
    await symbol_feeds.refresh(world.paths, config)
    assert fake.massive.ticker_details.await_count == 1  # cached for a week
    clock[0] = NOW + timedelta(minutes=31)
    symbol_feeds.request(world.dbs.market, "S10", clock[0])
    fake.massive.news.side_effect = ProviderError("massive", ProviderErrorKind.HTTP_ERROR, "news: HTTP 403", 403)
    await symbol_feeds.refresh(world.paths, config)
    news = symbol_feeds.read(world.dbs.market, "S10")["news"]
    assert news["value"] == [{"title": "t"}] and "MASSIVE_API_KEY" in news["error"]


async def test_finnhub_is_fetched_once_a_key_is_saved_and_plan_refusals_are_explained(world, monkeypatch):
    fake = Fake(finnhub_key=True)
    fake.finnhub.earnings.side_effect = ProviderError("finnhub", ProviderErrorKind.HTTP_ERROR, "earnings: HTTP 403", 403)
    monkeypatch.setattr(symbol_feeds, "build_stocks_suite", lambda *args: fake)
    monkeypatch.setattr(symbol_feeds, "utc_now", lambda: NOW)
    symbol_feeds.request(world.dbs.market, "S10", NOW)
    await symbol_feeds.refresh(world.paths, load_config(world.paths.config_dir))
    feeds = symbol_feeds.read(world.dbs.market, "S10")
    assert feeds["ratings"]["value"] == [] and "free plan" in feeds["earnings"]["error"]


def test_profile_and_logo_endpoints(world, monkeypatch):
    monkeypatch.setattr("desk.stocks.web.utc_now", lambda: NOW)
    world.dbs.market.execute("INSERT INTO stk_symbol_logos VALUES ('S10','image/png',?,?)",
                             (b"\x89PNG\r\n\x1a\nx", NOW.isoformat()))
    with TestClient(create_app(world.paths, auto_start=False), base_url="http://127.0.0.1:8767") as client:
        page = client.get("/api/stocks/stock/s10/profile").json()
        assert page["symbol"] == "S10" and page["logo"] is True and page["financials"]
        assert client.get("/api/stocks/stock/NOPE/profile").status_code == 404
        assert client.get("/api/stocks/stock/..%2F/profile").status_code == 404
        icon = client.get("/api/stocks/stock/S10/logo")
        assert icon.headers["content-type"] == "image/png" and icon.headers["x-content-type-options"] == "nosniff"
        assert client.get("/api/stocks/stock/S11/logo").status_code == 404
