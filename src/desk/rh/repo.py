"""Robinhood desk storage: robinhood-market.db (observations, specs, decisions) and the PAPER ledger."""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from typing import Any, cast

from desk.core.clock import parse_iso8601, to_epoch_millis, to_iso
from desk.core.hashing import canonical_json, sha256_hex
from desk.core.paths import DeskPaths
from desk.rh import multiframe as mf
from desk.rh import strategies as st
from desk.rh.contracts import BidAsk, Candle, Estimate, Pair
from desk.storage.db import DbKind, StorageError, connect, integrity_check, journal_mode, migrate, transaction

MARKET_FILE = "robinhood-market.db"
LEDGER_FILE = "robinhood-ledger-paper.db"
MICRO = Decimal("0.000001")


def to_micros(usd: Decimal, *, cost: bool) -> int:
    """Costs round up and credits round down, so rounding never flatters the book."""
    return int((usd / MICRO).quantize(Decimal(1), rounding=ROUND_CEILING if cost else ROUND_FLOOR))


def usd(micros: int) -> Decimal:
    return Decimal(micros) * MICRO


@dataclass
class RhDatabases:
    market: sqlite3.Connection
    ledger: sqlite3.Connection
    runtime: sqlite3.Connection

    @classmethod
    def open(cls, paths: DeskPaths) -> RhDatabases:
        paths.ensure()
        market = connect(paths.db_dir / MARKET_FILE, DbKind.RH_MARKET)
        ledger = connect(paths.db_dir / LEDGER_FILE, DbKind.RH_LEDGER)
        runtime = connect(paths.db_dir / "runtime.db", DbKind.RUNTIME)
        migrate(market, DbKind.RH_MARKET)
        migrate(ledger, DbKind.RH_LEDGER)
        migrate(runtime, DbKind.RUNTIME)
        row = ledger.execute("SELECT value FROM ledger_meta WHERE key = 'mode'").fetchone()
        if row is None:
            ledger.execute("INSERT INTO ledger_meta(key, value) VALUES ('mode', 'PAPER')")
        elif row["value"] != "PAPER":
            raise StorageError("the Robinhood ledger is PAPER only")
        return cls(market, ledger, runtime)

    def health(self) -> dict[str, dict[str, str]]:
        return {name: {"integrity": integrity_check(conn), "journal_mode": journal_mode(conn)}
                for name, conn in (("robinhood-market", self.market), ("robinhood-ledger-paper", self.ledger))}

    def close(self) -> None:
        for conn in (self.market, self.ledger, self.runtime):
            conn.close()


