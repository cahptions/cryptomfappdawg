"""Stock minute bars kept after Schwab forgets them (its price history serves about ten days of one-minute bars).

Every minute bar the app already asks for (the stock page's intraday and week charts, Plays' gap repair) is archived
here for finished sessions only: one compressed row per stock and session (minute of the New York day, open, high,
low, close, volume), labelled with its source and never rewritten, only added to (a minute already stored is kept).
Gap repair reads the archive for sessions older than Schwab's ten days instead of leaving them unestimated.

Size: about 6 KB a stock-session (measured on synthetic minutes). The archive is capped at ``MAX_BYTES`` (200 MB, about
32,000 stock-sessions); past it the oldest sessions are pruned first, after each write.
"""

from __future__ import annotations

import json
import sqlite3
import zlib
from collections.abc import Sequence
from datetime import date, datetime, timedelta

from desk.core.clock import to_iso
from desk.stocks import calendar as cal
from desk.stocks.contracts import IntradayBar
from desk.stocks.repo import date_of, day_of
from desk.storage.db import transaction

SOURCE = "schwab_pricehistory"
MAX_BYTES = 200_000_000
SETTLE = timedelta(minutes=5)


def _pack(rows: dict[int, list[float]]) -> bytes:
    return zlib.compress(json.dumps([[m, *v] for m, v in sorted(rows.items())], separators=(",", ":")).encode(), 9)


def _unpack(blob: bytes) -> dict[int, list[float]]:
    return {int(r[0]): [float(x) for x in r[1:]] for r in json.loads(zlib.decompress(blob))}


def archive(conn: sqlite3.Connection, bars: Sequence[IntradayBar], now: datetime, *, source: str = SOURCE,
            max_bytes: int = MAX_BYTES) -> int:
    """Add finished sessions' minutes; returns the number of minutes newly kept."""
    by_day: dict[tuple[str, date], dict[int, list[float]]] = {}
    for b in bars:
        local = cal.new_york(b.at)
        d = local.date()
        if not cal.is_trading_day(d) or now < cal.session_close(d) + SETTLE:
            continue                                            # the session is still running: its last bar may change
        by_day.setdefault((b.symbol, d), {})[local.hour * 60 + local.minute] = [
            round(b.open, 4), round(b.high, 4), round(b.low, 4), round(b.close, 4), b.volume]
    added = 0
    with transaction(conn):
        for (symbol, d), minutes in by_day.items():
            row = conn.execute("SELECT bars_zlib FROM stk_minute_bars WHERE symbol = ? AND day = ?",
                               (symbol, day_of(d))).fetchone()
            kept = _unpack(row[0]) if row else {}
            new = {m: v for m, v in minutes.items() if m not in kept}
            if not new:
                continue
            blob = _pack({**new, **kept})
            conn.execute("INSERT INTO stk_minute_bars(symbol, day, source, minutes, bytes, bars_zlib, received_at) "
                         "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(symbol, day) DO UPDATE SET minutes = excluded.minutes, "
                         "bytes = excluded.bytes, bars_zlib = excluded.bars_zlib, received_at = excluded.received_at",
                         (symbol, day_of(d), source, len(new) + len(kept), len(blob), blob, to_iso(now)))
            added += len(new)
    if added:
        prune(conn, max_bytes)
    return added


def prune(conn: sqlite3.Connection, max_bytes: int = MAX_BYTES) -> int:
    """Drop the oldest sessions until the archive fits ``max_bytes``."""
    total = conn.execute("SELECT COALESCE(SUM(bytes), 0) FROM stk_minute_bars").fetchone()[0]
    dropped = 0
    while total > max_bytes:
        oldest = conn.execute("SELECT MIN(day) FROM stk_minute_bars").fetchone()[0]
        size, count = conn.execute("SELECT SUM(bytes), COUNT(*) FROM stk_minute_bars WHERE day = ?", (oldest,)).fetchone()
        with transaction(conn):
            conn.execute("DELETE FROM stk_minute_bars WHERE day = ?", (oldest,))
        total -= size
        dropped += count
    return dropped


def read(conn: sqlite3.Connection, symbol: str, d: date) -> list[IntradayBar]:
    row = conn.execute("SELECT bars_zlib FROM stk_minute_bars WHERE symbol = ? AND day = ?",
                       (symbol, day_of(d))).fetchone()
    if row is None:
        return []
    midnight = datetime.combine(d, datetime.min.time(), cal.NEW_YORK)
    return [IntradayBar(symbol, midnight + timedelta(minutes=m), *v) for m, v in sorted(_unpack(row[0]).items())]


def sessions(conn: sqlite3.Connection, symbol: str) -> list[date]:
    return [date_of(r[0]) for r in conn.execute("SELECT day FROM stk_minute_bars WHERE symbol = ? ORDER BY day",
                                                (symbol,))]


__all__ = ["MAX_BYTES", "archive", "prune", "read", "sessions"]
