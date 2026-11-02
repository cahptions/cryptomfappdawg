"""The hunt: check an item's price against what it really sells for, keep every check and every set of sold prices,
and rank what's worth buying.

An item is what you'd search eBay for: a barcode, a LEGO set number, a sneaker style code, a card, or plain words.
Each check records the price you saw (in a store or online). Sold prices are read when you check an item and
refreshed in the background, one item every few minutes, for items you watch or checked in the last two weeks.
Deals ranks every checked item by its profit share; Buy & Hold ranks items whose sold prices are rising.
"""

from __future__ import annotations

import asyncio
import json
import re
import sqlite3
from contextlib import nullcontext
from dataclasses import replace
from datetime import date, datetime, timedelta
from typing import Any

import httpx

from desk.arbitrage import markets, value
from desk.arbitrage.comps import Sale, parse_text
from desk.core.clock import utc_now
from desk.core.errors import DeskError
from desk.core.hashing import sha256_hex
from desk.storage.db import transaction

HUNT_DDL = """
CREATE TABLE IF NOT EXISTS hunt_items(id TEXT PRIMARY KEY, kind TEXT NOT NULL, query TEXT NOT NULL, title TEXT NOT NULL,
 image TEXT, category TEXT NOT NULL, new_only INTEGER NOT NULL, watch INTEGER NOT NULL DEFAULT 0,
 hidden INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, body TEXT NOT NULL DEFAULT '{}');
CREATE TABLE IF NOT EXISTS hunt_comps(item_id TEXT NOT NULL, fetched_at TEXT NOT NULL, source TEXT NOT NULL,
 status TEXT NOT NULL, body TEXT NOT NULL, PRIMARY KEY(item_id, fetched_at, source));
CREATE TABLE IF NOT EXISTS hunt_checks(id INTEGER PRIMARY KEY, item_id TEXT NOT NULL, at TEXT NOT NULL,
 body TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS hunt_checks_item ON hunt_checks(item_id, at);
CREATE TABLE IF NOT EXISTS hunt_state(key TEXT PRIMARY KEY, body TEXT NOT NULL);
CREATE TRIGGER IF NOT EXISTS hunt_comps_append_only BEFORE UPDATE ON hunt_comps
 BEGIN SELECT RAISE(ABORT,'sold prices are append-only'); END;
"""

KINDS = {"item": "general", "lego": "lego", "pokemon": "cards", "mtg": "cards", "sneakers": "sneakers"}
FRESH = timedelta(hours=12)              # a check reuses sold prices read this recently
REFRESH = timedelta(hours=24)            # the background keeps watched items' sold prices this fresh
ACTIVE = timedelta(days=14)              # items checked this recently are refreshed too
_STYLE = re.compile(r"^[A-Z]{1,3}\d{3,5}-\d{3}$")
_STOP = {"the", "a", "an", "and", "of", "for", "with", "new", "in", "on", "to", "by"}


def classify(text: str, kind: str | None = None) -> tuple[str, str]:
    """(kind, eBay search) for what was typed or scanned."""
    t = " ".join(text.split())
    if not t:
        raise DeskError("Type or scan something to check")
    if kind == "lego" or (kind is None and re.fullmatch(r"(?i)(lego\s*)?\d{4,6}(-\d)?", t)):
        number = re.sub(r"(?i)^lego\s*", "", t).split("-")[0]
        return "lego", f"LEGO {number}"
    if kind is None and re.fullmatch(r"\d{12,14}", t):
        return "item", t
    if kind == "sneakers" or (kind is None and _STYLE.match(t.upper())):
        return "sneakers", t.upper() if _STYLE.match(t.upper()) else t
    return (kind if kind in KINDS else "item"), t


def item_id(kind: str, query: str) -> str:
    return sha256_hex(f"{kind}:{query.lower()}")[:16]


def match(query: str, kind: str, sales: list[Sale]) -> tuple[list[Sale], float | None]:
    """Keep sales of the same thing. A barcode search is matched by eBay itself (None); a set number or style code
    must appear in the title; for words, the share of titles holding every word is reported."""
    if not sales or re.fullmatch(r"\d{12,14}", query):
        return sales, None
    codes = [w for w in re.findall(r"[A-Za-z0-9-]+", query) if re.search(r"\d{4,}", w)]
    if codes:
        kept = [s for s in sales if all(c.lower() in s.title.lower() for c in codes)]
        return kept, len(kept) / len(sales)
    words = [w for w in re.findall(r"[a-z0-9]+", query.lower()) if w not in _STOP and len(w) > 1]
    if not words:
        return sales, None
    hits = sum(all(w in s.title.lower() for w in words) for s in sales)
    return sales, hits / len(sales)


