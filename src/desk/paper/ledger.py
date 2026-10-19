"""PAPER ledger repository (ledger-paper.db). Every money movement is an append-only cash entry."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from decimal import Decimal
from typing import Any

from desk.core.clock import to_iso
from desk.core.hashing import canonical_json
from desk.paper.model import Profiles, usd_to_lamports
from desk.storage.db import transaction

OPEN_STATES = ("OPEN", "EXITING", "EXIT_BLOCKED", "IMPAIRED")


class PaperLedger:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    # ── book ───────────────────────────────────────────────────────────────────────────────────────
    def meta(self) -> dict[str, str]:
        return {row["key"]: row["value"] for row in self.conn.execute("SELECT key, value FROM ledger_meta")}

    def initialized(self) -> bool:
        return "book_started_at" in self.meta()

    def init_book(self, at: datetime, sol_usd: Decimal, base_usd: Decimal, cash_usd: Decimal) -> dict[str, str]:
        """Create the learning book once: cash cushion and the $100-account sizing base, at one recorded rate."""
        if self.initialized():
            return self.meta()
        if sol_usd <= 0 or base_usd <= 0 or cash_usd < base_usd:
            raise ValueError("positive SOL/USD rate and cash >= sizing base required")
        cash = usd_to_lamports(cash_usd, sol_usd)
        values = {"book_started_at": to_iso(at), "sol_usd_at_start": str(sol_usd), "base_usd": str(base_usd),
                  "cash_usd": str(cash_usd), "base_lamports": str(usd_to_lamports(base_usd, sol_usd)),
                  "deposit_lamports": str(cash)}
        with transaction(self.conn):
            self.conn.executemany("INSERT INTO ledger_meta(key, value) VALUES (?, ?)", values.items())
            self.conn.execute("INSERT INTO cash_entries(at, kind, amount_lamports, note) VALUES (?, 'DEPOSIT', ?, ?)",
                              (to_iso(at), cash, f"PAPER learning cash ${cash_usd} at {sol_usd} USD/SOL"))
        return self.meta()

    def base_lamports(self) -> int:
        return int(self.meta()["base_lamports"])

    def cash_lamports(self) -> int:
        return int(self.conn.execute("SELECT COALESCE(SUM(amount_lamports), 0) FROM cash_entries").fetchone()[0])

    def realized_pnl_lamports(self) -> int:
        return int(self.conn.execute(
            "SELECT COALESCE(SUM(realized_pnl_lamports), 0) FROM positions WHERE state = 'CLOSED'").fetchone()[0])

    # ── halts ──────────────────────────────────────────────────────────────────────────────────────
    def halt(self, reason: str, at: datetime) -> None:
        self.conn.execute("INSERT INTO system_state(key, value, updated_at) VALUES ('entries_halted', ?, ?) "
                          "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
                          (reason, to_iso(at)))
        self.event(at, "HALT", {"reason": reason})

    def resume(self, at: datetime) -> None:
        self.conn.execute("INSERT INTO system_state(key, value, updated_at) VALUES ('entries_halted', 'no', ?) "
                          "ON CONFLICT(key) DO UPDATE SET value = 'no', updated_at = excluded.updated_at", (to_iso(at),))
        self.event(at, "RESUME", {})

    def halted(self) -> str | None:
        row = self.conn.execute("SELECT value FROM system_state WHERE key = 'entries_halted'").fetchone()
        return None if row is None or row["value"] == "no" else str(row["value"])

    # ── positions ──────────────────────────────────────────────────────────────────────────────────
    def event(self, at: datetime, kind: str, payload: dict[str, Any], *, position_id: str | None = None,
              candidate_id: str | None = None) -> None:
        self.conn.execute("INSERT INTO paper_events(at, position_id, candidate_id, kind, payload_json) VALUES (?, ?, ?, ?, ?)",
                          (to_iso(at), position_id, candidate_id, kind, canonical_json(payload)))

    def open_positions(self) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM positions WHERE state != 'CLOSED' ORDER BY opened_at, id").fetchall()

    def position(self, position_id: str) -> sqlite3.Row:
        row: sqlite3.Row | None = self.conn.execute("SELECT * FROM positions WHERE id = ?", (position_id,)).fetchone()
        if row is None:
            raise KeyError(position_id)
        return row

    def open_position(self, *, position_id: str, intent_id: str, candidate_id: str, mint: str, pool_address: str,
                      strategy_id: str, strategy_version: str, decision_hash: str, regime_label: str | None,
                      at: datetime, ticket_lamports: int, tokens_raw: int, entry_fees_lamports: int,
                      failed_fees_lamports: int, rent_lamports: int, entry: dict[str, Any]) -> None:
        """Atomic: the position, its cash debits and its audit event all exist, or none do."""
        iso = to_iso(at)
        with transaction(self.conn):
            self.conn.execute(
                "INSERT INTO positions(id, intent_id, candidate_id, mint, pool_address, strategy_id, strategy_version,"
                " decision_hash, regime_label, state, opened_at, ticket_lamports, tokens_raw, entry_fees_lamports,"
                " rent_lamports, entry_json, peak_value_lamports, last_route_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN', ?, ?, ?, ?, ?, ?, ?, ?)",
                (position_id, intent_id, candidate_id, mint, pool_address, strategy_id, strategy_version, decision_hash,
                 regime_label, iso, ticket_lamports, tokens_raw, entry_fees_lamports + failed_fees_lamports,
                 rent_lamports, canonical_json(entry), None, iso))
            rows = [("ENTRY_TICKET", -ticket_lamports), ("ENTRY_FEES", -entry_fees_lamports), ("RENT_LOCK", -rent_lamports)]
            if failed_fees_lamports:
                rows.append(("FAILED_TX_FEE", -failed_fees_lamports))
            self.conn.executemany(
                "INSERT INTO cash_entries(at, kind, amount_lamports, position_id) VALUES (?, ?, ?, ?)",
                [(iso, kind, amount, position_id) for kind, amount in rows])
            self.event(at, "OPENED", {"ticket_lamports": ticket_lamports, "tokens_raw": tokens_raw,
                                      "strategy": strategy_id, "decision_hash": decision_hash},
                       position_id=position_id, candidate_id=candidate_id)

    def record_mark(self, position_id: str, at: datetime, kind: str, value_lamports: int | None,
                    quote_sha256: str | None) -> None:
        iso = to_iso(at)
        with transaction(self.conn):
            self.conn.execute("INSERT INTO marks(position_id, at, kind, value_lamports, quote_sha256) VALUES (?, ?, ?, ?, ?)",
                              (position_id, iso, kind, value_lamports, quote_sha256))
            if kind == "EXECUTABLE" and value_lamports is not None:
                self.conn.execute(
                    "UPDATE positions SET last_mark_lamports = ?, last_mark_at = ?, last_mark_kind = 'EXECUTABLE',"
                    " last_route_at = ?, peak_value_lamports = MAX(COALESCE(peak_value_lamports, 0), ?) WHERE id = ?",
                    (value_lamports, iso, iso, value_lamports, position_id))
            else:
                self.conn.execute("UPDATE positions SET last_mark_kind = ?, last_mark_at = ? WHERE id = ?",
                                  (kind, iso, position_id))

    def set_state(self, position_id: str, state: str, at: datetime, payload: dict[str, Any], *,
                  failed_fee_lamports: int = 0) -> None:
        """Exit progress: attempts and simulated failed-transaction fees accumulate; the lock is retained."""
        if state not in OPEN_STATES:
            raise ValueError("use close_position to close")
        with transaction(self.conn):
            self.conn.execute(
                "UPDATE positions SET state = ?, exit_attempts = ?, exit_reason = COALESCE(?, exit_reason),"
                " exit_failed_fees_lamports = exit_failed_fees_lamports + ? WHERE id = ?",
                (state, int(payload.get("attempts", 0)), payload.get("reason"), failed_fee_lamports, position_id))
            if failed_fee_lamports:
                self.conn.execute("INSERT INTO cash_entries(at, kind, amount_lamports, position_id, note)"
                                  " VALUES (?, 'FAILED_TX_FEE', ?, ?, 'simulated failed exit')",
                                  (to_iso(at), -failed_fee_lamports, position_id))
            self.event(at, state, payload, position_id=position_id)

    def close_position(self, position_id: str, at: datetime, *, reason: str, exit_lamports: int, exit_fees_lamports: int,
                       profiles: dict[str, Any]) -> int:
        """Atomic close: proceeds, fees and rent refund journaled; realized P&L follows the conservative profile."""
        row = self.position(position_id)
        if row["state"] == "CLOSED":
            raise ValueError("position already closed")
        failed_fees_lamports = int(row["exit_failed_fees_lamports"])
        realized = int(exit_lamports - exit_fees_lamports - failed_fees_lamports
                    - row["ticket_lamports"] - row["entry_fees_lamports"])
        iso = to_iso(at)
        with transaction(self.conn):
            self.conn.executemany(
                "INSERT INTO cash_entries(at, kind, amount_lamports, position_id) VALUES (?, ?, ?, ?)",
                [(iso, kind, amount, position_id) for kind, amount in (
                    ("EXIT_PROCEEDS", exit_lamports), ("EXIT_FEES", -exit_fees_lamports),
                    ("RENT_REFUND", row["rent_lamports"])) if amount])
            self.conn.execute(
                "UPDATE positions SET state = 'CLOSED', closed_at = ?, exit_reason = ?, exit_lamports = ?,"
                " exit_fees_lamports = ?, realized_pnl_lamports = ?, profiles_json = ? WHERE id = ?",
                (iso, reason, exit_lamports, exit_fees_lamports, realized,
                 canonical_json(profiles), position_id))
            self.event(at, "CLOSED", {"reason": reason, "realized_pnl_lamports": realized, "profiles": profiles},
                       position_id=position_id)
        return realized

    # ── reconciliation (M5-01) ─────────────────────────────────────────────────────────────────────
    def reconcile(self) -> dict[str, int]:
        """Recompute cash from positions independently of the journal and compare."""
        deposit = int(self.meta().get("deposit_lamports", "0"))
        expected = deposit
        for row in self.conn.execute("SELECT * FROM positions"):
            expected -= (row["ticket_lamports"] + row["entry_fees_lamports"] + row["rent_lamports"]
                         + row["exit_failed_fees_lamports"])
            if row["state"] == "CLOSED":
                expected += row["exit_lamports"] - row["exit_fees_lamports"] + row["rent_lamports"]
        journal = self.cash_lamports()
        return {"journal_lamports": journal, "recomputed_lamports": int(expected),
                "difference_lamports": journal - int(expected)}


def profile_pnl(entry: Profiles, exit_: Profiles, ticket: int, fees: dict[str, int]) -> dict[str, Any]:
    """Per-profile net result for one closed trade (rent excluded: it is refunded)."""
    return {name: {"tokens": getattr(entry, name), "exit_lamports": getattr(exit_, name),
                   "fees_lamports": fees[name],
                   "pnl_lamports": getattr(exit_, name) - ticket - fees[name],
                   "return": str(Decimal(getattr(exit_, name) - ticket - fees[name]) / Decimal(ticket))}
            for name in ("observed", "conservative", "stress")}


def as_dict(row: sqlite3.Row) -> dict[str, Any]:
    out = dict(row)
    for key in ("entry_json", "profiles_json"):
        if out.get(key):
            out[key] = json.loads(out[key])
    return out


__all__ = ["OPEN_STATES", "PaperLedger", "as_dict", "profile_pnl"]
