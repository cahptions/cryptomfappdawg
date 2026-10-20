"""Historical simulation of the Robinhood desk's frozen strategies on Coinbase daily bars.

It runs the same ``strategies`` functions the PAPER engine runs, day by day: decisions for day D use bars that
ended by D 00:00 UTC, fills happen at day D's open, and a hard stop fills at the worse of the open and the stop.
Every fill pays half the round-trip cost (Robinhood-measured per coin when available, else the configured
assumption). This is a description of the past under stated assumptions, not evidence of future profit: the
universe is today's list (coins that died are missing), fills ignore size, and the cost model is an estimate.
"""

from __future__ import annotations

import bisect
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_FLOOR, Decimal
from typing import Any

from desk.rh import strategies as st
from desk.rh.contracts import Candle

LOOKBACK_BARS = 200         # snapshot() needs 120 days; slicing keeps the run fast


@dataclass
class SimPosition:
    strategy_id: str
    symbol: str
    entry_day: date
    entry_price: Decimal        # paid per unit, cost included
    ticket: Decimal
    stop: Decimal | None
    peak_close: Decimal
    position_id: str


@dataclass(frozen=True)
class SimTrade:
    strategy_id: str
    symbol: str
    entry_day: date
    exit_day: date
    entry_price: Decimal
    exit_price: Decimal
    ticket: Decimal
    net_return: Decimal
    pnl: Decimal
    reason: str


