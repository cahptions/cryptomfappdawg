"""Independent PAPER guardian (PLAN §13.3): its own process, provider clients and loop.

Every loop it writes a heartbeat, refreshes executable exit quotes that are older than the mark interval,
evaluates the eight exit policies on fresh executable marks only, and executes exits with fresh quotes.
It keeps supervising while the entry engine is down. Failed exits are retried; after three consecutive
failures the position is EXIT_BLOCKED (critical alert, lock retained) and retried on a slower schedule.
"""

from __future__ import annotations

import asyncio
import contextlib
import fcntl
import json
import logging
import os
import time
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from typing import Any

from desk.contracts.observed import DataStatus
from desk.contracts.quotes import QuoteObservation, QuoteUnavailable
from desk.contracts.strategy import StrategySpec
from desk.core.clock import parse_iso8601, to_iso, utc_now
from desk.core.config import LoadedConfig
from desk.core.errors import DeskError, ProviderError
from desk.features.builder import FeatureBuilder
from desk.paper import fees as paper_fees
from desk.paper.engine import execution_model, heartbeat_age
from desk.paper.executor import MAX_ATTEMPTS, exit_
from desk.paper.ledger import PaperLedger, profile_pnl
from desk.paper.model import ExitInputs, Profiles, exit_reason, impaired
from desk.providers.factory import ProviderSuite
from desk.security.policy import SOL_MINT
from desk.storage.databases import Databases
from desk.storage.market_repo import MarketRepository
from desk.strategies.specs import paper_specs

log = logging.getLogger(__name__)
BLOCKED_RETRY_SECONDS = 60
IDLE_EXIT_SECONDS = 300          # stop when nothing is open and the entry engine has been gone this long


def _feature(features: Any, name: str, *, allow_unverified: bool = False) -> Decimal | None:
    value = features.values.get(name)
    if value is None or value.value is None:
        return None
    ok = (DataStatus.OK, DataStatus.UNVERIFIED) if allow_unverified else (DataStatus.OK,)
    return value.value if value.status in ok else None


