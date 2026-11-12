"""The "My Schwab account" view: your real positions with KESTREL's own view of each, and your decisions graded.

Read only and research only. The positions come from the latest read (``desk.stocks.account``); KESTREL's view is
its committee score and verdict for a stock, and for an option the same odds and Plays-style exit plan a play gets
(take-profit, stop and leave-by, as stock prices too), computed from your own average price. Nothing here is an
instruction to trade; this app places no orders.

"Your decisions graded" pairs each closed real trade (first in, first out, from the stored fills) with a random
twin over the same days: for a stock, another stored stock picked at random (as Plays twins are picked on another
stock); for an option, SPY over the same days in the option's direction, since a same-delta option twin needs that
day's chains. The comparison is early until ``ENOUGH`` trades have closed.
"""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import statistics
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from desk.core.config import LoadedConfig
from desk.core.paths import DeskPaths
from desk.stocks import account as acct
from desk.stocks import calendar as cal
from desk.stocks import options as opt
from desk.stocks.repo import MARKET_FILE, RESEARCH_FILE, StocksMarket, day_of

ENOUGH = 20                     # closed trades before the comparison says more than "early"
DEFAULT_RATE = 0.04
STOCK_TYPES = ("EQUITY", "COLLECTIVE_INVESTMENT")
LABEL = ("Your real Schwab account, read only. KESTREL is a research and PAPER app: it cannot place, change or "
         "cancel an order, and nothing here is advice to trade.")


# ── KESTREL's view of a position ─────────────────────────────────────────────────────────────────
def _detail(research: sqlite3.Connection, symbol: str) -> dict[str, Any] | None:
    row = research.execute("SELECT detail_json FROM stk_scores_latest WHERE symbol = ?", (symbol,)).fetchone()
    try:
        value = json.loads(row[0]) if row else None
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def _committee(detail: dict[str, Any] | None, symbol: str) -> dict[str, Any]:
    c = (detail or {}).get("committee") or {}
    if c.get("score") is None:
        return {"score": None, "verdict": None, "asof": None,
                "text": f"KESTREL has no committee view of {symbol}: it is outside the research universe or not "
                        "scored yet."}
    return {"score": round(float(c["score"]), 1), "verdict": c.get("label"), "asof": detail.get("asof") if detail
            else None, "text": f"Committee {float(c['score']):.0f} of 100: {c.get('label') or 'no verdict'}"}


def _price(market: sqlite3.Connection, detail: dict[str, Any] | None, symbol: str, now: datetime
           ) -> tuple[float | None, str | None]:
    row = market.execute("SELECT quote_json FROM stk_stock_views WHERE symbol = ?", (symbol,)).fetchone()
    if row and row[0]:
        try:
            quote = json.loads(row[0])
            at = datetime.fromisoformat(quote["quoted_at"])
            if quote.get("last") and now - at < timedelta(days=1):
                return float(quote["last"]), f"Schwab quote {at.isoformat(timespec='minutes')}"
        except (ValueError, KeyError, TypeError):
            pass
    bar = market.execute("SELECT day, close FROM stk_bars WHERE symbol = ? ORDER BY day DESC LIMIT 1",
                         (symbol,)).fetchone()
    if bar:
        return float(bar[1]), f"close {date.fromordinal(bar[0]).isoformat()}"
    close = ((detail or {}).get("price") or {}).get("close")
    return (float(close), "research close") if close else (None, None)


def _rate(market: sqlite3.Connection, today: date) -> float:
    points = [p for p in StocksMarket(market).rates("UST3M", today - timedelta(days=30)) if p.day <= today]
    return points[-1].value / 100 if points else DEFAULT_RATE


