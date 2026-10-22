"""Published Product/Offer microdata from fixed Adafruit category pages; no cart requests."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from typing import Any

from desk.arbitrage.contracts import Observation, Product, Source
from desk.arbitrage.sources import retailer_image
from desk.core.errors import DeskError
from desk.core.hashing import sha256_hex


class Category(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.stack: list[str] = []
        self.start: int | None = None
        self.root_tag = ""
        self.row: dict[str, Any] = {}
        self.rows: list[dict[str, Any]] = []
        self.heading = ""
        self.in_heading = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        data = dict(attrs)
        void = tag in {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "wbr"}
        if data.get("itemtype") in {"https://schema.org/Product", "http://schema.org/Product"}:
            if self.start is not None:
                raise DeskError("Nested product identities are unsupported in a category offer")
            self.start = len(self.stack)
            self.root_tag = tag
            self.row = {}
        if tag == "h1":
            self.in_heading = True
        if self.start is not None:
            prop = data.get("itemprop")
            if prop in {"name", "price", "priceCurrency", "availability", "url", "itemCondition", "gtin13", "gtin12"}:
                value = data.get("content") or data.get("href")
                if value:
                    if prop in self.row and self.row[prop] != value:
                        raise DeskError("Ambiguous category product/variant price; no page saved")
                    self.row[prop] = value
            if tag == "img" and "image" not in self.row:
                self.row["image"] = data.get("src")
        if not void:
            self.stack.append(tag)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in {"meta", "img", "input", "br", "hr", "link", "source", "wbr", "area", "base", "embed", "col"}:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        if tag == "h1":
            self.in_heading = False
        if tag in self.stack:
            index = len(self.stack) - 1 - self.stack[::-1].index(tag)
            del self.stack[index:]
        if self.start is not None and len(self.stack) <= self.start:
            if tag != self.root_tag:
                raise DeskError("Truncated category product metadata; no page saved")
            self.rows.append(self.row)
            self.start = None
            if len(self.rows) > 2000:
                raise DeskError("Category exceeds the 2000-product bound")

    def handle_data(self, value: str) -> None:
        if self.in_heading:
            self.heading += value


def category_page(raw: str, source: Source, now: datetime) -> list[Observation]:
    from desk.arbitrage.catalog import merchant_url

    page = Category()
    page.feed(raw)
    if not page.rows or page.start is not None:
        raise DeskError("No complete priced Product microdata in this category; no empty-success assumed")
    results = {}
    try:
        for row in page.rows:
            url = row.get("url", "")
            if not merchant_url(url):
                raise ValueError("Noncanonical merchant product")
            product = Product(id="p-" + sha256_hex(url)[:20], title=row["name"],
                category=" ".join(page.heading.split())[:80] or "Uncategorized",
                image_url=retailer_image(row.get("image")),
                gtin=row.get("gtin13") or row.get("gtin12"),
                condition={"NewCondition": "new", "UsedCondition": "used", "RefurbishedCondition": "refurbished"}.get(
                    row.get("itemCondition", "").rsplit("/", 1)[-1], "Unknown"))
            status = row.get("availability", "").rsplit("/", 1)[-1]
            results[url] = Observation(product=product, source_id=source.id, url=url,
                price=Decimal(row["price"]), currency=row["priceCurrency"], at=now,
                availability="DECLARED_AVAILABLE" if status == "InStock" else "UNAVAILABLE"
                    if status in {"OutOfStock", "Discontinued", "SoldOut"} else "UNKNOWN",
                method="adafruit-category-microdata-v1",
                evidence="Published category Product/Offer price, currency, image and stock declaration. "
                         "Not regional delivery, confirmed quantity, shipping, tax or resale profit.")
    except (KeyError, TypeError, ValueError, InvalidOperation) as exc:
        raise DeskError("Category offer metadata is incomplete; no partial page saved") from exc
    return list(results.values())
