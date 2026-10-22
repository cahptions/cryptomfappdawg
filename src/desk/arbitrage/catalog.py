"""Bounded public merchant catalog discovery. Access/robots failures remain failures, never empty inventory."""
from __future__ import annotations

import asyncio
import json
import re
from contextlib import suppress
from datetime import datetime
from decimal import Decimal
from typing import Any
from urllib.parse import quote, urlsplit
from urllib.robotparser import RobotFileParser
from xml.etree import ElementTree as ET

import httpx

from desk.arbitrage.contracts import Observation, Product, Source
from desk.arbitrage.sources import AGENT, read
from desk.core.errors import DeskError
from desk.core.hashing import sha256_hex

CATALOGS = {
    "adafruit-catalog": ("adafruit_sitemap", "https://www.adafruit.com/sitemap.xml"),
    "arduino-us-catalog": ("shopify_catalog", "https://store-usa.arduino.cc/products.json"),
    **{f"adafruit-category-{key}": ("adafruit_category", f"https://www.adafruit.com/category/{key}")
       for key in (1025, 35, 105, 851, 63, 8, 33, 42)},
    "arduino-catalog": ("shopify_catalog", "https://store.arduino.cc/products.json"),
}


def merchant_url(url: str) -> bool:
    p = urlsplit(url)
    return (p.scheme == "https" and p.hostname == "www.adafruit.com" and p.port in (None, 443)
            and not p.username and not p.password and not p.query and not p.fragment
            and bool(re.fullmatch(r"/product/[0-9]+", p.path)))


def sitemap(raw: str) -> tuple[list[str], list[str]]:
    if "<!DOCTYPE" in raw.upper() or "<!ENTITY" in raw.upper():
        raise DeskError("External entities are unsupported")
    try:
        root = ET.fromstring(raw)  # noqa: S314 -- declarations rejected; response bounded before parsing
    except ET.ParseError as exc:
        raise DeskError("Merchant sitemap is not valid XML") from exc
    if root.tag.split("}")[-1] not in {"urlset", "sitemapindex"}:
        raise DeskError("Merchant returned a page instead of a sitemap; no empty-success assumed")
    offers, children = [], []
    for node in root.iter():
        if node.tag.split("}")[-1] != "loc" or not node.text:
            continue
        url = node.text.strip()
        p = urlsplit(url)
        if merchant_url(url):
            offers.append(url)
        elif (p.scheme == "https" and p.hostname == "www.adafruit.com" and not p.query
              and not p.username and not p.password and p.port in (None, 443)
              and re.fullmatch(r"/sitemap[A-Za-z0-9_./-]*\.xml", p.path) and ".." not in p.path):
            children.append(url)
    return list(dict.fromkeys(offers))[:50000], list(dict.fromkeys(children))[:100]


async def permitted(client: httpx.AsyncClient, url: str, *, robots: str | None = None) -> str:
    p = urlsplit(url)
    policy = robots if robots is not None else await read(client, f"https://{p.hostname}/robots.txt")
    robot = RobotFileParser()
    robot.parse(policy.splitlines())
    if not robot.can_fetch(AGENT, url):
        raise DeskError("Merchant robots policy disallows this catalog URL; no bypass attempted")
    delay = int(robot.crawl_delay(AGENT) or 0)
    if delay > 300:
        raise DeskError("Merchant crawl delay exceeds this adapter schedule")
    await asyncio.sleep(max(2, delay))
    return await read(client, url)


