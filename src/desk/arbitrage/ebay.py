"""Optional production Browse search, read-only. Needs independent account/production-access verification."""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from email.utils import parsedate_to_datetime
from math import isfinite
from typing import Any
from urllib.parse import urlsplit

import httpx

from desk.arbitrage.contracts import Observation, Product, Source
from desk.arbitrage.integrations import Credentials
from desk.core.errors import DeskError
from desk.core.hashing import sha256_hex


class Backoff(DeskError):
    def __init__(self, until: datetime) -> None:
        self.until = until
        super().__init__("eBay rate-limited requests; collection will wait until the reported retry time")


async def response_json(response: httpx.Response) -> dict[str, Any]:
    if response.status_code == 429:
        retry = response.headers.get("Retry-After", "")
        now = datetime.now(UTC)
        try:
            until = now + timedelta(seconds=min(1_000_000_000, max(900, int(retry))))
        except ValueError:
            try:
                until = max(now + timedelta(minutes=15), parsedate_to_datetime(retry).astimezone(UTC))
            except (ValueError, TypeError, OverflowError):
                until = now + timedelta(minutes=15)
        raise Backoff(until)
    if response.status_code != 200:
        raise DeskError(f"eBay HTTP {response.status_code}; check production access, credentials or API quota in Settings")
    raw = bytearray()
    async for chunk in response.aiter_bytes():
        raw.extend(chunk)
        if len(raw) > 2_000_000:
            raise DeskError("eBay response exceeds the 2 MB limit")
    import json

    try:
        data = json.loads(raw)
    except (ValueError, RecursionError) as exc:
        raise DeskError("eBay returned an unreadable response; no provider body was logged") from exc
    if not isinstance(data, dict):
        raise DeskError("eBay returned an unexpected response structure")
    return data


def listing_image(row: dict[str, Any]) -> str | None:
    """Only provider-owned HTTPS image hosts; a bad thumbnail must not discard an offer."""
    image = row.get("image")
    url = image.get("imageUrl") if isinstance(image, dict) else None
    if not isinstance(url, str):
        return None
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme == "https"
        and parsed.hostname in {"i.ebayimg.com", "i.ebaystatic.com"}
        and not parsed.username
        and not parsed.password
        and port in (None, 443)
        and not parsed.fragment
        and len(url) <= 1000
    ):
        return url
    return None


def parse(data: dict[str, Any], source: Source, now: datetime) -> list[Observation]:
    if data.get("errors") or ("itemSummaries" not in data and data.get("total") != 0):
        raise DeskError("eBay search returned errors or an incomplete result; no successful empty result assumed")
    rows = data.get("itemSummaries", [])
    if not isinstance(rows, list) or len(rows) > 20:
        raise DeskError("Unexpected eBay search batch; no observations stored")
    output: list[Observation] = []
    try:
        for row in rows:
            if not isinstance(row, dict):
                raise DeskError("eBay listing row is malformed; no search batch was stored")
            if "FIXED_PRICE" not in row.get("buyingOptions", []):
                continue  # auction bids are not executable fixed-price acquisition offers
            price = row.get("price")
            url = row.get("itemWebUrl")
            if not isinstance(price, dict) or not isinstance(url, str):
                continue
            if urlsplit(url).hostname not in {"www.ebay.com", "ebay.com"}:
                continue
            # A listing is a separate identity until someone proves an exact product match.
            product = Product(
                id="ebay-" + sha256_hex(str(row["itemId"]))[:24],
                title=str(row["title"]),
                category="Uncategorized",
                condition=str(row.get("condition") or "Unknown"),
                image_url=listing_image(row),
            )
            output.append(
                Observation(
                    product=product,
                    source_id=source.id,
                    url=url,
                    price=Decimal(str(price["value"])),
                    currency=price["currency"],
                    at=now,
                    availability="DECLARED_AVAILABLE",
                    method="ebay-browse-active-listing-v1",
                    evidence="Active fixed-price listing, not a completed sale. Exact variant, delivery, stock quantity, "
                    "tax and resale eligibility unverified. US marketplace query.",
                )
            )
    except (ValueError, TypeError, KeyError) as exc:
        raise DeskError("eBay listing could not be validated; search batch was not stored") from exc
    return output


class Browse:
    def __init__(self, credentials: Credentials, spend: Callable[[], None]) -> None:
        self.credentials = credentials
        self._spend = spend
        self._token = ""
        self._expires = 0.0

    async def search(self, client: httpx.AsyncClient, source: Source, now: datetime) -> list[Observation]:
        credentials = self.credentials
        if not credentials.enabled or not credentials.free_access_confirmed:
            raise DeskError("eBay live listings are disabled; open Business settings for the free-access setup guide")
        if time.monotonic() >= self._expires:
            self._spend()
            async with client.stream(
                "POST",
                "https://api.ebay.com/identity/v1/oauth2/token",
                auth=httpx.BasicAuth(credentials.client_id, credentials.client_secret),
                follow_redirects=False,
                data={"grant_type": "client_credentials", "scope": "https://api.ebay.com/oauth/api_scope"},
            ) as response:
                token = await response_json(response)
            if not isinstance(token.get("access_token"), str) or not isinstance(token.get("expires_in"), (int, float)):
                raise DeskError("eBay application token response is incomplete; no credentials were logged")
            if not isfinite(float(token["expires_in"])):
                raise DeskError("eBay token lifetime is not finite")
            self._token = token["access_token"]
            self._expires = time.monotonic() + min(max(float(token["expires_in"]) - 60, 0), 7200)
        self._spend()
        async with client.stream(
            "GET",
            "https://api.ebay.com/buy/browse/v1/item_summary/search",
            params={"q": source.query, "limit": 20},
            follow_redirects=False,
            headers={"Authorization": "Bearer " + self._token, "X-EBAY-C-MARKETPLACE-ID": "EBAY_US"},
        ) as response:
            body = await response_json(response)
        return parse(body, source, now)
