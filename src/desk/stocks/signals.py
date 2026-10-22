"""Non-price signals per stock at one day, each built only from what was public before that day."""

from __future__ import annotations

import json
import math
import statistics
from collections import defaultdict
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from itertools import pairwise
from typing import Any

from desk.stocks import calendar as cal
from desk.stocks.prices import Panel
from desk.stocks.repo import StocksMarket, date_of, day_of

SHORT_PUBLICATION_LAG = 12          # calendar days after settlement before FINRA's numbers are reliably public
ITEM_WORDS = {
    "1.01": "signed a material agreement", "1.02": "ended a material agreement", "1.03": "filed for bankruptcy",
    "2.01": "completed an acquisition or sale", "2.02": "reported results", "2.03": "took on a material debt obligation",
    "2.04": "had debt accelerated", "2.05": "announced restructuring costs", "2.06": "took a material impairment",
    "3.01": "received a delisting notice", "3.02": "sold unregistered shares", "3.03": "changed shareholder rights",
    "4.01": "changed auditors", "4.02": "said past financial statements can no longer be relied on",
    "5.01": "had a change in control", "5.02": "changed an executive or director", "5.03": "amended its bylaws",
    "5.07": "reported shareholder vote results", "7.01": "made a public disclosure (Reg FD)", "8.01": "reported another event",
}
RED_FLAG_ITEMS = {"1.03", "2.04", "2.06", "3.01", "4.01", "4.02"}
RED_FLAG_FORMS = {"NT 10-K": "filed its annual report late (NT 10-K)", "NT 10-Q": "filed its quarterly report late (NT 10-Q)",
                  "25-NSE": "is being removed from its exchange (Form 25)", "15-12B": "is deregistering its shares (Form 15)"}
DILUTION_FORMS = ("S-1", "S-1/A", "S-3", "S-3ASR", "424B1", "424B2", "424B3", "424B4", "424B5")


# ── insiders ─────────────────────────────────────────────────────────────────────────────────────
def insiders(market: StocksMarket, asof: date, *, days: int = 90) -> dict[str, dict[str, Any]]:
    """Open-market purchases (P) and sales (S) by officers, directors and 10% owners, filed before ``asof``."""
    rows = market.insider_trades(asof - timedelta(days=days), asof - timedelta(days=1))
    out: dict[str, dict[str, Any]] = defaultdict(lambda: {"buy_value": 0.0, "sell_value": 0.0, "planned_sell_value": 0.0,
                                                           "buyers": set(), "sellers": set(), "officer_buy_value": 0.0,
                                                           "buy_days": [], "recent": []})
    for r in rows:
        if r["code"] not in ("P", "S") or r["price"] is None or r["price"] <= 0:
            continue
        s = out[r["symbol"]]
        value = r["shares"] * r["price"]
        insider = bool(r["director"] or r["officer"])
        if r["code"] == "P" and r["acquired"]:
            s["buy_value"] += value
            s["buyers"].add(r["owner_cik"])
            s["buy_days"].append((r["day"], r["owner_cik"]))
            if r["officer"]:
                s["officer_buy_value"] += value
        elif r["code"] == "S" and not r["acquired"]:
            if r["planned"]:
                s["planned_sell_value"] += value
            else:
                s["sell_value"] += value
                if insider:
                    s["sellers"].add(r["owner_cik"])
        s["recent"].append({"day": date_of(r["day"]).isoformat(), "filed": date_of(r["filed"]).isoformat(),
                            "owner": r["owner"], "title": r["title"] or ("Director" if r["director"] else
                                                                         "10% owner" if r["ten_percent"] else "Officer"),
                            "code": r["code"], "shares": r["shares"], "price": r["price"], "value": value,
                            "planned": bool(r["planned"])})
    final: dict[str, dict[str, Any]] = {}
    for symbol, s in out.items():
        cluster = 0
        days_sorted = sorted(s["buy_days"])
        for i, (d0, _) in enumerate(days_sorted):
            window = {owner for d, owner in days_sorted[i:] if d - d0 <= 30}
            cluster = max(cluster, len(window))
        final[symbol] = {"buy_value": s["buy_value"], "sell_value": s["sell_value"],
                         "planned_sell_value": s["planned_sell_value"], "buyers": len(s["buyers"]),
                         "sellers": len(s["sellers"]), "cluster": cluster, "officer_buy_value": s["officer_buy_value"],
                         "recent": sorted(s["recent"], key=lambda x: x["filed"], reverse=True)[:12]}
    return final


