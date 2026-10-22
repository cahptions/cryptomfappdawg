"""Read-only projections from authoritative Arbitrage records."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal
from statistics import median
from typing import Any

from desk.arbitrage.contracts import Observation, Route
from desk.arbitrage.economics import allocate, evaluate
from desk.arbitrage.heat import profiles
from desk.arbitrage.inventory import ledger
from desk.arbitrage.ranking import comparison, rankings
from desk.arbitrage.repo import Repo


def snapshot(repo: Repo, now: datetime) -> dict[str, Any]:
    settings, products, observations = repo.settings(), repo.products(), repo.observations()
    signals, routes, jobs = repo.signals(), repo.routes(), repo.jobs()
    inventory = ledger(repo.events())
    heats = profiles(signals, now, products)
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    history: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in observations:
        if datetime.fromisoformat(row["at"]) <= now:
            latest.setdefault((row["product"]["id"], row["source_id"]), row)
            if datetime.fromisoformat(row["at"]) >= now - timedelta(days=30):
                history[(row["product"]["id"], row["source_id"], row["currency"])].append(row)
    offer_map: dict[str, list[dict[str, Any]]] = defaultdict(list)
    route_map: dict[str, list[Route]] = defaultdict(list)
    for (pid, _), row in latest.items():
        offer_map[pid].append(row)
    for route in routes:
        if route.enabled:
            route_map[route.product_id].append(route)
    combinations = sum(len(offers) * len(route_map[pid]) for pid, offers in offer_map.items())
    analysis_error = (
        "Comparison limit exceeded (5000 offer/route pairs). Pause unused selling routes in Where to sell."
        if combinations > 5000
        else None
    )
    evaluated: list[dict[str, Any]] = []
    board: list[dict[str, Any]] = []
    for product in products:
        offers = offer_map[product.id]
        local_routes = []
        for offer in offers:
            model = Observation.model_validate({k: v for k, v in offer.items() if k not in {"id", "input_hash"}})
            for route in () if analysis_error else route_map[product.id]:
                result = evaluate(product, model, route, settings, now)
                source_health = jobs.get(offer["source_id"], {})
                if (
                    source_health.get("status") == "unavailable"
                    and source_health.get("attempted_at")
                    and datetime.fromisoformat(source_health["attempted_at"]) >= model.at
                ):
                    result["blockers"].append("Source failed after this availability check; verification must be renewed")
                    result["qualifies"] = False
                result.update(
                    id=f"{route.id}:{offer['id']}",
                    observation_id=offer["id"],
                    source_url=offer["url"],
                    observed_at=offer["at"],
                    decision_at=now.isoformat(),
                    route_id=route.id,
                    source_current=timedelta(0) <= now - model.at <= timedelta(minutes=15)
                    and model.availability != "UNAVAILABLE"
                    and not (
                        source_health.get("status") == "unavailable"
                        and source_health.get("attempted_at")
                        and datetime.fromisoformat(source_health["attempted_at"]) >= model.at
                    ),
                    scenario_current=route.valid_until >= now,
                )
                local_routes.append(result)
        local_routes.sort(key=lambda r: (not r["qualifies"], -Decimal(r["stress_profit"] or "-1000000000"), r["id"]))
        evaluated.extend(local_routes)
        # Daily medians of the same source/product; do not weight frequently-polled days more heavily.
        deal: list[dict[str, Any]] = []
        for offer in offers:
            by_day: dict[str, list[Decimal]] = {}
            for record in history[(product.id, offer["source_id"], offer["currency"])]:
                old = record["product"]
                exact = (
                    product.identity_confirmed
                    and old.get("identity_confirmed")
                    and all(
                        old.get(k) == getattr(product, k) for k in ("brand", "mpn", "gtin", "variant", "pack_count", "condition")
                    )
                )
                if exact and record["source_id"] == offer["source_id"] and record["currency"] == offer["currency"]:
                    by_day.setdefault(record["at"][:10], []).append(Decimal(record["price"]))
            prior_days = [median(v) for k, v in by_day.items() if k < now.date().isoformat()]
            baseline = median(prior_days) if len(prior_days) >= 6 else None
            deal.append(
                {
                    "source_id": offer["source_id"],
                    "historical_daily_median": str(baseline) if baseline else None,
                    "discount_fraction": str(1 - Decimal(offer["price"]) / baseline) if baseline else None,
                    "days": len(by_day),
                    "baseline_days": len(prior_days),
                    "current": now - datetime.fromisoformat(offer["at"]) <= timedelta(minutes=15)
                    and offer["availability"] != "UNAVAILABLE",
                    "basis": "Same-source observed asking prices; not completed sales",
                }
            )
        board.append(
            {
                "product": product.model_dump(mode="json"),
                "offers": offers,
                "routes": local_routes,
                "heat": heats.get(product.id, []),
                "deal_history": deal,
                "comparison": comparison(local_routes),
            }
        )
    board.sort(key=lambda b: (not bool(b["routes"] and b["routes"][0]["qualifies"]), b["product"]["title"]))
    leads = repo.leads()
    for lead in leads:
        published = lead["published_at"] or lead["first_seen_at"]
        lead["fresh"] = now - datetime.fromisoformat(published) <= timedelta(hours=24)
        lead["age_basis"] = "Published time" if lead["published_at"] else "First seen; publication time unknown"
    return {
        "generated_at": now.isoformat(),
        "desk": "ARBITRAGE",
        "read_only_marketplaces": True,
        "settings": settings.model_dump(mode="json"),
        "board": board,
        "rankings": rankings(board),
        "leads": leads,
        "routes": evaluated,
        "configured_routes": [r.model_dump(mode="json") for r in routes],
        "sources": [s.model_dump(mode="json") for s in repo.sources()],
        "jobs": jobs,
        "inventory": inventory,
        "qualified_count": sum(r["qualifies"] for r in evaluated),
        "basket": allocate(
            evaluated,
            settings,
            Decimal(inventory["committed"]),
            {k: Decimal(v) for k, v in inventory["product_exposure"].items()},
        ),
        "coverage": {
            "comparison_pairs": combinations,
            "analysis_error": analysis_error,
            "observations_returned": len(observations),
            "projection_limit": 5000,
            "scope": "Bounded V1 observation board and reviewed scenarios; not broad-market discovery",
            "unknown_heat": ["momentum", "sell_through"],
            "discovery": "Public feed leads are unverified mentions. Real-network feed acceptance is pending.",
            "note": "Missing demand, prices or fees are unknown; no demonstrated business edge",
        },
    }
