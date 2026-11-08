"""Calls when IV is cheap and Cheap earnings straddles replayed on real past option prices: the strategies' own rules
read the replay's day with real IV30 and real straddle prices; trades buy at the next real open plus 4% and $0.65 a
contract, exit on later real closes by the rule's plan, and are judged against random calls on real prices. Each
report's straddle is downloaded through the Massive queue."""

import asyncio
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from desk.stocks import calendar as cal
from desk.stocks import options as opt
from desk.stocks import real_options as ro
from desk.stocks.analysis import Day
from desk.stocks.desks import Context, DeskResult
from desk.stocks.iv_history import save_list
from desk.stocks.paper.fills import OPTION_FEE_PER_CONTRACT as FEE
from desk.stocks.replay_grades import current
from desk.stocks.repo import day_of
from tests.stocks_world import ASOF, make_world

NOW = datetime(2026, 10, 24, 15, 0, tzinfo=UTC)


def _day(d: date, prices: dict[str, dict[str, float]], *, committee: float = 70.0, momentum: float = 60.0,
         events: dict[str, Any] | None = None) -> Day:
    symbols = sorted(prices)
    ctx = Context(d, symbols, {}, {}, {}, prices, {}, events=events or {}, options={"MODEL": {"iv30": 0.3}})
    results = {"momentum": {s: DeskResult(momentum, 1.0, "", []) for s in symbols}}
    return Day(d, ctx, results, {s: {"score": committee} for s in symbols}, {}, len(symbols))


def _bars(w: Any, occ: str, first: date, last: date, rows: dict[date, tuple[float, float]]) -> None:
    w.market.save_option_bars(occ, first, last, [{"day": d, "open": o, "high": max(o, c), "low": min(o, c), "close": c,
                                                  "volume": 40.0} for d, (o, c) in rows.items()], NOW)


