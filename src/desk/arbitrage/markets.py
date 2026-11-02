"""Where sold prices and reference prices come from, all free.

- eBay sold results: read the way a person sees them, from this computer, slowly (one request every 20 seconds at
  most), with an honest identity. The operator chose this knowing eBay's rules and robots.txt disallow it. A refusal or
  robot check is recorded and reading pauses for six hours; nothing tries to get around it. Pasting the text of a
  sold-results page is always available and gives the same result.
- Pokémon and Magic cards: TCGplayer's market price (from its recent sales) through the free Pokémon TCG API (with
  TCGdex when it fails, as it often does) and Scryfall.
- Gold and silver spot: gold-api.com (free, no key).
- Barcodes: UPCitemdb's free trial lookup (100 a day) for the product's name.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from datetime import date, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

import httpx

from desk.arbitrage.comps import Sale, blocked, parse_html, parse_listings
from desk.core.errors import DeskError

AGENT = "KESTRELArbitrage/2.0 (personal resale research; one person's local app)"
EBAY_GAP_SECONDS = 20
EBAY_PAUSE = timedelta(hours=6)
MAX_PAGE = 3_000_000
IMAGE_HOSTS = ("i.ebayimg.com", "images.pokemontcg.io", "cards.scryfall.io", "assets.tcgdex.net")


def listing_url(query: str, *, new_only: bool = False) -> str:
    """eBay's Buy It Now listings for a search, cheapest (with delivery) first, from US sellers."""
    params = {"_nkw": query, "LH_BIN": "1", "_sop": "15", "LH_PrefLoc": "1", "_ipg": "60", "rt": "nc"}
    if new_only:
        params["LH_ItemCondition"] = "1000|1500"
    return "https://www.ebay.com/sch/i.html?" + urlencode(params)


def sold_url(query: str, *, new_only: bool = False) -> str:
    """eBay's sold-results page for a search: newest first, 240 a page, US sellers' listings in dollars."""
    params = {"_nkw": query, "LH_Sold": "1", "LH_Complete": "1", "_ipg": "240", "_sop": "13", "rt": "nc"}
    if new_only:
        params["LH_ItemCondition"] = "1000|1500"
    return "https://www.ebay.com/sch/i.html?" + urlencode(params)


def paused(state: dict[str, Any], now: datetime) -> str | None:
    """Why eBay reading is paused after a refusal, or None."""
    until = state.get("paused_until")
    if until and datetime.fromisoformat(until) > now:
        return f"{state.get('reason') or 'eBay refused'}; reading resumes {until[:16].replace('T', ' ')} UTC"
    return None


class EbaySold:
    """One request at a time, at most one every ``gap`` seconds, paused after a refusal (state survives restarts)."""

    def __init__(self, client: httpx.AsyncClient, state: dict[str, Any], *, gap: float = EBAY_GAP_SECONDS) -> None:
        self.client = client
        self.state = state                # {"last_request": epoch, "paused_until": iso, "reason": str}
        self.gap = gap
        self.lock = asyncio.Lock()

    def paused(self, now: datetime) -> str | None:
        return paused(self.state, now)

    async def page(self, url: str, now: datetime) -> tuple[str, str | None, str]:
        """(status ok|paused|blocked|error, note, page) for one eBay page, at the shared pace."""
        if (why := self.paused(now)) is not None:
            return "paused", why, ""
        async with self.lock:
            wait = self.gap - (time.time() - float(self.state.get("last_request") or 0))
            if wait > 0:
                await asyncio.sleep(wait)
            self.state["last_request"] = time.time()
            try:
                response = await self.client.get(url, headers={"User-Agent": AGENT, "Accept-Language": "en-US"},
                                                  follow_redirects=True)
            except httpx.HTTPError as exc:
                return "error", f"eBay didn't answer ({type(exc).__name__})", ""
        text = response.text[:MAX_PAGE]
        refused = blocked(response.status_code, text)
        if refused:
            self.state.update(paused_until=(now + EBAY_PAUSE).isoformat(), reason=refused)
            return "blocked", refused, ""
        if response.status_code != 200:
            return "error", f"eBay answered HTTP {response.status_code}", ""
        self.state["last_ok"] = now.isoformat()
        return "ok", None, text

    async def fetch(self, query: str, *, new_only: bool, now: datetime) -> dict[str, Any]:
        """Sold results: {"status": ok|empty|paused|blocked|unreadable|error, "sales": [...], "url", "note"}."""
        url = sold_url(query, new_only=new_only)
        status, note, text = await self.page(url, now)
        if status != "ok":
            return {"status": status, "sales": [], "url": url, "note": note}
        sales = parse_html(text)
        if sales:
            return {"status": "ok", "sales": sales, "url": url, "note": None}
        if "sold" in text.lower() and "0 results" not in text.lower() and "no exact matches" not in text.lower():
            return {"status": "unreadable", "sales": [], "url": url,
                    "note": "eBay's page didn't read as sold results; run `kestrel ebay-check` and send the result"}
        return {"status": "empty", "sales": [], "url": url, "note": "No sales of this in the last 90 days"}

    async def listings(self, query: str, *, new_only: bool, now: datetime) -> dict[str, Any]:
        """The cheapest Buy It Now listings now (price plus delivery, US sellers)."""
        url = listing_url(query, new_only=new_only)
        status, note, text = await self.page(url, now)
        return {"status": status, "note": note, "url": url, "listings": parse_listings(text) if status == "ok" else []}