# ── filings, earnings and events ─────────────────────────────────────────────────────────────────
def _session_after(accepted: datetime | None, filed: date) -> date:
    """The first session whose close reflects a filing: the filing day if accepted before 16:00 New York, else the
    next trading day."""
    if accepted is not None:
        local = cal.new_york(accepted)
        d = local.date()
        if cal.is_trading_day(d) and local.hour < 16:
            return d
        return cal.next_trading_day(d)
    return filed if cal.is_trading_day(filed) else cal.next_trading_day(filed)


def events(market: StocksMarket, asof: date, ciks: dict[str, int], panel: Panel,
           benchmark: str = "SPY") -> dict[str, dict[str, Any]]:
    """Earnings releases (8-K 2.02) with the price reaction against the benchmark and the drift since, the next
    report window, material events in plain words, red flags and dilution filings."""
    by_cik: dict[int, list[Any]] = defaultdict(list)
    for r in market.filings(since=asof - timedelta(days=500), until=asof - timedelta(days=1)):
        by_cik[r["cik"]].append(r)
    bench = panel.series(benchmark)
    out: dict[str, dict[str, Any]] = {}
    for symbol, cik in ciks.items():
        rows = by_cik.get(cik, [])
        info: dict[str, Any] = {"earnings": [], "material": [], "red_flags": [], "dilution_filings": 0, "next_window": None}
        closes = panel.series(symbol)
        for r in rows:
            filed = date_of(r["filed"])
            items = [i for i in (r["items"] or "").split(",") if i]
            accepted = datetime.fromisoformat(r["accepted_at"]) if r["accepted_at"] else None
            if r["form"] in ("8-K", "8-K/A") and "2.02" in items:
                session = _session_after(accepted, filed)
                info["earnings"].append(_reaction(panel, closes, bench, session, asof) | {"filed": filed.isoformat()})
            if r["form"] in ("8-K", "8-K/A") and (asof - filed).days <= 45:
                words = [ITEM_WORDS[i] for i in items if i in ITEM_WORDS and i not in ("9.01", "2.02")]
                if words:
                    info["material"].append({"filed": filed.isoformat(), "items": items, "text": "; ".join(words)})
            if (asof - filed).days <= 365:
                flags = [ITEM_WORDS[i] for i in items if i in RED_FLAG_ITEMS]
                if r["form"] in RED_FLAG_FORMS:
                    flags.append(RED_FLAG_FORMS[r["form"]])
                for flag in flags:
                    info["red_flags"].append({"filed": filed.isoformat(), "text": flag})
            if r["form"] in DILUTION_FORMS and (asof - filed).days <= 90:
                info["dilution_filings"] += 1
        releases = sorted({e["filed"] for e in info["earnings"]})
        if len(releases) >= 2:
            spans = [(date.fromisoformat(b) - date.fromisoformat(a)).days for a, b in pairwise(releases)]
            gaps = [g for g in spans if 50 <= g <= 130]
            if gaps:
                last = date.fromisoformat(releases[-1])
                center = last + timedelta(days=int(statistics.median(gaps)))
                info["next_window"] = [(center - timedelta(days=7)).isoformat(), (center + timedelta(days=7)).isoformat()]
        info["earnings"] = sorted(info["earnings"], key=lambda e: e["filed"])[-6:]
        info["material"] = sorted(info["material"], key=lambda m: m["filed"], reverse=True)[:8]
        out[symbol] = info
    return out


def _reaction(panel: Panel, closes: Any, bench: Any, session: date, asof: date) -> dict[str, Any]:
    j = panel.day_index(session)
    end = panel.day_index(asof)
    if j < 1 or panel.days[j] != session or j > end:
        return {"session": session.isoformat(), "reaction": None, "abnormal": None, "drift": None}
    def r(series: Any, a: int, b: int) -> float | None:
        x, y = series[a], series[b]
        return None if math.isnan(x) or math.isnan(y) or x == 0 else float(y / x - 1)
    react, bench_react = r(closes, j - 1, j), r(bench, j - 1, j)
    drift, bench_drift = r(closes, j, end), r(bench, j, end)
    return {"session": session.isoformat(), "reaction": react,
            "abnormal": None if react is None or bench_react is None else react - bench_react,
            "drift": None if drift is None or bench_drift is None or end == j else drift - bench_drift}


