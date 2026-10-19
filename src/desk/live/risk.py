"""LIVE account limits (PLAN §13.2) and sizing (§13.1) from the LIVE book. Pure reads plus the two persistent halts.

Equity is cash plus each open position at its last executable mark (at cost until its first mark, zero while no
route exists) plus the refundable rent it locks. Start-of-day equity and the drawdown peak follow realized results
only: deposits, closed trades and failed-transaction fees, in time order. The two persistent halts (cumulative
drawdown, consecutive failed transactions) last until a typed ``desk live resume``; the peak and the failure count
restart from that moment, so the history that caused a halt cannot re-impose it the instant it is lifted.

LIVE sizing is the baseline rule: ticket = fraction x min(equity, allocation), within the equity, free-capital,
liquidity and exposure caps. It never compounds by itself: the signer caps every BUY at the allocation's ticket
fraction, so LIVE grows only when the operator raises ``live.max_capital_sol`` (bounded by the safety ceiling).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from desk.core.clock import to_iso
from desk.core.config import LimitsSection, SizingSection
from desk.core.units import sol_to_lamports
from desk.live.book import LiveBook
from desk.paper.model import TicketDecision, floor_int

FAILED_TRANSACTION = ("FAILED", "EXPIRED")


@dataclass(frozen=True)
class Account:
    allocation_lamports: int
    cash_lamports: int
    equity_lamports: int
    exposure_lamports: int
    open_positions: int
    open_mints: frozenset[str]
    day_start_equity_lamports: int
    realized_today_lamports: int
    peak_equity_lamports: int
    opened_today: int


def _realized_curve(book: LiveBook) -> list[tuple[str, int, bool]]:
    """(time, change, from trading) of every realized equity change: deposits, closed trades, failed entry fees."""
    rows = book.conn.execute(
        "SELECT at, amount_lamports, 0 FROM cash_entries WHERE kind = 'DEPOSIT'"
        " UNION ALL SELECT at, amount_lamports, 1 FROM cash_entries WHERE kind = 'FAILED_TX_FEE' AND position_id IS NULL"
        " UNION ALL SELECT closed_at, realized_pnl_lamports, 1 FROM positions WHERE state = 'CLOSED'").fetchall()
    return sorted((str(at), int(change), bool(trade)) for at, change, trade in rows)


def account(book: LiveBook, allocation_lamports: int, now: datetime) -> Account:
    day = to_iso(now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0))
    value = exposure = rent = 0
    open_rows = book.open_positions()
    for row in open_rows:
        if row["last_mark_at"] is None:
            mark = row["ticket_lamports"]                      # not marked yet: at cost
        elif row["last_mark_kind"] == "EXECUTABLE" and row["last_mark_lamports"] is not None:
            mark = row["last_mark_lamports"]
        else:
            mark = 0                                           # no executable route: worth nothing for now
        value += mark
        exposure += max(row["ticket_lamports"], mark)
        rent += row["rent_lamports"]
    cash = book.cash_lamports()
    equity = cash + value + rent
    resumed = _last_resume(book)
    running = peak = at_resume = realized_today = 0
    for at, change, trade in _realized_curve(book):
        running += change
        if resumed is None or at > resumed:
            peak = max(peak, running)
        else:
            at_resume = running
        if trade and at >= day:
            realized_today += change
    if resumed is not None:                                     # a manual reset restarts the peak from there
        peak = max(peak, at_resume)
    opened_today = int(book.conn.execute("SELECT COUNT(*) FROM paper_events WHERE kind = 'OPENED' AND at >= ?",
                                         (day,)).fetchone()[0])
    # start-of-day equity: today's deposits count, today's trading results do not
    return Account(allocation_lamports=allocation_lamports, cash_lamports=cash, equity_lamports=equity,
                   exposure_lamports=exposure, open_positions=len(open_rows),
                   open_mints=frozenset(row["mint"] for row in open_rows),
                   day_start_equity_lamports=running - realized_today, realized_today_lamports=realized_today,
                   peak_equity_lamports=max(peak, equity), opened_today=opened_today)


def _last_resume(book: LiveBook) -> str | None:
    row = book.conn.execute("SELECT at FROM paper_events WHERE kind = 'RESUME' ORDER BY id DESC LIMIT 1").fetchone()
    return None if row is None else str(row["at"])


def consecutive_failed_transactions(book: LiveBook, count: int) -> bool:
    """True when the last ``count`` swap transactions that reached the chain since the last manual resume all failed
    or expired."""
    rows = book.conn.execute(
        "SELECT json_extract(payload_json, '$.state') AS state FROM paper_events WHERE kind = 'INTENT_RESOLVED'"
        " AND json_extract(payload_json, '$.kind') IN ('BUY', 'SELL')"
        " AND json_extract(payload_json, '$.state') IN ('CONFIRMED', 'FAILED', 'EXPIRED')"
        " AND id > (SELECT COALESCE(MAX(id), 0) FROM paper_events WHERE kind = 'RESUME') ORDER BY id DESC LIMIT ?",
        (count,)).fetchall()
    return len(rows) == count and all(row["state"] in FAILED_TRANSACTION for row in rows)


def entry_blocks(book: LiveBook, acct: Account, limits: LimitsSection, mint: str, now: datetime) -> list[str]:
    """§13.2 account limits for one new entry; records the two halts that need a manual `desk live resume`."""
    reasons: list[str] = []
    if acct.equity_lamports <= floor_int(Decimal(acct.peak_equity_lamports) * (1 - limits.cumulative_drawdown_fraction)) \
            and not book.halted():
        book.halt("CUMULATIVE_DRAWDOWN", now)
    if consecutive_failed_transactions(book, limits.max_consecutive_failed_tx) and not book.halted():
        book.halt("CONSECUTIVE_FAILED_TX", now)
    halted = book.halted()
    if halted:
        reasons.append(f"HALTED:{halted}")
    if acct.open_positions >= limits.max_open_positions:
        reasons.append("POSITION_LIMIT")
    if mint in acct.open_mints:
        reasons.append("MINT_ALREADY_OPEN")
    if acct.opened_today >= limits.max_trades_per_day:
        reasons.append("TRADES_PER_DAY")
    sent = book.conn.execute("SELECT COUNT(*) FROM paper_events WHERE kind = 'INTENT_SENT' AND at > ?",
                             (to_iso(now - timedelta(minutes=1)),)).fetchone()[0]
    if sent >= limits.max_order_submissions_per_min:
        reasons.append("ORDER_RATE")
    if acct.realized_today_lamports <= -floor_int(Decimal(acct.day_start_equity_lamports) * limits.daily_realized_loss_fraction):
        reasons.append("DAILY_LOSS_LIMIT")
    if acct.equity_lamports <= floor_int(Decimal(acct.day_start_equity_lamports) * (1 - limits.daily_equity_drawdown_fraction)):
        reasons.append("DAILY_DRAWDOWN")
    return reasons


def live_ticket(acct: Account, sizing: SizingSection, limits: LimitsSection, *, liquidity_cap_lamports: int | None,
                fixed_costs_lamports: int, rent_lamports: int) -> TicketDecision:
    """§13.1 baseline: fraction x min(equity, allocation), bounded by every cap; 0 with reasons when it cannot trade."""
    capital = max(0, min(acct.equity_lamports, acct.allocation_lamports))
    exposure_room = max(0, floor_int(Decimal(acct.equity_lamports) * limits.max_total_exposure_fraction)
                        - acct.exposure_lamports)
    ticket = min(floor_int(Decimal(capital) * sizing.fixed_fraction),
                 floor_int(Decimal(capital) * sizing.max_ticket_fraction_equity),
                 floor_int(Decimal(max(0, acct.cash_lamports)) * sizing.max_ticket_fraction_free_capital),
                 exposure_room)
    if sizing.max_ticket_sol is not None:
        ticket = min(ticket, sol_to_lamports(sizing.max_ticket_sol))
    reasons: list[str] = []
    if liquidity_cap_lamports is None:
        reasons.append("LIQUIDITY_CAP_UNKNOWN")
    else:
        ticket = min(ticket, liquidity_cap_lamports)
    if exposure_room < sol_to_lamports(sizing.min_ticket_sol):
        reasons.append("EXPOSURE_LIMIT")
    if ticket < sol_to_lamports(sizing.min_ticket_sol):
        reasons.append("BELOW_MINIMUM_TICKET")
    if Decimal(fixed_costs_lamports) > Decimal(ticket) * sizing.max_fixed_cost_fraction:
        reasons.append("FEE_FLOOR")
    if ticket + fixed_costs_lamports + rent_lamports > acct.cash_lamports:
        reasons.append("INSUFFICIENT_CASH")
    return TicketDecision(0 if reasons else ticket, tuple(reasons), capital)


__all__ = ["Account", "account", "consecutive_failed_transactions", "entry_blocks", "live_ticket"]
