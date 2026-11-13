"""The PAPER engine. Once a session's prices, option chains and analysis are stored, ``step`` runs every strategy's
account through that session, in the order a real account would live it:

1. fill yesterday's orders: exits first, then entries (stocks at today's open, options at today's chain snapshot);
2. corporate actions on held stocks (splits change the share count; cash dividends are paid on the ex-date);
3. resting stops that today's range touched;
4. options that expired, settled at intrinsic value on the close;
5. mark everything at today's close (stocks) and mids (options), and raise trailing stops;
6. decide exits (time, take-profit, stop, days to expiry, results passed, rebalance) and entries from today's
   analysis, as orders for the next session — nothing decided on a close fills at that close. A candidate an event
   or option strategy passes over (no price, no liquidity, no chain, no fitting contract, over budget) is recorded
   as a SKIP with its reason: once per stock, at most ``MAX_SKIPS`` per strategy and session;
7. record each account's equity for the day (once; a day is never stepped twice).

A session stepped without its own analysis (a missed session caught up later, or one whose analysis never arrived) is
recovery simulation: exits, stops, expiries and marks only. It is recorded as a RECOVERY event so the evaluation
identifies it (``desk.stocks.paper.report.recovery_sessions``).
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any

from desk.core.clock import to_iso
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
MAX_SKIPS = 5                      # skip reasons recorded per strategy and session: the reasons, not a flood
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


def written_off(p: Position, credit: float, fees: float) -> tuple[float, float, str]:
    """(credit, fees, note) for closing an option position. A position of bought contracts only is never sold for no
    more than its exit fees: it is written off at zero instead (no sale, no fee; left to expire), so it never loses
    more than it cost. Any other position closes as quoted."""
    if p.asset == "option" and p.legs and all(leg["quantity"] > 0 for leg in p.legs) and fees > 0 and credit <= fees:
        return 0.0, 0.0, " (written off at zero: the bid was below the exit fees)"
    return credit, fees, ""


def sold_max_loss(legs: list[dict[str, Any]], total: float) -> float | None:
    """The most a position that was paid a credit can lose: what it received back at its worst settlement (fees
    included in ``total``, the debit of the fill: negative for a credit). None when the loss has no bound (an
    uncovered short call), which no Learner structure is."""
    strikes = sorted({float(leg["strike"]) for leg in legs})
    if not strikes:
        return None
    far = strikes[-1] * 10
    if fills.settle(legs, far * 2) < fills.settle(legs, far) - 1e-6:
        return None
    worst = min(fills.settle(legs, s) for s in (0.0, *strikes, far))
    return max(total - worst, 0.0)


def liquidation_return(p: Position, quotes: dict[str, OptionQuote]) -> float | None:
    """The return on the money at risk if the position were left at these real quotes: bought legs sold at the bid,
    sold legs bought back at the ask, after exit fees. A bought position never counts below −100% (it can be left to
    expire), a sold one never below its maximum loss (it can be held to settle). None when a leg has no real quote."""
    credit = 0.0
    for leg in p.legs:
        q = quotes.get(leg["occ"])
        if q is None or q.bid is None or q.ask is None or q.mid is None:
            return None
        credit += leg["quantity"] * float(q.bid if leg["quantity"] > 0 else q.ask) * fills.CONTRACT
    fees = fills.OPTION_FEE_PER_CONTRACT * sum(abs(leg["quantity"]) for leg in p.legs)
    risk = p.max_loss if p.max_loss else p.entry_cost + p.entry_fees
    if not risk or risk <= 0:
        return None
    pnl = max(credit - fees, 0.0 if p.entry_cost > 0 and all(leg["quantity"] > 0 for leg in p.legs) else -math.inf) \
        - p.entry_cost - p.entry_fees
    return float(max(pnl / risk, -1.0))


def strict_exit_problem(p: Position, quotes: dict[str, OptionQuote]) -> str | None:
    """Why a Learner position cannot be left at these quotes: a leg without a real quote (it waits; never a modelled
    price), or a buy-back of a sold structure that would lose more than holding it to settle can (it is held)."""
    for leg in p.legs:
        q = quotes.get(leg["occ"])
        if q is None or q.bid is None or q.ask is None or q.mid is None:
            return f"{leg['occ']} has no real quote today"
    if p.entry_cost <= 0 and p.max_loss is not None:
        credit = sum(leg["quantity"] * float((quotes[leg["occ"]].bid if leg["quantity"] > 0
                                              else quotes[leg["occ"]].ask) or 0) for leg in p.legs) * fills.CONTRACT
        fees = fills.OPTION_FEE_PER_CONTRACT * sum(abs(leg["quantity"]) for leg in p.legs)
        if credit - fees - p.entry_cost - p.entry_fees < -p.max_loss:
            return "buying it back would lose more than its maximum loss; it is held to settle instead"
    return None


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
        self._observed_by: datetime | None = None
        self._dividends = fills.Dividends((), ())

    # ── data ───────────────────────────────────────────────────────────────────────────────────────
    def _bars(self, asof: date) -> dict[str, Bar]:
        return {r[0]: Bar(r[0], asof, r[2], r[3], r[4], r[5], r[6]) for r in self.market.bar_rows(asof, asof)}

    def _rate(self, asof: date) -> float:
        points = [p for p in self.market.rates("UST3M") if p.day <= asof]
        return points[-1].value / 100 if points else DEFAULT_RATE

    def _chain_today(self, symbol: str, asof: date) -> dict[str, OptionQuote] | None:
        if symbol not in self._chains:
            day, chains = self.market.option_chain(symbol, asof, observed_by=self._observed_by)
            chains = {expiry: [q for q in quotes if q.quoted_at is not None
                              and cal.new_york(q.quoted_at).date() == asof
                              and (self._observed_by is None or q.quoted_at <= self._observed_by)]
                      for expiry, quotes in chains.items()}
            self._chains[symbol] = day, chains
        day, chains = self._chains[symbol]
        return fills.index_chain(chains) if day == asof else None

    def _chain_full(self, symbol: str, asof: date) -> dict[date, list[OptionQuote]] | None:
        self._chain_today(symbol, asof)
        day, chains = self._chains[symbol]
        return chains if day == asof else None

    def _q(self, symbol: str, asof: date, s: float | None) -> float:
        """The stock's trailing-12-month dividend yield, for option prices the model fills in."""
        return self._dividends.yield_on(symbol, asof, s)

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
        self._observed_by = now
        self._chains = {}
        self._splits = defaultdict(list)
        splits = self.market.splits()
        for sp in splits:
            self._splits[sp.symbol].append((sp.execution_date, sp.ratio))
        self._dividends = fills.Dividends(self.market.dividends(asof - timedelta(days=400)), splits)
        for st in self.strategies.values():
            self.market.register_spec(f"stocks_paper_{st.id}", st.version, st.content(), now)
            capital = st.p.get("capital") or (self.settings.paper_option_capital_usd if st.asset == "option"
                                              else self.settings.paper_starting_capital_usd)
            self.book.open_account(st.id, float(capital), now, asof)
        if not allow_entries:
            for order in self.book.pending():
                if order.order["action"] == "ENTER":
                    self.book.resolve(order.id, "CANCELLED", now, "STOP: new entries disabled")
                    rep.cancelled += 1
        if day is None or day.asof != asof:
            self.book.event(now, None, "RECOVERY", None, {"session": asof.isoformat(), "entries": False,
                                                          "why": "stepped without that session's analysis"})
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

    def _delisted_close(self, symbol: str, asof: date) -> float | None:
        """The last stored close of a stock gone from the listing files before ``asof`` (delisted), which an exit
        sells at, as the evidence report does; a halted or briefly missing stock that is still listed just waits."""
        listed = self.market.conn.execute("SELECT active, last_seen FROM stk_listings WHERE symbol = ?",
                                          (symbol,)).fetchone()
        if listed is None or listed[0] or listed[1] >= to_iso(datetime.combine(asof, time(0, 0), cal.NEW_YORK)):
            return None
        row = self.market.conn.execute("SELECT close FROM stk_bars WHERE symbol = ? AND day < ? ORDER BY day DESC LIMIT 1",
                                       (symbol, asof.toordinal())).fetchone()
        return None if row is None else float(row[0])

    def _fill_stock(self, o: Order, asof: date, now: datetime, bars: dict[str, Bar], day: Day | None,
                    rep: StepReport) -> bool:
        spec = o.order
        bar = bars.get(spec["symbol"])
        dv = self._dv(spec["symbol"], day, bars)
        if bar is None:
            last = self._delisted_close(spec["symbol"], asof) if spec["action"] == "EXIT" else None
            p = self._position(spec["position_id"]) if last is not None else None
            if last is None or p is None:
                return False
            f = fills.sell_at(last, self.shares(p, asof), dv, "last close before delisting")
            self.book.close_position(p, at=now, day=asof, credit=f.cost, fees=f.fees,
                                     reason=f"{spec['reason']} (delisted: sold at its last close)",
                                     evidence={"fill_price": f.price, "order": o.id, "delisted": True})
            rep.closed += 1
            return True
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
        if self.strategies[o.account].style != "hold":
            equity = cash + sum(p.last_value if p.last_value is not None else p.entry_cost
                                for p in self.book.positions(o.account))
            notional = min(notional, max(0, equity) * float(self.settings.paper_max_position_fraction))
            distance = spec["plan"].get("stop") or spec["plan"].get("trail")
            if distance:
                notional = min(notional, max(0, equity) * float(self.settings.paper_stock_risk_fraction) / distance)
            if dv is None or not math.isfinite(dv) or dv <= 0:
                raise _Cancel("stock liquidity is unavailable")
            notional = min(notional, dv * float(self.settings.paper_stock_participation_fraction))
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
            s = None if bar is None else bar.close
            if spec.get("strict_quotes"):
                problem = strict_exit_problem(p, quotes or {})
                if problem:
                    if not stale:
                        return False
                    raise _Cancel(problem)
            f = fills.close_legs(self._with_marks(p), quotes or {}, s, asof, self.rate, self._q(symbol, asof, s))
            if f.problem:
                if not stale:
                    return False
                raise _Cancel(f.problem)
            credit, fees, note = written_off(p, -f.debit, f.fees)
            self.book.close_position(p, at=now, day=asof, credit=credit, fees=fees, reason=spec["reason"] + note,
                                     evidence={"legs": f.legs, "order": o.id, "quoted": not f.modeled,
                                               "modeled_exit": f.modeled})
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
        if spec.get("strict_quotes"):
            for leg in spec["legs"]:
                q = quotes[leg["occ"]]
                if q.bid is None or q.ask is None or q.bid <= 0 or q.ask < q.bid:
                    raise _Cancel(f"{leg['occ']} is quoted {q.bid}/{q.ask}: no fill on a crossed or empty quote")
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
            leg["entry_iv"] = opt.implied_vol(leg["kind"], leg["entry_price"], bar.close, leg["strike"], t, self.rate,
                                              self._q(symbol, asof, bar.close)) or leg.get("entry_iv")
        expires = min(date.fromisoformat(leg["expiration"]) for leg in f.legs)
        st = self.strategies[o.account]
        self.book.open_position(account=o.account, spec_sha256=st.spec_sha256, asset="option", symbol=symbol,
                                structure=spec["structure"], legs=f.legs, decision_id=o.decision_id, at=now, day=asof,
                                cost=f.debit, fees=f.fees, max_loss=total if f.debit > 0 else sold_max_loss(f.legs, total),
                                stop_price=None,
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
                credit, fee, note = written_off(p, p.last_value or 0.0, fee)
                self.book.close_position(p, at=now, day=asof, credit=credit, fees=fee,
                                         reason="underlying split: closed at the last mark" + note, evidence={})
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
            # Settled on the official close of its last trading day (the session before an expiry that falls on a
            # market holiday), never on an earlier session's close: a stock missing from that day's bars waits.
            last = p.expires if cal.is_trading_day(p.expires) else cal.previous_trading_day(p.expires)
            if last == asof:
                bar = bars.get(p.symbol)
                close = None if bar is None else bar.close
            else:
                close = next((b.close for b in self.market.bars(p.symbol, last) if b.day == last), None)
            if close is None:
                continue
            value = fills.settle(p.legs, close)
            fee = fills.OPTION_FEE_PER_CONTRACT * sum(abs(leg["quantity"]) for leg in p.legs) if value > 0 else 0.0
            value, fee, _ = written_off(p, value, fee)
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
                s = None if bar is None else bar.close
                v, legs, modelled = fills.mark_legs(self._with_marks(p), quotes, s, asof, self.rate,
                                                    self._q(p.symbol, asof, s))
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
        order: dict[str, Any] = {"asset": p.asset, "action": "EXIT", "symbol": p.symbol, "position_id": p.id, "reason": reason}
        if st.style == "learner":
            order["strict_quotes"] = True
        self.book.place(did, st.id, now, asof, order)
        rep.orders += 1

    def _exits(self, st: Strategy, asof: date, now: datetime, day: Day | None, pending: list[Order],
               rep: StepReport) -> None:
        exiting = {o.order.get("position_id") for o in pending if o.account == st.id and o.order["action"] == "EXIT"}
        if st.style == "learner":
            self._learner_exits(st, asof, now, exiting, rep)
            return
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
        if st.style == "learner":           # its entries are decided by desk.stocks.learner.policy, not here
            return
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
        skipped: set[str] = set()

        def skip(symbol: str, reason: str, evidence: dict[str, Any] | None = None) -> None:
            if symbol not in skipped and len(skipped) < MAX_SKIPS:
                skipped.add(symbol)
                self.book.decide(now, asof, st.id, st.spec_sha256, symbol, "SKIP", [reason], evidence or {})

        if st.asset == "stock":
            plan: dict[str, Any] = {k: p[k] for k in ("stop", "trail", "hold") if k in p}
            fraction = min(p["size"], cap)
            distance = plan.get("stop") or plan.get("trail")
            if distance:
                fraction = min(fraction, float(self.settings.paper_stock_risk_fraction) / distance)
            for c in picks:
                if room <= 0 or available < 1:
                    break
                if c.symbol in held or c.symbol in entering:
                    continue
                if c.symbol not in bars:
                    skip(c.symbol, "no price for this stock today")
                    continue
                dv = self._dv(c.symbol, day, bars)
                if dv is None or not math.isfinite(dv) or dv <= 0:
                    skip(c.symbol, "no recent trading volume to size the order against")
                    continue
                size = min(equity * fraction, available * CASH_BUFFER,
                           dv * float(self.settings.paper_stock_participation_fraction))
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
            if c.symbol in held or c.symbol in entering:
                continue
            if c.symbol not in bars:
                skip(c.symbol, "no price for this stock today")
                continue
            structure = c.evidence.get("structure") or p["structure"]      # a followed contract names its own
            legs = self._structure(structure, c.symbol, asof, bars[c.symbol].close, p, c.evidence)
            if not legs:
                skip(c.symbol, self._no_contract(c.symbol, asof))
                continue
            if structure == "random_lotto":
                structure = f"long_{legs[0]['kind']}"
            unit = sum(leg["quantity"] * (leg["decision_price"] or 0) * fills.CONTRACT for leg in legs)
            fee = fills.OPTION_FEE_PER_CONTRACT * sum(abs(leg["quantity"]) for leg in legs)
            if unit <= 0:
                skip(c.symbol, "the chosen contracts have no price to trade at today", {"legs": legs})
                continue
            units = int(budget // (unit + fee))
            if units < 1:
                skip(c.symbol, f"one unit costs ${unit + fee:,.2f}, over the ${budget:,.2f} budget", {"legs": legs})
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
                                   {**c.evidence, "structure": structure, "units": units, "unit_debit": unit,
                                    "legs": legs})
            self.book.place(did, st.id, now, asof, {"asset": "option", "action": "ENTER", "symbol": c.symbol,
                                                    "structure": structure, "legs": legs, "units": units,
                                                    "max_debit": round(max_debit, 2), "plan": option_plan})
            rep.orders += 1
            at_risk += max_debit
            available -= max_debit
            room -= 1

    def _learner_exits(self, st: Strategy, asof: date, now: datetime, exiting: set[Any], rep: StepReport) -> None:
        """A Learner account's positions leave by the exit rule chosen at entry (``learner.actions.EXIT_RULES``),
        read on that session's real closing quotes at liquidation value: a take-profit or a stop on the return on
        the money at risk, or the session count since the decision (decided the session before, so the order fills
        on that session's quotes). A rule never reacts to a modelled price; a position no rule closes settles at
        expiry."""
        from desk.stocks.learner.actions import BY_EXIT

        for p in self.book.positions(st.id):
            if p.id in exiting or p.asset != "option":
                continue
            rule = BY_EXIT.get(str(p.plan.get("exit_rule")))
            if rule is None:
                continue
            decided = date.fromisoformat(p.plan.get("decided") or p.entry_day.isoformat())
            n = sessions_between(decided, asof)
            r = liquidation_return(p, self._chain_today(p.symbol, asof) or {})
            if rule.sessions is not None and n + 1 >= rule.sessions:
                self._exit(st, p, asof, now, f"{rule.title.lower()} (session {n + 1} after the decision)", rep,
                           {"exit_rule": rule.id, "return": r, "strict_quotes": True})
            elif r is not None and rule.take_profit is not None and r >= rule.take_profit:
                self._exit(st, p, asof, now, f"take profit ({r:+.0%} on the money at risk)", rep,
                           {"exit_rule": rule.id, "return": r})
            elif r is not None and rule.stop is not None and r <= rule.stop:
                self._exit(st, p, asof, now, f"stop ({r:+.0%} on the money at risk)", rep,
                           {"exit_rule": rule.id, "return": r})

    def _structure(self, structure: str, symbol: str, asof: date, s: float, p: dict[str, Any],
                   evidence: dict[str, Any]) -> list[dict[str, Any]] | None:
        chains = self._chain_full(symbol, asof)
        return build_structure(structure, chains, s, asof, self.rate, p, evidence) if chains else None

    def _no_contract(self, symbol: str, asof: date) -> str:
        """Why ``_structure`` found nothing to trade."""
        if not self._chain_full(symbol, asof):
            return "no option chain was collected for this stock today"
        return "no contract in today's chain fits the rule's expiry, strike and spread"

    def _enter_stock(self, st: Strategy, c: Candidate, asof: date, now: datetime, notional: float, plan: dict[str, Any],
                     rep: StepReport) -> None:
        if notional < 1:
            return
        did = self.book.decide(now, asof, st.id, st.spec_sha256, c.symbol, "ENTER", c.reasons,
                               {**c.evidence, "notional": round(notional, 2),
                                "risk_profile": {"position_fraction_cap": str(self.settings.paper_max_position_fraction),
                                                 "planned_stop_risk_fraction": str(self.settings.paper_stock_risk_fraction),
                                                 "liquidity_fraction_cap": str(self.settings.paper_stock_participation_fraction)},
                                "execution_revision": "stocks-reliability-v2"})
        self.book.place(did, st.id, now, asof, {"asset": "stock", "action": "ENTER", "symbol": c.symbol,
                                                "notional": round(notional, 2), "plan": plan})
        rep.orders += 1


class _Cancel(Exception):
    pass


__all__ = ["PaperEngine", "StepReport", "liquidation_return", "sessions_between", "sold_max_loss",
           "strict_exit_problem"]
