"""Everything the stock page shows beside the chart, built from stored records plus the cached company feeds.

Financials come from the company's own SEC filings (XBRL), short interest from FINRA, insider trades from Form 4,
dividends from Massive, lists and scores from KESTREL's research record. Each section names its source and date;
nothing is estimated to fill a gap. Display only: research features never read from here.
"""

from __future__ import annotations

import json
import math
import re
import sqlite3
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any

from desk.stocks.fundamentals import ANNUAL, Fundamentals, _latest_per_period
from desk.stocks.repo import StocksMarket, StocksResearch, date_of

FLOW_NAMES = ("revenue", "gross_profit", "net_income", "eps")
CEO = re.compile(r"\bchief executive\b|\bceo\b", re.I)
CODES = {"P": "Bought", "S": "Sold", "A": "Granted", "M": "Exercised options", "F": "Tax withholding", "G": "Gift",
         "D": "Returned to issuer", "C": "Converted", "X": "Exercised options"}


# ── financials ──────────────────────────────────────────────────────────────────────────────────
def periods(series: list[Any]) -> tuple[dict[date, tuple[float, bool]], dict[date, float]]:
    """Quarter-end → (value, derived) and fiscal-year-end → value, each the latest filed figure for its period.
    A fourth quarter that is only reported inside the annual report is the year minus its first nine months."""
    facts = list(_latest_per_period([f for f in series if f.start is not None]).values())
    quarters: dict[date, tuple[float, bool]] = {f.end: (f.value, False) for f in facts if 80 <= f.days <= 100}
    years = {f.end: f.value for f in facts if ANNUAL[0] <= f.days <= ANNUAL[1]}
    for year in (f for f in facts if ANNUAL[0] <= f.days <= ANNUAL[1]):
        if any(abs((end - year.end).days) <= 7 for end in quarters) or year.start is None:
            continue
        nine = [f for f in facts if 255 <= f.days <= 290 and f.start is not None
                and abs((f.start - year.start).days) <= 7 and f.end < year.end]
        if nine:
            quarters[year.end] = (year.value - max(nine, key=lambda f: f.end).value, True)
    return quarters, years


def _near(values: dict[date, Any], end: date) -> Any:
    if end in values:
        return values[end]
    return next((v for d, v in values.items() if abs((d - end).days) <= 5), None)


def financials(f: Fundamentals, as_of: date) -> dict[str, Any] | None:
    found = {name: periods(f.series(name, as_of)) for name in FLOW_NAMES}
    cost = periods(f.series("cost_of_revenue", as_of))

    def table(index: int, limit: int) -> list[dict[str, Any]]:
        primary = found["revenue"][index] or found["net_income"][index]
        out = []
        for end in sorted(primary)[-limit:]:
            row: dict[str, Any] = {"end": end.isoformat(), "derived": []}
            for name in FLOW_NAMES:
                value = _near(found[name][index], end)
                if isinstance(value, tuple):
                    value, derived = value
                    if derived:
                        row["derived"].append(name)
                row[name] = value
            if row["gross_profit"] is None and row["revenue"] is not None:
                spent = _near(cost[index], end)
                spent = spent[0] if isinstance(spent, tuple) else spent
                row["gross_profit"] = None if spent is None else row["revenue"] - spent
            row["net_margin"] = (row["net_income"] / row["revenue"]
                                 if row["revenue"] and row["net_income"] is not None else None)
            out.append(row)
        return out

    quarterly, annual = table(0, 8), table(1, 5)
    if not quarterly and not annual:
        return None
    return {"quarterly": quarterly, "annual": annual, "source": "Company filings with the SEC (XBRL)",
            "note": "As filed. A fourth quarter reported only in the annual report is the year minus its first nine "
                    "months; EPS for such a quarter is approximate because share counts change during the year."}


