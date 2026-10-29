"""Massive (formerly Polygon.io) Stocks Basic, free: end-of-day bars for every US stock, splits and dividends, plus
company details, news and icons for the stock page (display only, never research inputs).

The free plan (verified 2026-10-21 on massive.com/pricing): $0, personal licence, 5 requests a minute, 2 years of
history, end-of-day data. One "grouped daily" request returns the day's bar for every US stock, so the daily refresh
is a single request and two years of history is about 500 (one background backfill). The key is the ``apiKey``
query parameter (``MASSIVE_API_KEY`` in secrets.env).

Bars are stored unadjusted (``adjusted=false``) and splits are applied when reading, so a split announced later
never silently rewrites history already used for a decision.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any
from urllib.parse import parse_qs, quote, urlparse

from desk.core.errors import ProviderError, ProviderErrorKind, SchemaError
from desk.providers.http import ProviderHttp
from desk.stocks.contracts import Bar, Dividend, Split
from desk.stocks.sources.validation import clean_text, iso_day, json_object, number, schema_boundary, web_url

ICON_HOSTS = {"api.massive.com", "api.polygon.io"}
ICON_TYPES = {b"\x89PNG\r\n\x1a\n": "image/png", b"\xff\xd8\xff": "image/jpeg"}
ICON_MAX_BYTES = 300_000
SENTIMENTS = {"positive", "negative", "neutral"}


def _market_day(epoch_ms: int) -> date:
    """A grouped daily bar is stamped at the 16:00 New York close (20:00 or 21:00 UTC; documented example
    1602705600000 = 2020-10-14 20:00 UTC), so its UTC date is the trading day."""
    return datetime.fromtimestamp(epoch_ms / 1000, UTC).date()


@schema_boundary("massive grouped daily")
def parse_grouped(body: bytes) -> list[Bar]:
    try:
        d = json_object(body)
        if d.get("status") not in ("OK", "DELAYED"):
            raise SchemaError("massive grouped daily: unavailable response status")
        out = []
        for r in d.get("results") or []:
            if not all(k in r for k in ("T", "o", "h", "l", "c", "v", "t")):
                raise ValueError("incomplete bar")
            op, hi, lo, cl = (number(r[k], positive=True) for k in ("o", "h", "l", "c"))
            if hi < max(op, lo, cl) or lo > min(op, hi, cl):
                raise ValueError("invalid OHLC range")
            out.append(Bar(str(r["T"]), _market_day(int(r["t"])), op, hi, lo, cl,
                           number(r["v"], nonnegative=True), None if r.get("vw") is None else number(r["vw"], positive=True),
                           None if r.get("n") is None else int(r["n"])))
        return out
    except (ValueError, KeyError, TypeError) as exc:
        raise SchemaError("massive grouped daily: invalid bar response") from exc


@schema_boundary("massive splits")
def parse_splits(body: bytes) -> tuple[list[Split], str | None]:
    try:
        d = json_object(body)
        if d.get("status", "OK") != "OK":
            raise ValueError("unsuccessful response")
        out = [Split(str(r["ticker"]), date.fromisoformat(r["execution_date"]),
                     number(r["split_from"], positive=True), number(r["split_to"], positive=True))
               for r in d.get("results") or []]
        return out, _cursor(d.get("next_url"))
    except (ValueError, KeyError, TypeError) as exc:
        raise SchemaError("massive splits: invalid action response") from exc


@schema_boundary("massive dividends")
def parse_dividends(body: bytes) -> tuple[list[Dividend], str | None]:
    try:
        d = json_object(body)
        if d.get("status", "OK") != "OK":
            raise ValueError("unsuccessful response")
        out = []
        for r in d.get("results") or []:
            if r.get("ex_dividend_date") is None or r.get("cash_amount") is None:
                continue
            out.append(Dividend(str(r["ticker"]), date.fromisoformat(r["ex_dividend_date"]),
                                number(r["cash_amount"], nonnegative=True),
                                date.fromisoformat(r["pay_date"]) if r.get("pay_date") else None,
                                r.get("frequency"), r.get("distribution_type")))
        return out, _cursor(d.get("next_url"))
    except (ValueError, KeyError, TypeError) as exc:
        raise SchemaError("massive dividends: invalid action response") from exc


def _count(value: Any) -> int | None:
    try:
        n = number(value, nonnegative=True)
    except (ValueError, TypeError):
        return None
    return int(n)


def _amount(value: Any) -> float | None:
    try:
        return number(value, positive=True)
    except (ValueError, TypeError):
        return None


def icon_path(url: Any) -> str | None:
    """The request path for a branding icon named by ticker details, only on Massive's own API host."""
    if web_url(url) is None:
        return None
    parts = urlparse(url)
    if parts.scheme != "https" or parts.hostname not in ICON_HOSTS or parts.query or parts.fragment:
        return None
    return parts.path if parts.path.startswith("/v1/reference/company-branding/") and ".." not in parts.path else None


