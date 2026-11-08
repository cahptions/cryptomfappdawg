"""Company news by publication day for rebuilt and replayed days: finished days counted from the stored articles,
earlier days back-filled from Massive's feed page by page with each day counted once and complete, and a past day's
News desk reading what was published by then. Synthetic articles only."""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any

from desk.core.config import load_config
from desk.stocks import calendar as cal
from desk.stocks import news_flow
from desk.stocks import news_history as nh
from desk.stocks.analysis import Analyzer
from desk.stocks.prices import load_panel
from desk.stocks.repo import day_of
from tests.conftest import REPO
from tests.stocks_world import ASOF, NOW, make_world

SETTINGS = load_config(REPO / "config").settings.stocks


def article(k: int, d: date, hour: int, symbol: str = "S10", sentiment: str = "positive") -> dict[str, Any]:
    at = datetime.combine(d, time(hour), cal.NEW_YORK).astimezone(UTC)
    return {"id": f"a{k}-{d}-{hour}-{symbol}", "title": "t", "url": "https://example.com/x", "published": at.isoformat(),
            "source": "wire", "tickers": [symbol], "insights": [{"ticker": symbol, "sentiment": sentiment}]}


class Massive:
    """Massive's feed newest first up to a time, ``page`` articles a call (the boundary second included)."""

    def __init__(self, articles: list[dict[str, Any]], page: int = 3) -> None:
        self.articles = sorted(articles, key=lambda a: a["published"], reverse=True)
        self.page, self.calls = page, 0

    async def market_news_before(self, before: datetime, limit: int = 1000) -> list[dict[str, Any]]:
        self.calls += 1
        return [a for a in self.articles if datetime.fromisoformat(a["published"]) <= before][:self.page]


def test_stored_days_are_counted_once_finished_and_earlier_days_back_filled_whole(tmp_path: Path) -> None:
    w = make_world(tmp_path, n=12)
    try:
        conn = w.market.conn
        recent = [ASOF - timedelta(days=k) for k in (3, 2, 1, 0)]
        news_flow.save(conn, [article(k, d, h) for k, d in enumerate(recent) for h in (10, 14)], NOW)
        assert nh.count_stored(conn, NOW + timedelta(days=1)) == 3                 # the first stored day may be partial
        assert {r[0] for r in conn.execute("SELECT day FROM stk_news_days")} == {day_of(d) for d in recent[1:]}
        assert nh.count_stored(conn, NOW + timedelta(days=1)) == 0
        older = [ASOF - timedelta(days=k) for k in range(3, 12)]
        feed = [article(100 + k, d, h, sentiment="negative" if h == 9 else "neutral")
                for k, d in enumerate(older) for h in (9, 11, 15)]
        massive = Massive(feed)
        for _ in range(30):
            out = asyncio.run(nh.backfill(conn, massive, NOW, target=ASOF - timedelta(days=8), calls=2))
            if out["complete"]:
                break
        assert out["complete"]
        rows = {r[0]: r[1:] for r in conn.execute(
            "SELECT day, articles, positive, negative, neutral FROM stk_news_daily WHERE symbol = 'S10'")}
        for d in older[1:5]:                                                         # every whole day, counted once
            assert rows[day_of(d)] == (3, 0, 1, 2), d
        assert rows[day_of(recent[1])] == (2, 2, 0, 0)
        target_day = ASOF - timedelta(days=8)
        assert min(rows) <= day_of(target_day) + 1
    finally:
        w.close()


def test_a_past_days_news_desk_reads_what_was_published_by_then(tmp_path: Path) -> None:
    w = make_world(tmp_path, n=12)
    try:
        conn = w.market.conn
        for k in range(30):
            d = ASOF - timedelta(days=k)
            conn.execute("INSERT INTO stk_news_days VALUES (?, 5, 'massive_backfill')", (day_of(d),))
            conn.execute("INSERT INTO stk_news_daily VALUES (?, 'S10', ?, ?, 0, 0)",
                         (day_of(d), 6 if k < 3 else 1, 4 if k < 7 else 0))
        counts = nh.counts(conn, ASOF)
        assert counts["S10"]["positive"] == 28 and counts["S10"]["recent3"] == 18
        assert counts["S10"]["earlier_days"] == 23 and counts["S10"]["earlier60"] == round(23 * 60 / 23, 2)
        assert nh.counts(conn, ASOF + timedelta(days=1)) == {}                      # the newest day isn't counted yet
        day = Analyzer(w.market, w.research, SETTINGS).build(load_panel(w.market, w.days[0], ASOF), ASOF,
                                                              now=NOW, historical=True)
        assert day.ctx.news["S10"]["recent3"] == 18 and day.results["news"]["S10"] is not None
        cover = nh.coverage(conn)
        assert cover["company_from"] == (ASOF - timedelta(days=23)).isoformat()
    finally:
        w.close()
