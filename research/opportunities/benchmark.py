"""Disposable offline workload check, not a market experiment. Run from the checkout with uv run python."""

from __future__ import annotations

import json
import resource
import tempfile
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from desk.core.paths import REPO_ROOT, DeskPaths
from desk.ops.opportunities import pass_once
from desk.rh.contracts import BidAsk, Candle, Pair
from desk.rh.repo import RhDatabases, RhMarket


def main() -> None:
    at = datetime(2026, 10, 20, 12, 0, 30, tzinfo=UTC)
    end = at.replace(second=0)
    symbols = ["BTC-USD", *[f"TEST{i:03d}-USD" for i in range(89)]]
    with tempfile.TemporaryDirectory(prefix="kestrel-opportunity-benchmark-") as directory:
        paths = DeskPaths(Path(directory), REPO_ROOT / "config", Path(directory) / "unused.env")
        dbs = RhDatabases.open(paths)
        market = RhMarket(dbs.market)
        market.save_pairs([Pair(s, s.split("-")[0], "USD", Decimal("0.0001"), Decimal("0.01"),
                                Decimal(1_000_000), Decimal(1), "tradable", True) for s in symbols],
                          at - timedelta(days=2))
        market.save_candles("coinbase", [
            Candle(s, 900, end - timedelta(minutes=15 * (96 - i)), Decimal(100), Decimal(101),
                   Decimal(99), Decimal(100), Decimal(1000)) for s in symbols for i in range(96)], at)
        market.save_quotes([BidAsk(s, Decimal("99.9"), Decimal("100.1"), at, "synthetic") for s in symbols])
        before = (tuple(dbs.market.iterdump()), tuple(dbs.ledger.iterdump()))
        wall, cpu = time.perf_counter(), time.process_time()
        result = pass_once(paths, "robinhood", now=at, limit=10000)
        elapsed, consumed = time.perf_counter() - wall, time.process_time() - cpu
        assert result["report"]["cohorts"] == 90 and result["points"] == 90
        assert before == (tuple(dbs.market.iterdump()), tuple(dbs.ledger.iterdump()))
        dbs.close()
        sizes = {p.name: p.stat().st_size for p in paths.db_dir.iterdir() if p.is_file()}
        print(json.dumps({"kind": "SYNTHETIC SOFTWARE WORKLOAD, NOT FORWARD EVIDENCE",
                          "universe": 90, "source_candles": 8640, "research_points": result["points"],
                          "elapsed_seconds": round(elapsed, 4), "cpu_seconds": round(consumed, 4),
                          "process_peak_rss_kib_linux": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                          "database_bytes": sizes, "extra_provider_calls": 0,
                          "host": "cloud workspace; repeat on operator laptop"}, indent=2))


if __name__ == "__main__":
    main()
