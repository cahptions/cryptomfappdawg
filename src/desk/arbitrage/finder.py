"""The finders: what KESTREL looks for by itself and ranks in Deals and Buy & Hold.

1. **eBay underpriced listings.** For each hunt (LEGO sets, sneakers, sealed Pokémon products, anything you add), the
   cheapest Buy It Now listings from US sellers, compared with the same item's sold prices. Every 12 hours each.
2. **Gold & silver listings.** Listings whose title states the weight and karat, priced under what a scrap buyer pays
   for the melt.
3. **Deal posts.** Slickdeals and DealNews posts that name a LEGO set number or a sneaker style code, compared with that
   item's sold prices. Every 30 minutes.
4. **Rising Pokémon cards.** Cards whose last week of sales averages 15% or more above their 30-day average (Cardmarket's
   sale averages; the US price is TCGplayer's market price). Once a day, into Buy & Hold.

eBay pages are read at the shared, polite pace (one every 20 seconds at most) and pause for six hours on any refusal,
so the finders that need eBay wait while it's paused and say so. Every finder records when it last ran, what it found
and why it found nothing.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any

import httpx

from desk.arbitrage import hunt as hunting
from desk.arbitrage import markets, value
from desk.core.errors import DeskError
from desk.core.hashing import sha256_hex

FINDER_DDL = """
CREATE TABLE IF NOT EXISTS hunt_offers(id TEXT PRIMARY KEY, item_id TEXT NOT NULL, source TEXT NOT NULL,
 url TEXT NOT NULL, title TEXT NOT NULL, image TEXT, price REAL NOT NULL, shipping REAL, first_seen TEXT NOT NULL,
 seen_at TEXT NOT NULL, gone INTEGER NOT NULL DEFAULT 0, hidden INTEGER NOT NULL DEFAULT 0, body TEXT NOT NULL DEFAULT '{}');
