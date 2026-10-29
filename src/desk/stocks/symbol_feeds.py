"""Company context for a viewed stock, cached per source: Massive details, news and icon; Wikidata facts and a
Wikipedia summary; Finnhub analyst ratings, EPS surprises and the next earnings date (optional free key).

Display only, never research inputs. Viewing a stock registers interest; a bounded background pass fetches what is
due, a few requests at a time inside each source's shared rate budget, and keeps the last good value on failure.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from desk.core.clock import to_iso, utc_now
from desk.core.config import LoadedConfig
from desk.core.errors import DeskError, ProviderError
from desk.core.paths import DeskPaths
from desk.core.secrets import load_secrets
from desk.stocks.repo import MARKET_FILE
from desk.stocks.sources.wikimedia import article_title
from desk.stocks.suite import StocksSuite, build_stocks_suite
from desk.storage.db import DbKind, connect


@dataclass(frozen=True)
class Feed:
    source: str        # massive | wikimedia | finnhub
    fresh: timedelta   # refresh after
    retry: timedelta   # after a failure
    label: str


FEEDS: dict[str, Feed] = {
    "details": Feed("massive", timedelta(days=7), timedelta(minutes=30), "Massive company details"),
    "news": Feed("massive", timedelta(minutes=30), timedelta(minutes=10), "Massive news"),
    "icon": Feed("massive", timedelta(days=30), timedelta(hours=6), "Massive company icon"),
    "wikidata": Feed("wikimedia", timedelta(days=30), timedelta(hours=1), "Wikidata company facts"),
    "wikipedia": Feed("wikimedia", timedelta(days=30), timedelta(hours=1), "Wikipedia summary"),
    "ratings": Feed("finnhub", timedelta(days=1), timedelta(hours=1), "Finnhub analyst ratings"),
    "earnings": Feed("finnhub", timedelta(days=1), timedelta(hours=1), "Finnhub earnings surprises"),
    "calendar": Feed("finnhub", timedelta(hours=12), timedelta(hours=1), "Finnhub earnings calendar"),
}
ACTIVE = timedelta(minutes=10)          # a page polls every 30 s; older interest is not refreshed
PER_PASS = {"massive": 2, "wikimedia": 1, "finnhub": 3}


def request(conn: sqlite3.Connection, symbol: str, now: datetime) -> None:
    conn.executemany("INSERT INTO stk_symbol_feeds(symbol,feed,requested_at) VALUES (?,?,?) "
                     "ON CONFLICT(symbol,feed) DO UPDATE SET requested_at=excluded.requested_at",
                     [(symbol, feed, to_iso(now)) for feed in FEEDS])


def read(conn: sqlite3.Connection, symbol: str) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row in conn.execute("SELECT * FROM stk_symbol_feeds WHERE symbol=?", (symbol,)):
        try:
            value = json.loads(row["payload_json"]) if row["payload_json"] else None
        except ValueError:
            value = None
        out[row["feed"]] = {"value": value, "updated_at": row["updated_at"], "error": row["error"],
                            "label": FEEDS[row["feed"]].label if row["feed"] in FEEDS else row["feed"]}
    return out


def logo(conn: sqlite3.Connection, symbol: str) -> tuple[str, bytes] | None:
    row = conn.execute("SELECT content_type, body FROM stk_symbol_logos WHERE symbol=?", (symbol,)).fetchone()
    return (row["content_type"], bytes(row["body"])) if row else None


def _due(row: sqlite3.Row, now: datetime) -> bool:
    feed = FEEDS[row["feed"]]
    fresh = row["updated_at"] is None or now - datetime.fromisoformat(row["updated_at"]) >= feed.fresh
    rested = row["attempted_at"] is None or now - datetime.fromisoformat(row["attempted_at"]) >= feed.retry
    return fresh and rested


def _payload(conn: sqlite3.Connection, symbol: str, feed: str) -> Any:
    row = conn.execute("SELECT payload_json FROM stk_symbol_feeds WHERE symbol=? AND feed=?", (symbol, feed)).fetchone()
    try:
        return json.loads(row["payload_json"]) if row and row["payload_json"] else None
    except ValueError:
        return None


def _save(conn: sqlite3.Connection, symbol: str, feed: str, value: Any, now: datetime) -> None:
    conn.execute("UPDATE stk_symbol_feeds SET payload_json=?,updated_at=?,attempted_at=?,error=NULL "
                 "WHERE symbol=? AND feed=?", (json.dumps(value, allow_nan=False), to_iso(now), to_iso(now), symbol, feed))


def _fail(conn: sqlite3.Connection, symbol: str, feed: str, message: str, now: datetime) -> None:
    conn.execute("UPDATE stk_symbol_feeds SET attempted_at=?,error=? WHERE symbol=? AND feed=?",
                 (to_iso(now), message[:300], symbol, feed))


def explain(exc: Exception, feed: Feed) -> str:
    """What the operator sees: a key or plan problem is said plainly; other failures keep the previous value."""
    if isinstance(exc, ProviderError) and exc.status_code in (401, 403):
        if feed.source == "finnhub":
            return "Finnhub refused the request: check FINNHUB_API_KEY in Settings, or this data is not in the free plan."
        return "Massive refused the request: check MASSIVE_API_KEY in Settings."
    if isinstance(exc, ProviderError) and exc.status_code == 404:
        return f"{feed.label}: not available for this symbol."
    return f"{feed.label} could not be refreshed; the previous value is kept and retried automatically."


async def fetch(suite: StocksSuite, conn: sqlite3.Connection, symbol: str, feed: str, today: date) -> Any:
    """One source request for one feed; raises when it cannot be served (the caller records why)."""
    if feed == "details":
        return await suite.massive.ticker_details(symbol)
    if feed == "news":
        return await suite.massive.news(symbol)
    if feed == "icon":
        path = (_payload(conn, symbol, "details") or {}).get("icon_path")
        if not path:
            raise DeskError("Massive details name no company icon")
        content_type, body = await suite.massive.icon(path)
        conn.execute("INSERT INTO stk_symbol_logos(symbol,content_type,body,fetched_at) VALUES (?,?,?,?) "
                     "ON CONFLICT(symbol) DO UPDATE SET content_type=excluded.content_type,body=excluded.body,"
                     "fetched_at=excluded.fetched_at", (symbol, content_type, body, to_iso(utc_now())))
        return {"content_type": content_type, "bytes": len(body)}
    if feed == "wikidata":
        row = conn.execute("SELECT cik FROM stk_listings WHERE symbol=?", (symbol,)).fetchone()
        if row is None or not row["cik"] or suite.wikimedia is None:
            raise DeskError("No SEC CIK is recorded for this symbol")
        return await suite.wikimedia.company(int(row["cik"]))
    if feed == "wikipedia":
        title = article_title((_payload(conn, symbol, "wikidata") or {}).get("article") or "")
        if title is None or suite.wikimedia is None:
            raise DeskError("No English Wikipedia article is linked to this company")
        return await suite.wikimedia.summary(title)
    assert suite.finnhub is not None
    if feed == "ratings":
        return await suite.finnhub.recommendations(symbol)
    if feed == "earnings":
        return await suite.finnhub.earnings(symbol)
    return await suite.finnhub.earnings_calendar(symbol, today - timedelta(days=30), today + timedelta(days=120))


def _blocked(suite: StocksSuite, conn: sqlite3.Connection, symbol: str, feed: str) -> str | None:
    """A reason not to ask at all (missing key, or a feed waiting for the one it depends on)."""
    source = FEEDS[feed].source
    if source == "massive" and not suite.massive.configured:
        return "Add MASSIVE_API_KEY in Settings to load company details, news and the company icon."
    if source == "finnhub" and (suite.finnhub is None or not suite.finnhub.configured):
        return "not configured"
    if feed == "icon" and _payload(conn, symbol, "details") is None:
        return "waiting"
    if feed == "wikipedia" and _payload(conn, symbol, "wikidata") is None:
        return "waiting"
    return None


async def refresh(paths: DeskPaths, loaded: LoadedConfig) -> None:
    conn = connect(paths.db_dir / MARKET_FILE, DbKind.STOCKS_MARKET)
    runtime = connect(paths.db_dir / "runtime.db", DbKind.RUNTIME)
    suite = None
    try:
        now = utc_now()
        rows = [r for r in conn.execute("SELECT * FROM stk_symbol_feeds WHERE requested_at>=? ORDER BY requested_at DESC",
                                        (to_iso(now - ACTIVE),)) if r["feed"] in FEEDS and _due(r, now)]
        if not rows:
            return
        suite = build_stocks_suite(paths, loaded, load_secrets(paths.secrets_file), runtime)
        budget = dict(PER_PASS)
        order = list(FEEDS)
        for row in sorted(rows, key=lambda r: order.index(r["feed"])):
            symbol, feed = row["symbol"], row["feed"]
            source = FEEDS[feed].source
            if budget[source] <= 0:
                continue
            reason = _blocked(suite, conn, symbol, feed)
            if reason == "waiting":
                continue
            if reason is not None:
                # Not an attempt: the feed is fetched as soon as the key is saved.
                conn.execute("UPDATE stk_symbol_feeds SET error=? WHERE symbol=? AND feed=? AND error IS NOT ?",
                             (reason, symbol, feed, reason))
                continue
            budget[source] -= 1
            try:
                _save(conn, symbol, feed, await fetch(suite, conn, symbol, feed, now.date()), utc_now())
            except (DeskError, ProviderError) as exc:
                _fail(conn, symbol, feed, str(exc) if type(exc) is DeskError else explain(exc, FEEDS[feed]), utc_now())
    finally:
        if suite is not None:
            await suite.aclose()
        conn.close()
        runtime.close()


__all__ = ["FEEDS", "Feed", "explain", "fetch", "logo", "read", "refresh", "request"]
