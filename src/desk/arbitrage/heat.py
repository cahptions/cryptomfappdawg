"""Evidence counts and within-channel empirical ratings. Scores are descriptive ranks, never probabilities."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any

from desk.arbitrage.contracts import Product, SignalObservation

DIMENSIONS = {
    "volume": ("OWN_SALE", "CONFIRMED_UNITS"),
    "popularity": ("VIEWS", "WATCHERS", "MENTIONS"),
    "competition": ("OFFERS",),
}


def profiles(signals: list[SignalObservation], now: datetime, products: list[Product]) -> dict[str, list[dict[str, Any]]]:
    # Never pool different metrics or channels; caller-supplied records retain operator provenance.
    grouped: dict[tuple[str, str, str], list[SignalObservation]] = defaultdict(list)
    for row in signals:
        if now - timedelta(days=90) <= row.at <= now and row.available_at <= now:
            grouped[(row.product_id, row.channel, row.kind)].append(row)
    out: dict[str, list[dict[str, Any]]] = defaultdict(list)
    identities = {p.id: p for p in products}
    cohort: dict[tuple[str, str, str, str, int], list[float]] = defaultdict(list)
    metrics: list[dict[str, Any]] = []
    for (product, channel, kind), rows in grouped.items():
        recent = [r for r in rows if r.at >= now - timedelta(days=7)]
        is_flow = kind in {"OWN_SALE", "CONFIRMED_UNITS", "MENTIONS", "SOLD_LISTINGS"}
        value = sum(r.value for r in recent) if is_flow else max(rows, key=lambda r: r.at).value
        supported = len(recent) >= 3 and len({r.at.date() for r in recent}) >= 2
        stale = max(r.at for r in rows) < now - timedelta(days=2)
        identity = identities.get(product)
        comparable = bool(identity and identity.category != "Uncategorized" and identity.pack_count)
        key = (
            channel,
            kind,
            identity.category if identity else "",
            identity.condition if identity else "",
            identity.pack_count or 0 if identity else 0,
        )
        metric: dict[str, Any] = {
            "product_id": product,
            "channel": channel,
            "kind": kind,
            "value": value,
            "count": len(recent),
            "days": len({r.at.date() for r in recent}),
            "window_days": 7,
            "stale": stale,
            "supported": supported and not stale and comparable,
            "cohort_key": key,
            "latest_at": max(r.at for r in rows).isoformat(),
            "basis": "Operator-imported observations; source claims are not independently verified",
            "rating": "UNKNOWN",
            "score": None,
        }
        metrics.append(metric)
        if metric["supported"]:
            cohort[key].append(value)
    for metric in metrics:
        values = cohort[metric["cohort_key"]]
        metric["cohort_size"] = len(values)
        if metric["supported"] and len(values) >= 5:
            value = metric["value"]
            score = 100 * (sum(v < value for v in values) + 0.5 * sum(v == value for v in values)) / len(values)
            if value == 0:
                score = 0  # known zero activity/pressure is cool; missing evidence still abstains
            metric.update(
                score=round(score, 1),
                rating="HOT" if score >= 85 else "WARM" if score >= 60 else "MODERATE" if score >= 25 else "COOL",
            )
        out[metric["product_id"]].append(metric)
    return dict(out)
