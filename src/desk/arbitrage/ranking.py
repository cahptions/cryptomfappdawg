"""Separate dollar-profit, capital-efficiency and observed-discount rankings; no blended score."""

from __future__ import annotations

from decimal import Decimal
from typing import Any


def comparison(routes: list[dict[str, Any]]) -> dict[str, Any]:
    # Collapse multiple sourcing observations to one best modeled alternative per destination.
    # Qualified routes come first. Incomplete/stale routes remain research only, regardless of the profit shown.
    alternatives: dict[str, dict[str, Any]] = {}
    for row in routes:
        old = alternatives.get(row["route_id"])
        if old is None or order(row) < order(old):
            alternatives[row["route_id"]] = row
    modeled = sorted((r for r in alternatives.values() if r["net_profit"] is not None), key=order)
    qualified = [r for r in modeled if r["qualifies"]]
    winner = qualified[0] if qualified else None
    return {
        "recommended": winner,
        "modeled_best": modeled[0] if modeled else None,
        "alternatives": sorted(alternatives.values(), key=order),
        "basis": "Highest stress-case dollars per unit among covered qualified routes; research-only otherwise",
        "reason": (
            "Highest conservative dollars per unit among the checked alternatives; not a global market optimum"
            if winner
            else "No buy recommendation: required checks or supported positive economics are missing"
        ),
    }


def order(row: dict[str, Any]) -> tuple[bool, bool, bool, bool, Decimal, str]:
    return (
        not row["qualifies"],
        not row.get("source_current", True),
        not row.get("scenario_current", True),
        row["stress_profit"] is None,
        -Decimal(row["stress_profit"]) if row["stress_profit"] is not None else Decimal(0),
        str(row["id"]),
    )


def rankings(board: list[dict[str, Any]]) -> dict[str, Any]:
    choices: dict[str, dict[str, dict[str, Any]]] = {"profit": {}, "roi": {}}
    for b in board:
        current = [
            r
            for r in b["comparison"]["alternatives"]
            if r.get("source_current")
            and r.get("scenario_current")
            and r["stress_profit"] is not None
            and Decimal(r["stress_profit"]) > 0
        ]
        if current:
            pid = b["product"]["id"]
            choices["profit"][pid] = min(current, key=lambda r: (-Decimal(r["stress_profit"]), r["id"]))
            roi = [r for r in current if r["roi"] is not None]
            if roi:
                choices["roi"][pid] = min(roi, key=lambda r: (-Decimal(r["roi"]), r["id"]))
    drops: list[dict[str, Any]] = []
    for b in board:
        for deal in b["deal_history"]:
            if deal["discount_fraction"] and Decimal(deal["discount_fraction"]) > 0 and deal["current"]:
                drops.append({"product_id": b["product"]["id"], **deal})
    drops.sort(key=lambda d: (-Decimal(d["discount_fraction"]), d["product_id"], d["source_id"]))
    return {
        "specification": "arb-research-ranking-v2",
        "profit": sorted(choices["profit"], key=lambda p: (-Decimal(choices["profit"][p]["stress_profit"]), p)),
        "roi": sorted(choices["roi"], key=lambda p: (-Decimal(choices["roi"][p]["roi"]), p)),
        "selections": {kind: {pid: row["id"] for pid, row in rows.items()} for kind, rows in choices.items()},
        "price_drops": drops,
        "scope": "Rankings cover stored evidence only. Modeled candidates with unresolved checks are not buy recommendations.",
    }
