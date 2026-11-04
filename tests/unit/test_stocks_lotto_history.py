"""Lottos replayed on real past prices: Massive's past weekly listings and each contract's real daily bars, downloaded
a batch at a time; KESTREL's Lottos rule against random lottos, sold at triple or held to expiry, $300 a pick."""

import asyncio
import json
from datetime import UTC, date, datetime, timedelta
from itertools import pairwise

import pytest

from desk.core.errors import ProviderError, ProviderErrorKind
from desk.stocks import calendar as cal
from desk.stocks import lotto_history as lh
from desk.stocks.paper.fills import OPTION_FEE_PER_CONTRACT as FEE
from desk.stocks.repo import day_of
from tests.stocks_world import ASOF, make_world

NOW = datetime(2026, 10, 24, 15, 0, tzinfo=UTC)          # a Saturday: the market is closed


def test_every_past_weekly_expiry_newest_first_with_friday_holidays_moved_back() -> None:
    days = lh.expiries(date(2026, 11, 4))
    assert days[0] == date(2026, 10, 30) and all(d < date(2026, 11, 4) for d in days)
    assert all(b < a for a, b in pairwise(days)) and 95 <= len(days) <= 105
    assert date(2026, 4, 2) in days and date(2026, 4, 3) not in days            # Good Friday: Thursday's expiry
    assert all(cal.is_trading_day(d) for d in days)


def listing(spot: float, expiry: date) -> list[dict[str, object]]:
    out = []
    for step in range(-40, 41):                                  # strikes every 1% from 60% to 140% of the price
        k = round(spot * (1 + step / 100), 1)
        for kind in ("call", "put"):
            out.append({"ticker": f"SPY{expiry:%y%m%d}{kind[0].upper()}{round(k * 1000):08d}", "kind": kind,
                        "strike": k, "expiration": expiry, "underlying": "SPY"})
    return out


def test_candidates_are_out_of_the_money_strikes_near_the_lotto_range_nearest_first() -> None:
    expiry = date(2026, 10, 16)
    chosen = lh.choose(listing(100.0, expiry), 100.0, 0.18, expiry - timedelta(days=14), expiry)
    calls = [c for c in chosen if c["kind"] == "call"]
    puts = [c for c in chosen if c["kind"] == "put"]
    assert 0 < len(calls) <= lh.PER_SIDE and 0 < len(puts) <= lh.PER_SIDE
    assert all(c["strike"] >= 103 for c in calls) and all(c["strike"] <= 97 for c in puts)   # at least 3% out
    assert [c["strike"] for c in calls] == sorted(c["strike"] for c in calls)              # nearest the money first


class FakeMassive:
    configured = True

    def __init__(self, refuse: int | None = None) -> None:
        self.calls: list[tuple[str, str]] = []
        self.refuse = refuse

    async def option_contracts(self, underlying, as_of, first, last, *, kind=None, strikes=None):
        self.calls.append(("list", underlying))
        if self.refuse:
            raise ProviderError("massive", ProviderErrorKind.HTTP_ERROR, "refused", status_code=self.refuse)
        spot = (strikes[0] / 1.03) if kind == "call" else (strikes[1] / 0.97)
        return [c for c in listing(spot, first) if c["kind"] == kind and strikes[0] <= c["strike"] <= strikes[1]]

    async def option_bars(self, ticker, first, last):
        self.calls.append(("bars", ticker))
        return [{"day": first, "open": 0.2, "high": 0.3, "low": 0.1, "close": 0.2, "volume": 500}]


def test_the_download_lists_then_prices_a_batch_and_continues_next_pass(tmp_path) -> None:
    w = make_world(tmp_path, n=12)
    try:
        fake = FakeMassive()
        first = asyncio.run(lh.download(fake, w.market, NOW))
        assert first["ok"] and first["requests"] == lh.PER_PASS_CLOSED == len(fake.calls)
        assert fake.calls[:2] == [("list", "SPY"), ("list", "SPY")] and fake.calls[2][0] == "bars"
        rows = w.market.conn.execute("SELECT symbol, note, listed, contracts FROM stk_lotto_lists").fetchall()
        assert any(r["symbol"] == "SPY" and r["listed"] > 0 and json.loads(r["contracts"]) for r in rows)
        assert any(r["note"] and "no stored" in r["note"] for r in rows)            # names without stored prices
        again = asyncio.run(lh.download(fake, w.market, NOW))
        assert again["contracts_fetched"] > first["contracts_fetched"]               # the next pass carries on
        market_hours = asyncio.run(lh.download(FakeMassive(), w.market, datetime(2026, 10, 22, 15, 0, tzinfo=UTC)))
        assert market_hours["requests"] <= lh.PER_PASS_OPEN
        refused = asyncio.run(lh.download(FakeMassive(refuse=403), w.market, NOW - timedelta(days=200)))
        assert not refused["ok"] and "Options Basic" in refused["error"]
    finally:
        w.close()


