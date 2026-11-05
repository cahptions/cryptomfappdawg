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

HISTORY = timedelta(days=70)          # attention compares the last 3 days with days 8-67 before: 70 kept, 3 to spare
FIRST_FORWARD = timedelta(days=7)     # an empty store starts with the week tone reads; history then fills backward
CALLS_PER_PASS = 3                    # forward and history together: Massive's 5 a minute is shared by the whole app
EARLIER_MIN_DAYS = 14                 # fewer stored days of the comparison window: no attention measure at all


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
    """New articles forward from the newest stored (the last 7 days the first time), then, once caught up, history
    backward from the oldest stored until 70 days are kept: at most three calls a pass in all.

    Both directions extend one unbroken stretch of time, so ``company`` can tell how much of a window is stored. A
    store begun with less history (30 days until 2026-11) is extended back the same way."""
    if not getattr(massive, "configured", False):
        return {"new": 0, "note": "No Massive key: company news needs MASSIVE_API_KEY"}
    newest = conn.execute("SELECT MAX(published_at) FROM stk_news").fetchone()[0]
    since = datetime.fromisoformat(newest) if newest else now - FIRST_FORWARD
    new = calls = 0
    caught_up = False
    while calls < CALLS_PER_PASS:
        articles = await massive.market_news(since)
        calls += 1
        new += save(conn, articles, now)
        if len(articles) < 1000:
            caught_up = True
            break
        since = datetime.fromisoformat(articles[-1]["published"])
    out: dict[str, Any] = {"new": new, "calls": calls, "through": since.isoformat()}
    if caught_up:
        out["history"] = await _history(conn, massive, now, CALLS_PER_PASS - calls)
    return out


async def _history(conn: sqlite3.Connection, massive: Any, now: datetime, calls: int) -> dict[str, Any]:
    """Older articles, newest first from the oldest stored, until it is 70 days old. A call that adds nothing older
    means Massive has no more: that point is recorded (the ``news_history`` job) and not asked about again."""
    from desk.stocks.repo import StocksMarket

    market = StocksMarket(conn)
    state = market.jobs().get("news_history", {}).get("detail", {})
    target = now - HISTORY
    used = added = 0
    oldest = conn.execute("SELECT MIN(published_at) FROM stk_news").fetchone()[0]
    exhausted = state.get("exhausted_at")
    while oldest is not None and used < calls and datetime.fromisoformat(oldest) > target and exhausted != oldest:
        added += save(conn, await massive.market_news_before(datetime.fromisoformat(oldest)), now)
        used += 1
        older = conn.execute("SELECT MIN(published_at) FROM stk_news").fetchone()[0]
        if older == oldest:
            exhausted = oldest
        oldest = older
    complete = oldest is not None and (datetime.fromisoformat(oldest) <= target or exhausted == oldest)
    detail = {"oldest": oldest, "target": target.isoformat(), "complete": complete, "calls": used, "new": added,
              "exhausted_at": exhausted if exhausted == oldest else None}
    if used or state.get("complete") != complete:
        market.job("news_history", now, detail=detail)
    return detail


def company(conn: sqlite3.Connection, asof: date, received_by: datetime) -> dict[str, dict[str, Any]]:
    """Per stock: the last seven days' tone, attention against the 60 days before, and the newest headlines.

    ``earlier60`` is the comparison count for a full 60 days: when the store (as received by ``received_by``) begins
    inside that window, the count over the days it does cover is scaled up to 60, and with under two weeks covered it
    is 0, so the News desk reports no attention. Counting a 60-day usual level over 23 stored days made every stock
    look about three times busier than usual. ``earlier_days`` says how many days were covered."""
    end = datetime.combine(asof, time(23, 59), cal.NEW_YORK)
    first = conn.execute("SELECT MIN(published_at) FROM stk_news WHERE received_at<=?",
                         (received_by.isoformat(),)).fetchone()[0]
    window_start = end - timedelta(days=67)
    if first is not None:
        window_start = max(window_start, datetime.fromisoformat(first))
    covered = max(0.0, (end - timedelta(days=7) - window_start).total_seconds() / 86400)
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
    for s in out.values():
        s["earlier60"] = round(s["earlier60"] * 60 / covered, 2) if covered >= EARLIER_MIN_DAYS else 0
        s["earlier_days"] = round(covered, 1)
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
