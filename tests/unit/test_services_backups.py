from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from desk.core.paths import DeskPaths
from desk.ops.backups import backup_schedule_once, create_backup, restore_backup, verify_backup
from desk.ops.services import control_services, install_services, render_units
from desk.storage.databases import Databases


def paths_at(tmp_path: Path) -> DeskPaths:
    return DeskPaths(tmp_path / "desk data", tmp_path / "config", tmp_path / "private.env")


def test_units_escape_paths_limit_memory_and_preserve_guardian(tmp_path: Path) -> None:
    paths = DeskPaths(tmp_path / 'data "%x', tmp_path / "config", tmp_path / "private.env")
    units = render_units(paths, "/usr/local/bin/python")
    assert 'data \\"%%x' in units["desk-engine.service"]
    assert "MemoryMax=600M" in units["desk-engine.service"]
    assert "MemoryMax=200M" in units["desk-guardian.service"]
    assert "--what=idle:sleep" in units["desk-engine.service"]
    assert "PartOf=" not in "".join(units.values())
    assert "EnvironmentFile" not in "".join(units.values())
    assert "Restart=always" in units["desk-guardian.service"]
    assert "desk backups run" in units["desk-backup.service"]
    with pytest.raises(ValueError, match="one line"):
        render_units(paths, "/tmp/python\nExecStart=/evil")


def test_service_install_and_start_are_explicit(tmp_path: Path) -> None:
    with patch("desk.ops.services.subprocess.run") as run:
        installed = install_services(paths_at(tmp_path), "/usr/bin/python3", unit_dir=tmp_path / "units")
        assert len(installed) == 5
        assert all(path.stat().st_mode & 0o777 == 0o600 for path in installed)
        run.assert_called_once_with(["systemctl", "--user", "daemon-reload"], check=True)
        run.reset_mock()
        control_services("start", paper=True)
        assert run.call_args_list[0].args[0] == ["systemctl", "--user", "start", "desk-guardian.service"]
        run.reset_mock()
        control_services("stop", paper=True)
        assert "desk-guardian.service" not in run.call_args.args[0]
        assert "desk-engine.service" in run.call_args.args[0]
        with pytest.raises(ValueError):
            control_services("restart; shell")


def test_online_backup_captures_wal_and_restores_exact_cash(tmp_path: Path) -> None:
    paths = paths_at(tmp_path)
    dbs = Databases.open(paths)
    try:
        dbs.paper_ledger.execute("INSERT INTO cash_entries(at,kind,amount_lamports) VALUES ('now','DEPOSIT',1234)")
        manifest = create_backup(paths, ("ledger-paper",), datetime.now(UTC))
        payload = verify_backup(manifest)
        source_hash = payload["databases"]["ledger-paper"]["sha256"]
        dbs.paper_ledger.execute("INSERT INTO cash_entries(at,kind,amount_lamports) VALUES ('now','DEPOSIT',55)")
    finally:
        dbs.close()
    previous = restore_backup(manifest, paths, kind="ledger-paper", workers_active=lambda: False)
    assert previous is not None
    verify_backup(previous)
    assert verify_backup(manifest)["databases"]["ledger-paper"]["sha256"] == source_hash
    conn = sqlite3.connect(paths.db_dir / "ledger-paper.db")
    try:
        assert conn.execute("SELECT SUM(amount_lamports) FROM cash_entries").fetchone()[0] == 1234
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        conn.close()


def test_restore_rejects_active_workers_wrong_mode_and_corruption(tmp_path: Path) -> None:
    paths = paths_at(tmp_path)
    Databases.open(paths).close()
    manifest = create_backup(paths, ("ledger-paper",), datetime.now(UTC))
    with pytest.raises(RuntimeError, match="stop all workers"):
        restore_backup(manifest, paths, kind="ledger-paper", workers_active=lambda: True)
    with pytest.raises(ValueError, match="expected mode"):
        restore_backup(manifest, paths, kind="ledger-paper", expected_mode="LIVE", workers_active=lambda: False)
    source = manifest.parent / "ledger-paper.db"
    source.write_bytes(source.read_bytes() + b"corrupt")
    with pytest.raises(ValueError, match="checksum"):
        restore_backup(manifest, paths, kind="ledger-paper", workers_active=lambda: False)
    conn = sqlite3.connect(paths.db_dir / "ledger-paper.db")
    try:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        conn.close()


def test_restore_rechecks_worker_after_preparing(tmp_path: Path) -> None:
    paths = paths_at(tmp_path)
    Databases.open(paths).close()
    manifest = create_backup(paths, ("ledger-paper",), datetime.now(UTC))
    checks = iter([False, True])
    with pytest.raises(RuntimeError, match="restarted"):
        restore_backup(manifest, paths, kind="ledger-paper", workers_active=lambda: next(checks))


def test_scheduler_is_persistent_idempotent_and_rotates(tmp_path: Path) -> None:
    paths = paths_at(tmp_path)
    Databases.open(paths).close()
    now = datetime(2026, 10, 19, tzinfo=UTC)
    assert len(backup_schedule_once(paths, now, hourly_keep=2, daily_keep=1)["created"]) == 2
    assert backup_schedule_once(paths, now + timedelta(minutes=20))["created"] == []
    for increment in range(1, 4):
        backup_schedule_once(paths, now + timedelta(days=increment), hourly_keep=2, daily_keep=1)
    assert len(list((paths.backups_dir / "hourly").glob("*/manifest.json"))) == 2
    assert len(list((paths.backups_dir / "daily").glob("*/manifest.json"))) == 1
    assert json.loads((paths.backups_dir / "schedule.json").read_text())["daily"] == "2026-10-22"
    with pytest.raises(ValueError, match="retention"):
        backup_schedule_once(paths, now, hourly_keep=0)


def test_manifest_cannot_escape_backup_directory(tmp_path: Path) -> None:
    paths = paths_at(tmp_path)
    Databases.open(paths).close()
    manifest = create_backup(paths, ("ledger-paper",), datetime.now(UTC))
    payload = json.loads(manifest.read_text())
    payload["databases"]["ledger-paper"]["filename"] = "../../db/ledger-live.db"
    manifest.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="unknown database or path"):
        verify_backup(manifest)


def test_backup_failure_never_publishes_partial_archive(tmp_path: Path) -> None:
    paths = paths_at(tmp_path)
    Databases.open(paths).close()
    (paths.db_dir / "research.db").unlink()
    with pytest.raises(FileNotFoundError):
        create_backup(paths, ("ledger-paper", "research"), datetime.now(UTC))
    assert not list((paths.backups_dir / "manual").iterdir())


def test_even_checksum_matching_ledger_with_wrong_mode_is_refused(tmp_path: Path) -> None:
    paths = paths_at(tmp_path)
    Databases.open(paths).close()
    manifest = create_backup(paths, ("ledger-paper",), datetime.now(UTC))
    source = manifest.parent / "ledger-paper.db"
    conn = sqlite3.connect(source)
    try:
        conn.execute("UPDATE ledger_meta SET value='LIVE' WHERE key='mode'")
        conn.commit()
    finally:
        conn.close()
    payload = json.loads(manifest.read_text())
    payload["databases"]["ledger-paper"]["sha256"] = hashlib.sha256(source.read_bytes()).hexdigest()
    payload["databases"]["ledger-paper"]["bytes"] = source.stat().st_size
    manifest.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="mode mismatch"):
        verify_backup(manifest)