CREATE INDEX IF NOT EXISTS hunt_offers_item ON hunt_offers(item_id, gone);
"""

LEGO = ["75192", "10497", "10300", "75313", "10294", "21333", "10276", "75331", "42143", "10305", "75355", "10316",
        "10307", "76269", "75367", "10318", "10333", "76419", "42115", "10281", "10311", "21348", "10320", "10315",
        "21345", "10328", "31208", "21350", "10326", "10332"]
_GROUPS = [[f"LEGO {n}" for n in LEGO],
           ["metal: 10k gold scrap", "metal: 14k gold scrap", "metal: 10k gold ring", "metal: 14k gold chain",
            "metal: sterling silver scrap"],
           ["DD1391-100", "DZ5485-612", "FV5029-006"],
           ["Prismatic Evolutions Elite Trainer Box", "Pokemon 151 Booster Bundle", "Surging Sparks Booster Box",
            "Destined Rivals Elite Trainer Box"]]
# one of each kind in turn, so the first pass covers LEGO, gold, sneakers and sealed cards
DEFAULT_HUNTS = [g[i] for i in range(max(map(len, _GROUPS))) for g in _GROUPS if i < len(g)]
LISTING_EVERY = timedelta(hours=12)
FEED_EVERY = timedelta(minutes=30)
CARD_EVERY = timedelta(hours=24)
OFFER_LIFE = timedelta(days=3)          # a deal post or listing not seen again in 3 days drops out of Deals
NOT_THE_ITEM = re.compile(
    r"\b(?:lots?|box only|empty box|no box|instructions? only|manual only|minifig(?:ure)?s? only|incomplete|"
    r"parts only|for parts|custom|moc|replica|fake|read desc\w*|damaged|broken|sticker sheet|bags? only|"
    r"no minifig\w*|digital|pc|xbox|playstation|ps[45]|switch|code card|empty|display case|led light kit)\b", re.I)
NOT_SOLID = re.compile(r"\b(?:plated|filled|gf|gold tone|vermeil|rolled gold|tone|clad|electroplate\w*)\b", re.I)
GRAMS = re.compile(r"(\d+(?:\.\d+)?)\s*(?:g|gr|grams?|gram|gms?)\b", re.I)
KARAT = re.compile(r"\b(10|14|18|22|24)\s*(?:k|kt|karat|carat)\b", re.I)
RISING = 0.15


def hunts(h: hunting.Hunt) -> list[str]:
    saved = h.state("hunts").get("list")
    return list(saved) if isinstance(saved, list) else list(DEFAULT_HUNTS)


def save_hunts(h: hunting.Hunt, text: str) -> list[str]:
    lines = [" ".join(x.split())[:120] for x in text.splitlines() if x.strip()]
    if len(lines) > 300:
        raise DeskError("Keep it to 300 hunts or fewer")
    h.save_state("hunts", {"list": lines})
    return lines


def _status(h: hunting.Hunt, name: str, now: datetime, *, ok: bool, note: str | None = None, found: int = 0,
            extra: dict[str, Any] | None = None) -> None:
    state = h.state("finders")
    state[name] = {"at": now.isoformat(), "ok": ok, "note": note, "found": found, **(extra or {})}
    h.save_state("finders", state)


def _due(h: hunting.Hunt, key: str, every: timedelta, now: datetime) -> bool:
    last = h.state("finder_runs").get(key)
    return last is None or now - datetime.fromisoformat(last) >= every


def _ran(h: hunting.Hunt, key: str, now: datetime) -> None:
    runs = h.state("finder_runs")
    runs[key] = now.isoformat()
    h.save_state("finder_runs", runs)


def save_offer(h: hunting.Hunt, item_id: str, source: str, url: str, title: str, price: float, shipping: float | None,
               now: datetime, *, image: str | None = None, body: dict[str, Any] | None = None) -> None:
    import json

    oid = sha256_hex(url)[:16]
    with h._tx():
        h.conn.execute(
            "INSERT INTO hunt_offers(id,item_id,source,url,title,image,price,shipping,first_seen,seen_at,body) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET price=excluded.price, "
            "shipping=excluded.shipping, seen_at=excluded.seen_at, gone=0, title=excluded.title, body=excluded.body",
            (oid, item_id, source, url, title[:200], image, price, shipping, now.isoformat(), now.isoformat(),
             json.dumps(body or {})))


def _sold(item: dict[str, Any], h: hunting.Hunt, now: datetime) -> bool:
    usable, _ = h.comps(item["id"])
    return usable is not None and now - datetime.fromisoformat(usable["fetched_at"]) < timedelta(hours=24)


async def listings(h: hunting.Hunt, client: httpx.AsyncClient, ebay: markets.EbaySold, query: str,
                   now: datetime) -> dict[str, Any]:
    """One hunt: refresh its sold prices if a day old, then keep the cheapest matching listings as offers."""
    kind, search = hunting.classify(query)
    item = h.upsert(kind, search, search, now, reveal=False)
    if not _sold(item, h, now):
        await hunting.refresh(h, item, client, ebay, now)
    market, _, _ = h.market(item, now.date())
    if market is None:
        _, attempt = h.comps(item["id"])
        return {"found": 0, "note": (attempt or {}).get("body", {}).get("note") or "no sold prices yet"}
    got = await ebay.listings(search, new_only=item["new_only"], now=now)
    h.save_state("ebay", ebay.state)
    if got["status"] != "ok":
        return {"found": 0, "note": got["note"]}
    codes = [w for w in re.findall(r"[A-Za-z0-9-]+", search) if re.search(r"\d{4,}", w)]
    words = [w for w in re.findall(r"[a-z0-9]+", search.lower()) if len(w) > 1]
    kept = 0
    for listing in got["listings"][:40]:
        title = listing.title.lower()
        if NOT_THE_ITEM.search(listing.title) or not all(c.lower() in title for c in codes) or (
                not codes and not all(w in title for w in words)):
            continue
        save_offer(h, item["id"], "eBay listing", listing.url, listing.title, listing.price, listing.shipping, now,
                   image=listing.image)
        kept += 1
    with h._tx():
        h.conn.execute("UPDATE hunt_offers SET gone=1 WHERE item_id=? AND source='eBay listing' AND seen_at<?",
                       (item["id"], now.isoformat()))
    return {"found": kept, "note": None}


def metal_of(title: str) -> tuple[str, float] | None:
    """(karat or silver purity, grams) when a listing's title states both and isn't plated or filled."""
    if NOT_SOLID.search(title):
        return None
    grams = GRAMS.search(title)
    if grams is None or not 0 < float(grams[1]) < 2000:
        return None
    if re.search(r"\b(?:sterling|925)\b", title, re.I) and not KARAT.search(title):
        return "925", float(grams[1])
    karat = KARAT.search(title)
    return (f"{karat[1]}k", float(grams[1])) if karat else None


async def metal_listings(h: hunting.Hunt, ebay: markets.EbaySold, query: str, now: datetime) -> dict[str, Any]:
    item = h.upsert("item", f"metal: {query}", f"Gold & silver: {query}", now, reveal=False)
    got = await ebay.listings(query, new_only=False, now=now)
    h.save_state("ebay", ebay.state)
    if got["status"] != "ok":
        return {"found": 0, "note": got["note"]}
    kept = 0
    for listing in got["listings"][:60]:
        found = metal_of(listing.title)
        if found is None:
            continue
        karat, grams = found
        save_offer(h, item["id"], "eBay listing", listing.url, listing.title, listing.price, listing.shipping, now,
                   image=listing.image, body={"metal": {"karat": karat, "grams": grams}})
        kept += 1
    with h._tx():
        h.conn.execute("UPDATE hunt_offers SET gone=1 WHERE item_id=? AND seen_at<?", (item["id"], now.isoformat()))
    return {"found": kept, "note": None}


def identify(title: str) -> tuple[str, str] | None:
    """A deal post's item, when its title names one exactly: a LEGO set number or a sneaker style code."""
    if re.search(r"\blego\b", title, re.I) and not re.search(r"\b(?:pc|xbox|playstation|ps[45]|switch|"
                                                             r"digital|video ?game|movie|book)\b", title, re.I):
        number = re.search(r"\((\d{4,6})\)|#\s?(\d{4,6})\b|\b(\d{5})\b", title)
        if number:
            return "lego", f"LEGO {next(g for g in number.groups() if g)}"
    code = re.search(r"\b([A-Z]{1,3}\d{3,5}-\d{3})\b", title)
    if code:
        return "sneakers", code[1]
    return None


