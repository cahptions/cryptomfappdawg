"""M1-13 (databases, migrations, integrity, WAL), M1-17 (raw evidence), repository behaviour."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from desk.core.config import Mode
from desk.core.paths import DeskPaths
from desk.providers import dexscreener, geckoterminal
from desk.storage import db as dbmod
from desk.storage.databases import Databases, open_ledger
from desk.storage.db import DbKind, StorageError, connect, migrate
from desk.storage.market_repo import MarketRepository
from desk.storage.raw_store import RawStore
from tests.conftest import load_fixture

NOW = datetime(2026, 10, 18, 22, 0, tzinfo=UTC)


@pytest.fixture
def dbs(tmp_path: Path) -> Databases:
    paths = DeskPaths(home=tmp_path / "home", config_dir=tmp_path, secrets_file=tmp_path / "s.env")
    databases = Databases.open(paths)
    yield databases
    databases.close()


def test_all_databases_built_from_empty_with_wal_and_integrity_ok(dbs: Databases) -> None:
    health = dbs.health()
    assert set(health) == {"market", "research", "runtime", "ledger-paper"}
    for name, info in health.items():
        assert info["integrity"] == "ok", name
        assert info["journal_mode"] == "wal", name


def test_migrations_are_idempotent(dbs: Databases) -> None:
    assert migrate(dbs.market, DbKind.MARKET) == []


def test_modified_applied_migration_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    conn = connect(tmp_path / "m.db", DbKind.RESEARCH)
    migrate(conn, DbKind.RESEARCH)
    original = dbmod.available_migrations

    def tampered(kind: DbKind) -> list[dbmod.Migration]:
        return [dbmod.Migration(m.version, m.name, m.sql + "\n-- edited") for m in original(kind)]

    monkeypatch.setattr(dbmod, "available_migrations", tampered)
    with pytest.raises(StorageError, match="modified after being applied"):
        migrate(conn, DbKind.RESEARCH)


def test_experiment_registry_is_append_only(dbs: Databases) -> None:
    dbs.research.execute("INSERT INTO experiments(id, created_at, kind, payload_json) VALUES ('e1', 'x', 'k', '{}')")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        dbs.research.execute("UPDATE experiments SET kind = 'changed'")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        dbs.research.execute("DELETE FROM experiments")


def test_ledger_files_are_separate_per_mode_and_mode_checked(tmp_path: Path) -> None:
    paper = open_ledger(tmp_path, Mode.PAPER)
    paper.close()
    assert (tmp_path / "ledger-paper.db").exists() and not (tmp_path / "ledger-live.db").exists()
    (tmp_path / "ledger-paper.db").rename(tmp_path / "ledger-live.db")
    with pytest.raises(StorageError, match="mode mismatch"):
        open_ledger(tmp_path, Mode.LIVE)
    with pytest.raises(StorageError, match="SHADOW mode has no ledger"):
        open_ledger(tmp_path, Mode.SHADOW)


def test_raw_store_round_trip_is_byte_identical(dbs: Databases) -> None:
    store = RawStore(dbs.paths.raw_dir, dbs.market)
    body = load_fixture("provider/geckoterminal_new_pools_p1.json")
    digest = store.put("geckoterminal", "new_pools", 200, body)
    assert store.get(digest) == body
    assert store.put("geckoterminal", "new_pools", 200, body) == digest   # dedup by content
    assert dbs.market.execute("SELECT COUNT(*) FROM raw_responses").fetchone()[0] == 1


def test_raw_store_detects_corruption(dbs: Databases) -> None:
    store = RawStore(dbs.paths.raw_dir, dbs.market)
    digest = store.put("p", "e", 200, b'{"a": 1}')
    path = dbs.paths.raw_dir / digest[:2] / f"{digest}.gz"
    import gzip
    path.write_bytes(gzip.compress(b'{"a": 2}'))
    with pytest.raises(ValueError, match="corrupted"):
        store.get(digest)


def test_raw_store_retention_keeps_decision_linked(dbs: Databases) -> None:
    store = RawStore(dbs.paths.raw_dir, dbs.market)
    old = NOW - timedelta(days=30)
    a = store.put("p", "e", 200, b"old-unlinked", received_at=old)
    b = store.put("p", "e", 200, b"old-linked", received_at=old)
    c = store.put("p", "e", 200, b"fresh", received_at=NOW)
    store.mark_keep(b)
    assert store.purge(retention_days=14, now=NOW) == 1
    with pytest.raises(FileNotFoundError):
        store.get(a)
    assert store.get(b) == b"old-linked" and store.get(c) == b"fresh"


def test_repository_stores_snapshots_with_missing_kept_missing(dbs: Databases) -> None:
    repo = MarketRepository(dbs.market)
    ds = dexscreener.parse_pairs(load_fixture("provider/dexscreener_tokens_v1_10mints.json"), "ds", NOW).items
    gt = geckoterminal.parse_pools(load_fixture("provider/geckoterminal_new_pools_p1.json"), "gt", NOW).items
    assert repo.save_pool_snapshots(ds + gt) == len(ds) + len(gt)
    missing = dbs.market.execute(
        "SELECT COUNT(*) FROM market_snapshots WHERE source = 'ds' AND liquidity_usd IS NULL AND liquidity_status='missing'"
    ).fetchone()[0]
    assert missing == len(ds)
    zero_as_number = dbs.market.execute("SELECT COUNT(*) FROM market_snapshots WHERE liquidity_usd = '0'").fetchone()[0]
    assert zero_as_number == 0  # no unknown value was ever written as 0


def test_check_constraint_blocks_value_status_mismatch(dbs: Databases) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        dbs.market.execute(
            "INSERT INTO market_snapshots(pool_address, base_mint, source, observed_at, observed_at_ms, price_usd,"
            " price_usd_status, price_native_status, liquidity_usd, liquidity_status, fdv_status, market_cap_status,"
            " windows_json, flags) VALUES ('p','m','s','t',0,NULL,'ok','missing','0','missing','missing','missing','{}','')"
        )


def test_pool_first_seen_is_not_overwritten(dbs: Databases) -> None:
    repo = MarketRepository(dbs.market)
    snaps = geckoterminal.parse_pools(load_fixture("provider/geckoterminal_new_pools_p1.json"), "gt", NOW).items
    repo.save_pool_snapshots(snaps[:1])
    later = geckoterminal.parse_pools(load_fixture("provider/geckoterminal_new_pools_p1.json"), "gt",
                                      NOW + timedelta(hours=1)).items
    repo.save_pool_snapshots(later[:1])
    row = dbs.market.execute("SELECT first_seen_at, last_seen_at FROM pools WHERE address = ?",
                             (snaps[0].pool_address,)).fetchone()
    assert row["first_seen_at"] < row["last_seen_at"]


def test_backup_produces_identical_consistent_copy(dbs: Databases, tmp_path: Path) -> None:
    dbs.paper_ledger.execute("INSERT INTO system_state(key, value, updated_at) VALUES ('entries_halted', 'no', 'x')")
    target = dbmod.backup(dbs.paper_ledger, tmp_path / "backup" / "ledger-paper.db")
    copy = sqlite3.connect(target)
    assert copy.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert copy.execute("SELECT value FROM system_state WHERE key='entries_halted'").fetchone()[0] == "no"
    copy.close()


def test_upgrade_from_previous_schema_keeps_existing_rows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An M1-v1 database (only migration 0001) upgrades to the current schema without losing data."""
    conn = connect(tmp_path / "market.db", DbKind.MARKET)
    original = dbmod.available_migrations
    monkeypatch.setattr(dbmod, "available_migrations", lambda kind: original(kind)[:1])
    migrate(conn, DbKind.MARKET)
    conn.execute("INSERT INTO tokens(mint, first_seen_at, last_seen_at) VALUES ('m', 'a', 'b')")
    monkeypatch.setattr(dbmod, "available_migrations", original)
    assert migrate(conn, DbKind.MARKET) == [2, 3, 4, 5, 6, 7]
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(market_snapshots)")}
    assert {"liquidity_base_amount", "liquidity_quote_amount"} <= columns
    assert conn.execute("SELECT COUNT(*) FROM tokens").fetchone()[0] == 1


