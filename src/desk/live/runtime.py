"""Production wiring for LIVE processes: the LIVE book, the signer socket link and the per-intent authorizer."""

from __future__ import annotations

import os
import sqlite3
from decimal import Decimal

from desk.core.config import LoadedConfig, Mode
from desk.core.paths import DeskPaths
from desk.live.authorize import LiveAuthorizer
from desk.live.book import LiveBook
from desk.live.engine import LiveEngine
from desk.live.gates import capital_ceiling
from desk.live.link import SignerLink
from desk.providers.factory import ProviderSuite
from desk.signer.server import request
from desk.storage.databases import Databases, open_ledger

# A swap holds the signer for up to its blockhash lifetime (~60-90 s) plus settlement; lookups queue behind it.
SIGNER_TIMEOUT_SECONDS = 240.0


def signer_link(paths: DeskPaths) -> SignerLink:
    return SignerLink(lambda payload: request(paths.signer_socket, payload, timeout=SIGNER_TIMEOUT_SECONDS))


def open_book(paths: DeskPaths) -> tuple[LiveBook, sqlite3.Connection]:
    conn = open_ledger(paths.db_dir, Mode.LIVE)
    return LiveBook(conn), conn


def start_problems(paths: DeskPaths, loaded: LoadedConfig) -> list[str]:
    """What stops LIVE processes from starting at all (gates 1 and 4); the signer re-checks all seven per BUY."""
    problems = []
    if loaded.settings.desk.mode is not Mode.LIVE or os.environ.get("DESK_LIVE_CONFIRM") != "yes":
        problems.append("gate 1: set desk.mode = \"LIVE\" in config and export DESK_LIVE_CONFIRM=yes")
    if not Decimal(0) < loaded.settings.live.max_capital_sol <= capital_ceiling(paths):
        problems.append(f"gate 4: live.max_capital_sol must be > 0 and <= {capital_ceiling(paths)} SOL")
    return problems


def live_engine(paths: DeskPaths, loaded: LoadedConfig, suite: ProviderSuite, dbs: Databases,
                book: LiveBook) -> LiveEngine:
    return LiveEngine(suite, dbs, loaded, book, signer_link(paths),
                      LiveAuthorizer(paths, loaded, capital_ceiling(paths)))


__all__ = ["SIGNER_TIMEOUT_SECONDS", "live_engine", "open_book", "signer_link", "start_problems"]
