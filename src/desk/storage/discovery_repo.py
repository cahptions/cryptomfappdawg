"""Discovery identities, append-only stage evidence, and resumable collector backfill."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from decimal import Decimal
from typing import Any

from desk.contracts.discovery import DiscoveryReason, SecurityAssessment, StageResult
from desk.contracts.market import PoolSnapshot, WindowMetrics
from desk.contracts.observed import DataStatus, Observed
from desk.contracts.tokens import TokenClaims
from desk.core.base58 import is_solana_address
from desk.core.clock import parse_iso8601 as parse_iso
from desk.core.clock import to_iso
from desk.core.config import DiscoverySection
from desk.core.hashing import canonical_json, fingerprint
from desk.discovery.classify import classify
from desk.storage.db import transaction


def snapshot_from_row(row: sqlite3.Row) -> PoolSnapshot:
    source, at = row["source"], parse_iso(row["observed_at"])

    def obs(column: str, status: str) -> Observed[Decimal]:
        return Observed[Decimal](value=None if row[column] is None else Decimal(row[column]),
                                 status=DataStatus(row[status]), source=source, observed_at=at)

    return PoolSnapshot(
        source=source, pool_address=row["pool_address"], base_mint=row["base_mint"],
        quote_mint=row["quote_mint"], venue=row["venue"], venue_raw=row["venue_raw"],
        name=row["name"], base_symbol=row["symbol"], pool_created_at=(
            None if row["pool_created_at"] is None else parse_iso(row["pool_created_at"])),
        price_usd=obs("price_usd", "price_usd_status"), price_native=obs("price_native", "price_native_status"),
        liquidity_usd=obs("liquidity_usd", "liquidity_status"), fdv_usd=obs("fdv_usd", "fdv_status"),
        market_cap_usd=obs("market_cap_usd", "market_cap_status"),
        liquidity_base_amount=None if row["liquidity_base_amount"] is None else Decimal(row["liquidity_base_amount"]),
        liquidity_quote_amount=None if row["liquidity_quote_amount"] is None else Decimal(row["liquidity_quote_amount"]),
        locked_liquidity_pct=None if row["locked_liquidity_pct"] is None else Decimal(row["locked_liquidity_pct"]),
        windows={k: WindowMetrics.model_validate(v) for k, v in json.loads(row["windows_json"]).items()},
        flags=tuple(filter(None, row["flags"].split(","))), observed_at=at, raw_sha256=row["raw_sha256"],
    )


_SNAPSHOT_SELECT = (
    "SELECT s.*, p.quote_mint, p.venue, p.venue_raw, p.pool_created_at, t.name, t.symbol "
    "FROM market_snapshots s JOIN pools p ON p.address = s.pool_address JOIN tokens t ON t.mint = s.base_mint "
)


class DiscoveryRepository:
    def __init__(self, conn: sqlite3.Connection, settings: DiscoverySection) -> None:
        self.conn, self.settings = conn, settings

    def observe(self, mint: str, pool: str | None, source: str, at: datetime, *,
                reason: DiscoveryReason | None = None, token_created_at: datetime | None = None,
                pool_created_at: datetime | None = None, raw_ref: str | None = None,
                migration_evidence: bool = False) -> str:
        """One canonical candidate per mint/pool; separate source observations preserve conflicting claims."""
        if not is_solana_address(mint) or (pool is not None and not is_solana_address(pool)):
            raise ValueError("canonical Solana mint and pool addresses required")
        candidate_id = "cand_" + fingerprint({"mint": mint, "pool": pool})[:32]
        iso, pool_key = to_iso(at), pool or ""
        token_time = None if token_created_at is None else to_iso(token_created_at)
        pool_time = None if pool_created_at is None else to_iso(pool_created_at)
        with transaction(self.conn):
            # Claims-only discovery also registers token identity; it never creates a fictional pool.
            self.conn.execute(
                "INSERT INTO tokens(mint, first_seen_at, last_seen_at) VALUES (?, ?, ?) "
                "ON CONFLICT(mint) DO UPDATE SET first_seen_at = MIN(first_seen_at, excluded.first_seen_at), "
                "last_seen_at = MAX(last_seen_at, excluded.last_seen_at)", (mint, iso, iso))
            existing = self.conn.execute("SELECT reason FROM discoveries WHERE id = ?", (candidate_id,)).fetchone()
            known = self.conn.execute("SELECT 1 FROM discoveries WHERE mint = ? LIMIT 1", (mint,)).fetchone() is not None
            if reason is None:
                reason = DiscoveryReason(existing["reason"]) if existing else classify(
                    known_token=known, known_pool=False, migration_evidence=migration_evidence)
            assert reason is not None  # A new token/pool always has a discovery reason.
            self.conn.execute(
                "INSERT INTO discoveries(id, mint, pool_address, reason, first_observed_at, last_observed_at, "
                "token_created_at, pool_created_at, state) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'PENDING') "
                "ON CONFLICT(id) DO UPDATE SET first_observed_at = MIN(first_observed_at, excluded.first_observed_at), "
                "last_observed_at = MAX(last_observed_at, excluded.last_observed_at), "
                "token_created_at = COALESCE(token_created_at, excluded.token_created_at), "
                "pool_created_at = COALESCE(pool_created_at, excluded.pool_created_at)",
                (candidate_id, mint, pool_key, reason.value, iso, iso, token_time, pool_time))
            self.conn.execute(
                "INSERT OR IGNORE INTO discovery_observations(candidate_id, source, reason, observed_at, "
                "token_created_at, pool_created_at, raw_sha256) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (candidate_id, source, reason.value, iso, token_time, pool_time, raw_ref))
        return candidate_id

    def observe_snapshot(self, snap: PoolSnapshot) -> str:
        prior_curve = self.conn.execute(
            "SELECT 1 FROM discoveries d JOIN pools p ON p.address = d.pool_address "
            "WHERE d.mint = ? AND p.venue IN ('pumpfun_bonding_curve', 'meteora_dbc') LIMIT 1",
            (snap.base_mint,)).fetchone() is not None
        claims = self.latest_claim(snap.base_mint, snap.observed_at)
        migration = not snap.is_bonding_curve and (prior_curve or (
            claims is not None and claims.graduated_pool == snap.pool_address))
        existing = self.conn.execute(
            "SELECT id, last_observed_at FROM discoveries WHERE mint = ? AND pool_address = ?",
            (snap.base_mint, snap.pool_address)).fetchone()
        reason: DiscoveryReason | None = None
        if existing:
            previous = self.conn.execute(
                "SELECT windows_json FROM market_snapshots WHERE pool_address = ? AND observed_at < ? AND source = ? "
                "ORDER BY observed_at_ms DESC, id DESC LIMIT 1",
                (snap.pool_address, to_iso(snap.observed_at), snap.source)).fetchone()
            old = {} if previous is None else json.loads(previous["windows_json"])
            h1, m5 = snap.windows.get("h1"), snap.windows.get("m5")
            old_volume = old.get("h1", {}).get("volume_usd")
            volume_ratio = (h1.volume_usd / Decimal(old_volume) if h1 and h1.volume_usd is not None
                            and old_volume is not None and Decimal(old_volume) > 0 else None)
            acceleration = (m5.volume_usd * 12 / h1.volume_usd if h1 and m5 and h1.volume_usd
                            and m5.volume_usd is not None else None)
            if volume_ratio is not None and volume_ratio >= self.settings.volume_expansion_ratio:
                reason = DiscoveryReason.VOLUME_EXPANSION
            elif acceleration is not None and acceleration >= self.settings.acceleration_ratio:
                reason = DiscoveryReason.ACCELERATION
            elif ("trending" in snap.source and (snap.observed_at - parse_iso(existing["last_observed_at"])).total_seconds()
                  >= self.settings.renewed_after_seconds):
                reason = DiscoveryReason.RENEWED_ACTIVITY
        return self.observe(snap.base_mint, snap.pool_address, snap.source, snap.observed_at, reason=reason,
                            token_created_at=None if claims is None else claims.created_at,
                            pool_created_at=snap.pool_created_at, raw_ref=snap.raw_sha256, migration_evidence=migration)

    def sync(self, limit: int = 500) -> int:
        """Resume from persisted cursors, bounded work per cycle; safe to repeat after a crash."""
        count = 0
        for stream in ("token_claims", "market_snapshots"):
            row = self.conn.execute("SELECT last_id FROM discovery_cursors WHERE stream = ?", (stream,)).fetchone()
            cursor = 0 if row is None else int(row[0])
            if stream == "token_claims":
                rows = self.conn.execute("SELECT * FROM token_claims WHERE id > ? ORDER BY id LIMIT ?",
                                         (cursor, limit)).fetchall()
            else:
                rows = self.conn.execute(_SNAPSHOT_SELECT + "WHERE s.id > ? ORDER BY s.id LIMIT ?",
                                         (cursor, limit)).fetchall()
            for item in rows:
                if stream == "token_claims":
                    claim = self.claim_from_row(item)
                    self.observe(claim.mint, None, claim.source, claim.observed_at, token_created_at=claim.created_at,
                                 raw_ref=claim.raw_sha256)
                else:
                    self.observe_snapshot(snapshot_from_row(item))
                # Each observation committed first. A crash before this update only replays an idempotent insert.
                self.conn.execute(
                    "INSERT INTO discovery_cursors(stream, last_id) VALUES (?, ?) "
                    "ON CONFLICT(stream) DO UPDATE SET last_id = excluded.last_id", (stream, item["id"]))
                count += 1
        return count

    @staticmethod
    def claim_from_row(row: sqlite3.Row) -> TokenClaims:
        return TokenClaims.model_validate({**json.loads(row["claims_json"]), "mint": row["mint"],
                                          "source": row["source"], "observed_at": row["observed_at"],
                                          "raw_sha256": row["raw_sha256"]})

    def latest_claim(self, mint: str, as_of: datetime) -> TokenClaims | None:
        row = self.conn.execute("SELECT * FROM token_claims WHERE mint = ? AND observed_at <= ? "
                                "ORDER BY observed_at_ms DESC, id DESC LIMIT 1", (mint, to_iso(as_of))).fetchone()
        return None if row is None else self.claim_from_row(row)

    def latest_snapshot(self, pool: str, as_of: datetime, source_prefix: str | None = None) -> PoolSnapshot | None:
        row = self.conn.execute(_SNAPSHOT_SELECT + "WHERE s.pool_address = ? AND s.observed_at <= ? "
                                "AND s.source LIKE ? ORDER BY s.observed_at_ms DESC, s.id DESC LIMIT 1",
                                (pool, to_iso(as_of), (source_prefix or "") + "%")).fetchone()
        return None if row is None else snapshot_from_row(row)

    def save_results(self, results: list[StageResult], assessment: SecurityAssessment | None = None,
                     raw_refs: tuple[str, ...] = ()) -> None:
        if not results or len({r.candidate_id for r in results}) != 1:
            raise ValueError("results must belong to one candidate")
        last = results[-1]
        with transaction(self.conn):
            candidate = self.conn.execute("SELECT mint FROM discoveries WHERE id = ?", (last.candidate_id,)).fetchone()
            if candidate is None:
                raise ValueError("unknown candidate")
            if assessment is not None and assessment.mint != candidate["mint"]:
                raise ValueError("assessment mint does not match candidate")
            if last.stage == 4 and last.outcome.value == "PASS" and (
                    assessment is None or assessment.outcome.value != "PASS"):
                raise ValueError("security PASS requires a matching passing assessment")
            for result in results:
                self.conn.execute(
                    "INSERT INTO stage_results(candidate_id, stage, outcome, rule_id, observed_values_json, "
                    "thresholds_json, config_hash, decided_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (result.candidate_id, result.stage, result.outcome.value, result.rule_id,
                     canonical_json(result.observed_values), canonical_json(result.thresholds), result.config_hash,
                     to_iso(result.decided_at)))
            if assessment:
                self.conn.execute("INSERT INTO security_assessments(candidate_id, assessed_at, config_hash, payload_json) "
                                  "VALUES (?, ?, ?, ?)", (last.candidate_id, to_iso(assessment.assessed_at),
                                                          last.config_hash, assessment.model_dump_json()))
            self.conn.execute("UPDATE discoveries SET state = ?, stage = ?, last_evaluated_at = ?, last_rule_id = ? WHERE id = ?",
                              ("PENDING" if last.outcome.value == "PASS" and last.stage < 4 else last.outcome.value,
                               last.stage, to_iso(last.decided_at), last.rule_id, last.candidate_id))
            self.conn.executemany("UPDATE raw_responses SET keep = 1 WHERE sha256 = ?", ((r,) for r in raw_refs if r))

    def window_report(self, since: datetime) -> dict[str, Any]:
        iso = to_iso(since)
        return {
            "since": iso,
            "source_observations": [dict(r) for r in self.conn.execute(
                "SELECT source, reason, COUNT(*) AS count FROM discovery_observations WHERE observed_at >= ? "
                "GROUP BY source, reason", (iso,))],
            "stage_results": [dict(r) for r in self.conn.execute(
                "SELECT stage, outcome, COUNT(*) AS count FROM stage_results WHERE decided_at >= ? "
                "GROUP BY stage, outcome", (iso,))],
            "rejection_reasons": [dict(r) for r in self.conn.execute(
                "SELECT rule_id, COUNT(*) AS count FROM stage_results WHERE decided_at >= ? AND outcome != 'PASS' "
                "GROUP BY rule_id ORDER BY count DESC LIMIT 20", (iso,))],
        }

    def report(self) -> dict[str, Any]:
        return {
            "candidates": self.conn.execute("SELECT COUNT(*) FROM discoveries").fetchone()[0],
            "by_state": {r[0]: r[1] for r in self.conn.execute("SELECT state, COUNT(*) FROM discoveries GROUP BY state")},
            "by_reason": {r[0]: r[1] for r in self.conn.execute("SELECT reason, COUNT(*) FROM discoveries GROUP BY reason")},
            "observations_by_reason": {r[0]: r[1] for r in self.conn.execute(
                "SELECT reason, COUNT(*) FROM discovery_observations GROUP BY reason")},
            "by_source": {r[0]: r[1] for r in self.conn.execute(
                "SELECT source, COUNT(DISTINCT candidate_id) FROM discovery_observations GROUP BY source")},
            "stage_results": [dict(r) for r in self.conn.execute(
                "SELECT stage, outcome, COUNT(*) AS count FROM stage_results GROUP BY stage, outcome")],
            "rejection_reasons": [dict(r) for r in self.conn.execute(
                "SELECT rule_id, COUNT(*) AS count FROM stage_results WHERE outcome != 'PASS' "
                "GROUP BY rule_id ORDER BY count DESC LIMIT 20")],
            "orphans": self.conn.execute(
                "SELECT COUNT(*) FROM discoveries d WHERE state != 'PENDING' AND NOT EXISTS "
                "(SELECT 1 FROM stage_results s WHERE s.candidate_id = d.id)").fetchone()[0],
        }
