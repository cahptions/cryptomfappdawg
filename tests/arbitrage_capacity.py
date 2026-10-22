"""Isolated synthetic capacity experiment, never a measure of actual source coverage or profitability."""
from __future__ import annotations

import json
import resource
import tempfile
import time
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from desk.arbitrage.contracts import Observation, Product
from desk.arbitrage.repo import Repo
from desk.arbitrage.views import snapshot
from desk.core.paths import DeskPaths
from desk.storage.db import transaction


def run() -> dict[str, Any]:
    now = datetime.now(UTC)
    with tempfile.TemporaryDirectory(prefix="arbitrage-capacity-") as directory:
        paths = DeskPaths(Path(directory)/"data", Path(directory)/"config", Path(directory)/"private")
        begin = time.monotonic()
        with Repo.open(paths) as repo, transaction(repo.conn):
            for i in range(10000):
                product = Product(id=f"synthetic-{i:05d}", title=f"SYNTHETIC CAPACITY OFFER {i:05d}",
                                  category=f"Fixture category {i % 10}")
                repo.observe(Observation(product=product, source_id="synthetic-capacity",
                    url=f"https://www.adafruit.com/product/{i}", price=Decimal(10+i%100), currency="USD", at=now,
                    availability="UNKNOWN", method="synthetic-capacity-only"))
            repo.queue_offers([f"https://www.adafruit.com/product/{i}" for i in range(10000)], "synthetic", now.isoformat())
        ingest = time.monotonic()-begin
        durations = []
        with Repo.reader(paths) as repo:
            for offset in [0, 4950, 9950]:
                at = time.monotonic()
                state = snapshot(repo, now, offset=offset)
                durations.append(time.monotonic()-at)
                assert state["catalog"]["total"] == 10000 and len(state["board"]) == 50
                assert state["pipeline"]["priced_products"] == 10000 and not state["rankings"]["profit"]
            encoded = json.dumps(state)
            assert len(encoded.encode()) < 500000
            assert state["board"][-1]["product"]["id"] == "synthetic-09999"
        with Repo.open(paths) as repo:
            assert repo.conn.execute("SELECT COUNT(*) FROM arb_offer_queue").fetchone()[0] == 10000
            ids, total = repo.catalog_ids(query="09999")
            assert ids == ["synthetic-09999"] and total == 1
        return {"basis": "Synthetic cloud capacity experiment, not Dell measurement, source access or market evidence",
                "distinct_synthetic_priced_products": 10000, "persistent_queued_urls": 10000,
                "ingest_seconds": round(ingest, 3), "page_seconds": [round(t, 4) for t in durations],
                "last_page_bytes": len(encoded.encode()), "products_per_page": 50,
                "process_peak_rss_mib": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024, 2),
                "database_bytes": sum(p.stat().st_size for p in paths.db_dir.glob('arbitrage.db*')),
                "real_source_offers_verified": 0, "qualifying_opportunities": 0}


if __name__ == "__main__":
    result = run()
    import sys
    if len(sys.argv) > 1:
        Path(sys.argv[1]).write_text(json.dumps(result, indent=2)+"\n")
    print(json.dumps(result, indent=2))
