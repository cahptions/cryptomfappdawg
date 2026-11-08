"""Company news as it was published, per stock and New York day, so rebuilt and replayed days can read the News desk.

Live research reads stored articles as they were received (desk.stocks.news_flow). A past day can't: most of its news
was never received live. This keeps compact daily counts per stock (articles, and Massive's positive / negative /
neutral labels), point in time by publication: a day counts once it is over.

* Days the stored articles cover are counted from them.
* Earlier days are back-filled from Massive's market-wide feed, newest first from the earliest counted day, a few
  requests a pass (Massive's 5 a minute are shared), back to the first stored Massive price day or until Massive has
  nothing older. Articles of a day still being paged sit in ``stk_news_staging`` until the day is complete, so a day
  is never counted from half its articles and an article is never counted twice.

Counts take about 30 bytes per stock-day (two years: tens of MB), where keeping the articles would take hundreds.
Massive's sentiment labels on back-filled articles may have been assigned after publication; they are the same
labels live research reads.

World events (GDELT) are not back-filled: their 15-minute files would be about 70,000 downloads (roughly 10 GB) for
two years. Rebuilt and replayed days read the stored files, so world shocks count from the first day with a full
32-day window of them, collected going forward.
"""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from desk.stocks import calendar as cal
from desk.stocks.repo import date_of, day_of
from desk.storage.db import transaction

JOB = "news_history"
CALLS_PER_PASS = 3
SENTIMENTS = ("positive", "negative", "neutral")


def _detail(conn: sqlite3.Connection) -> dict[str, Any]:
    row = conn.execute("SELECT detail_json FROM stk_jobs WHERE name = 'news_backfill'").fetchone()
    value = json.loads(row[0]) if row and row[0] else {}
    return value if isinstance(value, dict) else {}


def _bounds(d: date) -> tuple[str, str]:
    start = datetime.combine(d, time(0), cal.NEW_YORK)
    return start.astimezone(UTC).isoformat(), (start + timedelta(days=1)).astimezone(UTC).isoformat()   # as stored


def _ny_day(published: str) -> date:
    return cal.new_york(datetime.fromisoformat(published)).date()


def _count(conn: sqlite3.Connection, d: date, rows: list[tuple[str, str | None]], source: str) -> None:
    by: dict[str, dict[str, int]] = defaultdict(lambda: {"articles": 0, **dict.fromkeys(SENTIMENTS, 0)})
    for symbol, sentiment in rows:
        by[symbol]["articles"] += 1
        if sentiment in SENTIMENTS:
            by[symbol][sentiment] += 1
    conn.executemany("INSERT OR IGNORE INTO stk_news_daily(day, symbol, articles, positive, negative, neutral) "
                     "VALUES (?, ?, ?, ?, ?, ?)", [(day_of(d), s, v["articles"], v["positive"], v["negative"],
                                                    v["neutral"]) for s, v in by.items()])
    conn.execute("INSERT OR IGNORE INTO stk_news_days(day, articles, source) VALUES (?, ?, ?)",
                 (day_of(d), len(rows), source))


def count_stored(conn: sqlite3.Connection, now: datetime) -> int:
    """Count every finished day the stored articles fully cover (after the first stored day, before today)."""
    first = conn.execute("SELECT MIN(published_at) FROM stk_news").fetchone()[0]
    if first is None:
        return 0
    done = {r[0] for r in conn.execute("SELECT day FROM stk_news_days")}
    today = cal.new_york(now).date()
    counted = 0
    d = _ny_day(first) + timedelta(days=1)
    with transaction(conn):
        while d < today:
            if day_of(d) not in done:
                lo, hi = _bounds(d)
                rows = conn.execute("SELECT t.symbol, t.sentiment FROM stk_news n JOIN stk_news_tickers t ON t.news_id = "
                                    "n.id WHERE n.published_at >= ? AND n.published_at < ?", (lo, hi)).fetchall()
                _count(conn, d, [(r[0], r[1]) for r in rows], "stored")
                counted += 1
            d += timedelta(days=1)
    return counted


