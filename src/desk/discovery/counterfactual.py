"""M6-05: outcomes of rejected candidates at +5m/15m/1h/6h/24h, so filters can be judged by what they rejected.

Prices come from same-pool DexScreener observations near each horizon; pools that are no longer observed are
re-fetched once their horizon is due. A pool that cannot be observed in time is recorded as NO_DATA — dead tokens
remain in the dataset (no survivorship). A deterministic 10% sample also gets an executable round-trip quote at
the 1 h horizon, measuring whether the rejected token could actually have been sold.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime
from decimal import Decimal
from typing import Any

from desk.contracts.quotes import QuoteObservation
from desk.core.clock import to_epoch_millis, to_iso
from desk.core.units import sol_to_lamports
from desk.security.policy import SOL_MINT
from desk.storage.market_repo import HAZARD_SOURCE, MarketRepository

HORIZONS = (300, 900, 3600, 21600, 86400)
WINDOW_SECONDS = 150            # an observation within ±150 s of the horizon counts
GRACE_SECONDS = 600             # after this, an unobserved horizon is recorded as NO_DATA
QUOTE_HORIZON = 3600
QUOTE_SAMPLE_PERCENT = 10


def quote_sampled(candidate_id: str) -> bool:
    return int(hashlib.sha256(candidate_id.encode()).hexdigest()[:8], 16) % 100 < QUOTE_SAMPLE_PERCENT


class CounterfactualTracker:
    def __init__(self, suite: Any, conn: sqlite3.Connection, ticket_sol: Decimal) -> None:
        self.suite, self.conn, self.ticket = suite, conn, sol_to_lamports(ticket_sol)
        self.market = MarketRepository(conn)

    def register(self, limit: int = 2000) -> int:
        """Register the first REJECT of each candidate (resumable cursor over append-only stage results)."""
        row = self.conn.execute("SELECT last_id FROM discovery_cursors WHERE stream = 'counterfactual'").fetchone()
        cursor = 0 if row is None else int(row[0])
        rows = self.conn.execute(
            "SELECT r.id, r.candidate_id, r.stage, r.rule_id, r.outcome, r.decided_at, d.mint, d.pool_address "
            "FROM stage_results r JOIN discoveries d ON d.id = r.candidate_id WHERE r.id > ? ORDER BY r.id LIMIT ?",
            (cursor, limit)).fetchall()
        added = 0
        for r in rows:
            if r["outcome"] == "REJECT" and r["pool_address"]:
                at_ms = to_epoch_millis(datetime.fromisoformat(r["decided_at"]))
                price = self.conn.execute(
                    "SELECT price_usd FROM market_snapshots WHERE pool_address = ? AND source = ? AND price_usd IS NOT NULL "
                    "AND observed_at_ms <= ? AND observed_at_ms >= ? ORDER BY observed_at_ms DESC, id DESC LIMIT 1",
                    (r["pool_address"], HAZARD_SOURCE, at_ms, at_ms - 300_000)).fetchone()
                added += self.conn.execute(
                    "INSERT OR IGNORE INTO counterfactuals(candidate_id, mint, pool_address, rule_id, stage, reference_at,"
                    " reference_at_ms, reference_price_usd, quote_sample) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (r["candidate_id"], r["mint"], r["pool_address"], r["rule_id"], r["stage"], r["decided_at"], at_ms,
                     None if price is None else price["price_usd"], int(quote_sampled(r["candidate_id"])))).rowcount
        if rows:
            self.conn.execute("INSERT INTO discovery_cursors(stream, last_id) VALUES ('counterfactual', ?) "
                              "ON CONFLICT(stream) DO UPDATE SET last_id = excluded.last_id", (rows[-1]["id"],))
        return added

    def _due(self, now: datetime) -> list[tuple[sqlite3.Row, int]]:
        now_ms = to_epoch_millis(now)
        due = []
        for horizon in HORIZONS:
            for row in self.conn.execute(
                    "SELECT c.* FROM counterfactuals c WHERE c.reference_at_ms + ? <= ? AND NOT EXISTS ("
                    "SELECT 1 FROM counterfactual_outcomes o WHERE o.candidate_id = c.candidate_id "
                    "AND o.horizon_seconds = ?) ORDER BY c.reference_at_ms LIMIT 3000",
                    (horizon * 1000 - WINDOW_SECONDS * 1000, now_ms, horizon)):
                due.append((row, horizon))
        return due

    def _observation(self, row: sqlite3.Row, horizon: int) -> sqlite3.Row | None:
        target = row["reference_at_ms"] + horizon * 1000
        found: sqlite3.Row | None = self.conn.execute(
            "SELECT * FROM market_snapshots WHERE pool_address = ? AND source = ? AND observed_at_ms BETWEEN ? AND ? "
            "ORDER BY ABS(observed_at_ms - ?), id LIMIT 1",
            (row["pool_address"], HAZARD_SOURCE, target - WINDOW_SECONDS * 1000, target + WINDOW_SECONDS * 1000,
             target)).fetchone()
        return found

    async def _quote_check(self, mint: str) -> dict[str, Any]:
        buy = await self.suite.jupiter.order(SOL_MINT, mint, self.ticket)
        self.market.save_quote(buy)
        if not isinstance(buy, QuoteObservation) or buy.out_amount_raw <= 0:
            return {"executable": False, "reason": "NO_BUY_ROUTE"}
        sell = await self.suite.jupiter.order(mint, SOL_MINT, buy.out_amount_raw)
        self.market.save_quote(sell)
        if not isinstance(sell, QuoteObservation) or sell.out_amount_raw <= 0:
            return {"executable": False, "reason": "NO_SELL_ROUTE"}
        return {"executable": True, "ticket_lamports": self.ticket, "sell_back_lamports": sell.out_amount_raw,
                "roundtrip_cost_fraction": str(Decimal(self.ticket - sell.out_amount_raw) / Decimal(self.ticket))}

    async def run_once(self, now: datetime) -> dict[str, int]:
        self.register()
        stats = {"recorded": 0, "no_data": 0, "fetched_mints": 0, "quote_checks": 0}
        due = self._due(now)
        missing = sorted({row["mint"] for row, h in due if self._observation(row, h) is None
                          and to_epoch_millis(now) <= row["reference_at_ms"] + (h + WINDOW_SECONDS) * 1000})
        for i in range(0, len(missing), 30):
            fetched = await self.suite.dexscreener.tokens(missing[i:i + 30])
            self.market.save_pool_snapshots(fetched.items)
            stats["fetched_mints"] += len(missing[i:i + 30])
        now_ms = to_epoch_millis(now)
        for row, horizon in due:
            observation = self._observation(row, horizon)
            if observation is None:
                if now_ms < row["reference_at_ms"] + (horizon + GRACE_SECONDS) * 1000:
                    continue                       # still time to observe it on a later pass
                self._record(row, horizon, now, None, "NO_DATA", None)
                stats["no_data"] += 1
                continue
            quote = None
            if row["quote_sample"] and horizon == QUOTE_HORIZON:
                quote = await self._quote_check(row["mint"])
                stats["quote_checks"] += 1
            liquidity = observation["liquidity_usd"]
            status = "ZERO_LIQUIDITY" if liquidity is not None and Decimal(liquidity) == 0 else "OK"
            self._record(row, horizon, now, observation, status, quote)
            stats["recorded"] += 1
        return stats

    def _record(self, row: sqlite3.Row, horizon: int, now: datetime, observation: sqlite3.Row | None, status: str,
                quote: dict[str, Any] | None) -> None:
        price = None if observation is None else observation["price_usd"]
        reference = row["reference_price_usd"]
        ret = None
        if price is not None and reference is not None and Decimal(reference) > 0:
            ret = str(Decimal(price) / Decimal(reference) - 1)
        self.conn.execute(
            "INSERT OR IGNORE INTO counterfactual_outcomes(candidate_id, horizon_seconds, recorded_at, observed_at,"
            " price_usd, liquidity_usd, return, status, quote_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (row["candidate_id"], horizon, to_iso(now), None if observation is None else observation["observed_at"], price,
             None if observation is None else observation["liquidity_usd"], ret, status,
             None if quote is None else json.dumps(quote, sort_keys=True)))


def summary(conn: sqlite3.Connection) -> dict[str, Any]:
    """Rejected-candidate outcomes by horizon and rule: did the filters reject winners or losers?"""
    out: dict[str, Any] = {"candidates": conn.execute("SELECT COUNT(*) FROM counterfactuals").fetchone()[0], "by_horizon": {}}
    for horizon in HORIZONS:
        rows = conn.execute("SELECT status, return FROM counterfactual_outcomes WHERE horizon_seconds = ?", (horizon,)).fetchall()
        returns = [Decimal(r["return"]) for r in rows if r["return"] is not None]
        out["by_horizon"][str(horizon)] = {
            "recorded": len(rows), "no_data": sum(r["status"] == "NO_DATA" for r in rows),
            "zero_liquidity": sum(r["status"] == "ZERO_LIQUIDITY" for r in rows),
            "median_return": None if not returns else str(sorted(returns)[len(returns) // 2]),
            "share_up_50pct": None if not returns else str(Decimal(sum(r > Decimal("0.5") for r in returns)) / len(returns))}
    return out