async def _json(client: httpx.AsyncClient, url: str, **params: Any) -> Any:
    """A free API's JSON; a server error is retried twice (the Pokémon TCG API often fails a request or two)."""
    for attempt in range(3):
        try:
            response = await client.get(url, params=params or None, headers={"User-Agent": AGENT})
        except httpx.HTTPError as exc:
            raise DeskError(f"{url.split('/')[2]} didn't answer ({type(exc).__name__})") from exc
        if response.status_code < 500 or attempt == 2:
            break
        await asyncio.sleep(0.5 * (attempt + 1))
    if response.status_code == 404:
        return None
    if response.status_code != 200:
        raise DeskError(f"{url.split('/')[2]} answered HTTP {response.status_code}")
    try:
        return response.json()
    except ValueError as exc:
        raise DeskError(f"{url.split('/')[2]} sent an unreadable answer") from exc


def _pokemon_card(c: dict[str, Any]) -> dict[str, Any] | None:
    tcg = c.get("tcgplayer") or {}
    prices = tcg.get("prices") or {}
    # the printing with the most valuable market price is the one most people mean
    priced = [(k, float(v["market"])) for k, v in prices.items() if isinstance(v, dict) and v.get("market")]
    best = max(priced, key=lambda kv: kv[1], default=None)
    if best is None:
        return None
    updated = tcg.get("updatedAt")
    return {"id": "pokemon:" + c["id"], "kind": "pokemon", "title": f"{c['name']} · {c['set']['name']} #{c['number']}",
            "printing": best[0], "market": best[1], "image": (c.get("images") or {}).get("small"),
            "as_of": updated.replace("/", "-") if updated else None, "url": tcg.get("url")}


def _tcgdex_card(c: dict[str, Any]) -> dict[str, Any] | None:
    tcg = (c.get("pricing") or {}).get("tcgplayer") or {}
    priced = [(k, float(v["marketPrice"])) for k, v in tcg.items() if isinstance(v, dict) and v.get("marketPrice")]
    best = max(priced, key=lambda kv: kv[1], default=None)
    if best is None:
        return None
    image = c.get("image")
    return {"id": "pokemon:tcgdex:" + c["id"], "kind": "pokemon",
            "title": f"{c['name']} · {(c.get('set') or {}).get('name', '')} #{c.get('localId', '')}",
            "printing": best[0], "market": best[1], "image": f"{image}/low.png" if image else None,
            "as_of": str(tcg.get("updated") or "")[:10] or None, "url": None}


async def _tcgdex(client: httpx.AsyncClient, name: str, number: str | None) -> list[dict[str, Any]]:
    found = await _json(client, "https://api.tcgdex.net/v2/en/cards", name=name, **{"pagination:itemsPerPage": 40})
    ids = [c["id"] for c in found or [] if number is None or str(c.get("localId", "")).lstrip("0") == number.lstrip("0")]
    details = await asyncio.gather(*(_json(client, f"https://api.tcgdex.net/v2/en/cards/{i}") for i in ids[:16]),
                                   return_exceptions=True)
    return [c for c in (_tcgdex_card(d) for d in details if isinstance(d, dict)) if c]


async def pokemon(client: httpx.AsyncClient, query: str) -> list[dict[str, Any]]:
    """Pokémon cards matching ``query`` (a name, optionally a number like ``charizard 4``), with market prices,
    most valuable first."""
    words = query.split()
    number = words[-1].split("/")[0] if len(words) > 1 and words[-1].split("/")[0].isdigit() else None
    name = " ".join(words[:-1] if number else words).replace('"', "")
    q = f'name:"{name}*"' + (f" number:{number}" if number else "")
    try:
        value = await _json(client, "https://api.pokemontcg.io/v2/cards", q=q, pageSize=24)
        cards = [c for c in (_pokemon_card(x) for x in (value or {}).get("data", [])) if c]
    except DeskError:
        cards = await _tcgdex(client, name, number)
    return sorted(cards, key=lambda c: -c["market"])