# ── shorts ───────────────────────────────────────────────────────────────────────────────────────
def shorts(market: StocksMarket, asof: date, shares: dict[str, float | None], volume_20d: dict[str, float | None]
           ) -> dict[str, dict[str, Any]]:
    published_by = asof - timedelta(days=SHORT_PUBLICATION_LAG)
    latest: dict[str, Any] = {}
    previous: dict[str, Any] = {}
    for r in market.short_interest():
        if date_of(r["settlement"]) > published_by:
            continue
        if r["symbol"] in latest:
            previous[r["symbol"]] = latest[r["symbol"]]
        latest[r["symbol"]] = r
    sv: dict[str, list[float]] = defaultdict(list)
    for r in market.conn.execute("SELECT symbol, short_volume, total FROM stk_short_volume WHERE day >= ? AND day < ?",
                                 (day_of(asof - timedelta(days=30)), day_of(asof))):
        if r["total"]:
            sv[r["symbol"]].append(r["short_volume"] / r["total"])
    fails_rows: dict[str, float] = defaultdict(float)
    for f in market.conn.execute("SELECT symbol, quantity FROM stk_fails WHERE settlement >= ? AND settlement < ?",
                                 (day_of(asof - timedelta(days=45)), day_of(asof - timedelta(days=14)))):
        fails_rows[f["symbol"]] += f["quantity"]
    out: dict[str, dict[str, Any]] = {}
    for symbol in set(latest) | set(sv):
        rec = latest.get(symbol)
        p = previous.get(symbol)
        n_shares = shares.get(symbol)
        si = None if rec is None else rec["short_shares"]
        out[symbol] = {
            "settlement": None if rec is None else date_of(rec["settlement"]).isoformat(),
            "short_shares": si, "short_pct": None if si is None or not n_shares else si / n_shares,
            "days_to_cover": None if rec is None else rec["days_to_cover"],
            "change": None if rec is None or p is None or not p["short_shares"] or not si else si / p["short_shares"] - 1,
            "short_volume_ratio": None if not sv.get(symbol) else sum(sv[symbol]) / len(sv[symbol]),
            "fails": fails_rows.get(symbol, 0.0),
            "fails_to_volume": None if not volume_20d.get(symbol) else fails_rows.get(symbol, 0.0) / (volume_20d[symbol] or 1),
        }
    return out


