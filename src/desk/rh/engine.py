"""The Robinhood desk's PAPER engine: history, quotes, cost probes, daily sessions, exits and hard stops.

* Daily session (after 00:05 UTC, once the previous day's BTC bar is stored): exits first, then entries, using
  the frozen ``strategies``. Every ENTER, EXIT and blocked signal (SKIP, with its reasons) is recorded.
* Fills use Robinhood's own estimate for the exact quantity on both routes: buy on the cheaper route, sell on the
  better one, then a 2.5 s wait and a second estimate; the worse of the two is booked (same rule as the Solana
  desk). An entry is refused when the measured round trip exceeds ``max_roundtrip_cost_fraction``.
* Hard stops are checked every quote interval on Robinhood's best bid; the BTC baseline has no stop.
* Without a Robinhood key the desk still syncs history and records what each strategy would do (SKIP
  NO_ROBINHOOD_PRICE), so the dashboard is useful before the key exists. Nothing here can place an order.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import ROUND_FLOOR, Decimal
from typing import Any

from desk.core.clock import utc_now
from desk.core.config import LoadedConfig
from desk.core.errors import DeskError
from desk.core.paths import DeskPaths
from desk.rh import strategies as st
from desk.rh.contracts import Estimate, Pair, Route
from desk.rh.history import DAILY, HOURLY, sync_candles, today_start, universe, write_backtest
from desk.rh.repo import Position, RhDatabases, RhLedger, RhMarket
from desk.rh.suite import RhSuite
from desk.storage.db import StorageError

log = logging.getLogger(__name__)
SESSION_DELAY = timedelta(minutes=5)          # Coinbase publishes the finished daily bar shortly after 00:00 UTC
CONSERVATIVE_WAIT_SECONDS = 2.5
HISTORY_INTERVAL_SECONDS = 900
PAIRS_INTERVAL_SECONDS = 3600
PROBE_INTERVAL_SECONDS = 60
SESSION_CHECK_SECONDS = 60
EQUITY_INTERVAL_SECONDS = 300
HOURLY_CHART_DAYS = 14


@dataclass
class Stats:
    started_at: datetime
    sessions: int = 0
    entries: int = 0
    exits: int = 0
    skips: int = 0
    quotes: int = 0
    probes: int = 0
    errors: dict[str, int] = field(default_factory=dict)
    last_error: str | None = None


def _floor(value: Decimal, step: Decimal) -> Decimal:
    return (value / step).to_integral_value(rounding=ROUND_FLOOR) * step


class RhDesk:
    def __init__(self, paths: DeskPaths, loaded: LoadedConfig, suite: RhSuite, dbs: RhDatabases, *,
                 clock: Callable[[], datetime] = utc_now, sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
                 wait_seconds: float = CONSERVATIVE_WAIT_SECONDS) -> None:
        self.paths, self.loaded, self.suite = paths, loaded, suite
        self.settings = loaded.settings.robinhood
        self.market, self.ledger = RhMarket(dbs.market), RhLedger(dbs.ledger)
        self.clock, self.sleep, self.wait_seconds = clock, sleep, wait_seconds
        self.stats = Stats(clock())
        self._probe_cursor = 0
        self._pending_exits: dict[str, str] = {}       # position id -> reason, retried until a price exists

    # ── helpers ──────────────────────────────────────────────────────────────────────────────────
    @property
    def priced(self) -> bool:
        return self.suite.key_problem is None

    def symbols(self) -> tuple[list[str], str]:
        return universe(self.market)

    def pair(self, symbol: str) -> Pair | None:
        return next((p for p in self.market.pairs() if p.symbol == symbol), None)

    def ticket(self) -> Decimal:
        capital = self.settings.starting_capital_usd + self.ledger.realized()
        fraction = min(self.settings.fixed_fraction, self.settings.max_ticket_fraction)
        return (capital * fraction).quantize(Decimal("0.01"), rounding=ROUND_FLOOR)

    def _fresh(self, estimates: list[Estimate]) -> list[Estimate]:
        limit = timedelta(seconds=self.settings.estimate_max_age_seconds)
        now = self.clock()
        return [e for e in estimates if timedelta(0) <= now - e.received_at <= limit]

    async def _estimates(self, symbol: str, quantity: Decimal, purpose: str) -> list[Estimate]:
        out: list[Estimate] = []
        routes: tuple[Route, ...] = ("MM", "EX")
        for route in routes:
            try:
                found = await self.suite.robinhood.estimates(symbol, route, [quantity])
            except DeskError as exc:
                log.warning("robinhood estimate failed", extra={"symbol": symbol, "route": route, "error": str(exc)[:160]})
                continue
            self.market.save_estimates(found, purpose)
            out.extend(e for e in found if e.quantity == quantity)
        return self._fresh(out)

    def _error(self, job: str, exc: BaseException) -> None:
        self.stats.errors[job] = self.stats.errors.get(job, 0) + 1
        self.stats.last_error = f"{job}: {type(exc).__name__}: {exc}"[:300]
        log.warning("robinhood job failed", extra={"job": job, "error": self.stats.last_error})

    # ── jobs ─────────────────────────────────────────────────────────────────────────────────────
    async def refresh_pairs(self) -> None:
        if self.priced:
            self.market.save_pairs(await self.suite.robinhood.trading_pairs(), self.clock())

    async def sync_history(self) -> None:
        symbols, _ = self.symbols()
        now = self.clock()
        await sync_candles(self.suite.coinbase, self.market, symbols, granularity=DAILY,
                           days=int(st.COMMON["min_history_days"]) + 120, now=now)
        await sync_candles(self.suite.coinbase, self.market, symbols, granularity=HOURLY, days=HOURLY_CHART_DAYS, now=now)

    async def poll_quotes(self) -> None:
        if not self.priced:
            return
        symbols = [p.symbol for p in self.market.pairs() if p.tradable]
        if not symbols:
            return
        quotes = await self.suite.robinhood.best_bid_ask(symbols)
        self.stats.quotes += self.market.save_quotes(quotes)
        bids = {q.symbol: q.bid for q in quotes}
        for pos in self.ledger.positions("OPEN"):
            if pos.id in self._pending_exits:
                await self.exit(pos, self._pending_exits[pos.id])
            elif pos.stop_price is not None and pos.symbol in bids and bids[pos.symbol] <= pos.stop_price:
                await self.exit(pos, "HARD_STOP")

    async def probe_costs(self) -> None:
        if not self.priced:
            return
        pairs = [p for p in self.market.pairs() if p.tradable]
        quotes = self.market.latest_quotes()
        if not pairs or not quotes:
            return
        pair = pairs[self._probe_cursor % len(pairs)]
        self._probe_cursor += 1
        quote = quotes.get(pair.symbol)
        if quote is None:
            return
        quantity = _floor(self.ticket() / quote.ask, pair.asset_increment)
        if quantity > 0:
            await self._estimates(pair.symbol, quantity, "PROBE")
            self.stats.probes += 1

    async def snapshot_equity(self) -> None:
        quotes = self.market.latest_quotes()
        open_ = self.ledger.positions("OPEN")
        value = sum((p.quantity * quotes[p.symbol].bid if p.symbol in quotes else p.entry_cost for p in open_), Decimal(0))
        self.ledger.snapshot_equity(self.clock(), value, len(open_))

    # ── the daily session ────────────────────────────────────────────────────────────────────────
    def state(self, day: date) -> tuple[dict[str, st.Snapshot], dict[str, st.Snapshot], str, dict[str, str]]:
        symbols, _ = self.symbols()
        snaps, eligible, why = {}, {}, {}
        since = today_start(self.clock()) - timedelta(days=st.COMMON["min_history_days"] + 90)
        for symbol in symbols:
            snap, reason = st.snapshot(symbol, self.market.candles(symbol, DAILY, since=since), day)
            if snap is not None:
                snaps[symbol] = snap
            if reason is None and snap is not None:
                eligible[symbol] = snap
            else:
                why[symbol] = reason or "?"
        return snaps, eligible, st.regime(snaps.get(str(st.COMMON["regime_symbol"]))), why

    def session_ready(self, now: datetime) -> date | None:
        day = now.date()
        if now < today_start(now) + SESSION_DELAY or self.ledger.session_done(day):
            return None
        last = self.market.last_candle_start(str(st.COMMON["regime_symbol"]), DAILY)
        return day if last is not None and last.date() == day - timedelta(days=1) else None

    async def session(self) -> None:
        now = self.clock()
        day = self.session_ready(now)
        if day is None:
            return
        snaps, eligible, reg, why = self.state(day)
        open_ = self.ledger.positions("OPEN")
        for pos in open_:
            if pos.symbol in snaps:
                self.ledger.update_peak(pos.id, snaps[pos.symbol].close)
        open_ = self.ledger.positions("OPEN")
        held = [st.Held(p.id, p.strategy_id, p.symbol, p.session_day, p.peak_close) for p in open_]
        for signal in st.exits(day, held, snaps, eligible, reg):
            pos = next(p for p in open_ if p.id == signal.position_id)
            self.market.record_decision(now, day, signal, "EXIT", signal.reasons, {"regime": reg})
            await self.exit(pos, signal.reasons[0])
        held = [st.Held(p.id, p.strategy_id, p.symbol, p.session_day, p.peak_close)
                for p in self.ledger.positions("OPEN")]
        for signal in st.entries(day, eligible, reg, held):
            await self.enter(signal, day, eligible[signal.symbol], reg)
        self.ledger.event(now, "SESSION", {"day": day.isoformat(), "regime": reg, "eligible": len(eligible),
                                           "ineligible": why, "open_positions": len(self.ledger.positions("OPEN"))})
        self.stats.sessions += 1
        try:
            await asyncio.to_thread(write_backtest, self.paths, self.loaded, now)
        except (DeskError, StorageError, OSError, ArithmeticError) as exc:
            self._error("backtest", exc)

    def _skip(self, now: datetime, day: date, signal: st.Signal, reasons: list[str], inputs: dict[str, Any]) -> None:
        self.market.record_decision(now, day, signal, "SKIP", [*signal.reasons, *reasons], inputs)
        self.stats.skips += 1

    async def enter(self, signal: st.Signal, day: date, snap: st.Snapshot, reg: str) -> None:
        now = self.clock()
        inputs: dict[str, Any] = {"regime": reg}
        if not self.priced:                         # nothing below can be checked without Robinhood's prices
            self._skip(now, day, signal, ["NO_ROBINHOOD_PRICE"], inputs)
            return
        blocks: list[str] = []
        if self.paths.stop_file.exists():
            blocks.append("STOP_FILE")
        if (halt := self.ledger.halted()) is not None:
            blocks.append(f"HALTED:{halt}")
        if len(self.ledger.positions("OPEN")) >= self.settings.max_open_positions:
            blocks.append("BOOK_FULL")
        pair = self.pair(signal.symbol)
        if pair is None or not pair.tradable:
            blocks.append("NOT_TRADABLE_ON_ROBINHOOD")
        quote = self.market.latest_quotes().get(signal.symbol)
        if quote is None or now - quote.received_at > timedelta(seconds=3 * self.settings.quote_interval_seconds):
            blocks.append("STALE_QUOTE")
        if blocks or pair is None or quote is None:
            self._skip(now, day, signal, blocks, inputs)
            return
        ticket = self.ticket()
        quantity = _floor(ticket / quote.ask, pair.asset_increment)
        if quantity <= 0 or quantity > pair.max_order_size or (
                pair.min_order_amount is not None and quantity * quote.ask < pair.min_order_amount):
            self._skip(now, day, signal, ["SIZE_OUTSIDE_PAIR_LIMITS"], {**inputs, "ticket": str(ticket)})
            return
        first = await self._estimates(signal.symbol, quantity, "ENTRY")
        buys = [e for e in first if e.side == "BUY"]
        sells = [e for e in first if e.side == "SELL"]
        if not buys or not sells:
            self._skip(now, day, signal, ["NO_FRESH_ESTIMATE"], inputs)
            return
        best_buy = min(buys, key=lambda e: e.total)
        roundtrip = (best_buy.total - max(e.total for e in sells)) / best_buy.total
        inputs.update(ticket=str(ticket), quantity=str(quantity), route=best_buy.route, roundtrip=str(roundtrip),
                      estimates=sorted({e.raw_sha256 for e in first}))
        if roundtrip > self.settings.max_roundtrip_cost_fraction:
            self._skip(now, day, signal, ["ROUNDTRIP_COST_ABOVE_CEILING"], inputs)
            return
        await self.sleep(self.wait_seconds)
        second = [e for e in await self._estimates(signal.symbol, quantity, "ENTRY")
                  if e.side == "BUY" and e.route == best_buy.route]
        buy = max([best_buy, *second], key=lambda e: e.total)            # the worse of the two estimates
        stop = None if signal.strategy_id == "B1_HOLD_BTC" else buy.unit_price * (1 - snap.stop_fraction)
        digest = self.market.record_decision(now, day, signal, "ENTER", signal.reasons, inputs)
        try:
            self.ledger.open_position(at=self.clock(), day=day, strategy_id=signal.strategy_id, symbol=signal.symbol,
                                      decision_hash=digest, buy=buy, roundtrip=roundtrip, stop_price=stop,
                                      plan={"features": signal.features, "stop_fraction": str(snap.stop_fraction)})
        except StorageError as exc:
            self._skip(now, day, signal, [f"LEDGER:{exc}"[:80]], inputs)
            return
        self.stats.entries += 1

    async def exit(self, pos: Position, reason: str) -> bool:
        first = [e for e in await self._estimates(pos.symbol, pos.quantity, "EXIT") if e.side == "SELL"]
        if not first:
            self._pending_exits[pos.id] = reason                         # retried at the next quote poll
            return False
        best = max(first, key=lambda e: e.total)
        await self.sleep(self.wait_seconds)
        second = [e for e in await self._estimates(pos.symbol, pos.quantity, "EXIT")
                  if e.side == "SELL" and e.route == best.route]
        sell = min([best, *second], key=lambda e: e.total)              # the worse credit
        self.ledger.close_position(pos.id, at=self.clock(), sell=sell, reason=reason)
        self._pending_exits.pop(pos.id, None)
        self.stats.exits += 1
        self._check_loss_halt()
        return True

    def _check_loss_halt(self) -> None:
        if self.ledger.realized() <= -self.settings.learning_max_cumulative_loss_usd and self.ledger.halted() is None:
            self.ledger.set_halt("CUMULATIVE_LOSS", self.clock())

    # ── scheduler ────────────────────────────────────────────────────────────────────────────────
    async def _loop(self, name: str, interval: float, job: Callable[[], Awaitable[None]], stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                await job()
            except (DeskError, StorageError, ArithmeticError, KeyError, ValueError) as exc:
                self._error(name, exc)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=interval)

    async def run(self, minutes: float | None = None) -> Stats:
        now = self.clock()
        self.market.register_specs(now)
        self.ledger.ensure_book(self.settings.learning_cash_usd, now)
        stop = asyncio.Event()
        await self._first_pass()
        jobs = [("pairs", PAIRS_INTERVAL_SECONDS, self.refresh_pairs),
                ("history", HISTORY_INTERVAL_SECONDS, self.sync_history),
                ("quotes", self.settings.quote_interval_seconds, self.poll_quotes),
                ("probes", PROBE_INTERVAL_SECONDS, self.probe_costs),
                ("session", SESSION_CHECK_SECONDS, self.session),
                ("equity", EQUITY_INTERVAL_SECONDS, self.snapshot_equity)]
        tasks = [asyncio.create_task(self._loop(n, i, j, stop), name=f"rh-{n}") for n, i, j in jobs]
        try:
            if minutes is None:
                await asyncio.Event().wait()
            else:
                await asyncio.sleep(minutes * 60)
        finally:
            stop.set()
            await asyncio.gather(*tasks, return_exceptions=True)
        return self.stats

    async def _first_pass(self) -> None:
        """Pairs before history (the universe), history before the session."""
        for name, job in (("pairs", self.refresh_pairs), ("history", self.sync_history), ("quotes", self.poll_quotes)):
            try:
                await job()
            except (DeskError, StorageError, ArithmeticError, KeyError, ValueError) as exc:
                self._error(name, exc)


__all__ = ["RhDesk", "Stats"]