def shopify_page(raw: str, source: Source, now: datetime, *, currency: str) -> list[Observation]:
    try:
        if not re.fullmatch(r"[A-Z]{3}", currency):
            raise ValueError("Published currency missing")
        data = json.loads(raw)
        products = data["products"]
        if not isinstance(products, list) or len(products) > 100:
            raise ValueError("Unexpected page size")
        result: list[Observation] = []
        for item in products:
            variants = item.get("variants", [])
            if len(variants) > 100:
                raise ValueError("Too many variants")
            for v in variants:
                if len(result) >= 1000:
                    raise ValueError("Catalog page exceeds 1000 offers")
                handle = str(item["handle"])
                if (not handle or len(handle) > 240 or not all(c.isalnum() or c in "_-" for c in handle)
                        or not str(v["id"]).isdigit()):
                    raise ValueError("Invalid canonical product path")
                barcode = str(v.get("barcode") or "").strip()
                # Invalid published barcodes do not discard an otherwise useful priced offer.
                gtin = None
                if barcode:
                    with suppress(ValueError):
                        gtin = Product(id="check", title="check", gtin=barcode).gtin
                title = str(item["title"])[:240]
                variant = str(v.get("title") or "")[:200]
                image = next((im.get("src") for im in item.get("images", []) if
                              isinstance(im, dict) and im.get("id") == v.get("featured_image", {}).get("id")), None) \
                    if isinstance(v.get("featured_image"), dict) else None
                image = image or (item.get("images") or [{}])[0].get("src")
                if isinstance(image, str) and image.startswith("//"):
                    image = "https:" + image
                if not isinstance(image, str) or urlsplit(image).hostname != "cdn.shopify.com":
                    image = None
                prefix = "arduino-us-" if urlsplit(source.url).hostname == "store-usa.arduino.cc" else "arduino-"
                product = Product(id=prefix + str(item["id"]) + "-" + str(v["id"]), title=title,
                                  category=str(item.get("product_type") or "Uncategorized")[:80],
                                  brand=str(item.get("vendor") or "")[:100] or None,
                                  mpn=str(v.get("sku") or "")[:100] or None, gtin=gtin,
                                  variant=variant, condition="Unknown", image_url=image)
                available = v.get("available")
                result.append(Observation(product=product, source_id=source.id,
                    url="https://" + str(urlsplit(source.url).hostname) + "/products/" + quote(handle, safe="-_")
                        + "?variant=" + str(v["id"]),
                    price=Decimal(str(v["price"])), currency=currency, at=now,
                    availability="DECLARED_AVAILABLE" if available is True else
                    "UNAVAILABLE" if available is False else "UNKNOWN", method="public-shopify-catalog-v1",
                    evidence="Published merchant variant price/currency; shipping, tax, condition, pack and "
                    "seller eligibility unverified."))
        return result
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        raise DeskError("Merchant catalog page is malformed; no partial page was saved") from exc


async def fetch_catalog(source: Source, now: datetime, client: httpx.AsyncClient,
                        previous: dict[str, Any]) -> tuple[list[Observation], list[str], dict[str, Any]]:
    from desk.arbitrage.sources import validate_source

    validate_source(source)
    if source.adapter == "adafruit_category":
        from desk.arbitrage.bulk import category_page

        rows = category_page(await permitted(client, source.url), source, now)
        return rows, [], {"complete": True, "items": len(rows)}
    if source.adapter == "adafruit_sitemap":
        pending = previous.get("pending_sitemaps") or [source.url]
        visited = [] if previous.get("complete") else previous.get("visited_sitemaps", [])
        url = pending[0]
        offers, children = sitemap(await permitted(client, url))
        visited = list(dict.fromkeys([*visited, url]))[-100:]
        pending = list(dict.fromkeys([*pending[1:], *(u for u in children if u not in visited)]))[:100]
        return [], offers, {"pending_sitemaps": pending, "visited_sitemaps": visited,
                             "complete": not pending, "items": len(offers)}
    page = int(previous.get("next_page", 1))
    if not 1 <= page <= 1000:
        raise DeskError("Catalog checkpoint outside supported page range")
    # Keep an old 50-row checkpoint aligned; new catalogs start with bounded 100-product pages.
    size = int(previous.get("page_size", 50 if page > 1 else 100))
    if size not in (50, 100):
        raise DeskError("Unsupported saved catalog page size")
    raw = await permitted(client, source.url + f"?limit={size}&page={page}")
    digest = sha256_hex(raw)
    if page > 1 and digest == previous.get("page_digest"):
        raise DeskError("Merchant repeated a page; pagination stopped instead of counting duplicates")
    home = await permitted(client, "https://" + str(urlsplit(source.url).hostname) + "/")
    match = re.search(r"Shopify\.currency\s*=\s*(\{[^{}]{1,500}\})", home)
    try:
        currency = json.loads(match.group(1))["active"] if match else None
    except (ValueError, KeyError):
        currency = None
    if not isinstance(currency, str):
        raise DeskError("Merchant currency unavailable; numeric catalog prices cannot be assigned a guessed currency")
    rows = shopify_page(raw, source, now, currency=currency)
    return rows, [], {"next_page": page + 1 if rows and page < 1000 else 1,
                       "complete": not rows or page == 1000, "page_digest": digest, "page": page, "page_size": size}
