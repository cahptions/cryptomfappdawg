"""Policy actions: presidential documents (executive orders, proclamations, memoranda) from the Federal Register,
read for trade actions (tariffs, duties, import adjustments), sanctions and export controls, the sectors and
countries they name, and which US companies they plausibly help or hurt.

Tariffs on imports of a product help the US companies that make it and hurt the US companies that buy it; the
mapping below is written down as hypotheses. ``event_study`` checks them against what prices actually did after
each trade action since January 2025: SPY and the named companies over the next 1, 5, 10 and 20 sessions, against
SPY's ordinary returns over the same stretch. It reports what happened; it doesn't promise it again.

The Federal Register publishes a document days after it is signed, and announcements often come earlier still
(news conferences, posts), so the signing date is used and the study measures from there.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta
from statistics import mean, median
from typing import Any

import httpx

from desk.core.errors import DeskError

API = "https://www.federalregister.gov/api/v1/documents.json"
AGENT = "KESTRELStocks/1.0 (personal market research; local app)"
SINCE = date(2025, 1, 20)
TRADE = re.compile(r"\btariffs?\b|\bduties\b|\bduty\b|\bimports?\b|section 232|section 301|reciprocal|trade agreement",
                   re.I)
SANCTIONS = re.compile(r"sanction|export control|entity list|embargo|blocking property", re.I)
COUNTRIES = ("China", "Canada", "Mexico", "India", "Brazil", "Japan", "Korea", "Vietnam", "Russia", "Iran",
             "Venezuela", "European Union", "Switzerland", "Taiwan", "United Kingdom")


@dataclass(frozen=True)
class Topic:
    id: str
    label: str
    pattern: str
    helped_sic: tuple[str, ...] = ()
    helped: tuple[str, ...] = ()
    hurt_sic: tuple[str, ...] = ()
    hurt: tuple[str, ...] = ()


TOPICS = (
    Topic("steel", "Steel", r"\bsteel\b", ("3312", "3316", "3317"), ("NUE", "STLD", "CLF", "CMC"),
          ("3711", "3585", "3531")),
    Topic("aluminum", "Aluminum", r"\balumin(?:i)?um\b", ("3334", "3350", "3353"), ("AA", "CENX", "KALU"), ("3411",)),
    Topic("copper", "Copper", r"\bcopper\b", ("1021",), ("FCX", "SCCO"), ("3351", "3357")),
    Topic("lumber", "Lumber & timber", r"\blumber\b|\btimber\b|wood products", ("2421", "2436"), ("WY", "PCH"),
          ("1531",)),
    Topic("autos", "Cars & auto parts", r"automobiles?|passenger vehicles|light trucks|auto(?:motive)? parts", (), (),
          ("5500", "5510", "5531"), ("TM", "HMC", "STLA")),
    Topic("trucks", "Heavy trucks", r"heavy[- ]duty trucks|medium[- ] and heavy|\btrucks?\b", (), ("PCAR",),
          ("4213",)),
    Topic("semiconductors", "Semiconductors", r"semiconductor", (), ("INTC", "GFS", "TXN", "MU"),
          ("3571", "3572", "3576", "3577", "3661", "3663")),
    Topic("pharma", "Pharmaceuticals", r"pharmaceutical", (), (), ("2834",)),
    Topic("machinery", "Robotics & industrial machinery", r"robotics|industrial machinery|machine tools",
          ("3541", "3542", "3549", "3559", "3569"), (), ("3711",)),
    Topic("minerals", "Critical minerals", r"critical minerals?|rare earth|processed critical", (), ("MP", "UUUU", "ALB"),
          ()),
    Topic("ships", "Shipbuilding", r"shipbuilding|maritime|vessels", ("3730", "3731"), ("HII",), ()),
    Topic("wind", "Wind turbines", r"wind turbines?", (), ("GEV",), ()),
    Topic("furniture", "Furniture & cabinets", r"furniture|kitchen cabinets|vanities|upholstered", ("2510", "2511",
          "2520"), (), ("5712",)),
)


def tags(title: str, abstract: str | None) -> dict[str, Any]:
    text = f"{title} {abstract or ''}"
    return {"trade": bool(TRADE.search(text)), "sanctions": bool(SANCTIONS.search(text)),
            "topics": [t.id for t in TOPICS if re.search(t.pattern, text, re.I)],
            "countries": [c for c in COUNTRIES if re.search(rf"\b{c}\b", text)]}


async def fetch(client: httpx.AsyncClient, since: date, *, pages: int = 10) -> list[dict[str, Any]]:
    """Presidential documents published since ``since``, newest first."""
    out: list[dict[str, Any]] = []
    for page in range(1, pages + 1):
        params: list[tuple[str, str | int | float | bool | None]] = [
            ("conditions[type][]", "PRESDOCU"), ("conditions[publication_date][gte]", since.isoformat()),
            ("per_page", 100), ("page", page), ("order", "newest")]
        params += [("fields[]", f) for f in ("document_number", "title", "publication_date", "signing_date",
                                             "html_url", "abstract", "subtype")]
        try:
            response = await client.get(API, params=params, headers={"User-Agent": AGENT})
        except httpx.HTTPError as exc:
            raise DeskError(f"The Federal Register didn't answer ({type(exc).__name__})") from exc
        if response.status_code != 200:
            raise DeskError(f"The Federal Register answered HTTP {response.status_code}")
        try:
            value = response.json()
        except ValueError as exc:
            raise DeskError("The Federal Register sent an unreadable answer") from exc
        results = value.get("results") or []
        out += [r for r in results if isinstance(r, dict) and r.get("document_number") and r.get("title")]
        if len(results) < 100:
            break
    return out


def save(conn: sqlite3.Connection, docs: list[dict[str, Any]]) -> int:
    new = 0
    for d in docs:
        try:
            published = date.fromisoformat(d["publication_date"]).toordinal()
        except (KeyError, TypeError, ValueError):
            continue
        body = {"abstract": (d.get("abstract") or "")[:2000], **tags(d["title"], d.get("abstract"))}
        cur = conn.execute("INSERT OR IGNORE INTO stk_policy_docs VALUES (?,?,?,?,?,?,?)",
                           (d["document_number"], published, d.get("signing_date"), d["title"][:500],
                            str(d.get("html_url") or ""), d.get("subtype"), json.dumps(body)))
        new += cur.rowcount
    return new


async def collect(conn: sqlite3.Connection, client: httpx.AsyncClient, today: date) -> dict[str, Any]:
    """Everything since January 2025 the first time, then the last two weeks (late publications)."""
    have = conn.execute("SELECT COUNT(*) FROM stk_policy_docs").fetchone()[0]
    since = SINCE if not have else today - timedelta(days=14)
    docs = await fetch(client, since, pages=10 if not have else 2)
    return {"documents": len(docs), "new": save(conn, docs)}


def documents(conn: sqlite3.Connection, since: date, until: date) -> list[dict[str, Any]]:
    out = []
    for r in conn.execute("SELECT * FROM stk_policy_docs WHERE published BETWEEN ? AND ? ORDER BY published DESC",
                          (since.toordinal(), until.toordinal())):
        body = json.loads(r["body"])
        signed = r["signed"] or date.fromordinal(r["published"]).isoformat()
        out.append({"number": r["number"], "title": r["title"], "url": r["url"], "subtype": r["subtype"],
                    "published": date.fromordinal(r["published"]).isoformat(), "signed": signed, **body,
                    "topic_labels": [t.label for t in TOPICS if t.id in body.get("topics", [])]})
    return out


def exposure(topic: Topic, symbol: str, sic: str | int | None) -> int:
    if symbol in topic.helped:
        return 1
    if symbol in topic.hurt:
        return -1
    code = str(sic or "")
    if code and any(code.startswith(p) for p in topic.helped_sic):
        return 1
    if code and any(code.startswith(p) for p in topic.hurt_sic):
        return -1
    return 0


BY_TOPIC = {t.id: t for t in TOPICS}


# ── what happened after trade actions ─────────────────────────────────────────────────────────────
def _forward(closes: list[tuple[date, float]], day: date, sessions: int) -> float | None:
    """The return from the close before ``day`` to the close ``sessions`` sessions after it."""
    i = next((k for k, (d, _) in enumerate(closes) if d >= day), None)
    if i is None or i == 0 or i + sessions - 1 >= len(closes):
        return None
    return closes[i + sessions - 1][1] / closes[i - 1][1] - 1


def event_study(conn: sqlite3.Connection, market: Any, *, horizons: tuple[int, ...] = (1, 5, 10, 20)) -> dict[str, Any]:
    """SPY (and each topic's named companies against SPY) after trade actions since January 2025, events within three
    days of each other counted once, against SPY's ordinary returns over the same period."""
    docs = [d for d in documents(conn, SINCE, date.today()) if d["trade"]]
    if not docs:
        return {"events": 0, "note": "No trade actions collected yet"}
    spy = [(b.day, b.close) for b in market.bars("SPY", SINCE - timedelta(days=10))]
    if len(spy) < 30:
        return {"events": 0, "note": "SPY's daily history is needed for the study"}
    days: list[date] = []
    events: list[dict[str, Any]] = []
    for d in sorted(docs, key=lambda d: d["signed"]):
        signed = date.fromisoformat(d["signed"][:10])
        if not days or (signed - days[-1]).days > 3:
            days.append(signed)
            events.append(d)
    out: dict[str, Any] = {"events": len(days), "first": days[0].isoformat(), "last": days[-1].isoformat(),
                           "horizons": {}}
    for h in horizons:
        after = [r for r in (_forward(spy, d, h) for d in days) if r is not None]
        ordinary = [spy[i + h - 1][1] / spy[i - 1][1] - 1 for i in range(1, len(spy) - h + 1) if spy[i][0] >= SINCE]
        if not after or not ordinary:
            continue
        out["horizons"][str(h)] = {"n": len(after), "mean": mean(after), "median": median(after),
                                   "up_share": sum(r > 0 for r in after) / len(after), "ordinary_mean": mean(ordinary),
                                   "ordinary_up_share": sum(r > 0 for r in ordinary) / len(ordinary)}
    topics: dict[str, Any] = {}
    for topic in TOPICS:
        named = [*topic.helped, *topic.hurt]
        hits = [date.fromisoformat(e["signed"][:10]) for e in events if topic.id in e["topics"]]
        if not hits or not named:
            continue
        rows = []
        for symbol in named:
            bars = [(b.day, b.close) for b in market.bars(symbol, SINCE - timedelta(days=10))]
            sign = 1 if symbol in topic.helped else -1
            for hit in hits:
                a, s = _forward(bars, hit, 5), _forward(spy, hit, 5)
                if a is not None and s is not None:
                    rows.append(sign * (a - s))
        if rows:
            topics[topic.id] = {"label": topic.label, "n": len(rows), "mean_vs_spy": mean(rows),
                                "right_share": sum(r > 0 for r in rows) / len(rows), "actions": len(hits)}
    out["topics"] = topics
    return out


def recent_exposure(conn: sqlite3.Connection, symbol: str, sic: str | int | None, asof: date,
                    days: int = 15) -> list[dict[str, Any]]:
    """Trade actions in the last ``days`` that name a sector this company makes or buys, with their direction."""
    out = []
    for d in documents(conn, asof - timedelta(days=days), asof):
        if not d["trade"]:
            continue
        for tid in d["topics"]:
            sign = exposure(BY_TOPIC[tid], symbol, sic)
            if sign:
                age = (asof - date.fromisoformat(d["signed"][:10])).days
                out.append({"title": d["title"], "url": d["url"], "signed": d["signed"], "topic": BY_TOPIC[tid].label,
                            "sign": sign, "weight": max(0.0, 1 - age / days)})
    return out


__all__ = ["BY_TOPIC", "TOPICS", "Topic", "collect", "documents", "event_study", "exposure", "fetch", "recent_exposure",
           "save", "tags"]
