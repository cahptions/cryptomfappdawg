"""Measured market context for the regime classifier (PLAN §11.3), recorded as append-only rows.

Every number comes from data the desk observed at or before the measurement time. An input without the
coverage its definition requires is stored as NULL with a reason; it is never a zero. The thirty-day reference
is rebuilt only from these stored rows, so a cold start has no regime until thirty days have actually passed.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from decimal import Decimal, localcontext
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from desk.contracts.market import OhlcvBar
from desk.contracts.observed import UtcDatetime
from desk.core.clock import ensure_utc, from_epoch_millis, to_epoch_millis, to_iso
from desk.core.hashing import canonical_json, sha256_hex
from desk.regime.classifier import ContextObservation, RegimeReference
from desk.storage.market_repo import HAZARD_SOURCE

INPUTS = ("sol_return_1h", "sol_volatility_1h", "watchlist_volume_usd_1h", "migrations_per_hour", "hazard_rate_24h")
DEFINITION: dict[str, Any] = {
    "sol_reference": "GeckoTerminal 1-minute OHLCV of the configured SOL/USDC pool. return = ln(latest close / close "
                     "of the bar starting exactly 3600 s earlier); volatility = sqrt(sum of squared ln returns between "
                     "bars exactly 60 s apart within that hour)",
    "sol_latest_bar_max_age_seconds": 300,
    "sol_min_minute_returns": 55,
    "watchlist": "sum of DexScreener h1 volume_usd, latest row per pool observed within the window before measurement",
    "watchlist_window_seconds": 180,
    "watchlist_min_pools": 20,
    "migrations": "distinct mints with a MIGRATION discovery observation in the preceding hour; requires discovery "
                  "to have run continuously for that hour",
    "hazard": "cohort = pools whose first positive DexScreener liquidity observation is 24-25 h before measurement; "
              "outcome = first DexScreener observation at or after entry + 24 h; event = liquidity <= loss fraction "
              "of entry (explicit zero counts); rate = events / resolved",
    "hazard_min_entry_liquidity_usd": "5000",
    "hazard_loss_fraction": "0.2",
    "hazard_min_resolved": 20,
    "hazard_min_resolved_fraction": "0.8",
    "hazard_followup_lookback_seconds": 1200,
    "reference_sampling": "first complete row per UTC hour; anchored at the latest complete row at or before "
                          "(start of the decision hour - 30 days)",
}
CONTEXT_VERSION = "context-v1-" + sha256_hex(canonical_json(DEFINITION))[:16]

_HOUR_MS = 3_600_000
_DAY_MS = 24 * _HOUR_MS
_SOL_MAX_AGE = timedelta(seconds=int(DEFINITION["sol_latest_bar_max_age_seconds"]))
_LOSS = Decimal(DEFINITION["hazard_loss_fraction"])
_MIN_ENTRY = Decimal(DEFINITION["hazard_min_entry_liquidity_usd"])


class ContextRecord(BaseModel):
    """One measurement. ``reasons`` explains every NULL input; ``coverage`` keeps the counts behind each value."""

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    observed_at: UtcDatetime
    version: str = CONTEXT_VERSION
    sol_return_1h: Decimal | None = None
    sol_volatility_1h: Decimal | None = Field(default=None, ge=0)
    watchlist_volume_usd_1h: Decimal | None = Field(default=None, ge=0)
    migrations_per_hour: Decimal | None = Field(default=None, ge=0)
    hazard_rate_24h: Decimal | None = Field(default=None, ge=0, le=1)
    reasons: dict[str, str] = Field(default_factory=dict)
    coverage: dict[str, int] = Field(default_factory=dict)
    raw_sha256: str | None = None


# ── individual inputs ──────────────────────────────────────────────────────────────────────────────────
def sol_reference_metrics(bars: list[OhlcvBar], now: datetime) -> tuple[Decimal | None, Decimal | None, str | None]:
    """(1 h log return, 1 h realized volatility, reason when unavailable) from 1-minute bars."""
    now = ensure_utc(now)
    closes = {bar.ts: bar.close for bar in bars if bar.timeframe == "minute:1"}
    if not closes:
        return None, None, "SOL_REFERENCE_EMPTY"
    latest = max(closes)
    if latest > now or now - latest > _SOL_MAX_AGE:
        return None, None, "SOL_REFERENCE_STALE"
    anchor = latest - timedelta(hours=1)
    if anchor not in closes:
        return None, None, "SOL_ANCHOR_BAR_MISSING"
    if any(closes[ts] <= 0 for ts in closes):
        return None, None, "SOL_REFERENCE_NONPOSITIVE_CLOSE"
    with localcontext() as ctx:
        ctx.prec = 28
        log_return = (closes[latest] / closes[anchor]).ln()
        squares = [(closes[ts] / closes[ts - timedelta(minutes=1)]).ln() ** 2
                   for ts in sorted(closes) if anchor < ts <= latest and ts - timedelta(minutes=1) in closes]
        if len(squares) < int(DEFINITION["sol_min_minute_returns"]):
            return log_return, None, "SOL_MINUTE_COVERAGE_INSUFFICIENT"
        return log_return, sum(squares, Decimal(0)).sqrt(), None


def watchlist_volume(conn: sqlite3.Connection, now: datetime) -> tuple[Decimal | None, int, str | None]:
    now_ms = to_epoch_millis(now)
    rows = conn.execute(
        "SELECT pool_address, windows_json FROM market_snapshots WHERE source = ? AND observed_at_ms > ? "
        "AND observed_at_ms <= ? ORDER BY observed_at_ms, id",
        (HAZARD_SOURCE, now_ms - int(DEFINITION["watchlist_window_seconds"]) * 1000, now_ms)).fetchall()
    latest = {row["pool_address"]: row["windows_json"] for row in rows}
    volumes = []
    for windows_json in latest.values():
        volume = json.loads(windows_json).get("h1", {}).get("volume_usd")
        if volume is not None:
            volumes.append(Decimal(str(volume)))
    if len(volumes) < int(DEFINITION["watchlist_min_pools"]):
        return None, len(volumes), "WATCHLIST_COVERAGE_INSUFFICIENT"
    return sum(volumes, Decimal(0)), len(volumes), None


def migrations_last_hour(conn: sqlite3.Connection, now: datetime) -> int:
    return int(conn.execute(
        "SELECT COUNT(DISTINCT d.mint) FROM discovery_observations o JOIN discoveries d ON d.id = o.candidate_id "
        "WHERE o.reason = 'MIGRATION' AND o.observed_at > ? AND o.observed_at <= ?",
        (to_iso(now - timedelta(hours=1)), to_iso(now))).fetchone()[0])


def hazard_rate(conn: sqlite3.Connection, now: datetime) -> tuple[Decimal | None, dict[str, int], str | None]:
    now_ms = to_epoch_millis(now)
    cohort = [row for row in conn.execute(
        "SELECT pool_address, entry_at_ms, entry_liquidity_usd FROM hazard_cohort "
        "WHERE entry_at_ms >= ? AND entry_at_ms < ? ORDER BY entry_at_ms, pool_address",
        (now_ms - 25 * _HOUR_MS, now_ms - _DAY_MS)) if Decimal(row["entry_liquidity_usd"]) >= _MIN_ENTRY]
    resolved = events = 0
    for row in cohort:
        outcome = conn.execute(
            "SELECT liquidity_usd, liquidity_status FROM market_snapshots WHERE pool_address = ? AND source = ? "
            "AND observed_at_ms >= ? AND observed_at_ms <= ? ORDER BY observed_at_ms, id LIMIT 1",
            (row["pool_address"], HAZARD_SOURCE, row["entry_at_ms"] + _DAY_MS, now_ms)).fetchone()
        if outcome is None or outcome["liquidity_status"] != "ok":
            continue
        resolved += 1
        if Decimal(outcome["liquidity_usd"]) <= _LOSS * Decimal(row["entry_liquidity_usd"]):
            events += 1
    counts = {"hazard_cohort": len(cohort), "hazard_resolved": resolved, "hazard_events": events}
    if resolved < int(DEFINITION["hazard_min_resolved"]):
        return None, counts, "HAZARD_COHORT_TOO_SMALL"
    if Decimal(resolved) < Decimal(DEFINITION["hazard_min_resolved_fraction"]) * len(cohort):
        return None, counts, "HAZARD_COHORT_UNRESOLVED"
    return Decimal(events) / Decimal(resolved), counts, None


def hazard_followup_mints(conn: sqlite3.Connection, now: datetime, limit: int = 300) -> list[str]:
    """Mints whose cohort pool reached 24 h since entry and has not been re-observed since then."""
    now_ms = to_epoch_millis(now)
    rows = conn.execute(
        "SELECT c.base_mint, c.entry_liquidity_usd FROM hazard_cohort c WHERE c.entry_at_ms >= ? AND c.entry_at_ms <= ? "
        "AND NOT EXISTS (SELECT 1 FROM market_snapshots s WHERE s.pool_address = c.pool_address AND s.source = ? "
        "AND s.observed_at_ms >= c.entry_at_ms + ?) ORDER BY c.entry_at_ms, c.pool_address",
        (now_ms - _DAY_MS - int(DEFINITION["hazard_followup_lookback_seconds"]) * 1000, now_ms - _DAY_MS,
         HAZARD_SOURCE, _DAY_MS)).fetchall()
    mints: list[str] = []
    for row in rows:
        if Decimal(row["entry_liquidity_usd"]) >= _MIN_ENTRY and row["base_mint"] not in mints:
            mints.append(row["base_mint"])
    return mints[:limit]


# ── storage ────────────────────────────────────────────────────────────────────────────────────────────
def save_context(conn: sqlite3.Connection, record: ContextRecord) -> None:
    def text(value: Decimal | None) -> str | None:
        return None if value is None else str(value)

    for name in INPUTS:
        if getattr(record, name) is None and name not in record.reasons:
            raise ValueError(f"{name} is unknown without a recorded reason")
    conn.execute(
        "INSERT INTO market_context(observed_at, observed_at_ms, version, sol_return_1h, sol_volatility_1h,"
        " watchlist_volume_usd_1h, migrations_per_hour, hazard_rate_24h, coverage_json, raw_sha256)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (to_iso(record.observed_at), to_epoch_millis(record.observed_at), record.version,
         *(text(getattr(record, name)) for name in INPUTS),
         canonical_json({"reasons": record.reasons, "counts": record.coverage}), record.raw_sha256))


def latest_context(conn: sqlite3.Connection, as_of: datetime) -> sqlite3.Row | None:
    row: sqlite3.Row | None = conn.execute(
        "SELECT * FROM market_context WHERE observed_at_ms <= ? AND version = ? ORDER BY observed_at_ms DESC, id DESC "
        "LIMIT 1", (to_epoch_millis(as_of), CONTEXT_VERSION)).fetchone()
    return row


# ── thirty-day reference ───────────────────────────────────────────────────────────────────────────────
_COMPLETE_ROWS = ("SELECT * FROM market_context WHERE version = ? AND sol_return_1h IS NOT NULL "
                  "AND sol_volatility_1h IS NOT NULL AND watchlist_volume_usd_1h IS NOT NULL "
                  "AND migrations_per_hour IS NOT NULL AND hazard_rate_24h IS NOT NULL ")


def _observation(row: sqlite3.Row) -> ContextObservation:
    return ContextObservation(observed_at=from_epoch_millis(row["observed_at_ms"]),
                              **{name: Decimal(row[name]) for name in INPUTS})


def build_reference(conn: sqlite3.Connection, as_of: datetime) -> RegimeReference | None:
    """The reference for the decision hour containing ``as_of``; None while thirty measured days are absent."""
    anchor_ms = to_epoch_millis(ensure_utc(as_of)) // _HOUR_MS * _HOUR_MS
    first = conn.execute(_COMPLETE_ROWS + "AND observed_at_ms <= ? ORDER BY observed_at_ms DESC, id DESC LIMIT 1",
                         (CONTEXT_VERSION, anchor_ms - 30 * _DAY_MS)).fetchone()
    if first is None:
        return None
    end_ms = first["observed_at_ms"] + 30 * _DAY_MS
    sampled: dict[int, sqlite3.Row] = {}
    for row in conn.execute(_COMPLETE_ROWS + "AND observed_at_ms > ? AND observed_at_ms <= ? ORDER BY observed_at_ms, id",
                            (CONTEXT_VERSION, first["observed_at_ms"], end_ms)):
        sampled.setdefault(row["observed_at_ms"] // _HOUR_MS, row)
    try:
        return RegimeReference(as_of=from_epoch_millis(end_ms),
                               observations=(_observation(first), *(_observation(r) for r in sampled.values())))
    except (ValidationError, ArithmeticError):
        return None


class ReferenceCache:
    """One reference per decision hour; the result depends only on rows at or before that hour."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self._hour: int | None = None
        self._reference: RegimeReference | None = None

    def get(self, as_of: datetime) -> RegimeReference | None:
        hour = to_epoch_millis(ensure_utc(as_of)) // _HOUR_MS
        if hour != self._hour:
            self._hour, self._reference = hour, build_reference(self.conn, as_of)
        return self._reference
