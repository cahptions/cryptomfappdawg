"""How each PAPER strategy is doing, honestly: its equity against SPY and against the baseline it has to beat (random
stocks for stock strategies, random calls for option strategies), its closed trades, and a grade that waits for
enough evidence. Daily excess returns get a stationary-bootstrap interval and one-sided p-value; the strategies tested
together are Holm-adjusted."""

from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import date
from typing import Any

import numpy as np

from desk.stats.core import holm, max_drawdown
from desk.stocks import validation as val
from desk.stocks.paper.book import PaperBook, Position
from desk.stocks.paper.engine import sessions_between
from desk.stocks.paper.strategies import Strategy
from desk.stocks.repo import StocksMarket

MIN_SESSIONS = 63              # three months of daily equity before any grade
MIN_SESSIONS_PORTFOLIO = 126   # six months for monthly-rebalanced portfolios
MIN_TRADES = 20                # closed trades before an event or options strategy is graded
BASELINE_FOR = {"stock": "random_10", "option": "random_calls"}


def daily_returns(equity: Sequence[float]) -> np.ndarray:
    e = np.asarray(equity, dtype=float)
    if len(e) < 2:
        return np.array([])
    out: np.ndarray = e[1:] / e[:-1] - 1
    return out


def trade_stats(closed: Sequence[Position]) -> dict[str, Any]:
    rets = [(p.pnl or 0.0) / (p.entry_cost + p.entry_fees) for p in closed if p.entry_cost + p.entry_fees > 0]
    wins = [p.pnl for p in closed if (p.pnl or 0) > 0]
    losses = [-(p.pnl or 0) for p in closed if (p.pnl or 0) < 0]
    held = [sessions_between(p.entry_day, p.exit_day) for p in closed if p.exit_day]
    best = max(closed, key=lambda p: p.pnl or 0, default=None)
    worst = min(closed, key=lambda p: p.pnl or 0, default=None)
    return {"trades": len(closed), "win_rate": None if not closed else len(wins) / len(closed),
            "avg_return": None if not rets else float(np.mean(rets)),
            "median_return": None if not rets else float(np.median(rets)),
            "profit_factor": None if not losses else sum(w or 0 for w in wins) / sum(losses),
            "avg_sessions_held": None if not held else float(np.mean(held)),
            "best": None if best is None else {"symbol": best.symbol, "pnl": best.pnl},
            "worst": None if worst is None else {"symbol": worst.symbol, "pnl": worst.pnl},
            "total_pnl": sum(p.pnl or 0 for p in closed)}


def _spy_closes(market: StocksMarket, since: date) -> dict[date, float]:
    return {b.day: b.close for b in market.bars("SPY", since)}


def _aligned(a: dict[date, float], b: dict[date, float]) -> tuple[np.ndarray, np.ndarray]:
    days = sorted(set(a) & set(b))
    return daily_returns([a[d] for d in days]), daily_returns([b[d] for d in days])