@dataclass
class SimResult:
    start: date
    end: date
    symbols: list[str]
    cost_model: dict[str, str]
    trades: list[SimTrade] = field(default_factory=list)
    skipped: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    regime_days: dict[str, int] = field(default_factory=lambda: defaultdict(int))

    def summary(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for sid in st.ORDER:
            rows = [t for t in self.trades if t.strategy_id == sid]
            returns = [t.net_return for t in rows]
            pnl = [t.pnl for t in rows]
            curve, peak, worst = Decimal(0), Decimal(0), Decimal(0)
            for t in sorted(rows, key=lambda r: (r.exit_day, r.symbol)):
                curve += t.pnl
                peak = max(peak, curve)
                worst = min(worst, curve - peak)
            out[sid] = {
                "trades": len(rows), "wins": sum(1 for r in returns if r > 0),
                "win_rate": _q(Decimal(sum(1 for r in returns if r > 0)) / len(rows)) if rows else None,
                "mean_net_return": _q(sum(returns, Decimal(0)) / len(rows)) if rows else None,
                "median_net_return": _q(st.median(returns)) if rows else None,
                "best": _q(max(returns)) if rows else None, "worst": _q(min(returns)) if rows else None,
                "total_pnl_usd": _q(sum(pnl, Decimal(0)), "0.01"), "max_drawdown_usd": _q(worst, "0.01"),
                "mean_hold_days": _q(Decimal(sum((t.exit_day - t.entry_day).days for t in rows)) / len(rows), "0.1")
                if rows else None,
                "exit_reasons": dict(sorted(_count(t.reason for t in rows).items())),
                # P&L per dollar-year held: compares a long hold with many short trades on deployed capital
                "pnl_per_dollar_year": _q(sum(pnl, Decimal(0)) * 365 / ticket_days) if (ticket_days := sum(
                    (t.ticket * max((t.exit_day - t.entry_day).days, 1) for t in rows), Decimal(0))) else None,
            }
        return out


def _q(value: Decimal, step: str = "0.0001") -> str:
    return str(value.quantize(Decimal(step)))


def _count(items: Any) -> dict[str, int]:
    out: dict[str, int] = defaultdict(int)
    for item in items:
        out[item] += 1
    return out


def _day_start(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=UTC)


def simulate(history: dict[str, list[Candle]], start: date, end: date, *, cost: Callable[[str], Decimal],
             cost_source: Callable[[str], str], starting_capital: Decimal, fixed_fraction: Decimal,
             max_fraction: Decimal, max_open: int, max_roundtrip_cost: Decimal) -> SimResult:
    """Simulate sessions ``start``..``end`` inclusive. ``history`` maps Robinhood symbol -> daily bars, oldest first."""
    starts = {s: [b.start for b in bars] for s, bars in history.items()}
    by_day = {s: {b.start.date(): b for b in bars} for s, bars in history.items()}
    result = SimResult(start, end, sorted(history), {s: f"{cost(s)} ({cost_source(s)})" for s in sorted(history)})
    open_: list[SimPosition] = []
    realized = Decimal(0)
    sequence = 0

    def close(pos: SimPosition, day: date, price: Decimal, reason: str) -> None:
        nonlocal realized
        exit_price = price * (1 - cost(pos.symbol) / 2)
        units = pos.ticket / pos.entry_price
        pnl = units * exit_price - pos.ticket
        realized += pnl
        result.trades.append(SimTrade(pos.strategy_id, pos.symbol, pos.entry_day, day, pos.entry_price, exit_price,
                                      pos.ticket, exit_price / pos.entry_price - 1, pnl, reason))
        open_.remove(pos)

    day = start
    while day <= end:
        cutoff = _day_start(day)
        snaps: dict[str, st.Snapshot] = {}
        eligible: dict[str, st.Snapshot] = {}
        for symbol, bars in history.items():
            idx = bisect.bisect_left(starts[symbol], cutoff)
            snap, reason = st.snapshot(symbol, bars[max(0, idx - LOOKBACK_BARS):idx], day)
            if snap is not None:
                snaps[symbol] = snap
                if reason is None:
                    eligible[symbol] = snap
        reg = st.regime(snaps.get(str(st.COMMON["regime_symbol"])))
        result.regime_days[reg] += 1
        held = [st.Held(p.position_id, p.strategy_id, p.symbol, p.entry_day, p.peak_close) for p in open_]
        for signal in st.exits(day, held, snaps, eligible, reg):
            pos = next(p for p in open_ if p.position_id == signal.position_id)
            bar = by_day[pos.symbol].get(day)
            price = bar.open if bar else (snaps[pos.symbol].close if pos.symbol in snaps else None)
            if price is None:
                continue                            # no price today: the exit waits for the next bar
            close(pos, day, price, signal.reasons[0])
        held = [st.Held(p.position_id, p.strategy_id, p.symbol, p.entry_day, p.peak_close) for p in open_]
        for signal in st.entries(day, eligible, reg, held):
            bar = by_day[signal.symbol].get(day)
            if bar is None:
                result.skipped["NO_BAR"] += 1
                continue
            if len(open_) >= max_open:
                result.skipped["BOOK_FULL"] += 1
                continue
            if cost(signal.symbol) > max_roundtrip_cost:
                result.skipped["COST_ABOVE_CEILING"] += 1
                continue
            capital = starting_capital + realized
            ticket = (capital * min(fixed_fraction, max_fraction)).quantize(Decimal("0.01"), rounding=ROUND_FLOOR)
            if ticket < 1:
                result.skipped["TICKET_TOO_SMALL"] += 1
                continue
            entry = bar.open * (1 + cost(signal.symbol) / 2)
            snap = eligible[signal.symbol]
            stop = None if signal.strategy_id == "B1_HOLD_BTC" else bar.open * (1 - snap.stop_fraction)
            sequence += 1
            open_.append(SimPosition(signal.strategy_id, signal.symbol, day, entry, ticket, stop, bar.open,
                                     f"sim{sequence}"))
        for pos in list(open_):                     # intraday hard stops on day D
            bar = by_day[pos.symbol].get(day)
            if bar is None:
                continue
            if pos.stop is not None and bar.low <= pos.stop:
                close(pos, day, min(bar.open, pos.stop), "HARD_STOP")
                continue
            pos.peak_close = max(pos.peak_close, bar.close)
        day += timedelta(days=1)
    for pos in list(open_):                         # mark what is still open at the last close, net of exit cost
        bars = [b for b in history[pos.symbol] if b.start.date() <= end]
        close(pos, end, bars[-1].close, "OPEN_AT_END")
    return result


__all__ = ["SimResult", "SimTrade", "simulate"]
