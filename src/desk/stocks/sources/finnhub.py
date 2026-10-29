"""Finnhub, free key (optional): analyst rating counts, the last four quarters of EPS actual vs estimate, and the
next earnings date. Display only on the stock page; without ``FINNHUB_API_KEY`` these sections are simply absent.

Shapes follow Finnhub's published OpenAPI document (finnhub.io/static/swagger.json, read 2026-10-29). Estimates in
the earnings calendar are non-GAAP, so the page keeps them apart from SEC-reported EPS.
"""

from __future__ import annotations

import json
import math
from datetime import date
from typing import Any

from desk.core.errors import ProviderError, ProviderErrorKind
from desk.providers.http import ProviderHttp
from desk.stocks.sources.validation import iso_day, schema_boundary

HOURS = {"bmo": "before the open", "amc": "after the close", "dmh": "during market hours"}


def _num(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    out = float(value)
    return out if math.isfinite(out) else None


def _count(value: Any) -> int:
    n = _num(value)
    if n is None or n < 0:
        raise ValueError("count")
    return int(n)


def _list(body: bytes) -> list[Any]:
    value = json.loads(body)
    if not isinstance(value, list):
        raise ValueError("expected list")
    return value


@schema_boundary("finnhub recommendation")
def parse_recommendations(body: bytes) -> list[dict[str, Any]]:
    """Monthly analyst rating counts, newest first."""
    out = []
    for r in _list(body):
        period = iso_day(r.get("period"))
        if period is None:
            continue
        out.append({"period": period, **{k: _count(r.get(src)) for k, src in (
            ("strong_buy", "strongBuy"), ("buy", "buy"), ("hold", "hold"), ("sell", "sell"),
            ("strong_sell", "strongSell"))}})
    return sorted(out, key=lambda r: str(r["period"]), reverse=True)[:12]


@schema_boundary("finnhub earnings")
def parse_earnings(body: bytes) -> list[dict[str, Any]]:
    """Reported quarters, oldest first: EPS actual vs the consensus estimate."""
    out = []
    for r in _list(body):
        period = iso_day(r.get("period"))
        actual, estimate = _num(r.get("actual")), _num(r.get("estimate"))
        if period is None or (actual is None and estimate is None):
            continue
        out.append({"period": period, "year": r.get("year") if isinstance(r.get("year"), int) else None,
                    "quarter": r.get("quarter") if isinstance(r.get("quarter"), int) else None,
                    "actual": actual, "estimate": estimate, "surprise": _num(r.get("surprise")),
                    "surprise_pct": _num(r.get("surprisePercent"))})
    return sorted(out, key=lambda r: str(r["period"]))[-8:]


@schema_boundary("finnhub earnings calendar")
def parse_earnings_calendar(body: bytes, symbol: str) -> list[dict[str, Any]]:
    value = json.loads(body)
    if not isinstance(value, dict) or not isinstance(value.get("earningsCalendar", []), list):
        raise ValueError("expected calendar")
    out = []
    for r in value.get("earningsCalendar") or []:
        day = iso_day(r.get("date"))
        if day is None or r.get("symbol") not in (None, symbol):
            continue
        out.append({"date": day, "when": HOURS.get(str(r.get("hour") or "").lower()),
                    "year": r.get("year") if isinstance(r.get("year"), int) else None,
                    "quarter": r.get("quarter") if isinstance(r.get("quarter"), int) else None,
                    "eps_estimate": _num(r.get("epsEstimate")), "eps_actual": _num(r.get("epsActual")),
                    "revenue_estimate": _num(r.get("revenueEstimate")), "revenue_actual": _num(r.get("revenueActual"))})
    return sorted(out, key=lambda r: str(r["date"]))


class FinnhubClient:
    def __init__(self, http: ProviderHttp) -> None:
        self.http = http

    @property
    def configured(self) -> bool:
        return self.http.has_key

    async def _get(self, endpoint: str, params: dict[str, Any]) -> bytes:
        if not self.configured:
            raise ProviderError(self.http.name, ProviderErrorKind.HTTP_ERROR, "FINNHUB_API_KEY is not in secrets.env")
        response = await self.http.request(endpoint, params=params)
        if response.status != 200:
            raise ProviderError(self.http.name, ProviderErrorKind.HTTP_ERROR, f"{endpoint}: HTTP {response.status}",
                                response.status)
        return response.body

    async def recommendations(self, symbol: str) -> list[dict[str, Any]]:
        return parse_recommendations(await self._get("recommendation", {"symbol": symbol}))

    async def earnings(self, symbol: str) -> list[dict[str, Any]]:
        return parse_earnings(await self._get("earnings", {"symbol": symbol}))

    async def earnings_calendar(self, symbol: str, start: date, end: date) -> list[dict[str, Any]]:
        return parse_earnings_calendar(await self._get("earnings_calendar", {
            "symbol": symbol, "from": start.isoformat(), "to": end.isoformat()}), symbol)


__all__ = ["FinnhubClient", "parse_earnings", "parse_earnings_calendar", "parse_recommendations"]
