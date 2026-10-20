"""SOL/USD benchmark minutes for the council, from the payload the collector already requests (zero new requests).

Each market-context cycle fetches the reference pool's latest 1-minute bars. A minute is stored the first time it
arrives closed for longer than the provider's 60-second response cache (bar time is the interval start), with that
response's receipt time and digest. Later copies never replace it, so the table says what was known and when.
``reconstruct_from_raw`` rebuilds the same rows from retained raw payloads, oldest receipt first, so history
collected before this table existed is usable with its original availability times.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from desk.contracts.market import OhlcvBar
from desk.core.clock import parse_iso8601, to_epoch_millis, to_iso
from desk.core.errors import SchemaError
from desk.council.specs import BENCHMARK_NATIVE_SECONDS, PROVIDER_CACHE_SECONDS
from desk.providers.geckoterminal import parse_ohlcv
from desk.providers.parsing import expect_dict, load_json
from desk.security.policy import SOL_MINT, USDC_MINT
from desk.storage.db import transaction
from desk.storage.raw_store import RawStore

INSTRUMENT = "SOL/USD"
CLOSED_AFTER_MS = (BENCHMARK_NATIVE_SECONDS + PROVIDER_CACHE_SECONDS) * 1000


def _usable(bar: OhlcvBar, pool: str) -> bool:
    prices = (bar.open, bar.high, bar.low, bar.close)
    return (bar.pool_address == pool and bar.timeframe == "minute:1" and bar.source == "geckoterminal.ohlcv"
            and all(v.is_finite() for v in (*prices, bar.volume_usd)) and min(prices) > 0 and bar.volume_usd >= 0
            and bar.low <= min(bar.open, bar.close) and bar.high >= max(bar.open, bar.close)
            and to_epoch_millis(bar.ts) % 60_000 == 0)


def save_benchmark_bars(conn: sqlite3.Connection, pool: str, bars: list[OhlcvBar], raw_sha256: str | None) -> int:
    """Store minutes closed beyond the provider cache when received; returns how many were new."""
    if not raw_sha256:
        return 0
    rows = []
    for bar in bars:
        start, received = to_epoch_millis(bar.ts), to_epoch_millis(bar.observed_at)
        if not _usable(bar, pool) or received < start + CLOSED_AFTER_MS:
            continue                       # still open, or possibly a cached partial minute: wait for a later copy
        rows.append((pool, INSTRUMENT, to_iso(bar.ts), start, str(bar.open), str(bar.high), str(bar.low),
                     str(bar.close), str(bar.volume_usd), to_iso(bar.observed_at), received, raw_sha256))
    if not rows:
        return 0
    before = conn.total_changes
    with transaction(conn):
        conn.executemany(
            "INSERT INTO benchmark_bars(pool_address, instrument, source, timeframe, bar_start, bar_start_ms, open, high,"
            " low, close, volume_usd, first_received_at, first_received_at_ms, raw_sha256)"
            " VALUES (?, ?, 'geckoterminal.ohlcv', '1m', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(pool_address, timeframe, bar_start_ms) DO NOTHING", rows)
    return conn.total_changes - before


def reconstruct_from_raw(conn: sqlite3.Connection, raw: RawStore, pool: str) -> dict[str, Any]:
    """Rebuild minutes from the raw SOL payloads market context recorded, with their original receipt times."""
    rows = conn.execute(
        "SELECT DISTINCT r.sha256, r.received_at FROM market_context m JOIN raw_responses r ON r.sha256 = m.raw_sha256"
        " ORDER BY r.received_at, r.sha256").fetchall()
    report = {"payloads": len(rows), "unavailable": 0, "foreign": 0, "bars_added": 0}
    for row in rows:
        try:
            body = raw.get(row["sha256"])
            meta = expect_dict(expect_dict(load_json(body), "response").get("meta"), "meta")
            if (expect_dict(meta.get("base"), "base").get("address") != SOL_MINT
                    or expect_dict(meta.get("quote"), "quote").get("address") != USDC_MINT):
                report["foreign"] += 1             # not a SOL/USDC payload: never relabelled as the benchmark
                continue
            parsed = parse_ohlcv(body, pool, "minute:1", parse_iso8601(row["received_at"]))
        except (OSError, ValueError, SchemaError):
            report["unavailable"] += 1             # purged by retention or unreadable: that history stays missing
            continue
        report["bars_added"] += save_benchmark_bars(conn, pool, parsed.items, row["sha256"])
    return report


__all__ = ["CLOSED_AFTER_MS", "INSTRUMENT", "reconstruct_from_raw", "save_benchmark_bars"]
