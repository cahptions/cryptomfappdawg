"""Exact-identity price evidence. Asking prices never become transactions or automatically qualified routes."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from statistics import median
from typing import Any, Literal

from pydantic import Field

from desk.arbitrage.contracts import Identifier, Money, Product, Record, public_url
from desk.arbitrage.repo import Repo


class Comparable(Record):
    id: Identifier
    product_id: Identifier
    title: str = Field(min_length=1, max_length=240)
    channel: str = Field(min_length=1, max_length=80)
    url: str
    price: Money
    currency: str = Field(pattern="^[A-Z]{3}$")
    region: str = Field(min_length=2, max_length=80)
    condition: str = Field(min_length=1, max_length=50)
    pack_count: int | None = Field(default=None, ge=1, le=10000)
    gtin: str | None = None
    evidence_type: Literal["ASKING", "REPORTED_SOLD", "TRANSACTION_RECORD", "OWN_NET", "REFERENCE"] = "ASKING"
    at: datetime
    shipping: Money | None = None
    evidence_note: str = Field(min_length=1, max_length=1000)


def save(repo: Repo, value: Comparable) -> None:
    public_url(value.url)
    if value.at.tzinfo is None:
        raise ValueError("Comparable timestamp requires timezone")
    normalized_gtin = Product(id="validate", title="validate", gtin=value.gtin).gtin if value.gtin else None
    value = value.model_copy(update={"at": value.at.astimezone(UTC), "gtin": normalized_gtin})
    if not repo.conn.execute("SELECT 1 FROM arb_products WHERE id=?", (value.product_id,)).fetchone():
        raise ValueError("Record the source product before its resale evidence")
    old = repo.conn.execute("SELECT body FROM arb_comparables WHERE id=?", (value.id,)).fetchone()
    if old and old[0] != value.model_dump_json():
        raise ValueError("Evidence ID already exists with different content")
    repo.conn.execute("INSERT OR IGNORE INTO arb_comparables VALUES (?,?,?,?)",
                      (value.id, value.product_id, value.at.isoformat(), value.model_dump_json()))


def summary(repo: Repo, product: Product, now: datetime, *, currency: str, region: str) -> dict[str, Any]:
    groups: dict[tuple[str, str], dict[str, Comparable]] = {}
    excluded = 0
    for row in repo.conn.execute("SELECT body FROM arb_comparables WHERE product_id=? AND at>=? ORDER BY at",
                                 (product.id, (now-timedelta(days=30)).isoformat())):
        c = Comparable.model_validate_json(row[0])
        gtin = Product(id="normalize", title="normalize", gtin=c.gtin).gtin if c.gtin else None
        if (not product.gtin or gtin != product.gtin or not product.pack_count or c.pack_count != product.pack_count
                or c.condition.casefold() != product.condition.casefold() or c.currency != currency or c.region != region
                or c.at > now or c.at < now-timedelta(days=30)):
            excluded += 1
            continue
        groups.setdefault((c.channel, c.evidence_type), {})[c.url] = c  # newest per listing, not polling count
    cohorts = []
    for (channel, kind), items in sorted(groups.items()):
        records = list(items.values())
        prices = sorted(c.price for c in records)
        cohorts.append({"channel": channel, "evidence_type": kind, "count": len(prices), "window_days": 30,
                        "currency": currency, "low": str(prices[0]), "high": str(prices[-1]),
                        "average": str(sum(prices) / len(prices)), "median": str(median(prices)),
                        "lower_quartile": str(prices[(len(prices)-1)//4]),
                        "latest_at": max(c.at for c in records).isoformat(),
                        "points": [{"at": c.at.isoformat(), "price": str(c.price), "url": c.url} for c in records[:100]],
                        "basis": "Exact declared GTIN, pack, condition, region and currency; item prices excluding "
                        "shipping/tax. ASKING is not sold; reported sold prices may hide accepted offers; "
                        "transaction imports are source claims, not independent verification. OWN_NET is net proceeds."})
    return {"cohorts": cohorts, "excluded": excluded,
            "missing": [] if cohorts else ["Exact comparable GTIN, pack, condition, region and currency evidence"],
            "no_automatic_profit_assumption": True}
