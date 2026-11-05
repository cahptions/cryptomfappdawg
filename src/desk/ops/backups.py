"""Checksummed online SQLite snapshots and explicit, stopped-worker restore.

The Stocks databases are in the set too (since 2026-11): the always-on Stocks computer installs the same hourly
timer (desk.ops.services.keep_stocks_running), so its PAPER books and collected history are copied there even when
the crypto desks never run on it."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from desk.core.paths import DeskPaths

DATABASES = {"market": "market.db", "research": "research.db", "ledger-paper": "ledger-paper.db",
             "ledger-live": "ledger-live.db", "stocks-market": "stocks-market.db",
             "stocks-research": "stocks-research.db", "stocks-ledger-paper": "stocks-ledger-paper.db",
             "stocks-learning-paper": "stocks-learning-paper.db"}
STOCKS_BOOKS = ("stocks-ledger-paper", "stocks-learning-paper")     # PAPER only: any other recorded mode is refused
CADENCES = {"manual", "hourly", "daily", "stocks-hourly", "stocks-daily", "before-restore"}
STOCKS_DAILY_KEEP = 3       # stocks-market.db grows by gigabytes (bars, option chains): three days bound the disk


def _sync(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


@contextmanager
def _readonly(path: Path) -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", timeout=10, uri=True)
    try:
        yield conn
    finally:
        conn.close()


def _validate_database(path: Path, kind: str) -> None:
    with _readonly(path) as conn:
        if conn.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
            raise ValueError(f"database integrity failed: {kind}")
        if conn.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise ValueError(f"database foreign-key check failed: {kind}")
        if kind.startswith("ledger-"):
            row = conn.execute("SELECT value FROM ledger_meta WHERE key='mode'").fetchone()
            if row is None or row[0] != kind.removeprefix("ledger-").upper():
                raise ValueError("ledger mode mismatch")
        if kind in STOCKS_BOOKS:      # the learning book never writes a mode row; a recorded one must say PAPER
            row = conn.execute("SELECT value FROM ledger_meta WHERE key='mode'").fetchone()
            if row is not None and row[0] != "PAPER":
                raise ValueError("ledger mode mismatch")


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    fd, filename = tempfile.mkstemp(prefix=".state-", dir=path.parent)
    temporary = Path(filename)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, sort_keys=True, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
        _sync(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def create_backup(paths: DeskPaths, kinds: tuple[str, ...], now: datetime, *, cadence: str = "manual") -> Path:
    """Use SQLite's backup API, never copy a live main file without its WAL.

    Each database is copied in ONE step: a WAL reader never blocks the writer, and a stepwise copy starts over
    whenever another connection writes, which the Stocks collector does every few seconds, so a multi-gigabyte
    stocks-market.db copied 128 pages at a time might never finish."""
    if not kinds or len(set(kinds)) != len(kinds) or any(kind not in DATABASES for kind in kinds):
        raise ValueError("select unique known database kinds")
    if cadence not in CADENCES:
        raise ValueError("unknown backup cadence")
    if now.tzinfo is None:
        raise ValueError("backup time must have a timezone")
    folder = paths.backups_dir / cadence
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".pending-", dir=folder))
    entries: dict[str, Any] = {}
    try:
        for kind in kinds:
            source = paths.db_dir / DATABASES[kind]
            if not source.is_file():
                raise FileNotFoundError(source)
            target = temporary / DATABASES[kind]
            with _readonly(source) as connection:
                destination = sqlite3.connect(target)
                try:
                    connection.backup(destination, pages=-1)
                finally:
                    destination.close()
            target.chmod(0o600)
            _validate_database(target, kind)
            _sync(target)
            entries[kind] = {"filename": target.name, "sha256": _digest(target), "bytes": target.stat().st_size}
        _atomic_json(temporary / "manifest.json", {"schema": 1, "created_at": now.astimezone(UTC).isoformat(),
                                                  "cadence": cadence, "databases": entries})
        # The database files represent individual consistent snapshots, not a cross-database transaction.
        name = now.astimezone(UTC).strftime("%Y%m%dT%H%M%S.%fZ") + "-" + temporary.name.removeprefix(".pending-")
        completed = folder / name
        temporary.replace(completed)
        _sync(folder)
        return completed / "manifest.json"
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def verify_backup(manifest: Path) -> dict[str, Any]:
    payload: dict[str, Any] = json.loads(manifest.read_text(encoding="utf-8"))
    if payload.get("schema") != 1 or not isinstance(payload.get("databases"), dict) or not payload["databases"]:
        raise ValueError("unsupported or empty backup manifest")
    for kind, info in payload["databases"].items():
        if kind not in DATABASES or not isinstance(info, dict) or info.get("filename") != DATABASES[kind]:
            raise ValueError("backup contains an unknown database or path")
        database = manifest.parent / DATABASES[kind]
        if database.is_symlink() or not database.is_file():
            raise ValueError("backup database must be a regular file")
        if database.stat().st_size != info.get("bytes") or _digest(database) != info.get("sha256"):
            raise ValueError(f"backup checksum failed: {kind}")
        _validate_database(database, kind)
    return payload


def _rotate(paths: DeskPaths, cadence: str, keep: int) -> None:
    completed = sorted((paths.backups_dir / cadence).glob("*/manifest.json"), reverse=True)
    for manifest in completed[keep:]:
        shutil.rmtree(manifest.parent)


def _room_for(paths: DeskPaths, kinds: tuple[str, ...]) -> bool:
    """Twice the databases' size (the new copy, plus the oldest one until rotation removes it) and 512 MB must stay
    free: a backup that fills the disk would stop the desks themselves from writing."""
    size = sum(path.stat().st_size for kind in kinds for path in (paths.db_dir / DATABASES[kind],
               paths.db_dir / (DATABASES[kind] + "-wal")) if path.is_file())
    return shutil.disk_usage(paths.backups_dir).free >= 2 * size + 512 * 1024 * 1024


def backup_schedule_once(paths: DeskPaths, now: datetime, *, hourly_keep: int = 24, daily_keep: int = 7,
                         stocks_daily_keep: int = STOCKS_DAILY_KEEP) -> dict[str, Any]:
    """Idempotent UTC hour/day schedule, serialized across dashboard and timer processes.

    Crypto and Stocks are separate groups with their own folders and rotation, so one desk's missing or damaged
    database never stops the other's backups. Every group is tried and any failure is raised afterwards (the timer
    then shows failed, with the reason in its journal); a group that failed is tried again at the next run."""
    if any(type(keep) is not int for keep in (hourly_keep, daily_keep, stocks_daily_keep)) or not (
            1 <= hourly_keep <= 8760 and 1 <= daily_keep <= 366 and 1 <= stocks_daily_keep <= 366):
        raise ValueError("backup retention must be positive bounded integers")
    if now.tzinfo is None:
        raise ValueError("backup time must have a timezone")
    paths.backups_dir.mkdir(parents=True, exist_ok=True)
    created: list[str] = []
    failed: list[str] = []
    with (paths.backups_dir / ".schedule.lock").open("a") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        state_file = paths.backups_dir / "schedule.json"
        state = json.loads(state_file.read_text()) if state_file.exists() else {}
        current = now.astimezone(UTC)
        hour, day = current.strftime("%Y-%m-%dT%H"), current.strftime("%Y-%m-%d")
        groups = (("hourly", hour, ("ledger-paper", "ledger-live"), hourly_keep),
                  ("daily", day, ("market", "research"), daily_keep),
                  ("stocks-hourly", hour, STOCKS_BOOKS, hourly_keep),
                  ("stocks-daily", day, ("stocks-market", "stocks-research"), stocks_daily_keep))
        for cadence, period, candidates, keep in groups:
            if state.get(cadence) == period:
                continue
            kinds = tuple(kind for kind in candidates if (paths.db_dir / DATABASES[kind]).is_file())
            if not kinds:
                continue
            if not _room_for(paths, kinds):
                failed.append(f"{cadence}: not enough free disk for a copy of {', '.join(kinds)}")
                continue
            try:
                manifest = create_backup(paths, kinds, now, cadence=cadence)
            except Exception as exc:          # reported below; the other groups still run
                failed.append(f"{cadence}: {type(exc).__name__}: {exc}"[:300])
                continue
            created.append(str(manifest))
            state[cadence] = period
            _atomic_json(state_file, state)
            _rotate(paths, cadence, keep)
    if failed:
        made = f" (made: {', '.join(created)})" if created else ""
        raise RuntimeError("backups failed: " + "; ".join(failed) + made)
    return {"created": created, "hourly_keep": hourly_keep, "daily_keep": daily_keep,
            "stocks_daily_keep": stocks_daily_keep}


def restore_backup(manifest: Path, paths: DeskPaths, *, kind: str, expected_mode: str = "PAPER",
                   workers_active: Callable[[], bool]) -> Path | None:
    """Restore one verified role only after workers stop; preserve the previous file online first."""
    paths.backups_dir.mkdir(parents=True, exist_ok=True)
    with (paths.backups_dir / ".restore.lock").open("a") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        return _restore_backup(manifest, paths, kind=kind, expected_mode=expected_mode, workers_active=workers_active)


def _restore_backup(manifest: Path, paths: DeskPaths, *, kind: str, expected_mode: str,
                    workers_active: Callable[[], bool]) -> Path | None:
    if kind not in DATABASES:
        raise ValueError("unknown database role")
    if expected_mode not in {"PAPER", "LIVE"}:
        raise ValueError("an explicit PAPER or LIVE mode is required")
    if kind.startswith("ledger-") and kind != "ledger-" + expected_mode.lower():
        raise ValueError("requested ledger does not match expected mode")
    payload = verify_backup(manifest)
    if kind not in payload["databases"]:
        raise ValueError("backup does not contain the requested database")
    if workers_active():
        raise RuntimeError("stop all workers before restoring databases")
    paths.db_dir.mkdir(parents=True, exist_ok=True)
    target = paths.db_dir / DATABASES[kind]
    previous = create_backup(paths, (kind,), datetime.now(UTC), cadence="before-restore") if target.exists() else None
    fd, name = tempfile.mkstemp(prefix=".restore-", dir=paths.db_dir)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "wb") as output, (manifest.parent / DATABASES[kind]).open("rb") as source:
            shutil.copyfileobj(source, output)
            output.flush()
            os.fsync(output.fileno())
        if _digest(temporary) != payload["databases"][kind]["sha256"]:
            raise ValueError("backup changed while preparing restore")
        _validate_database(temporary, kind)
        if workers_active():
            raise RuntimeError("a worker restarted while preparing restore")
        if target.exists():
            conn = sqlite3.connect(target, timeout=10)
            try:
                if conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()[0] != 0:
                    raise RuntimeError("database still has an active writer")
            finally:
                conn.close()
        for suffix in ("-wal", "-shm"):
            Path(str(target) + suffix).unlink(missing_ok=True)
        temporary.replace(target)
        _sync(paths.db_dir)
        return previous
    finally:
        temporary.unlink(missing_ok=True)
