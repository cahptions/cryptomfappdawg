"""M5-14/M5-22: the $100-account replica applies the real account's limits to the learning book's entries."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from desk.core.clock import to_iso
from desk.core.config import load_config
from desk.core.paths import REPO_ROOT
from desk.paper.report import replica

LOADED = load_config(REPO_ROOT / "config")
BASE = 10 ** 9                 # $100 at 100 USD/SOL
LEARNING_TICKET = 10 ** 8
FEES = 105_000
DAY1 = datetime(2026, 10, 20, 9, tzinfo=UTC)


def trade(n: int, opened: datetime, minutes: float | None = 30, ret: str = "0") -> dict[str, Any]:
    """A learning-book position; ``ret`` is its gross return, ``minutes=None`` keeps it open."""
    closed = None if minutes is None else opened + timedelta(minutes=minutes)
    return {"id": f"p{n:03d}", "opened_at": to_iso(opened), "closed_at": None if closed is None else to_iso(closed),
            "ticket_lamports": LEARNING_TICKET, "entry_fees_lamports": FEES,
            "exit_fees_lamports": None if closed is None else FEES,
            "exit_lamports": None if closed is None else int(LEARNING_TICKET * (1 + Decimal(ret)))}


def run(trades: list[dict[str, Any]]) -> dict[str, Any]:
    return replica([t for t in trades if t["closed_at"]], [t for t in trades if not t["closed_at"]], LOADED, BASE)


def test_two_position_limit_and_slots_free_when_trades_close() -> None:
    trades = [trade(1, DAY1, 60), trade(2, DAY1 + timedelta(minutes=1), 60), trade(3, DAY1 + timedelta(minutes=2)),
              trade(4, DAY1 + timedelta(minutes=61))]
    result = run(trades)
    assert result["trades_taken"] == 3 and result["skipped"] == {"POSITION_LIMIT": 1}


def test_a_close_at_the_same_instant_frees_the_slot_first() -> None:
    trades = [trade(1, DAY1, 10), trade(2, DAY1, 10), trade(3, DAY1 + timedelta(minutes=10))]
    assert run(trades)["trades_taken"] == 3


def test_open_positions_hold_slots_at_zero_pnl() -> None:
    result = run([trade(1, DAY1, None), trade(2, DAY1, None), trade(3, DAY1 + timedelta(hours=5))])
    assert result["trades_taken"] == 2 and result["open_at_end"] == 2 and result["equity_lamports"] == BASE


def test_pnl_scales_by_ticket_and_charges_fixed_fees_without_compounding_above_100() -> None:
    first = trade(1, DAY1, 30, "0.2")                         # +20% on 0.1 SOL
    second = trade(2, DAY1 + timedelta(hours=1), 30, "0.1")   # equity now above $100: ticket stays 0.1 SOL
    result = run([first, second])
    expected = BASE + (20_000_000 - 2 * FEES) + (10_000_000 - 2 * FEES)
    assert result["equity_lamports"] == expected


def test_losses_shrink_the_next_ticket() -> None:
    first = trade(1, DAY1, 30, "-0.3")                        # equity 1e9 - 3e7 - fees
    second = trade(2, DAY1 + timedelta(hours=1), 30, "0.1")
    result = run([first, second])
    equity_after_first = BASE - 30_000_000 - 2 * FEES
    ticket = equity_after_first // 10
    assert result["equity_lamports"] == equity_after_first + int(Decimal("0.1") * ticket) - 2 * FEES


def test_daily_loss_limit_blocks_the_rest_of_the_day_only() -> None:
    loss = trade(1, DAY1, 30, "-0.5")                         # -5% of start-of-day equity, plus fees
    later = trade(2, DAY1 + timedelta(hours=2))
    tomorrow = trade(3, DAY1 + timedelta(days=1))
    result = run([loss, later, tomorrow])
    assert result["skipped"] == {"DAILY_LOSS_LIMIT": 1} and result["trades_taken"] == 2


def test_a_loss_closed_after_midnight_counts_toward_the_new_day() -> None:
    overnight = trade(1, DAY1.replace(hour=23), 120, "-0.5")  # closes 01:00 the next day
    next_morning = trade(2, DAY1.replace(hour=23) + timedelta(hours=3))
    result = run([overnight, next_morning])
    assert result["skipped"] == {"DAILY_LOSS_LIMIT": 1}


def test_trades_per_day_limit() -> None:
    trades = [trade(n, DAY1 + timedelta(minutes=30 * n), 10) for n in range(11)]
    result = run(trades)
    assert result["trades_taken"] == 10 and result["skipped"] == {"TRADES_PER_DAY": 1}


def test_cumulative_drawdown_halt_is_permanent() -> None:
    trades = []
    for day in range(8):                                      # one -50% trade per day: ~-5% equity per day
        trades.append(trade(day, DAY1 + timedelta(days=day), 30, "-0.5"))
    result = run(trades)
    assert result["halted_by_cumulative_drawdown"] is True
    assert result["skipped"].get("CUMULATIVE_DRAWDOWN_HALT", 0) >= 1
    assert Decimal(result["max_drawdown"]) >= Decimal("0.25")
    taken_after = run([*trades, trade(99, DAY1 + timedelta(days=30), 30, "0.5")])
    assert taken_after["trades_taken"] == result["trades_taken"]   # never lifted by a new day


def test_fee_floor_refuses_trades_whose_fixed_costs_exceed_one_percent() -> None:
    expensive = trade(1, DAY1) | {"entry_fees_lamports": 600_000, "exit_fees_lamports": 600_000}
    assert run([expensive])["skipped"] == {"FEE_FLOOR_OR_MINIMUM": 1}
