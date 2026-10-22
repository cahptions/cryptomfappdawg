"""Child processes for the PAPER fault-injection tests (run with ``python -m tests.fault_injection.paper_procs``).

``engine HOME``: opens positions in a loop (each one atomic), printing an id only after its commit returned.
``guardian HOME PRICE_FILE``: the real Guardian with a fake Jupiter whose token price is read from a file.
"""

from __future__ import annotations

import asyncio
import dataclasses
import os
import signal
import sys
import time
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from solders.pubkey import Pubkey

from desk.contracts.quotes import QuoteObservation
from desk.core.clock import to_iso, utc_now
from desk.core.config import LoadedConfig, load_config
from desk.core.paths import REPO_ROOT, DeskPaths
from desk.paper.guardian import Guardian
from desk.paper.ledger import PaperLedger
from desk.security.policy import SOL_MINT
from desk.storage.databases import Databases
from desk.strategies.specs import paper_specs

TICKET = 10 ** 8
TOKENS = 10 ** 9


def databases(home: str) -> Databases:
    return Databases.open(DeskPaths(home=Path(home), config_dir=REPO_ROOT / "config", secrets_file=Path(home) / "none.env"))


def fast_config() -> LoadedConfig:
    loaded = load_config(REPO_ROOT / "config")
    paper = loaded.settings.paper.model_copy(update={"mark_interval_seconds": 10, "guardian_loop_seconds": 1,
                                                     "conservative_wait_seconds": 0.0,
                                                     "simulated_failure_rate": Decimal(0)})
    return dataclasses.replace(loaded, settings=loaded.settings.model_copy(update={"paper": paper}))


class FilepriceJupiter:
    def __init__(self, price_file: Path) -> None:
        self.price_file = price_file

    async def order(self, input_mint: str, output_mint: str, amount: int) -> QuoteObservation:
        price = Decimal(self.price_file.read_text().strip() or "1")
        out = int(Decimal(amount) * 10 / price) if input_mint == SOL_MINT else int(Decimal(amount) / 10 * price)
        now = utc_now()
        return QuoteObservation(source="fake.order", input_mint=input_mint, output_mint=output_mint, in_amount_raw=amount,
                                out_amount_raw=out, swap_mode="ExactIn", price_impact_pct=Decimal("0.1"),
                                requested_at=now, received_at=now, latency_ms=Decimal(1))


def engine(home: str) -> None:
    dbs = databases(home)
    ledger = PaperLedger(dbs.paper_ledger)
    spec = next(s for s in paper_specs() if s.strategy_id == "B2")
    entry: dict[str, Any] = {"profiles": {"observed": TOKENS, "conservative": TOKENS, "stress": TOKENS},
                             "entry_reported_liquidity_usd": None}
    i = 0
    while True:
        i += 1
        dbs.runtime.execute("INSERT INTO heartbeats(process, pid, at) VALUES ('collector', ?, ?) ON CONFLICT(process) "
                            "DO UPDATE SET pid = excluded.pid, at = excluded.at", (os.getpid(), to_iso(utc_now())))
        ledger.open_position(position_id=f"pos{os.getpid()}-{i}", intent_id=f"intent{os.getpid()}-{i}", candidate_id="c",
                             mint=str(Pubkey.new_unique()), pool_address=str(Pubkey.new_unique()), strategy_id="B2",
                             strategy_version=spec.version_id, decision_hash=f"d{i}", regime_label=None, at=utc_now(),
                             ticket_lamports=TICKET, tokens_raw=TOKENS, entry_fees_lamports=105_000,
                             failed_fees_lamports=0, rent_lamports=2_039_280, entry=entry)
        print(f"pos{os.getpid()}-{i}", flush=True)      # only after the commit returned
        time.sleep(0.02)


def guardian(home: str, price_file: str) -> None:
    dbs = databases(home)
    loaded = fast_config()
    suite: Any = SimpleNamespace(jupiter=FilepriceJupiter(Path(price_file)))
    keeper = Guardian(suite, dbs, loaded)
    loop = asyncio.new_event_loop()
    loop.add_signal_handler(signal.SIGTERM, keeper.stop)
    stats = loop.run_until_complete(keeper.run(None))
    print(stats, flush=True)


if __name__ == "__main__":
    role = sys.argv[1]
    if role == "engine":
        engine(sys.argv[2])
    elif role == "guardian":
        guardian(sys.argv[2], sys.argv[3])
    else:
        raise SystemExit(f"unknown role {role}")
