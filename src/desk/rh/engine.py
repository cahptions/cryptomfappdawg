"""Robinhood PAPER: continuous multi-timeframe checks, legacy daily maintenance and quoted hard stops.

* Experimental v2: current closed 15m candles trigger checks using daily regime, 4h trend and hourly setups.
* Daily maintenance (after 00:05 UTC): legacy v1 exits, original baselines and the archived daily backtest.
  Daily maintenance does not gate v2 entries. Every entry, exit and blocked signal is recorded.
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
import math
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from decimal import ROUND_FLOOR, Decimal
from typing import Any

import numpy as np

from desk.core.clock import utc_now
from desk.core.config import LoadedConfig
from desk.core.errors import DeskError
from desk.core.hashing import canonical_json, sha256_hex
from desk.core.paths import DeskPaths
from desk.rh import learn, learning
from desk.rh import multiframe as mf
from desk.rh import strategies as st
from desk.rh.contracts import Estimate, Pair, Route
from desk.rh.history import DAILY, HOURLY, sync_candles, today_start, universe, write_backtest
from desk.rh.repo import Position, RhDatabases, RhLedger, RhMarket
from desk.rh.suite import RhSuite
from desk.storage.db import StorageError

log = logging.getLogger(__name__)
SESSION_DELAY = timedelta(minutes=5)          # Coinbase publishes the finished daily bar shortly after 00:00 UTC
CONSERVATIVE_WAIT_SECONDS = 2.5
HISTORY_INTERVAL_SECONDS = 15
PAIRS_INTERVAL_SECONDS = 3600
PROBE_INTERVAL_SECONDS = 60
SESSION_CHECK_SECONDS = 60
EQUITY_INTERVAL_SECONDS = 300
HOURLY_CHART_DAYS = 14
LEARNING_INTERVAL_SECONDS = 3600


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
        self._history_cursor = 0
        self._history_due: dict[tuple[str, int], datetime] = {}
        self._daily_attempted: dict[str, date] = {}
        self._exiting: set[str] = set()
        self._backfilled = False
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
        # Rotate six assets per pass; refresh the benchmark first. Quotes/stops and decisions run in
        # independent tasks, including while initial public history downloads are still in progress.
        others = [s for s in symbols if s != "BTC-USD"]
        batch = ["BTC-USD"] + [others[(self._history_cursor + i) % len(others)]
                                  for i in range(min(6, len(others)))] if others else ["BTC-USD"]
        self._history_cursor += 6
        for symbol in batch:
            for granularity, days in ((DAILY, 240), (HOURLY, HOURLY_CHART_DAYS), (mf.QUARTER, 3)):
                now = self.clock()
                end = mf.floor_time(now - mf.GRACE, granularity)
                key = (symbol, granularity)
                if self._history_due.get(key, now) > now:
                    continue
                report = await sync_candles(self.suite.coinbase, self.market, [symbol], granularity=granularity,
                                           days=days, now=now - mf.GRACE, clock=self.clock)
                if granularity == DAILY:
                    self._daily_attempted[symbol] = now.date()
                if report["errors"]:
                    self._error("history", DeskError(str(report["errors"])))
                # Complete series need no polling until their next close; missing products retry hourly.
                last = self.market.last_candle_start(symbol, granularity)
                complete = last is not None and last + timedelta(seconds=granularity) >= end
                self._history_due[key] = (end + timedelta(seconds=granularity) + mf.GRACE if complete else
                                          now + timedelta(seconds=3600 if report["missing_products"] else 60))

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
        # Rotating history must not select a weekly baseline or finish legacy exits against only
        # the first batch of assets. Each missing/stale daily series gets at least one sync attempt.
        # Unsupported/erroring sources are then explicit ineligible inputs, rather than a global lock.
        for symbol in self.symbols()[0]:
            latest = self.market.last_candle_start(symbol, DAILY)
            if (latest is None or latest.date() != day - timedelta(days=1)) and self._daily_attempted.get(symbol) != day:
                return None
        return day if last is not None and last.date() == day - timedelta(days=1) else None

    async def daily_session(self) -> None:
        now = self.clock()
        day = self.session_ready(now)
        if day is None:
            return
        snaps, eligible, reg, why = self.state(day)
        open_ = [p for p in self.ledger.positions("OPEN")
                 if p.strategy_id in st.SPECS and p.spec_sha256 == st.spec_sha256(p.strategy_id)]
        for pos in open_:
            if pos.symbol in snaps:
                self.ledger.update_peak(pos.id, snaps[pos.symbol].close)
        open_ = [p for p in self.ledger.positions("OPEN")
                 if p.strategy_id in st.SPECS and p.spec_sha256 == st.spec_sha256(p.strategy_id)]
        held = [st.Held(p.id, p.strategy_id, p.symbol, p.session_day, p.peak_close) for p in open_]
        for signal in st.exits(day, held, snaps, eligible, reg):
            pos = next(p for p in open_ if p.id == signal.position_id)
            self.market.record_decision(now, day, signal, "EXIT", signal.reasons, {"regime": reg})
            await self.exit(pos, signal.reasons[0])
        held = [st.Held(p.id, p.strategy_id, p.symbol, p.session_day, p.peak_close)
                for p in self.ledger.positions("OPEN")]
        for signal in st.entries(day, eligible, reg, held):
            if signal.strategy_id.startswith("B"):
                await self.enter(signal, day, eligible[signal.symbol], reg)
        await self.learned_session(day, now)
        self.ledger.event(now, "SESSION", {"day": day.isoformat(), "regime": reg, "eligible": len(eligible),
                                           "ineligible": why, "open_positions": len(self.ledger.positions("OPEN"))})
        self.stats.sessions += 1
        try:
            await asyncio.to_thread(write_backtest, self.paths, self.loaded, now)
        except (DeskError, StorageError, OSError, ArithmeticError) as exc:
            self._error("backtest", exc)

    async def session(self) -> None:
        """New PAPER decisions on fresh 15m closes; the old daily session maintains baselines/legacy exits."""
        # Do not make a historical backtest (daily maintenance) delay a current entry or exit check.
        await self.intraday_session()
        await self.daily_session()

    async def intraday_session(self) -> None:
        now = self.clock()
        symbols, _ = self.symbols()
        states = {s: mf.load(self.market, s, now) for s in symbols}
        btc = states.get("BTC-USD")
        reg = mf.regime(btc)
        # Time limits and available regime exits still run if an asset's candles are missing.
        for pos in self.ledger.positions("OPEN"):
            if pos.strategy_id not in mf.IDS or pos.spec_sha256 != mf.spec_sha256(pos.strategy_id):
                continue
            state = states.get(pos.symbol)
            if state and state.ready:
                self.ledger.update_peak(pos.id, Decimal(state.features["close"]))
            reason = mf.exit_reason(pos, state, reg, btc, now)
            if reason:
                signal = st.Signal(pos.strategy_id, pos.symbol, "EXIT", (reason,),
                                   state.features if state else {"spec_version": "2"}, pos.id)
                self.market.record_decision(now, now.date(), signal, "EXIT", signal.reasons, {"regime": reg})
                await self.exit(pos, reason)
        checked = set()
        ready = [s for s, state in states.items() if state.ready]
        unavailable = {s: list(state.missing) for s, state in states.items() if not state.ready}
        for symbol, state in states.items():
            # Wait for required benchmark data too. A late download may qualify on a later poll of the
            # same candle, but an already-claimed check is never replayed after restart.
            if not state.ready or reg == "UNKNOWN":
                continue
            if self.market.claim_frame_check(state, now, {
                    "regime": reg, "features": state.features, "frames": state.frames,
                    "ready_universe": ready, "unavailable": unavailable,
                    "universe_inputs": {s: other.input_hash for s, other in states.items()},
                    "conditions": mf.conditions(state, states, reg)}):
                checked.add(symbol)
        allowed = {(sid, symbol) for sid in mf.IDS for symbol in checked if not self.market.setup_entered(
            st.Signal(sid, symbol, "ENTER", (), states[symbol].features))}
        universe_hash = sha256_hex(canonical_json({s: state.input_hash for s, state in states.items()}))
        for signal in mf.entries(states, reg, self.ledger.positions("OPEN"), allowed=allowed):
            signal = replace(signal, features={**signal.features, "setup_end": mf.floor_time(
                states[signal.symbol].bar_end, mf.HOUR).isoformat(), "universe_input_hash": universe_hash})
            await self.enter(signal, now.date(), states[signal.symbol], reg)
        if checked:
            self.ledger.event(now, "FRAME_SESSION", {"day": now.date().isoformat(), "regime": reg,
                              "bar_end": mf.cutoff(now).isoformat(), "eligible": len(ready),
                              "checked": sorted(checked), "ineligible": {s: ",".join(v) for s, v in unavailable.items()},
                              "open_positions": len(self.ledger.positions("OPEN"))})
            self.stats.sessions += 1

    # ── the learning lab ─────────────────────────────────────────────────────────────────────────
    async def learning_step(self) -> None:
        """Download the lab's years of daily history once per start, then run the lab when a month is due."""
        if self.settings.learning == "off":
            return
        now = self.clock()
        if not self._backfilled:
            symbols, _ = self.symbols()
            report = await learning.backfill_daily(self.suite.coinbase, self.market, symbols, now)
            self._backfilled = True
            self.ledger.event(now, "LEARNING_HISTORY", report)
        if learning.due(self.market.conn, now):
            run = await asyncio.to_thread(_learn_in_thread, self.paths, self.loaded, now)
            self.ledger.event(self.clock(), "LEARNING_RUN", {"run_id": run.id, "data_end": run.data_end.isoformat(),
                                                             "promoted": run.promoted})

    async def learned_session(self, day: date, now: datetime) -> None:
        """Daily: exit learned positions by the rule that opened them; enter only for a promoted, unbenched run."""
        if self.settings.learning != "paper":
            return
        run = learning.latest(self.market.conn)
        open_l1 = [p for p in self.ledger.positions("OPEN") if p.strategy_id == learn.STRATEGY_ID]
        if run is None and not open_l1:
            return
        since = today_start(now) - timedelta(days=430)
        symbols, _ = self.symbols()
        history = {s: self.market.candles(s, DAILY, since=since, as_of=now) for s in symbols}
        try:
            panel = learn.build_panel(history, until=day - timedelta(days=1))
        except ValueError:
            return
        t = len(panel.days) - 1
        if panel.days[t] != day - timedelta(days=1):
            return                                       # yesterday's bar is not in yet: decide on a later poll
        index = {sym: i for i, sym in enumerate(panel.symbols)}
        for pos in open_l1:
            s = index.get(pos.symbol)
            if s is None or math.isnan(panel.c[s, t]):
                continue
            close = Decimal(repr(float(panel.c[s, t])))
            self.ledger.update_peak(pos.id, close)
            reason = learned_exit(pos, panel, s, t)
            if reason:
                signal = st.Signal(pos.strategy_id, pos.symbol, "EXIT", (reason,), dict(pos.plan.get("features", {})),
                                   pos.id)
                self.market.record_decision(now, day, signal, "EXIT", signal.reasons, {"learning_run": run and run.id})
                await self.exit(pos, reason)
        if run is None or not run.promoted or learning.forward(self.ledger, run.selection_sha256)["benched"]:
            return
        held = {p.symbol for p in self.ledger.positions("OPEN") if p.strategy_id == learn.STRATEGY_ID}
        for cid, s, fraction in learned_picks(run, panel, t):
            symbol = panel.symbols[s]
            if symbol in held or len(held) >= learn.MAX_POSITIONS or math.isnan(fraction):
                continue
            signal = st.Signal(learn.STRATEGY_ID, symbol, "ENTER", ("LEARNED", str(run.promoted)), {
                "learned_spec": run.selection_sha256, "learned_candidate": cid, "learned_procedure": run.promoted,
                "learning_run": str(run.id), "stop_fraction": f"{fraction:.6f}"})
            before = len(self.ledger.positions("OPEN"))
            await self.enter(signal, day, _LearnedStop(Decimal(f"{fraction:.6f}")), "LEARNED")
            if len(self.ledger.positions("OPEN")) > before:
                held.add(symbol)

    def _skip(self, now: datetime, day: date, signal: st.Signal, reasons: list[str], inputs: dict[str, Any]) -> None:
        self.market.record_decision(now, day, signal, "SKIP", [*signal.reasons, *reasons], inputs)
        self.stats.skips += 1

    async def enter(self, signal: st.Signal, day: date, snap: st.Snapshot | mf.FrameState | _LearnedStop,
                    reg: str) -> None:
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
        if quote is None or not timedelta(0) <= now - quote.received_at <= timedelta(
                seconds=3 * self.settings.quote_interval_seconds):
            blocks.append("STALE_QUOTE")
        if isinstance(snap, mf.FrameState) and mf.cutoff(now) != snap.bar_end:
            blocks.append("STALE_CANDLE_CHECK")
        if blocks or pair is None or quote is None:
            self._skip(now, day, signal, blocks, inputs)
            return
        ticket = self.ticket()
        if ticket <= 0:
            self._skip(now, day, signal, ["NO_RISK_CAPITAL"], inputs)
            return
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
        # Awaited estimates can outlive an operator STOP, an exit/halt, or a candle boundary.
        final_blocks = []
        if self.paths.stop_file.exists() or self.ledger.halted():
            final_blocks.append("ENTRY_STOPPED_DURING_ESTIMATE")
        if not second or not self._fresh([buy]):
            final_blocks.append("NO_FRESH_SECOND_ESTIMATE")
        roundtrip = (buy.total - max(e.total for e in sells)) / buy.total
        if roundtrip > self.settings.max_roundtrip_cost_fraction:
            final_blocks.append("ROUNDTRIP_COST_ABOVE_CEILING")
        if buy.total > (self.settings.starting_capital_usd + self.ledger.realized()) * self.settings.max_ticket_fraction:
            final_blocks.append("ACCOUNT_RELATIVE_TICKET_CAP")
        if isinstance(snap, mf.FrameState) and mf.cutoff(self.clock()) != snap.bar_end:
            final_blocks.append("STALE_CANDLE_CHECK")
        if final_blocks:
            self._skip(self.clock(), day, signal, final_blocks, inputs)
            return
        stop = None if signal.strategy_id == "B1_HOLD_BTC" else buy.unit_price * (1 - snap.stop_fraction)
        digest = self.market.record_decision(self.clock(), day, signal, "ENTER", signal.reasons, inputs)
        try:
            self.ledger.open_position(at=self.clock(), day=day, strategy_id=signal.strategy_id, symbol=signal.symbol,
                                      decision_hash=digest, buy=buy, roundtrip=roundtrip, stop_price=stop,
                                      plan={"features": signal.features, "stop_fraction": str(snap.stop_fraction)},
                                      spec_sha256=mf.signal_spec(signal))
        except StorageError as exc:
            self._skip(now, day, signal, [f"LEDGER:{exc}"[:80]], inputs)
            return
        self.stats.entries += 1

    async def exit(self, pos: Position, reason: str) -> bool:
        if pos.id not in {p.id for p in self.ledger.positions("OPEN")}:
            return True
        if pos.id in self._exiting:
            return False
        self._exiting.add(pos.id)
        try:
            return await self._execute_exit(pos, reason)
        finally:
            self._exiting.discard(pos.id)

    async def _execute_exit(self, pos: Position, reason: str) -> bool:
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
        self.ledger.sync_reference_capital(self.settings.starting_capital_usd, now)
        stop = asyncio.Event()
        await self._first_pass()
        jobs = [("pairs", PAIRS_INTERVAL_SECONDS, self.refresh_pairs),
                ("history", HISTORY_INTERVAL_SECONDS, self.sync_history),
                ("quotes", self.settings.quote_interval_seconds, self.poll_quotes),
                ("probes", PROBE_INTERVAL_SECONDS, self.probe_costs),
                ("session", SESSION_CHECK_SECONDS, self.session),
                ("equity", EQUITY_INTERVAL_SECONDS, self.snapshot_equity),
                ("learning", LEARNING_INTERVAL_SECONDS, self.learning_step)]
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
        for name, job in (("pairs", self.refresh_pairs), ("quotes", self.poll_quotes)):
            try:
                await job()
            except (DeskError, StorageError, ArithmeticError, KeyError, ValueError) as exc:
                self._error(name, exc)