async def feeds(h: hunting.Hunt, client: httpx.AsyncClient, ebay: markets.EbaySold, now: datetime,
                *, new_sold_reads: int = 6) -> dict[str, Any]:
    """Deal posts naming an exact item, with that item's sold prices (a few new items' sold prices a run)."""
    posts, errors = [], []
    for name, url in markets.FEEDS.items():
        try:
            posts += [{**p, "feed": name} for p in await markets.feed(client, url)]
        except DeskError as exc:
            errors.append(str(exc))
    kept = reads = 0
    for post in posts:
        found = identify(post["title"])
        if found is None or post["price"] is None:
            continue
        kind, query = found
        item = h.upsert(kind, query, query, now, reveal=False)
        if not _sold(item, h, now):
            if reads >= new_sold_reads or ebay.paused(now):
                continue
            await hunting.refresh(h, item, client, ebay, now)
            reads += 1
        free = re.search(r"free (?:shipping|s/h|delivery|pickup)", post["title"], re.I)
        save_offer(h, item["id"], "Slickdeals" if post["feed"].startswith("slickdeals") else "DealNews", post["url"],
                   post["title"], post["price"], 0.0 if free else None, now)
        kept += 1
    return {"found": kept, "posts": len(posts), "note": "; ".join(errors) or None}


async def cards(h: hunting.Hunt, client: httpx.AsyncClient, now: datetime, *, sets: int = 14) -> dict[str, Any]:
    """Pokémon cards rising at least 15% this week against their 30-day average sale price, worth $5 or more."""
    found = failed = 0
    for s in await markets.pokemon_sets(client, sets):
        try:
            priced = await markets.pokemon_set_cards(client, s["id"])
        except DeskError:
            failed += 1
            continue
        for c in priced:
            if not (c["avg7"] and c["avg30"] and c["market"] >= 5 and c["avg7"] >= c["avg30"] * (1 + RISING)):
                continue
            item = h.upsert("pokemon", c["id"], c["title"], now, image=c["image"], reveal=False)
            h.save_comps(item["id"], "tcgplayer", "market", now, market={
                "price": c["market"], "as_of": c["as_of"], "printing": c["printing"], "avg1": c["avg1"],
                "avg7": c["avg7"], "avg30": c["avg30"]}, url=c.get("url"))
            found += 1
    return {"found": found, "note": f"{failed} sets didn't answer" if failed else None}