def _option_view(p: acct.AccountPosition, detail: dict[str, Any] | None, s: float | None, rate: float,
                 today: date) -> dict[str, Any]:
    from desk.stocks.odds import forecast_sigma
    from desk.stocks.paper.fills import OPTION_FEE_PER_CONTRACT as FEE
    from desk.stocks.play_plan import exit_plan, plan_odds
    from desk.stocks.play_targets import chance

    facts = (((detail or {}).get("desks") or {}).get("options") or {}).get("facts") or {}
    out: dict[str, Any] = {"iv_rank": facts.get("iv_rank"), "iv30": facts.get("iv30") or
                           ((detail or {}).get("options") or {}).get("iv30")}
    if p.expiration is None or p.strike is None or p.put_call is None or not p.quantity:
        return {**out, "note": "Not a standard option KESTREL can value."}
    days = (p.expiration - today).days
    out["days_left"] = days
    if days < 0:
        return {**out, "note": "Past its expiration: Schwab has not yet removed it."}
    if s is None or p.average_price is None or p.market_value is None:
        return {**out, "note": f"No stored price for {p.stock}, so KESTREL cannot value this option yet."}
    kind = p.put_call.lower()
    long = p.quantity > 0
    mark = abs(p.market_value) / abs(p.quantity) / 100
    years = max(days, 1) / 365
    leg_iv = opt.implied_vol(kind, mark, s, p.strike, years, rate) if mark > 0 else None
    rv = ((detail or {}).get("price") or {}).get("vol_20d")
    sigma = forecast_sigma(leg_iv or out["iv30"], rv)
    out.update(mark=round(mark, 4), underlying_price=round(s, 2), implied_vol=leg_iv and round(leg_iv, 4))
    if not sigma:
        return {**out, "note": "No implied or realized volatility stored, so no odds."}
    avg = float(p.average_price)
    leg = {"occ": p.symbol, "side": "buy" if long else "sell", "kind": kind, "strike": p.strike,
           "iv": leg_iv or sigma}
    if long:
        structure = f"long_{kind}"
        play: dict[str, Any] = {"structure": structure, "action": "BUY", "entry": avg, "max_loss": avg * 100 + FEE,
                                "max_gain": None}
        current = (mark - avg) / avg if avg > 0 else None
    else:
        max_loss = (p.strike if kind == "put" else max(p.strike, s) * 3) * 100 - avg * 100 + FEE
        structure = "cash_secured_put" if kind == "put" else "short_call"
        play = {"structure": structure, "action": "SELL", "entry": avg, "max_loss": max_loss,
                "max_gain": avg * 100 - FEE}
        current = (avg - mark) * 100 / max_loss if max_loss > 0 else None
    play.update(legs=[leg], expiration=p.expiration.isoformat(), dte=max(days, 1), underlying_price=s,
                forecast={"sigma": sigma, "drift": 0.0})
    from_avg = chance(play, 0.0, sigma, 0.0, years)
    from_mark = chance({**play, "entry": mark}, 0.0, sigma, 0.0, years)
    play["profit_kestrel"] = from_avg
    out.update(chance_profit=round(from_avg, 4), chance_from_here=round(from_mark, 4),
               chance_basis="KESTREL's volatility forecast (implied and realized blended), no drift",
               current_return=None if current is None else round(current, 4), per="contract")
    if structure == "short_call":
        out["plan"] = None
        out["note"] = ("KESTREL has no exit plan for a sold call: whether it is covered depends on shares it cannot "
                       "tie to it. The odds are for this contract alone.")
        return out
    plan = exit_plan(play, "account", None, rate, today)
    plan["odds"] = plan_odds(play, plan, rate, today)
    tp, sl = plan.get("take_profit"), plan.get("stop")
    if current is not None and tp and current >= tp["return"]:
        state = f"Take-profit reached: {current:+.0%} against a {tp['return']:+.0%} target"
    elif current is not None and sl and current <= sl["return"]:
        state = f"Stop reached: {current:+.0%} against a {sl['return']:+.0%} limit"
    elif plan.get("leave_by") and today >= date.fromisoformat(plan["leave_by"]):
        state = f"Time exit: the plan leaves by {plan['leave_by']}"
    else:
        state = "Hold: no exit condition of the plan is met"
    out.update(plan=plan, plan_state=state)
    return out


