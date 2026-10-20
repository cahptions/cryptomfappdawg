"""Council records in research.db: frozen specifications, the daily cohort, advisories and the runner's cursor.

Records only. Writing the same advisory again is a no-op; the same identity with different inputs is refused,
because a historical advisory is never rewritten.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any

from desk.contracts.advisory import Advisory
from desk.core.clock import to_iso
from desk.core.hashing import canonical_json, fingerprint
from desk.council.specs import COMMON, SPECS, spec_json, spec_sha256
from desk.storage.db import StorageError, transaction

REGISTRATION_KIND = "COUNCIL_REGISTRATION"


def registration() -> dict[str, Any]:
    """The preregistered experiment: frozen specifications, arms, masks and what the records may claim."""
    return {
        "kind": REGISTRATION_KIND, "design": "research/council (STRATEGIES.md, COUNCIL.md, validation_plan.md)",
        "profile": COMMON["profile"], "mode": "SHADOW", "influence": "NONE",
        "specs": {advisor: spec_sha256(spec) for advisor, spec in SPECS.items()},
        "arms": {"P": "unchanged primary", "P+A": "primary minus entries with a valid C1_BRC OPPOSE",
                 "P+B": "primary minus entries with a valid C2_ECDR OPPOSE",
                 "P+A+B": "primary minus entries with either valid OPPOSE"},
        "mask": "OPPOSE removes; SUPPORT, CAUTION, ABSTAIN and no record keep the primary decision",
        "horizons_seconds": {"C1_BRC": 28800, "C2_ECDR": 5400},
        "baselines": {"C1_BRC": ["primary alone", "raw same-window momentum", "beta-one relative momentum", "B2"],
                      "C2_ECDR": ["primary alone", "plain relative reversal", "same event without execution recovery",
                                  "H2/H4/H7 conditioned comparison"]},
        "evidence_floors": {"calendar_days_untouched": 60, "closed_primary_trades": 300, "effective_sample": 150},
        "network_requests_added": 0,
        "claims": "records only; no stance is a probability, expected return or permission to trade",
    }


class CouncilRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    # ── frozen specifications and the experiment registration ──────────────────────────────────────
    def register(self, at: datetime) -> None:
        payload = registration()
        with transaction(self.conn):
            for advisor, spec in SPECS.items():
                digest = spec_sha256(spec)
                existing = self.conn.execute("SELECT spec_sha256 FROM council_specs WHERE advisor_id = ? AND version = ?",
                                             (advisor, spec["version"])).fetchone()
                if existing is not None and existing["spec_sha256"] != digest:
                    raise StorageError(f"{advisor} v{spec['version']} changed without a new version")
                self.conn.execute("INSERT OR IGNORE INTO council_specs(spec_sha256, advisor_id, version, registered_at,"
                                  " content_json) VALUES (?, ?, ?, ?, ?)",
                                  (digest, advisor, spec["version"], to_iso(at), spec_json(spec)))
            self.conn.execute("INSERT OR IGNORE INTO experiments(id, created_at, kind, payload_json) VALUES (?, ?, ?, ?)",
                              ("council_" + fingerprint(payload)[:32], to_iso(at), REGISTRATION_KIND,
                               canonical_json(payload)))

    # ── daily frozen cohort ────────────────────────────────────────────────────────────────────────
    def admit(self, profile: str, day: str, mint: str, pool: str, at: datetime, decision_hash: str,
              limit: int) -> bool:
        """True if (mint, pool) is a member for ``day``; admits it while the day's cohort has room."""
        with transaction(self.conn):
            if self.conn.execute("SELECT 1 FROM council_members WHERE profile = ? AND day = ? AND mint = ? AND"
                                 " pool_address = ?", (profile, day, mint, pool)).fetchone() is not None:
                return True
            count = self.conn.execute("SELECT COUNT(*) FROM council_members WHERE profile = ? AND day = ?",
                                      (profile, day)).fetchone()[0]
            if count >= limit:
                return False
            self.conn.execute("INSERT INTO council_members(profile, day, mint, pool_address, admitted_at,"
                              " primary_decision_hash) VALUES (?, ?, ?, ?, ?, ?)",
                              (profile, day, mint, pool, to_iso(at), decision_hash))
        return True

    # ── advisories ─────────────────────────────────────────────────────────────────────────────────
    def save(self, source: str, advisories: list[Advisory], *, row_id: int, at: datetime) -> int:
        """Store a decision's advisories and advance the source cursor in one transaction; returns rows added."""
        added = 0
        with transaction(self.conn):
            for advisory in advisories:
                existing = self.conn.execute("SELECT input_sha256 FROM council_advisories WHERE advisory_id = ?",
                                             (advisory.advisory_id,)).fetchone()
                if existing is not None:
                    if existing["input_sha256"] != advisory.input_sha256:
                        raise StorageError(f"advisory {advisory.advisory_id} would be rewritten with other inputs")
                    continue
                self.conn.execute(
                    "INSERT INTO council_advisories(advisory_id, advisor_id, spec_sha256, source, primary_decision_hash,"
                    " primary_action, mint, pool_address, decision_at, computed_at, stance, reasons, input_sha256,"
                    " payload_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (advisory.advisory_id, advisory.advisor_id, advisory.spec_sha256, source,
                     advisory.primary_decision_hash, advisory.primary_action, advisory.asset.instrument_id,
                     advisory.asset.pool_or_venue, advisory.decision_at, advisory.computed_at, advisory.stance,
                     ",".join(advisory.rationale_codes), advisory.input_sha256,
                     canonical_json(advisory.model_dump(mode="json"))))
                added += 1
            self._advance(source, row_id, at)
        return added

    def cursor(self, source: str) -> int:
        row = self.conn.execute("SELECT last_row_id FROM council_progress WHERE source = ?", (source,)).fetchone()
        return 0 if row is None else int(row["last_row_id"])

    def advance(self, source: str, row_id: int, at: datetime) -> None:
        with transaction(self.conn):
            self._advance(source, row_id, at)

    def _advance(self, source: str, row_id: int, at: datetime) -> None:
        self.conn.execute("INSERT INTO council_progress(source, last_row_id, updated_at) VALUES (?, ?, ?)"
                          " ON CONFLICT(source) DO UPDATE SET last_row_id = MAX(last_row_id, excluded.last_row_id),"
                          " updated_at = excluded.updated_at", (source, row_id, to_iso(at)))


__all__ = ["REGISTRATION_KIND", "CouncilRepository", "registration"]
