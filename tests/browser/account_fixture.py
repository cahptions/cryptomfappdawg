"""Synthetic "My Schwab account" data for the browser fixture: invented positions and fills on the synthetic world's
stocks, saved the way a real read would save them. Never a real account, account number or Schwab response."""

from __future__ import annotations

from datetime import UTC, date, datetime

from desk.stocks import account
from desk.stocks.sources import schwab_account as sa
from tests.stocks_world import StocksWorld, occ

KEY = sa.account_key("SYNTHETICBROWSERFIXTURE0000000000000000000000000000000000000001")
EXPIRY = date(2027, 1, 15)


def _close(world: StocksWorld, symbol: str) -> float:
    return world.market.bars(symbol)[-1].close


def populate(world: StocksWorld, now: datetime) -> None:
    s10, s11 = _close(world, "S10"), _close(world, "S11")
    k10 = round(s10 * 1.05)
    k11 = round(s11 * 0.9)
    call, put = occ("S10", EXPIRY, "call", k10), occ("S11", EXPIRY, "put", k11)

    def position(symbol: str, asset: str, qty: float, avg: float, value: float) -> sa.Position:
        parts = sa.option_parts(symbol) if asset == "OPTION" else None
        return sa.Position(KEY, "4321", asset, symbol, parts[0] if parts else None, parts[2] if parts else None,
                           parts[3] if parts else None, parts[1] if parts else None, qty, avg, value, 12.5, 0.4)

    positions = [position("S10", "EQUITY", 20, round(s10 * 0.92, 2), round(20 * s10, 2)),
                 position(call, "OPTION", 2, 4.2, 2 * 100 * 4.6),
                 position(put, "OPTION", -1, 3.1, -100 * 2.4),
                 position("SPY", "COLLECTIVE_INVESTMENT", 3, round(_close(world, "SPY") * 0.97, 2),
                          round(3 * _close(world, "SPY"), 2))]
    days = world.days

    def fill(n: int, symbol: str, qty: float, price: float, day: date, effect: str, asset: str = "EQUITY") -> sa.Fill:
        parts = sa.option_parts(symbol) if asset == "OPTION" else None
        return sa.Fill(KEY, "4321", f"9{n:07d}", 0, datetime(day.year, day.month, day.day, 15, tzinfo=UTC), asset,
                       symbol, parts[0] if parts else None, parts[2] if parts else None, parts[3] if parts else None,
                       parts[1] if parts else None, qty, price, 0.65 if asset == "OPTION" else 0.0, effect)

    old_call = occ("S11", days[-25], "call", round(s11))
    fills = [fill(1, "S12", 15, 40.0, days[-60], "OPENING"), fill(2, "S12", -15, 43.5, days[-40], "CLOSING"),
             fill(3, "S13", 10, 55.0, days[-30], "OPENING"), fill(4, "S13", -10, 51.0, days[-12], "CLOSING"),
             fill(5, old_call, 1, 2.0, days[-45], "OPENING", "OPTION"),
             fill(6, old_call, -1, 3.1, days[-30], "CLOSING", "OPTION")]
    account.set_enabled(world.paths, True)
    account._record(world.dbs.research, now, "browser fixture", "ok", None, positions=positions, fills=fills)