def positions_view(research: sqlite3.Connection, market: sqlite3.Connection, now: datetime) -> list[dict[str, Any]]:
    today = cal.new_york(now).date()
    rate = _rate(market, today)
    rows = []
    for p in acct.account_positions(research):
        detail = _detail(research, p.stock)
        row: dict[str, Any] = {"account": f"…{p.account_last4}", "symbol": p.symbol, "stock": p.stock,
                               "asset_type": p.asset_type, "quantity": p.quantity, "average_price": p.average_price,
                               "market_value": p.market_value, "day_pnl": p.day_pnl, "day_pnl_pct": p.day_pnl_pct,
                               "put_call": p.put_call, "strike": p.strike,
                               "expiration": p.expiration.isoformat() if p.expiration else None,
                               "committee": _committee(detail, p.stock)}
        if p.asset_type == "OPTION":
            s, basis = _price(market, detail, p.stock, now)
            try:
                row["option"] = {**_option_view(p, detail, s, rate, today), "price_basis": basis}
            except (ValueError, ZeroDivisionError, OverflowError):
                row["option"] = {"note": "KESTREL could not value this option from the stored prices."}
        rows.append(row)
    return rows


# ── your decisions graded ────────────────────────────────────────────────────────────────────────
@dataclass
class _Lot:
    qty: float           # signed: + long, − short
    price: float
    fee_unit: float
    day: date


