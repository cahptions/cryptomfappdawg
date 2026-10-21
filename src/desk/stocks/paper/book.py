"""The PAPER books in stocks-ledger-paper.db: one account per strategy. Money is integer micro-dollars; cash,
decisions, events and daily equity are append-only, an order never changes once placed, and a position's entry (and
a closed position) never changes."""

from __future__ import annotations

import json
import math
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from desk.core.clock import to_iso
from desk.core.hashing import canonical_json
from desk.core.ids import new_id
from desk.stocks.repo import date_of, day_of
from desk.storage.db import transaction

MICRO = 1_000_000


def to_micros(usd: float, *, cost: bool) -> int:
    """Costs round up and credits round down, so rounding never flatters an account."""
    x = usd * MICRO
    return math.ceil(x - 1e-6) if cost else math.floor(x + 1e-6)


def usd(micros: int | None) -> float:
    return 0.0 if micros is None else micros / MICRO


@dataclass
class Order:
    id: int
    decision_id: int
    account: str
    created_at: str
    fill_after: date
    order: dict[str, Any]


@dataclass
class Position:
    id: str
    account: str
    spec_sha256: str
    asset: str
    symbol: str
    structure: str
    legs: list[dict[str, Any]]
    decision_id: int
    opened_at: str
    entry_day: date
    entry_cost: float
    entry_fees: float
    max_loss: float | None
    stop_price: float | None
    expires: date | None
    plan: dict[str, Any]
    state: str
    last_value: float | None
    peak_value: float | None
    exit_day: date | None = None
    exit_credit: float | None = None
    exit_fees: float | None = None
    exit_reason: str | None = None
    marks: dict[str, Any] | None = None

    @property
    def pnl(self) -> float | None:
        if self.exit_credit is None:
            return None
        return self.exit_credit - (self.exit_fees or 0.0) - self.entry_cost - self.entry_fees

    def as_json(self) -> dict[str, Any]:
        return {"id": self.id, "account": self.account, "asset": self.asset, "symbol": self.symbol,
                "structure": self.structure, "legs": self.legs, "entry_day": self.entry_day.isoformat(),
                "entry_cost": self.entry_cost, "entry_fees": self.entry_fees, "max_loss": self.max_loss,
                "stop_price": self.stop_price, "expires": self.expires.isoformat() if self.expires else None,
                "plan": self.plan, "state": self.state, "value": self.last_value, "peak": self.peak_value,
                "exit_day": self.exit_day.isoformat() if self.exit_day else None, "exit_credit": self.exit_credit,
                "exit_fees": self.exit_fees, "exit_reason": self.exit_reason, "pnl": self.pnl, "marks": self.marks}


def _position(r: sqlite3.Row) -> Position:
    return Position(r["id"], r["account"], r["spec_sha256"], r["asset"], r["symbol"], r["structure"],
                    json.loads(r["legs_json"]), r["decision_id"], r["opened_at"], date_of(r["entry_day"]),
                    usd(r["entry_cost_micros"]), usd(r["entry_fees_micros"]),
                    None if r["max_loss_micros"] is None else usd(r["max_loss_micros"]), r["stop_price"],
                    date_of(r["expires_day"]) if r["expires_day"] else None, json.loads(r["plan_json"]), r["state"],
                    None if r["last_value_micros"] is None else usd(r["last_value_micros"]),
                    None if r["peak_value_micros"] is None else usd(r["peak_value_micros"]),
                    date_of(r["exit_day"]) if r["exit_day"] else None,
                    None if r["exit_credit_micros"] is None else usd(r["exit_credit_micros"]),
                    None if r["exit_fees_micros"] is None else usd(r["exit_fees_micros"]), r["exit_reason"],
                    json.loads(r["marks_json"]))


