"""Causal 15-minute panels: only bars that had ended AND been received by the decision time.

Token: three contiguous closed native 5-minute bars of one pool and mint (provider OHLCV, first observation, USD).
Benchmark: fifteen aligned closed SOL/USD minutes. Nothing is forward-filled, spliced or interpolated: a missing
native bar makes its 15-minute bar missing, and an advisor's history must be consecutive and common to both.
Bar time is the interval start (verified; RESEARCH.md).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from decimal import Decimal

from desk.core.clock import parse_iso8601, to_epoch_millis
from desk.core.hashing import fingerprint
from desk.council.specs import BAR_SECONDS, BENCHMARK_NATIVE_SECONDS, TOKEN_NATIVE_SECONDS

BAR_MS = BAR_SECONDS * 1000


@dataclass(frozen=True)
class Bar:
    """One 15-minute bar aggregated from its complete set of native bars."""

    start_ms: int
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume_usd: Decimal
    available_ms: int          # when its last native bar was first received
    first_row: int
    last_row: int
    digest: str                # hash of the native rows' values, receipt times and raw digests

    @property
    def end_ms(self) -> int:
        return self.start_ms + BAR_MS


def _aggregate(rows: list[sqlite3.Row], native_seconds: int, decision_ms: int, start_key: str,
               available_key: str) -> dict[int, Bar]:
    per_bar = BAR_SECONDS // native_seconds
    groups: dict[int, list[sqlite3.Row]] = {}
    for row in rows:
        available = row[available_key]
        if int(available) > decision_ms:
            continue                                   # received after the decision: unknown at that time
        groups.setdefault(int(row[start_key]) - int(row[start_key]) % BAR_MS, []).append(row)
    bars = {}
    for start, members in groups.items():
        members.sort(key=lambda r: int(r[start_key]))
        expected = [start + i * native_seconds * 1000 for i in range(per_bar)]
        if [int(r[start_key]) for r in members] != expected or start + BAR_MS > decision_ms:
            continue                                   # incomplete or not yet closed: no 15-minute bar exists
        bars[start] = Bar(
            start_ms=start, open=Decimal(members[0]["open"]), high=max(Decimal(r["high"]) for r in members),
            low=min(Decimal(r["low"]) for r in members), close=Decimal(members[-1]["close"]),
            volume_usd=sum((Decimal(r["volume_usd"]) for r in members), Decimal(0)),
            available_ms=max(int(r[available_key]) for r in members),
            first_row=min(int(r["id"]) for r in members), last_row=max(int(r["id"]) for r in members),
            digest=fingerprint([[r["id"], r[start_key], r["open"], r["high"], r["low"], r["close"], r["volume_usd"],
                                 r[available_key], r["raw_sha256"]] for r in members]))
    return bars


def token_bars(market: sqlite3.Connection, pool: str, mint: str, decision_ms: int, count: int) -> dict[int, Bar]:
    since = (decision_ms // BAR_MS - count - 2) * BAR_MS
    rows = market.execute(
        "SELECT id, bar_at_ms, open, high, low, close, volume_usd, observed_at, raw_sha256 FROM ohlcv_bars"
        " WHERE pool_address = ? AND mint = ? AND timeframe = '5m' AND bar_at_ms >= ? AND bar_at_ms + 300000 <= ?"
        " ORDER BY bar_at_ms", (pool, mint, since, decision_ms)).fetchall()
    with_ms = [dict(row) | {"observed_ms": to_epoch_millis(parse_iso8601(row["observed_at"]))} for row in rows]
    return _aggregate(with_ms, TOKEN_NATIVE_SECONDS, decision_ms, "bar_at_ms", "observed_ms")  # type: ignore[arg-type]


def benchmark_bars(market: sqlite3.Connection, pool: str, decision_ms: int, count: int) -> dict[int, Bar]:
    since = (decision_ms // BAR_MS - count - 2) * BAR_MS
    rows = market.execute(
        "SELECT id, bar_start_ms, open, high, low, close, volume_usd, first_received_at_ms, raw_sha256"
        " FROM benchmark_bars WHERE pool_address = ? AND timeframe = '1m' AND bar_start_ms >= ?"
        " AND bar_start_ms + 60000 <= ? ORDER BY bar_start_ms", (pool, since, decision_ms)).fetchall()
    return _aggregate(rows, BENCHMARK_NATIVE_SECONDS, decision_ms, "bar_start_ms", "first_received_at_ms")


@dataclass(frozen=True)
class Panel:
    """The longest run of consecutive 15-minute bars common to token and benchmark, ending at the latest one."""

    token: tuple[Bar, ...]
    benchmark: tuple[Bar, ...]
    latest_end_ms: int | None

    @property
    def length(self) -> int:
        return len(self.token)

    def last(self, count: int) -> tuple[tuple[Bar, ...], tuple[Bar, ...]]:
        return self.token[-count:], self.benchmark[-count:]


def aligned(token: dict[int, Bar], benchmark: dict[int, Bar]) -> Panel:
    common = sorted(set(token) & set(benchmark))
    if not common:
        return Panel((), (), None)
    run = [common[-1]]
    while run[-1] - BAR_MS in token and run[-1] - BAR_MS in benchmark:
        run.append(run[-1] - BAR_MS)
    run.reverse()
    return Panel(tuple(token[s] for s in run), tuple(benchmark[s] for s in run), common[-1] + BAR_MS)


def load_panel(market: sqlite3.Connection, *, pool: str, mint: str, benchmark_pool: str, decision_ms: int,
               count: int) -> Panel:
    return aligned(token_bars(market, pool, mint, decision_ms, count),
                   benchmark_bars(market, benchmark_pool, decision_ms, count))


__all__ = ["BAR_MS", "Bar", "Panel", "aligned", "benchmark_bars", "load_panel", "token_bars"]
