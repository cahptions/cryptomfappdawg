"""M8-10 emergency liquidation: STOP first, then *attempt* every exit and report each result truthfully.

A position is closed only after the signer reports CONFIRMED; its proceeds are then booked at the intent's
guaranteed minimum and flagged for on-chain reconciliation. Anything else leaves the position open and supervised.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from desk.contracts.quotes import QuoteObservation
from desk.paper.ledger import PaperLedger
from desk.security.policy import SOL_MINT


async def liquidate_all(ledger: PaperLedger, *, quote: Callable[[str, str, int], Awaitable[Any]],
                        send_intent: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]],
                        stop_entries: Callable[[], None], clock: Callable[[], datetime],
                        slippage_bps: int = 300) -> list[dict[str, Any]]:
    stop_entries()
    report = []
    for row in ledger.open_positions():
        entry = {"position_id": row["id"], "mint": row["mint"], "tokens_raw": row["tokens_raw"]}
        try:
            q = await quote(row["mint"], SOL_MINT, row["tokens_raw"])
            if not isinstance(q, QuoteObservation) or q.out_amount_raw <= 0:
                report.append({**entry, "result": "FAILED", "reason": "NO_SELL_ROUTE"})
                ledger.set_state(row["id"], "EXIT_BLOCKED", clock(), {"reason": "LIQUIDATION_NO_ROUTE",
                                                                      "attempts": row["exit_attempts"] + 1})
                continue
            min_out = int(Decimal(q.out_amount_raw) * (Decimal(10_000 - slippage_bps) / Decimal(10_000)))
            intent = {"intent_id": f"liq-{row['id']}-{row['exit_attempts'] + 1}"[:80], "side": "SELL",
                      "mint": row["mint"], "amount_raw": row["tokens_raw"], "max_slippage_bps": slippage_bps,
                      "min_out_raw": max(1, min_out), "quote_ref": q.raw_sha256 or "quote",
                      "expires_at": (clock() + timedelta(seconds=25)).isoformat()}
            result = await send_intent({"op": "intent", "intent": intent})
        except Exception as exc:  # report, never assume
            report.append({**entry, "result": "FAILED", "reason": f"{type(exc).__name__}"})
            continue
        if result.get("ok") and result.get("state") == "CONFIRMED":
            ledger.close_position(row["id"], clock(), reason="EMERGENCY_LIQUIDATION", exit_lamports=intent["min_out_raw"],
                                  exit_fees_lamports=0, profiles={"note": "booked at guaranteed minimum; reconcile on-chain",
                                                                  "signature": result.get("signature")})
            report.append({**entry, "result": "CONFIRMED", "signature": result.get("signature")})
        else:
            ledger.set_state(row["id"], "EXIT_BLOCKED", clock(), {"reason": "LIQUIDATION_FAILED",
                                                                  "attempts": row["exit_attempts"] + 1, "signer": result})
            report.append({**entry, "result": "FAILED", "reason": result.get("error") or result.get("state"),
                           "problems": result.get("problems")})
    return report
