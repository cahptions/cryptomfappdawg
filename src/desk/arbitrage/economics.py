"""Transparent scenario economics; no default marketplace fee claims."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from typing import Any

from desk.arbitrage.contracts import Observation, Product, Route, Settings
from desk.core.hashing import canonical_json, sha256_hex

CENT = Decimal(".01")


def cost(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_CEILING)


def credit(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_FLOOR)


def evaluate(product: Product, offer: Observation, route: Route, settings: Settings, now: datetime) -> dict[str, Any]:
    blocked: list[str] = []
    if not route.enabled:
        blocked.append("Selling route is paused")
    if (
        not product.identity_confirmed
        or not product.pack_count
        or not route.identity_confirmed
        or not (product.gtin or (product.brand and product.mpn))
    ):
        blocked.append("Exact product/variant match needs confirmation")
    if any(
        getattr(offer.product, key) != getattr(product, key)
        for key in ("brand", "mpn", "gtin", "variant", "pack_count", "condition")
    ):
        blocked.append("Offer identity differs from the current reviewed product; renew its exact delivery check")
    if route.eligible is not True:
        blocked.append("Seller eligibility is unconfirmed or restricted")
    if offer.availability != "VERIFIED_AVAILABLE" or not offer.verified_until or offer.verified_until < now or offer.at > now:
        blocked.append("Offer needs current regional availability verification")
    if offer.region != settings.region:
        blocked.append("Delivery region is unconfirmed or differs from the business profile")
    if now - offer.at > timedelta(minutes=5) or offer.quantity == 0:
        blocked.append("Availability must be rechecked within five minutes and quantity must be positive")
    if route.valid_until < now:
        blocked.append("Selling-price/fee scenario is stale")
    if not route.reviewed or not route.fee_version or not route.evidence_note:
        blocked.append("Channel costs and price evidence need a dated operator review")
    if route.currency != offer.currency or route.currency != settings.currency:
        blocked.append("Currency conversion is unsupported; use a same-currency route")
    if route.monthly_cost > settings.max_monthly_cost:
        blocked.append("Channel exceeds the configured monthly-cost budget")
    if route.days_to_cash is None or route.days_to_cash > settings.max_days:
        blocked.append("Cash-cycle timing is unknown or outside the planning horizon")
    if not route.demand_limit:
        blocked.append("Supported demand quantity is missing")
    fields = {
        "source shipping": offer.shipping,
        "purchase tax": offer.tax,
        "fee rate": route.fee_fraction,
        "fixed fee": route.fixed_fee,
        "fulfillment": route.fulfillment_cost,
        "returns allowance": route.return_allowance,
        "overhead": route.overhead,
    }
    missing = [name for name, value in fields.items() if value is None]
    if missing:
        blocked.append("Missing costs: " + ", ".join(missing))
    result: dict[str, Any] = {
        "id": route.id,
        "product_id": product.id,
        "channel": route.channel,
        "fulfillment": route.fulfillment,
        "destination_url": route.destination_url,
        "evidence_kind": route.evidence_kind,
        "qualifies": False,
        "blockers": blocked,
        "landed_cost": None,
        "net_profit": None,
        "stress_profit": None,
        "roi": None,
        "break_even": None,
        "currency": offer.currency,
        "days_to_cash": route.days_to_cash,
        "effort_minutes": route.effort_minutes,
        "max_units": min(settings.max_units, offer.quantity if offer.quantity is not None else 1, route.demand_limit or 1),
        "price": str(route.sale_price),
    }
    if not missing and route.currency == offer.currency == settings.currency:
        landed = cost(offer.price + (offer.shipping or Decimal(0)) + (offer.tax or Decimal(0)))
        other = sum(
            (
                route.fixed_fee or Decimal(0),
                route.fulfillment_cost or Decimal(0),
                route.return_allowance or Decimal(0),
                route.overhead or Decimal(0),
            ),
            Decimal(0),
        )
        fee = route.fee_fraction or Decimal(0)
        net = credit(route.sale_price) - cost(route.sale_price * fee) - landed - cost(other)
        stress = credit(route.stress_price) - cost(route.stress_price * fee) - landed - cost(other)
        break_even = None if fee == 1 else cost((landed + other) / (1 - fee))
        result.update(
            landed_cost=str(landed),
            net_profit=str(net),
            stress_profit=str(stress),
            roi=str(net / landed) if landed else None,
            break_even=str(break_even) if break_even else None,
            profit_per_capital_day=(str(net / landed / route.days_to_cash) if landed and route.days_to_cash else None),
        )
        if net < settings.min_profit or stress <= 0 or landed <= 0:
            blocked.append("Conservative economics do not meet the profit/cost requirements")
        if route.stress_price > route.sale_price:
            blocked.append("Stress price cannot exceed the base selling price")
    result["qualifies"] = not blocked
    result["input_hash"] = sha256_hex(
        canonical_json(
            {
                "spec": "arb-economics-v1",
                "product": product.model_dump(mode="json"),
                "offer": offer.model_dump(mode="json"),
                "route": route.model_dump(mode="json"),
                "settings": settings.model_dump(mode="json"),
            }
        )
    )
    return result


def allocate(
    routes: list[dict[str, Any]], settings: Settings, committed: Decimal, exposure: dict[str, Decimal] | None = None
) -> dict[str, Any]:
    """Bounded deterministic marginal-profit baseline. No claim of a globally optimal knapsack solution."""
    cash = max(Decimal(0), (settings.capital or Decimal(0)) - settings.reserve - committed)
    if settings.capital is None:
        return {
            "basket": [],
            "cash_remaining": "0",
            "cash_available": "0",
            "reason": "Enter separate business capital in Settings",
            "method": "bounded-greedy-v1",
            "estimated_profit": "0",
        }
    budget = cash
    selected: list[dict[str, Any]] = []
    used: dict[str, Decimal] = dict(exposure or {})
    channels: dict[str, Decimal] = {}
    products: set[str] = set()
    # Choose one destination per product in V1. Subsequent units cannot be duplicated on a second channel.
    for route in sorted((r for r in routes if r["qualifies"]), key=lambda r: (-Decimal(r["stress_profit"]), r["id"]))[:200]:
        product, channel = route["product_id"], route["channel"]
        if product in products:
            continue
        unit_cost = Decimal(route["landed_cost"])
        if unit_cost <= 0:
            continue
        product_room = max(
            Decimal(0),
            ((settings.capital or Decimal(0)) - settings.reserve) * settings.max_product_fraction - used.get(product, Decimal(0)),
        )
        # Existing inventory has no reliable destination yet: charge it against every channel conservatively.
        channel_room = max(
            Decimal(0),
            ((settings.capital or Decimal(0)) - settings.reserve) * settings.max_channel_fraction
            - committed
            - channels.get(channel, Decimal(0)),
        )
        units = min(route["max_units"], int(min(cash, product_room, channel_room) // unit_cost))
        if units <= 0:
            continue
        spend = unit_cost * units
        selected.append(
            {
                **route,
                "units": units,
                "commitment": str(spend),
                "estimated_total_profit": str(Decimal(route["net_profit"]) * units),
            }
        )
        cash -= spend
        used[product] = used.get(product, Decimal(0)) + spend
        channels[channel] = channels.get(channel, Decimal(0)) + spend
        products.add(product)
    return {
        "basket": selected,
        "cash_remaining": str(cash),
        "cash_available": str(budget),
        "estimated_profit": str(sum((Decimal(r["estimated_total_profit"]) for r in selected), Decimal(0))),
        "method": "bounded-greedy-v1",
        "reason": "Reviewed scenarios, not purchases or guaranteed profit",
        "keep_cash_alternative": str(budget),
    }
