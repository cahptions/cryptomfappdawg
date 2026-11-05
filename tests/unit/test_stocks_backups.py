"""The Stocks databases are backed up like the crypto ones: online (SQLite's backup API, WAL included), checksummed,
private, rotated, and in their own groups so one desk's trouble never stops the other's backups."""

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from desk.core.paths import DeskPaths
from desk.ops import backups
from desk.ops.backups import backup_schedule_once, create_backup, verify_backup
from desk.stocks import learning
from desk.stocks.repo import StocksDatabases
from desk.storage.databases import Databases

NOW = datetime(2026, 11, 5, 14, 10, tzinfo=UTC)


def stocks_and_crypto(tmp_path: Path) -> DeskPaths:
    paths = DeskPaths(tmp_path / "data", tmp_path / "config", tmp_path / "secrets.env")
    Databases.open(paths).close()
    StocksDatabases.open(paths).close()
    learning.open_book(paths).close()
    return paths


def test_every_stocks_database_is_copied_privately_and_verifies(tmp_path: Path) -> None:
    paths = stocks_and_crypto(tmp_path)
    made = backup_schedule_once(paths, NOW)["created"]
    assert {Path(m).parent.parent.name for m in made} == {"hourly", "daily", "stocks-hourly", "stocks-daily"}
    kinds = {Path(m).parent.parent.name: set(verify_backup(Path(m))["databases"]) for m in made}
    assert kinds["stocks-hourly"] == {"stocks-ledger-paper", "stocks-learning-paper"}     # the PAPER books, hourly
    assert kinds["stocks-daily"] == {"stocks-market", "stocks-research"}                  # collected history, daily
    for manifest in map(Path, made):
        assert manifest.parent.parent.stat().st_mode & 0o777 == 0o700
        assert all(f.stat().st_mode & 0o777 == 0o600 for f in manifest.parent.glob("*.db"))
    assert backup_schedule_once(paths, NOW + timedelta(minutes=20))["created"] == []         # once an hour / a day
    later = backup_schedule_once(paths, NOW + timedelta(hours=1))["created"]
    assert {Path(m).parent.parent.name for m in later} == {"hourly", "stocks-hourly"}


def test_an_online_copy_holds_committed_rows_while_the_collector_is_mid_write(tmp_path: Path) -> None:
    paths = stocks_and_crypto(tmp_path)
    writer = sqlite3.connect(paths.db_dir / "stocks-research.db", isolation_level=None)
    try:
        writer.execute("CREATE TABLE probe(x INTEGER)")
        writer.execute("INSERT INTO probe VALUES (1)")
        writer.execute("BEGIN IMMEDIATE")
        writer.execute("INSERT INTO probe VALUES (2)")                    # not committed while the copy is made
        manifest = create_backup(paths, ("stocks-research",), NOW)
        writer.execute("COMMIT")
    finally:
        writer.close()
    copy = sqlite3.connect(manifest.parent / "stocks-research.db")
    try:
        assert copy.execute("SELECT x FROM probe").fetchall() == [(1,)]
    finally:
        copy.close()


def test_a_stocks_book_that_is_not_paper_is_refused(tmp_path: Path) -> None:
    paths = stocks_and_crypto(tmp_path)
    book = sqlite3.connect(paths.db_dir / "stocks-learning-paper.db")
    try:
        book.execute("INSERT INTO ledger_meta(key, value) VALUES ('mode', 'LIVE')")
        book.commit()
    finally:
        book.close()
    with pytest.raises(ValueError, match="mode mismatch"):
        create_backup(paths, ("stocks-learning-paper",), NOW)


def test_a_damaged_stocks_database_never_stops_the_crypto_backups_and_is_retried(tmp_path: Path) -> None:
    paths = stocks_and_crypto(tmp_path)
    research = paths.db_dir / "stocks-research.db"
    for suffix in ("-wal", "-shm"):
        Path(str(research) + suffix).unlink(missing_ok=True)
    research.write_bytes(b"not a database" * 100)
    with pytest.raises(RuntimeError, match="stocks-daily: DatabaseError") as failure:
        backup_schedule_once(paths, NOW)
    assert "made:" in str(failure.value)
    state = json.loads((paths.backups_dir / "schedule.json").read_text())
    assert set(state) == {"hourly", "daily", "stocks-hourly"}                    # stocks-daily is tried next time
    assert not list((paths.backups_dir / "stocks-daily").glob("*/manifest.json"))   # nothing half-made published


def test_a_backup_that_would_fill_the_disk_is_skipped_and_reported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    paths = stocks_and_crypto(tmp_path)
    monkeypatch.setattr(backups.shutil, "disk_usage", lambda _: SimpleNamespace(free=100 * 1024 * 1024))
    with pytest.raises(RuntimeError, match="not enough free disk"):
        backup_schedule_once(paths, NOW)
    assert not (paths.backups_dir / "schedule.json").exists()


def test_stocks_daily_copies_are_rotated_to_three(tmp_path: Path) -> None:
    paths = stocks_and_crypto(tmp_path)
    for day in range(5):
        backup_schedule_once(paths, NOW + timedelta(days=day))
    assert len(list((paths.backups_dir / "stocks-daily").glob("*/manifest.json"))) == backups.STOCKS_DAILY_KEEP == 3
    assert len(list((paths.backups_dir / "daily").glob("*/manifest.json"))) == 5          # crypto keeps its seven
