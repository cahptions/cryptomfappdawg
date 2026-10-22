"""Bounded RSS/Atom deal leads, never inferred stock, transaction prices or sales volume.

The two fixed public-feed candidates still need real-network acceptance. Robots/access errors are visible;
the adapter neither follows affiliate redirects nor requests a merchant page discovered inside a feed.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser
from xml.etree import ElementTree as ET

import httpx

from desk.arbitrage.contracts import DealLead, Source, public_url
from desk.core.errors import DeskError
from desk.core.hashing import sha256_hex

FEEDS: dict[str, dict[str, str]] = {
    "slickdeals": {
        "name": "Slickdeals front page",
        "url": "https://slickdeals.net/newsearch.php?mode=frontpage&searcharea=deals&searchin=first&rss=1",
        "home": "https://slickdeals.net/",
        "basis": "Community-selected deal mentions; votes are not sales. Live feed acceptance pending.",
    },
    "dealnews": {
        "name": "DealNews",
        "url": "https://www.dealnews.com/?rss=1",
        "home": "https://www.dealnews.com/",
        "basis": "Editorial deal mentions; a published deal can have expired. Live feed acceptance pending.",
    },
}
IMAGE_HOSTS = frozenset(
    {"i.ebayimg.com", "i.ebaystatic.com", "cdn-shop.adafruit.com", "static.slickdealscdn.com", "st.slickdeals.net", "c.dlnws.com"}
)
AGENT = "KESTRELResearch/0.2"


class FeedDelay(DeskError):
    def __init__(self, until: datetime) -> None:
        self.until = until
        super().__init__("Source rate-limited feed access; waiting until its Retry-After time")


def thumbnail(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        public_url(value)
        return value if urlsplit(value).hostname in IMAGE_HOSTS else None
    except ValueError:
        return None


def descriptor(source: Source) -> dict[str, str]:
    for feed in FEEDS.values():
        if source.adapter == "deal_feed" and source.url == feed["url"] and not source.query and not source.product_id:
            return feed
    raise DeskError("Choose one of the supported public deal feeds; arbitrary URLs and private servers are not fetched")


class PlainText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def text_content(value: str, limit: int) -> str:
    parser = PlainText()
    parser.feed(value)
    return re.sub(r"\s+", " ", " ".join(parser.parts)).strip()[:limit]


def published(value: str) -> datetime | None:
    if not value:
        return None
    try:
        date = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        try:
            date = parsedate_to_datetime(value)
        except (ValueError, TypeError, OverflowError):
            return None
    return date.astimezone(UTC) if date.tzinfo else None


def parse_feed(raw: str, source: Source, now: datetime) -> list[DealLead]:
    home = urlsplit(descriptor(source)["home"]).hostname or ""
    domain = home.removeprefix("www.")
    if len(raw.encode("utf-8")) > 2_000_000 or "<!DOCTYPE" in raw.upper() or "<!ENTITY" in raw.upper():
        raise DeskError("Deal feed is oversized or contains unsupported XML declarations")
    try:
        root = ET.fromstring(raw)  # noqa: S314 -- declarations rejected above, body bounded before parsing
    except ET.ParseError as exc:
        raise DeskError("Source did not return a readable RSS/Atom feed; no leads were created") from exc
    if root.tag.split("}")[-1] not in {"rss", "feed", "RDF"}:
        raise DeskError("Source returned a page rather than a supported deal feed")
    rows = [row for row in root.iter() if row.tag.split("}")[-1] in {"item", "entry"}]
    leads: dict[str, DealLead] = {}
    for row in rows[:100]:
        fields = {child.tag.split("}")[-1]: child for child in row}

        def value(name: str, fields: dict[str, ET.Element] = fields) -> str:
            element = fields.get(name)
            return element.text or "" if element is not None else ""

        link = fields.get("link")
        url = (link.get("href") or link.text or "") if link is not None else ""
        # Keep only source-owned deal pages. Merchant and affiliate links are opened by the operator, never followed here.
        try:
            public_url(url)
            if urlsplit(url).hostname not in {domain, "www." + domain}:
                continue
        except ValueError:
            continue
        title = text_content(value("title"), 240)
        at = published(value("pubDate") or value("published") or value("updated") or value("date"))
        if not title or (at and (at > now + timedelta(minutes=5) or at < now - timedelta(days=7))):
            continue
        image = next(
            (
                thumbnail(child.get("url"))
                for child in row.iter()
                if child.tag.split("}")[-1] in {"thumbnail", "enclosure", "content"} and thumbnail(child.get("url"))
            ),
            None,
        )
        key = "lead-" + sha256_hex(source.id + "\n" + url)[:32]
        leads[key] = DealLead(
            id=key,
            source_id=source.id,
            title=title,
            url=url,
            summary=text_content(value("description") or value("summary") or value("encoded"), 1000),
            category=text_content(value("category"), 80) or "Uncategorized",
            image_url=image,
            published_at=at,
            first_seen_at=now,
            retrieved_at=now,
        )
    return list(leads.values())


async def bounded_get(client: httpx.AsyncClient, url: str, headers: dict[str, str]) -> tuple[int, str, dict[str, str]]:
    async with client.stream("GET", url, follow_redirects=False, headers={"User-Agent": AGENT, **headers}) as response:
        if response.status_code == 429:
            now = datetime.now(UTC)
            retry = response.headers.get("Retry-After", "")
            try:
                until = now + timedelta(seconds=min(1_000_000_000, max(900, int(retry))))
            except ValueError:
                until = max(now + timedelta(minutes=15), published(retry) or now)
            raise FeedDelay(until)
        if response.status_code == 304:
            return 304, "", {}
        if response.status_code != 200:
            raise DeskError(f"Deal-feed HTTP {response.status_code}; access/redirect denied, no bypass or empty success")
        body = bytearray()
        async for chunk in response.aiter_bytes():
            body.extend(chunk)
            if len(body) > 2_000_000:
                raise DeskError("Deal feed exceeds the 2 MB response limit")
        cache = {k: response.headers[k][:200] for k in ("etag", "last-modified") if k in response.headers}
        return 200, body.decode("utf-8", "replace"), cache


async def fetch_feed(
    source: Source, now: datetime, *, client: httpx.AsyncClient, previous: dict[str, Any]
) -> tuple[list[DealLead], dict[str, str], bool]:
    feed = descriptor(source)
    policy_code, policy, _ = await bounded_get(client, feed["home"] + "robots.txt", {})
    if policy_code != 200:
        raise DeskError("Source robots policy could not be read; feed fetch deferred")
    robots = RobotFileParser()
    robots.parse(policy.splitlines())
    if not robots.can_fetch(AGENT, source.url):
        raise DeskError("Source robots policy disallows this feed request")
    delay = int(robots.crawl_delay(AGENT) or 0)
    if delay and delay > 900:
        raise DeskError("Source crawl delay exceeds this feed schedule; adapter review required")
    import asyncio

    await asyncio.sleep(max(2, delay or 0))
    headers = {}
    for field, header in (("etag", "If-None-Match"), ("last-modified", "If-Modified-Since")):
        if previous.get(field):
            headers[header] = str(previous[field])[:200]
    code, raw, cache = await bounded_get(client, source.url, headers)
    return ([] if code == 304 else parse_feed(raw, source, now)), cache, code == 304
