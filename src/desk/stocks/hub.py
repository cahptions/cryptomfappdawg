"""Bounded views of stored market research for the Stocks home page; no provider calls or strategy changes."""

from __future__ import annotations

import json
import sqlite3
from datetime import date
from typing import Any

from desk.stocks import calendar as cal
from desk.stocks.repo import StocksMarket, StocksResearch, date_of

INDEX_NAMES = {"SPY": "S&P 500", "QQQ": "Nasdaq 100", "IWM": "Russell 2000", "DIA": "Dow Jones",
               "TLT": "Treasuries", "GLD": "Gold"}
SECTOR_NAMES = {"XLK": "Technology", "XLF": "Financials", "XLV": "Healthcare", "XLE": "Energy",
                "XLI": "Industrials", "XLY": "Consumer discretionary", "XLP": "Consumer staples", "XLU": "Utilities",
                "XLB": "Materials", "XLRE": "Real estate", "XLC": "Communication"}


def adjusted_bars(conn: sqlite3.Connection, symbol: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Split-adjust only the displayed history through its latest observed day. Never create observations."""
    if not rows:
        return rows
    splits = conn.execute("SELECT execution_date, split_from, split_to FROM stk_splits "
                          "WHERE symbol=? AND execution_date>? AND execution_date<=?",
                          (symbol, min(r["day"] for r in rows), max(r["day"] for r in rows))).fetchall()
    for split in splits:
        if split["split_from"] <= 0 or split["split_to"] <= 0:
            continue
        ratio = split["split_to"] / split["split_from"]
        for row in rows:
            if row["day"] < split["execution_date"]:
                for field in ("open", "high", "low", "close"):
                    if field in row:
                        row[field] /= ratio
                if "volume" in row:
                    row["volume"] *= ratio
    ordered = sorted(rows, key=lambda r: r["day"])
    for index, row in enumerate(ordered):
        row["gap_before"] = bool(index and cal.next_trading_day(date_of(ordered[index - 1]["day"]))
                                 != date_of(row["day"]))
    return rows


def price_card(conn: sqlite3.Connection, symbol: str, name: str | None = None, *, bars: int = 24) -> dict[str, Any]:
    rows = adjusted_bars(conn, symbol, [dict(r) for r in conn.execute(
        "SELECT day, close FROM stk_bars WHERE symbol=? ORDER BY day DESC LIMIT ?", (symbol, bars))])
    current, previous = (rows[0] if rows else None), (rows[1] if len(rows) > 1 else None)
    return {"symbol": symbol, "name": name or symbol, "price": current["close"] if current else None,
            "asof": date_of(current["day"]).isoformat() if current else None,
            "change": current["close"] / previous["close"] - 1 if current and previous and previous["close"] else None,
            "previous_day": date_of(previous["day"]).isoformat() if previous else None,
            "consecutive_sessions": bool(current and previous and cal.next_trading_day(date_of(previous["day"]))
                                         == date_of(current["day"])),
            "history": [{"day": date_of(r["day"]).isoformat(), "close": r["close"], "gap_before": r["gap_before"]}
                        for r in reversed(rows)]}


def home(market_conn: sqlite3.Connection, research_conn: sqlite3.Connection) -> dict[str, Any]:
    research = StocksResearch(research_conn)
    priority = ["trending", "gainers", "losers", "momentum", "committee", "undervalued", "squeeze", "unusual_options",
                "cheap_iv", "big_moves", "earnings_week", "insider_buying", "quality_fair", "new_highs"]
    wanted = research.watchlist()[:25]
    for list_id in priority:
        _, rows = research.list_on(list_id)
        wanted.extend(r["symbol"] for r in rows[:10])
    wanted = list(dict.fromkeys(wanted))[:150]
    stocks = {}
    for symbol in wanted:
        row = research_conn.execute(
            "SELECT json_extract(detail_json, '$.name') name, json_extract(detail_json, '$.sector') sector, "
            "json_extract(detail_json, '$.committee.score') score, json_extract(detail_json, '$.committee.label') verdict, "
            "json_extract(detail_json, '$.price.rel_volume') relative_volume FROM stk_scores_latest WHERE symbol=?",
            (symbol,)).fetchone()
        stocks[symbol] = {**price_card(market_conn, symbol), **(dict(row) if row else {})}
    breadth = research_conn.execute(
        "SELECT COUNT(*) researched, COUNT(json_extract(detail_json, '$.price.ret_1d')) observed, "
        "SUM(json_extract(detail_json, '$.price.ret_1d') > 0) advancing, "
        "SUM(json_extract(detail_json, '$.price.ret_1d') < 0) declining, "
        "SUM(json_extract(detail_json, '$.price.ret_1d') = 0) unchanged FROM stk_scores_latest").fetchone()
    return {"benchmarks": [price_card(market_conn, s, name) for s, name in INDEX_NAMES.items()],
            "sectors": [price_card(market_conn, s, name, bars=2) for s, name in SECTOR_NAMES.items()],
            "stocks": stocks, "breadth": dict(breadth), "watchlist": research.watchlist()[:25],
            "limits": {"stock_cards": 150, "sparkline_observations": 24, "list_rows": 10},
            "basis": "Stored daily closes; ETF benchmarks are proxies, not index quotes. No live data calls."}


def option_table(conn: sqlite3.Connection, symbol: str, *, expiration: date | None = None) -> dict[str, Any]:
    market = StocksMarket(conn)
    # Read one compressed expiry; never unpack a full-universe chain for a page request.
    newest = conn.execute("SELECT MAX(day) FROM stk_option_chains WHERE underlying=?", (symbol,)).fetchone()[0]
    if newest is None:
        return {"asof": None, "expirations": [], "contracts": [], "source": None}
    expirations = [date_of(r[0]) for r in conn.execute(
        "SELECT expiration FROM stk_option_chains WHERE underlying=? AND day=? ORDER BY expiration", (symbol, newest))]
    selected = expiration if expiration is not None else next((e for e in expirations if e >= date_of(newest)), expirations[0])
    row = conn.execute("SELECT * FROM stk_option_chains WHERE underlying=? AND day=? AND expiration=?",
                       (symbol, newest, selected.toordinal())).fetchone()
    if row is None:
        return {"asof": date_of(newest).isoformat(), "expirations": [e.isoformat() for e in expirations],
                "contracts": [], "source": None, "expiration": selected.isoformat()}
    import zlib

    raw = json.loads(zlib.decompress(row["chain_zlib"]))
    price = market.chain_underlying_price(symbol, date_of(newest))
    nearest = sorted(raw, key=lambda q: abs(float(q[2]) - float(price or q[2])))[:40]
    contracts = [{"occ": q[0], "kind": q[1], "strike": q[2], "bid": q[3], "ask": q[4],
                  "last": q[5], "volume": q[6], "open_interest": q[7], "quoted_at": q[9]} for q in nearest]
    return {"asof": date_of(newest).isoformat(), "observed_at": row["observed_at"], "source": row["source"],
            "expirations": [e.isoformat() for e in expirations], "expiration": selected.isoformat(),
            "underlying_price": price, "contracts": sorted(contracts, key=lambda q: (q["strike"], q["kind"]))}
