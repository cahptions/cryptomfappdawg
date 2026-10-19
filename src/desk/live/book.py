"""LIVE book (ledger-live.db): PAPER's append-only ledger, booked only from what the chain reports.

What differs from PAPER:
- capital is the operator's allocation (``live.max_capital_sol``), recorded as deposits; raising the allocation
  records the difference as a new deposit, and lowering it shrinks sizing at once (sizing never exceeds it);
- position amounts come from the signer's measured balance changes; when a node could not report them, from the
  intent's guaranteed bounds, flagged for reconciliation;
- a fee paid by a transaction that failed on-chain is journaled even when no position exists;
- every request to the signer is journaled (INTENT_SENT) before it is sent and resolved (INTENT_RESOLVED) after
  its outcome is booked. Booking is idempotent per intent, so an answer lost to a crash or timeout is recovered
  from the signer's own journal, never by repeating the request.
"""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from typing import Any

from desk.core.clock import to_iso
from desk.paper.ledger import PaperLedger
from desk.storage.db import transaction

LAMPORTS = Decimal(10 ** 9)


class LiveBook(PaperLedger):
    # ── capital ────────────────────────────────────────────────────────────────────────────────────
    def deposits(self) -> int:
        return int(self.conn.execute(
            "SELECT COALESCE(SUM(amount_lamports), 0) FROM cash_entries WHERE kind = 'DEPOSIT'").fetchone()[0])

    def sync_allocation(self, at: datetime, allocation_lamports: int, sol_usd: Decimal) -> int:
        """Create the book on first use, then record any raise of the allocation as a deposit; returns lamports added."""
        if allocation_lamports <= 0 or sol_usd <= 0:
            raise ValueError("a positive allocation and SOL/USD rate are required")
        iso, sol = to_iso(at), Decimal(allocation_lamports) / LAMPORTS
        if not self.initialized():
            usd = str((sol * sol_usd).quantize(Decimal("0.01")))
            values = {"book_started_at": iso, "sol_usd_at_start": str(sol_usd), "base_usd": usd, "cash_usd": usd,
                      "base_lamports": str(allocation_lamports), "deposit_lamports": str(allocation_lamports)}
            with transaction(self.conn):
                self.conn.executemany("INSERT INTO ledger_meta(key, value) VALUES (?, ?)", values.items())
                self.conn.execute("INSERT INTO cash_entries(at, kind, amount_lamports, note) VALUES (?, 'DEPOSIT', ?, ?)",
                                  (iso, allocation_lamports, f"LIVE allocation {sol} SOL (live.max_capital_sol)"))
                self.event(at, "ALLOCATION", {"allocation_lamports": allocation_lamports, "added_lamports": allocation_lamports})
            return allocation_lamports
        added = allocation_lamports - self.deposits()
        if added <= 0:
            return 0
        with transaction(self.conn):
            self.conn.execute("INSERT INTO cash_entries(at, kind, amount_lamports, note) VALUES (?, 'DEPOSIT', ?, ?)",
                              (iso, added, f"LIVE allocation raised to {sol} SOL (live.max_capital_sol)"))
            self.event(at, "ALLOCATION", {"allocation_lamports": allocation_lamports, "added_lamports": added})
        return added

    def failed_fee(self, at: datetime, lamports: int, payload: dict[str, Any], *, candidate_id: str | None = None) -> None:
        """A fee the wallet paid for a transaction that failed on-chain before any position existed."""
        with transaction(self.conn):
            self.conn.execute("INSERT INTO cash_entries(at, kind, amount_lamports, note) VALUES (?, 'FAILED_TX_FEE', ?, ?)",
                              (to_iso(at), -lamports, "failed LIVE entry transaction"))
            self.event(at, "FAILED_TX_FEE", {"fee_lamports": lamports, **payload}, candidate_id=candidate_id)

    def reconcile(self) -> dict[str, int]:
        """Cash recomputed from deposits, positions and position-less failed fees, compared with the journal."""
        expected = self.deposits() + int(self.conn.execute(
            "SELECT COALESCE(SUM(amount_lamports), 0) FROM cash_entries WHERE kind = 'FAILED_TX_FEE'"
            " AND position_id IS NULL").fetchone()[0])
        for row in self.conn.execute("SELECT * FROM positions"):
            expected -= (row["ticket_lamports"] + row["entry_fees_lamports"] + row["rent_lamports"]
                         + row["exit_failed_fees_lamports"])
            if row["state"] == "CLOSED":
                expected += row["exit_lamports"] - row["exit_fees_lamports"] + row["rent_lamports"]
        journal = self.cash_lamports()
        return {"journal_lamports": journal, "recomputed_lamports": expected, "difference_lamports": journal - expected}

    # ── requests to the signer ─────────────────────────────────────────────────────────────────────
    def sent(self, at: datetime, intent_id: str, kind: str, payload: dict[str, Any], *, position_id: str | None = None,
             candidate_id: str | None = None) -> None:
        """Journal a request before it leaves the desk, with everything needed to book its outcome later."""
        self.event(at, "INTENT_SENT", {"intent_id": intent_id, "kind": kind, **payload},
                   position_id=position_id, candidate_id=candidate_id)

    def pending(self, kinds: tuple[str, ...] = ("BUY", "SELL", "CLOSE")) -> list[dict[str, Any]]:
        """Requests sent whose outcome is not booked yet, oldest first (payload plus ids)."""
        rows = self.conn.execute(
            "SELECT * FROM paper_events s WHERE s.kind = 'INTENT_SENT' AND NOT EXISTS ("
            " SELECT 1 FROM paper_events r WHERE r.kind = 'INTENT_RESOLVED'"
            " AND json_extract(r.payload_json, '$.intent_id') = json_extract(s.payload_json, '$.intent_id'))"
            " ORDER BY s.id").fetchall()
        sent = [{**json.loads(row["payload_json"]), "position_id": row["position_id"], "candidate_id": row["candidate_id"]}
                for row in rows]
        return [item for item in sent if item["kind"] in kinds]

    def _resolved(self, intent_id: str) -> bool:
        return self.conn.execute("SELECT 1 FROM paper_events WHERE kind = 'INTENT_RESOLVED'"
                                 " AND json_extract(payload_json, '$.intent_id') = ?", (intent_id,)).fetchone() is not None

    def _resolve_rows(self, at: datetime, sent: dict[str, Any], outcome: dict[str, Any]) -> None:
        self.event(at, "INTENT_RESOLVED", {"intent_id": sent["intent_id"], "kind": sent["kind"], **outcome},
                   position_id=sent.get("position_id"), candidate_id=sent.get("candidate_id"))

    # Each booking below is one transaction that also resolves its request, and does nothing if the request was
    # already resolved: a recovered outcome can never be booked twice, by this process or another.
    def resolve(self, at: datetime, sent: dict[str, Any], outcome: dict[str, Any],
                event: tuple[str, dict[str, Any]] | None = None) -> bool:
        with transaction(self.conn):
            if self._resolved(sent["intent_id"]):
                return False
            if event is not None:
                self.event(at, event[0], event[1], position_id=sent.get("position_id"),
                           candidate_id=sent.get("candidate_id"))
            self._resolve_rows(at, sent, outcome)
        return True

    def book_entry(self, at: datetime, sent: dict[str, Any], outcome: dict[str, Any], position: dict[str, Any]) -> bool:
        with transaction(self.conn):
            if self._resolved(sent["intent_id"]):
                return False
            if self.conn.execute("SELECT 1 FROM positions WHERE intent_id = ?", (sent["intent_id"],)).fetchone() is None:
                self._insert_position(**position)
            self._resolve_rows(at, sent, outcome)
        return True

    def book_failed_entry(self, at: datetime, sent: dict[str, Any], outcome: dict[str, Any], fee_lamports: int) -> bool:
        """A BUY that landed and failed: its fee was paid although no position exists."""
        with transaction(self.conn):
            if self._resolved(sent["intent_id"]):
                return False
            if fee_lamports:
                self.conn.execute("INSERT INTO cash_entries(at, kind, amount_lamports, note)"
                                  " VALUES (?, 'FAILED_TX_FEE', ?, 'failed LIVE entry transaction')",
                                  (to_iso(at), -fee_lamports))
            self.event(at, "ENTRY_FAILED", {"intent_id": sent["intent_id"], "fee_lamports": fee_lamports, **outcome},
                       candidate_id=sent.get("candidate_id"))
            self._resolve_rows(at, sent, outcome)
        return True

    def book_sale(self, at: datetime, sent: dict[str, Any], outcome: dict[str, Any], sale: dict[str, Any]) -> bool:
        """Tokens sold; the position stays open until its token account is closed and the rent is back."""
        return self.resolve(at, sent, outcome, ("SOLD", sale))

    def book_exit_failure(self, at: datetime, sent: dict[str, Any], outcome: dict[str, Any], *, state: str,
                          payload: dict[str, Any], fee_lamports: int) -> bool:
        with transaction(self.conn):
            if self._resolved(sent["intent_id"]):
                return False
            self._state_rows(sent["position_id"], state, at, payload, failed_fee_lamports=fee_lamports,
                             note="failed LIVE exit transaction")
            self.event(at, "EXIT_FAILED", payload, position_id=sent["position_id"])
            self._resolve_rows(at, sent, outcome)
        return True

    def book_close(self, at: datetime, sent: dict[str, Any], outcome: dict[str, Any], *, reason: str,
                   exit_lamports: int, profiles: dict[str, Any]) -> bool:
        """Close the position at what the wallet actually received; on-chain fees are inside these amounts."""
        with transaction(self.conn):
            if self._resolved(sent["intent_id"]):
                return False
            if self.position(sent["position_id"])["state"] != "CLOSED":
                self._close_rows(sent["position_id"], at, reason=reason, exit_lamports=exit_lamports,
                                 exit_fees_lamports=0, profiles=profiles)
            self._resolve_rows(at, sent, outcome)
        return True

    # ── position progress ─────────────────────────────────────────────────────────────────────────
    def sold(self, position_id: str) -> dict[str, Any] | None:
        """What the position's sales so far brought in (None before any sale); it closes once its account does."""
        sales = [json.loads(row["payload_json"]) for row in self.conn.execute(
            "SELECT payload_json FROM paper_events WHERE position_id = ? AND kind = 'SOLD' ORDER BY id", (position_id,))]
        if not sales:
            return None
        return {"sol_lamports": sum(int(sale["sol_lamports"]) for sale in sales),
                "tokens_sold": sum(int(sale["tokens_sold"]) for sale in sales),
                "reason": sales[0]["reason"], "sales": sales}

    def sent_count(self, position_id: str, kind: str) -> int:
        return int(self.conn.execute(
            "SELECT COUNT(*) FROM paper_events WHERE position_id = ? AND kind = 'INTENT_SENT'"
            " AND json_extract(payload_json, '$.kind') = ?", (position_id, kind)).fetchone()[0])


__all__ = ["LiveBook"]