async def backfill(conn: sqlite3.Connection, massive: Any, now: datetime, *, target: date,
                   calls: int = CALLS_PER_PASS) -> dict[str, Any]:
    """Page Massive's feed backward from the earliest counted day; count each day once all its articles are in."""
    from desk.stocks.repo import StocksMarket

    state = _detail(conn)
    earliest = conn.execute("SELECT MIN(day) FROM stk_news_days").fetchone()[0]
    if earliest is None:
        return {"waiting": "no counted day yet", "remaining": 1}
    cursor = state.get("before") or _bounds(date_of(earliest))[0]
    used = 0
    exhausted = bool(state.get("exhausted"))
    while not exhausted and used < calls and _ny_day(cursor) > target:
        articles = await massive.market_news_before(datetime.fromisoformat(cursor))
        used += 1
        older = [a for a in articles if a["published"] < cursor]
        if not older:
            exhausted = True
            break
        with transaction(conn):
            for a in older:
                sentiments = {i["ticker"]: i.get("sentiment") for i in a.get("insights") or [] if i.get("ticker")}
                for symbol in dict.fromkeys([*(a.get("tickers") or []), *sentiments]):
                    conn.execute("INSERT OR IGNORE INTO stk_news_staging(id, symbol, published_at, sentiment) "
                                 "VALUES (?, ?, ?, ?)", (a["id"], symbol, a["published"], sentiments.get(symbol)))
            cursor = min(a["published"] for a in older)
            boundary = _ny_day(cursor)               # days after the oldest article seen are complete
            staged = conn.execute("SELECT symbol, sentiment, published_at FROM stk_news_staging").fetchall()
            by_day: dict[date, list[tuple[str, str | None]]] = defaultdict(list)
            for symbol, sentiment, published in staged:
                by_day[_ny_day(published)].append((symbol, sentiment))
            for d in sorted(by_day):
                if d > boundary:
                    _count(conn, d, by_day[d], "massive_backfill")
                    lo, hi = _bounds(d)
                    conn.execute("DELETE FROM stk_news_staging WHERE published_at >= ? AND published_at < ?", (lo, hi))
    done = exhausted or _ny_day(cursor) <= target
    detail = {"before": cursor, "exhausted": exhausted, "complete": done, "calls": used, "target": target.isoformat(),
              "remaining": int(not done)}
    StocksMarket(conn).job("news_backfill", now, detail=detail)
    return detail


def counts(conn: sqlite3.Connection, asof: date) -> dict[str, dict[str, Any]]:
    """What news_flow.company gives a live day, from the published counts: the last seven days' tone, the last three
    days' articles and the 60 days before the last week (scaled up to 60 when fewer are counted; none under 14)."""
    try:
        days = {r[0] for r in conn.execute("SELECT day FROM stk_news_days WHERE day BETWEEN ? AND ?",
                                           (day_of(asof) - 66, day_of(asof)))}
    except sqlite3.OperationalError:
        return {}
    week = [day_of(asof) - k for k in range(7)]
    if not all(d in days for d in week):
        return {}                                     # the tone needs every day of the last week counted
    earlier = [d for d in days if d <= day_of(asof) - 7]
    out: dict[str, dict[str, Any]] = defaultdict(lambda: {"positive": 0, "negative": 0, "neutral": 0, "recent3": 0,
                                                          "earlier60": 0, "headlines": []})
    for day, symbol, articles, positive, negative, neutral in conn.execute(
            "SELECT day, symbol, articles, positive, negative, neutral FROM stk_news_daily WHERE day BETWEEN ? AND ?",
            (day_of(asof) - 66, day_of(asof))):
        s = out[symbol]
        age = day_of(asof) - day
        if age < 7:
            s["positive"] += positive
            s["negative"] += negative
            s["neutral"] += neutral
        if age < 3:
            s["recent3"] += articles
        elif age >= 7:
            s["earlier60"] += articles
    covered = len(earlier)
    for s in out.values():
        s["earlier60"] = round(s["earlier60"] * 60 / covered, 2) if covered >= 14 else 0
        s["earlier_days"] = covered
    return dict(out)


def coverage(conn: sqlite3.Connection) -> dict[str, Any]:
    first = conn.execute("SELECT MIN(day) FROM stk_news_days").fetchone()[0]
    worlds = sorted({int(n[0][:8]) for n in conn.execute("SELECT name FROM stk_world_files")})
    world_from = None
    if worlds:
        first_world = datetime.strptime(str(worlds[0]), "%Y%m%d").date()
        world_from = (first_world + timedelta(days=32)).isoformat()
    return {"company_from": (date_of(first) + timedelta(days=6)).isoformat() if first else None,
            "world_from": world_from, "complete": bool(_detail(conn).get("complete"))}


__all__ = ["JOB", "backfill", "count_stored", "counts", "coverage"]
