"""Sold prices ("comps") read from eBay's sold results, as the page reads to a person.

A downloaded page is read one result container at a time (eBay's ``s-item`` or ``s-card`` list items), so a sale's
date, title, price and delivery always belong together. Text copied from a sold-results page (select all, copy,
paste) is read line by line: each "Sold <date>" line starts a result. Results after eBay's "Results matching fewer
words" divider are other products and are dropped.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import date
from html.parser import HTMLParser
from typing import Any

SOLD = re.compile(r"^Sold\s+([A-Z][a-z]{2})\s+(\d{1,2}),\s+(\d{4})$")
PRICE = re.compile(r"^\$([\d,]+(?:\.\d{2})?)$")
RANGE = re.compile(r"^\$[\d,.]+\s+to\s+\$[\d,.]+$")
SHIPPING = re.compile(r"^\+\s?\$([\d,]+(?:\.\d{2})?)\s+(?:shipping|delivery|postage)\b", re.I)
FREE = re.compile(r"^free\s+(?:shipping|delivery|postage)\b", re.I)
FEWER = re.compile(r"results matching fewer words", re.I)
CONDITIONS = ("Brand New", "New (Other)", "New with tags", "New with box", "New without tags", "New without box",
              "Open Box", "Pre-Owned", "Used", "Refurbished", "Certified - Refurbished", "Seller refurbished",
              "For parts or not working", "Graded", "Ungraded")
LABELS = re.compile(r"^(?:or Best Offer|Best offer accepted|Buy It Now|Free returns|Located in .*|from .*|"
                    r"Sponsored|Shop on eBay|New Listing|Top Rated Plus|Authenticity Guarantee|.*\bwatchers?\b|"
                    r".*\bsold\b.*|Last one|Almost gone|View similar.*|Sell one like this|\d+(?:\.\d)?% positive.*|"
                    r"\(\d[\d,]*\)|Opens in a new window or tab|Shipping not specified|Free local pickup|"
                    r"Was: .*|\d+% off.*|Extra \d+% off.*|or \$.*)$", re.I)
_MONTHS = {m: i for i, m in enumerate(("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov",
                                       "Dec"), 1)}
_BLOCK_TAGS = {"div", "li", "p", "h1", "h2", "h3", "h4", "h5", "h6", "br", "ul", "ol", "section", "article", "tr",
               "td", "header", "footer", "span"}


@dataclass(frozen=True)
class Sale:
    price: float
    shipping: float | None          # what the buyer paid for delivery; None when the page didn't say
    sold_on: date | None
    title: str
    condition: str | None = None

    @property
    def total(self) -> float:
        """What the buyer paid (the price eBay's fees and the seller's proceeds are worked from)."""
        return self.price + (self.shipping or 0.0)

    def as_json(self) -> dict[str, Any]:
        out = asdict(self)
        out["sold_on"] = self.sold_on.isoformat() if self.sold_on else None
        return out

    @classmethod
    def from_json(cls, value: dict[str, Any]) -> Sale:
        return cls(float(value["price"]), None if value.get("shipping") is None else float(value["shipping"]),
                   date.fromisoformat(value["sold_on"]) if value.get("sold_on") else None, str(value["title"]),
                   value.get("condition"))


ITEM = "\x00item"                     # marks the start of one result container in the page's lines
LINK = "\x00link "                    # a listing's own address inside a container
IMAGE = "\x00img "                    # a listing's photo inside a container


class _Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.lines: list[str] = []
        self.buffer: list[str] = []
        self.skip = 0

    def _flush(self) -> None:
        text = " ".join("".join(self.buffer).split())
        if text:
            self.lines.append(text)
        self.buffer = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style", "noscript", "template", "svg"):
            self.skip += 1
        if tag in _BLOCK_TAGS:
            self._flush()
        a = dict(attrs)
        if tag == "li" and re.search(r"\bs-(?:item|card)\b", a.get("class") or ""):
            self.lines.append(ITEM)
        if tag == "a" and (m := re.match(r"https://www\.ebay\.com/itm/(\d+)", a.get("href") or "")):
            self.lines.append(LINK + "https://www.ebay.com/itm/" + m[1])
        if tag == "img":
            src = a.get("src") or a.get("data-src") or ""
            if src.startswith("https://i.ebayimg.com/"):
                self.lines.append(IMAGE + src)

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style", "noscript", "template", "svg") and self.skip:
            self.skip -= 1
        if tag in _BLOCK_TAGS:
            self._flush()

    def handle_data(self, data: str) -> None:
        if not self.skip:
            self.buffer.append(data)


def html_lines(html: str) -> list[str]:
    reader = _Text()
    reader.feed(html)
    reader.close()
    reader._flush()
    return reader.lines


def _sold_on(line: str) -> date | None:
    m = SOLD.match(line)
    if m is None or m[1] not in _MONTHS:
        return None
    try:
        return date(int(m[3]), _MONTHS[m[1]], int(m[2]))
    except ValueError:
        return None


def _clean_title(line: str) -> str:
    line = re.sub(r"\s*Opens in a new window or tab$", "", line)
    return re.sub(r"^New Listing\s*", "", line).strip()


def _record(lines: list[str], sold_on: date) -> Sale | None:
    price = shipping = None
    title = condition = None
    for line in lines:
        if RANGE.match(line):
            return None                                  # a multi-variation listing: no single sold price
        if price is None and (m := PRICE.match(line)):
            price = float(m[1].replace(",", ""))
            continue
        if shipping is None and (m := SHIPPING.match(line)):
            shipping = float(m[1].replace(",", ""))
            continue
        if shipping is None and FREE.match(line):
            shipping = 0.0
            continue
        if condition is None and any(line.startswith(c) for c in CONDITIONS):
            condition = line.split(" · ")[0]
            continue
        if title is None and len(line) >= 12 and not LABELS.match(line) and not line.startswith(("$", "+")):
            title = _clean_title(line)
    if price is None or title is None or price <= 0:
        return None
    return Sale(price, shipping, sold_on, title, condition)


def _until_fewer(lines: list[str]) -> list[str]:
    lines = [" ".join(x.split()) for x in lines]
    lines = [x for x in lines if x]
    for i, line in enumerate(lines):
        if FEWER.search(line):
            return lines[:i]
    return lines


def parse_lines(lines: list[str]) -> list[Sale]:
    """Sold results from pasted lines: each "Sold <date>" line starts a result. (A layout that puts the date after
    the title shifts each date by one listing; title, price and delivery stay together.)"""
    lines = _until_fewer(lines)
    marks = [i for i, line in enumerate(lines) if _sold_on(line)]
    if not marks:
        return []
    rows = [_record(lines[i + 1:j], _sold_on(lines[i]) or date.min)
            for i, j in zip(marks, [*marks[1:], len(lines)], strict=True)]
    return [r for r in rows if r is not None]


def parse_text(text: str) -> list[Sale]:
    """Text copied from an eBay sold-results page."""
    return parse_lines(text.splitlines())


def parse_html(html: str) -> list[Sale]:
    """A downloaded sold-results page, one result container at a time (line by line if eBay renames them)."""
    lines = _until_fewer(html_lines(html))
    if ITEM not in lines:
        return parse_lines([x for x in lines if not x.startswith("\x00")])
    out = []
    starts = [i for i, line in enumerate(lines) if line == ITEM]
    for a, b in zip(starts, [*starts[1:], len(lines)], strict=True):
        chunk = lines[a + 1:b]
        when = next((d for d in map(_sold_on, chunk) if d), None)
        if when is not None:
            record = _record([x for x in chunk if not _sold_on(x) and not x.startswith("\x00")], when)
            if record is not None:
                out.append(record)
    return out


@dataclass(frozen=True)
class Listing:
    """An item for sale now (a Buy It Now listing)."""
    price: float
    shipping: float | None
    title: str
    url: str
    image: str | None = None
    condition: str | None = None

    @property
    def total(self) -> float:
        return self.price + (self.shipping or 0.0)


def parse_listings(html: str) -> list[Listing]:
    """Buy It Now listings from a search-results page, one result container at a time (sold results are skipped)."""
    lines = _until_fewer(html_lines(html))
    starts = [i for i, line in enumerate(lines) if line == ITEM]
    out = []
    for a, b in zip(starts, [*starts[1:], len(lines)], strict=True):
        chunk = lines[a + 1:b]
        if any(_sold_on(x) for x in chunk):
            continue
        url = next((x[len(LINK):] for x in chunk if x.startswith(LINK)), None)
        image = next((x[len(IMAGE):] for x in chunk if x.startswith(IMAGE)), None)
        record = _record([x for x in chunk if not x.startswith("\x00")], date.min)
        if record is not None and url:
            out.append(Listing(record.price, record.shipping, record.title, url, image, record.condition))
    return out


def blocked(status: int, html: str) -> str | None:
    """Why a response is a refusal or a robot check rather than results (None when it reads as a results page)."""
    if status in (403, 429, 503):
        return f"eBay refused the request (HTTP {status})"
    head = html[:5000].lower()
    for marker in ("pardon our interruption", "security measure", "captcha", "error page | ebay", "access denied"):
        if marker in head:
            return f"eBay answered with a robot check ({marker})"
    return None


__all__ = ["Listing", "Sale", "blocked", "html_lines", "parse_html", "parse_lines", "parse_listings", "parse_text"]
