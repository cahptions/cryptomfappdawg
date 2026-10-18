"""SQLite connections and migrations (standard library only).

Each database kind has a folder of numbered ``.sql`` files under ``storage/migrations/<kind>/``. Applied files
are recorded with their SHA-256; if an already-applied file is edited later, the desk refuses to start
(history must never be rewritten — add a new numbered migration instead).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from desk.core.clock import to_iso, utc_now
from desk.core.errors import DeskError
from desk.core.hashing import sha256_hex

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


class DbKind(StrEnum):
    MARKET = "market"
    LEDGER = "ledger"
    RESEARCH = "research"
    RUNTIME = "runtime"


class StorageError(DeskError):
    pass


# The money ledger gets full fsync on every commit (survives power loss); the others survive process crashes.
_SYNCHRONOUS = {DbKind.MARKET: "NORMAL", DbKind.LEDGER: "FULL", DbKind.RESEARCH: "NORMAL", DbKind.RUNTIME: "NORMAL"}


def connect(path: Path, kind: DbKind, *, check_same_thread: bool = True) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10.0, isolation_level=None, check_same_thread=check_same_thread)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(f"PRAGMA synchronous={_SYNCHRONOUS[kind]}")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    sql: str

    @property
    def checksum(self) -> str:
        return sha256_hex(self.sql)


def available_migrations(kind: DbKind) -> list[Migration]:
    folder = MIGRATIONS_DIR / kind.value
    migrations = []
    for file in sorted(folder.glob("*.sql")):
        version_text, _, name = file.stem.partition("_")
        migrations.append(Migration(version=int(version_text), name=name, sql=file.read_text(encoding="utf-8")))
    versions = [m.version for m in migrations]
    if versions != list(range(1, len(versions) + 1)):
        raise StorageError(f"{kind.value} migrations must be numbered 0001, 0002, … without gaps: {versions}")
    return migrations


def migrate(conn: sqlite3.Connection, kind: DbKind) -> list[int]:
    """Apply pending migrations in order; return the versions applied now."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        " version INTEGER PRIMARY KEY, name TEXT NOT NULL, checksum TEXT NOT NULL, applied_at TEXT NOT NULL)"
    )
    applied = {row["version"]: row["checksum"] for row in conn.execute("SELECT version, checksum FROM schema_migrations")}
    newly_applied: list[int] = []
    for migration in available_migrations(kind):
        if migration.version in applied:
            if applied[migration.version] != migration.checksum:
                raise StorageError(
                    f"{kind.value} migration {migration.version:04d}_{migration.name} was modified after being "
                    "applied. Never edit applied migrations; add a new one."
                )
            continue
        conn.execute("BEGIN IMMEDIATE")
        try:
            for statement in _split_sql(migration.sql):
                conn.execute(statement)
            conn.execute(
                "INSERT INTO schema_migrations(version, name, checksum, applied_at) VALUES (?, ?, ?, ?)",
                (migration.version, migration.name, migration.checksum, to_iso(utc_now())),
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        newly_applied.append(migration.version)
    return newly_applied


def _split_sql(script: str) -> list[str]:
    """Split a migration into statements; ``sqlite3.complete_statement`` handles triggers (BEGIN…END)."""
    statements: list[str] = []
    buffer = ""
    for line in script.splitlines(keepends=True):
        if not buffer and line.strip().startswith("--"):
            continue
        buffer += line
        if sqlite3.complete_statement(buffer):
            if buffer.strip():
                statements.append(buffer.strip())
            buffer = ""
    if buffer.strip():
        raise StorageError(f"incomplete SQL statement in migration: {buffer.strip()[:80]}")
    return statements


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """``BEGIN IMMEDIATE`` … ``COMMIT`` (or ``ROLLBACK`` on any exception)."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def integrity_check(conn: sqlite3.Connection) -> str:
    """Return ``"ok"`` or the first integrity problem SQLite reports."""
    row = conn.execute("PRAGMA integrity_check").fetchone()
    return str(row[0])


def journal_mode(conn: sqlite3.Connection) -> str:
    return str(conn.execute("PRAGMA journal_mode").fetchone()[0])


def backup(conn: sqlite3.Connection, destination: Path) -> Path:
    """Consistent online copy using SQLite's backup API (safe while other processes write)."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    tmp = destination.with_suffix(destination.suffix + ".tmp")
    target = sqlite3.connect(tmp)
    try:
        conn.backup(target)
    finally:
        target.close()
    tmp.replace(destination)
    return destination