def test_reserve_amounts_stored_and_null_when_not_reported(dbs: Databases) -> None:
    repo = MarketRepository(dbs.market)
    drained = dexscreener.parse_pairs(load_fixture("provider/m1_dexscreener_tokens_v1_drained_pumpswap_pools.json"),
                                      "ds_drained", NOW).items
    curve = dexscreener.parse_pairs(load_fixture("provider/dexscreener_tokens_v1_10mints.json"), "ds_curve", NOW).items
    repo.save_pool_snapshots(drained + curve)
    # reserves are present exactly when the provider reported liquidity (incl. the drained pool's explicit 0)
    mismatched = dbs.market.execute(
        "SELECT COUNT(*) FROM market_snapshots WHERE (liquidity_status = 'missing') != (liquidity_quote_amount IS NULL)"
    ).fetchone()[0]
    assert mismatched == 0
    reported = dbs.market.execute("SELECT COUNT(*) FROM market_snapshots WHERE source='ds_drained'"
                                  " AND liquidity_quote_amount IS NOT NULL").fetchone()[0]
    assert reported == sum(1 for s in drained if s.liquidity_usd.status.value == "ok") > 0
    assert dbs.market.execute("SELECT COUNT(*) FROM market_snapshots WHERE source='ds_curve'"
                              " AND liquidity_quote_amount IS NULL").fetchone()[0] == len(curve)
