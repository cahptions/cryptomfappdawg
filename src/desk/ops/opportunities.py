"""Manual/sidecar research workflow; no collector edits, source migrations, ledger opens or secrets needed."""

from __future__ import annotations

import asyncio
import json
import tempfile
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from desk.core.clock import utc_now
from desk.core.config import load_config
from desk.core.paths import DeskPaths
from desk.core.worker_lock import WorkerLock
from desk.research.opportunities import OpportunityLab, readonly_market
from desk.research.rh_opportunities import RhOpportunityLab
from desk.storage.db import DbKind, connect, migrate

FILES = {"solana": "market.db", "robinhood": "robinhood-market.db"}


def read_report(paths: DeskPaths, venue: str) -> dict[str, Any]:
    """Display a bounded sidecar report; a dashboard read never runs research or opens a database."""
    if venue not in FILES:
        raise ValueError("unknown venue")
    path = paths.home / "reports" / f"opportunities-{venue}.json"
    try:
        with path.open(encoding="utf-8") as handle:
            raw = handle.read(1_000_001)
        if len(raw) > 1_000_000:
            return {"available": False, "reason": "Research report exceeds display limit"}
        value = json.loads(raw)
        if not isinstance(value, dict) or not isinstance(value.get("report"), dict):
            return {"available": False, "reason": "No valid research report yet"}
        return dict(value)
    except (OSError, ValueError):
        return {"available": False, "reason": "Research sidecar has not produced a report yet"}


def pass_once(paths: DeskPaths, venue: str, *, now: datetime | None = None, limit: int = 2000,
              outcomes: int = 200, ingest: bool = True) -> dict[str, Any]:
    if venue not in FILES:
        raise ValueError("venue must be robinhood or solana")
    if not 1 <= limit <= 10000 or not 1 <= outcomes <= 2000:
        raise ValueError("bounded limits required: rows 1..10000, outcomes 1..2000")
    paths.ensure()
    now = now or utc_now()
    source = paths.db_dir / FILES[venue]
    if not source.is_file():
        return {"venue": venue, "available": False, "reason": f"{FILES[venue]} does not exist; start the existing desk"}
    market = readonly_market(source)
    research = connect(paths.db_dir / "research.db", DbKind.RESEARCH)
    try:
        migrate(research, DbKind.RESEARCH)
        # Keep a consistent source snapshot while the real collector continues writing its WAL.
        market.execute("BEGIN")
        lab: OpportunityLab
        if venue == "robinhood":
            settings = load_config(paths.config_dir).settings.robinhood
            ticket = settings.starting_capital_usd * min(settings.fixed_fraction, settings.max_ticket_fraction)
            lab = RhOpportunityLab(market, research, ticket_lamports=100_000_000, ticket_usd=ticket)
        else:
            # Native-unit cached security probe, distinct from the dollar-denominated account reference.
            lamports = int(load_config(paths.config_dir).settings.discovery.ticket_sol * Decimal(10 ** 9))
            lab = OpportunityLab(market, research, ticket_lamports=lamports)
        stats: dict[str, Any] = {"venue": venue, "available": True}
        if ingest:
            stats.update(lab.ingest(now, limit=limit))
            stats["outcomes_written"] = lab.label(now, limit=outcomes)
        stats["report"] = lab.report(now)
        folder = paths.home / "reports"
        folder.mkdir(parents=True, exist_ok=True)
        dest = folder / f"opportunities-{venue}.json"
        with tempfile.NamedTemporaryFile(mode="w", dir=folder, prefix=dest.name, suffix=".tmp",
                                         encoding="utf-8", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(json.dumps(stats, indent=2) + "\n")
        try:
            temporary.replace(dest)
        finally:
            temporary.unlink(missing_ok=True)
        return stats
    finally:
        market.rollback()
        market.close()
        research.close()


async def run(paths: DeskPaths, venues: list[str], *, minutes: float | None = None, limit: int = 2000,
              outcomes: int = 200) -> list[dict[str, Any]]:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + minutes * 60 if minutes is not None else None
    latest = []
    with WorkerLock(paths, "opportunity-research"):
        while True:
            latest = [await asyncio.to_thread(pass_once, paths, venue, limit=limit, outcomes=outcomes) for venue in venues]
            if deadline is None or loop.time() >= deadline:
                return latest
            await asyncio.sleep(min(60, max(0, deadline - loop.time())))
