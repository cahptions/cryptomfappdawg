"""PAPER transaction-fee estimates (PLAN §12.3): measured where possible, conservative where not.

What each paper trade pays:
- Jupiter's swap fee, pool fees and price impact are already inside every quoted amount. Verified 2026-10-19:
  on the same route, Ultra ``outAmount`` equals the fee-free route output × (1 − ``feeBps``)
  (``research/evidence/2026-10-19/m5_jupiter_fee_embedding.json``). They are never charged twice.
- Network cost per transaction (signature + Jupiter's priority fee) is not reported by quotes without a taker.
  A probe asks Jupiter for real orders built for a read-only public taker every 10 minutes and stores the fees
  it reports. Each transaction is then charged the 90th-percentile priority fee of the last 24 hours × 1.5,
  plus the largest reported signature fee. With fewer than six observations the conservative configured
  model (``paper.priority_fee_lamports``, floored by the ceilings) applies instead.
- A round trip is three transactions: buy, sell, and closing the token account — the only way its rent
  (locked while the position is open) comes back.
"""

from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from desk.contracts.quotes import QuoteObservation
from desk.core.clock import to_epoch_millis, to_iso
from desk.core.config import LoadedConfig
from desk.paper.model import FeeModel

MIN_OBSERVATIONS = 6
WINDOW = timedelta(hours=24)
QUANTILE = 0.9
MARGIN = 1.5
TRANSACTIONS_PER_ROUND_TRIP = 3          # buy, sell, close the token account


@dataclass(frozen=True)
class FeeEstimate:
    signature_lamports: int
    priority_lamports: int
    rent_lamports: int
    source: str                          # MEASURED | FALLBACK_MODEL
    observations: int
    priority_p50: int | None = None
    priority_p90: int | None = None

    @property
    def per_transaction(self) -> int:
        return self.signature_lamports + self.priority_lamports

    @property
    def round_trip(self) -> int:
        return TRANSACTIONS_PER_ROUND_TRIP * self.per_transaction

    @property
    def model(self) -> FeeModel:
        return FeeModel(self.signature_lamports, self.priority_lamports, self.rent_lamports)

    def as_dict(self) -> dict[str, Any]:
        return {"source": self.source, "observations": self.observations, "per_transaction_lamports": self.per_transaction,
                "signature_lamports": self.signature_lamports, "priority_lamports": self.priority_lamports,
                "priority_p50_lamports": self.priority_p50, "priority_p90_lamports": self.priority_p90,
                "rent_lamports": self.rent_lamports, "transactions_per_round_trip": TRANSACTIONS_PER_ROUND_TRIP,
                "rule": f"p{int(QUANTILE * 100)} x {MARGIN} of the last 24 h when >= {MIN_OBSERVATIONS} probes, "
                        "else the conservative model"}


def _rank(values: list[int], quantile: float) -> int:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(quantile * len(ordered)) - 1)]


def estimate(conn: sqlite3.Connection, loaded: LoadedConfig, now: datetime) -> FeeEstimate:
    paper = loaded.settings.paper
    rows = conn.execute(
        "SELECT signature_fee_lamports, prioritization_fee_lamports FROM fee_observations "
        "WHERE observed_at_ms > ? AND observed_at_ms <= ?",
        (to_epoch_millis(now - WINDOW), to_epoch_millis(now))).fetchall()
    if len(rows) < MIN_OBSERVATIONS:
        return FeeEstimate(paper.signature_fee_lamports, paper.priority_fee_lamports, paper.token_account_rent_lamports,
                           "FALLBACK_MODEL", len(rows))
    priority = [int(r["prioritization_fee_lamports"]) for r in rows]
    p90 = _rank(priority, QUANTILE)
    signature = max(max(int(r["signature_fee_lamports"]) for r in rows), 5000)
    return FeeEstimate(signature, math.ceil(p90 * MARGIN), paper.token_account_rent_lamports, "MEASURED", len(rows),
                       _rank(priority, 0.5), p90)


def record(conn: sqlite3.Connection, quote: QuoteObservation, route: str) -> bool:
    """Store the fees of an order built for a taker; quotes without reported fees are not evidence."""
    if quote.taker is None or quote.signature_fee_lamports is None or quote.prioritization_fee_lamports is None:
        return False
    conn.execute(
        "INSERT INTO fee_observations(observed_at, observed_at_ms, route, router, signature_fee_lamports,"
        " prioritization_fee_lamports, rent_fee_lamports, fee_bps, raw_sha256) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (to_iso(quote.received_at), to_epoch_millis(quote.received_at), route, quote.router,
         quote.signature_fee_lamports, quote.prioritization_fee_lamports, quote.rent_fee_lamports, quote.fee_bps,
         quote.raw_sha256))
    return True