def account_report(book: PaperBook, market: StocksMarket, st: Strategy, baseline: dict[date, float] | None,
                   spy: dict[date, float]) -> dict[str, Any]:
    rows = book.equity(st.id)
    equity = {d: e for d, _, _, e in rows}
    closed = book.positions(st.id, state="CLOSED")
    open_ = book.positions(st.id)
    start = book.deposits(st.id)
    last = rows[-1] if rows else None
    rets = daily_returns([e for _, _, _, e in rows])
    mine, spy_r = _aligned(equity, spy)
    recorded_specs = {r[0] for r in book.conn.execute(
        'SELECT DISTINCT spec_sha256 FROM stk_decisions WHERE account=?', (st.id,))}
    out: dict[str, Any] = {
        "id": st.id, "title": st.title, "asset": st.asset, "style": st.style, "hypothesis": st.hypothesis,
        "baseline": st.baseline, "learned": st.learned, "params": st.p, "spec_sha256": st.spec_sha256,
        "recorded_specifications": sorted(recorded_specs),
        "mixed_specifications": bool(recorded_specs - {st.spec_sha256}),
        "start": rows[0][0].isoformat() if rows else None, "sessions": len(rows), "deposits": start,
        "equity": None if last is None else last[3], "cash": None if last is None else last[1],
        "invested": None if last is None or not last[3] else last[2] / last[3],
        "return": None if last is None or not start else last[3] / start - 1,
        "spy_return": None if len(spy_r) == 0 else float(np.prod(1 + spy_r) - 1),
        "max_drawdown": None if len(rets) == 0 else max_drawdown(rets),
        "volatility": None if len(rets) < 20 else float(np.std(rets, ddof=1) * math.sqrt(252)),
        "sharpe": None if len(rets) < 20 or float(np.std(rets)) == 0 else float(np.mean(rets) / np.std(rets, ddof=1)
                                                                                * math.sqrt(252)),
        "open_positions": len(open_), **trade_stats(closed),
        "curve": [{"day": d.isoformat(), "equity": round(e, 2)} for d, _, _, e in rows][-400:],
    }
    vs_spy = mine - spy_r if len(mine) else np.array([])
    out["vs_spy"] = val.summarize(list(vs_spy), 1, 1) if len(vs_spy) else None
    if baseline is not None and not st.baseline:
        a, b = _aligned(equity, baseline)
        out["vs_baseline"] = val.summarize(list(a - b), 1, 1) if len(a) else None
    else:
        out["vs_baseline"] = None
    return out


def grade(acct: dict[str, Any]) -> dict[str, Any]:
    if acct["baseline"]:
        return {"grade": "info", "text": "A baseline: the bar the others must clear"}
    if acct.get('mixed_specifications'):
        return {'grade': 'early',
                'text': 'Rules changed: cumulative results mix specification versions; evaluate versions separately'}
    need = MIN_SESSIONS_PORTFOLIO if acct["style"] in ("rebalance", "hold") else MIN_SESSIONS
    if acct["sessions"] < need:
        return {"grade": "early", "text": f"Too early: {acct['sessions']} of {need} sessions"}
    if acct["style"] == "event" and acct["trades"] < MIN_TRADES:
        return {"grade": "early", "text": f"Too early: {acct['trades']} of {MIN_TRADES} closed trades"}
    stat = acct.get("vs_baseline") or acct.get("vs_spy") or {}
    p, ph = stat.get("p"), stat.get("p_holm")
    if ph is not None and ph < 0.05:
        return {"grade": "working", "text": "Beat its baseline, after correcting for every strategy tested"}
    if p is not None and p < 0.10:
        return {"grade": "promising", "text": "Ahead of its baseline, not yet beyond chance"}
    if p is not None and p > 0.95:
        return {"grade": "backwards", "text": "Behind its baseline by more than chance"}
    return {"grade": "not_working", "text": "No better than its baseline so far"}


def report(book: PaperBook, market: StocksMarket, strategies: Sequence[Strategy]) -> dict[str, Any]:
    first = min((rows[0][0] for s in strategies if (rows := book.equity(s.id))), default=None)
    spy = _spy_closes(market, first) if first else {}
    baselines = {asset: {d: e for d, _, _, e in book.equity(sid)} for asset, sid in BASELINE_FOR.items()}
    accounts = [account_report(book, market, s, baselines.get(s.asset), spy) for s in strategies]
    tested = [a["vs_baseline"] for a in accounts if a["vs_baseline"] and a["vs_baseline"].get("p") is not None]
    for stat, adjusted in zip(tested, holm([t["p"] for t in tested]), strict=True):
        stat["p_holm"] = adjusted
    for a in accounts:
        a["grade"] = grade(a)
    accounts.sort(key=lambda a: (a["baseline"], -(a["return"] if a["return"] is not None else -1e9)))
    return {"accounts": accounts, "first_day": first.isoformat() if first else None,
            "rules": {"min_sessions": MIN_SESSIONS, "min_sessions_portfolio": MIN_SESSIONS_PORTFOLIO,
                      "min_trades": MIN_TRADES, "baselines": BASELINE_FOR}}


__all__ = ["BASELINE_FOR", "account_report", "daily_returns", "grade", "report", "trade_stats"]
