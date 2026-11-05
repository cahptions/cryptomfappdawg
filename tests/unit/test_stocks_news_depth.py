"""The News desk's stores reach as far back as what reads them: company news 70 days (attention compares the last three
days with days 8-67), world events 32 days (the 30-day baseline) with holes filled, and trade actions refetched from
the newest stored. Every source answer is a fake; nothing touches the network."""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from desk.core.paths import DeskPaths
from desk.stocks import desks, news_flow
from desk.stocks.repo import StocksDatabases, StocksMarket
from tests.conftest import REPO

NOW = datetime(2026, 11, 5, 15, 0, tzinfo=UTC)
TODAY = NOW.date()


@pytest.fixture
def conn(tmp_path: Path):
    dbs = StocksDatabases.open(DeskPaths(tmp_path / "home", REPO / "config", tmp_path / "secrets.env"))
    yield dbs.market
    dbs.close()


def article(aid: str, published: datetime, tickers: list[str], sentiment: str = "neutral") -> dict[str, Any]:
    return {"id": aid, "published": published.isoformat(), "title": f"Headline {aid}", "url": f"https://news.test/{aid}",
            "source": "Wire", "summary": None, "tickers": tickers,
            "insights": [{"ticker": t, "sentiment": sentiment, "reasoning": "fixture"} for t in tickers]}


def daily(first: datetime, last: datetime, prefix: str = "d") -> list[dict[str, Any]]:
    """One ACME article every day at the same hour, ``first`` to ``last``."""
    out, at = [], first
    while at <= last:
        out.append(article(f"{prefix}{at:%Y%m%d}", at, ["ACME"]))
        at += timedelta(days=1)
    return out


class Massive:
    configured = True

    def __init__(self, archive: list[dict[str, Any]], *, page: int = 1000) -> None:
        self.archive = sorted(archive, key=lambda a: a["published"])
        self.page, self.forward, self.backward = page, list[datetime](), list[datetime]()

    async def market_news(self, since: datetime, limit: int = 1000) -> list[dict[str, Any]]:
        self.forward.append(since)
        return [a for a in self.archive if datetime.fromisoformat(a["published"]) >= since][:self.page]

    async def market_news_before(self, until: datetime, limit: int = 1000) -> list[dict[str, Any]]:
        self.backward.append(until)
        older = [a for a in self.archive if datetime.fromisoformat(a["published"]) <= until]
        return list(reversed(older))[:self.page]


# ── company news ─────────────────────────────────────────────────────────────────────────────────
def test_a_30_day_store_is_extended_back_to_70_days_newest_first_a_few_calls_a_pass(conn) -> None:
    archive = daily(NOW - timedelta(days=100, hours=1), NOW - timedelta(hours=1))
    news_flow.save(conn, [a for a in archive if a["published"] >= (NOW - timedelta(days=30)).isoformat()], NOW)
    massive = Massive(archive, page=10)
    passes = []
    while not (out := asyncio.run(news_flow.collect(conn, massive, NOW)))["history"]["complete"]:
        passes.append(out)
        assert out["calls"] + out["history"]["calls"] <= news_flow.CALLS_PER_PASS and len(passes) < 10
    oldest = conn.execute("SELECT MIN(published_at) FROM stk_news").fetchone()[0]
    assert NOW - timedelta(days=80) < datetime.fromisoformat(oldest) <= NOW - timedelta(days=70)    # a page past it
    assert len(passes) == 2 and massive.backward == sorted(massive.backward, reverse=True)      # backward, in steps
    calls = len(massive.backward)
    asyncio.run(news_flow.collect(conn, massive, NOW + timedelta(minutes=30)))
    assert len(massive.backward) == calls                                                       # complete: no more
    job = StocksMarket(conn).jobs()["news_history"]["detail"]
    assert job["complete"] and job["oldest"] == oldest


def test_when_massive_has_no_older_news_that_is_recorded_and_not_asked_again(conn) -> None:
    archive = daily(NOW - timedelta(days=40, hours=1), NOW - timedelta(hours=1))
    massive = Massive(archive)
    first = asyncio.run(news_flow.collect(conn, massive, NOW))                  # empty store: the last week first
    assert massive.forward[0] == NOW - news_flow.FIRST_FORWARD and first["history"]["calls"] == 2
    assert first["history"]["complete"] and first["history"]["exhausted_at"] == archive[0]["published"]
    asyncio.run(news_flow.collect(conn, massive, NOW + timedelta(minutes=30)))
    assert len(massive.backward) == 2


def test_attention_is_measured_against_the_days_actually_stored(conn) -> None:
    """One article a day, every day: the stock is exactly as busy as usual whatever depth is stored."""
    end = datetime(2026, 11, 4, 16, 0, tzinfo=UTC)
    asof = date(2026, 11, 4)

    def attention(days: int) -> tuple[Any, list[str], float]:
        conn.execute("DELETE FROM stk_news_tickers")
        conn.execute("DELETE FROM stk_news")
        news_flow.save(conn, daily(end - timedelta(days=days), end), NOW)
        n = news_flow.company(conn, asof, NOW)
        ctx = desks.Context(asof, ["ACME"], {}, {}, {}, {}, {}, news=news_flow.inputs(conn, asof, NOW, ["ACME"], {}))
        result = desks.news(ctx, ["ACME"])["ACME"]
        return result.facts["attention"], result.reasons, n["ACME"]["earlier_days"]

    full, reasons, covered = attention(70)
    assert covered == 60 and full == pytest.approx(1.0, abs=0.05)
    partial, reasons, covered = attention(30)                       # the old 30-day store: 23 of 60 days
    assert 22 < covered < 24 and partial == pytest.approx(1.0, abs=0.1)
    assert not any("more than usual" in r for r in reasons)          # it read about 2.6× before the fix
    thin, _, covered = attention(15)                                 # under two weeks to compare with: no measure
    assert covered < news_flow.EARLIER_MIN_DAYS and thin is None