# ── short interest ──────────────────────────────────────────────────────────────────────────────
def short_interest(conn: sqlite3.Connection, symbol: str, shares_out: float | None) -> dict[str, Any] | None:
    rows = StocksMarket(conn).short_interest(symbol)[-12:]
    volume = conn.execute("SELECT day, short_volume, total FROM stk_short_volume WHERE symbol=? AND total>0 "
                          "ORDER BY day DESC LIMIT 30", (symbol,)).fetchall()
    if not rows and not volume:
        return None
    series = [{"settlement": date_of(r["settlement"]).isoformat(), "shares": r["short_shares"],
               "share_of_outstanding": r["short_shares"] / shares_out if shares_out else None,
               "days_to_cover": r["days_to_cover"], "avg_volume": r["avg_volume"]} for r in rows]
    latest = dict(series[-1]) if series else None
    if latest and rows[-1]["previous"]:
        latest["change"] = rows[-1]["short_shares"] / rows[-1]["previous"] - 1
    return {"series": series, "latest": latest,
            "daily_volume": [{"day": date_of(r["day"]).isoformat(), "short_share": r["short_volume"] / r["total"]}
                             for r in reversed(volume)],
            "source": "FINRA short interest (twice a month) and Reg SHO daily short-sale volume",
            "note": "Short-sale volume counts trades marked short that day, including market makers; it is not the "
                    "number of shares held short."}


# ── insiders ───────────────────────────────────────────────────────────────────────────────────
def person(name: str) -> str:
    """Form 4 owners are filed surname-first in capitals: ``COOK TIMOTHY D`` → ``Timothy D. Cook``. Only the
    unambiguous shapes are reordered; anything else is shown as filed, in title case."""
    parts = name.replace(",", " ").split()
    if name.upper() != name or not 2 <= len(parts) <= 3 or any(not p.isalpha() for p in parts):
        return " ".join(p.capitalize() if p.isupper() else p for p in parts)
    if len(parts) == 3 and len(parts[2]) != 1:
        return " ".join(p.capitalize() for p in parts)
    given = [p.capitalize() + ("." if len(p) == 1 else "") for p in parts[1:]]
    return " ".join([*given, parts[0].capitalize()])


def role(row: sqlite3.Row) -> str:
    if row["officer"] and row["title"]:
        return " ".join(row["title"].split())[:60]
    if row["director"]:
        return "Director"
    return "10% owner" if row["ten_percent"] else "Insider"