# ── crowd ────────────────────────────────────────────────────────────────────────────────────────
def crowd(market: StocksMarket, asof_end: datetime) -> dict[str, dict[str, Any]]:
    """The newest ApeWisdom snapshot at or before ``asof_end`` and the seven days before it."""
    hour = int(asof_end.timestamp() // 3600)
    out: dict[str, dict[str, Any]] = {}
    for source, key in (("wallstreetbets", "wsb"), ("all-stocks", "all")):
        h, rows = market.latest_mentions(source, before_hour=hour)
        if h is None:
            continue
        week: dict[str, list[tuple[int, int]]] = {r["symbol"]: [] for r in rows}
        for r in market.conn.execute("SELECT symbol, hour, mentions FROM stk_mentions WHERE source = ? AND hour > ?"
                                     " AND hour <= ?", (source, h - 24 * 14, h)):
            week.setdefault(r["symbol"], []).append((r["hour"], r["mentions"]))
        for r in rows:
            entry = out.setdefault(r["symbol"], {})
            hist = week.get(r["symbol"], [])
            last7 = [m for hh, m in hist if hh > h - 24 * 7]
            prior7 = [m for hh, m in hist if h - 24 * 14 < hh <= h - 24 * 7]
            entry[key] = {"rank": r["rank"], "mentions": r["mentions"], "upvotes": r["upvotes"],
                          "mentions_24h_ago": r["mentions_24h"], "rank_24h_ago": r["rank_24h"],
                          "velocity": (r["mentions"] / r["mentions_24h"]) if r["mentions_24h"] else None,
                          "avg_7d": sum(last7) / len(last7) if last7 else None,
                          "avg_prior_7d": sum(prior7) / len(prior7) if prior7 else None, "observed_hour": h}
    return out


# ── options ──────────────────────────────────────────────────────────────────────────────────────
def options(market: StocksMarket, asof: date, *, observed_by: datetime | None = None,
            symbol: str | None = None) -> dict[str, dict[str, Any]]:
    """Each optioned stock's newest summary at or before ``asof`` with its IV30 history for rank and percentile."""
    out: dict[str, dict[str, Any]] = {}
    rows = market.conn.execute(
        "SELECT underlying, day, summary_json, observed_at FROM stk_option_daily WHERE day <= ? AND day >= ? "
        "AND observed_at<=? ORDER BY day",
        (day_of(asof), day_of(asof - timedelta(days=400)),
         observed_by.astimezone(UTC).isoformat().replace("+00:00", "Z") if observed_by else "9999"))
    if symbol is not None:
        rows = market.conn.execute(
            "SELECT underlying,day,summary_json,observed_at FROM stk_option_daily WHERE underlying=? "
            "AND day<=? AND day>=? AND observed_at<=? ORDER BY day", (symbol, day_of(asof),
             day_of(asof-timedelta(days=400)),
             observed_by.astimezone(UTC).isoformat().replace("+00:00", "Z") if observed_by else "9999"))
    history: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        summary = json.loads(r["summary_json"])
        if summary.get("iv30"):
            history[r["underlying"]].append(summary["iv30"])
        out[r["underlying"]] = {**summary, "day": date_of(r["day"]).isoformat(), "observed_at": r["observed_at"]}
    for symbol, summary in out.items():
        summary["iv_history"] = history.get(symbol, [])
        if not summary.get("activity") and summary["day"] == asof.isoformat():
            from desk.stocks.activity import chain_activity

            _, chains = market.option_chain(symbol, asof, observed_by=observed_by)
            summary["activity"] = chain_activity([q for qs in chains.values() for q in qs],
                                                  datetime.fromisoformat(summary["observed_at"]))
    return out


# ── macro ────────────────────────────────────────────────────────────────────────────────────────
def macro(market: StocksMarket, asof: date, spy_above_200: float | None) -> dict[str, Any]:
    def last(series: str, before: date) -> tuple[date, float] | None:
        rows = [p for p in market.rates(series, before - timedelta(days=30)) if p.day < before]
        return (rows[-1].day, rows[-1].value) if rows else None

    def change(series: str, days: int) -> float | None:
        now_v, then_v = last(series, asof), last(series, asof - timedelta(days=days))
        return None if now_v is None or then_v is None else now_v[1] - then_v[1]

    ten, two, hy, vix = last("UST10Y", asof), last("UST2Y", asof), last("BAMLH0A0HYM2", asof), last("VIXCLS", asof)
    hy_change = change("BAMLH0A0HYM2", 91)
    risk_off = (spy_above_200 is not None and spy_above_200 < 0) or (hy_change is not None and hy_change > 0.75) or (
        vix is not None and vix[1] > 30)
    return {"ust10y": None if ten is None else ten[1], "ust2y": None if two is None else two[1],
            "curve_10y_2y": None if ten is None or two is None else ten[1] - two[1], "ust10y_change_3m": change("UST10Y", 91),
            "hy_spread": None if hy is None else hy[1], "hy_change_3m": hy_change, "vix": None if vix is None else vix[1],
            "spy_above_200": spy_above_200, "regime": "RISK_OFF" if risk_off else "RISK_ON",
            "rates_as_of": None if ten is None else ten[0].isoformat()}


def sector_strength(values: Sequence[float], sectors: Sequence[str]) -> dict[str, float]:
    by: dict[str, list[float]] = defaultdict(list)
    for v, s in zip(values, sectors, strict=True):
        if v is not None and not math.isnan(v):
            by[s].append(v)
    return {s: statistics.median(v) for s, v in by.items() if len(v) >= 5}


__all__ = ["DILUTION_FORMS", "ITEM_WORDS", "RED_FLAG_FORMS", "RED_FLAG_ITEMS", "crowd", "events", "insiders", "macro",
           "options", "sector_strength", "shorts"]
