"""Separate Arbitrage database: immutable observations/events and replaceable operator preferences."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager, nullcontext
from typing import Any

from desk.arbitrage.contracts import InventoryEvent, Observation, Product, Route, Settings, SignalObservation, Source
from desk.core.hashing import canonical_json, sha256_hex
from desk.core.paths import DeskPaths
from desk.storage.db import DbKind, StorageError, connect, transaction

MAX_PRODUCTS = 2000


def isolated_paths(base: DeskPaths) -> DeskPaths:
    return DeskPaths(base.home / "arbitrage", base.config_dir, base.secrets_file.parent / "arbitrage-credentials.json")


DDL = """
CREATE TABLE IF NOT EXISTS arb_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS arb_products(id TEXT PRIMARY KEY,body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS arb_sources(id TEXT PRIMARY KEY,body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS arb_observations(id INTEGER PRIMARY KEY,product_id TEXT NOT NULL,source_id TEXT NOT NULL,
 at TEXT NOT NULL,body TEXT NOT NULL,input_hash TEXT NOT NULL UNIQUE);
CREATE INDEX IF NOT EXISTS arb_obs_product ON arb_observations(product_id,at);
CREATE TABLE IF NOT EXISTS arb_routes(id TEXT PRIMARY KEY,product_id TEXT NOT NULL,body TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS arb_route_product ON arb_routes(product_id);
CREATE TABLE IF NOT EXISTS arb_signals(id TEXT PRIMARY KEY,product_id TEXT NOT NULL,at TEXT NOT NULL,body TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS arb_signal_product ON arb_signals(product_id,at);
CREATE TABLE IF NOT EXISTS arb_inventory(id TEXT PRIMARY KEY,lot_id TEXT NOT NULL,product_id TEXT NOT NULL,
 at TEXT NOT NULL,body TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS arb_inventory_lot ON arb_inventory(lot_id,at);
CREATE INDEX IF NOT EXISTS arb_inventory_product ON arb_inventory(product_id);
CREATE TABLE IF NOT EXISTS arb_jobs(id TEXT PRIMARY KEY,body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS arb_decisions(input_hash TEXT PRIMARY KEY,at TEXT NOT NULL,body TEXT NOT NULL);
CREATE TRIGGER IF NOT EXISTS arb_signals_immutable_update BEFORE UPDATE ON arb_signals
 BEGIN SELECT RAISE(ABORT,'signal evidence is append-only'); END;
CREATE TRIGGER IF NOT EXISTS arb_obs_immutable_update BEFORE UPDATE ON arb_observations
 BEGIN SELECT RAISE(ABORT,'observations are append-only'); END;
CREATE TRIGGER IF NOT EXISTS arb_events_immutable_update BEFORE UPDATE ON arb_inventory
 BEGIN SELECT RAISE(ABORT,'inventory events are append-only'); END;
CREATE TRIGGER IF NOT EXISTS arb_events_immutable_delete BEFORE DELETE ON arb_inventory
 BEGIN SELECT RAISE(ABORT,'inventory events are append-only'); END;
"""


class Repo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    @classmethod
    @contextmanager
    def reader(cls, paths: DeskPaths) -> Iterator[Repo]:
        conn = sqlite3.connect((paths.db_dir / "arbitrage.db").as_uri() + "?mode=ro", uri=True, timeout=2)
        conn.row_factory = sqlite3.Row
        try:
            yield cls(conn)
        finally:
            conn.close()

    @classmethod
    @contextmanager
    def open(cls, paths: DeskPaths) -> Iterator[Repo]:
        paths.ensure()
        conn = connect(paths.db_dir / "arbitrage.db", DbKind.LEDGER)
        try:
            # An independent versioned schema; no migration or financial state of another desk is touched.
            checksum = sha256_hex(DDL)
            existing = conn.execute("SELECT name FROM sqlite_master WHERE name='arb_meta'").fetchone()
            if existing:
                row = conn.execute("SELECT value FROM arb_meta WHERE key='schema_v1'").fetchone()
                if row and row[0] != checksum:
                    raise StorageError("Arbitrage schema v1 changed; an additive migration is required")
            with transaction(conn):
                from desk.storage.db import _split_sql

                for statement in _split_sql(DDL):
                    conn.execute(statement)
                conn.execute("INSERT OR IGNORE INTO arb_meta VALUES ('schema_v1',?)", (checksum,))
            yield cls(conn)
        finally:
            conn.close()

    def settings(self) -> Settings:
        row = self.conn.execute("SELECT value FROM arb_meta WHERE key='settings'").fetchone()
        return Settings.model_validate_json(row[0]) if row else Settings()

    def save_settings(self, value: Settings) -> None:
        with nullcontext() if self.conn.in_transaction else transaction(self.conn):
            if value.currency != self.settings().currency and self.conn.execute("SELECT 1 FROM arb_inventory LIMIT 1").fetchone():
                raise StorageError("Inventory already exists; its currency cannot be relabeled or converted by a profile change")
            self.conn.execute("INSERT OR REPLACE INTO arb_meta VALUES ('settings',?)", (value.model_dump_json(),))

    def products(self) -> list[Product]:
        return [Product.model_validate_json(r[0]) for r in self.conn.execute("SELECT body FROM arb_products LIMIT 2000")]

    def save_product(self, product: Product, *, manual: bool = False) -> None:
        with nullcontext() if self.conn.in_transaction else transaction(self.conn):
            old = self.conn.execute("SELECT body FROM arb_products WHERE id=?", (product.id,)).fetchone()
            if not old and self.conn.execute("SELECT COUNT(*) FROM arb_products").fetchone()[0] >= MAX_PRODUCTS:
                victim = self.conn.execute("""
                    SELECT p.id FROM arb_products p WHERE p.id LIKE 'ebay-%'
                    AND json_extract(p.body,'$.identity_confirmed')=0
                    AND NOT EXISTS(SELECT 1 FROM arb_meta m WHERE m.key='protected:'||p.id)
                    AND NOT EXISTS(SELECT 1 FROM arb_routes r WHERE r.product_id=p.id)
                    AND NOT EXISTS(SELECT 1 FROM arb_inventory i WHERE i.product_id=p.id)
                    AND NOT EXISTS(SELECT 1 FROM arb_signals s WHERE s.product_id=p.id)
                    AND NOT EXISTS(SELECT 1 FROM arb_sources c WHERE json_extract(c.body,'$.product_id')=p.id)
                    AND NOT EXISTS(SELECT 1 FROM arb_observations o WHERE o.product_id=p.id
                                   AND json_extract(o.body,'$.method')!='ebay-browse-active-listing-v1')
                    ORDER BY (SELECT MAX(at) FROM arb_observations o WHERE o.product_id=p.id),p.id LIMIT 1
                """).fetchone()
                if victim is None:
                    raise StorageError("Protected catalog capacity reached; no reviewed or owned product was deleted")
                self.conn.execute("DELETE FROM arb_observations WHERE product_id=?", (victim[0],))
                self.conn.execute("DELETE FROM arb_products WHERE id=?", (victim[0],))
            if manual:
                self.conn.execute("INSERT OR IGNORE INTO arb_meta VALUES (?,?)", ("protected:" + product.id, "1"))
            if old:
                previous = Product.model_validate_json(old[0])
                changed = any(
                    getattr(previous, key) != getattr(product, key)
                    for key in ("brand", "mpn", "gtin", "variant", "pack_count", "condition")
                )
                if changed:
                    for row in self.conn.execute("SELECT body FROM arb_routes WHERE product_id=?", (product.id,)).fetchall():
                        route = Route.model_validate_json(row[0]).model_copy(
                            update={"identity_confirmed": False, "reviewed": False}
                        )
                        self.save_route(route)
            self.conn.execute("INSERT OR REPLACE INTO arb_products VALUES (?,?)", (product.id, product.model_dump_json()))

    def sources(self) -> list[Source]:
        return [Source.model_validate_json(r[0]) for r in self.conn.execute("SELECT body FROM arb_sources LIMIT 100")]

    def save_source(self, source: Source) -> None:
        if len(self.sources()) >= 100 and not any(s.id == source.id for s in self.sources()):
            raise StorageError("At most 100 source URLs may be tracked in this local version")
        self.conn.execute("INSERT OR REPLACE INTO arb_sources VALUES (?,?)", (source.id, source.model_dump_json()))

    def observe(self, value: Observation) -> None:
        body = value.model_dump_json()
        with transaction(self.conn):
            if not self.conn.execute("SELECT 1 FROM arb_products WHERE id=?", (value.product.id,)).fetchone():
                self.save_product(value.product)
            self.conn.execute(
                "INSERT OR IGNORE INTO arb_observations(product_id,source_id,at,body,input_hash) VALUES (?,?,?,?,?)",
                (
                    value.product.id,
                    value.source_id,
                    value.at.isoformat(),
                    body,
                    sha256_hex(canonical_json(value.model_dump(mode="json"))),
                ),
            )

    def observations(self, product_id: str | None = None, *, limit: int = 5000) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT id,body,input_hash FROM arb_observations WHERE (? IS NULL OR product_id=?) ORDER BY at DESC,id DESC LIMIT ?",
            (product_id, product_id, min(5000, limit)),
        )
        return [{"id": r[0], "input_hash": r[2], **json.loads(r[1])} for r in rows]

    def save_route(self, value: Route) -> None:
        if not self.conn.execute("SELECT 1 FROM arb_products WHERE id=?", (value.product_id,)).fetchone():
            raise StorageError("Record the product before its selling scenario")
        if not self.conn.execute("SELECT 1 FROM arb_routes WHERE id=?", (value.id,)).fetchone() and (
            self.conn.execute("SELECT COUNT(*) FROM arb_routes").fetchone()[0] >= 2000
        ):
            raise StorageError("This version supports at most 2000 selling routes")
        self.conn.execute(
            "INSERT OR REPLACE INTO arb_routes VALUES (?,?,?)", (value.id, value.product_id, value.model_dump_json())
        )

    def routes(self) -> list[Route]:
        return [Route.model_validate_json(r[0]) for r in self.conn.execute("SELECT body FROM arb_routes LIMIT 2000")]

    def add_signal(self, value: SignalObservation) -> bool:
        previous = self.conn.execute("SELECT body FROM arb_signals WHERE id=?", (value.id,)).fetchone()
        if previous:
            if previous[0] != value.model_dump_json():
                raise StorageError("Signal identifier already exists with different evidence")
            return False
        if self.conn.execute("SELECT COUNT(*) FROM arb_signals").fetchone()[0] >= 20000:
            raise StorageError("Signal limit reached; refusing to silently truncate heat evidence")
        self.conn.execute(
            "INSERT INTO arb_signals VALUES (?,?,?,?)",
            (value.id, value.product_id, value.at.isoformat(), value.model_dump_json()),
        )
        return True

    def signals(self) -> list[SignalObservation]:
        return [
            SignalObservation.model_validate_json(r[0])
            for r in self.conn.execute("SELECT body FROM arb_signals ORDER BY at DESC LIMIT 20000")
        ]

    def events(self, lot_id: str | None = None) -> list[InventoryEvent]:
        rows = self.conn.execute(
            "SELECT body FROM arb_inventory WHERE (? IS NULL OR lot_id=?) ORDER BY rowid LIMIT 20001", (lot_id, lot_id)
        ).fetchall()
        if len(rows) > 20000:
            raise StorageError("Inventory history limit reached; refusing a partial balance")
        return [InventoryEvent.model_validate_json(r[0]) for r in rows]

    def add_event(self, event: InventoryEvent) -> bool:
        from desk.arbitrage.inventory import ledger

        with transaction(self.conn):
            if event.currency != self.settings().currency:
                raise StorageError("Inventory event currency differs from this business profile; no conversion is supported")
            row = self.conn.execute("SELECT body FROM arb_inventory WHERE id=?", (event.id,)).fetchone()
            if row:
                if row[0] != event.model_dump_json():
                    raise StorageError("Inventory event identifier already exists with different contents")
                return False
            events = self.events()
            if not self.conn.execute("SELECT 1 FROM arb_products WHERE id=?", (event.product_id,)).fetchone():
                raise StorageError("Record the product before its inventory")
            if len(events) >= 20000:
                raise StorageError("Inventory event limit reached; archive with a reviewed migration before continuing")
            ledger([*events, event])  # validate the entire prospective state before committing
            self.conn.execute(
                "INSERT INTO arb_inventory VALUES (?,?,?,?,?)",
                (event.id, event.lot_id, event.product_id, event.at.isoformat(), event.model_dump_json()),
            )
            return True

    def job(self, name: str, value: dict[str, Any]) -> None:
        self.conn.execute("INSERT OR REPLACE INTO arb_jobs VALUES (?,?)", (name, canonical_json(value)))

    def jobs(self) -> dict[str, Any]:
        return {r[0]: json.loads(r[1]) for r in self.conn.execute("SELECT id,body FROM arb_jobs LIMIT 100")}

    def archive_decisions(self, now: str, record: dict[str, Any]) -> None:
        digest = sha256_hex(canonical_json({k: v for k, v in record.items() if k != "decision_at"}))
        self.conn.execute("INSERT OR IGNORE INTO arb_decisions VALUES (?,?,?)", (digest, now, canonical_json(record)))


__all__ = ["Repo", "isolated_paths"]