async def run(h: hunting.Hunt, client: httpx.AsyncClient, ebay: markets.EbaySold, now: datetime,
              *, budget: int = 3) -> None:
    """One background pass: whatever is due, at most ``budget`` eBay hunts (each one or two pages)."""
    if _due(h, "feeds", FEED_EVERY, now):
        _ran(h, "feeds", now)
        try:
            out = await feeds(h, client, ebay, now)
            _status(h, "feeds", now, ok=out["note"] is None, note=out["note"], found=out["found"],
                    extra={"posts": out["posts"]})
        except DeskError as exc:
            _status(h, "feeds", now, ok=False, note=str(exc))
    if _due(h, "cards", CARD_EVERY, now):
        _ran(h, "cards", now)
        try:
            out = await cards(h, client, now)
            _status(h, "cards", now, ok=True, note=out["note"], found=out["found"])
        except DeskError as exc:
            _status(h, "cards", now, ok=False, note=str(exc))
    if (why := ebay.paused(now)) is not None:
        _status(h, "listings", now, ok=False, note=f"Waiting for eBay: {why}")
        return
    done = 0
    for query in hunts(h):
        if done >= budget:
            break
        if not _due(h, "hunt:" + query, LISTING_EVERY, now):
            continue
        _ran(h, "hunt:" + query, now)
        done += 1
        if query.startswith("metal:"):
            out = await metal_listings(h, ebay, query.removeprefix("metal:").strip(), now)
        else:
            out = await listings(h, client, ebay, query, now)
        state = h.state("finders").get("listings", {})
        found = int(state.get("found", 0)) + out["found"] if state.get("ok") else out["found"]
        _status(h, "listings", now, ok=out["note"] is None, note=out["note"], found=found,
                extra={"last": query})
        if ebay.paused(now):
            break


def offers(h: hunting.Hunt, now: datetime) -> list[dict[str, Any]]:
    """Live offers (seen in the last 3 days, not hidden) as deal views: priced against sold prices, or for gold
    and silver against melt."""
    import json

    spot = h.state("spot")
    since = (now - OFFER_LIFE).isoformat()
    out = []
    for r in h.conn.execute("SELECT o.* FROM hunt_offers o JOIN hunt_items i ON i.id=o.item_id "
                            "WHERE o.gone=0 AND o.hidden=0 AND i.hidden=0 AND o.seen_at>=?", (since,)):
        offer = {k: r[k] for k in ("id", "source", "url", "title", "image", "price", "shipping", "first_seen", "seen_at")}
        total = r["price"] + (r["shipping"] or 0.0)
        body = json.loads(r["body"])
        if "metal" in body:
            m = body["metal"]
            ounce = spot.get("silver" if m["karat"] in value.SILVER else "gold")
            if not ounce:
                continue
            metal = value.evaluate_metal(total, m["grams"], m["karat"], float(ounce), h.settings())
            out.append({"offer": {**offer, **m}, "metal": metal, "item": h.item(r["item_id"])})
            continue
        view = h.view(r["item_id"], now, price=total)
        if view["deal"] is not None:
            out.append({**view, "offer": offer})
    return out


def hide_offer(h: hunting.Hunt, oid: str) -> None:
    with h._tx():
        if not h.conn.execute("UPDATE hunt_offers SET hidden=1 WHERE id=?", (oid,)).rowcount:
            raise DeskError("No such offer")


__all__ = ["DEFAULT_HUNTS", "FINDER_DDL", "cards", "feeds", "hide_offer", "hunts", "identify", "listings", "metal_of",
           "offers", "run", "save_hunts"]
