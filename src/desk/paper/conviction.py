"""Conviction sizing for rare, strongly confirmed signals (operator request 2026-10-19, PLAN §13.1.1).

Only the moonshot hypothesis H8 may size up, and only when both hold:
1. Evidence — the latest confidence report (at most 7 days old) shows PASS on net expectancy (one-sided 95%
   stationary-bootstrap lower bound above zero) for this exact H8 version: the strategy earned it with its own
   closed PAPER trades. A single exciting chart never qualifies.
2. Signal — confirmation beyond H8's own entry rules: 5-minute volume at least 5x the hourly pace and at least
   1.5 buys per sell.
Then the fraction is 1.5x the normal one, capped by the 15% ticket ceiling. Otherwise the normal fraction is used,
and the record keeps what conviction size would have been so its simulated effect can be reported.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from desk.contracts.features import FeatureSnapshot
from desk.contracts.strategy import StrategySpec
from desk.core.clock import parse_iso8601
from desk.core.hashing import canonical_json, sha256_hex

POLICY: dict[str, Any] = {
    "strategy": "H8", "multiplier": "1.5", "report_max_age_days": 7,
    "evidence": "net_expectancy PASS for this exact version in the latest CONFIDENCE_MATRIX report",
    "signal": {"volume_acceleration": "5", "buy_sell_ratio": "1.5"},
}
POLICY_VERSION = "conviction-v1-" + sha256_hex(canonical_json(POLICY))[:12]
MULTIPLIER = Decimal(POLICY["multiplier"])


@dataclass(frozen=True)
class Conviction:
    eligible_strategy: bool
    evidence: str                    # PASS | NOT_MET | NO_REPORT | STALE_REPORT | NOT_APPLICABLE
    signal_confirmed: bool
    detail: str

    @property
    def applied(self) -> bool:
        return self.eligible_strategy and self.evidence == "PASS" and self.signal_confirmed

    def fraction(self, base: Decimal, cap: Decimal) -> Decimal:
        return min(base * MULTIPLIER, cap) if self.applied else base

    def as_dict(self, base: Decimal, cap: Decimal) -> dict[str, Any]:
        return {"policy": POLICY_VERSION, "applied": self.applied, "evidence": self.evidence,
                "signal_confirmed": self.signal_confirmed, "detail": self.detail,
                "fraction": str(self.fraction(base, cap)), "conviction_fraction": str(min(base * MULTIPLIER, cap))}


def evidence_status(research: sqlite3.Connection, version: str, now: datetime) -> tuple[str, str]:
    """(PASS | NOT_MET | NO_REPORT | STALE_REPORT, detail) for one strategy version's own evidence."""
    row = research.execute("SELECT created_at, payload_json FROM experiments WHERE kind = 'CONFIDENCE_MATRIX' "
                           "ORDER BY created_at DESC, id DESC LIMIT 1").fetchone()
    if row is None:
        return "NO_REPORT", "no confidence report recorded yet (desk confidence)"
    if now - parse_iso8601(row["created_at"]) > timedelta(days=int(POLICY["report_max_age_days"])):
        return "STALE_REPORT", f"latest confidence report is from {row['created_at']}"
    for matrix in json.loads(row["payload_json"]).get("matrices", []):
        if matrix.get("strategy_version") == version:
            cell = next((c for c in matrix.get("cells", []) if c.get("dimension") == "net_expectancy"), None)
            if cell and cell.get("status") == "PASS":
                return "PASS", f"net expectancy lower bound {cell.get('lower')} over {cell.get('evidence')} trades"
            return "NOT_MET", f"net expectancy {cell.get('status') if cell else 'missing'} for this version"
    return "NOT_MET", "no closed trades of this version in the latest report"


def assess(research: sqlite3.Connection, spec: StrategySpec, features: FeatureSnapshot, now: datetime) -> Conviction:
    if spec.strategy_id != POLICY["strategy"]:
        return Conviction(False, "NOT_APPLICABLE", False, "only the moonshot hypothesis can size up")
    signal = all((features.usable(name) or Decimal(-1)) >= Decimal(threshold)
                 for name, threshold in POLICY["signal"].items())
    evidence, detail = evidence_status(research, spec.version_id, now)
    return Conviction(True, evidence, signal, detail)
