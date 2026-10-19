"""Checksummed online SQLite snapshots and explicit, stopped-worker restore."""

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
             "ledger-live": "ledger-live.db"}


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
    """Use SQLite's backup API, never copy a live main file without its WAL."""
    if not kinds or len(set(kinds)) != len(kinds) or any(kind not in DATABASES for kind in kinds):
        raise ValueError("select unique known database kinds")
    if cadence not in {"manual", "hourly", "daily", "before-restore"}:
        raise ValueError("unknown backup cadence")
    if now.tzinfo is None:
        raise ValueError("backup time must have a timezone")
    folder = paths.backups_dir / cadence
    folder.mkdir(parents=True, exist_ok=True)
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
                    connection.backup(destination, pages=128)
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


def backup_schedule_once(paths: DeskPaths, now: datetime, *, hourly_keep: int = 24, daily_keep: int = 7) -> dict[str, Any]:
    """Idempotent UTC hour/day schedule, serialized across dashboard and timer processes."""
    if type(hourly_keep) is not int or type(daily_keep) is not int or not 1 <= hourly_keep <= 8760 or not 1 <= daily_keep <= 366:
        raise ValueError("backup retention must be positive bounded integers")
    if now.tzinfo is None:
        raise ValueError("backup time must have a timezone")
    paths.backups_dir.mkdir(parents=True, exist_ok=True)
    created: list[str] = []
    with (paths.backups_dir / ".schedule.lock").open("a") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        state_file = paths.backups_dir / "schedule.json"
        state = json.loads(state_file.read_text()) if state_file.exists() else {}
        current = now.astimezone(UTC)
        groups = (("hourly", current.strftime("%Y-%m-%dT%H"), ("ledger-paper", "ledger-live"), hourly_keep),
                  ("daily", current.strftime("%Y-%m-%d"), ("market", "research"), daily_keep))
        for cadence, period, candidates, keep in groups:
            if state.get(cadence) == period:
                continue
            kinds = tuple(kind for kind in candidates if (paths.db_dir / DATABASES[kind]).is_file())
            if kinds:
                manifest = create_backup(paths, kinds, now, cadence=cadence)
                created.append(str(manifest))
                state[cadence] = period
                _atomic_json(state_file, state)
                _rotate(paths, cadence, keep)
    return {"created": created, "hourly_keep": hourly_keep, "daily_keep": daily_keep}


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
