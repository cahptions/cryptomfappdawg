"""Read-only five-minute provider candles and causal, gap-aware technical indicators."""

from __future__ import annotations

import contextlib
import sqlite3
from collections import deque
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from desk.core.base58 import is_solana_address
from desk.core.clock import TimestampError, parse_iso8601, to_epoch_millis, to_iso, utc_now
from desk.core.paths import DeskPaths

SOURCE = "geckoterminal.ohlcv"
STEP = timedelta(minutes=5)
WARMUP = 100


class _EMA:
    def __init__(self, period: int) -> None:
        self.period = period
        self.seed: deque[Decimal] = deque(maxlen=period)
        self.value: Decimal | None = None

    def add(self, close: Decimal) -> Decimal | None:
        if self.value is None:
            self.seed.append(close)
            if len(self.seed) == self.period:
                self.value = sum(self.seed, Decimal(0)) / self.period
        else:
            self.value += Decimal(2) / (self.period + 1) * (close - self.value)
        return self.value


class _RSI:
    def __init__(self, period: int = 14) -> None:
        self.period = period
        self.previous: Decimal | None = None
        self.gains: list[Decimal] = []
        self.losses: list[Decimal] = []
        self.gain: Decimal | None = None
        self.loss: Decimal | None = None

    def add(self, close: Decimal) -> Decimal | None:
        previous, self.previous = self.previous, close
        if previous is None:
            return None
        change = close - previous
        gain, loss = max(Decimal(0), change), max(Decimal(0), -change)
        if self.gain is None or self.loss is None:
            self.gains.append(gain)
            self.losses.append(loss)
            if len(self.gains) < self.period:
                return None
            self.gain = sum(self.gains, Decimal(0)) / self.period
            self.loss = sum(self.losses, Decimal(0)) / self.period
        else:
            self.gain = (self.gain * (self.period - 1) + gain) / self.period
            self.loss = (self.loss * (self.period - 1) + loss) / self.period
        if self.gain == self.loss == 0:
            return Decimal(50)
        if self.loss == 0:
            return Decimal(100)
        return Decimal(100) - Decimal(100) / (1 + self.gain / self.loss)


def _indicators(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    result = []
    previous: datetime | None = None
    ema20, ema50, rsi = _EMA(20), _EMA(50), _RSI()
    for row in rows:
        try:
            at = parse_iso8601(row["bar_at"])
            values = {key: Decimal(row[key]) for key in ("open", "high", "low", "close", "volume_usd")}
            if (not all(value.is_finite() and value >= 0 for value in values.values())
                    or values["low"] > min(values["open"], values["close"])
                    or values["high"] < max(values["open"], values["close"])
                    or to_epoch_millis(at) != row["bar_at_ms"] or row["bar_at_ms"] % 300_000):
                previous = None
                continue
        except (ValueError, InvalidOperation, TimestampError):
            previous = None
            continue
        if previous is None or at - previous != STEP:
            ema20, ema50, rsi = _EMA(20), _EMA(50), _RSI()
        previous = at
        indicators = {"ema20": ema20.add(values["close"]), "ema50": ema50.add(values["close"]),
                      "rsi14": rsi.add(values["close"])}
        result.append({"at": to_iso(at), **{key: str(value) for key, value in values.items()},
                       **{key: None if value is None else str(value) for key, value in indicators.items()}})
    return result


def chart_data(paths: DeskPaths, mint: str, pool_address: str | None = None, limit: int = 200) -> dict[str, Any]:
    """Read one pool only. Each indicator uses preceding closed bars; gaps restart its warm-up."""
    if not is_solana_address(mint) or (pool_address is not None and not is_solana_address(pool_address)):
        raise ValueError("a valid mint and optional pool address are required")
    if type(limit) is not int or not 1 <= limit <= 500:
        raise ValueError("chart limit must be an integer between 1 and 500")
    result: dict[str, Any] = {
        "mint": mint, "pool_address": pool_address, "timeframe": "5m", "source": SOURCE,
        "observed_at": None, "stale": True, "points": [], "status": "missing",
        "indicators": {
            "ema20": {"period": 20, "method": "EMA seeded by 20-close SMA", "unit": "USD"},
            "ema50": {"period": 50, "method": "EMA seeded by 50-close SMA", "unit": "USD"},
            "rsi14": {"period": 14, "method": "Wilder smoothing; flat=50, only gains=100, only losses=0",
                      "unit": "0–100"}},
        "note": "No stored closed five-minute OHLCV bars for this pool. Collection runs independently of the chart.",
    }
    database = paths.db_dir / "market.db"
    if not database.is_file():
        return result
    now = utc_now()
    with contextlib.closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=2)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        try:
            if pool_address is None:
                row = conn.execute(
                    "SELECT pool_address FROM ohlcv_bars WHERE mint = ? AND timeframe = '5m' AND source = ? "
                    "AND bar_at_ms <= ? AND observed_at <= ? ORDER BY bar_at_ms DESC, id DESC LIMIT 1",
                    (mint, SOURCE, to_epoch_millis(now - STEP), to_iso(now))).fetchone()
                if row is None:
                    return result
                pool_address = row["pool_address"]
                result["pool_address"] = pool_address
            rows = list(conn.execute(
                "SELECT * FROM ohlcv_bars WHERE id IN (SELECT MAX(id) FROM ohlcv_bars WHERE mint = ? "
                "AND pool_address = ? AND timeframe = '5m' AND source = ? AND bar_at_ms <= ? AND observed_at <= ? "
                "GROUP BY bar_at_ms) ORDER BY bar_at_ms DESC LIMIT ?",
                (mint, pool_address, SOURCE, to_epoch_millis(now - STEP), to_iso(now), limit + WARMUP)))
        except sqlite3.OperationalError as exc:
            if "no such table" in str(exc):
                return result
            raise
    points = _indicators(list(reversed(rows)))[-limit:]
    if not points:
        return result
    observed_at = max(row["observed_at"] for row in rows)
    result.update({"status": "ok", "observed_at": observed_at, "points": points,
                   "stale": (now - parse_iso8601(points[-1]["at"]) - STEP > timedelta(minutes=15)
                             or now - parse_iso8601(observed_at) > timedelta(minutes=15)),
                   "note": "Provider OHLCV in USD, closed bars only; indicators are descriptive, not trade approval. "
                           "Indicators remain unavailable until enough consecutive bars exist; gaps reset warm-up. "
                           "EMA history uses up to 100 additional preceding bars."})
    return result
