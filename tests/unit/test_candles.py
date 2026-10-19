from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from desk.contracts.market import OhlcvBar
from desk.core.clock import to_iso
from desk.core.errors import ProviderError
from desk.core.paths import DeskPaths
from desk.ops.candles import candle_targets, collect_candles
from desk.providers.factory import ProviderSuite
from desk.providers.parsing import Fetched
from desk.storage.databases import Databases
from desk.storage.raw_store import RawStore

POOL = "G2UuRbmVDzDFP4qyoBJrSKBXSi7d6V4bPP9tHPs36y4b"
MINT = "ArpPsesHXKbGeJsHdquM6acT3uNs7BChyetJjiNYpump"
NOW = datetime(2026, 10, 19, 1, 2, tzinfo=UTC)


def setup(tmp_path: Path) -> Databases:
    dbs = Databases.open(DeskPaths(tmp_path / "data", tmp_path / "config", tmp_path / "secrets"))
    dbs.market.execute("INSERT INTO tokens(mint,first_seen_at,last_seen_at) VALUES (?,?,?)", (MINT, to_iso(NOW), to_iso(NOW)))
    dbs.market.execute(
        "INSERT INTO pools(address,base_mint,venue,venue_raw,first_seen_at,first_seen_source,last_seen_at) "
        "VALUES (?,?,'pumpswap','pumpswap',?,'test',?)", (POOL, MINT, to_iso(NOW), to_iso(NOW)))
    return dbs


def fetched(dbs: Databases, bars: list[OhlcvBar], *, mint: str = MINT) -> Fetched[OhlcvBar]:
    raw = RawStore(dbs.paths.raw_dir, dbs.market).put(
        "geckoterminal", "ohlcv", 200, json.dumps({"meta": {"base": {"address": mint}}}).encode(), NOW)
    return Fetched(bars, [], raw, 200, Decimal(1))


def bar(at: datetime, **changes: object) -> OhlcvBar:
    return OhlcvBar(pool_address=POOL, timeframe="minute:5", ts=at, open=Decimal(1), high=Decimal(2),
                    low=Decimal("0.5"), close=Decimal("1.5"), volume_usd=Decimal(100),
                    source="geckoterminal.ohlcv", observed_at=NOW).model_copy(update=changes)


async def test_candles_store_only_closed_actual_bars_and_never_overwrite(tmp_path: Path) -> None:
    dbs = setup(tmp_path)
    suite = AsyncMock(spec=ProviderSuite)
    suite.geckoterminal = AsyncMock()
    old = bar(NOW.replace(hour=0, minute=55))
    suite.geckoterminal.ohlcv.return_value = fetched(dbs, [old, bar(NOW.replace(minute=0))])
    try:
        result = await collect_candles(suite, dbs, POOL, MINT, NOW)
        assert result.inserted == 1 and result.skipped == 1
        suite.geckoterminal.ohlcv.assert_awaited_once_with(POOL, "minute", aggregate=5, limit=200)
        suite.geckoterminal.ohlcv.return_value = fetched(dbs, [old.model_copy(update={"close": Decimal("1.7")})])
        assert (await collect_candles(suite, dbs, POOL, MINT, NOW + timedelta(minutes=1))).inserted == 0
        row = dbs.market.execute("SELECT * FROM ohlcv_bars").fetchone()
        assert row["close"] == "1.5" and row["timeframe"] == "5m" and row["mint"] == MINT
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            dbs.market.execute("UPDATE ohlcv_bars SET close='7'")
    finally:
        dbs.close()


async def test_mismatched_base_or_pool_cannot_mislabel_prices(tmp_path: Path) -> None:
    dbs = setup(tmp_path)
    suite = AsyncMock(spec=ProviderSuite)
    suite.geckoterminal = AsyncMock()
    suite.geckoterminal.ohlcv.return_value = fetched(dbs, [bar(NOW.replace(hour=0, minute=55))], mint="wrong")
    try:
        with pytest.raises(ProviderError, match="identity"):
            await collect_candles(suite, dbs, POOL, MINT, NOW)
        with pytest.raises(ProviderError, match="known pool"):
            await collect_candles(suite, dbs, POOL, "wrong", NOW)
        assert dbs.market.execute("SELECT COUNT(*) FROM ohlcv_bars").fetchone()[0] == 0
    finally:
        dbs.close()


async def test_malformed_bars_rejected_without_fabrication(tmp_path: Path) -> None:
    dbs = setup(tmp_path)
    suite = AsyncMock(spec=ProviderSuite)
    suite.geckoterminal = AsyncMock()
    old = NOW.replace(hour=0, minute=55)
    suite.geckoterminal.ohlcv.return_value = fetched(dbs, [bar(old, close=Decimal(-1)),
        bar(old, volume_usd=Decimal(-1)), bar(old, timeframe="minute:1"), bar(old + timedelta(seconds=1))])
    try:
        result = await collect_candles(suite, dbs, POOL, MINT, NOW)
        assert result.inserted == 0 and len(result.errors) == 4
    finally:
        dbs.close()


def test_targets_only_passed_known_pools_and_bounded(tmp_path: Path) -> None:
    dbs = setup(tmp_path)
    try:
        assert candle_targets(dbs) == []
        dbs.market.execute(
            "INSERT INTO discoveries(id,mint,pool_address,reason,first_observed_at,last_observed_at,state) "
            "VALUES ('candidate',?,?,'new_pool',?,?,'PASS')", (MINT, POOL, to_iso(NOW), to_iso(NOW)))
        assert candle_targets(dbs) == [(POOL, MINT)]
        dbs.market.execute("UPDATE discoveries SET state='DEFER',stage=1")
        assert candle_targets(dbs) == [(POOL, MINT)]
        dbs.market.execute("UPDATE discoveries SET state='REJECT'")
        assert candle_targets(dbs) == []
        with pytest.raises(ValueError, match="two"):
            candle_targets(dbs, limit=3)
    finally:
        dbs.close()
