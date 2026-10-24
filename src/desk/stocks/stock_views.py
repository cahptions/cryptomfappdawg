"""Schwab-first, per-symbol quotes/history. Separate from complete bulk sessions and PAPER inputs.

Dashboard POSTs register interest; a bounded lifespan task fetches it without waiting for bulk backfill.
All prices remain source-labelled; failure retains previous observations, never splices adjustment bases.
"""

from __future__ import annotations

import json
import math
import sqlite3
import zlib
from dataclasses import asdict
from datetime import datetime, timedelta
from typing import Any

from desk.core.clock import to_iso, utc_now
from desk.core.config import LoadedConfig
from desk.core.errors import DeskError
from desk.core.paths import DeskPaths
from desk.core.secrets import load_secrets
from desk.stocks import calendar as cal
from desk.stocks.contracts import Bar, IntradayBar, StockQuote
from desk.stocks.repo import MARKET_FILE
from desk.stocks.suite import build_stocks_suite
from desk.storage.db import DbKind, connect, transaction

BASIS = "Schwab daily candles as supplied; no local split adjustment; adjustment policy not independently verified"


def request(conn: sqlite3.Connection, symbol: str, now: datetime) -> None:
    conn.execute("INSERT INTO stk_stock_views(symbol,requested_at) VALUES (?,?) "
                 "ON CONFLICT(symbol) DO UPDATE SET requested_at=excluded.requested_at", (symbol, to_iso(now)))


def save_history(conn: sqlite3.Connection, symbol: str, bars: list[Bar], now: datetime) -> None:
    if not bars:
        raise DeskError("Schwab returned no daily candles; previous chart history was preserved")
    if any(b.symbol != symbol or b.day > cal.new_york(now).date() for b in bars):
        raise DeskError("Schwab history has an unexpected symbol or future day; previous history was preserved")
    rows = [{"day": b.day.toordinal(), "open": b.open, "high": b.high, "low": b.low,
             "close": b.close, "volume": b.volume} for b in bars]
    payload = zlib.compress(json.dumps(rows, allow_nan=False, separators=(",", ":")).encode())
    conn.execute("UPDATE stk_stock_views SET history_zlib=?,history_at=?,history_error=NULL WHERE symbol=?",
                 (payload, to_iso(now), symbol))


def save_quote(conn: sqlite3.Connection, quote: StockQuote, now: datetime) -> None:
    if quote.last is None or quote.last <= 0 or quote.quoted_at is None or quote.quoted_at > now + timedelta(minutes=5):
        raise DeskError("Schwab returned no timestamped stock price; previous quote was preserved")
    value = {**asdict(quote), "quoted_at": to_iso(quote.quoted_at)}
    conn.execute("UPDATE stk_stock_views SET quote_json=?,quote_at=?,quote_error=NULL WHERE symbol=?",
                 (json.dumps(value, allow_nan=False), to_iso(now), quote.symbol))


def save_intraday(conn: sqlite3.Connection, symbol: str, bars: list[IntradayBar], now: datetime) -> None:
    if not bars:
        raise DeskError("Schwab returned no minute candles; previous intraday chart was preserved")
    if (len(bars) > 10000 or len({b.at for b in bars}) != len(bars)
            or any(b.symbol != symbol or b.at.utcoffset() is None or b.at > now + timedelta(minutes=1)
                   or not all(math.isfinite(v) for v in (b.open, b.high, b.low, b.close, b.volume))
                   or min(b.open, b.high, b.low, b.close) <= 0 or b.volume < 0
                   or b.high < max(b.open, b.low, b.close) or b.low > min(b.open, b.high, b.close) for b in bars)):
        raise DeskError("Unexpected minute candles; previous intraday chart was preserved")
    session = max(cal.new_york(b.at).date() for b in bars)
    selected = sorted((b for b in bars if cal.new_york(b.at).date() == session), key=lambda b: b.at)
    rows = [{"at": to_iso(b.at), "open": b.open, "high": b.high, "low": b.low, "close": b.close,
             "volume": b.volume, "gap_before": bool(i and b.at - selected[i-1].at > timedelta(minutes=1))}
            for i, b in enumerate(selected)]
    payload = zlib.compress(json.dumps(rows, allow_nan=False, separators=(",", ":")).encode())
    conn.execute("UPDATE stk_stock_views SET intraday_zlib=?,intraday_at=?,intraday_error=NULL WHERE symbol=?",
                 (payload, to_iso(now), symbol))


