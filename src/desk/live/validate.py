"""M8-09 zero-cost LIVE-path validation: order with a read-only taker → inspect → simulate (sigVerify off).

No key is loaded, nothing is signed and nothing is sent (PLAN §16.6).
"""

from __future__ import annotations

from typing import Any

from desk.signer import programs as p
from desk.signer.inspect import SwapExpectation, associated_token_address, check_simulation, inspect_swap


async def validate_path(swap: Any, rpc: Any, *, taker: str, output_mint: str, amount_lamports: int,
                        slippage_bps: int = 50, max_priority_fee_lamports: int = 1_000_000) -> dict[str, Any]:
    order = await swap.order(p.WSOL, output_mint, amount_lamports, taker, slippage_bps)
    if not order.get("transaction"):
        return {"ok": False, "stage": "order", "error": order.get("errorCode") or order.get("error") or "no transaction"}
    min_out = int(order.get("otherAmountThreshold") or 0)
    expected = SwapExpectation(wallet=taker, side="BUY", input_mint=p.WSOL, output_mint=output_mint,
                               amount_raw=amount_lamports, min_out_raw=max(1, min_out),
                               max_priority_fee_lamports=max_priority_fee_lamports)
    inspection = inspect_swap(order["transaction"], order, expected, await rpc.block_height())
    tracked = [taker, taker, associated_token_address(taker, output_mint)]
    pre = await rpc.accounts(tracked)
    simulation = await rpc.simulate(order["transaction"], tracked)
    fee_budget = 2 * 5000 + max_priority_fee_lamports + 2 * 2_039_280
    sim_problems = check_simulation(simulation, pre, expected, fee_budget)
    return {"ok": not inspection and not sim_problems, "inspection_problems": inspection,
            "simulation_problems": sim_problems, "simulation_error": simulation.get("err"),
            "units_consumed": simulation.get("unitsConsumed"), "router": order.get("router"),
            "in_amount": order.get("inAmount"), "out_amount": order.get("outAmount"),
            "priority_fee_lamports": order.get("prioritizationFeeLamports"),
            "note": "nothing was signed or sent; simulation cannot prove landing, MEV exposure or future state"}
