"""Automatic read-only resale research. No fuzzy title matching and no auto qualification or purchase."""
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx

from desk.arbitrage.comparables import Comparable, save, summary
from desk.arbitrage.contracts import Product, Route, Source
from desk.arbitrage.ebay import Backoff, Browse
from desk.arbitrage.repo import Repo
from desk.core.errors import DeskError
from desk.core.hashing import sha256_hex
from desk.core.paths import DeskPaths


def exact_comparable(product: Product, item: dict[str, Any], now: datetime) -> Comparable | None:
    try:
        gtin = Product(id="barcode", title="barcode", gtin=item.get("gtin")).gtin
        aspects = {str(a.get("name", "")).casefold(): str(a.get("value", ""))
                   for a in item.get("localizedAspects", []) if isinstance(a, dict)}
        pack = aspects.get("number in pack") or aspects.get("pack size")
        if (not product.gtin or gtin != product.gtin or not pack or not pack.isdigit()
                or int(pack) != product.pack_count
                or str(item.get("condition", "")).casefold() != product.condition.casefold()):
            return None
        return Comparable(id="ebay-comp-" + sha256_hex(str(item["itemId"]) + now.isoformat())[:32],
            product_id=product.id, title=str(item["title"])[:240], channel="eBay", url=item["itemWebUrl"],
            price=Decimal(str(item["price"]["value"])), currency=item["price"]["currency"], region="US",
            condition=product.condition, pack_count=product.pack_count, gtin=gtin, at=now,
            evidence_note="Read-only production Browse detail: exact published GTIN, pack count and condition. "
            "Current asking price, not a completed sale, cash recovery estimate or seller eligibility check.")
    except (ValueError, KeyError, TypeError, AttributeError):
        return None


async def enrich(paths: DeskPaths, browse: Browse, client: httpx.AsyncClient, now: datetime) -> dict[str, Any]:
    with Repo.reader(paths) as repo:
        products = [Product.model_validate_json(r[0]) for r in repo.conn.execute(
            "SELECT p.body FROM arb_products p LEFT JOIN arb_match_state s ON s.product_id=p.id "
            "WHERE json_extract(p.body,'$.gtin') IS NOT NULL AND json_extract(p.body,'$.pack_count') IS NOT NULL "
            "AND lower(json_extract(p.body,'$.condition'))!='unknown' AND (s.attempted_at IS NULL OR s.attempted_at<?) "
            "ORDER BY COALESCE(s.attempted_at,''),p.id LIMIT 2", ((now-timedelta(hours=6)).isoformat(),))]
    matched = checked = 0
    errors = {}
    for product in products:
        error = None
        try:
            source = Source(id="resale-ebay", adapter="ebay_browse", url="https://www.ebay.com/", query=product.gtin)
            rows = await browse.search(client, source, now, limit=20, gtin=product.gtin)
            for row in rows[:5]:
                if not row.external_id:
                    continue
                checked += 1
                record = exact_comparable(product, await browse.item(client, row.external_id), now)
                if record:
                    with Repo.open(paths) as repo:
                        save(repo, record)
                    matched += 1
            with Repo.open(paths) as repo:
                evidence = summary(repo, product, now, currency=repo.settings().currency, region=repo.settings().region)
                cohorts = [c for c in evidence["cohorts"] if c["channel"] == "eBay" and c["evidence_type"] == "ASKING"]
                route_id = "auto-ebay-" + sha256_hex(product.id)[:24]
                if cohorts and cohorts[0]["count"] >= 3 and not repo.conn.execute(
                        "SELECT 1 FROM arb_routes WHERE id=?", (route_id,)).fetchone():
                    c = cohorts[0]
                    repo.save_route(Route(id=route_id, product_id=product.id, channel="eBay",
                        sale_price=Decimal(c["median"]), stress_price=Decimal(c["lower_quartile"]),
                        currency=c["currency"], identity_confirmed=product.identity_confirmed, reviewed=False, eligible=None,
                        valid_until=now+timedelta(hours=24),
                        evidence_note="Automatic research draft from exact matched current asking prices only. "
                        "Fees, demand, delivery and eligibility remain unknown; no buying recommendation."))
        except Backoff as exc:
            error = str(exc)
            errors[product.id] = error
            with Repo.open(paths) as repo:
                repo.job("ebay-provider", {"status": "rate_limited", "retry_at": exc.until.isoformat()})
            break
        except (DeskError, httpx.HTTPError) as exc:
            error = str(exc)[:200] if isinstance(exc, DeskError) else "Network/proxy failure"
            errors[product.id] = error
        with Repo.open(paths) as repo:
            repo.conn.execute("INSERT OR REPLACE INTO arb_match_state VALUES (?,?,?)", (product.id, now.isoformat(), error))
    return {"products_attempted": len(products), "details_checked": checked, "exact_asking_comparables": matched,
            "errors": errors,
            "scope": "Explicit published GTIN/pack/condition identities; never sold-history or qualified purchases"}