@dataclass(frozen=True)
class _LearnedStop:
    stop_fraction: Decimal


def _learn_in_thread(paths: DeskPaths, loaded: LoadedConfig, now: datetime) -> learning.Run:
    dbs = RhDatabases.open(paths)                        # its own connections: SQLite objects stay in their thread
    try:
        return learning.run_and_record(RhMarket(dbs.market), loaded, now)
    finally:
        dbs.close()


def learned_exit(pos: Position, panel: learn.Panel, s: int, t: int) -> str | None:
    """The exit rule of the variant (or model) that opened the position, evaluated at the close of day t."""
    cid = str(pos.plan.get("features", {}).get("learned_candidate", ""))
    entry_t = (pos.session_day - panel.days[0]).days
    peak = np.array([max(float(pos.peak_close), float(panel.c[s, t]))])
    if cid == "MODEL":
        family, params = "MODEL", {}
    else:
        try:
            cand = learn.candidate_by_id(cid)
        except KeyError:
            return None
        if cand.family == "XMOM":
            every = int(cand.p("every", 1))
            if learn.rebalance_day(panel, t, every) and s not in learn.xmom_targets(cand, panel, t):
                return "REBALANCED_OUT"
            return None
        family, params = cand.family, dict(cand.params)
    code = int(learn.exit_codes(family, params, panel, s, t, t + 1, entry_t, peak)[0])
    return learn.EXIT_CODES[code] if code else None


def learned_picks(run: learning.Run, panel: learn.Panel, t: int) -> list[tuple[str, int, float]]:
    """What the promoted procedure buys at the open after the close of day t: (variant id, coin index, stop)."""
    selection = run.report.get("selection", {})
    picks: list[tuple[str, int, float]] = []
    if run.promoted == "LEARN_MODEL":
        model = learn.model_from(selection)
        if model is not None and learn.rebalance_day(panel, t):
            picks = [("MODEL", s, float(panel.f["stop_frac"][s, t])) for s in learn.model_picks(model, panel, t)]
        return picks
    for cid in selection.get("best" if run.promoted == "LEARN_BEST" else "top3", []):
        cand = learn.candidate_by_id(cid)
        if cand.family == "XMOM":
            if learn.rebalance_day(panel, t, int(cand.p("every", 1))):
                picks += [(cid, s, float(panel.f["stop_frac"][s, t])) for s in learn.xmom_targets(cand, panel, t)]
            continue
        for s in np.nonzero(learn.entry_mask(cand, panel)[:, t])[0]:
            picks.append((cid, int(s), learn.stop_fraction(cand, panel, int(s), t)))
    return picks


__all__ = ["RhDesk", "Stats", "learned_exit", "learned_picks"]
