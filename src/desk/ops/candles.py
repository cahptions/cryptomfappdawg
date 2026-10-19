"""Bounded actual-candle backfill through the existing shared free-provider quota."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from desk.core.clock import ensure_utc, to_epoch_millis, to_iso
from desk.core.errors import ProviderError, ProviderErrorKind, SchemaError
from desk.providers.factory import ProviderSuite
from desk.providers.parsing import expect_dict, load_json
from desk.storage.databases import Databases
from desk.storage.db import transaction
from desk.storage.raw_store import RawStore


@dataclass
class CandleResult:
    inserted: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)


def candle_targets(dbs: Databases, limit: int = 2) -> list[tuple[str, str]]:
    """Prioritize held positions; rotate by persisted attempt time so failures cannot monopolize a cohort."""
    if not 1 <= limit <= 2:
        raise ValueError("at most two candle pools may be fetched per cycle")
    held = []
    for position in dbs.paper_ledger.execute(
            "SELECT DISTINCT pool_address,mint FROM positions WHERE state!='CLOSED' LIMIT 100"):
        known = dbs.market.execute(
            "SELECT p.address,p.base_mint,COALESCE(f.last_attempt_at,'') AS attempted FROM pools p "
            "LEFT JOIN ohlcv_fetch_state f ON f.pool_address=p.address WHERE p.address=? AND p.base_mint=?",
            (position["pool_address"], position["mint"])).fetchone()
        if known:
            held.append(known)
    selected = [(row["address"], row["base_mint"]) for row in sorted(held, key=lambda row: row["attempted"])[:limit]]
    if len(selected) < limit:
        for row in dbs.market.execute(
                "SELECT p.address,p.base_mint FROM (SELECT * FROM discoveries WHERE state IN ('PASS','DEFER') "
                "AND (state='PASS' OR stage >= 1) "
                "ORDER BY CASE WHEN state='PASS' THEN 0 ELSE 1 END,last_observed_at DESC LIMIT 20) d "
                "JOIN pools p ON p.address=d.pool_address "
                "AND p.base_mint=d.mint LEFT JOIN ohlcv_fetch_state f ON f.pool_address=p.address "
                "ORDER BY COALESCE(f.last_attempt_at,''),d.last_observed_at DESC LIMIT 20"):
            item = (row["address"], row["base_mint"])
            if item not in selected:
                selected.append(item)
            if len(selected) == limit:
                break
    return selected


async def collect_candles(suite: ProviderSuite, dbs: Databases, pool: str, mint: str, now: datetime) -> CandleResult:
    """Store closed 5-minute OHLCV only after independently verifying its raw response base mint."""
    now = ensure_utc(now)
    known = dbs.market.execute("SELECT base_mint FROM pools WHERE address=?", (pool,)).fetchone()
    if known is None or known[0] != mint:
        raise ProviderError("geckoterminal", ProviderErrorKind.SCHEMA, "candle pool/base mint does not match known pool")
    dbs.market.execute(
        "INSERT INTO ohlcv_fetch_state(pool_address,mint,last_attempt_at) VALUES (?,?,?) "
        "ON CONFLICT(pool_address) DO UPDATE SET mint=excluded.mint,last_attempt_at=excluded.last_attempt_at",
        (pool, mint, to_iso(now)))
    fetched = await suite.geckoterminal.ohlcv(pool, "minute", aggregate=5, limit=200)
    try:
        if not re.fullmatch(r"[0-9a-f]{64}", fetched.raw_sha256):
            raise SchemaError("missing candle raw evidence digest")
        body = RawStore(dbs.paths.raw_dir, dbs.market).get(fetched.raw_sha256)
        doc = expect_dict(load_json(body), "candle response")
        base = expect_dict(expect_dict(doc.get("meta"), "meta").get("base"), "base")
        if base.get("address") != mint:
            raise SchemaError("candle response base mint does not match requested mint")
    except (SchemaError, OSError, ValueError) as exc:
        raise ProviderError("geckoterminal", ProviderErrorKind.SCHEMA, "candle raw evidence/base identity unavailable") from exc
    result = CandleResult(errors=list(fetched.errors))
    with transaction(dbs.market):
        for bar in fetched.items:
            values = (bar.open, bar.high, bar.low, bar.close, bar.volume_usd)
            if bar.pool_address != pool or bar.timeframe != "minute:5" or bar.source != "geckoterminal.ohlcv":
                result.errors.append("candle pool, timeframe or source mismatch")
                continue
            if not all(value.is_finite() for value in values) or min(values[:4]) <= 0 or bar.volume_usd < 0:
                result.errors.append("invalid candle price or volume")
                continue
            if bar.low > min(bar.open, bar.close) or bar.high < max(bar.open, bar.close) or bar.high < bar.low:
                result.errors.append("inconsistent candle OHLC")
                continue
            timestamp = to_epoch_millis(bar.ts)
            if timestamp % 300_000 or bar.ts.microsecond:
                result.errors.append("unaligned candle timestamp")
                continue
            if bar.ts + timedelta(minutes=5) > min(now, bar.observed_at):
                result.skipped += 1
                continue
            cursor = dbs.market.execute(
                "INSERT INTO ohlcv_bars(pool_address,mint,timeframe,bar_at,bar_at_ms,open,high,low,close,volume_usd,"
                "observed_at,raw_sha256,source) VALUES (?,?,'5m',?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(pool_address,timeframe,bar_at_ms) DO NOTHING",
                (pool, mint, to_iso(bar.ts), timestamp, str(bar.open), str(bar.high), str(bar.low), str(bar.close),
                 str(bar.volume_usd), to_iso(bar.observed_at), fetched.raw_sha256, bar.source))
            result.inserted += cursor.rowcount
        dbs.market.execute("UPDATE ohlcv_fetch_state SET last_success_at=? WHERE pool_address=?", (to_iso(now), pool))
        if result.inserted:
            dbs.market.execute("UPDATE raw_responses SET keep=1 WHERE sha256=?", (fetched.raw_sha256,))
    return result