class PaperBook:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    @contextmanager
    def atomic(self) -> Iterator[None]:
        """A whole session commits once; individual position operations may join that session transaction."""
        if self.conn.in_transaction:
            yield
        else:
            with transaction(self.conn):
                yield

    # accounts and cash ────────────────────────────────────────────────────────────────────────────
    def accounts(self) -> list[str]:
        return [r["account"] for r in self.conn.execute("SELECT DISTINCT account FROM stk_cash ORDER BY account")]

    def open_account(self, account: str, capital: float, at: datetime, day: date) -> bool:
        if self.conn.execute("SELECT 1 FROM stk_cash WHERE account = ? LIMIT 1", (account,)).fetchone():
            return False
        self.add_cash(account, at, day, to_micros(capital, cost=False), "DEPOSIT")
        return True

    def add_cash(self, account: str, at: datetime, day: date, amount_micros: int, reason: str,
                 position_id: str | None = None) -> None:
        self.conn.execute("INSERT INTO stk_cash(account, at, day, amount_micros, reason, position_id) VALUES (?, ?, ?, ?, ?, ?)",
                          (account, to_iso(at), day_of(day), amount_micros, reason, position_id))

    def cash(self, account: str) -> float:
        return usd(int(self.conn.execute("SELECT COALESCE(SUM(amount_micros), 0) FROM stk_cash WHERE account = ?",
                                         (account,)).fetchone()[0]))

    def deposits(self, account: str) -> float:
        return usd(int(self.conn.execute("SELECT COALESCE(SUM(amount_micros), 0) FROM stk_cash WHERE account = ? AND"
                                         " reason = 'DEPOSIT'", (account,)).fetchone()[0]))

    def dividends_paid(self, position_id: str) -> set[str]:
        return {json.loads(r["payload_json"]).get("ex_date") for r in self.conn.execute(
            "SELECT payload_json FROM stk_events WHERE position_id = ? AND kind = 'DIVIDEND'", (position_id,))}

    # decisions, orders, events ────────────────────────────────────────────────────────────────────
    def decide(self, at: datetime, asof: date, account: str, spec_sha256: str, symbol: str, action: str,
               reasons: list[str], evidence: dict[str, Any]) -> int:
        cur = self.conn.execute(
            "INSERT INTO stk_decisions(at, asof, account, spec_sha256, symbol, action, reasons, evidence_json)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (to_iso(at), day_of(asof), account, spec_sha256, symbol, action,
                                                "; ".join(reasons), canonical_json(evidence)))
        return int(cur.lastrowid or 0)

    def decisions(self, account: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        sql, args = "SELECT * FROM stk_decisions", list[str]()
        if account:
            sql, args = sql + " WHERE account = ?", [account]
        rows = self.conn.execute(sql + " ORDER BY id DESC LIMIT ?", (*args, limit)).fetchall()
        return [{"id": r["id"], "at": r["at"], "asof": date_of(r["asof"]).isoformat(), "account": r["account"],
                 "symbol": r["symbol"], "action": r["action"], "reasons": r["reasons"],
                 "evidence": json.loads(r["evidence_json"])} for r in rows]

    def place(self, decision_id: int, account: str, at: datetime, fill_after: date, order: dict[str, Any]) -> int:
        cur = self.conn.execute("INSERT INTO stk_pending(decision_id, account, created_at, fill_after_day, order_json, state)"
                                " VALUES (?, ?, ?, ?, ?, 'PENDING')",
                                (decision_id, account, to_iso(at), day_of(fill_after), canonical_json(order)))
        return int(cur.lastrowid or 0)

    def pending(self, account: str | None = None) -> list[Order]:
        sql, args = "SELECT * FROM stk_pending WHERE state = 'PENDING'", list[str]()
        if account:
            sql, args = sql + " AND account = ?", [account]
        return [Order(r["id"], r["decision_id"], r["account"], r["created_at"], date_of(r["fill_after_day"]),
                      json.loads(r["order_json"])) for r in self.conn.execute(sql + " ORDER BY id", args)]

    def resolve(self, order_id: int, state: str, at: datetime, note: str = "") -> None:
        self.conn.execute("UPDATE stk_pending SET state = ?, resolved_at = ?, note = ? WHERE id = ? AND state = 'PENDING'",
                          (state, to_iso(at), note, order_id))

    def event(self, at: datetime, account: str | None, kind: str, position_id: str | None, payload: dict[str, Any]) -> None:
        self.conn.execute("INSERT INTO stk_events(at, account, kind, position_id, payload_json) VALUES (?, ?, ?, ?, ?)",
                          (to_iso(at), account, kind, position_id, canonical_json(payload)))

    def events(self, account: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        sql, args = "SELECT * FROM stk_events", list[str]()
        if account:
            sql, args = sql + " WHERE account = ?", [account]
        return [{"at": r["at"], "account": r["account"], "kind": r["kind"], "position_id": r["position_id"],
                 **json.loads(r["payload_json"])}
                for r in self.conn.execute(sql + " ORDER BY id DESC LIMIT ?", (*args, limit))]

    # positions ────────────────────────────────────────────────────────────────────────────────────
    def open_position(self, *, account: str, spec_sha256: str, asset: str, symbol: str, structure: str,
                      legs: list[dict[str, Any]], decision_id: int, at: datetime, day: date, cost: float, fees: float,
                      max_loss: float | None, stop_price: float | None, expires: date | None,
                      plan: dict[str, Any]) -> str:
        pid = new_id("pos", int(at.timestamp() * 1000))
        cost_m, fees_m = to_micros(cost, cost=True), to_micros(fees, cost=True)
        with self.atomic():
            self.conn.execute(
                "INSERT INTO stk_positions(id, account, spec_sha256, asset, symbol, structure, legs_json, decision_id,"
                " opened_at, entry_day, entry_cost_micros, entry_fees_micros, max_loss_micros, stop_price, expires_day,"
                " plan_json, state, last_value_micros, peak_value_micros) VALUES"
                " (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN', ?, ?)",
                (pid, account, spec_sha256, asset, symbol, structure, canonical_json(legs), decision_id, to_iso(at),
                 day_of(day), cost_m, fees_m, None if max_loss is None else to_micros(max_loss, cost=True), stop_price,
                 day_of(expires) if expires else None, canonical_json(plan), cost_m, cost_m))
            self.add_cash(account, at, day, -(cost_m + fees_m), "OPEN", pid)
        return pid

    def positions(self, account: str | None = None, state: str | None = "OPEN") -> list[Position]:
        sql, args = "SELECT * FROM stk_positions WHERE 1 = 1", list[str]()
        if account:
            sql, args = sql + " AND account = ?", [*args, account]
        if state:
            sql, args = sql + " AND state = ?", [*args, state]
        return [_position(r) for r in self.conn.execute(sql + " ORDER BY opened_at, id", args)]

    def mark(self, position_id: str, value: float, *, stop_price: float | None = None,
             marks: dict[str, Any] | None = None) -> None:
        m = to_micros(value, cost=False) if value >= 0 else -to_micros(-value, cost=True)
        self.conn.execute("UPDATE stk_positions SET last_value_micros = ?, peak_value_micros = MAX(COALESCE("
                          "peak_value_micros, ?), ?), stop_price = COALESCE(?, stop_price),"
                          " marks_json = COALESCE(?, marks_json) WHERE id = ? AND state = 'OPEN'",
                          (m, m, m, stop_price, None if marks is None else canonical_json(marks), position_id))

    def close_position(self, position: Position, *, at: datetime, day: date, credit: float, fees: float, reason: str,
                       evidence: dict[str, Any], cash_reason: str = "CLOSE") -> float:
        credit_m = to_micros(credit, cost=False) if credit >= 0 else -to_micros(-credit, cost=True)
        fees_m = to_micros(fees, cost=True)
        with self.atomic():
            self.conn.execute(
                "UPDATE stk_positions SET state = 'CLOSED', closed_at = ?, exit_day = ?, exit_credit_micros = ?,"
                " exit_fees_micros = ?, exit_reason = ?, exit_evidence_json = ?, last_value_micros = ? WHERE id = ?"
                " AND state = 'OPEN'", (to_iso(at), day_of(day), credit_m, fees_m, reason, canonical_json(evidence),
                                        credit_m, position.id))
            self.add_cash(position.account, at, day, credit_m - fees_m, cash_reason, position.id)
        return usd(credit_m - fees_m) - position.entry_cost - position.entry_fees

    def held_expirations(self) -> dict[str, set[date]]:
        """Every expiration an open option position or a pending option order needs quoted at the next snapshot."""
        out: dict[str, set[date]] = {}
        for p in self.positions():
            if p.asset == "option":
                out.setdefault(p.symbol, set()).update(date.fromisoformat(leg["expiration"]) for leg in p.legs)
        for o in self.pending():
            if o.order.get("asset") == "option":
                legs = o.order.get("legs") or next((p.legs for p in self.positions() if p.id == o.order.get("position_id")), [])
                out.setdefault(o.order["symbol"], set()).update(date.fromisoformat(leg["expiration"]) for leg in legs)
        return out

    # equity ───────────────────────────────────────────────────────────────────────────────────────
    def snapshot(self, day: date, account: str, at: datetime, cash: float, positions: float) -> bool:
        c, p = to_micros(cash, cost=False), to_micros(positions, cost=False)
        cur = self.conn.execute("INSERT OR IGNORE INTO stk_equity(day, account, at, cash_micros, positions_micros,"
                                " equity_micros) VALUES (?, ?, ?, ?, ?, ?)", (day_of(day), account, to_iso(at), c, p, c + p))
        return cur.rowcount == 1

    def equity(self, account: str) -> list[tuple[date, float, float, float]]:
        return [(date_of(r["day"]), usd(r["cash_micros"]), usd(r["positions_micros"]), usd(r["equity_micros"]))
                for r in self.conn.execute("SELECT * FROM stk_equity WHERE account = ? ORDER BY day", (account,))]

    def stepped(self, day: date) -> bool:
        return self.conn.execute("SELECT 1 FROM stk_equity WHERE day = ? LIMIT 1", (day_of(day),)).fetchone() is not None

    def last_step(self) -> date | None:
        row = self.conn.execute("SELECT MAX(day) AS d FROM stk_equity").fetchone()
        return None if row is None or row["d"] is None else date_of(row["d"])


__all__ = ["MICRO", "Order", "PaperBook", "Position", "to_micros", "usd"]