@schema_boundary("massive ticker details")
def parse_ticker_details(body: bytes) -> dict[str, Any]:
    d = json_object(body)
    if d.get("status") not in ("OK", "DELAYED") or not isinstance(d.get("results"), dict):
        raise ValueError("unavailable response")
    r = d["results"]
    raw_address = r.get("address")
    address = raw_address if isinstance(raw_address, dict) else {}
    city, state = clean_text(address.get("city"), 80), clean_text(address.get("state"), 40)
    raw_branding = r.get("branding")
    branding: dict[str, Any] = raw_branding if isinstance(raw_branding, dict) else {}
    return {"ticker": clean_text(r.get("ticker"), 12), "name": clean_text(r.get("name"), 200),
            "description": clean_text(r.get("description"), 4000), "homepage_url": web_url(r.get("homepage_url")),
            "employees": _count(r.get("total_employees")), "list_date": iso_day(r.get("list_date")),
            "market_cap": _amount(r.get("market_cap")), "phone": clean_text(r.get("phone_number"), 40),
            "industry": clean_text(r.get("sic_description"), 120), "exchange": clean_text(r.get("primary_exchange"), 20),
            "shares_outstanding": _amount(r.get("share_class_shares_outstanding"))
            or _amount(r.get("weighted_shares_outstanding")),
            "headquarters": ", ".join(x.title() if x.isupper() and len(x) > 3 else x for x in (city, state) if x) or None,
            "street": clean_text(address.get("address1"), 120), "postal_code": clean_text(address.get("postal_code"), 20),
            "icon_path": icon_path(branding.get("icon_url"))}


@schema_boundary("massive news")
def parse_news(body: bytes, limit: int = 20) -> list[dict[str, Any]]:
    d = json_object(body)
    if d.get("status", "OK") != "OK" or not isinstance(d.get("results", []), list):
        raise ValueError("unavailable response")
    out = []
    for r in d.get("results") or []:
        if not isinstance(r, dict):
            continue
        url, title, published = web_url(r.get("article_url")), clean_text(r.get("title"), 300), r.get("published_utc")
        try:
            when = datetime.fromisoformat(str(published).replace("Z", "+00:00"))
        except ValueError:
            continue
        if url is None or title is None or when.tzinfo is None:
            continue
        raw_publisher = r.get("publisher")
        publisher: dict[str, Any] = raw_publisher if isinstance(raw_publisher, dict) else {}
        insights = [{"ticker": clean_text(i.get("ticker"), 12), "sentiment": i.get("sentiment"),
                     "reasoning": clean_text(i.get("sentiment_reasoning"), 400)}
                    for i in (r.get("insights") or []) if isinstance(i, dict) and i.get("sentiment") in SENTIMENTS]
        out.append({"id": clean_text(r.get("id"), 80) or url, "title": title, "url": url,
                    "summary": clean_text(r.get("description"), 600), "published": when.astimezone(UTC).isoformat(),
                    "source": clean_text(publisher.get("name"), 80), "author": clean_text(r.get("author"), 80),
                    "tickers": [t for t in (clean_text(x, 12) for x in (r.get("tickers") or [])[:12]) if t],
                    "insights": insights[:6]})
        if len(out) >= limit:
            break
    return out


