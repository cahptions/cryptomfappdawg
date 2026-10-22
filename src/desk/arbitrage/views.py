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


def snapshot(repo: Repo, now: datetime, *, product_ids: list[str] | None = None, query: str = "", category: str = "",
             offset: int = 0, page_size: int = 50, order: str = "profit", source_host: str = "") -> dict[str, Any]:
    settings = repo.settings()
    page_ids, total = repo.catalog_ids(query=query, category=category, offset=offset, limit=page_size, order=order,
                                       current_after=(now-timedelta(minutes=15)).isoformat(),
                                       source_host=source_host, current_at=now.isoformat())
    products = repo.products(product_ids if product_ids is not None else page_ids)
    observations = [row for product in products for row in repo.observations(product.id, limit=500)]
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
                url_failure = repo.conn.execute("SELECT attempted_at,error FROM arb_offer_queue WHERE url=?",
                                                 (offer["url"],)).fetchone()
                if url_failure and url_failure[1]:
                    source_health = {"status": "unavailable", "attempted_at": url_failure[0]}
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
                    catalog_expires_at=min(route.valid_until, model.at+timedelta(minutes=15),
                        model.verified_until if result["qualifies"] and model.verified_until else
                        model.at+timedelta(minutes=15),
                        model.at+timedelta(minutes=5) if result["qualifies"] else
                        model.at+timedelta(minutes=15)).isoformat(),
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
        from desk.arbitrage.comparables import summary

        board.append(
            {
                "product": product.model_dump(mode="json"),
                "offers": offers,
                "routes": local_routes,
                "heat": heats.get(product.id, []),
                "deal_history": deal,
                "comparison": comparison(local_routes),
                "resale_evidence": summary(repo, product, now, currency=settings.currency, region=settings.region),
            }
        )
    board.sort(key=lambda b: (not bool(b["routes"] and b["routes"][0]["qualifies"]), b["product"]["title"]))
    leads = repo.leads()
    for lead in leads:
        published = lead["published_at"] or lead["first_seen_at"]
        lead["fresh"] = now - datetime.fromisoformat(published) <= timedelta(hours=24)
        lead["age_basis"] = "Published time" if lead["published_at"] else "First seen; publication time unknown"
    return {
        "source_groups": [{"host": r[0], "products": r[1], "latest_at": r[2]} for r in repo.conn.execute(
            "SELECT m.host,COUNT(*),MAX(m.latest_at) FROM arb_product_sources m "
            "JOIN arb_products p ON p.id=m.product_id GROUP BY m.host ORDER BY COUNT(*) DESC,m.host LIMIT 300")],
        "categories": [r[0] for r in repo.conn.execute(
            "SELECT DISTINCT json_extract(body,'$.category') FROM arb_products ORDER BY 1 LIMIT 300")],
        "catalog": {"total": total, "offset": offset, "page_size": page_size, "returned": len(products),
                    "query": query, "category": category, "order": order, "source_host": source_host,
                    "scope": "Paged catalog. Numeric profit ranks require a current costed scenario; unknowns remain unranked."},
        "pipeline": {"products": repo.conn.execute("SELECT COUNT(*) FROM arb_products").fetchone()[0],
                     "priced_products": repo.conn.execute(
                         "SELECT COUNT(DISTINCT product_id) FROM arb_observations").fetchone()[0],
                     "pending_merchant_urls": repo.conn.execute(
                         "SELECT COUNT(*) FROM arb_offer_queue WHERE product_id IS NULL").fetchone()[0],
                     "blocked_merchant_urls": repo.conn.execute(
                         "SELECT COUNT(*) FROM arb_offer_queue WHERE error IS NOT NULL").fetchone()[0],
                     "resale_products": repo.conn.execute("SELECT COUNT(DISTINCT product_id) FROM arb_comparables").fetchone()[0],
                     "costed_products": repo.conn.execute("SELECT COUNT(DISTINCT product_id) FROM arb_routes").fetchone()[0]},
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
            "projection_limit": 500 * len(products),
            "scope": "Current catalog page and costed comparisons; capital planning covers this page, not the whole catalog.",
            "unknown_heat": ["momentum", "sell_through"],
            "discovery": "Public feed leads are unverified mentions. Real-network feed acceptance is pending.",
            "note": "Missing demand, prices or fees are unknown; no demonstrated business edge",
        },
    }
