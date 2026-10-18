"""Repository for market.db. All SQL for observations lives here (swappable for Postgres later)."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from datetime import datetime
from decimal import Decimal

from desk.contracts.market import PoolSnapshot
from desk.contracts.observed import Observed
from desk.contracts.quotes import QuoteObservation, QuoteUnavailable
from desk.contracts.tokens import MintAccount, TokenClaims
from desk.core.clock import to_epoch_millis, to_iso, utc_now
from desk.storage.db import transaction


def _dec(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def _obs(observed: Observed[Decimal]) -> tuple[str | None, str]:
    return _dec(observed.value), observed.status.value


class MarketRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    # ── pools & snapshots ──────────────────────────────────────────────────────────────────────────
    def save_pool_snapshots(self, snapshots: Iterable[PoolSnapshot]) -> int:
        count = 0
        with transaction(self.conn):
            for snap in snapshots:
                self._upsert_token(snap.base_mint, snap.observed_at, snap.base_symbol, snap.name)
                if snap.quote_mint:
                    self._upsert_token(snap.quote_mint, snap.observed_at, None, None)
                self._upsert_pool(snap)
                self._insert_snapshot(snap)
                count += 1
        return count

    def _upsert_token(self, mint: str, seen_at: datetime, symbol: str | None, name: str | None) -> None:
        iso = to_iso(seen_at)
        self.conn.execute(
            "INSERT INTO tokens(mint, first_seen_at, last_seen_at, symbol, name) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(mint) DO UPDATE SET last_seen_at = MAX(last_seen_at, excluded.last_seen_at), "
            "symbol = COALESCE(excluded.symbol, symbol), name = COALESCE(excluded.name, name)",
            (mint, iso, iso, symbol, name),
        )

    def _upsert_pool(self, snap: PoolSnapshot) -> None:
        iso = to_iso(snap.observed_at)
        self.conn.execute(
            "INSERT INTO pools(address, base_mint, quote_mint, venue, venue_raw, pool_created_at, first_seen_at,"
            " first_seen_source, last_seen_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(address) DO UPDATE SET last_seen_at = MAX(last_seen_at, excluded.last_seen_at), "
            "pool_created_at = COALESCE(pool_created_at, excluded.pool_created_at)",
            (snap.pool_address, snap.base_mint, snap.quote_mint, snap.venue, snap.venue_raw,
             None if snap.pool_created_at is None else to_iso(snap.pool_created_at), iso, snap.source, iso),
        )

    def _insert_snapshot(self, snap: PoolSnapshot) -> None:
        windows = {k: v.model_dump(mode="json", exclude_none=True) for k, v in snap.windows.items()}
        price, price_status = _obs(snap.price_usd)
        native, native_status = _obs(snap.price_native)
        liq, liq_status = _obs(snap.liquidity_usd)
        fdv, fdv_status = _obs(snap.fdv_usd)
        mcap, mcap_status = _obs(snap.market_cap_usd)
        self.conn.execute(
            "INSERT INTO market_snapshots(pool_address, base_mint, source, observed_at, observed_at_ms,"
            " price_usd, price_usd_status, price_native, price_native_status, liquidity_usd, liquidity_status,"
            " fdv_usd, fdv_status, market_cap_usd, market_cap_status, locked_liquidity_pct, windows_json, flags,"
            " raw_sha256) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (snap.pool_address, snap.base_mint, snap.source, to_iso(snap.observed_at), to_epoch_millis(snap.observed_at),
             price, price_status, native, native_status, liq, liq_status, fdv, fdv_status, mcap, mcap_status,
             _dec(snap.locked_liquidity_pct), json.dumps(windows, sort_keys=True), ",".join(snap.flags),
             snap.raw_sha256),
        )

    # ── token claims, quotes, mint accounts ────────────────────────────────────────────────────────
    def save_token_claims(self, claims: Iterable[TokenClaims]) -> int:
        rows = [
            (c.mint, c.source, to_iso(c.observed_at), to_epoch_millis(c.observed_at),
             c.model_dump_json(exclude={"raw_sha256", "observed_at", "source", "mint"}), c.raw_sha256)
            for c in claims
        ]
        with transaction(self.conn):
            self.conn.executemany(
                "INSERT INTO token_claims(mint, source, observed_at, observed_at_ms, claims_json, raw_sha256)"
                " VALUES (?, ?, ?, ?, ?, ?)", rows,
            )
        return len(rows)

    def save_quote(self, quote: QuoteObservation | QuoteUnavailable) -> None:
        if isinstance(quote, QuoteObservation):
            outcome, out_amount = "quote", str(quote.out_amount_raw)
        else:
            outcome, out_amount = "unavailable", None
        self.conn.execute(
            "INSERT INTO quotes(source, outcome, input_mint, output_mint, in_amount_raw, out_amount_raw, received_at,"
            " received_at_ms, payload_json, raw_sha256) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (quote.source, outcome, quote.input_mint, quote.output_mint, str(quote.in_amount_raw), out_amount,
             to_iso(quote.received_at), to_epoch_millis(quote.received_at), quote.model_dump_json(), quote.raw_sha256),
        )

    def save_mint_account(self, mint: MintAccount) -> None:
        self.conn.execute(
            "INSERT INTO mint_accounts(mint, slot, observed_at, token_program, decimals, supply_raw, mint_authority,"
            " freeze_authority, extensions_json, raw_sha256) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (mint.mint, mint.slot, to_iso(mint.observed_at), mint.token_program, mint.decimals, str(mint.supply_raw),
             mint.mint_authority, mint.freeze_authority,
             json.dumps([e.model_dump(mode="json") for e in mint.extensions], sort_keys=True), mint.raw_sha256),
        )

    def record_ingest_errors(self, source: str, errors: Iterable[str], raw_sha256: str | None) -> int:
        now = to_iso(utc_now())
        rows = [(source, now, err[:2000], raw_sha256) for err in errors]
        with transaction(self.conn):
            self.conn.executemany("INSERT INTO ingest_errors(source, at, error, raw_sha256) VALUES (?, ?, ?, ?)",
                                  rows)
        return len(rows)

    # ── queries used by the collector and doctor ───────────────────────────────────────────────────
    def counts(self) -> dict[str, int]:
        tables = ("tokens", "pools", "market_snapshots", "token_claims", "quotes", "mint_accounts", "raw_responses",
                  "ingest_errors")
        return {t: int(self.conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]) for t in tables}  # noqa: S608