class Guardian:
    heartbeat_name = "guardian"
    lock_name = "paper-guardian.lock"
    display_name = "independent PAPER guardian"

    def __init__(self, suite: ProviderSuite, dbs: Databases, loaded: LoadedConfig, *,
                 clock: Callable[[], datetime] = utc_now, sleep: Callable[[float], Any] = asyncio.sleep) -> None:
        self.suite, self.dbs, self.loaded, self.clock, self.sleep = suite, dbs, loaded, clock, sleep
        self.ledger = PaperLedger(dbs.paper_ledger)
        self.market = MarketRepository(dbs.market)
        self.execution = execution_model(loaded)
        self.specs: dict[str, StrategySpec] = {spec.version_id: spec for spec in paper_specs()}
        self.paper = loaded.settings.paper
        self._stop = asyncio.Event()
        self._force_remark = True      # after start or a suspend/resume jump, re-quote before any exit decision
        self.stats: dict[str, int] = {"loops": 0, "marks": 0, "no_route": 0, "exits": 0, "exit_failures": 0,
                                      "suspend_gaps": 0, "provider_errors": 0, "unhandled_exceptions": 0}

    def stop(self) -> None:
        self._stop.set()

    def heartbeat(self) -> None:
        self.dbs.runtime.execute(
            "INSERT INTO heartbeats(process, pid, at) VALUES (?, ?, ?) "
            "ON CONFLICT(process) DO UPDATE SET pid = excluded.pid, at = excluded.at",
            (self.heartbeat_name, os.getpid(), to_iso(self.clock())))

    async def _quote(self, input_mint: str, output_mint: str, amount: int) -> QuoteObservation | QuoteUnavailable:
        quote = await self.suite.jupiter.order(input_mint, output_mint, amount)
        self.market.save_quote(quote)
        return quote

    # ── one position ──────────────────────────────────────────────────────────────────────────────
    async def mark(self, row: Any, now: datetime) -> None:
        quote = await self._quote(row["mint"], SOL_MINT, row["tokens_raw"])
        value = quote.out_amount_raw if isinstance(quote, QuoteObservation) and quote.out_amount_raw > 0 else None
        sha = quote.raw_sha256 if isinstance(quote, QuoteObservation) else None
        self.ledger.record_mark(row["id"], now, "EXECUTABLE" if value is not None else "NO_ROUTE", value, sha)
        self.stats["marks" if value is not None else "no_route"] += 1
        without_route = (now - parse_iso8601(row["last_route_at"])).total_seconds() if row["last_route_at"] else 0.0
        if row["state"] == "OPEN" and impaired(row["last_mark_lamports"], value, without_route):
            self.ledger.set_state(row["id"], "IMPAIRED", now, {"reason": "IMPAIRED", "value_lamports": value,
                                                                "last_mark_lamports": row["last_mark_lamports"],
                                                                "seconds_without_route": without_route})
            log.critical("paper position impaired", extra={"position_id": row["id"]})

    def _security_rejected(self, row: Any, now: datetime) -> bool:
        latest = self.dbs.market.execute(
            "SELECT outcome FROM stage_results WHERE candidate_id = ? AND stage = 4 AND decided_at > ? AND decided_at <= ?"
            " ORDER BY decided_at DESC, id DESC LIMIT 1", (row["candidate_id"], row["opened_at"], to_iso(now))).fetchone()
        return latest is not None and latest["outcome"] == "REJECT"

    def exit_decision(self, row: Any, now: datetime) -> str | None:
        spec = self.specs.get(row["strategy_version"])
        if spec is None:
            return "STRATEGY_INVALIDATED"   # a position whose specification is unknown is not kept by default
        entry = json.loads(row["entry_json"])
        features = FeatureBuilder(self.dbs.market).build(row["mint"], row["pool_address"], now)
        fresh = (row["last_mark_kind"] == "EXECUTABLE" and row["last_mark_at"] is not None
                 and (now - parse_iso8601(row["last_mark_at"])).total_seconds() <= 2 * self.paper.mark_interval_seconds)
        entry_liquidity = entry.get("entry_reported_liquidity_usd")
        facts = ExitInputs(
            ticket_lamports=row["ticket_lamports"], value_lamports=row["last_mark_lamports"] if fresh else None,
            peak_value_lamports=row["peak_value_lamports"], opened_at=parse_iso8601(row["opened_at"]), now=now,
            security_rejected=self._security_rejected(row, now),
            liquidity_usd=_feature(features, "reported_liquidity_usd"),
            entry_liquidity_usd=None if entry_liquidity in (None, "None") else Decimal(entry_liquidity),
            sol_return_1h=_feature(features, "sol_return_1h"),
            # exits may act on an unverified decay estimate: exiting is the cautious direction
            volume_decay=_feature(features, "volume_decay_adj", allow_unverified=True),
            features={name: v for name in features.values if (v := _feature(features, name)) is not None})
        return exit_reason(spec, facts)

    async def try_exit(self, row: Any, reason: str, now: datetime) -> None:
        entry = json.loads(row["entry_json"])["profiles"]
        profiles = Profiles(observed=entry["observed"], conservative=entry["conservative"], stress=entry["stress"])
        attempt = int(row["exit_attempts"])
        if row["state"] != "EXITING":
            self.ledger.set_state(row["id"], "EXITING", now, {"reason": reason, "attempts": attempt})
        estimate = paper_fees.estimate(self.dbs.market, self.loaded, now)
        result = await exit_(replace(self.execution, fees=estimate.model), self._quote, self.sleep,
                             intent_id=row["intent_id"], mint=row["mint"], entry=profiles, attempt=attempt)
        done = self.clock()
        if result.status == "FILLED" and result.profiles is not None:
            stored = json.loads(row["entry_json"])
            failed = int(row["exit_failed_fees_lamports"])
            # the rent comes back only through a separate close-account transaction, which also pays a fee
            close_fee = estimate.per_transaction
            exit_fees = result.fee_lamports + close_fee
            fees = {"observed": row["entry_fees_lamports"] + exit_fees,
                    "conservative": row["entry_fees_lamports"] + exit_fees + failed,
                    "stress": row["entry_fees_lamports"] + exit_fees + failed
                    + int(stored.get("stress_extra_fees_lamports", 0)) + result.stress_extra_fees_lamports}
            pnl = profile_pnl(profiles, result.profiles, row["ticket_lamports"], fees)
            pnl["exit_quotes"] = result.quotes
            pnl["fee_breakdown"] = {
                "entry_tx_lamports": row["entry_fees_lamports"], "sell_tx_lamports": result.fee_lamports,
                "close_account_tx_lamports": close_fee, "failed_exit_tx_lamports": failed,
                "rent_locked_and_refunded_lamports": row["rent_lamports"], "exit_fee_source": estimate.source,
                "exit_fee_observations": estimate.observations,
                "platform_fee_bps_exit": next((q.get("fee_bps") for q in result.quotes if q.get("fee_bps") is not None), None),
                "platform_fee_included_in_quote": True}
            realized = self.ledger.close_position(row["id"], done, reason=reason, exit_lamports=result.profiles.conservative,
                                                  exit_fees_lamports=exit_fees, profiles=pnl)
            self.stats["exits"] += 1
            log.info("paper position closed", extra={"position_id": row["id"], "reason": reason,
                                                      "realized_pnl_lamports": realized})
            return
        self.stats["exit_failures"] += 1
        attempts = attempt + 1
        failed_fee = result.fee_lamports if result.status == "TX_FAILED" else 0       # estimate already applied
        state = "EXIT_BLOCKED" if attempts >= MAX_ATTEMPTS else "EXITING"
        self.ledger.set_state(row["id"], state, done, {"reason": reason, "attempts": attempts, "status": result.status,
                                                        "quotes": result.quotes}, failed_fee_lamports=failed_fee)
        self.ledger.event(done, "EXIT_FAILED", {"reason": reason, "attempts": attempts, "status": result.status},
                          position_id=row["id"])
        if state == "EXIT_BLOCKED":
            log.critical("paper exit blocked; position remains supervised", extra={"position_id": row["id"]})

    async def supervise(self, row: Any, now: datetime) -> None:
        last = parse_iso8601(row["last_mark_at"]) if row["last_mark_at"] else None
        if self._force_remark or last is None or (now - last).total_seconds() >= self.paper.mark_interval_seconds:
            await self.mark(row, now)
            row = self.ledger.position(row["id"])
        if row["state"] == "EXIT_BLOCKED":
            # Marks have their own cadence and may refresh forever while an exit remains blocked.
            # Throttle from the persisted attempt, never from the continually refreshed mark.
            attempt = self.dbs.paper_ledger.execute(
                "SELECT at FROM paper_events WHERE position_id = ? AND kind IN ('EXIT_FAILED', 'EXIT_BLOCKED') "
                "ORDER BY id DESC LIMIT 1", (row["id"],)).fetchone()
            if attempt is not None and (now - parse_iso8601(attempt["at"])).total_seconds() < BLOCKED_RETRY_SECONDS:
                return
            await self.try_exit(row, row["exit_reason"] or "RETRY", now)
            return
        reason = row["exit_reason"] if row["state"] in ("EXITING", "IMPAIRED") and row["exit_reason"] not in (None, "IMPAIRED") \
            else self.exit_decision(row, now)
        if row["state"] == "IMPAIRED" and reason is None:
            reason = "IMPAIRED"     # impaired positions keep attempting exits on the bounded schedule
        if reason is not None:
            await self.try_exit(row, reason, now)

    # ── loop ──────────────────────────────────────────────────────────────────────────────────────
    async def tick(self) -> None:
        self.heartbeat()
        now = self.clock()
        for row in self.ledger.open_positions():
            try:
                await self.supervise(row, now)
            except ProviderError as exc:
                # provider trouble is not a missing route; the position is re-quoted on the next loop
                self.stats["provider_errors"] += 1
                log.warning("guardian quote failed", extra={"position_id": row["id"], "error": str(exc)[:200]})
            except Exception:
                # One failing position must not stop supervision of the others; it is retried next loop.
                self.stats["unhandled_exceptions"] += 1
                log.exception("guardian error while supervising", extra={"position_id": row["id"]})
            self.heartbeat()
        self._force_remark = False
        self.stats["loops"] += 1

    async def run(self, minutes: float | None = None) -> dict[str, int]:
        # The kernel releases this lock on normal exit and SIGKILL. A heartbeat alone cannot
        # prevent simultaneous starts or distinguish a paused existing guardian from a dead one.
        self.dbs.paths.home.mkdir(parents=True, exist_ok=True)
        with (self.dbs.paths.home / self.lock_name).open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise DeskError(f"The {self.display_name} is already running for this data directory") from exc
            return await self._run(minutes)

    async def _run(self, minutes: float | None = None) -> dict[str, int]:
        started = time.monotonic()
        while not self._stop.is_set():
            wall, mono = time.time(), time.monotonic()
            await self.tick()
            if minutes is not None and time.monotonic() - started >= minutes * 60:
                break
            engine_age = heartbeat_age(self.dbs.runtime, "collector", self.clock())
            # grace period: the entry engine that started this process writes its first heartbeat after start-up
            if (minutes is None and time.monotonic() - started >= IDLE_EXIT_SECONDS and not self.ledger.open_positions()
                    and (engine_age is None or engine_age > IDLE_EXIT_SECONDS)):
                log.info("guardian idle: no open positions and no entry engine")
                break
            elapsed = time.monotonic() - mono
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stop.wait(), timeout=max(0.0, self.paper.guardian_loop_seconds - elapsed))
            if (time.time() - wall) - (time.monotonic() - mono) > 30:
                self.stats["suspend_gaps"] += 1
                self._force_remark = True      # marks before the gap are stale; re-quote before deciding
                log.warning("guardian suspend/resume gap; re-quoting all positions before any exit decision")
        return self.stats