# ── market ──────────────────────────────────────────────────────────────────────────────────────
class RhMarket:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def register_specs(self, at: datetime) -> None:
        with transaction(self.conn):
            for sid, spec, content, digest in [
                    (sid, module.SPECS[sid], module.spec_content(sid), module.spec_sha256(sid))
                    for module in (st, mf) for sid in module.SPECS]:
                existing = self.conn.execute("SELECT spec_sha256 FROM rh_specs WHERE strategy_id = ? AND version = ?",
                                             (sid, spec["version"])).fetchone()
                if existing is not None and existing["spec_sha256"] != digest:
                    raise StorageError(f"{sid} v{spec['version']} changed without a new version")
                self.conn.execute("INSERT OR IGNORE INTO rh_specs(spec_sha256, strategy_id, version, registered_at,"
                                  " content_json) VALUES (?, ?, ?, ?, ?)",
                                  (digest, sid, spec["version"], to_iso(at), canonical_json(content)))

    def save_pairs(self, pairs: Iterable[Pair], at: datetime) -> int:
        changed = 0
        with transaction(self.conn):
            for p in pairs:
                content = {"asset_code": p.asset_code, "quote_code": p.quote_code, "asset_increment": str(p.asset_increment),
                           "quote_increment": str(p.quote_increment), "max_order_size": str(p.max_order_size),
                           "min_order_amount": None if p.min_order_amount is None else str(p.min_order_amount),
                           "status": p.status, "is_api_tradable": p.is_api_tradable}
                old = self.conn.execute("SELECT * FROM rh_pairs WHERE symbol = ?", (p.symbol,)).fetchone()
                stored = None if old is None else {**{k: old[k] for k in content},
                                                   "is_api_tradable": bool(old["is_api_tradable"])}
                if stored != content:
                    self.conn.execute("INSERT INTO rh_pair_events(symbol, at, content_json) VALUES (?, ?, ?)",
                                      (p.symbol, to_iso(at), canonical_json(content)))
                    changed += 1
                self.conn.execute(
                    "INSERT INTO rh_pairs(symbol, asset_code, quote_code, asset_increment, quote_increment, max_order_size,"
                    " min_order_amount, status, is_api_tradable, first_seen_at, last_seen_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(symbol) DO UPDATE SET"
                    " asset_code = excluded.asset_code, quote_code = excluded.quote_code,"
                    " asset_increment = excluded.asset_increment, quote_increment = excluded.quote_increment,"
                    " max_order_size = excluded.max_order_size, min_order_amount = excluded.min_order_amount,"
                    " status = excluded.status, is_api_tradable = excluded.is_api_tradable,"
                    " last_seen_at = excluded.last_seen_at",
                    (p.symbol, p.asset_code, p.quote_code, str(p.asset_increment), str(p.quote_increment),
                     str(p.max_order_size), content["min_order_amount"], p.status, int(p.is_api_tradable),
                     to_iso(at), to_iso(at)))
        return changed

    def pairs(self) -> list[Pair]:
        return [Pair(r["symbol"], r["asset_code"], r["quote_code"], Decimal(r["asset_increment"]),
                     Decimal(r["quote_increment"]), Decimal(r["max_order_size"]),
                     None if r["min_order_amount"] is None else Decimal(r["min_order_amount"]), r["status"],
                     bool(r["is_api_tradable"]))
                for r in self.conn.execute("SELECT * FROM rh_pairs ORDER BY symbol")]

    def save_quotes(self, quotes: Iterable[BidAsk]) -> int:
        rows = [(q.symbol, str(q.bid), str(q.ask), to_iso(q.received_at), to_epoch_millis(q.received_at), q.raw_sha256)
                for q in quotes]
        with transaction(self.conn):
            self.conn.executemany("INSERT INTO rh_quotes(symbol, bid, ask, received_at, received_at_ms, raw_sha256)"
                                  " VALUES (?, ?, ?, ?, ?, ?)", rows)
        return len(rows)

    def latest_quotes(self) -> dict[str, BidAsk]:
        rows = self.conn.execute(
            "SELECT q.* FROM rh_quotes q JOIN (SELECT symbol, MAX(id) AS id FROM rh_quotes GROUP BY symbol) m"
            " ON q.id = m.id").fetchall()
        return {r["symbol"]: BidAsk(r["symbol"], Decimal(r["bid"]), Decimal(r["ask"]), parse_iso8601(r["received_at"]),
                                    r["raw_sha256"]) for r in rows}

    def save_estimates(self, estimates: Iterable[Estimate], purpose: str) -> None:
        with transaction(self.conn):
            for e in estimates:
                self.conn.execute(
                    "INSERT INTO rh_estimates(symbol, route, side, quantity, unit_price, fee, total, fee_ratio,"
                    " provider_at, received_at, received_at_ms, purpose, raw_sha256)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (e.symbol, e.route, e.side, str(e.quantity), str(e.unit_price), str(e.fee), str(e.total),
                     None if e.fee_ratio is None else str(e.fee_ratio),
                     None if e.provider_at is None else to_iso(e.provider_at), to_iso(e.received_at),
                     to_epoch_millis(e.received_at), purpose, e.raw_sha256))

    def measured_roundtrip(self, symbol: str, *, minimum: int = 3, limit: int = 50) -> Decimal | None:
        """Cheapest route's median round trip; each trip pairs the buy and sell legs of one Robinhood response."""
        rows = self.conn.execute(
            "SELECT received_at_ms, route, side, total FROM rh_estimates WHERE symbol = ? ORDER BY id DESC LIMIT ?",
            (symbol, limit * 4)).fetchall()
        legs: dict[tuple[str, int], dict[str, Decimal]] = {}
        for r in rows:
            legs.setdefault((r["route"], int(r["received_at_ms"])), {})[r["side"]] = Decimal(r["total"])
        by_route: dict[str, list[Decimal]] = {}
        for (route, _), sides in legs.items():
            if "BUY" in sides and "SELL" in sides and sides["BUY"] > 0:
                by_route.setdefault(route, []).append((sides["BUY"] - sides["SELL"]) / sides["BUY"])
        medians = [st.median(trips[:limit]) for trips in by_route.values() if len(trips) >= minimum]
        return min(medians) if medians else None

    def save_candles(self, source: str, candles: Iterable[Candle], received_at: datetime) -> int:
        rows = [(source, c.product, c.granularity, to_iso(c.start), to_epoch_millis(c.start), str(c.open), str(c.high),
                 str(c.low), str(c.close), str(c.volume), to_iso(received_at), to_epoch_millis(received_at))
                for c in candles]
        with transaction(self.conn):
            before = self.conn.total_changes
            self.conn.executemany(
                "INSERT INTO candles(source, product, granularity, start, start_ms, open, high, low, close, volume,"
                " first_received_at, first_received_at_ms) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT DO NOTHING", rows)
            return self.conn.total_changes - before

    def candles(self, product: str, granularity: int, *, since: datetime | None = None,
                source: str = "coinbase", as_of: datetime | None = None) -> list[Candle]:
        lower = to_epoch_millis(since) if since else 0
        upper = to_epoch_millis(as_of) if as_of else 2**63 - 1
        return [Candle(product, granularity, parse_iso8601(r["start"]), Decimal(r["open"]), Decimal(r["high"]),
                       Decimal(r["low"]), Decimal(r["close"]), Decimal(r["volume"]))
                for r in self.conn.execute(
                    "SELECT * FROM candles WHERE source = ? AND product = ? AND granularity = ? AND start_ms >= ?"
                    " AND first_received_at_ms <= ? AND start_ms + granularity * 1000 <= ? ORDER BY start_ms",
                    (source, product, granularity, lower, upper, upper))]

    def last_candle_start(self, product: str, granularity: int, source: str = "coinbase") -> datetime | None:
        row = self.conn.execute("SELECT MAX(start_ms) FROM candles WHERE source = ? AND product = ? AND granularity = ?",
                                (source, product, granularity)).fetchone()
        return None if row[0] is None else datetime.fromtimestamp(row[0] / 1000, UTC)

    def record_decision(self, at: datetime, day: date, signal: st.Signal, action: str, reasons: Iterable[str],
                        inputs: dict[str, Any]) -> str:
        reasons = tuple(reasons)
        inputs_sha = sha256_hex(canonical_json(inputs))
        digest = sha256_hex(canonical_json({"day": day.isoformat(), "strategy": signal.strategy_id,
                                            "spec": mf.signal_spec(signal), "symbol": signal.symbol,
                                            "action": action, "reasons": reasons, "inputs": inputs_sha,
                                            "features": signal.features, "position": signal.position_id}))
        with transaction(self.conn):
            self.conn.execute(
                "INSERT OR IGNORE INTO rh_decisions(decided_at, session_day, strategy_id, spec_sha256, symbol, action,"
                " reasons, features_json, inputs_sha256, decision_hash) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (to_iso(at), day.isoformat(), signal.strategy_id, mf.signal_spec(signal), signal.symbol,
                 action, ",".join(reasons), canonical_json(signal.features), inputs_sha, digest))
        return digest

    def claim_frame_check(self, state: mf.FrameState, at: datetime, evidence: dict[str, Any]) -> bool:
        bundle = sha256_hex(canonical_json({sid: mf.spec_sha256(sid) for sid in mf.IDS}))
        with transaction(self.conn):
            return self.conn.execute(
                "INSERT OR IGNORE INTO rh_frame_checks(symbol, bar_end, spec_bundle_sha256, decided_at,"
                " input_sha256, evidence_json) VALUES (?, ?, ?, ?, ?, ?)",
                (state.symbol, to_iso(state.bar_end), bundle, to_iso(at), state.input_hash,
                 canonical_json(evidence))).rowcount == 1

    def setup_entered(self, signal: st.Signal) -> bool:
        """A closed position still consumes its hourly setup; restart cannot reopen the same setup."""
        end = mf.floor_time(datetime.fromisoformat(signal.features["bar_end"]), mf.HOUR).isoformat()
        return self.conn.execute(
            "SELECT 1 FROM rh_decisions WHERE strategy_id = ? AND symbol = ? AND spec_sha256 = ?"
            " AND action = 'ENTER' AND json_extract(features_json, '$.setup_end') = ? LIMIT 1",
            (signal.strategy_id, signal.symbol, mf.signal_spec(signal), end)).fetchone() is not None

    def decided(self, day: date) -> bool:
        return self.conn.execute("SELECT 1 FROM rh_decisions WHERE session_day = ? LIMIT 1",
                                 (day.isoformat(),)).fetchone() is not None