def test_a_cheap_iv_call_is_bought_at_the_next_real_open_and_sold_at_its_take_profit(tmp_path: Path) -> None:
    w = make_world(tmp_path, n=12)
    try:
        days = [d for d in sorted({b.day for b in w.market.bars("CHEAP")}) if d <= ASOF]
        decided = days[-40]
        spot = next(b.close for b in w.market.bars("CHEAP") if b.day == decided)
        expiry = decided + timedelta(days=45)
        strike = round(spot)
        p0 = opt.bs_price("call", spot, strike, opt.year_fraction(decided, expiry), 0.30, 0.04)
        entry, later = cal.next_trading_day(decided), cal.next_trading_day(cal.next_trading_day(decided))
        occ = f"CHEAP{expiry:%y%m%d}C{round(strike * 1000):08d}"
        save_list(w.market.conn, "iv", "CHEAP", expiry, decided - timedelta(days=18), 30,
                  [{"occ": occ, "kind": "call", "strike": strike}], None, NOW)
        _bars(w, occ, decided - timedelta(days=18), expiry, {decided: (p0, p0), entry: (p0, p0), later: (p0, 2.2 * p0)})
        w.market.conn.execute("INSERT INTO stk_iv_daily VALUES ('CHEAP',?,0.2,'massive_close',2,?)",
                              (day_of(decided), NOW.isoformat()))
        real = ro.RealOptions(w.market.conn, ASOF, {"CHEAP"})
        day = _day(decided, {"CHEAP": {"close": spot, "vol_20d": 0.4}})
        real.observe(day, 0.04)
        assert day.ctx.options == {"MODEL": {"iv30": 0.3}}                       # the modeled view is put back
        assert [s["symbol"] for s in real.signals["cheap_iv_calls"]] == ["CHEAP"]
        assert real.signals["earnings_straddles"] == []
        report = real.replay(w.market)
        calls = report["strategies"]["cheap_iv_calls"]
        price = p0 * (1 + ro.SLIP)
        units = int(ro.STAKE // (price * 100 + FEE))
        cost = units * (price * 100 + FEE)
        value = units * (2.2 * p0 * (1 - ro.SLIP) * 100 - FEE)
        assert calls["trades"] == 1 and calls["return"] == pytest.approx(value / cost - 1)
        assert calls["best"]["exit"].startswith("take-profit")
        assert report["strategies"]["random_calls"]["trades"] == 1             # the baseline drew the same call
        later_day = _day(decided, {"CHEAP": {"close": spot, "vol_20d": 0.15}})  # options no longer cheap
        again = ro.RealOptions(w.market.conn, ASOF, {"CHEAP"})
        again.observe(later_day, 0.04)
        assert again.signals["cheap_iv_calls"] == [] and len(again.signals["random_calls"]) == 1
    finally:
        w.close()


def test_a_cheap_straddle_before_results_exits_the_second_session_after_the_window(tmp_path: Path) -> None:
    w = make_world(tmp_path, n=12)
    try:
        days = [d for d in sorted({b.day for b in w.market.bars("S10")}) if d <= ASOF]
        decided = days[-30]
        spot = next(b.close for b in w.market.bars("S10") if b.day == decided)
        start, end = decided, decided + timedelta(days=12)          # centre 6 days ahead
        expiry = end + timedelta(days=9)
        strike = round(spot)
        legs = [{"occ": f"S10{expiry:%y%m%d}{k[0].upper()}{round(strike * 1000):08d}", "kind": k, "strike": strike,
                 "expiration": expiry.isoformat()} for k in ("call", "put")]
        save_list(w.market.conn, ro.PURPOSE, "S10", expiry, decided, 20, legs, None, NOW)
        half = 0.025 * spot                                          # the straddle prices a 5% move
        flat = {d: (half, half) for d in cal.trading_days(decided, expiry - timedelta(days=1))}
        for leg in legs:
            _bars(w, leg["occ"], decided, expiry, flat)
        events = {"S10": {"next_window": [start.isoformat(), end.isoformat()],
                          "earnings": [{"reaction": 0.10} for _ in range(4)]}}
        real = ro.RealOptions(w.market.conn, ASOF, {"S10"})
        real.observe(_day(decided, {"S10": {"close": spot, "vol_20d": 0.3}}, events=events), 0.04)
        [signal] = real.signals["earnings_straddles"]
        assert signal["symbol"] == "S10" and len(signal["legs"]) == 2
        assert real.wanted[("S10", end.isoformat())]["as_of"] == decided.isoformat()
        straddles = real.replay(w.market)["strategies"]["earnings_straddles"]
        exit_on = cal.next_trading_day(cal.next_trading_day(end))
        assert straddles["trades"] == 1 and straddles["best"]["exit"] == f"results are out on {exit_on.isoformat()}"
        assert straddles["return"] < 0                                    # flat prices lose the spread and fees
        few = {"S10": {"next_window": events["S10"]["next_window"], "earnings": [{"reaction": 0.1}] * 3}}
        thin = ro.RealOptions(w.market.conn, ASOF, {"S10"})
        thin.observe(_day(decided, {"S10": {"close": spot, "vol_20d": 0.3}}, events=few), 0.04)
        assert thin.signals["earnings_straddles"] == [] and not thin.wanted    # fewer than 4 past reports
    finally:
        w.close()


class FakeMassive:
    configured = True

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def option_contracts(self, underlying: str, as_of: date, first: date, last: date, *, kind: Any = None,
                               strikes: Any = None) -> list[dict[str, Any]]:
        self.calls.append("list")
        out = []
        for expiry in (first + timedelta(days=1), first + timedelta(days=8)):
            for k in (99.0, 100.0, 101.0):
                for kind_ in ("call", "put"):
                    out.append({"ticker": f"{underlying}{expiry:%y%m%d}{kind_[0].upper()}{round(k * 1000):08d}",
                                "kind": kind_, "strike": k, "expiration": expiry, "underlying": underlying})
        return out

    async def option_bars(self, ticker: str, first: date, last: date) -> list[dict[str, Any]]:
        self.calls.append("bars")
        return [{"day": first, "open": 2.0, "high": 2.0, "low": 2.0, "close": 2.0, "volume": 10.0}]


def test_each_wanted_report_gets_its_listing_and_the_first_expiry_after_the_window(tmp_path: Path) -> None:
    w = make_world(tmp_path, n=12)
    try:
        wanted = [{"symbol": "S10", "as_of": "2026-09-01", "end": "2026-09-15", "spot": 100.2},
                  {"symbol": "S11", "as_of": "2026-09-02", "end": "2026-09-16", "spot": 100.0}]
        (w.paths.home / ro.REPORT).write_text(json.dumps({"real_price": {"wanted_reports": wanted}}))
        fake = FakeMassive()
        got = asyncio.run(ro.task(fake, w.market, w.paths.home, NOW, 4))
        assert got["ok"] and got["requests"] == 4 and fake.calls == ["list", "bars", "bars", "list"]
        stored = ro.earnings_lists(w.market.conn)
        first = stored[("S10", date(2026, 9, 1))]
        assert first["expiry"] == date(2026, 9, 17) and {c["strike"] for c in first["contracts"]} == {100.0}
        again = asyncio.run(ro.task(fake, w.market, w.paths.home, NOW, 9))
        assert again["requests"] == 2 and again["reports_done"] == 2                # nothing is asked twice
    finally:
        w.close()


def test_the_replay_view_shows_the_real_price_result_beside_the_unsupported_modeled_grade() -> None:
    replay = {"run_id": "r", "last_day": "2026-10-21",
              "real_price": {"first": "2024-10-26", "coverage": {"stocks_with_iv": 3}, "method": ro.METHOD,
                             "strategies": {"cheap_iv_calls": {"trades": 4, "return": 0.1},
                                            "random_calls": {"trades": 9, "return": -0.2}}}}
    view = current(replay)
    assert view["cheap_iv_calls"]["status"] == "unsupported" and "real prices" in view["cheap_iv_calls"]["message"]
    assert view["cheap_iv_calls"]["real_prices"]["trades"] == 4
    assert view["cheap_iv_calls"]["real_prices"]["baseline"]["trades"] == 9
    assert "real_prices" not in view["earnings_straddles"] and "real_prices" not in view["random_calls"]
