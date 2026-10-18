"""The desk's set of databases, opened and migrated together."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from desk.core.config import Mode
from desk.core.paths import DeskPaths
from desk.storage.db import DbKind, StorageError, connect, integrity_check, journal_mode, migrate


def ledger_filename(mode: Mode) -> str:
    if mode is Mode.SHADOW:
        raise StorageError("SHADOW mode has no ledger: it never holds positions")
    return f"ledger-{mode.value.lower()}.db"


def open_ledger(db_dir: Path, mode: Mode) -> sqlite3.Connection:
    """Open the ledger for ``mode``; refuse if the file was created for a different mode."""
    conn = connect(db_dir / ledger_filename(mode), DbKind.LEDGER)
    migrate(conn, DbKind.LEDGER)
    row = conn.execute("SELECT value FROM ledger_meta WHERE key = 'mode'").fetchone()
    if row is None:
        conn.execute("INSERT INTO ledger_meta(key, value) VALUES ('mode', ?)", (mode.value,))
    elif row["value"] != mode.value:
        conn.close()
        raise StorageError(f"ledger file mode mismatch: file is {row['value']}, requested {mode.value}")
    return conn


@dataclass
class Databases:
    market: sqlite3.Connection
    research: sqlite3.Connection
    runtime: sqlite3.Connection
    paper_ledger: sqlite3.Connection
    paths: DeskPaths

    @classmethod
    def open(cls, paths: DeskPaths) -> Databases:
        paths.ensure()
        market = connect(paths.db_dir / "market.db", DbKind.MARKET)
        research = connect(paths.db_dir / "research.db", DbKind.RESEARCH)
        runtime = connect(paths.db_dir / "runtime.db", DbKind.RUNTIME)
        migrate(market, DbKind.MARKET)
        migrate(research, DbKind.RESEARCH)
        migrate(runtime, DbKind.RUNTIME)
        paper = open_ledger(paths.db_dir, Mode.PAPER)
        return cls(market=market, research=research, runtime=runtime, paper_ledger=paper, paths=paths)

    def all(self) -> dict[str, sqlite3.Connection]:
        return {"market": self.market, "research": self.research, "runtime": self.runtime,
                "ledger-paper": self.paper_ledger}

    def health(self) -> dict[str, dict[str, str]]:
        return {name: {"integrity": integrity_check(conn), "journal_mode": journal_mode(conn)}
                for name, conn in self.all().items()}

    def close(self) -> None:
        for conn in self.all().values():
            conn.close()
