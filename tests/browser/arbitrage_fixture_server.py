"""Synthetic Arbitrage hunt fixture, isolated from operator data and every network source.

uv run python -m tests.browser.arbitrage_fixture_server --port 8895

eBay answers with invented sold results (or refuses for a "refused" search), the card search and spot prices are
invented, and nothing leaves this computer. Every value is a fixture, not a market observation.
"""

from __future__ import annotations

import argparse
import tempfile
from datetime import timedelta
from pathlib import Path
from typing import Any

import uvicorn

from desk.arbitrage import hunt, markets
from desk.arbitrage.comps import Listing, Sale
from desk.arbitrage.web import create_app
from desk.core.clock import utc_now
from desk.core.paths import DeskPaths


async def fake_fetch(self: markets.EbaySold, query: str, *, new_only: bool, now: Any) -> dict[str, Any]:
    url = markets.sold_url(query, new_only=new_only)
    if "refused" in query:
        return {"status": "blocked", "sales": [], "url": url, "note": "eBay refused the request (HTTP 403)"}
    today = now.date()
    if query.startswith("LEGO"):          # rising: about 40% a year
        sales = [Sale(round(100 * 1.0009 ** (90 - d), 2), 0.0, today - timedelta(days=d), f"{query} sealed set")
                 for d in range(0, 90, 5)]
    else:
        sales = [Sale(30.0 + i % 4, 8.5, today - timedelta(days=4 * i), "Fixture 20 car gift pack") for i in range(14)]
    return {"status": "ok", "sales": sales, "url": url, "note": None}


async def fake_listings(self: markets.EbaySold, query: str, *, new_only: bool, now: Any) -> dict[str, Any]:
    url = markets.listing_url(query, new_only=new_only)
    if "gold" in query:
        found = [Listing(110.0, 0.0, "10K Yellow Gold Scrap Lot 5.2 Grams", "https://www.ebay.com/itm/9001")]
    else:
        number = 9000 + sum(map(ord, query)) % 900                       # one listing per hunt
        found = [Listing(40.0, 0.0, f"{query} new sealed", f"https://www.ebay.com/itm/{number}")]
    return {"status": "ok", "note": None, "url": url, "listings": found}


async def fake_feed(client: Any, url: str) -> list[dict[str, Any]]:
    if "lego" not in url:
        return []
    return [{"title": "LEGO Botanical Fixture Set (10497) $58.00 + Free Shipping", "price": 58.0,
             "url": "https://slickdeals.net/f/fixture-10497"}]


async def fake_sets(client: Any, count: int) -> list[dict[str, Any]]:
    return [{"id": "fx", "name": "Fixture Set"}]


async def fake_set_cards(client: Any, set_id: str) -> list[dict[str, Any]]:
    return [{"id": "pokemon:fx-9", "kind": "pokemon", "title": "Fixture Riser · Fixture Set #9", "printing": "holofoil",
             "market": 40.0, "image": None, "as_of": utc_now().date().isoformat(), "url": None,
             "avg1": 31.0, "avg7": 30.0, "avg30": 20.0}]


async def fake_pokemon(client: Any, query: str) -> list[dict[str, Any]]:
    return [{"id": "pokemon:fixture-4", "kind": "pokemon", "title": "Fixture Charizard · Base #4", "printing": "holofoil",
             "market": 420.0, "image": None, "as_of": utc_now().date().isoformat(), "url": None}]


async def fake_card(client: Any, card_id: str) -> dict[str, Any]:
    return (await fake_pokemon(client, ""))[0]


async def fake_spot(client: Any) -> dict[str, Any]:
    return {"gold": 4000.0, "silver": 50.0, "as_of": utc_now().isoformat()}


async def fake_barcode(client: Any, upc: str) -> dict[str, Any] | None:
    return {"title": "Fixture 20-car gift pack", "image": None, "retail_low": 21.99}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8895)
    args = parser.parse_args()
    markets.EbaySold.fetch = fake_fetch          # type: ignore[method-assign]
    markets.EbaySold.listings = fake_listings    # type: ignore[method-assign]
    markets.feed = fake_feed                     # type: ignore[assignment]
    markets.pokemon_sets = fake_sets             # type: ignore[assignment]
    markets.pokemon_set_cards = fake_set_cards   # type: ignore[assignment]
    markets.pokemon = fake_pokemon               # type: ignore[assignment]
    markets.spot = fake_spot                     # type: ignore[assignment]
    markets.barcode = fake_barcode               # type: ignore[assignment]
    hunt.card = fake_card                        # type: ignore[assignment]
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        app = create_app(DeskPaths(root, root / "config", root / "secrets.env"), auto_start=False)
        uvicorn.run(app, host="127.0.0.1", port=args.port, access_log=False)


if __name__ == "__main__":
    main()
