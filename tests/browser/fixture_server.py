"""Serve the real dashboard against disposable, offline test evidence.

Run with ``uv run python -m tests.browser.fixture_server --scenario populated``.
The fixture never loads operator secrets, contacts providers, or starts a trading worker.
"""

from __future__ import annotations

import argparse
import asyncio
import tempfile
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest
import uvicorn
from solders.pubkey import Pubkey

from desk.core.clock import to_epoch_millis, to_iso, utc_now
from desk.core.paths import REPO_ROOT, DeskPaths
from desk.stats.evidence import confidence_report
from desk.storage.databases import Databases
from desk.web.app import create_app


def seed(paths: DeskPaths) -> None:
    from tests.integration import test_paper_lifecycle as lifecycle

    dbs = Databases.open(paths)
    monkeypatch = pytest.MonkeyPatch()
    try:
        engine, guardian, jupiter, clock, candidate = lifecycle._setup(dbs, monkeypatch)
        assert asyncio.run(engine.consider(candidate)) == "OPENED"
        jupiter.price = Decimal("1.3")
        clock.now += timedelta(seconds=30)
        asyncio.run(guardian.tick())
        jupiter.price *= Decimal("0.88")
        clock.now += timedelta(seconds=30)
        asyncio.run(guardian.tick())
        assert dbs.paper_ledger.execute("SELECT COUNT(*) FROM positions WHERE state = 'CLOSED'").fetchone()[0] == 1

        # A second mint leaves one open position, so both detail routes have real fixture evidence.
        monkeypatch.setattr(lifecycle, "MINT", str(Pubkey.from_bytes(bytes([7]) * 32)))
        engine, guardian, _, _, candidate = lifecycle._setup(dbs, monkeypatch)
        assert asyncio.run(engine.consider(candidate)) == "OPENED"
        asyncio.run(guardian.tick())
        dbs.market.execute("UPDATE tokens SET symbol = 'FIXTURE', name = 'Offline browser fixture'")
        confidence_report(dbs.paper_ledger, dbs.research, clock.now, seed=0, resamples=100)
        # Synthetic test candles only; these fixtures never represent operator market results.
        end_ms = to_epoch_millis(utc_now()) // 300_000 * 300_000
        candidate_pool = dbs.market.execute(
            "SELECT pool_address,mint FROM discoveries ORDER BY last_observed_at DESC LIMIT 1").fetchone()
        if candidate_pool is not None:
            dbs.market.execute(
                "INSERT OR IGNORE INTO pools(address,base_mint,venue,venue_raw,first_seen_at,first_seen_source,last_seen_at) "
                "VALUES (?,?,'fixture','fixture',?,'fixture',?)",
                (candidate_pool["pool_address"], candidate_pool["mint"], to_iso(utc_now()), to_iso(utc_now())))
        for pool in dbs.market.execute("SELECT address,base_mint FROM pools LIMIT 10"):
            for index in range(80):
                from desk.core.clock import from_epoch_millis

                at = from_epoch_millis(end_ms - (80 - index) * 300_000)
                close = Decimal("1") + Decimal(index) / 1000
                dbs.market.execute(
                    "INSERT INTO ohlcv_bars(pool_address,mint,timeframe,bar_at,bar_at_ms,open,high,low,close,"
                    "volume_usd,observed_at,raw_sha256,source) VALUES (?,?,'5m',?,?,?,?,?,?,?,?,'fixture','geckoterminal.ohlcv')",
                    (pool["address"], pool["base_mint"], to_iso(at), to_epoch_millis(at), str(close),
                     str(close + Decimal("0.002")), str(close - Decimal("0.002")), str(close), "1000", to_iso(utc_now())))
    finally:
        monkeypatch.undo()
        dbs.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", choices=("empty", "populated"), default="empty")
    parser.add_argument("--port", type=int, default=8877)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="kestrel-browser-fixture-") as temporary:
        root = Path(temporary)
        paths = DeskPaths(home=root / "data", config_dir=REPO_ROOT / "config", secrets_file=root / "no-secrets.env")
        if args.scenario == "populated":
            seed(paths)
        uvicorn.run(create_app(paths), host="127.0.0.1", port=args.port, access_log=False, server_header=False)


if __name__ == "__main__":
    main()