class Hunt:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def _tx(self) -> Any:
        return nullcontext() if self.conn.in_transaction else transaction(self.conn)

    # state and settings ──────────────────────────────────────────────────────────────────────────
    def state(self, key: str) -> dict[str, Any]:
        row = self.conn.execute("SELECT body FROM hunt_state WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else {}

    def save_state(self, key: str, body: dict[str, Any]) -> None:
        with self._tx():
            self.conn.execute("INSERT OR REPLACE INTO hunt_state VALUES (?,?)", (key, json.dumps(body)))

    def settings(self) -> dict[str, Any]:
        return {**value.DEFAULTS, **self.state("settings")}

    def save_settings(self, changes: dict[str, Any]) -> dict[str, Any]:
        bounds = {"purchase_tax": (0, 0.2), "buyer_tax": (0, 0.2), "min_profit_pct": (0, 5), "min_profit_usd": (0, 10_000),
                  "scrap_payout": (0.3, 1.0), "bankroll": (0, 10_000_000)}
        current = self.state("settings")
        for key, raw in changes.items():
            if key not in bounds:
                raise DeskError(f"Unknown setting {key}")
            if raw is None and key == "bankroll":
                current.pop(key, None)
                continue
            number = float(raw)
            lo, hi = bounds[key]
            if not lo <= number <= hi:
                raise DeskError(f"{key} must be between {lo} and {hi}")
            current[key] = number
        self.save_state("settings", current)
        return self.settings()

    # items ───────────────────────────────────────────────────────────────────────────────────────
    def item(self, iid: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM hunt_items WHERE id=?", (iid,)).fetchone()
        if row is None:
            return None
        return {**json.loads(row["body"]), "id": row["id"], "kind": row["kind"], "query": row["query"],
                "title": row["title"], "image": row["image"], "category": row["category"],
                "new_only": bool(row["new_only"]), "watch": bool(row["watch"]), "hidden": bool(row["hidden"]),
                "created_at": row["created_at"]}

    def upsert(self, kind: str, query: str, title: str, now: datetime, *, image: str | None = None,
               extra: dict[str, Any] | None = None) -> dict[str, Any]:
        iid = item_id(kind, query)
        with self._tx():
            self.conn.execute(
                "INSERT INTO hunt_items(id,kind,query,title,image,category,new_only,created_at,body) VALUES (?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET hidden=0, title=excluded.title, image=COALESCE(excluded.image, image), "
                "body=excluded.body", (iid, kind, query, title, image, KINDS[kind], int(kind in ("item", "lego", "sneakers")),
                                       now.isoformat(), json.dumps(extra or {})))
        item = self.item(iid)
        assert item is not None
        return item

    def flag(self, iid: str, column: str, on: bool) -> None:
        if column not in ("watch", "hidden"):
            raise DeskError("Unknown flag")
        with self._tx():
            if not self.conn.execute(f"UPDATE hunt_items SET {column}=? WHERE id=?", (int(on), iid)).rowcount:  # noqa: S608
                raise DeskError("No such item")

    # sold prices and checks ───────────────────────────────────────────────────────────────────────
    def save_comps(self, iid: str, source: str, status: str, now: datetime, *, sales: list[Sale] | None = None,
                   market: dict[str, Any] | None = None, note: str | None = None, url: str | None = None) -> None:
        body = {"sales": [s.as_json() for s in sales or []], "market": market, "note": note, "url": url}
        with self._tx():
            self.conn.execute("INSERT OR IGNORE INTO hunt_comps VALUES (?,?,?,?,?)",
                              (iid, now.isoformat(), source, status, json.dumps(body)))

    def comps(self, iid: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        """(the newest usable sold prices, the newest attempt) for an item."""
        rows = self.conn.execute("SELECT * FROM hunt_comps WHERE item_id=? ORDER BY fetched_at DESC LIMIT 40",
                                 (iid,)).fetchall()
        attempt = dict(rows[0]) if rows else None
        usable = next((dict(r) for r in rows if r["status"] in ("ok", "paste", "market", "empty")), None)
        for row in (attempt, usable):
            if row is not None:
                row["body"] = json.loads(row["body"]) if isinstance(row["body"], str) else row["body"]
        return usable, attempt

    def market_history(self, iid: str) -> list[tuple[date, float]]:
        """Each day's market price for items priced by a marketplace (cards), to see a trend over time."""
        out: dict[date, float] = {}
        for r in self.conn.execute("SELECT fetched_at, body FROM hunt_comps WHERE item_id=? AND status='market' "
                                   "ORDER BY fetched_at", (iid,)):
            m = json.loads(r["body"]).get("market") or {}
            if m.get("price"):
                out[datetime.fromisoformat(r["fetched_at"]).date()] = float(m["price"])
        return sorted(out.items())

    def add_check(self, iid: str, price: float, now: datetime, *, quantity: int | None = None, store: str | None = None,
                  shipping: float | None = None) -> None:
        if not 0 < price < 1_000_000:
            raise DeskError("Enter the price you see")
        with self._tx():
            self.conn.execute("INSERT INTO hunt_checks(item_id,at,body) VALUES (?,?,?)", (iid, now.isoformat(), json.dumps(
                {"price": price, "quantity": quantity, "store": store, "shipping": shipping})))

    def last_check(self, iid: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT at, body FROM hunt_checks WHERE item_id=? ORDER BY at DESC, id DESC LIMIT 1",
                                (iid,)).fetchone()
        return None if row is None else {**json.loads(row["body"]), "at": row["at"]}

    # what the app shows ───────────────────────────────────────────────────────────────────────────
    def market(self, item: dict[str, Any], today: date) -> tuple[value.Market | None, float | None, list[Sale]]:
        usable, _ = self.comps(item["id"])
        if usable is None:
            return None, None, []
        body = usable["body"]
        if usable["status"] == "market":
            m = body["market"]
            history = self.market_history(item["id"])
            trend = None
            if len(history) >= 2 and (history[-1][0] - history[0][0]).days >= 30:
                days = (history[-1][0] - history[0][0]).days
                trend = (history[-1][1] / history[0][1]) ** (30 / days) - 1
            base = value.market_price(float(m["price"]), date.fromisoformat(m["as_of"]) if m.get("as_of") else None)
            return replace(base, trend=trend), None, []
        sales = [Sale.from_json(s) for s in body["sales"]]
        kept, ratio = match(item["query"], item["kind"], sales)
        return value.summarize(kept, today), ratio, kept

    def view(self, iid: str, now: datetime) -> dict[str, Any]:
        item = self.item(iid)
        if item is None:
            raise DeskError("No such item")
        market, ratio, sales = self.market(item, now.date())
        if re.fullmatch(r"\d{12,14}", item["title"]) and sales:      # an unknown barcode: name it from its sales
            item = {**item, "title": max(sales, key=lambda x: x.sold_on or date.min).title[:90]}
        usable, attempt = self.comps(iid)
        check = self.last_check(iid)
        out: dict[str, Any] = {
            "item": item, "check": check, "sold_url": markets.sold_url(item["query"], new_only=item["new_only"]),
            "status": None if attempt is None else {"source": attempt["source"], "status": attempt["status"],
                                                    "at": attempt["fetched_at"], "note": attempt["body"].get("note")},
            "comps_at": None if usable is None else usable["fetched_at"], "match": ratio,
            "sales": [s.as_json() for s in sorted(sales, key=lambda s: s.sold_on or date.min, reverse=True)[:30]],
            "market": None if market is None else market.as_json(), "deal": None,
        }
        if market is not None and check is not None:
            out["deal"] = value.evaluate(
                float(check["price"]), market, category=item["category"], settings=self.settings(),
                quantity=check.get("quantity"), shipping=check.get("shipping"), match=ratio,
                comps_at=datetime.fromisoformat(usable["fetched_at"]) if usable else None,
                price_at=datetime.fromisoformat(check["at"]), now=now)
        return out

    def _views(self, now: datetime, where: str) -> list[dict[str, Any]]:
        ids = [r[0] for r in self.conn.execute(f"SELECT id FROM hunt_items WHERE hidden=0 AND {where}")]  # noqa: S608
        return [self.view(i, now) for i in ids]

    def deals(self, now: datetime, kind: str | None = None) -> list[dict[str, Any]]:
        """Every checked item, best first: Buy before Maybe before Skip, then the biggest profit share; items still
        waiting for sold prices come last."""
        rows = [v for v in self._views(now, "id IN (SELECT item_id FROM hunt_checks)")
                if kind is None or v["item"]["kind"] == kind or (kind == "cards" and v["item"]["category"] == "cards")]
        order = {"buy": 0, "maybe": 1, "skip": 2}
        return sorted(rows, key=lambda v: (order[v["deal"]["verdict"]], -v["deal"]["profit_pct"]) if v["deal"]
                      else (3, 0.0))

    def holds(self, now: datetime) -> list[dict[str, Any]]:
        """Items whose sold prices are rising at least 10% a year, steepest first (needs dated sales over 3 weeks,
        or a month of a card's market prices)."""
        rows = [v for v in self._views(now, "1") if v["market"] and v["market"]["yearly"] is not None
                and v["market"]["yearly"] >= 0.10 and (v["market"]["basis"] == "market_price" or v["market"]["count"] >= 8)]
        return sorted(rows, key=lambda v: -v["market"]["yearly"])

    def due(self, now: datetime, limit: int = 1) -> list[dict[str, Any]]:
        """Items whose sold prices the background should refresh next: watched or recently checked, oldest first."""
        since = (now - ACTIVE).isoformat()
        rows = self.conn.execute(
            "SELECT i.id, (SELECT MAX(fetched_at) FROM hunt_comps c WHERE c.item_id=i.id) AS last FROM hunt_items i "
            "WHERE i.hidden=0 AND (i.watch=1 OR EXISTS (SELECT 1 FROM hunt_checks k WHERE k.item_id=i.id AND k.at>=?)) "
            "ORDER BY last IS NOT NULL, last", (since,)).fetchall()
        out = []
        for r in rows:
            if r["last"] is None or now - datetime.fromisoformat(r["last"]) >= REFRESH:
                item = self.item(r["id"])
                if item:
                    out.append(item)
            if len(out) >= limit:
                break
        return out


# ── reading prices ──────────────────────────────────────────────────────────────────────────────────
async def card(client: httpx.AsyncClient, card_id: str) -> dict[str, Any]:
    """A Pokémon or Magic card by its id, with today's market price."""
    game, _, raw = card_id.partition(":")
    if game == "pokemon" and raw.startswith("tcgdex:"):
        found = await markets._json(client, f"https://api.tcgdex.net/v2/en/cards/{raw.removeprefix('tcgdex:')}")
        parsed = markets._tcgdex_card(found) if found else None
    elif game == "pokemon":
        found = await markets._json(client, f"https://api.pokemontcg.io/v2/cards/{raw}")
        parsed = markets._pokemon_card((found or {}).get("data") or {}) if found else None
    elif game == "mtg":
        found = await markets._json(client, f"https://api.scryfall.com/cards/{raw}")
        hits = [c for c in await markets.magic(client, f"!\"{found['name']}\" set:{found['set']}")
                if c["id"] == card_id] if found else []
        parsed = hits[0] if hits else None
    else:
        raise DeskError("Unknown card")
    if parsed is None:
        raise DeskError("That card has no market price today")
    return parsed


async def refresh(hunt: Hunt, item: dict[str, Any], client: httpx.AsyncClient, ebay: markets.EbaySold,
                  now: datetime) -> str:
    """Read an item's sold prices now; returns the status recorded."""
    if item["kind"] in ("pokemon", "mtg"):
        try:
            c = await card(client, item["query"])
        except DeskError as exc:
            hunt.save_comps(item["id"], "tcgplayer", "error", now, note=str(exc))
            return "error"
        hunt.save_comps(item["id"], "tcgplayer", "market", now, market={"price": c["market"], "as_of": c["as_of"],
                                                                         "printing": c["printing"]}, url=c.get("url"))
        return "market"
    got = await ebay.fetch(item["query"], new_only=item["new_only"], now=now)
    hunt.save_state("ebay", ebay.state)
    hunt.save_comps(item["id"], "ebay", got["status"], now, sales=got["sales"], note=got["note"], url=got["url"])
    return str(got["status"])


async def check(hunt: Hunt, client: httpx.AsyncClient, ebay: markets.EbaySold, now: datetime, *, text: str,
                price: float | None = None, quantity: int | None = None, kind: str | None = None,
                store: str | None = None, shipping: float | None = None) -> dict[str, Any]:
    """Check one thing: identify it, read its sold prices (unless read in the last 12 hours), record your price."""
    if text.startswith(("pokemon:", "mtg:")):
        c = await card(client, text)
        item = hunt.upsert(c["kind"], c["id"], c["title"], now, image=c["image"])
    elif kind in ("pokemon", "mtg"):
        raise DeskError("Search the card, then pick it from the results")
    else:
        kind, query = classify(text, kind)
        title, image = query, None
        extra: dict[str, Any] = {}
        if re.fullmatch(r"\d{12,14}", query):
            existing = hunt.item(item_id(kind, query))
            if existing and existing["title"] != query:
                title, image, extra = existing["title"], existing["image"], {}
            else:
                with_name = await _barcode(client, query)
                if with_name:
                    title, extra = with_name["title"], {"retail_low": with_name.get("retail_low")}
                    image = with_name["image"] if _shown(with_name["image"]) else None
        item = hunt.upsert(kind, query, title, now, image=image, extra=extra)
    if price is not None:
        hunt.add_check(item["id"], price, now, quantity=quantity, store=store, shipping=shipping)
    usable, _ = hunt.comps(item["id"])
    if usable is None or now - datetime.fromisoformat(usable["fetched_at"]) >= FRESH:
        await refresh(hunt, item, client, ebay, now)
    return hunt.view(item["id"], now)


async def _barcode(client: httpx.AsyncClient, upc: str) -> dict[str, Any] | None:
    try:
        return await markets.barcode(client, upc)
    except DeskError:
        return None                          # the name is a nicety; eBay matches the barcode itself


def _shown(url: str | None) -> bool:
    return bool(url) and any(f"//{h}/" in str(url) for h in markets.IMAGE_HOSTS)


def paste(hunt: Hunt, iid: str, text: str, now: datetime) -> dict[str, Any]:
    """Sold prices from the text of an eBay sold-results page the operator copied."""
    if hunt.item(iid) is None:
        raise DeskError("No such item")
    sales = parse_text(text[:500_000])
    if not sales:
        raise DeskError("No sold results found in that text. On eBay's sold results, select all, copy, and paste here.")
    hunt.save_comps(iid, "paste", "paste", now, sales=sales, note=f"{len(sales)} sales pasted")
    return hunt.view(iid, now)


class Hunter:
    """The background: refreshes one due item's sold prices every few minutes and the metal spot price every 30.
    It shares the app's eBay reader, so the one-request-per-20-seconds pace holds across checks and refreshes."""

    def __init__(self, open_hunt: Any, client: httpx.AsyncClient, ebay: markets.EbaySold, *, every: float = 300) -> None:
        self.open_hunt = open_hunt           # a context manager factory yielding a Hunt
        self.client = client
        self.ebay = ebay
        self.every = every
        self.task: asyncio.Task[None] | None = None
        self.error: str | None = None

    def start(self) -> None:
        if self.task is None or self.task.done():
            self.task = asyncio.create_task(self.run())

    async def stop(self) -> None:
        if self.task is not None:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            self.task = None

    async def run(self) -> None:
        while True:
            try:
                await self.tick(utc_now())
                self.error = None
            except (DeskError, sqlite3.Error, httpx.HTTPError) as exc:
                self.error = f"{type(exc).__name__}: {str(exc)[:200]}"
            await asyncio.sleep(self.every)

    async def tick(self, now: datetime) -> None:
        with self.open_hunt() as hunt:
            await spot(hunt, self.client, now)
            for item in hunt.due(now):
                await refresh(hunt, item, self.client, self.ebay, now)


async def spot(hunt: Hunt, client: httpx.AsyncClient, now: datetime) -> dict[str, Any]:
    """Gold and silver spot, read at most every 30 minutes."""
    saved = hunt.state("spot")
    if not saved.get("at") or now - datetime.fromisoformat(saved["at"]) >= timedelta(minutes=30):
        saved = {**await markets.spot(client), "at": now.isoformat()}
        hunt.save_state("spot", saved)
    return saved


__all__ = ["HUNT_DDL", "Hunt", "Hunter", "check", "classify", "item_id", "match", "paste", "refresh", "spot"]