def insiders(conn: sqlite3.Connection, symbol: str, today: date) -> dict[str, Any] | None:
    rows = StocksMarket(conn).insider_trades(today - timedelta(days=372), symbol=symbol)
    if not rows:
        return None
    months: dict[str, dict[str, float]] = defaultdict(lambda: {"bought": 0.0, "sold": 0.0, "planned_sold": 0.0})
    buyers, sellers = set(), set()
    for r in rows:
        if r["code"] not in ("P", "S") or not r["price"] or r["price"] <= 0:
            continue
        month = date_of(r["day"]).strftime("%Y-%m")
        value = r["shares"] * r["price"]
        if r["code"] == "P" and r["acquired"]:
            months[month]["bought"] += value
            buyers.add(r["owner_cik"])
        elif r["code"] == "S":
            months[month]["sold"] += value
            months[month]["planned_sold"] += value if r["planned"] else 0.0
            sellers.add(r["owner_cik"])
    start = date(today.year - 1, today.month, 1)
    calendar: list[dict[str, Any]] = []
    while start <= today:
        key = start.strftime("%Y-%m")
        found = months.get(key, {})
        calendar.append({"month": key, **{k: round(found.get(k, 0.0), 2) for k in ("bought", "sold", "planned_sold")}})
        start = date(start.year + start.month // 12, start.month % 12 + 1, 1)
    recent = sorted(rows, key=lambda r: (r["day"], r["filed"]), reverse=True)[:12]
    return {"months": calendar,
            "bought": round(sum(m.get("bought", 0.0) for m in calendar), 2),
            "sold": round(sum(m.get("sold", 0.0) for m in calendar), 2),
            "planned_sold": round(sum(m.get("planned_sold", 0.0) for m in calendar), 2),
            "buyers": len(buyers), "sellers": len(sellers),
            "recent": [{"day": date_of(r["day"]).isoformat(), "filed": date_of(r["filed"]).isoformat(),
                        "name": person(r["owner"]), "role": role(r), "code": r["code"],
                        "action": CODES.get(r["code"], f"Code {r['code']}"), "shares": r["shares"], "price": r["price"],
                        "value": r["shares"] * r["price"] if r["price"] else None, "planned": bool(r["planned"])}
                       for r in recent],
            "source": "SEC Form 4 filings",
            "note": "Bought and sold are open-market trades (codes P and S). Sales under pre-arranged Rule 10b5-1 "
                    "plans are marked: they were scheduled in advance."}


def chief_executive(conn: sqlite3.Connection, symbol: str) -> dict[str, Any] | None:
    """The most recent Form 4 filer whose officer title names the chief executive."""
    row = conn.execute("SELECT owner, title, filed FROM stk_insider_trades WHERE symbol=? AND officer=1 AND title IS NOT "
                       "NULL ORDER BY filed DESC", (symbol,)).fetchall()
    found = next((r for r in row if CEO.search(r["title"])), None)
    return None if found is None else {"name": person(found["owner"]), "source": "SEC Form 4",
                                       "asof": date_of(found["filed"]).isoformat()}


# ── dividends ──────────────────────────────────────────────────────────────────────────────────
def dividends(conn: sqlite3.Connection, symbol: str, price: float | None, today: date) -> dict[str, Any] | None:
    rows = conn.execute("SELECT ex_date, cash, pay_date, frequency, kind FROM stk_dividends WHERE symbol=? "
                        "ORDER BY ex_date", (symbol,)).fetchall()
    if not rows:
        return None
    paid = [r for r in rows if date_of(r["ex_date"]) <= today]
    trailing = sum(r["cash"] for r in paid if date_of(r["ex_date"]) > today - timedelta(days=365))
    upcoming = next((r for r in rows if date_of(r["ex_date"]) > today), None)

    def item(r: sqlite3.Row | None) -> dict[str, Any] | None:
        return None if r is None else {"ex_date": date_of(r["ex_date"]).isoformat(), "cash": r["cash"],
                                       "pay_date": date_of(r["pay_date"]).isoformat() if r["pay_date"] else None,
                                       "kind": r["kind"]}

    return {"trailing_12m": round(trailing, 6) if trailing else None,
            "yield": trailing / price if trailing and price else None,
            "frequency": paid[-1]["frequency"] if paid else None,
            "last": item(paid[-1] if paid else None), "next": item(upcoming),
            "history": [item(r) for r in paid[-12:]],
            "source": "Massive dividends", "note": "Yield is the last twelve months of cash dividends over the price."}


# ── research record ─────────────────────────────────────────────────────────────────────────────
def related_lists(research: sqlite3.Connection, symbol: str, titles: dict[str, str]) -> list[dict[str, Any]]:
    store = StocksResearch(research)
    days = store.snapshot_days()
    if not days:
        return []
    return [{"id": r["list_id"], "title": titles.get(r["list_id"], r["list_id"]), "rank": r["rank"],
             "reason": r["reason"]} for r in store.lists_for(symbol, days[-1]) if r["list_id"] in titles]


def score_history(research: sqlite3.Connection, symbol: str) -> list[dict[str, Any]]:
    return [{"day": d.isoformat(), "score": s.get("c")} for d, s in StocksResearch(research).score_history(symbol)
            if s.get("c") is not None][-260:]


def peers(market: sqlite3.Connection, research: sqlite3.Connection, symbol: str, sector: str | None,
          market_cap: float | None, limit: int = 6) -> list[dict[str, Any]]:
    """Same-sector companies closest in size, with KESTREL's latest score."""
    if not sector:
        return []
    names = {r["symbol"]: r["name"] for r in market.execute(
        "SELECT l.symbol, l.name FROM stk_listings l JOIN stk_companies c ON c.cik=l.cik WHERE c.sector=? "
        "AND l.active=1 AND l.etf=0 AND l.test=0 AND l.symbol<>?", (sector, symbol))}
    asof = research.execute("SELECT MAX(asof) FROM stk_score_history").fetchone()[0]
    if not names or asof is None:
        return []
    found = []
    for r in research.execute("SELECT symbol, scores_json FROM stk_score_history WHERE asof=?", (asof,)):
        if r["symbol"] in names:
            s = json.loads(r["scores_json"])
            if s.get("mcap") and s.get("px"):
                found.append((r["symbol"], s))
    if market_cap:
        found.sort(key=lambda x: abs(math.log(x[1]["mcap"] / market_cap)))
    else:
        found.sort(key=lambda x: -x[1]["mcap"])
    from desk.stocks.hub import price_card

    out = []
    for peer, s in found[:limit]:
        card = price_card(market, peer, names[peer], bars=22)
        out.append({"symbol": peer, "name": names[peer], "price": card["price"], "change": card["change"],
                    "spark": [h["close"] for h in card["history"]], "score": s.get("c"), "market_cap": s["mcap"]})
    return out


# ── the page ───────────────────────────────────────────────────────────────────────────────────
def _feed(feeds: dict[str, dict[str, Any]], name: str) -> Any:
    return (feeds.get(name) or {}).get("value")


def about(listing: sqlite3.Row | None, profile: dict[str, Any], feeds: dict[str, dict[str, Any]],
          ceo: dict[str, Any] | None) -> dict[str, Any]:
    """One description and one value per fact, each from the most direct source available, and labelled."""
    details, wiki, summary = _feed(feeds, "details") or {}, _feed(feeds, "wikidata") or {}, _feed(feeds, "wikipedia")
    meta = profile.get("metadata") or {}
    description = None
    if details.get("description"):
        description = {"text": details["description"], "source": "Massive", "url": None}
    elif summary:
        description = {"text": summary["extract"], "source": "Wikipedia", "url": summary.get("url")}
    elif profile.get("about"):
        description = {"text": profile["about"], "source": "Annual report (SEC)", "url": profile.get("source_url")}
    facts: list[dict[str, Any]] = []

    def fact(label: str, value: Any, source: str, note: str | None = None) -> None:
        if value not in (None, "") and not any(f["label"] == label for f in facts):
            facts.append({"label": label, "value": value, "source": source, **({"note": note} if note else {})})

    if ceo:
        fact("CEO", ceo["name"], "SEC Form 4", f"latest filing {ceo['asof']}")
    fact("CEO", wiki.get("ceo"), "Wikidata")
    fact("Founded", wiki.get("founded"), "Wikidata")
    fact("Employees", details.get("employees"), "Massive")
    fact("Employees", wiki.get("employees"), "Wikidata", f"as of {wiki['employees_asof']}" if wiki.get("employees_asof")
         else None)
    fact("Headquarters", details.get("headquarters"), "Massive")
    fact("Headquarters", meta.get("headquarters"), "SEC")
    fact("Headquarters", wiki.get("headquarters"), "Wikidata")
    fact("Listed", details.get("list_date"), "Massive")
    fact("Industry", (profile.get("sic_description") or details.get("industry") or "").capitalize() or None,
         "SEC" if profile.get("sic_description") else "Massive")
    fact("Exchange", listing["exchange"] if listing else None, "Nasdaq Trader")
    website = details.get("homepage_url") or meta.get("website") or wiki.get("website")
    return {"name": details.get("name") or (listing["name"] if listing else None), "description": description,
            "facts": facts, "website": website if isinstance(website, str) and website.startswith("http") else None,
            "wikipedia": wiki.get("article"), "fiscal_year_end": profile.get("fiscal_year_end"),
            "filing": {"url": profile.get("source_url"), "date": profile.get("filing_date")}
            if profile.get("source_url") else None}


def earnings(f_rows: dict[str, Any] | None, feeds: dict[str, dict[str, Any]], today: date) -> dict[str, Any] | None:
    estimates = _feed(feeds, "earnings")
    calendar = _feed(feeds, "calendar") or []
    upcoming = next((c for c in calendar if c["date"] >= today.isoformat()), None)
    if estimates:
        history = [{"period": e["period"], "actual": e["actual"], "estimate": e["estimate"],
                    "surprise_pct": e["surprise_pct"]} for e in estimates]
        source = "Finnhub (actual vs analyst consensus)"
    elif f_rows and f_rows.get("quarterly"):
        history = [{"period": q["end"], "actual": q["eps"], "estimate": None, "surprise_pct": None,
                    "derived": "eps" in q["derived"]} for q in f_rows["quarterly"] if q["eps"] is not None]
        source = "Company filings with the SEC (diluted EPS)"
    else:
        history = []
        source = None
    if not history and upcoming is None:
        return None
    return {"history": history[-8:], "next": upcoming, "source": source}


def ratings(feeds: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    rows = _feed(feeds, "ratings")
    if not rows:
        return None
    latest = rows[0]
    buy, hold, sell = latest["strong_buy"] + latest["buy"], latest["hold"], latest["sell"] + latest["strong_sell"]
    total = buy + hold + sell
    return None if total == 0 else {"period": latest["period"], "buy": buy, "hold": hold, "sell": sell, "total": total,
                                    "history": rows[:6], "source": "Finnhub analyst recommendation trends"}


def notable_events(research: sqlite3.Connection, symbol: str) -> list[dict[str, Any]]:
    """This symbol's entries in the notable-events log (its own big moves, or its fund's), newest first."""
    from desk.stocks import market_events

    try:
        return market_events.read(research, symbol=symbol, limit=12)
    except sqlite3.OperationalError:
        return []


def build(market: sqlite3.Connection, research: sqlite3.Connection, symbol: str, now: datetime, *,
          detail: dict[str, Any] | None, quote: dict[str, Any] | None, profile: dict[str, Any],
          feeds: dict[str, dict[str, Any]], list_titles: dict[str, str], finnhub_configured: bool,
          has_logo: bool) -> dict[str, Any]:
    today = now.date()
    listing = market.execute("SELECT name, exchange, cik, etf FROM stk_listings WHERE symbol=?", (symbol,)).fetchone()
    fund = (detail or {}).get("fundamentals") or {}
    price = (quote or {}).get("last") or ((detail or {}).get("price") or {}).get("close")
    f = None
    if listing and listing["cik"] and not listing["etf"]:
        f = Fundamentals(StocksMarket(market).facts(int(listing["cik"])))
    fin = financials(f, today + timedelta(days=1)) if f else None
    shares = f.value("shares_out", today + timedelta(days=1)) if f else None
    details = _feed(feeds, "details") or {}
    shares = shares or details.get("shares_outstanding")
    market_cap = fund.get("market_cap") or (shares * price if shares and price else None) or details.get("market_cap")
    sources = {name: {"label": v["label"], "updated_at": v["updated_at"], "error": v["error"]} for name, v in feeds.items()}
    return {"symbol": symbol, "asof": now.isoformat(),
            "about": about(listing, profile, feeds, chief_executive(market, symbol)),
            "market_cap": market_cap, "shares_outstanding": shares,
            "quote_details": (quote or {}).get("details") or {},
            "financials": fin, "earnings": earnings(fin, feeds, today), "ratings": ratings(feeds),
            "short_interest": short_interest(market, symbol, shares),
            "insiders": insiders(market, symbol, today),
            "dividends": dividends(market, symbol, price, today),
            "news": (_feed(feeds, "news") or [])[:12],
            "lists": related_lists(research, symbol, list_titles),
            "peers": peers(market, research, symbol, (detail or {}).get("sector"), market_cap),
            "score_history": score_history(research, symbol),
            "events": notable_events(research, symbol),
            "logo": has_logo, "finnhub_configured": finnhub_configured, "sources": sources}


__all__ = ["about", "build", "chief_executive", "dividends", "earnings", "financials", "insiders", "notable_events",
           "peers", "periods", "person", "ratings", "related_lists", "score_history", "short_interest"]