def seed(w, expiry: date, contracts: dict[str, tuple[str, float, dict[date, tuple[float, float, int]]]]) -> None:
    """One SPY weekly with given contracts: occ → (kind, strike, {day: (open, close, volume)})."""
    as_of = expiry - timedelta(days=14)
    w.market.conn.execute("INSERT INTO stk_lotto_lists VALUES ('SPY',?,?,?,?,NULL,?)",
                          (day_of(expiry), day_of(as_of), len(contracts),
                           json.dumps([{"occ": o, "kind": k, "strike": s} for o, (k, s, _) in contracts.items()]),
                           NOW.isoformat()))
    for occ, (_kind, _strike, days) in contracts.items():
        w.market.save_option_bars(occ, as_of, expiry, [{"day": d, "open": o, "high": max(o, c), "low": min(o, c),
                                                        "close": c, "volume": v} for d, (o, c, v) in days.items()], NOW)


def test_the_replay_buys_at_the_next_open_sizes_300_sells_at_triple_or_settles_at_expiry(tmp_path) -> None:
    """A two-week call 3.2% out of the money: KESTREL's rule counts it only when a one-standard-deviation move reaches
    the strike (a 4%, 7-day call on this stock would not). Bought at the next open; sold when a close is worth 3x."""
    w = make_world(tmp_path, n=12)
    try:
        days = [d for d in sorted({b.day for b in w.market.bars("SPY")}) if d <= ASOF]
        expiry = days[-1]
        decided = max(d for d in days if (expiry - d).days >= 13)
        spot = next(b.close for b in w.market.bars("SPY") if b.day == decided)
        settle = next(b.close for b in w.market.bars("SPY") if b.day == expiry)
        strike = round(spot * 1.032, 2)
        after = cal.next_trading_day(decided)
        later = cal.next_trading_day(after)
        seed(w, expiry, {"WIN": ("call", strike, {decided: (0.40, 0.40, 400), after: (0.40, 0.42, 300),
                                                  later: (1.00, 1.50, 900)})})
        report = lh.replay(w.market, NOW)
        triple = report["variants"]["kestrel_triple"]
        assert triple["picks"] == 2 and report["sessions"] == 2                      # decided, then after (still cheap)
        price = 0.40 * 1.05 + 0.005
        units = int(300 // (price * 100 + FEE))
        first = units * (1.50 * 0.95 * 100 - FEE) / (units * (price * 100 + FEE))     # a close worth 3x: sold
        second_price = 1.00 * 1.05 + 0.005
        second_units = int(300 // (second_price * 100 + FEE))
        intrinsic = max(settle - strike, 0.0)
        second = (second_units * (intrinsic * 100 - FEE) if intrinsic * 100 > FEE else 0.0) / (
            second_units * (second_price * 100 + FEE))
        assert first >= 3 and triple["best"]["multiple"] == pytest.approx(max(first, second))
        held = units * (intrinsic * 100 - FEE) / (units * (price * 100 + FEE)) if intrinsic * 100 > FEE else 0.0
        run = report["variants"]["kestrel_run"]
        assert run["picks"] == 2 and run["best"]["multiple"] == pytest.approx(max(held, second))
        assert report["variants"]["random_run"]["picks"] == 2                         # one stock: the same contract
        assert report["coverage"]["expiries_listed"] == 1 and set(report["variants"]) == {v for v, _ in lh.VARIANTS}
    finally:
        w.close()


def test_a_trade_needs_the_next_session_and_counts_fees_and_spread() -> None:
    d0 = date(2026, 10, 1)
    d1 = cal.next_trading_day(d0)
    expiry = d0 + timedelta(days=9)
    bars = {d0: {"open": 0.05, "close": 0.05}, d1: {"open": 0.05, "close": 0.06}}
    done = lh.trade(bars, d0, expiry, "call", 50.0, 40.0, triple=False)
    assert done is not None and done["value"] == 0.0 and done["exit"] == "expired worthless"
    assert done["units"] == int(300 // ((0.05 * 1.05 + 0.005) * 100 + FEE))
    assert lh.trade({d0: bars[d0]}, d0, expiry, "call", 50.0, 60.0, triple=False) is None   # did not trade next day
    itm = lh.trade(bars, d0, expiry, "call", 50.0, 60.0, triple=False)
    assert itm is not None and itm["value"] == pytest.approx(itm["units"] * (10 * 100 - FEE))


def test_kestrel_against_random_needs_enough_days() -> None:
    mine = [{"decided": f"2026-09-{d:02d}", "multiple": 2.0} for d in range(1, 9)]
    base = [{"decided": f"2026-09-{d:02d}", "multiple": 0.5} for d in range(1, 9)]
    got = lh.versus(mine, base)
    assert got is not None and got["days"] == 8 and got["mean_gap"] == pytest.approx(1.5) and got["p_not_better"] == 0
    assert lh.versus(mine[:3], base[:3]) is None