def icon_type(body: bytes) -> str:
    """PNG or JPEG only: an SVG or HTML answer is never served from the desk's origin."""
    if len(body) > ICON_MAX_BYTES:
        raise SchemaError("massive icon: larger than the accepted size")
    for magic, kind in ICON_TYPES.items():
        if body.startswith(magic):
            return kind
    raise SchemaError("massive icon: not a PNG or JPEG image")


def _cursor(next_url: str | None) -> str | None:
    """The next page's cursor (the next_url itself is never requested: it would carry the key back to us)."""
    if not next_url:
        return None
    values = parse_qs(urlparse(next_url).query).get("cursor")
    return values[0] if values else None


class MassiveClient:
    def __init__(self, http: ProviderHttp) -> None:
        self.http = http

    @property
    def configured(self) -> bool:
        return self.http.has_key

    async def _get(self, endpoint: str, *, path: str | None = None, params: dict[str, Any] | None = None) -> bytes:
        if not self.configured:
            raise ProviderError(self.http.name, ProviderErrorKind.HTTP_ERROR, "MASSIVE_API_KEY is not in secrets.env")
        resp = await self.http.request(endpoint, path=path, params=params)
        if resp.status != 200:
            raise ProviderError(self.http.name, ProviderErrorKind.HTTP_ERROR, f"{endpoint}: HTTP {resp.status}", resp.status)
        return resp.body

    async def grouped_daily(self, day: date) -> list[Bar]:
        """Every US stock's bar for ``day`` (empty on a market holiday)."""
        body = await self._get("grouped_daily", path=f"/v2/aggs/grouped/locale/us/market/stocks/{day.isoformat()}",
                               params={"adjusted": "false"})
        return parse_grouped(body)

    async def splits(self, since: date) -> list[Split]:
        return await self._paged("splits", parse_splits, {"execution_date.gte": since.isoformat(), "limit": 1000})

    async def dividends(self, since: date) -> list[Dividend]:
        return await self._paged("dividends", parse_dividends, {"ex_dividend_date.gte": since.isoformat(), "limit": 1000})

    async def ticker_details(self, symbol: str) -> dict[str, Any]:
        return parse_ticker_details(await self._get("ticker_details", path=f"/v3/reference/tickers/{quote(symbol, safe='')}"))

    async def news(self, symbol: str, limit: int = 20) -> list[dict[str, Any]]:
        return parse_news(await self._get("news", params={"ticker": symbol, "order": "desc", "sort": "published_utc",
                                                           "limit": limit}), limit)

    async def icon(self, path: str) -> tuple[str, bytes]:
        body = await self._get("branding", path=path)
        return icon_type(body), body

    async def _paged(self, endpoint: str, parse: Any, params: dict[str, Any]) -> list[Any]:
        out: list[Any] = []
        cursor: str | None = None
        seen: set[str] = set()
        for _ in range(200):
            rows, cursor = parse(await self._get(endpoint, params={**params, **({"cursor": cursor} if cursor else {})}))
            out += rows
            if not cursor:
                return out
            if cursor in seen:
                raise SchemaError(f"massive {endpoint}: pagination repeated; history remains incomplete")
            seen.add(cursor)
        raise SchemaError(f"massive {endpoint}: page limit reached; history remains incomplete")


__all__ = ["MassiveClient", "icon_path", "icon_type", "parse_dividends", "parse_grouped", "parse_news", "parse_splits",
           "parse_ticker_details"]
