"""The PAPER engine. Once a session's prices, option chains and analysis are stored, ``step`` runs every strategy's
account through that session, in the order a real account would live it:

1. fill yesterday's orders: exits first, then entries (stocks at today's open, options at today's chain snapshot);
2. corporate actions on held stocks (splits change the share count; cash dividends are paid on the ex-date);
3. resting stops that today's range touched;
4. options that expired, settled at intrinsic value on the close;
5. mark everything at today's close (stocks) and mids (options), and raise trailing stops;
6. decide exits (time, take-profit, stop, days to expiry, results passed, rebalance) and entries from today's
   analysis, as orders for the next session — nothing decided on a close fills at that close;
7. record each account's equity for the day (once; a day is never stepped twice).
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from desk.core.config import StocksSection
from desk.stocks import calendar as cal
from desk.stocks import options as opt
from desk.stocks.analysis import Day
from desk.stocks.contracts import Bar, OptionQuote
from desk.stocks.paper import fills
from desk.stocks.paper.book import Order, PaperBook, Position
from desk.stocks.paper.strategies import STRATEGIES, Candidate, Strategy, build_structure, candidates
from desk.stocks.repo import StocksMarket

STALE_STOCK_SESSIONS = 5           # an order that can't fill for this many sessions is cancelled
STALE_OPTION_SESSIONS = 3
CASH_BUFFER = 0.995                # keep half a percent in cash for fees and rounding
DEFAULT_RATE = 0.04


@dataclass
class StepReport:
    asof: date
    skipped: str | None = None
    filled: int = 0
    cancelled: int = 0
    opened: int = 0
    closed: int = 0
    orders: int = 0
    notes: list[str] = field(default_factory=list)
    equity: dict[str, float] = field(default_factory=dict)

    def as_json(self) -> dict[str, Any]:
        return {"asof": self.asof.isoformat(), "skipped": self.skipped, "filled": self.filled, "cancelled": self.cancelled,
                "opened": self.opened, "closed": self.closed, "orders": self.orders, "notes": self.notes[:50],
                "equity": self.equity}


def sessions_between(a: date, b: date) -> int:
    """Trading sessions after ``a`` up to and including ``b``."""
    return 0 if b <= a else len(cal.trading_days(a, b)) - (1 if cal.is_trading_day(a) else 0)


class PaperEngine:
    def __init__(self, market: StocksMarket, book: PaperBook, settings: StocksSection,
                 strategies: tuple[Strategy, ...] = STRATEGIES, retired: set[str] | frozenset[str] = frozenset()) -> None:
        self.market = market
        self.book = book
        self.settings = settings
        self.strategies = {s.id: s for s in strategies}
        self.retired = set(retired)          # still managed and marked, but never opens a position again
        self._chains: dict[str, tuple[date | None, dict[date, list[OptionQuote]]]] = {}
        self._splits: dict[str, list[tuple[date, float]]] = {}
        self.rate = DEFAULT_RATE

    # ── data ───────────────────────────────────────────────────────────────────────────────────────
    def _bars(self, asof: date) -> dict[str, Bar]:
        return {r[0]: Bar(r[0], asof, r[2], r[3], r[4], r[5], r[6]) for r in self.market.bar_rows(asof, asof)}

    def _rate(self, asof: date) -> float:
        points = [p for p in self.market.rates("UST3M") if p.day <= asof]
        return points[-1].value / 100 if points else DEFAULT_RATE

    def _chain_today(self, symbol: str, asof: date) -> dict[str, OptionQuote] | None:
        if symbol not in self._chains:
            self._chains[symbol] = self.market.option_chain(symbol, asof)
        day, chains = self._chains[symbol]
        return fills.index_chain(chains) if day == asof else None

    def _chain_full(self, symbol: str, asof: date) -> dict[date, list[OptionQuote]] | None:
        self._chain_today(symbol, asof)
        day, chains = self._chains[symbol]
        return chains if day == asof else None

    def shares(self, p: Position, on: date) -> float:
        factor = math.prod(ratio for d, ratio in self._splits.get(p.symbol, []) if p.entry_day < d <= on)
        return float(p.legs[0]["quantity"]) * factor

    def _dv(self, symbol: str, day: Day | None, bars: dict[str, Bar]) -> float | None:
        if day is not None:
            v = day.ctx.price.get(symbol, {}).get("dollar_volume_20d")
            if v is not None:
                return float(v)
        b = bars.get(symbol)
        return None if b is None else b.close * b.volume

    # ── the session ────────────────────────────────────────────────────────────────────────────────
    def step(self, asof: date, now: datetime, day: Day | None, *, allow_entries: bool = True) -> StepReport:
        with self.book.atomic():
            return self._step(asof, now, day, allow_entries=allow_entries)

    def _step(self, asof: date, now: datetime, day: Day | None, *, allow_entries: bool) -> StepReport:
        rep = StepReport(asof)
        if self.book.stepped(asof):
            rep.skipped = f"{asof.isoformat()} was already stepped"
            return rep
        last = self.book.last_step()
        if last is not None and asof < last:
            rep.skipped = f"{asof.isoformat()} is before the last step ({last.isoformat()})"
            return rep
        bars = self._bars(asof)
        if "SPY" not in bars:
            rep.skipped = f"no prices stored for {asof.isoformat()}"
            return rep
        self.rate = self._rate(asof)
        self._chains = {}
        self._splits = defaultdict(list)
        for sp in self.market.splits():
            self._splits[sp.symbol].append((sp.execution_date, sp.ratio))
        for st in self.strategies.values():
            self.market.register_spec(f"stocks_paper_{st.id}", st.version, st.content(), now)
            capital = self.settings.paper_option_capital_usd if st.asset == "option" else self.settings.paper_starting_capital_usd
            self.book.open_account(st.id, float(capital), now, asof)
        if not allow_entries:
            for order in self.book.pending():
                if order.order["action"] == "ENTER":
                    self.book.resolve(order.id, "CANCELLED", now, "STOP: new entries disabled")
                    rep.cancelled += 1
        self._fill(asof, now, bars, day, rep)
        self._corporate_actions(asof, now, last, rep)
        self._stops(asof, now, bars, day, rep)
        self._expiries(asof, now, bars, rep)
        values = self._mark(asof, bars)
        pending = self.book.pending()
        for st in self.strategies.values():
            self._exits(st, asof, now, day, pending, rep)
            if allow_entries and day is not None and day.asof == asof and st.id not in self.retired:
                self._entries(st, asof, now, bars, day, values, pending, rep)
        for st in self.strategies.values():
            cash = self.book.cash(st.id)
            held = values.get(st.id, 0.0)
            self.book.snapshot(asof, st.id, now, cash, held)
            rep.equity[st.id] = round(cash + held, 2)
        return rep

    # 1. fills ─────────────────────────────────────────────────────────────────────────────────────
    def _fill(self, asof: date, now: datetime, bars: dict[str, Bar], day: Day | None, rep: StepReport) -> None:
        orders = [o for o in self.book.pending() if o.fill_after < asof and o.account in self.strategies]
        for o in sorted(orders, key=lambda o: (o.order["action"] != "EXIT", o.id)):
            waited = sessions_between(o.fill_after, asof)
            stock = o.order["asset"] == "stock"
            if stock:
                opening = datetime.combine(asof, datetime.min.time().replace(hour=9, minute=30), cal.NEW_YORK)
                if datetime.fromisoformat(o.created_at) >= opening:
                    rep.notes.append("An order created after this session opened waits for a future opening")
                    continue
            else:
                captured = self.market.conn.execute(
                    "SELECT MIN(observed_at) FROM stk_option_chains WHERE underlying=? AND day=?",
                    (o.order["symbol"], asof.toordinal())).fetchone()[0]
                if captured is not None and datetime.fromisoformat(captured) <= datetime.fromisoformat(o.created_at):
                    continue
            try:
                done = (self._fill_stock if stock else self._fill_option)(o, asof, now, bars, day, rep)
            except _Cancel as c:
                self.book.resolve(o.id, "CANCELLED", now, str(c))
                rep.cancelled += 1
                rep.notes.append(f"{o.account} {o.order['symbol']}: cancelled — {c}")
                continue
            if done:
                self.book.resolve(o.id, "FILLED", now)
                rep.filled += 1
            elif waited >= (STALE_STOCK_SESSIONS if stock else STALE_OPTION_SESSIONS):
                self.book.resolve(o.id, "CANCELLED", now, f"no fillable data for {waited} sessions")
                rep.cancelled += 1

    def _position(self, pid: str) -> Position | None:
        return next((p for p in self.book.positions() if p.id == pid), None)

    def _fill_stock(self, o: Order, asof: date, now: datetime, bars: dict[str, Bar], day: Day | None,
                    rep: StepReport) -> bool:
        spec = o.order
        bar = bars.get(spec["symbol"])
        if bar is None:
            return False
        dv = self._dv(spec["symbol"], day, bars)
        if spec["action"] == "EXIT":
            p = self._position(spec["position_id"])
            if p is None:
                raise _Cancel("the position is already closed")
            f = fills.sell_at(bar.open, self.shares(p, asof), dv, "next open")
            self.book.close_position(p, at=now, day=asof, credit=f.cost, fees=f.fees, reason=spec["reason"],
                                     evidence={"fill_price": f.price, "order": o.id})
            rep.closed += 1
            return True
        cash = self.book.cash(o.account)
        notional = min(float(spec["notional"]), cash * CASH_BUFFER)
        if notional < float(spec["notional"]) * 0.25:
            raise _Cancel(f"not enough cash (${cash:,.2f})")
        bought = fills.buy_at_open(bar, notional, dv)
        if bought is None:
            raise _Cancel("the price is above the order's size")
        f = bought
        plan = spec["plan"]
        stop = (f.price * (1 - plan["stop"]) if plan.get("stop") else
                f.price * (1 - plan["trail"]) if plan.get("trail") else None)
        st = self.strategies[o.account]
        self.book.open_position(account=o.account, spec_sha256=st.spec_sha256, asset="stock", symbol=spec["symbol"],
                                structure="long_stock", legs=[{"kind": "stock", "symbol": spec["symbol"],
                                                               "quantity": f.shares, "entry_price": f.price}],
                                decision_id=o.decision_id, at=now, day=asof, cost=f.cost, fees=f.fees, max_loss=None,
                                stop_price=stop, expires=None, plan=plan)
        rep.opened += 1
        return True

    def _fill_option(self, o: Order, asof: date, now: datetime, bars: dict[str, Bar], day: Day | None,
                     rep: StepReport) -> bool:
        spec = o.order
        symbol = spec["symbol"]
        quotes = self._chain_today(symbol, asof)
        bar = bars.get(symbol)
        if spec["action"] == "EXIT":
            p = self._position(spec["position_id"])
            if p is None:
                raise _Cancel("the position is already closed")
            stale = sessions_between(o.fill_after, asof) >= STALE_OPTION_SESSIONS
            if quotes is None and not stale:
                return False
            f = fills.close_legs(self._with_marks(p), quotes or {}, None if bar is None else bar.close, asof, self.rate)
            if f.problem:
                if not stale:
                    return False
                raise _Cancel(f.problem)
            self.book.close_position(p, at=now, day=asof, credit=-f.debit, fees=f.fees, reason=spec["reason"],
                                     evidence={"legs": f.legs, "order": o.id, "quoted": quotes is not None})
            rep.closed += 1
            return True
        if quotes is None or bar is None:
            return False
        created = datetime.fromisoformat(o.created_at)
        for leg in spec["legs"]:
            quote = quotes.get(leg["occ"])
            if (quote is None or quote.quoted_at is None or quote.quoted_at <= created
                    or cal.new_york(quote.quoted_at).date() != asof or quote.quoted_at > now):
                return False
        legs = [{**leg, "quantity": leg["quantity"] * spec["units"]} for leg in spec["legs"]]
        f = fills.open_legs(legs, quotes)
        if f.problem:
            raise _Cancel(f.problem)
        total = f.debit + f.fees
        cash = self.book.cash(o.account)
        if total > float(spec["max_debit"]):
            raise _Cancel(f"the debit ${total:,.2f} is over the order's limit ${float(spec['max_debit']):,.2f}")
        if total > cash:
            raise _Cancel(f"not enough cash (${cash:,.2f})")
        for leg in f.legs:
            t = opt.year_fraction(asof, date.fromisoformat(leg["expiration"]))
            leg["entry_iv"] = opt.implied_vol(leg["kind"], leg["entry_price"], bar.close, leg["strike"], t, self.rate) or leg.get(
                "entry_iv")
        expires = min(date.fromisoformat(leg["expiration"]) for leg in f.legs)
        st = self.strategies[o.account]
        self.book.open_position(account=o.account, spec_sha256=st.spec_sha256, asset="option", symbol=symbol,
                                structure=spec["structure"], legs=f.legs, decision_id=o.decision_id, at=now, day=asof,
                                cost=f.debit, fees=f.fees, max_loss=total if f.debit > 0 else None, stop_price=None,
                                expires=expires, plan=spec["plan"])
        rep.opened += 1
        return True

    def _with_marks(self, p: Position) -> list[dict[str, Any]]:
        marks = (p.marks or {}).get("legs", {})
        return [{**leg, "last_iv": (marks.get(leg["occ"]) or {}).get("iv") or leg.get("entry_iv")} for leg in p.legs]

    # 2. corporate actions ─────────────────────────────────────────────────────────────────────────
    def _corporate_actions(self, asof: date, now: datetime, last: date | None, rep: StepReport) -> None:
        since = last or asof
        held = self.book.positions()
        stocks = [p for p in held if p.asset == "stock"]
        for p in stocks:
            for d, ratio in self._splits.get(p.symbol, []):
                if since < d <= asof and p.entry_day < d and p.stop_price:
                    self.book.mark(p.id, p.last_value or 0.0, stop_price=p.stop_price / ratio)
                    self.book.event(now, p.account, "SPLIT", p.id, {"symbol": p.symbol, "date": d.isoformat(), "ratio": ratio})
        if stocks:
            by_symbol: dict[str, list[Position]] = defaultdict(list)
            for p in stocks:
                by_symbol[p.symbol].append(p)
            for dv in self.market.dividends(since):
                if not since < dv.ex_date <= asof or dv.symbol not in by_symbol:
                    continue
                for p in by_symbol[dv.symbol]:
                    if p.entry_day >= dv.ex_date or dv.ex_date.isoformat() in self.book.dividends_paid(p.id):
                        continue
                    amount = round(self.shares(p, dv.ex_date) * dv.cash_amount, 2)
                    if amount > 0:
                        self.book.add_cash(p.account, now, asof, int(amount * 1_000_000), "DIVIDEND", p.id)
                        self.book.event(now, p.account, "DIVIDEND", p.id, {"ex_date": dv.ex_date.isoformat(),
                                                                           "per_share": dv.cash_amount, "amount": amount})
        for p in held:                       # an option whose underlying splits: close at its last mark
            if p.asset == "option" and any(p.entry_day < d <= asof for d, _ in self._splits.get(p.symbol, [])):
                fee = fills.OPTION_FEE_PER_CONTRACT * sum(abs(leg["quantity"]) for leg in p.legs)
                self.book.close_position(p, at=now, day=asof, credit=p.last_value or 0.0, fees=fee,
                                         reason="underlying split: closed at the last mark", evidence={})
                rep.closed += 1

    # 3. stops ─────────────────────────────────────────────────────────────────────────────────────
    def _stops(self, asof: date, now: datetime, bars: dict[str, Bar], day: Day | None, rep: StepReport) -> None:
        for p in self.book.positions():
            if p.asset != "stock" or p.account not in self.strategies:
                continue
            bar = bars.get(p.symbol)
            price = None if bar is None else fills.stop_hit(bar, p.stop_price)
            if price is None:
                continue
            assert bar is not None
            f = fills.sell_at(price, self.shares(p, asof), self._dv(p.symbol, day, bars), "stop")
            kind = "trailing stop" if p.plan.get("trail") else "stop"
            self.book.close_position(p, at=now, day=asof, credit=f.cost, fees=f.fees,
                                     reason=f"{kind} at ${p.stop_price:,.2f}" + (
                                         " (gapped through)" if price == bar.open else ""),
                                     evidence={"bar": [bar.open, bar.high, bar.low, bar.close], "fill_price": f.price})
            rep.closed += 1

    # 4. expiries ──────────────────────────────────────────────────────────────────────────────────
    def _expiries(self, asof: date, now: datetime, bars: dict[str, Bar], rep: StepReport) -> None:
        for p in self.book.positions():
            if p.asset != "option" or p.expires is None or p.expires > asof or p.account not in self.strategies:
                continue
            if p.expires == asof:
                bar = bars.get(p.symbol)
                close = None if bar is None else bar.close
            else:
                prior = [b for b in self.market.bars(p.symbol, p.expires - (asof - p.expires)) if b.day <= p.expires]
                close = prior[-1].close if prior else None
            if close is None:
                continue
            value = fills.settle(p.legs, close)
            fee = fills.OPTION_FEE_PER_CONTRACT * sum(abs(leg["quantity"]) for leg in p.legs) if value > 0 else 0.0
            self.book.close_position(p, at=now, day=asof, credit=value, fees=fee,
                                     reason="expired in the money" if value > 0 else "expired worthless",
                                     evidence={"underlying_close": close}, cash_reason="EXPIRE")
            rep.closed += 1

    # 5. marks ─────────────────────────────────────────────────────────────────────────────────────
    def _mark(self, asof: date, bars: dict[str, Bar]) -> dict[str, float]:
        values: dict[str, float] = defaultdict(float)
        for p in self.book.positions():
            if p.account not in self.strategies:
                continue
            bar = bars.get(p.symbol)
            if p.asset == "stock":
                value = p.last_value or 0.0 if bar is None else self.shares(p, asof) * bar.close
                stop = None
                if bar is not None and p.plan.get("trail") and p.stop_price is not None:
                    stop = max(p.stop_price, bar.close * (1 - p.plan["trail"]))
                self.book.mark(p.id, value, stop_price=stop)
            else:
                quotes = self._chain_today(p.symbol, asof) or {}
                v, legs, modelled = fills.mark_legs(self._with_marks(p), quotes, None if bar is None else bar.close, asof,
                                                    self.rate)
                value = p.last_value or 0.0 if v is None else v
                self.book.mark(p.id, value, marks={"day": asof.isoformat(), "modelled": modelled,
                                                   "legs": {leg["occ"]: {"mark": leg.get("mark"), "iv": leg.get("last_iv")}
                                                            for leg in legs}})
            values[p.account] += value
        return dict(values)

    # 6a. exits ────────────────────────────────────────────────────────────────────────────────────
    def _exit(self, st: Strategy, p: Position, asof: date, now: datetime, reason: str, rep: StepReport,
              evidence: dict[str, Any] | None = None) -> None:
        did = self.book.decide(now, asof, st.id, st.spec_sha256, p.symbol, "EXIT", [reason],
                               {"position_id": p.id, "value": p.last_value, **(evidence or {})})
        self.book.place(did, st.id, now, asof, {"asset": p.asset, "action": "EXIT", "symbol": p.symbol,
                                                "position_id": p.id, "reason": reason})
        rep.orders += 1

    def _exits(self, st: Strategy, asof: date, now: datetime, day: Day | None, pending: list[Order],
               rep: StepReport) -> None:
        exiting = {o.order.get("position_id") for o in pending if o.account == st.id and o.order["action"] == "EXIT"}
        params = st.p
        for p in self.book.positions(st.id):
            if p.id in exiting:
                continue
            if p.asset == "stock":
                if st.style == "event" and sessions_between(p.entry_day, asof) >= params["hold"]:
                    self._exit(st, p, asof, now, f"held {params['hold']} sessions", rep)
                continue
            value, cost = p.last_value or 0.0, p.entry_cost
            change = value / cost - 1 if cost > 0 else 0.0
            days_left = None if p.expires is None else (p.expires - asof).days
            if change >= params["take_profit"]:
                self._exit(st, p, asof, now, f"take profit ({change:+.0%})", rep, {"change": change})
            elif change <= params["stop"]:
                self._exit(st, p, asof, now, f"stop ({change:+.0%})", rep, {"change": change})
            elif p.plan.get("exit_on") and asof >= date.fromisoformat(p.plan["exit_on"]):
                self._exit(st, p, asof, now, "results are out", rep, {"change": change})
            elif days_left is not None and days_left <= params["exit_dte"]:
                self._exit(st, p, asof, now, f"{days_left} days to expiry", rep, {"change": change})

    # 6b. entries ──────────────────────────────────────────────────────────────────────────────────
    def _entries(self, st: Strategy, asof: date, now: datetime, bars: dict[str, Bar], day: Day,
                 values: dict[str, float], pending: list[Order], rep: StepReport) -> None:
        open_positions = self.book.positions(st.id)
        mine = [o for o in pending if o.account == st.id]
        entering = {o.order["symbol"] for o in mine if o.order["action"] == "ENTER"}
        exiting = {o.order.get("position_id") for o in mine if o.order["action"] == "EXIT"}
        held = {p.symbol for p in open_positions if p.id not in exiting}
        equity = self.book.cash(st.id) + values.get(st.id, 0.0)
        committed = sum(float(o.order.get("notional") or o.order.get("max_debit") or 0) for o in mine
                        if o.order["action"] == "ENTER")
        available = self.book.cash(st.id) - committed
        cap = min(float(self.settings.paper_max_position_fraction), 1.0)
        p = st.p
        if st.style == "hold":
            if not open_positions and not entering:
                for c in candidates(st, day):
                    if c.symbol in bars:
                        self._enter_stock(st, c, asof, now, equity * p["weight"] * CASH_BUFFER, {}, rep)
            return
        picks = candidates(st, day)
        if st.style == "rebalance":
            month_end = cal.next_trading_day(asof).month != asof.month
            if not (month_end or (not open_positions and not entering)):
                return
            target = [c for c in picks if c.symbol in bars][:p["n"]]
            names = {c.symbol for c in target}
            for pos in open_positions:
                if pos.symbol not in names and pos.id not in exiting:
                    self._exit(st, pos, asof, now, f"left the top {p['n']}", rep)
            size = equity * min(1 / p["n"], cap)
            for c in target:
                if c.symbol not in held and c.symbol not in entering:
                    self._enter_stock(st, c, asof, now, size, {}, rep)
            return
        room = min(p["max_open"], int(self.settings.paper_max_open_positions)) - len(held) - len(entering)
        if st.asset == "stock":
            size = equity * min(p["size"], cap)
            plan: dict[str, Any] = {k: p[k] for k in ("stop", "trail", "hold") if k in p}
            for c in picks:
                if room <= 0 or available < size * 0.5:
                    break
                if c.symbol in held or c.symbol in entering or c.symbol not in bars:
                    continue
                self._enter_stock(st, c, asof, now, size, plan, rep)
                available -= size
                room -= 1
            return
        at_risk = sum(pos.max_loss or pos.entry_cost for pos in open_positions) + committed
        risk_cap = equity * float(self.settings.paper_max_options_at_risk_fraction)
        budget = equity * min(p["premium"], float(self.settings.paper_max_option_premium_fraction))
        for c in picks:
            if room <= 0:
                break
            if c.symbol in held or c.symbol in entering or c.symbol not in bars:
                continue
            chains = self._chain_full(c.symbol, asof)
            if not chains:
                continue
            legs = build_structure(p["structure"], chains, bars[c.symbol].close, asof, self.rate, p, c.evidence)
            if not legs:
                continue
            unit = sum(leg["quantity"] * (leg["decision_price"] or 0) * fills.CONTRACT for leg in legs)
            fee = fills.OPTION_FEE_PER_CONTRACT * sum(abs(leg["quantity"]) for leg in legs)
            if unit <= 0:
                continue
            units = int(budget // (unit + fee))
            if units < 1:
                self.book.decide(now, asof, st.id, st.spec_sha256, c.symbol, "SKIP",
                                 [f"one unit costs ${unit + fee:,.2f}, over the ${budget:,.2f} budget"], {"legs": legs})
                continue
            max_debit = units * (sum(leg["quantity"] * (fills.limit(leg["decision_price"]) if leg["quantity"] > 0 else
                                         leg["decision_price"]) * fills.CONTRACT for leg in legs) + fee)
            if at_risk + max_debit > risk_cap or max_debit > available:
                break
            option_plan: dict[str, Any] = {k: p[k] for k in ("take_profit", "stop", "exit_dte") if k in p}
            if p.get("exit_after_window") is not None and c.evidence.get("window"):
                end = date.fromisoformat(c.evidence["window"][1])
                exit_on = cal.next_trading_day(end)
                for _ in range(int(p["exit_after_window"])):
                    exit_on = cal.next_trading_day(exit_on)
                option_plan["exit_on"] = exit_on.isoformat()
            did = self.book.decide(now, asof, st.id, st.spec_sha256, c.symbol, "ENTER", c.reasons,
                                   {**c.evidence, "structure": p["structure"], "units": units, "unit_debit": unit,
                                    "legs": legs})
            self.book.place(did, st.id, now, asof, {"asset": "option", "action": "ENTER", "symbol": c.symbol,
                                                    "structure": p["structure"], "legs": legs, "units": units,
                                                    "max_debit": round(max_debit, 2), "plan": option_plan})
            rep.orders += 1
            at_risk += max_debit
            available -= max_debit
            room -= 1

    def _enter_stock(self, st: Strategy, c: Candidate, asof: date, now: datetime, notional: float, plan: dict[str, Any],
                     rep: StepReport) -> None:
        if notional < 1:
            return
        did = self.book.decide(now, asof, st.id, st.spec_sha256, c.symbol, "ENTER", c.reasons,
                               {**c.evidence, "notional": round(notional, 2)})
        self.book.place(did, st.id, now, asof, {"asset": "stock", "action": "ENTER", "symbol": c.symbol,
                                                "notional": round(notional, 2), "plan": plan})
        rep.orders += 1


class _Cancel(Exception):
    pass


__all__ = ["PaperEngine", "StepReport", "sessions_between"]