def closed_trades(research: sqlite3.Connection) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(closed round trips, lots still open) from the stored fills, first in first out per account and instrument.

    A closing fill with no recorded opening (opened before the first stored fill) is left out."""
    lots: dict[tuple[str, str], list[_Lot]] = defaultdict(list)
    meta: dict[tuple[str, str], dict[str, Any]] = {}
    closed = []
    for f in research.execute("SELECT * FROM stk_account_fills ORDER BY traded_at, activity_id, leg"):
        key = (f["account_key"], f["symbol"])
        meta[key] = {"symbol": f["symbol"], "asset_type": f["asset_type"], "underlying": f["underlying"],
                     "put_call": f["put_call"], "expiration": f["expiration"], "account": f"…{f['account_last4']}"}
        mult = 100 if f["asset_type"] == "OPTION" else 1
        qty, day = float(f["quantity"]), date.fromisoformat(f["trade_day"])
        fee_unit = float(f["fees"]) / abs(qty) if qty else 0.0
        book = lots[key]
        while qty and book and math.copysign(1, book[0].qty) != math.copysign(1, qty):
            lot = book[0]
            size = min(abs(lot.qty), abs(qty))
            long = lot.qty > 0
            gross = (float(f["price"]) - lot.price) * size * mult * (1 if long else -1)
            fees = (lot.fee_unit + fee_unit) * size
            basis = lot.price * size * mult
            closed.append({**meta[key], "side": "long" if long else "short", "opened": lot.day.isoformat(),
                           "closed": day.isoformat(), "quantity": size, "entry": lot.price,
                           "exit": float(f["price"]), "pnl": round(gross - fees, 2),
                           "return": round((gross - fees) / basis, 4) if basis > 0 else None})
            lot.qty += size if lot.qty < 0 else -size
            qty += size if qty < 0 else -size
            if abs(lot.qty) < 1e-9:
                book.pop(0)
        if qty and not (f["position_effect"] == "CLOSING" and not book):
            book.append(_Lot(qty, float(f["price"]), fee_unit, day))
    still = [{**meta[k], "quantity": sum(lot.qty for lot in v), "opened": v[0].day.isoformat()}
             for k, v in lots.items() if v]
    return closed, still


def _close_on(market: sqlite3.Connection, symbol: str, day: date) -> tuple[int, float] | None:
    row = market.execute("SELECT day, close FROM stk_bars WHERE symbol = ? AND day <= ? ORDER BY day DESC LIMIT 1",
                         (symbol, day_of(day))).fetchone()
    return (int(row[0]), float(row[1])) if row and row[1] else None


def _move(market: sqlite3.Connection, symbol: str, start: date, end: date) -> float | None:
    a, b = _close_on(market, symbol, start), _close_on(market, symbol, end)
    if not a or not b or b[0] <= a[0] or b[0] < day_of(end) - 5:      # no bar near the closing day yet
        return None
    split = market.execute("SELECT 1 FROM stk_splits WHERE symbol = ? AND execution_date > ? AND execution_date <= ?",
                           (symbol, a[0], b[0])).fetchone()
    return None if split else b[1] / a[1] - 1


def _twin_stock(market: sqlite3.Connection, trade: dict[str, Any], seed: str) -> tuple[str, float] | None:
    """Another stored stock at random (fixed by the trade), with closes on both days and no split between."""
    start, end = date.fromisoformat(trade["opened"]), date.fromisoformat(trade["closed"])
    a, b = _close_on(market, "SPY", start), _close_on(market, "SPY", end)
    if not a or not b or b[0] <= a[0]:
        return None
    rows = market.execute("SELECT x.symbol, x.close, y.close FROM stk_bars x JOIN stk_bars y ON y.symbol = x.symbol "
                          "AND y.day = ? JOIN stk_listings l ON l.symbol = x.symbol AND l.etf = 0 AND l.test = 0 "
                          "WHERE x.day = ? AND x.symbol != ? AND x.close > 0 ORDER BY x.symbol",
                          (b[0], a[0], trade["symbol"])).fetchall()
    order = sorted(rows, key=lambda r: hashlib.sha256(f"{seed}|{r[0]}".encode()).hexdigest())
    for symbol, first, last in order[:20]:
        if market.execute("SELECT 1 FROM stk_splits WHERE symbol = ? AND execution_date > ? AND execution_date <= ?",
                          (symbol, a[0], b[0])).fetchone() is None:
            return symbol, last / first - 1
    return None


def graded(research: sqlite3.Connection, market: sqlite3.Connection, now: datetime) -> dict[str, Any]:
    today = cal.new_york(now).date()
    closed, still = closed_trades(research)
    rows = []
    for n, t in enumerate(closed):
        start, end = date.fromisoformat(t["opened"]), date.fromisoformat(t["closed"])
        option = t["asset_type"] == "OPTION"
        bullish = (t["side"] == "long") != (option and t["put_call"] == "PUT")
        sign = 1 if bullish else -1
        spy = _move(market, "SPY", start, end)
        twin: dict[str, Any] | None = None
        if option or end <= start:
            if spy is not None:
                twin = {"symbol": "SPY", "return": round(sign * spy, 4),
                        "basis": "SPY over the same days, in the option's direction"}
        else:
            found = _twin_stock(market, t, f"{t['account']}|{t['symbol']}|{t['opened']}|{t['closed']}|{n}")
            if found:
                twin = {"symbol": found[0], "return": round(sign * found[1], 4),
                        "basis": "a random other stock over the same days"}
            elif spy is not None:
                twin = {"symbol": "SPY", "return": round(sign * spy, 4), "basis": "SPY over the same days"}
        beat = (None if twin is None or t["return"] is None else t["return"] > twin["return"])
        rows.append({**t, "days": (end - start).days, "twin": twin,
                     "spy": None if spy is None else round(sign * spy, 4), "beat_twin": beat})
    scored = [r for r in rows if r["beat_twin"] is not None]
    expired = [s for s in still if s.get("expiration") and date.fromisoformat(s["expiration"]) < today]
    summary: dict[str, Any] = {"closed": len(rows), "compared": len(scored), "enough": ENOUGH,
                               "strength": "early" if len(scored) < ENOUGH else "indicative",
                               "not_graded_expired": len(expired)}
    if scored:
        mine = [r["return"] for r in scored]
        twins = [r["twin"]["return"] for r in scored]
        summary.update(average=round(statistics.fmean(mine), 4), twin_average=round(statistics.fmean(twins), 4),
                       beat_share=round(sum(r["beat_twin"] for r in scored) / len(scored), 4),
                       pnl=round(sum(r["pnl"] for r in scored), 2))
    if len(scored) < ENOUGH:
        summary["text"] = (f"Early: {len(scored)} closed trade{'s' if len(scored) != 1 else ''} compared, "
                           f"{ENOUGH} needed before the comparison means much. A handful of trades is mostly luck.")
    else:
        summary["text"] = (f"{len(scored)} closed trades compared with their random twins: "
                           f"{summary['beat_share']:.0%} beat theirs. Indicative, not proof of skill.")
    if expired:
        summary["expired_text"] = (f"{len(expired)} option position{'s' if len(expired) != 1 else ''} reached "
                                   "expiration without a closing trade (expired, exercised or assigned are not trade "
                                   "records): not graded.")
    rows.sort(key=lambda r: (r["closed"], r["symbol"]), reverse=True)
    return {"summary": summary, "trades": rows[:100]}


# ── the whole view ───────────────────────────────────────────────────────────────────────────────
def build(paths: DeskPaths, now: datetime, reader: Callable[[DeskPaths, str], Any]) -> dict[str, Any]:
    on = acct.enabled(paths)
    out: dict[str, Any] = {"enabled": on, "label": LABEL, "positions": [], "graded": None, "state": None,
                           "taken_at": None, "accounts": []}
    with reader(paths, RESEARCH_FILE) as research, reader(paths, MARKET_FILE) as market:
        try:
            last = acct.last_pull(research)
            ok = research.execute("SELECT at FROM stk_account_pulls WHERE outcome = 'ok' ORDER BY id DESC "
                                  "LIMIT 1").fetchone()
        except sqlite3.OperationalError:          # a database from before migration 0016, until the next open
            return out
        if last is not None:
            out["state"] = {"outcome": last["outcome"], "detail": last["detail"], "at": last["at"],
                            "slot": last["slot"]}
        if ok is not None:
            out["taken_at"] = ok[0]
            out["positions"] = positions_view(research, market, now)
            out["accounts"] = sorted({r["account"] for r in out["positions"]})
        out["graded"] = graded(research, market, now)
    slot = acct.current_slot(now)
    out["schedule"] = ("Market days at 09:45, 12:30 and 15:30 New York time and once after the close; trades after "
                       "the close." + (f" Latest slot: {slot[0]}." if slot else ""))
    return out


class AccountSwitch(BaseModel):
    read_account: bool


def install(app: FastAPI, paths: DeskPaths, loaded: LoadedConfig, now: Callable[[], datetime],
            reader: Callable[[DeskPaths, str], Any]) -> None:
    """The account view's three routes: read the view, flip the opt-in switch, read the account now."""

    @app.get("/api/stocks/account")
    def account_page() -> dict[str, Any]:
        return build(paths, now(), reader)

    @app.post("/api/stocks/account/settings")
    def account_switch(inputs: AccountSwitch) -> dict[str, Any]:
        acct.set_enabled(paths, inputs.read_account)
        return build(paths, now(), reader)

    @app.post("/api/stocks/account/refresh")
    async def account_refresh() -> dict[str, Any]:
        if not acct.enabled(paths):
            raise HTTPException(400, "Turn on Read my Schwab account (read-only) in Settings first")
        result = await acct.cycle(paths, loaded, now(), manual=True)
        return {**build(paths, now(), reader), "read": result}


__all__ = ["ENOUGH", "LABEL", "build", "closed_trades", "graded", "install", "positions_view"]