async def magic(client: httpx.AsyncClient, query: str) -> list[dict[str, Any]]:
    value = await _json(client, "https://api.scryfall.com/cards/search", q=query, order="usd", dir="desc",
                        unique="prints")
    out = []
    for c in (value or {}).get("data", [])[:24]:
        prices = c.get("prices") or {}
        market = prices.get("usd") or prices.get("usd_foil")
        if not market:
            continue
        out.append({"id": "mtg:" + c["id"], "kind": "mtg", "title": f"{c['name']} · {c['set_name']} #{c['collector_number']}",
                    "printing": "nonfoil" if prices.get("usd") else "foil", "market": float(market),
                    "image": (c.get("image_uris") or {}).get("small"), "as_of": date.today().isoformat(),
                    "url": (c.get("purchase_uris") or {}).get("tcgplayer")})
    return out


async def spot(client: httpx.AsyncClient) -> dict[str, Any]:
    """Gold and silver spot in dollars per troy ounce."""
    out: dict[str, Any] = {}
    for metal, symbol in (("gold", "XAU"), ("silver", "XAG")):
        value = await _json(client, f"https://api.gold-api.com/price/{symbol}")
        if not value or not value.get("price"):
            raise DeskError(f"gold-api.com sent no {metal} price")
        out[metal] = float(value["price"])
        out["as_of"] = value.get("updatedAt")
    return out


async def barcode(client: httpx.AsyncClient, upc: str) -> dict[str, Any] | None:
    """A barcode's product name, brand and lowest recorded retail price (None when unknown)."""
    value = await _json(client, "https://api.upcitemdb.com/prod/trial/lookup", upc=upc)
    items = (value or {}).get("items") or []
    if not items:
        return None
    item = items[0]
    return {"title": item.get("title") or upc, "brand": item.get("brand"),
            "image": next(iter(item.get("images") or []), None),
            "retail_low": item.get("lowest_recorded_price"), "retail_high": item.get("highest_recorded_price")}


FEEDS = {
    "slickdeals": "https://slickdeals.net/newsearch.php?mode=frontpage&searcharea=deals&searchin=first&rss=1",
    "slickdeals-lego": "https://slickdeals.net/newsearch.php?q=lego&searcharea=deals&searchin=first&rss=1",
    "slickdeals-sneakers": "https://slickdeals.net/newsearch.php?q=nike+jordan&searcharea=deals&searchin=first&rss=1",
    "slickdeals-pokemon": "https://slickdeals.net/newsearch.php?q=pokemon&searcharea=deals&searchin=first&rss=1",
    "dealnews": "https://www.dealnews.com/?rss=1",
}


async def feed(client: httpx.AsyncClient, url: str) -> list[dict[str, Any]]:
    """A deal feed's posts: title, link, the price in the title (None for "from $x" or no price)."""
    from xml.etree import ElementTree as ET

    try:
        response = await client.get(url, headers={"User-Agent": AGENT})
    except httpx.HTTPError as exc:
        raise DeskError(f"{url.split('/')[2]} didn't answer ({type(exc).__name__})") from exc
    if response.status_code != 200:
        raise DeskError(f"{url.split('/')[2]} answered HTTP {response.status_code}")
    text = response.text[:3_000_000]
    if "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
        raise DeskError(f"{url.split('/')[2]} sent a page, not a feed")
    try:
        root = ET.fromstring(text)  # noqa: S314 -- declarations rejected above; size bounded
    except ET.ParseError as exc:
        raise DeskError(f"{url.split('/')[2]} sent an unreadable feed") from exc
    out = []
    for item in root.iter("item"):
        title = " ".join((item.findtext("title") or "").split())
        link = (item.findtext("link") or "").strip()
        m = re.search(r"(?<!from )(?<!From )~?\$([\d,]+(?:\.\d{2})?)", title)
        if title and link.startswith("https://"):
            out.append({"title": title, "url": link, "price": float(m[1].replace(",", "")) if m else None})
    return out


async def pokemon_sets(client: httpx.AsyncClient, count: int) -> list[dict[str, Any]]:
    value = await _json(client, "https://api.pokemontcg.io/v2/sets", orderBy="-releaseDate", pageSize=count)
    return [{"id": s["id"], "name": s["name"]} for s in (value or {}).get("data", [])]


async def pokemon_set_cards(client: httpx.AsyncClient, set_id: str) -> list[dict[str, Any]]:
    """A set's cards with TCGplayer's US market price and Cardmarket's 1-, 7- and 30-day average sale prices."""
    value = await _json(client, "https://api.pokemontcg.io/v2/cards", q=f"set.id:{set_id}", pageSize=250)
    out = []
    for c in (value or {}).get("data", []):
        card = _pokemon_card(c)
        cm = ((c.get("cardmarket") or {}).get("prices") or {})
        if card is not None:
            out.append({**card, "avg1": cm.get("avg1"), "avg7": cm.get("avg7"), "avg30": cm.get("avg30")})
    return out


def comps_json(sales: list[Sale]) -> str:
    return json.dumps([s.as_json() for s in sales])


__all__ = ["AGENT", "FEEDS", "IMAGE_HOSTS", "EbaySold", "barcode", "comps_json", "feed", "listing_url", "magic", "paused",
           "pokemon", "pokemon_set_cards", "pokemon_sets", "sold_url", "spot"]
