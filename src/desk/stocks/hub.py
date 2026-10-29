"""Bounded views of stored market research for the Stocks home page; no provider calls or strategy changes."""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime
from typing import Any

from desk.stocks import calendar as cal
from desk.stocks.lists import LISTS
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


def research_lists(research: StocksResearch, preview: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Prefer a newer mutable research preview, without altering any dated evidence list."""
    current = {v["id"]: v for v in (preview or {}).get("lists", [])}
    output = []
    for spec in LISTS:
        day, rows = research.list_on(spec.id)
        latest = research.conn.execute("SELECT created_at,spec_sha256 FROM stk_list_days WHERE list_id=? "
                                       "ORDER BY asof DESC LIMIT 1",
                                       (spec.id,)).fetchone()
        value = current.get(spec.id)
        if spec.id == "trending" and value and value.get("spec_sha256") != spec.spec_sha256:
            value = None  # Do not relabel an old crowd-weighted preview as market-only.
        if (value and (day is None or value["asof"] >= day.isoformat()) and
                (latest is None or (preview or {}).get("computed_at", "") > latest[0])):
            output.append(value)
        else:
            if latest and latest["spec_sha256"] != spec.spec_sha256:
                day,rows = None,[]  # Frozen old records remain in the DB; current discovery waits for its own rules.
            stored = []
            for row in rows[:25]:
                base = dict(row)
                details = json.loads(base.pop("detail_json", "{}"))
                stored.append({**details, **base})
            output.append({"id": spec.id, "title": spec.title, "blurb": spec.blurb,
                           "asof": str(day) if day else None, "rows": stored, "preview": False})
    return output


DESK_ORDER = ("valuation", "quality", "growth", "momentum", "earnings", "insiders", "shorts", "risk", "macro", "options",
              "crowd")


def list_detail(market_conn: sqlite3.Connection, research_conn: sqlite3.Connection, lists: list[dict[str, Any]],
                list_id: str) -> dict[str, Any] | None:
    """One research list with what a reader needs beside each stock: the company, price moves, a three-month chart,
    the committee's score and every desk's, what lifts and what holds the score back, and the strongest signals."""
    from desk.stocks.desks import DESK_LABELS
    from desk.stocks.lists import BY_ID

    item = next((x for x in lists if x["id"] == list_id), None)
    spec = BY_ID.get(list_id)
    if item is None or spec is None:
        return None
    rows = []
    for r in item.get("rows", [])[:25]:
        symbol = r["symbol"]
        found = research_conn.execute("SELECT detail_json FROM stk_scores_latest WHERE symbol=?", (symbol,)).fetchone()
        detail = json.loads(found[0]) if found else {}
        weights = detail.get("weights") or {}
        desks: list[dict[str, Any]] = []
        for name in DESK_ORDER:
            score = ((detail.get("desks") or {}).get(name) or {}).get("score")
            desks.append({"id": name, "label": DESK_LABELS.get(name, name),
                          "score": None if score is None else round(float(score)),
                          "voting": float(weights.get(name) or 0) > 0})
        voting = [d for d in desks if d["voting"] and d["score"] is not None]
        listing = market_conn.execute("SELECT name FROM stk_listings WHERE symbol=?", (symbol,)).fetchone()
        card = price_card(market_conn, symbol, detail.get("name") or (listing[0] if listing else symbol), bars=64)
        price = detail.get("price") or {}
        committee = detail.get("committee") or {}
        rows.append({**r, "name": card["name"], "sector": detail.get("sector"), "close": card["price"],
                     "close_day": card["asof"], "change_1d": card["change"], "ret_1m": price.get("ret_21d"),
                     "ret_1y": price.get("ret_252d"), "spark": [h["close"] for h in card["history"]],
                     "committee": {"score": committee.get("score"), "label": committee.get("label")},
                     "desks": desks,
                     "lifts": [d for d in sorted(voting, key=lambda d: -d["score"]) if d["score"] >= 55][:3],
                     "drags": [d for d in sorted(voting, key=lambda d: d["score"]) if d["score"] <= 45][:2],
                     "bull": (detail.get("bull") or [])[:2], "bear": (detail.get("bear") or [])[:2],
                     "analysis_asof": detail.get("asof")})
    return {**item, "rows": rows, "rule": spec.rule, "claim": spec.claim,
            "lists": [{"id": x["id"], "title": x["title"]} for x in lists]}


def home(market_conn: sqlite3.Connection, research_conn: sqlite3.Connection, *,
         preview: dict[str, Any] | None = None, options_view: dict[str, Any] | None = None) -> dict[str, Any]:
    from desk.stocks.options_view import overlay

    research = StocksResearch(research_conn)
    priority = ["market_activity", "options_activity", "trending", "wsb", "gainers", "losers", "momentum", "committee",
                "undervalued", "squeeze", "unusual_options",
                "cheap_iv", "big_moves", "earnings_week", "insider_buying", "quality_fair", "new_highs"]
    wanted = research.watchlist()[:25]
    lists = {value["id"]: value for value in overlay(research_lists(research, preview), options_view)}
    for list_id in priority:
        wanted.extend(r["symbol"] for r in lists[list_id]["rows"][:10])
    wanted = list(dict.fromkeys(wanted))[:150]
    stocks = {}
    for symbol in wanted:
        row = research_conn.execute(
            "SELECT json_extract(detail_json, '$.name') name, json_extract(detail_json, '$.sector') sector, "
            "json_extract(detail_json, '$.committee.score') score, json_extract(detail_json, '$.committee.label') verdict, "
            "json_extract(detail_json, '$.price.rel_volume') relative_volume FROM stk_scores_latest WHERE symbol=?",
            (symbol,)).fetchone()
        listing = market_conn.execute("SELECT name FROM stk_listings WHERE symbol=?", (symbol,)).fetchone()
        stocks[symbol] = {**price_card(market_conn, symbol, listing[0] if listing else symbol), **(dict(row) if row else {})}
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


def option_table(conn: sqlite3.Connection, symbol: str, *, expiration: date | None = None,
                 now: datetime | None = None, limit: int = 40, asof: date | None = None,
                 focus: tuple[str, ...] = ()) -> dict[str, Any]:
    from desk.core.clock import utc_now
    from desk.stocks.activity import quote_session

    market = StocksMarket(conn)
    # Read one compressed expiry; never unpack a full-universe chain for a page request.
    newest = (asof.toordinal() if asof is not None else
              conn.execute("SELECT MAX(day) FROM stk_option_chains WHERE underlying=?", (symbol,)).fetchone()[0])
    if newest is None:
        return {"asof": None, "expirations": [], "contracts": [], "source": None}
    expirations = [date_of(r[0]) for r in conn.execute(
        "SELECT expiration FROM stk_option_chains WHERE underlying=? AND day=? ORDER BY expiration", (symbol, newest))]
    expected = quote_session(now or utc_now())
    selected = expiration if expiration is not None else next((e for e in expirations if e >= expected), expirations[0])
    row = conn.execute("SELECT * FROM stk_option_chains WHERE underlying=? AND day=? AND expiration=?",
                       (symbol, newest, selected.toordinal())).fetchone()
    if row is None:
        return {"asof": date_of(newest).isoformat(), "expirations": [e.isoformat() for e in expirations],
                "contracts": [], "source": None, "expiration": selected.isoformat()}
    import zlib

    try:
        raw = json.loads(zlib.decompress(row["chain_zlib"]))
        if (not isinstance(raw, list) or any(not isinstance(q, list) or len(q) != 10
                or not isinstance(q[0], str) or q[1] not in ("call", "put")
                or not isinstance(q[2], (int, float)) for q in raw)):
            raise ValueError
    except (ValueError, TypeError, zlib.error):
        return {"asof": date_of(newest).isoformat(), "expirations": [e.isoformat() for e in expirations],
                "contracts": [], "source": row["source"], "status": "stored_snapshot_unreadable",
                "message": "This saved expiry is unreadable. Collection has been requested again; other symbols are unaffected."}
    price = market.chain_underlying_price(symbol, date_of(newest))
    # Balance both sides: a large one-sided chain cannot consume the other tab's entire allowance.
    per_side = {kind: sorted((q for q in raw if q[1] == kind),
                            key=lambda q: abs(float(q[2]) - float(price or q[2])) or q[2])
                for kind in ("call", "put")}
    allowance = limit // 2 if all(per_side.values()) else limit
    nearest = [q for side in per_side.values() for q in side[:allowance]]
    focused = [q for q in raw if q[0] in focus]
    nearest = (focused + [q for q in nearest if q[0] not in focus])[:max(limit, len(focused))]
    contracts = [{"occ": q[0], "kind": q[1], "strike": q[2], "bid": q[3], "ask": q[4],
                  "last": q[5], "volume": q[6], "open_interest": q[7], "quoted_at": q[9]} for q in nearest]
    return {"asof": date_of(newest).isoformat(), "observed_at": row["observed_at"], "source": row["source"],
            "stored_contracts": len(raw), "displayed_contracts": len(contracts),
            "stale_session": date_of(newest) < expected, "expired": selected < expected,
            "expirations": [e.isoformat() for e in expirations], "expiration": selected.isoformat(),
            "underlying_price": price, "contracts": sorted(contracts, key=lambda q: (q["strike"], q["kind"]))}


def etf_profile(conn: sqlite3.Connection, symbol: str, bars: list[dict[str, Any]], name: str) -> dict[str, Any] | None:
    """Read-only fund context from a bounded, gap-preserving panel. No company scores or singleton percentiles."""
    if not bars:
        return None
    import math

    import numpy as np

    from desk.stocks.analysis import PRICE_FEATURES
    from desk.stocks.features import compute
    from desk.stocks.prices import Panel
    from desk.stocks.signals import options

    days = cal.trading_days(date_of(bars[0]["day"]), date_of(bars[-1]["day"]))
    names = list(dict.fromkeys([symbol, "SPY"]))
    arrays = [np.full((len(names), len(days)), np.nan) for _ in range(5)]
    columns = {d.toordinal(): i for i, d in enumerate(days)}
    for index, ticker in enumerate(names):
        rows = bars if ticker == symbol else adjusted_bars(conn, ticker, [dict(r) for r in conn.execute(
            "SELECT day, open, high, low, close, volume FROM stk_bars WHERE symbol=? AND day>=? AND day<=? ORDER BY day",
            (ticker, bars[0]["day"], bars[-1]["day"]))])
        for row in rows:
            if row["day"] in columns:
                for a, key in zip(arrays, ("open", "high", "low", "close", "volume"), strict=True):
                    a[index, columns[row["day"]]] = row[key]
    if not days:
        return None
    pf = compute(Panel(names, days, arrays[0], arrays[1], arrays[2], arrays[3], arrays[4]), len(days) - 1)
    px = {key: pf.get(key, symbol) for key in PRICE_FEATURES}
    px = {key: value if value is not None and math.isfinite(value) else None for key, value in px.items()}
    closes = arrays[3][0]
    for lookback, keys in {21: ("vol_20d",), 50: ("sma50", "above_sma50"),
                           200: ("sma200", "above_sma200"),
                           20: ("dollar_volume_20d",),
                           252: ("high_52w", "low_52w", "max_drawdown_1y"),
                           253: ("beta_1y",)}.items():
        if len(closes) < lookback or not np.isfinite(closes[-lookback:]).all():
            for key in keys:
                px[key] = None
    latest_option = conn.execute("SELECT MAX(day) FROM stk_option_daily WHERE underlying=?", (symbol,)).fetchone()[0]
    opt = options(StocksMarket(conn), date_of(latest_option) if latest_option else days[-1], symbol=symbol).get(symbol, {})
    desks = {}
    for key, measures in {"momentum": ("ret_21d", "ret_63d", "above_sma200"),
                          "risk": ("vol_20d", "max_drawdown_1y", "beta_1y")}.items():
        known = {k: px[k] for k in measures if px[k] is not None}
        desks[key] = {"score": None, "confidence": len(known) / len(measures),
                      "verdict": "21-session price return" if key == "momentum" else "20-session annualized volatility",
                      "reasons": [f"{k}: {v:.4g}" for k, v in known.items()] or ["Insufficient stored sessions"]}
    desks["options"] = {"score": None, "confidence": 1.0 if opt else 0.0,
                        "verdict": "Stored options sample" if opt else "No options sample",
                        "reasons": [f"Option session: {opt.get('day', 'missing')}; observed: {opt.get('observed_at', 'missing')}",
                                    "Volume and open interest do not identify trade direction."]}
    bull: list[str] = []
    bear: list[str] = []
    for key, label in (("ret_21d", "21-session price return"), ("ret_63d", "63-session price return"),
                       ("above_sma50", "Distance from 50-session average"),
                       ("above_sma200", "Distance from 200-session average")):
        value = px[key]
        if value is not None:
            (bull if value >= 0 else bear).append(f"{label}: {value:+.2%}.")
    if px["vol_20d"] is not None:
        bear.append(f"20-session annualized price volatility: {px['vol_20d']:.2%}; future losses can exceed this measure.")
    bear.append("Price returns exclude dividends. Fund holdings, expenses and holdings-based valuation are not connected.")
    return {"symbol": symbol, "name": name, "asof": days[-1].isoformat(), "security_type": "ETF",
            "profile_version": "etf-market-v2",
            "coverage": {"stored_sessions": len(bars), "expected_sessions": len(days),
                         "first_session": days[0].isoformat(), "last_session": days[-1].isoformat()},
            "price": px, "fundamentals": {}, "options": opt, "desks": desks, "bull": bull, "bear": bear,
            "change_view": ["Compare trend, volatility and executable option spreads with your intended horizon."],
            "committee": None, "weights": {}, "scope": "Fund market profile; no company-fundamental committee"}
