"""Headlines come from the company news KESTREL already stores, at no request cost: for every logged event the stored
news covers (index, sector, theme and market days too, not only recent single stocks), and on the stock page. The
per-stock Massive call is only a fallback. Every source answer here is a fake."""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from desk.core.paths import DeskPaths
from desk.stocks import market_events as me
from desk.stocks import news_flow, stock_page, symbol_feeds
from desk.stocks.contracts import Company, Listing
from desk.stocks.repo import StocksDatabases, StocksMarket
from desk.stocks.sectors import sector
from tests.conftest import REPO

DAY = date(2026, 10, 29)                                         # a Thursday; the window opens at Wednesday's close
NOW = datetime(2026, 11, 5, 15, 0, tzinfo=UTC)
COMPANIES = {"NVDA": "3674", "AMD": "3674", "MSFT": "7372", "ORCL": "7372", "XOM": "2911", "GOOGL": "7370"}


@pytest.fixture
def dbs(tmp_path: Path):
    d = StocksDatabases.open(DeskPaths(tmp_path / "home", REPO / "config", tmp_path / "secrets.env"))
    market = StocksMarket(d.market)
    ciks = {s: 100 + i for i, s in enumerate(COMPANIES)}
    market.save_listings([Listing(s, f"{s} Inc", "Nasdaq", False, False, "common") for s in COMPANIES], ciks, NOW)
    for s, sic in COMPANIES.items():
        market.save_company(Company(ciks[s], f"{s} Inc", sic, None, (s,), ("Nasdaq",), "1231", None, ()), sector(sic), NOW)
    yield d
    d.close()


def at(hour: int, day: date = DAY) -> datetime:
    return datetime(day.year, day.month, day.day, hour, 0, tzinfo=UTC)


def article(aid: str, published: datetime, tickers: list[str]) -> dict[str, Any]:
    return {"id": aid, "published": published.isoformat(), "title": f"Headline {aid}", "url": f"https://news.test/{aid}",
            "source": "Wire", "summary": f"About {aid}", "tickers": tickers,
            "insights": [{"ticker": t, "sentiment": "negative", "reasoning": "fixture"} for t in tickers]}


def stored_news(market: Any) -> None:
    news_flow.save(market, [
        article("span-start", at(12, DAY - timedelta(days=60)), ["XOM"]),         # the stored news reaches back here
        article("googl-own", at(14), ["GOOGL"]),
        article("googl-roundup", at(15), ["GOOGL", "MSFT", "XOM", "AMD", "ORCL"]),
        article("spy", at(16), ["SPY"]),
        article("two-tech", at(17), ["MSFT", "ORCL"]),                             # names two technology stocks
        article("one-tech", at(18), ["MSFT"]),                                     # one: about Microsoft, not the sector
        article("chips", at(19), ["NVDA", "AMD"]),
        article("next-day", at(14, DAY + timedelta(days=1)), ["GOOGL", "SPY"]),     # outside the window
        article("span-end", NOW - timedelta(hours=1), ["XOM"]),
    ], NOW)


class Massive:
    configured = True

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def news(self, symbol: str, limit: int = 20, *, published_after: datetime | None = None,
                   published_before: datetime | None = None) -> list[dict[str, Any]]:
        self.calls.append(symbol)
        return [{"title": f"{symbol} from Massive", "url": "https://example.com/m", "source": "Wire",
                 "published": "2026-09-30T13:00:00+00:00"}]


def event(day: date, family: str, subject: str) -> me.Event:
    return me.Event(day, "move", family, subject, 2, "down", "negative", f"{subject} fell", "…", {"symbol": subject})


def test_every_kind_of_event_reads_its_headlines_from_the_stored_news(dbs) -> None:
    stored_news(dbs.market)
    with me.transaction(dbs.research):
        me.save(dbs.research, [event(DAY, "stock", "GOOGL"), event(DAY, "index", "QQQ"), event(DAY, "group", "XLK"),
                               event(DAY, "theme", "SMH"), event(DAY, "market", "market"), event(DAY, "rates", "VIX"),
                               event(DAY - timedelta(days=50), "stock", "XOM")], NOW, "close")
    massive = Massive()
    report = asyncio.run(me.fetch_headlines(massive, dbs.research, NOW, market=dbs.market))
    assert report["stored_headlines"] == 7 and report["headlines"] == 0 and massive.calls == []   # no request at all
    got = {(e["subject"], e["day"]): [h["title"].removeprefix("Headline ") for h in e["headlines"]]
           for e in me.read(dbs.research)}
    d = DAY.isoformat()
    assert got["GOOGL", d] == ["googl-roundup", "googl-own"]          # its own story ranks first, shown newest first
    assert me.stored_headlines(dbs.market, "GOOGL", DAY, limit=1)[0]["title"] == "Headline googl-own"
    assert got["QQQ", d] == got["market", d] == got["VIX", d] == ["spy"]          # the broad market's articles
    assert got["XLK", d] == ["chips", "two-tech", "googl-roundup"]    # two or more of its stocks; not one-tech
    assert got["SMH", d] == ["chips"]                                 # chip makers by SIC code
    assert got["XOM", (DAY - timedelta(days=50)).isoformat()] == []   # an older day in stored news: none, and known
    assert asyncio.run(me.fetch_headlines(massive, dbs.research, NOW, market=dbs.market))["stored_headlines"] == 0