def read(conn: sqlite3.Connection, symbol: str, now: datetime) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM stk_stock_views WHERE symbol=?", (symbol,)).fetchone()
    if row is None:
        return {"bars": [], "quote": None, "requested": False}
    bars, quote = [], None
    history_error = row["history_error"]
    try:
        if row["history_zlib"]:
            bars = json.loads(zlib.decompress(row["history_zlib"]))
        if row["quote_json"]:
            quote = json.loads(row["quote_json"])
            quote["source"] = "Schwab"
            age = (now - datetime.fromisoformat(quote["quoted_at"])).total_seconds()
            quote["stale"] = age > 120
    except (ValueError, TypeError, KeyError, zlib.error):
        bars, quote = [], None
        history_error = "Saved chart data is unreadable; refresh has been requested"
    minute_bars = []
    session = None
    intraday_error = row["intraday_error"]
    try:
        if row["intraday_zlib"]:
            minute_bars = json.loads(zlib.decompress(row["intraday_zlib"]))
            if not isinstance(minute_bars, list) or any(not isinstance(b, dict) or not isinstance(b.get("at"), str)
                                                       for b in minute_bars):
                raise ValueError
            session = cal.new_york(datetime.fromisoformat(minute_bars[-1]["at"])).date() if minute_bars else None
    except (ValueError, TypeError, KeyError, zlib.error):
        minute_bars = []
        intraday_error = "Saved minute candles are unreadable; the next refresh will retry"
    previous_close = None
    if session:
        if quote and cal.new_york(datetime.fromisoformat(quote["quoted_at"])).date() == session:
            previous_close = quote.get("prev_close")
        if not previous_close:
            previous = cal.previous_trading_day(session).toordinal()
            previous_close = next((b["close"] for b in reversed(bars) if b["day"] == previous), None)
    intraday = {"bars": minute_bars, "source": "Schwab", "interval_minutes": 1, "regular_hours": True,
                "session": session.isoformat() if session else None, "updated_at": row["intraday_at"],
                "last_at": minute_bars[-1]["at"] if minute_bars else None,
                "previous_close": previous_close, "error": intraday_error}
    return {"bars": bars, "quote": quote, "requested": True, "updated_at": row["history_at"], "intraday": intraday,
            "history_error": history_error, "quote_error": row["quote_error"], "source": "Schwab",
            "basis": BASIS, "max_note": "Max shows available Schwab history from a request for up to 20 years; "
            "it may not reach the listing's first trading day."}


def due(row: sqlite3.Row, now: datetime, field: str, seconds: int) -> bool:
    value = row[field]
    return value is None or now - datetime.fromisoformat(value) >= timedelta(seconds=seconds)


async def refresh(paths: DeskPaths, loaded: LoadedConfig) -> None:
    """At most two daily histories, two minute histories and one quote batch per pass, with shared rate limits."""
    conn = connect(paths.db_dir / MARKET_FILE, DbKind.STOCKS_MARKET)
    runtime = connect(paths.db_dir / "runtime.db", DbKind.RUNTIME)
    suite = None
    try:
        now = utc_now()
        rows = conn.execute("SELECT * FROM stk_stock_views WHERE requested_at>=? "
                            "ORDER BY requested_at DESC LIMIT 100", (to_iso(now - timedelta(minutes=2)),)).fetchall()
        if not rows:
            return
        suite = build_stocks_suite(paths, loaded, load_secrets(paths.secrets_file), runtime)
        client = suite.schwab
        if client is None or not client.configured:
            conn.executemany("UPDATE stk_stock_views SET intraday_error=? WHERE symbol=?",
                             [("Connect Schwab in Settings to load the 1D minute chart", r["symbol"]) for r in rows])
            return
        symbols = [r["symbol"] for r in rows if due(r, now, "quote_attempt_at", 30)]
        if symbols:
            with transaction(conn):
                conn.executemany("UPDATE stk_stock_views SET quote_attempt_at=? WHERE symbol=?",
                                 [(to_iso(now), s) for s in symbols])
            try:
                received = {q.symbol: q for q in await client.quotes(symbols)}
                for symbol in symbols:
                    try:
                        if symbol not in received:
                            raise DeskError("Schwab did not return this symbol's quote")
                        save_quote(conn, received[symbol], utc_now())
                    except DeskError as exc:
                        conn.execute("UPDATE stk_stock_views SET quote_error=? WHERE symbol=?", (str(exc)[:300], symbol))
            except DeskError as exc:
                conn.executemany("UPDATE stk_stock_views SET quote_error=? WHERE symbol=?",
                                 [(str(exc)[:300], s) for s in symbols])
        minute_count = 0
        for row in rows:
            retry_seconds = 60 if row["intraday_error"] else 30
            if not due(row, now, "intraday_attempt_at", retry_seconds):
                continue
            conn.execute("UPDATE stk_stock_views SET intraday_attempt_at=? WHERE symbol=?", (to_iso(now), row["symbol"]))
            try:
                save_intraday(conn, row["symbol"], await client.intraday_history(row["symbol"]), utc_now())
            except DeskError as exc:
                conn.execute("UPDATE stk_stock_views SET intraday_error=? WHERE symbol=?", (str(exc)[:300], row["symbol"]))
            minute_count += 1
            if minute_count >= 2:
                break
        count = 0
        for row in rows:
            current = row["history_at"] and not due(row, now, "history_at", 900)
            if current or not due(row, now, "history_attempt_at", 300):
                continue
            conn.execute("UPDATE stk_stock_views SET history_attempt_at=? WHERE symbol=?", (to_iso(now), row["symbol"]))
            try:
                save_history(conn, row["symbol"], await client.price_history(row["symbol"]), utc_now())
            except DeskError as exc:
                conn.execute("UPDATE stk_stock_views SET history_error=? WHERE symbol=?", (str(exc)[:300], row["symbol"]))
            count += 1
            if count >= 2:
                break
    finally:
        if suite is not None:
            await suite.aclose()
        conn.close()
        runtime.close()