# ── ledger ──────────────────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Position:
    id: str
    strategy_id: str
    symbol: str
    session_day: date
    opened_at: datetime
    quantity: Decimal
    entry_cost: Decimal
    entry_route: str
    entry_unit_price: Decimal
    stop_price: Decimal | None
    peak_close: Decimal
    roundtrip_cost_fraction: Decimal
    state: str
    exit_credit: Decimal | None
    exit_reason: str | None
    closed_at: datetime | None
    realized_pnl: Decimal | None
    spec_sha256: str = ""
    plan: dict[str, Any] = field(default_factory=dict, compare=False)


class RhLedger:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def ensure_book(self, learning_cash: Decimal, at: datetime) -> None:
        with transaction(self.conn):
            if self.conn.execute("SELECT 1 FROM rh_cash WHERE kind = 'DEPOSIT' LIMIT 1").fetchone() is None:
                self.conn.execute("INSERT INTO rh_cash(at, kind, amount_micros, note) VALUES (?, 'DEPOSIT', ?, ?)",
                                  (to_iso(at), to_micros(learning_cash, cost=False), "PAPER learning book"))
                self._event(at, "BOOK_OPENED", None, {"learning_cash_usd": str(learning_cash)})

    def _event(self, at: datetime, kind: str, position_id: str | None, payload: dict[str, Any]) -> None:
        self.conn.execute("INSERT INTO rh_events(at, kind, position_id, payload_json) VALUES (?, ?, ?, ?)",
                          (to_iso(at), kind, position_id, canonical_json(payload)))

    def event(self, at: datetime, kind: str, payload: dict[str, Any], position_id: str | None = None) -> None:
        with transaction(self.conn):
            self._event(at, kind, position_id, payload)

    def cash(self) -> Decimal:
        return usd(int(self.conn.execute("SELECT COALESCE(SUM(amount_micros), 0) FROM rh_cash").fetchone()[0]))

    def realized(self) -> Decimal:
        row = self.conn.execute("SELECT COALESCE(SUM(realized_pnl_micros), 0) FROM rh_positions WHERE state = 'CLOSED'")
        return usd(int(row.fetchone()[0]))

    @staticmethod
    def _position(r: sqlite3.Row) -> Position:
        plan = json.loads(r["plan_json"])
        return Position(
            r["id"], r["strategy_id"], r["symbol"], date.fromisoformat(r["session_day"]), parse_iso8601(r["opened_at"]),
            Decimal(r["quantity"]), usd(r["entry_cost_micros"]), r["entry_route"], Decimal(r["entry_unit_price"]),
            None if plan.get("stop") == "none" else Decimal(r["stop_price"]), Decimal(r["peak_close"]),
            Decimal(r["roundtrip_cost_fraction"]), r["state"],
            None if r["exit_credit_micros"] is None else usd(r["exit_credit_micros"]), r["exit_reason"],
            None if r["closed_at"] is None else parse_iso8601(r["closed_at"]),
            None if r["realized_pnl_micros"] is None else usd(r["realized_pnl_micros"]), r["spec_sha256"], plan)

    def positions(self, state: str | None = None) -> list[Position]:
        sql, args = "SELECT * FROM rh_positions", cast(tuple[str, ...], ())
        if state:
            sql, args = sql + " WHERE state = ?", (state,)
        return [self._position(r) for r in self.conn.execute(sql + " ORDER BY opened_at", args)]

    def open_position(self, *, at: datetime, day: date, strategy_id: str, symbol: str, decision_hash: str,
                      buy: Estimate, roundtrip: Decimal, stop_price: Decimal | None, plan: dict[str, Any],
                      spec_sha256: str | None = None) -> str:
        position_id = "rhp_" + uuid.uuid4().hex[:20]
        cost = to_micros(buy.total, cost=True)
        with transaction(self.conn):
            if cost > int(self.conn.execute("SELECT COALESCE(SUM(amount_micros), 0) FROM rh_cash").fetchone()[0]):
                raise StorageError("not enough paper cash")
            self.conn.execute(
                "INSERT INTO rh_positions(id, strategy_id, spec_sha256, symbol, decision_hash, opened_at, session_day,"
                " quantity, entry_route, entry_unit_price, entry_fee, entry_cost_micros, entry_estimate_sha256,"
                " roundtrip_cost_fraction, stop_price, plan_json, peak_close, state)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN')",
                (position_id, strategy_id, spec_sha256 or st.spec_sha256(strategy_id), symbol,
                 decision_hash, to_iso(at), day.isoformat(),
                 str(buy.quantity), buy.route, str(buy.unit_price), str(buy.fee), cost, buy.raw_sha256, str(roundtrip),
                 "0" if stop_price is None else str(stop_price),
                 canonical_json({**plan, "stop": "none" if stop_price is None else str(stop_price)}),
                 str(buy.unit_price)))
            self.conn.execute("INSERT INTO rh_cash(at, kind, amount_micros, position_id) VALUES (?, 'BUY', ?, ?)",
                              (to_iso(at), -cost, position_id))
            self._event(at, "ENTRY", position_id, {"symbol": symbol, "strategy_id": strategy_id, "route": buy.route,
                                                   "quantity": str(buy.quantity), "cost_usd": str(usd(cost)),
                                                   "roundtrip_cost_fraction": str(roundtrip)})
        return position_id

    def close_position(self, position_id: str, *, at: datetime, sell: Estimate, reason: str) -> Decimal:
        credit = to_micros(sell.total, cost=False)
        with transaction(self.conn):
            row = self.conn.execute("SELECT * FROM rh_positions WHERE id = ?", (position_id,)).fetchone()
            if row is None or row["state"] != "OPEN":
                raise StorageError(f"{position_id} is not open")
            if Decimal(row["quantity"]) != sell.quantity:
                raise StorageError("an exit must sell exactly the position's quantity")
            pnl = credit - int(row["entry_cost_micros"])
            self.conn.execute(
                "UPDATE rh_positions SET state = 'CLOSED', closed_at = ?, exit_route = ?, exit_unit_price = ?, exit_fee = ?,"
                " exit_credit_micros = ?, exit_reason = ?, realized_pnl_micros = ? WHERE id = ?",
                (to_iso(at), sell.route, str(sell.unit_price), str(sell.fee), credit, reason, pnl, position_id))
            self.conn.execute("INSERT INTO rh_cash(at, kind, amount_micros, position_id) VALUES (?, 'SELL', ?, ?)",
                              (to_iso(at), credit, position_id))
            self._event(at, "EXIT", position_id, {"symbol": row["symbol"], "strategy_id": row["strategy_id"],
                                                  "reason": reason, "route": sell.route, "credit_usd": str(usd(credit)),
                                                  "pnl_usd": str(usd(pnl))})
        return usd(pnl)

    def update_peak(self, position_id: str, close: Decimal) -> None:
        with transaction(self.conn):
            row = self.conn.execute("SELECT peak_close, state FROM rh_positions WHERE id = ?", (position_id,)).fetchone()
            if row is not None and row["state"] == "OPEN" and close > Decimal(row["peak_close"]):
                self.conn.execute("UPDATE rh_positions SET peak_close = ? WHERE id = ?", (str(close), position_id))

    def snapshot_equity(self, at: datetime, positions_value: Decimal, open_positions: int) -> None:
        cash = int(self.conn.execute("SELECT COALESCE(SUM(amount_micros), 0) FROM rh_cash").fetchone()[0])
        with transaction(self.conn):
            self.conn.execute("INSERT INTO rh_equity(at, at_ms, cash_micros, positions_value_micros, open_positions)"
                              " VALUES (?, ?, ?, ?, ?)", (to_iso(at), to_epoch_millis(at), cash,
                                                          to_micros(positions_value, cost=False), open_positions))

    def equity_series(self, since: datetime) -> list[tuple[datetime, Decimal]]:
        return [(parse_iso8601(r["at"]), usd(int(r["cash_micros"]) + int(r["positions_value_micros"])))
                for r in self.conn.execute("SELECT at, cash_micros, positions_value_micros FROM rh_equity"
                                           " WHERE at_ms >= ? ORDER BY at_ms", (to_epoch_millis(since),))]

    def session_done(self, day: date) -> bool:
        return self.conn.execute("SELECT 1 FROM rh_events WHERE kind = 'SESSION' AND json_extract(payload_json, '$.day')"
                                 " = ? LIMIT 1", (day.isoformat(),)).fetchone() is not None

    def halted(self) -> str | None:
        row = self.conn.execute("SELECT value FROM rh_state WHERE key = 'entries_halted'").fetchone()
        return None if row is None or row["value"] == "" else str(row["value"])

    def set_halt(self, reason: str | None, at: datetime) -> None:
        with transaction(self.conn):
            self.conn.execute("INSERT INTO rh_state(key, value, updated_at) VALUES ('entries_halted', ?, ?)"
                              " ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
                              (reason or "", to_iso(at)))
            self._event(at, "HALT" if reason else "RESUME", None, {"reason": reason})


__all__ = ["LEDGER_FILE", "MARKET_FILE", "Position", "RhDatabases", "RhLedger", "RhMarket", "to_micros", "usd"]