def test_massive_is_asked_only_for_a_recent_stock_move_the_stored_news_cannot_cover(dbs) -> None:
    news_flow.save(dbs.market, [article("recent", NOW - timedelta(days=3), ["XOM"]),
                                article("latest", NOW - timedelta(hours=1), ["XOM"])], NOW)
    old, today = date(2026, 10, 20), date(2026, 11, 5)
    with me.transaction(dbs.research):
        me.save(dbs.research, [event(old, "stock", "AMD"), event(old, "group", "XLK"), event(today, "stock", "NVDA")],
                NOW, "close")
    massive = Massive()
    report = asyncio.run(me.fetch_headlines(massive, dbs.research, NOW, market=dbs.market))
    assert massive.calls == ["AMD"] and report["headlines"] == 1      # before the stored news began: one request
    # today's window has not closed: neither read nor requested yet; the stored news covers it tomorrow
    assert [e["subject"] for e in me.read(dbs.research) if e["headlines"] is not None] == ["AMD"]
    news_flow.save(dbs.market, [article("tomorrow", datetime(2026, 11, 6, 15, 0, tzinfo=UTC), ["NVDA"])], NOW)
    later = asyncio.run(me.fetch_headlines(massive, dbs.research, datetime(2026, 11, 6, 16, 0, tzinfo=UTC),
                                           market=dbs.market))
    assert later["stored_headlines"] == 1 and massive.calls == ["AMD"]


# ── the stock page ────────────────────────────────────────────────────────────────────────────────
class Suite:
    def __init__(self) -> None:
        self.massive = Massive()


def test_the_stock_page_shows_stored_headlines_and_asks_massive_only_for_a_stock_none_name(dbs, tmp_path: Path,
                                                                                         monkeypatch) -> None:
    stored_news(dbs.market)
    recent = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)
    news_flow.save(dbs.market, [article("msft-1", recent, ["MSFT"]), article("msft-2", recent + timedelta(hours=1),
                                                                                    ["MSFT", "ORCL"])], NOW)
    page = stock_page.headlines(dbs.market, "MSFT", NOW, {})
    titles = ["msft-2", "msft-1", "one-tech", "two-tech", "googl-roundup"]          # the last 30 days, newest first
    assert page["news_source"] == "stored" and [n["title"].removeprefix("Headline ") for n in page["news"]] == titles
    assert page["news"][0]["insights"] == [{"ticker": "MSFT", "sentiment": "negative", "reasoning": "fixture"}]
    assert page["news"][0]["summary"] == "About msft-2"
    feeds = {"news": {"value": [{"title": "From the per-stock call"}], "updated_at": None, "error": None, "label": ""}}
    assert stock_page.headlines(dbs.market, "AAPL", NOW, feeds) == {"news": [{"title": "From the per-stock call"}],
                                                                   "news_source": "per_stock"}   # none in 30 days

    # the background pass: stored headlines are saved without a request; only AAPL is asked for
    paths = DeskPaths(tmp_path / "home", REPO / "config", tmp_path / "secrets.env")
    for symbol in ("MSFT", "AAPL"):
        symbol_feeds.request(dbs.market, symbol, NOW)
    dbs.market.execute("DELETE FROM stk_symbol_feeds WHERE feed != 'news'")
    suite = Suite()
    monkeypatch.setattr(symbol_feeds, "utc_now", lambda: NOW)
    monkeypatch.setattr(symbol_feeds, "build_stocks_suite", lambda *a, **k: suite)

    async def closed() -> None:
        return None

    suite.aclose = closed  # type: ignore[attr-defined]
    monkeypatch.setattr(Massive, "news", _per_stock)
    asyncio.run(symbol_feeds.refresh(paths, None))  # type: ignore[arg-type]
    assert suite.massive.calls == ["AAPL"]
    saved = symbol_feeds.read(dbs.market, "MSFT")["news"]
    assert [n["title"].removeprefix("Headline ") for n in saved["value"]] == titles and saved["error"] is None


async def _per_stock(self: Massive, symbol: str, limit: int = 20, **_: Any) -> list[dict[str, Any]]:
    self.calls.append(symbol)
    return [{"title": f"{symbol} from Massive", "url": "https://example.com/m"}]
