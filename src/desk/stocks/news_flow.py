"""Company news and everything the News desk reads, as of a given time.

Company news is Massive's market-wide feed: every article once, with the stocks it names and Massive's own sentiment
for each (positive, neutral, negative). Per stock the desk sees the last seven days' tone, and attention: articles in
the last three days against its usual daily count. Research reads only articles received by its analysis time.

World events (``desk.stocks.world``) and trade actions (``desk.stocks.policy``) reach a stock through its exposure:
named, or by its SIC code. Everything is counted and weighed by rule; nothing is read by an AI.
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from typing import Any

from desk.core.errors import DeskError
from desk.stocks import calendar as cal
from desk.stocks import policy, world

FIRST_BACKFILL = timedelta(days=30)
CALLS_PER_PASS = 3


def save(conn: sqlite3.Connection, articles: list[dict[str, Any]], now: datetime) -> int:
    from desk.storage.db import transaction

    new = 0
    with transaction(conn):
        for a in articles:
            cur = conn.execute("INSERT OR IGNORE INTO stk_news VALUES (?,?,?,?,?,?,?)",
                               (a["id"], a["published"], now.isoformat(), a.get("source"), a["title"], a["url"],
                                a.get("summary")))
            if not cur.rowcount:
                continue
            new += 1
            sentiments = {i["ticker"]: i for i in a.get("insights") or [] if i.get("ticker")}
            for symbol in dict.fromkeys([*(a.get("tickers") or []), *sentiments]):
                i = sentiments.get(symbol) or {}
                conn.execute("INSERT OR IGNORE INTO stk_news_tickers VALUES (?,?,?,?)",
                             (a["id"], symbol, i.get("sentiment"), i.get("reasoning")))
    return new


async def collect(conn: sqlite3.Connection, massive: Any, now: datetime) -> dict[str, Any]:
    """Forward from the newest article stored (30 days back the first time), a few pages a pass."""
    if not getattr(massive, "configured", False):
        return {"new": 0, "note": "No Massive key: company news needs MASSIVE_API_KEY"}
    newest = conn.execute("SELECT MAX(published_at) FROM stk_news").fetchone()[0]
    since = datetime.fromisoformat(newest) if newest else now - FIRST_BACKFILL
    new = calls = 0
    while calls < CALLS_PER_PASS:
        articles = await massive.market_news(since)
        calls += 1
        new += save(conn, articles, now)
        if len(articles) < 1000:
            break
        since = datetime.fromisoformat(articles[-1]["published"])
    return {"new": new, "calls": calls, "through": since.isoformat()}


def company(conn: sqlite3.Connection, asof: date, received_by: datetime) -> dict[str, dict[str, Any]]:
    """Per stock: the last seven days' tone, attention against the 60 days before, and the newest headlines."""
    end = datetime.combine(asof, time(23, 59), cal.NEW_YORK)
    rows = conn.execute(
        "SELECT t.symbol, t.sentiment, n.published_at, n.title, n.url, n.source FROM stk_news_tickers t "
        "JOIN stk_news n ON n.id=t.news_id WHERE n.published_at>=? AND n.published_at<=? AND n.received_at<=? "
        "ORDER BY n.published_at DESC", ((end - timedelta(days=67)).isoformat(), end.isoformat(),
                                         received_by.isoformat())).fetchall()
    out: dict[str, dict[str, Any]] = defaultdict(lambda: {"positive": 0, "negative": 0, "neutral": 0, "recent3": 0,
                                                          "earlier60": 0, "headlines": []})
    for symbol, sentiment, published, title, url, source in rows:
        age = (end - datetime.fromisoformat(published)).total_seconds() / 86400
        s = out[symbol]
        if age <= 7:
            if sentiment in ("positive", "negative", "neutral"):
                s[sentiment] += 1
            if len(s["headlines"]) < 3:
                s["headlines"].append({"title": title, "url": url, "source": source, "published": published,
                                       "sentiment": sentiment})
        if age <= 3:
            s["recent3"] += 1
        elif 7 < age <= 67:
            s["earlier60"] += 1
    return dict(out)


def inputs(conn: sqlite3.Connection, asof: date, received_by: datetime, symbols: list[str],
           sic: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """What the News desk reads for each stock: company news, world supply shocks and trade actions."""
    try:
        news = company(conn, asof, received_by)
        nodes = [n for n in world.status(conn, asof) if n["spike"]]
        actions = [d for d in policy.documents(conn, asof - timedelta(days=15), asof) if d["trade"]]
    except sqlite3.OperationalError:          # a database from before news collection
        return {}
    out: dict[str, dict[str, Any]] = {}
    for s in symbols:
        shocks = []
        for n in nodes:
            sign = world.exposure(world.BY_ID[n["id"]], s, sic.get(s))
            if sign:
                shocks.append({"node": n["id"], "label": n["label"], "why": n["why"], "sign": sign,
                               "strength": n["strength"], "ratio": n["ratio"]})
        trade = []
        for d in actions:
            for tid in d["topics"]:
                sign = policy.exposure(policy.BY_TOPIC[tid], s, sic.get(s))
                if sign:
                    age = (asof - date.fromisoformat(d["signed"][:10])).days
                    trade.append({"title": d["title"], "url": d["url"], "signed": d["signed"],
                                  "topic": policy.BY_TOPIC[tid].label, "sign": sign, "weight": max(0.0, 1 - age / 15)})
        c = news.get(s)
        if c or shocks or trade:
            out[s] = {**(c or {}), "world": shocks, "policy": trade}
    return out


async def job(conn: sqlite3.Connection, massive: Any, now: datetime, *, due: dict[str, bool]) -> dict[str, Any]:
    """One pass of whatever is due: world events every pass, company news and policy every few hours."""
    import httpx

    out: dict[str, Any] = {}
    async with httpx.AsyncClient(timeout=httpx.Timeout(30), follow_redirects=True) as client:
        for name in ("world", "policy", "news"):
            if not due.get(name, True):
                continue
            try:
                if name == "world":
                    out[name] = await world.collect(conn, client, now)
                elif name == "policy":
                    out[name] = await policy.collect(conn, client, cal.new_york(now).date())
                else:
                    out[name] = await collect(conn, massive, now)
            except DeskError as exc:
                out[name] = {"error": str(exc)[:240]}
    return out


__all__ = ["collect", "company", "inputs", "job", "save"]
