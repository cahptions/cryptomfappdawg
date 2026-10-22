"""Bounded read-only Product JSON-LD adapter. No login, cart, checkout, redirects or anti-bot evasion."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from decimal import Decimal
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx

from desk.arbitrage.contracts import Observation, Product, Source
from desk.core.errors import DeskError
from desk.core.hashing import sha256_hex

AGENT = "KESTRELResearch/0.1"
ALLOWED_HOSTS = frozenset({"www.adafruit.com"})
MAX_BYTES = 2_000_000


def validate_source(source: Source) -> None:
    url = urlsplit(source.url)
    if source.adapter == "deal_feed":
        from desk.arbitrage.discovery import descriptor

        descriptor(source)
        return
    if source.adapter == "ebay_browse":
        if source.url != "https://www.ebay.com/" or not source.query or source.product_id:
            raise DeskError("An eBay source needs its fixed public homepage, a search query and no forced product match")
        return
    if source.adapter != "jsonld" or url.hostname not in ALLOWED_HOSTS or not url.path.startswith("/product/"):
        raise DeskError("V1 supports public Adafruit /product/ URLs only; other retailers need a reviewed adapter")
    if url.query or "%" in url.path or ".." in url.path or not url.path.removeprefix("/product/").isdigit():
        raise DeskError("Use a canonical retailer product URL without query parameters")


class Documents(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.active = False
        self.chunks: list[str] = []
        self.documents: list[Any] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "script":
            self.active = dict(attrs).get("type") == "application/ld+json"
            self.chunks = []

    def handle_data(self, data: str) -> None:
        if self.active:
            self.chunks.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self.active:
            self.active = False
            try:
                self.documents.append(json.loads("".join(self.chunks)))
            except ValueError:
                return


def products(value: Any, depth: int = 0) -> list[dict[str, Any]]:
    if depth > 12:
        return []
    if isinstance(value, list):
        return [row for item in value for row in products(item, depth + 1)]
    if isinstance(value, dict):
        types = value.get("@type", [])
        if types == "Product" or (isinstance(types, list) and "Product" in types):
            return [value]
        return products(value.get("@graph", []), depth + 1)
    return []


def parse_page(raw: str, source: Source, now: datetime) -> Observation:
    parser = Documents()
    parser.feed(raw)
    rows = [row for document in parser.documents for row in products(document)]
    if len(rows) != 1:
        raise DeskError("Expected one exact Product record; ambiguous/missing product data is not an offer")
    row = rows[0]
    offers = row.get("offers")
    if isinstance(offers, list):
        if len(offers) != 1:
            raise DeskError("Multiple variant offers need an explicit variant adapter")
        offers = offers[0]
    if not isinstance(offers, dict) or offers.get("@type") != "Offer" or offers.get("price") is None:
        raise DeskError("Exact fixed-price Offer missing; aggregate price ranges are unsupported")
    status = str(offers.get("availability", "")).rsplit("/", 1)[-1]
    availability = (
        "DECLARED_AVAILABLE"
        if status == "InStock"
        else "UNAVAILABLE"
        if status in {"OutOfStock", "Discontinued", "SoldOut"}
        else "UNKNOWN"
    )
    product = Product(
        id=source.product_id or "p-" + sha256_hex(source.url)[:20],
        title=str(row.get("name") or "Untitled product"),
        image_url=retailer_image(row.get("image")),
        mpn=str(row["mpn"]) if row.get("mpn") else None,
        gtin=str(row.get("gtin13") or row.get("gtin12") or row.get("gtin"))
        if row.get("gtin13") or row.get("gtin12") or row.get("gtin")
        else None,
    )
    return Observation(
        product=product,
        source_id=source.id,
        url=source.url,
        price=Decimal(str(offers["price"])),
        currency=str(offers.get("priceCurrency", "")),
        at=now,
        availability=availability,
        method="retailer-jsonld-v1",
        evidence="Retailer Product/Offer markup; delivery, quantity, pack and seller eligibility unverified",
    )


def retailer_image(value: Any) -> str | None:
    from desk.arbitrage.discovery import thumbnail

    if isinstance(value, list):
        value = value[0] if value else None
    if isinstance(value, dict):
        value = value.get("url")
    return thumbnail(value)


async def read(client: httpx.AsyncClient, url: str) -> str:
    async with client.stream("GET", url, follow_redirects=False, headers={"User-Agent": AGENT}) as response:
        if response.status_code != 200:
            raise DeskError(f"Retailer HTTP {response.status_code}; source unavailable, no bypass attempted")
        body = bytearray()
        async for chunk in response.aiter_bytes():
            body.extend(chunk)
            if len(body) > MAX_BYTES:
                raise DeskError("Source response exceeds the 2 MB safety limit")
        return body.decode("utf-8", "replace")


async def fetch(source: Source, now: datetime, *, client: httpx.AsyncClient) -> Observation:
    validate_source(source)
    robots = RobotFileParser()
    robots.parse((await read(client, "https://www.adafruit.com/robots.txt")).splitlines())
    if not robots.can_fetch(AGENT, source.url):
        raise DeskError("Retailer robots policy disallows this request")
    delay = robots.crawl_delay(AGENT)
    if delay and int(delay) > 300:
        raise DeskError("Retailer crawl delay exceeds this adapter schedule; adapter review required")
    await asyncio.sleep(max(2, int(delay or 0)))
    try:
        return parse_page(await read(client, source.url), source, now)
    except (ValueError, TypeError, KeyError, RecursionError) as exc:
        raise DeskError("Retailer product data could not be validated; no observation stored") from exc
