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
