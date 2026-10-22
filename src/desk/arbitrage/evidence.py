"""Prospective scenario/rejection registry. Captures exact retained inputs, never retroactive purchases."""

from __future__ import annotations

from datetime import datetime

from desk.arbitrage.repo import Repo
from desk.arbitrage.views import snapshot


def archive(repo: Repo, now: datetime) -> int:
    state = snapshot(repo, now)
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
