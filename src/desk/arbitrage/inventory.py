"""Append-only lot accounting. Amount is total cost, net sale proceeds or refund, never a unit price."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from desk.arbitrage.contracts import InventoryEvent
from desk.core.errors import DeskError


def ledger(events: list[InventoryEvent]) -> dict[str, Any]:
    if len({e.currency for e in events}) > 1:
        raise DeskError("Mixed-currency inventory cannot be combined; no conversion is supported")
    lots: dict[str, dict[str, Any]] = {}
    for event in events:
        lot = lots.get(event.lot_id)
        if event.kind == "RECEIVE":
            if lot is not None or event.units < 1:
                raise DeskError("Each lot must begin with exactly one positive RECEIVE event")
            lot = {
                "currency": event.currency,
                "id": event.lot_id,
                "product_id": event.product_id,
                "received_at": event.at,
                "units": event.units,
                "reserved": 0,
                "sold": 0,
                "returned": 0,
                "quarantined": 0,
                "unit_cost": event.amount / event.units,
                "proceeds": Decimal(0),
                "disposed_cost": Decimal(0),
                "last_at": event.at,
            }
            lots[event.lot_id] = lot
            continue
        if lot is None or lot["product_id"] != event.product_id or event.at < lot["last_at"]:
            raise DeskError("Unknown lot, mismatched product or event earlier than its preceding event")
        lot["last_at"] = event.at
        units = event.units
        if units <= 0 and event.kind != "ADJUST_COST":
            raise DeskError("Inventory quantity must be positive")
        free = lot["units"] - lot["reserved"]
        if event.kind == "RESERVE" and units <= free:
            lot["reserved"] += units
        elif event.kind == "RELEASE" and units <= lot["reserved"]:
            lot["reserved"] -= units
        elif event.kind == "SELL" and units <= free:
            lot["units"] -= units
            lot["sold"] += units
            lot["proceeds"] += event.amount
            lot["disposed_cost"] += lot["unit_cost"] * units
        elif event.kind == "RETURN" and units <= lot["sold"] - lot["returned"]:
            lot["returned"] += units
            lot["quarantined"] += units
            lot["proceeds"] -= event.amount
            lot["disposed_cost"] -= lot["unit_cost"] * units
        elif event.kind == "RESTOCK" and units <= lot["quarantined"]:
            lot["quarantined"] -= units
            lot["units"] += units
        elif event.kind == "DAMAGE" and units <= free:
            lot["units"] -= units
            lot["disposed_cost"] += lot["unit_cost"] * units
        elif event.kind == "DISCARD_RETURN" and units <= lot["quarantined"]:
            lot["quarantined"] -= units
            lot["disposed_cost"] += lot["unit_cost"] * units
        elif event.kind == "ADJUST_COST" and units == 0 and lot["units"] > 0 and lot["sold"] == 0:
            # Before first sale only: changing basis after a sale would misprice a subsequent return.
            lot["unit_cost"] += event.amount / (lot["units"] + lot["quarantined"])
        else:
            raise DeskError("Event exceeds available, reserved, sold or returned units; release reservations before a sale")
    committed = sum((r["unit_cost"] * (r["units"] + r["quarantined"]) for r in lots.values()), Decimal(0))
    realized = sum((r["proceeds"] - r["disposed_cost"] for r in lots.values()), Decimal(0))
    exposure: dict[str, Decimal] = {}
    for lot in lots.values():
        value = lot["unit_cost"] * (lot["units"] + lot["quarantined"])
        exposure[lot["product_id"]] = exposure.get(lot["product_id"], Decimal(0)) + value
        lot["available"] = lot["units"] - lot["reserved"]
        lot["committed_cost"] = str(value)
        lot["realized_profit"] = str(lot["proceeds"] - lot["disposed_cost"])
        for key in ("unit_cost", "proceeds", "disposed_cost"):
            lot[key] = str(lot[key])
        for key in ("received_at", "last_at"):
            lot[key] = lot[key].isoformat()
    return {
        "lots": list(lots.values()),
        "committed": str(committed),
        "realized_profit": str(realized),
        "product_exposure": {k: str(v) for k, v in exposure.items()},
    }
