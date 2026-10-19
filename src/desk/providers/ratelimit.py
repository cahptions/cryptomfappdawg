"""Cross-process sliding-window rate limiter with shared 429 backoff, stored in ``runtime.db``.

Every desk process (engine, guardian, collector, doctor) draws from the same per-provider budget because the
request log lives in SQLite and each check-and-record happens inside one ``BEGIN IMMEDIATE`` transaction.

A request is allowed only if it fits in *every* window of its bucket, e.g. Jupiter keyless uses
``[(4, 10 s), (25, 60 s)]`` because the provider enforces a short window as well as a per-minute one.
"""

from __future__ import annotations

import asyncio
import random
import sqlite3
import threading
import time
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path

from desk.storage.db import DbKind, connect, migrate

Window = tuple[int, float]
_CLOCK_SKEW_TOLERANCE = 5.0  # events "in the future" beyond this are from a clock that moved backwards


class RateLimiter:
    def __init__(
        self,
        runtime_db: Path,
        *,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        rng: Callable[[], float] = random.random,
        offload: bool = True,
    ) -> None:
        self._conn = connect(runtime_db, DbKind.RUNTIME, check_same_thread=False)
        migrate(self._conn, DbKind.RUNTIME)
        self._lock = threading.Lock()
        self._clock = clock
        self._sleep = sleep
        self._rng = rng
        self._offload = offload  # run SQLite work in a thread so a busy lock never blocks the event loop

    def close(self) -> None:
        self._conn.close()

    # ── synchronous core (one transaction per decision) ───────────────────────────────────────────
    def try_acquire(self, bucket: str, windows: Sequence[Window]) -> float:
        """Record a request and return 0.0, or return how many seconds to wait before trying again."""
        now = self._clock()
        longest = max(seconds for _, seconds in windows)
        with self._lock:
            conn = self._conn
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute("DELETE FROM rate_events WHERE bucket = ? AND (ts < ? OR ts > ?)",
                             (bucket, now - longest, now + _CLOCK_SKEW_TOLERANCE))
                wait = 0.0
                row = conn.execute("SELECT until_ts FROM rate_backoff WHERE bucket = ?", (bucket,)).fetchone()
                if row is not None and row["until_ts"] > now:
                    wait = row["until_ts"] - now
                for limit, seconds in windows:
                    in_window = conn.execute("SELECT COUNT(*) FROM rate_events WHERE bucket = ? AND ts > ?",
                                             (bucket, now - seconds)).fetchone()[0]
                    if in_window >= limit:
                        # the request at this offset must age out before one more fits
                        oldest = conn.execute(
                            "SELECT ts FROM rate_events WHERE bucket = ? AND ts > ? ORDER BY ts LIMIT 1 OFFSET ?",
                            (bucket, now - seconds, in_window - limit),
                        ).fetchone()[0]
                        wait = max(wait, oldest + seconds - now + 0.001)
                if wait <= 0:
                    conn.execute("INSERT INTO rate_events(bucket, ts) VALUES (?, ?)", (bucket, now))
                    conn.execute("INSERT INTO rate_totals(bucket, allocated) VALUES (?, 1) "
                                 "ON CONFLICT(bucket) DO UPDATE SET allocated = allocated + 1", (bucket,))
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
        return float(max(wait, 0.0))

    def note_rate_limited(self, bucket: str, *, retry_after: float | None, base: float, maximum: float) -> float:
        """Register a 429: exponential backoff with ±20% jitter, shared by every process. Returns the delay."""
        now = self._clock()
        with self._lock:
            conn = self._conn
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute("SELECT consecutive_429 FROM rate_backoff WHERE bucket = ?", (bucket,)).fetchone()
                streak = (row["consecutive_429"] if row else 0) + 1
                delay = min(base * 2 ** (streak - 1), maximum) * (0.8 + 0.4 * self._rng())
                if retry_after is not None and retry_after > 0:  # GeckoTerminal sends retry-after: 0 -> ignored
                    delay = max(delay, min(retry_after, maximum))
                conn.execute(
                    "INSERT INTO rate_backoff(bucket, until_ts, consecutive_429, updated_at) VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(bucket) DO UPDATE SET until_ts = MAX(until_ts, excluded.until_ts), "
                    "consecutive_429 = excluded.consecutive_429, updated_at = excluded.updated_at",
                    (bucket, now + delay, streak, now),
                )
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
        return float(delay)

    def note_success(self, bucket: str) -> None:
        with self._lock:
            self._conn.execute("UPDATE rate_backoff SET consecutive_429 = 0 WHERE bucket = ?", (bucket,))

    def backoff_state(self, bucket: str) -> sqlite3.Row | None:
        with self._lock:
            row: sqlite3.Row | None = self._conn.execute(
                "SELECT until_ts, consecutive_429 FROM rate_backoff WHERE bucket = ?", (bucket,)).fetchone()
            return row

    # ── async wrapper ──────────────────────────────────────────────────────────────────────────────
    async def acquire(self, bucket: str, windows: Sequence[Window], *, max_wait: float = 600.0) -> float:
        """Wait until a request fits the budget; return the total seconds waited."""
        waited = 0.0
        while True:
            if self._offload:
                wait = await asyncio.to_thread(self.try_acquire, bucket, windows)
            else:
                wait = self.try_acquire(bucket, windows)
            if wait <= 0:
                return waited
            if waited + wait > max_wait:
                raise TimeoutError(f"rate limiter for {bucket} would wait {waited + wait:.0f}s (> {max_wait:.0f}s)")
            step = min(wait, 2.0) + 0.05 * self._rng()
            await self._sleep(step)
            waited += step
