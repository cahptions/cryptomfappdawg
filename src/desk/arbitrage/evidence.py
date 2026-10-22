"""Prospective scenario/rejection registry. Captures exact retained inputs, never retroactive purchases."""

from __future__ import annotations

from datetime import datetime

from desk.arbitrage.repo import Repo
from desk.arbitrage.views import snapshot


def _archive(repo: Repo, now: datetime) -> int:
    route_ids = [r[0] for r in repo.conn.execute("SELECT DISTINCT product_id FROM arb_routes")]
    state = snapshot(repo, now, product_ids=route_ids)
    products = {b["product"]["id"]: b["product"] for b in state["board"]}
    observations = {o["id"]: o for b in state["board"] for o in b["offers"]}
    routes = {r.id: r.model_dump(mode="json") for r in repo.routes()}
    for result in state["routes"]:
        decision = {k: v for k, v in result.items() if k != "decision_at"}
        repo.archive_decisions(
            now.isoformat(),
            {
                "specification": "arbitrage-prospective-scenarios-v1",
                "decision_at": now.isoformat(),
                "result": decision,
                "settings": state["settings"],
                "product": products[result["product_id"]],
                "observation": observations[result["observation_id"]],
                "route": routes[result["route_id"]],
                "source_status": state["jobs"].get(observations[result["observation_id"]]["source_id"]),
                "basis": "Prospective scenario/rejection, not a purchase, historical backtest or completed sale",
            },
        )
    from decimal import Decimal

    repo.conn.execute("DELETE FROM arb_opportunity_index")
    for row in state["board"]:
        candidates = [r for r in row["routes"] if r.get("source_current") and r.get("scenario_current")
                      and r["stress_profit"] is not None]
        if candidates:
            best = max(candidates, key=lambda r: Decimal(r["stress_profit"]))
            import json

            repo.conn.execute("INSERT INTO arb_opportunity_index VALUES (?,?,?,?,?)",
                              (row["product"]["id"], now.isoformat(), float(best["stress_profit"]),
                               float(best["roi"]) if best["roi"] is not None else None, json.dumps(best)))
    # Bounded retention. Exact replay is possible only for retained records (full inputs are included).
    repo.conn.execute(
        "DELETE FROM arb_decisions WHERE input_hash NOT IN (SELECT input_hash FROM arb_decisions ORDER BY at DESC LIMIT 5000)"
    )
    from datetime import timedelta

    repo.conn.execute(
        "DELETE FROM arb_decisions WHERE at<? AND json_extract(body,'$.observation.method')='ebay-browse-active-listing-v1'",
        ((now - timedelta(hours=24)).isoformat(),),
    )
    return len(state["routes"])



def archive(repo: Repo, now: datetime) -> int:
    from contextlib import nullcontext

    from desk.storage.db import transaction

    with nullcontext() if repo.conn.in_transaction else transaction(repo.conn):
        return _archive(repo, now)
