"""Historical, whole-position exit checks using the tracker's original quote receipts."""
from __future__ import annotations

import json
import sqlite3
import zlib
from collections import defaultdict
from collections.abc import Sequence
from datetime import datetime, time
from typing import Any

from desk.stocks import calendar as cal
from desk.stocks.execution import Quote, evidence, positions
from desk.stocks.paper.fills import CONTRACT, OPTION_FEE_PER_CONTRACT

# The working table is cleared after the close. Preserve each response separately, including incomplete evidence.
ReceiptRow = tuple[str, str, str | None, float | None, float | None, int | None, int | None]


def receipt_row(row: sqlite3.Row) -> ReceiptRow:
    return row["at"], row["source"], row["quoted_at"], row["bid"], row["ask"], row["bid_size"], row["ask_size"]


def pack_receipts(rows: Sequence[sqlite3.Row]) -> bytes:
    return zlib.compress(json.dumps([receipt_row(row) for row in rows], separators=(",", ":"), allow_nan=False).encode())


class ExitReview:
    """Group by the actual retrieval and source, never by rounded chart minute or most recent price per leg."""

    def __init__(self) -> None:
        self.receipts: dict[tuple[str, str], dict[str, Quote]] = defaultdict(dict)
        self.legacy_sessions: set[int] = set()

    def add(self, occ: str, row: ReceiptRow) -> None:
        at, source, quoted_at, bid, ask, bid_size, ask_size = row
        if source == "schwab_quotes" or source == "chains" or source.startswith("collector:"):
            stamp = datetime.fromisoformat(quoted_at) if quoted_at else None
            self.receipts[at, source][occ] = Quote(occ, stamp if stamp and stamp.tzinfo else None, bid, ask, bid_size, ask_size)

    def folded(self, occ: str, session: int, blob: bytes | None) -> None:
        if blob is None:
            self.legacy_sessions.add(session)
        else:
            for row in json.loads(zlib.decompress(blob)):
                self.add(occ, row)

    def report(self, legs: list[dict[str, Any]], action: str, bases: dict[str, float], entries: dict[str, datetime],
               end: datetime) -> dict[str, Any]:
        # Fallback chain batches are grouped by underlying/expiry. Only same-series legs can share one chain response.
        if not bases or not legs or len({leg["occ"][:-9] for leg in legs}) != 1:
            return {"reason": "This view needs contracts on one underlying and expiry."}
        held = positions(legs)
        closing = [(occ, -quantity) for occ, quantity in held]
        fees = 2 * OPTION_FEE_PER_CONTRACT * len(legs)
        summaries: dict[str, dict[str, Any]] = {
            key: {"receipts": 0, "incomplete": 0, "unverified": 0, "capacity_bins": {}} for key in bases}
        for (at, source), quotes in sorted(self.receipts.items()):
            received = datetime.fromisoformat(at)
            local = cal.new_york(received)
            if (received <= min(entries.values()) or received > end or not cal.is_trading_day(local.date())
                    or local.time() < time(9, 30) or local >= cal.session_close(local.date())):
                continue
            complete = all(occ in quotes for occ, _ in held)
            check = evidence(closing, quotes, received, received) if complete else {}
            for basis, entry in bases.items():
                if received <= entries[basis]:
                    continue
                summary = summaries[basis]
                summary["receipts"] += 1
                if not complete:
                    summary["incomplete"] += 1
                    continue
                if "capacity" not in check or datetime.fromisoformat(check["quoted_at"]) <= entries[basis]:
                    summary["unverified"] += 1
                    continue
                # A zero bid provides no sale price even if a provider included a bid size.
                capacity = 0 if any(qty > 0 and quotes[occ].bid == 0 for occ, qty in held) else check["capacity"]
                bins = summary["capacity_bins"]
                bucket = bins.setdefault(capacity, {"capacity": capacity, "count": 0, "mid_only_gains": 0})
                bucket["count"] += 1
                if capacity == 0:
                    continue
                opening_cash = (-1 if action == "BUY" else 1) * entry * CONTRACT
                side_pnl = round(opening_cash - check["net_debit"] - fees, 2)
                mid_value = sum(-leg["quantity"] * (leg["bid"] + leg["ask"]) / 2 * CONTRACT for leg in check["legs"])
                mid_pnl = round(opening_cash + mid_value - fees, 2)
                bucket["mid_only_gains"] += int(mid_pnl > 0 >= side_pnl)
                bucket["latest"] = {"at": at, "source": source, "pnl": side_pnl, "mid_pnl": mid_pnl,
                                    "spread_cost": round(mid_pnl - side_pnl, 2)}
        for summary in summaries.values():
            summary["capacity_bins"] = list(summary["capacity_bins"].values())
        return {"bases": summaries, "legacy_sessions": len(self.legacy_sessions)}
