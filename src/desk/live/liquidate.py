"""M8-10 emergency liquidation: STOP first, then *attempt* every exit and report each result truthfully.

Exits run through the LIVE guardian's own path, so they are booked exactly like any other exit: the wallet's actual
token balance is sold, the sale is booked from measured balance changes, and the position closes once its token
account is closed. Anything that does not complete stays open and supervised, and is reported as such.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from desk.live.guardian import LiveGuardian


async def liquidate_all(guardian: LiveGuardian, *, stop_entries: Callable[[], None]) -> list[dict[str, Any]]:
    stop_entries()
    report = []
    for row in guardian.book.open_positions():
        entry = {"position_id": row["id"], "mint": row["mint"], "tokens_raw": row["tokens_raw"]}
        try:
            await guardian.exit_now(row, "EMERGENCY_LIQUIDATION")
        except Exception as exc:  # report, never assume
            report.append({**entry, "result": "FAILED", "reason": type(exc).__name__})
            continue
        after = guardian.book.position(row["id"])
        if after["state"] == "CLOSED":
            report.append({**entry, "result": "CLOSED", "realized_pnl_lamports": after["realized_pnl_lamports"]})
        elif any(sent["position_id"] == row["id"] for sent in guardian.book.pending(("SELL", "CLOSE"))):
            report.append({**entry, "result": "UNRESOLVED", "reason": "no answer yet; the guardian looks it up"})
        elif guardian.book.sold(row["id"]) is not None:
            report.append({**entry, "result": "SOLD", "reason": "tokens sold; closing the token account is retried"})
        else:
            failure = guardian.book.conn.execute(
                "SELECT json_extract(payload_json, '$.status') AS status,"
                " json_extract(payload_json, '$.problems') AS problems"
                " FROM paper_events WHERE position_id = ? AND kind = 'EXIT_FAILED' ORDER BY id DESC LIMIT 1",
                (row["id"],)).fetchone()
            report.append({**entry, "result": "FAILED",
                           "reason": None if failure is None else failure["status"],
                           "problems": None if failure is None else failure["problems"]})
    return report
